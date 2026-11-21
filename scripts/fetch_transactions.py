"""Every WGNK Transfer log from the mint block to the window end, and the receipt and transaction object of every transaction that has one."""
from common import *

W = window()
logs = get_logs({"address": WGNK, "topics": [TRANSFER]}, MINT, W["end"], size=500)
logs.sort(key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16)))
save("transfers.json", logs)
hashes = sorted({l["transactionHash"] for l in logs})
print("transfers", len(logs), "txs", len(hashes), flush=True)
for name, method, key in (("receipts.json", "eth_getTransactionReceipt", "transactionHash"), ("txs.json", "eth_getTransactionByHash", "hash")):
    have = load(name, {})
    need = [h for h in hashes if h not in have]
    for i in range(0, len(need), 100):
        part = need[i:i + 100]
        for h, r in zip(part, batch([(method, [h]) for h in part])):
            assert r and r[key] == h, h
            have[h] = r
        save(name, have)
        print(name, len(have), "/", len(hashes), flush=True)
