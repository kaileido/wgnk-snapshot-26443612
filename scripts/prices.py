"""Prices, all from slot0 or reserves by eth_call at a fixed block.

- The pre-hack rate: slot0 of the Uniswap V3 WGNK/USDT 0.3% pool (the deepest WGNK pool) at the end of block PRE, USDT taken at 1 USD; the other two WGNK/USDT pools for comparison.
- ETH/USD at every window block that has a WGNK transfer: slot0 of the Uniswap V3 USDC/WETH 0.05% pool at that block.
- WBTC/USD at the mint block: slot0 of the Uniswap V3 WBTC/USDC 0.3% pool.
- Uniswap V4 pool keys of every V4 pool that swapped in a window transaction, from the V4 PositionManager's poolKeys(bytes25), to find native-ETH legs.
"""
from decimal import Decimal as D, getcontext
from common import *

getcontext().prec = 60
Q96 = D(2) ** 96
USDC_WETH = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"  # token0 USDC (6), token1 WETH (18)
WBTC_USDC = "0x99ac8ca7087fa4a2a1fb6357269965a2014abc35"  # token0 WBTC (8), token1 USDC (6)
V4_POSITION_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
SWAP_V4 = topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")


def sqrt_price(pool, block):
    return D(int(eth_call(pool, sel("slot0()"), block)[2:66], 16)) / Q96


W = window()
rate = {"block": PRE}
p = sqrt_price("0x203ee836d417cf944133bbdd2c62b4bc7388c55d", PRE)
rate["v3_0.3%_slot0_usd_per_wgnk"] = str(p * p * D(10) ** (9 - 6))  # token0 WGNK (9), token1 USDT (6)
p = sqrt_price("0xdbc0edbced03c87a7f287f9949e01c012511f117", PRE)
rate["v3_1%_slot0_usd_per_wgnk"] = str(p * p * D(10) ** (9 - 6))
r = eth_call(V2_PAIR, sel("getReserves()"), PRE)
rate["v2_pair_reserves_usd_per_wgnk"] = str(D(int(r[66:130], 16)) / D(10) ** 6 / (D(int(r[2:66], 16)) / D(10) ** 9))
rate["rate_used"] = RATE
save("prehack-rate.json", rate)
print(json.dumps(rate, indent=1))

R = load("receipts.json")
blocks = sorted({int(r["blockNumber"], 16) for r in R.values()})
px = load("eth-usd.json", {})
need = [b for b in blocks if str(b) not in px]
res = batch([("eth_call", [{"to": USDC_WETH, "data": sel("slot0()")}, hex(b)]) for b in need])
for b, s in zip(need, res):
    q = D(int(s[2:66], 16)) / Q96
    px[str(b)] = str(1 / (q * q * D(10) ** (6 - 18)))
q = sqrt_price(WBTC_USDC, MINT)
px["wbtc"] = str(q * q * D(10) ** (8 - 6))
save("eth-usd.json", px)
eth = [D(v) for k, v in px.items() if k != "wbtc"]
print("ETH/USD", len(eth), "blocks", round(min(eth), 2), "..", round(max(eth), 2), "WBTC/USD", round(D(px["wbtc"]), 2))

ids = sorted({l["topics"][1] for r in R.values() for l in r["logs"] if l["topics"] and l["topics"][0] == SWAP_V4 and l["address"].lower() == POOL_MANAGER})
keys = load("v4-keys.json", {})
need = [i for i in ids if i not in keys]
res = batch([("eth_call", [{"to": V4_POSITION_MANAGER, "data": sel("poolKeys(bytes25)") + i[2:52].ljust(64, "0")}, hex(W["end"])]) for i in need])
for i, s in zip(need, res):
    c0, c1 = "0x" + s[26:66], "0x" + s[90:130]
    keys[i] = {"currency0": c0, "currency1": c1} if int(s[2:130], 16) else None
save("v4-keys.json", keys)
print("V4 pools swapped in the window", len(ids), "keys found", sum(1 for v in keys.values() if v))
