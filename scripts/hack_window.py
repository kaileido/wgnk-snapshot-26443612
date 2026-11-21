"""Window blocks: the last block at or before 2026-11-19 00:24:00 UTC (the announcement) and 00:30:00 UTC (the window end), by binary search on block timestamps."""
from common import *

ANNOUNCE_TS = 1795047840  # 2026-11-19 00:24:00 UTC
END_TS = 1795048200  # 2026-11-19 00:30:00 UTC


def ts(n):
    return int(rpc("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)


def last_block_at_or_before(t, lo):
    hi = lo + 1
    while ts(hi) <= t:
        hi += 1000
    assert ts(lo) <= t
    while hi - lo > 1:
        m = (lo + hi) // 2
        if ts(m) <= t:
            lo = m
        else:
            hi = m
    return lo


out = {"pre": PRE, "pre_ts": ts(PRE), "mint": MINT, "mint_ts": ts(MINT)}
for name, t in (("announce", ANNOUNCE_TS), ("end", END_TS)):
    b = last_block_at_or_before(t, MINT)
    out[name] = b
    out[name + "_ts"] = ts(b)
    out[name + "_next_ts"] = ts(b + 1)
save("window.json", out)
for k in ("pre", "mint", "announce", "end"):
    print(k, out[k], time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(out[k + "_ts"])))
