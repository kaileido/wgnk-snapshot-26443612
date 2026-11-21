"""Buys of WGNK in the window (mint block .. window end), one record per (transaction, buyer), with what was paid and its USD value.

Per transaction, every ERC-20 Transfer gives the net delta of each token per address. WETH Deposit/Withdrawal and the native-ETH legs of Uniswap V4 Swap events stand in for native ETH, which emits no logs; tx.value counts as native ETH paid by the sender.
A buyer is an address that is not a WGNK pool, not the zero address and not in the attacker cluster, whose net WGNK in the transaction is positive and who paid for it:
1. its own net outflow of other tokens (and tx.value when it is the sender), less the USD of other tokens it got back in the same transaction; or
2. failing that, the transaction sender's net outflow, when the WGNK was delivered to the buyer on the sender's behalf, split by WGNK among the wallets the sender paid for; or
3. failing that (cross-chain fills and relayed orders, where the buyer paid on another chain or off chain), the net USD outflow of the third parties in the same transaction, split by WGNK among the wallets in this case.
Gainers that paid nothing themselves are not buyers when they got less than 2% of the largest WGNK gain in the transaction (fee takers), when a WGNK pool emits Burn, Collect or ModifyLiquidity in the transaction (LP withdrawals), or when no pool sold WGNK in the transaction (plain transfers).
USD: stablecoins at 1 USD, ETH and WETH at the USDC/WETH 0.05% pool's slot0 price at that block, WBTC at its mint-block price; other tokens are listed as unpriced and add nothing.
"""
import collections
from decimal import Decimal as D, ROUND_HALF_EVEN, getcontext
from common import *

getcontext().prec = 60
W = window()
END, ANN = W["end"], W["announce"]
R, TX, PX, V4K = load("receipts.json"), load("txs.json"), load("eth-usd.json"), load("v4-keys.json")
NATIVE = "eth"
WETH, USDT, USDC, DAI, WBTC = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "0xdac17f958d2ee523a2206206994597c13d831ec7", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "0x6b175474e89094c44da98b954eedeac495271d0f", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
TOK = {WGNK: ("WGNK", 9), USDT: ("USDT", 6), USDC: ("USDC", 6), DAI: ("DAI", 18), WETH: ("WETH", 18), NATIVE: ("ETH", 18), WBTC: ("WBTC", 8),
       "0x853d955acef822db058eb8505911ed77f175b99e": ("FRAX", 18), "0xdc035d45d973e3ec169d2276ddab16f1e407384f": ("USDS", 18), "0x6c3ea9036406852006290770bedfcaba0e23a0e8": ("PYUSD", 6),
       "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": ("USDe", 18), "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f": ("GHO", 18), "0x8292bb45bf1ee4d140127049757c2e0ff06317ed": ("RLUSD", 18)}
STABLE = {USDT, USDC, DAI, "0x853d955acef822db058eb8505911ed77f175b99e", "0xdc035d45d973e3ec169d2276ddab16f1e407384f", "0x6c3ea9036406852006290770bedfcaba0e23a0e8",
          "0x4c9edd5852cd905f086c759e8383e09bff1e68b3", "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f", "0x8292bb45bf1ee4d140127049757c2e0ff06317ed"}
SWAP_V4 = topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
DEP, WDR = topic("Deposit(address,uint256)"), topic("Withdrawal(address,uint256)")
LIQ = {topic(x) for x in ("Burn(address,int24,int24,uint128,uint256,uint256)", "Collect(address,address,int24,int24,uint128,uint128)", "ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)", "Burn(address,uint256,uint256,address)")}
CENT6 = D("0.000001")


def usd(tok, raw, block):
    """USD value of raw units of a token (sign kept), or None when the token has no price."""
    if tok not in TOK:
        return None
    v = D(raw) / D(10) ** TOK[tok][1]
    if tok in STABLE:
        return v
    if tok in (WETH, NATIVE):
        return v * D(PX[str(block)])
    if tok == WBTC:
        return v * D(PX["wbtc"])
    return None


def tx_deltas(h):
    r, t = R[h], TX[h]
    d = collections.defaultdict(collections.Counter)  # token -> address -> raw delta
    native_pm = 0  # native ETH the V4 swappers took out of (+) or put into (-) the PoolManager
    wraps = collections.Counter()  # native ETH each address wrapped (+) or unwrapped (-) through WETH
    for l in r["logs"]:
        tp, a = l["topics"], l["address"].lower()
        if not tp:
            continue
        if tp[0] == TRANSFER and len(tp) == 3 and len(l["data"]) == 66:
            x, y, v = topic_addr(tp[1]), topic_addr(tp[2]), int(l["data"], 16)
            d[a][x] -= v
            d[a][y] += v
        elif a == WETH and tp[0] in (DEP, WDR):
            v = int(l["data"], 16) * (1 if tp[0] == DEP else -1)
            d[WETH][topic_addr(tp[1])] += v
            wraps[topic_addr(tp[1])] += v
        elif tp[0] == SWAP_V4 and a == POOL_MANAGER:
            k = V4K.get(tp[1])
            if k and k["currency0"] == ZERO and k["currency1"] != ZERO:
                native_pm += s256("0x" + l["data"][2:66])
    return d, wraps, native_pm, int(t["value"], 16), t["from"].lower(), int(r["status"], 16)


def flows(d, who, extra_eth=0):
    """Net outflow (positive) or inflow (negative) of every non-WGNK token for one address."""
    out = {tok: -m.get(who, 0) for tok, m in d.items() if tok != WGNK and m.get(who, 0)}
    if extra_eth:
        out[NATIVE] = out.get(NATIVE, 0) + extra_eth
    return {k: v for k, v in out.items() if v}


def usd_of(m, block, sign):
    """USD of the outflows (sign=1) or inflows (sign=-1) in m, and the tokens without a price."""
    s, unknown = D(0), []
    for tok, v in m.items():
        if v * sign > 0:
            u = usd(tok, abs(v), block)
            if u is None:
                unknown.append(tok)
            else:
                s += u
    return s, unknown


def fmt_tokens(m):
    return "; ".join(f"{fmt(abs(v), TOK[t][1]) if t in TOK else abs(v)} {TOK[t][0] if t in TOK else t}" for t, v in sorted(m.items()) if v > 0)


buys, outflows, fees, lp, attacker_moves = [], [], [], [], []
hashes = sorted(R, key=lambda h: (int(R[h]["blockNumber"], 16), int(R[h]["transactionIndex"], 16)))
for h in hashes:
    d, wraps, native_pm, value, frm, ok = tx_deltas(h)
    if not ok:
        continue
    b = int(R[h]["blockNumber"], 16)
    wg = d[WGNK]
    if any(wg.get(a) for a in ATTACKER) or frm in ATTACKER:
        attacker_moves.append({"block": b, "tx": h, "from": frm, "wgnk": {a: v for a, v in wg.items() if v and a != ZERO}})
    skip = set(POOLS) | set(ATTACKER) | {ZERO}
    wrapped = sum(d[WETH].values())  # net WETH created in the tx (Deposit adds, Withdrawal removes)
    pool_out = -sum(wg.get(p, 0) for p in POOLS)
    gainers = {a: v for a, v in wg.items() if v > 0 and a not in skip}
    top = max(gainers.values(), default=0)
    own = {a: flows(d, a, value if a == frm else 0) for a in gainers}
    unpaid = {a: v for a, v in gainers.items() if not any(x > 0 for x in own[a].values())}
    fee = {a for a, v in unpaid.items() if v < 0.02 * top}
    fpay = flows(d, frm, value) if frm not in gainers else {}
    via_from = {a: v for a, v in unpaid.items() if a not in fee and a != frm and any(x > 0 for x in fpay.values())}
    rest = {a: v for a, v in unpaid.items() if a not in fee and a not in via_from}
    is_lp = any(l["topics"] and l["topics"][0] in LIQ and l["address"].lower() in POOLS for l in R[h]["logs"])
    third = {}
    if rest and not is_lp and pool_out > 0:
        # cross-chain fill or relayed order: value the net USD outflow of the third parties in the same tx. With no tx.value, ETH a third party
        # wraps came from its own balance (e.g. an order escrow that holds the user's ETH), so a wrap counts as its native ETH outflow.
        for x in set().union(*[set(m) for t, m in d.items() if t != WGNK]):
            if x in POOLS or x in gainers:
                continue
            m = flows(d, x, wraps[x] if not value else 0)
            net = usd_of(m, b, 1)[0] - usd_of(m, b, -1)[0]
            if net > D("0.01"):
                third[x] = (net, m)
    for a, v in gainers.items():
        if a in fee:
            fees.append({"block": b, "tx": h, "address": a, "wgnk": v})
            continue
        share = D(1)
        if a not in unpaid:
            pay, payer, via = {k: x for k, x in own[a].items() if x > 0}, own[a], "self"
        elif a in via_from:  # delivered on the sender's behalf: the sender's payment, split by WGNK among the wallets it paid for
            pay, payer, via, share = {k: x for k, x in fpay.items() if x > 0}, fpay, "tx.from", D(v) / D(sum(via_from.values()))
        elif is_lp:
            lp.append({"block": b, "tx": h, "address": a, "wgnk": v})
            continue
        elif pool_out <= 0:
            continue  # a plain transfer, not a buy
        else:
            pay, payer, via, share = {}, {}, "third party in the same tx", D(v) / D(sum(rest.values()))
            for net, m in third.values():
                for k, q in m.items():
                    if q > 0:
                        pay[k] = pay.get(k, 0) + q
        if via == "third party in the same tx":
            u, unknown = sum((n for n, _ in third.values()), D(0)), []
        else:
            u, unknown = usd_of(pay, b, 1)
            u = max(D(0), u - usd_of(payer, b, -1)[0])  # less what the payer got back in the same tx
        u *= share
        buys.append({"block": b, "tx": h, "tx_from": frm, "buyer": a, "wgnk": v, "paid": fmt_tokens(pay), "paid_by": via, "payer_share": str(share.quantize(D("0.000001"))),
                     "usd_paid": str(u.quantize(CENT6, ROUND_HALF_EVEN)), "unpriced_tokens": ";".join(sorted(unknown)), "after_announcement": b > ANN})
    for a in (a for a, v in wg.items() if v < 0 and a not in skip):
        got = {k: -x for k, x in flows(d, a).items() if x < 0}
        if a == frm and native_pm > 0:
            got[NATIVE] = got.get(NATIVE, 0) + native_pm
        if a == frm and wrapped < 0:
            got[NATIVE] = got.get(NATIVE, 0) - wrapped
        outflows.append({"block": b, "tx": h, "address": a, "wgnk": -wg[a], "received": fmt_tokens(got), "usd_received": str(usd_of(got, b, 1)[0].quantize(CENT6, ROUND_HALF_EVEN))})

save("post-hack-buys.json", {"buys": buys, "outflows": outflows, "fees": fees, "lp_withdrawals": lp, "attacker_moves": attacker_moves})
print("buys", len(buys), "wallets", len({x["buyer"] for x in buys}), "WGNK", fmt(sum(x["wgnk"] for x in buys)), "USD", sum(D(x["usd_paid"]) for x in buys))
print("paid by", dict(collections.Counter(x["paid_by"] for x in buys)), "no payment found", sum(1 for x in buys if D(x["usd_paid"]) == 0), "with unpriced tokens", sum(1 for x in buys if x["unpriced_tokens"]))
print("fee takers", len(fees), "LP withdrawals", len(lp), "WGNK", fmt(sum(x["wgnk"] for x in lp)), "attacker txs", len(attacker_moves))
