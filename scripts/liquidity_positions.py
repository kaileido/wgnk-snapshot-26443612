"""Attribute the WGNK held by liquidity pool contracts at the end of block PRE to the owners of the liquidity positions.

- Uniswap V3 pools: every position ever opened in the pool (pool Mint events). Positions opened through the NonfungiblePositionManager (NPM) are split into NPM token ids (IncreaseLiquidity events in the same transactions) and credited to ownerOf(tokenId) at PRE; other positions are credited to the address that owns them in the pool. A position's WGNK = what burning all its liquidity at PRE returns (TickMath / SqrtPriceMath, rounding down) + fees owed (tokensOwed plus fee growth inside since its last update).
- The V2-style pair has no LP token and no mint/burn events, so its WGNK stays with the pair as unattributed pool liquidity.
- Uniswap V4 PoolManager: every pool with WGNK as a currency (Initialize events), every position in it (ModifyLiquidity events, position key = sender, ticks, salt), read through StateView at PRE; positions held by the V4 PositionManager are credited to ownerOf(tokenId = salt) at PRE. ERC-6909 WGNK claims held in the PoolManager are credited to their holders.
- Whatever remains in a pool contract (protocol fees, rounding, positions that cannot be attributed) stays with the pool contract, so the per-pool total equals the pool's WGNK balance exactly.
"""
from common import *
from univ3math import position_amounts, fees, Q128

NPM = "0xc36442b4a4522e871399cd717abdd847ab11fe88"
V4_POSITION_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
STATE_VIEW = "0x7ffe42c4a5deea5b0fec41c94c136cf115597227"
V4_DEPLOY = 21688329  # PoolManager deployment block
MINT_V3 = topic("Mint(address,address,int24,int24,uint128,uint256,uint256)")
INCREASE = topic("IncreaseLiquidity(uint256,uint128,uint256,uint256)")
INITIALIZE = topic("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
MODIFY = topic("ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)")
TRANSFER_6909 = topic("Transfer(address,address,address,uint256,uint256)")
snap = snapshot()


def u(h, i):
    return int(h[2 + 64 * i: 66 + 64 * i], 16)


def w_int(v):
    return format(v & ((1 << 256) - 1), "064x")


def created(addr):
    lo, hi = 0, PRE
    while hi - lo > 1:
        m = (lo + hi) // 2
        lo, hi = (m, hi) if rpc("eth_getCode", [addr, hex(m)]) == "0x" else (lo, m)
    return hi


def calls(to, datas, block=PRE, reverts_ok=False):
    return batch([("eth_call", [{"to": to, "data": d}, hex(block)]) for d in datas], reverts_ok=reverts_ok)


def owners_of(nft, ids):
    res = calls(nft, [sel("ownerOf(uint256)") + w_int(t) for t in ids], reverts_ok=True)
    return {t: ("0x" + r[26:66] if r else None) for t, r in zip(ids, res)}


out = {"block": PRE, "pools": {}}

# ---------------------------------------------------------------- Uniswap V3
for pool, name in V3_POOLS.items():
    start = created(pool)
    token0 = "0x" + eth_call(pool, sel("token0()"), PRE)[26:]
    assert token0 == WGNK, "WGNK is token0 in both V3 pools"
    s0 = eth_call(pool, sel("slot0()"), PRE)
    sqrt_p, tick = u(s0, 0), s256("0x" + s0[66:130])
    fg0 = int(eth_call(pool, sel("feeGrowthGlobal0X128()"), PRE), 16)
    pool_fee = int(eth_call(pool, sel("fee()"), PRE), 16)
    mints = get_logs({"address": pool, "topics": [MINT_V3]}, start, PRE)
    keys = sorted({(topic_addr(l["topics"][1]), s256(l["topics"][2]), s256(l["topics"][3])) for l in mints})
    # NPM token ids: IncreaseLiquidity logged by the NPM in the transactions that minted into this pool
    npm_txs = sorted({l["transactionHash"] for l in mints if topic_addr(l["topics"][1]) == NPM})
    first_tx = {}  # token id or position key -> first transaction that added liquidity
    for r in batch([("eth_getTransactionReceipt", [h]) for h in npm_txs]):
        for l in r["logs"]:
            if l["address"].lower() == NPM and l["topics"][0] == INCREASE:
                first_tx.setdefault(int(l["topics"][1], 16), r["transactionHash"])
    for l in mints:
        first_tx.setdefault((topic_addr(l["topics"][1]), s256(l["topics"][2]), s256(l["topics"][3])), l["transactionHash"])
    ids = sorted(k for k in first_tx if isinstance(k, int))
    pos = calls(NPM, [sel("positions(uint256)") + w_int(t) for t in ids], reverts_ok=True)
    own = owners_of(NPM, ids)
    ticks = sorted({t for _, tl, tu in keys for t in (tl, tu)})
    tk = dict(zip(ticks, calls(pool, [sel("ticks(int24)") + w_int(t) for t in ticks])))

    def inside0(tl, tu):
        lo, hi = u(tk[tl], 2), u(tk[tu], 2)
        below = lo if tick >= tl else (fg0 - lo)
        above = hi if tick < tu else (fg0 - hi)
        return (fg0 - below - above) % (1 << 256)

    rows, npm_liq = [], {}
    for t, p in zip(ids, pos):
        if p is None:
            continue  # token burned before PRE
        t0, t1, fee, tl, tu = "0x" + p[2 + 64 * 2 + 24: 2 + 64 * 3], "0x" + p[2 + 64 * 3 + 24: 2 + 64 * 4], u(p, 4), s256("0x" + p[2 + 64 * 5: 2 + 64 * 6]), s256("0x" + p[2 + 64 * 6: 2 + 64 * 7])
        if (tl, tu) not in {(a, b) for o, a, b in keys if o == NPM} or t0 != WGNK or fee != pool_fee:
            continue
        liq, last0, owed0 = u(p, 7), u(p, 8), u(p, 10)
        npm_liq[(tl, tu)] = npm_liq.get((tl, tu), 0) + liq
        prin = position_amounts(sqrt_p, tick, tl, tu, liq)[0]
        f = fees(liq, inside0(tl, tu), last0)
        if prin + f + owed0:
            rows.append({"owner": own[t], "via": "NPM", "token_id": t, "tx": first_tx[t], "tick_lower": tl, "tick_upper": tu, "liquidity": liq, "wgnk_principal": prin, "wgnk_fees": f + owed0, "wgnk": prin + f + owed0})
    for o, tl, tu in keys:
        pk = "0x" + keccak256(bytes.fromhex(o[2:]) + (tl & 0xffffff).to_bytes(3, "big") + (tu & 0xffffff).to_bytes(3, "big")).hex()
        p = eth_call(pool, sel("positions(bytes32)") + pk[2:], PRE)
        liq = u(p, 0)
        if o == NPM:
            assert liq == npm_liq.get((tl, tu), 0), f"NPM token liquidity does not add up for {pool} {tl} {tu}"
            continue
        prin = position_amounts(sqrt_p, tick, tl, tu, liq)[0]
        f = fees(liq, inside0(tl, tu), u(p, 1)) + u(p, 3)
        if prin + f:
            rows.append({"owner": o, "via": "pool", "token_id": None, "tx": first_tx[(o, tl, tu)], "tick_lower": tl, "tick_upper": tu, "liquidity": liq, "wgnk_principal": prin, "wgnk_fees": f, "wgnk": prin + f})
    out["pools"][pool] = {"name": name, "created_block": start, "positions_opened": len(keys), "npm_token_ids": len(ids), "rows": rows}
    print(name, "positions", len(keys), "NPM ids", len(ids), "with WGNK", len(rows), flush=True)

# ---------------------------------------------------------------- V2-style pair
out["pools"][V2_PAIR] = {"name": POOLS[V2_PAIR], "rows": [], "note": "no LP token (totalSupply and balanceOf revert) and no mint or burn events"}

# ---------------------------------------------------------------- Uniswap V4
inits = get_logs({"address": POOL_MANAGER, "topics": [INITIALIZE, None, "0x" + word(WGNK)]}, V4_DEPLOY, PRE, size=200_000)
inits += get_logs({"address": POOL_MANAGER, "topics": [INITIALIZE, None, None, "0x" + word(WGNK)]}, V4_DEPLOY, PRE, size=200_000)
pools4 = {l["topics"][1]: {"currency0": topic_addr(l["topics"][2]), "currency1": topic_addr(l["topics"][3]), "block": int(l["blockNumber"], 16)} for l in inits}
v4rows = []
if pools4:
    mods = get_logs({"address": POOL_MANAGER, "topics": [MODIFY, sorted(pools4)]}, min(p["block"] for p in pools4.values()), PRE, size=200_000)
    first_tx = {}
    for l in sorted(mods, key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16))):
        first_tx.setdefault((l["topics"][1], topic_addr(l["topics"][2]), s256("0x" + l["data"][2:66]), s256("0x" + l["data"][66:130]), l["data"][194:258]), l["transactionHash"])
    keys = sorted(first_tx)
    slot0 = dict(zip(sorted(pools4), calls(STATE_VIEW, [sel("getSlot0(bytes32)") + i[2:] for i in sorted(pools4)])))
    info = calls(STATE_VIEW, [sel("getPositionInfo(bytes32,address,int24,int24,bytes32)") + i[2:] + word(o) + w_int(tl) + w_int(tu) + salt for i, o, tl, tu, salt in keys])
    inside = calls(STATE_VIEW, [sel("getFeeGrowthInside(bytes32,int24,int24)") + i[2:] + w_int(tl) + w_int(tu) for i, o, tl, tu, salt in keys])
    pm_ids = sorted({int(salt, 16) for i, o, tl, tu, salt in keys if o == V4_POSITION_MANAGER})
    own = owners_of(V4_POSITION_MANAGER, pm_ids)
    for (i, o, tl, tu, salt), inf, ins in zip(keys, info, inside):
        liq = u(inf, 0)
        if not liq:
            continue  # V4 credits fees on every liquidity change, so an empty position owes nothing
        s = slot0[i]
        sqrt_p, tick = u(s, 0), s256("0x" + s[66:130])
        side = 0 if pools4[i]["currency0"] == WGNK else 1
        prin = position_amounts(sqrt_p, tick, tl, tu, liq)[side]
        f = fees(liq, u(ins, side), u(inf, 1 + side))
        owner = own[int(salt, 16)] if o == V4_POSITION_MANAGER else o
        if prin + f:
            v4rows.append({"owner": owner, "via": "V4 PositionManager" if o == V4_POSITION_MANAGER else "PoolManager", "pool_id": i, "token_id": int(salt, 16) if o == V4_POSITION_MANAGER else None, "tx": first_tx[(i, o, tl, tu, salt)],
                           "tick_lower": tl, "tick_upper": tu, "liquidity": liq, "wgnk_principal": prin, "wgnk_fees": f, "wgnk": prin + f})
claims = get_logs({"address": POOL_MANAGER, "topics": [TRANSFER_6909, None, None, "0x" + word(WGNK)]}, V4_DEPLOY, PRE, size=200_000)
holders = sorted({topic_addr(l["topics"][x]) for l in claims for x in (1, 2)} - {ZERO})
for h, r in zip(holders, calls(POOL_MANAGER, [sel("balanceOf(address,uint256)") + word(h) + word(WGNK) for h in holders])):
    if int(r, 16):
        v4rows.append({"owner": h, "via": "ERC-6909 claim", "tx": [l["transactionHash"] for l in claims if topic_addr(l["topics"][2]) == h][-1], "wgnk_principal": int(r, 16), "wgnk_fees": 0, "wgnk": int(r, 16)})
out["pools"][POOL_MANAGER] = {"name": POOLS[POOL_MANAGER], "v4_pools": pools4, "rows": v4rows}
print("V4 pools with WGNK", len(pools4), "rows with WGNK", len(v4rows), flush=True)

# ---------------------------------------------------------------- checks
# every V3 NPM position: the computed WGNK equals what withdrawing it at PRE returns (eth_call of NPM.multicall(decreaseLiquidity, collect) from its owner)
def multicall(parts):
    head, offs, body, off = w_int(0x20) + w_int(len(parts)), "", "", 32 * len(parts)
    for d in parts:
        seg = w_int(len(d) // 2) + d + "0" * ((64 - len(d) % 64) % 64)
        offs, body, off = offs + w_int(off), body + seg, off + len(seg) // 2
    return sel("multicall(bytes[])") + head + offs + body


mismatch = 0
for pool in V3_POOLS:
    for r in out["pools"][pool]["rows"]:
        if r["via"] != "NPM":
            continue
        parts = [sel("decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))")[2:] + w_int(r["token_id"]) + w_int(r["liquidity"]) + w_int(0) + w_int(0) + w_int(1 << 64)] if r["liquidity"] else []
        parts.append(sel("collect((uint256,address,uint128,uint128))")[2:] + w_int(r["token_id"]) + word(r["owner"]) + w_int((1 << 128) - 1) * 2)
        got = int(eth_call(NPM, multicall(parts), PRE, frm=r["owner"])[-128:-64], 16)
        mismatch += got != r["wgnk"]
print("V3 NPM positions checked against a simulated withdrawal at PRE, mismatches:", mismatch)
assert mismatch == 0
for pool in V3_POOLS:
    out["pools"][pool]["protocol_fees"] = int(eth_call(pool, sel("protocolFees()"), PRE)[2:66], 16)
out["pools"][POOL_MANAGER]["protocol_fees"] = int(eth_call(POOL_MANAGER, sel("protocolFeesAccrued(address)") + word(WGNK), PRE), 16)

# ---------------------------------------------------------------- residuals
for pool, p in out["pools"].items():
    p["balance"] = snap[pool]
    p["attributed"] = sum(r["wgnk"] for r in p["rows"])
    p["residual"] = p["balance"] - p["attributed"]
    assert p["residual"] >= 0, f"attributed more than the pool holds: {pool}"
    print(p["name"], "balance", fmt(p["balance"]), "attributed", fmt(p["attributed"]), "residual", fmt(p["residual"]), "of which protocol fees", fmt(p.get("protocol_fees", 0)))
out["owners"] = sorted({r["owner"] for p in out["pools"].values() for r in p["rows"]})
save("lp-positions.json", out)
