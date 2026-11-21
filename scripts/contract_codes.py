"""eth_getCode at the window end for every snapshot holder, every WGNK Transfer party in the window and every transaction sender, to tell EOAs, EIP-7702 EOAs and contracts apart."""
from common import *

W = window()
addrs = set(snapshot())
for l in load("transfers.json"):
    addrs |= {topic_addr(l["topics"][1]), topic_addr(l["topics"][2])}
addrs |= {t["from"].lower() for t in load("txs.json").values()}
addrs |= set(load("lp-positions.json", {}).get("owners", []))
addrs.discard(ZERO)
have = load("codes.json", {})
need = sorted(a for a in addrs if a not in have)
for i in range(0, len(need), 200):
    part = need[i:i + 200]
    for a, c in zip(part, batch([("eth_getCode", [a, hex(W["end"])]) for a in part])):
        have[a] = {"code_len": (len(c) - 2) // 2, "prefix": c[:48]}
    save("codes.json", have)
print(len(have), "addresses;", sum(1 for a in have if kind(have, a) == "contract"), "contracts;", sum(1 for a in have if kind(have, a) == "7702"), "EIP-7702 EOAs")
