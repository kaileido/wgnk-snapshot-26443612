#!/usr/bin/env python3
"""Snapshot of every ERC-20 holder balance at a given Ethereum block.

Method (read-only JSON-RPC, Python 3 standard library only, no API keys):
  1. Find the token's deployment block by binary search on eth_getCode.
  2. Collect every `from` / `to` address of every Transfer log up to the target block.
  3. Read balanceOf(address) and totalSupply() at the target block with eth_call.
  4. Write a CSV of non-zero balances sorted by balance descending.
  5. Exit non-zero unless the sum of all balances equals totalSupply exactly.

All token amounts are Python integers (raw units); no floating point is used.
"""
import argparse
import csv
import json
import sys
import time
import urllib.request

DEFAULT_TOKEN = "0x972a7a92d92796a98801a8818bcf91f1648f2f68"
DEFAULT_BLOCK = 26443612
DEFAULT_RPCS = ["https://gateway.tenderly.co/public/mainnet", "https://eth.drpc.org"]
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SEL_BALANCE_OF = "0x70a08231"
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_DECIMALS = "0x313ce567"


# ---------------------------------------------------------------- keccak-256 / EIP-55
# hashlib.sha3_256 is NIST SHA-3, not Ethereum's Keccak-256 (different padding), and
# Keccak is not in hashlib, so a small pure-Python implementation is included.

_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]
_M64 = (1 << 64) - 1


def _rol(x, n):
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _M64 if n else x


def _keccak_f(a):
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        a = [[b[x][y] ^ (~b[(x + 1) % 5][y] & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data) + b"\x01"
    padded += b"\x00" * (-len(padded) % rate)
    padded[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), rate):
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(padded[off + 8 * i: off + 8 * i + 8], "little")
        a = _keccak_f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out[:32]


def checksum_address(addr: str) -> str:
    """EIP-55 mixed-case checksum form of a 0x-prefixed 20-byte hex address."""
    low = addr.lower()[2:]
    digest = keccak256(low.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(digest[i], 16) >= 8 else c for i, c in enumerate(low))


# ---------------------------------------------------------------- JSON-RPC

class Rpc:
    """Minimal JSON-RPC client over HTTP with retries and endpoint fallback."""

    def __init__(self, urls, retries=3):
        self.urls = list(urls)
        self.retries = retries

    def _post(self, url, payload):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "wgnk-snapshot/1.0"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.load(resp)

    def batch(self, calls):
        """calls: list of (method, params). Returns results in order; raises RuntimeError on failure."""
        payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        last = None
        for attempt in range(self.retries):  # each round tries every endpoint once, then backs off
            for url in self.urls:
                try:
                    reply = self._post(url, payload)
                    if isinstance(reply, dict):  # whole-batch error (e.g. batch not allowed / too large)
                        raise RuntimeError(json.dumps(reply.get("error", reply))[:200])
                    by_id = {r.get("id"): r for r in reply}
                    out = []
                    for i in range(len(calls)):
                        r = by_id.get(i)
                        if r is None or r.get("result") is None:
                            raise RuntimeError(json.dumps((r or {}).get("error", "missing reply"))[:200])
                        out.append(r["result"])
                    return out
                except Exception as exc:  # noqa: BLE001 - any transport/RPC failure moves on to the next endpoint
                    last = f"{url}: {exc}"
            time.sleep(1 + attempt)
        raise RuntimeError(f"all endpoints failed, last error: {last}")

    def call(self, method, params):
        return self.batch([(method, params)])[0]

    def eth_call(self, to, data, block):
        return self.call("eth_call", [{"to": to, "data": data}, hex(block)])


# ---------------------------------------------------------------- steps

def find_deployment_block(rpc, token, target):
    """Smallest block whose state has code at `token` (binary search, needs an archive node)."""
    if rpc.call("eth_getCode", [token, hex(target)]) in ("0x", "0x0"):
        raise SystemExit(f"error: no contract code at {token} in block {target}")
    lo, hi = 0, target  # invariant: no code at lo (genesis), code at hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if rpc.call("eth_getCode", [token, hex(mid)]) in ("0x", "0x0"):
            lo = mid
        else:
            hi = mid
    return hi


def collect_candidates(rpc, token, start, end):
    """Every from/to address in Transfer logs in [start, end]; chunk size adapts to RPC limits."""
    addrs = set()
    n_logs = 0
    size = 100_000
    cur = start
    while cur <= end:
        to_block = min(cur + size - 1, end)
        flt = {"address": token, "topics": [TRANSFER_TOPIC], "fromBlock": hex(cur), "toBlock": hex(to_block)}
        try:
            logs = rpc.call("eth_getLogs", [flt])
        except RuntimeError as exc:
            if size <= 1:
                raise SystemExit(f"error: eth_getLogs fails even for a single block ({cur}): {exc}")
            size = max(1, size // 4)
            print(f"  eth_getLogs failed, chunk size -> {size}", file=sys.stderr)
            continue
        for lg in logs:
            topics = lg["topics"]
            if len(topics) != 3:  # not an ERC-20 Transfer (indexed from, to)
                continue
            n_logs += 1
            addrs.add("0x" + topics[1][-40:].lower())
            addrs.add("0x" + topics[2][-40:].lower())
        print(f"  blocks {cur}-{to_block}: {len(logs)} logs", file=sys.stderr)
        cur = to_block + 1
        size = min(size * 2, 1_000_000)
    return addrs, n_logs


def read_balances(rpc, token, block, addrs):
    """balanceOf(addr) at `block` for each address, in adaptive JSON-RPC batches."""
    todo = sorted(addrs)
    balances = {}
    size = 50
    i = 0
    while i < len(todo):
        chunk = todo[i:i + size]
        calls = [("eth_call", [{"to": token, "data": SEL_BALANCE_OF + a[2:].rjust(64, "0")}, hex(block)])
                 for a in chunk]
        try:
            res = rpc.batch(calls)
        except RuntimeError as exc:
            if size <= 1:
                raise SystemExit(f"error: balanceOf failed for {chunk[0]}: {exc}")
            size = max(1, size // 2)
            print(f"  batch failed, batch size -> {size}", file=sys.stderr)
            continue
        for a, r in zip(chunk, res):
            balances[a] = int(r, 16)
        i += len(chunk)
        print(f"  balanceOf {i}/{len(todo)}", file=sys.stderr)
    return balances


def format_amount(raw, decimals):
    if decimals == 0:
        return str(raw)
    whole, frac = divmod(raw, 10 ** decimals)
    return f"{whole}.{frac:0{decimals}d}"


def main():
    ap = argparse.ArgumentParser(description="Snapshot ERC-20 holder balances at a block.")
    ap.add_argument("--token", default=DEFAULT_TOKEN, help="ERC-20 contract address")
    ap.add_argument("--block", type=int, default=DEFAULT_BLOCK, help="block number of the snapshot")
    ap.add_argument("--rpc", action="append",
                    help="archive JSON-RPC endpoint; repeat to add fallbacks (default: %s)" % ", ".join(DEFAULT_RPCS))
    ap.add_argument("--out", help="output CSV path (default: snapshot.csv)")
    args = ap.parse_args()

    token = args.token.lower()
    out_path = args.out or "snapshot.csv"
    rpc = Rpc(args.rpc or DEFAULT_RPCS)
    t0 = time.time()

    head = int(rpc.call("eth_blockNumber", []), 16)
    if args.block > head:
        raise SystemExit(f"error: block {args.block} is above the chain head {head}")
    blk = rpc.call("eth_getBlockByNumber", [hex(args.block), False])
    print(f"block {args.block}, timestamp {int(blk['timestamp'], 16)}", file=sys.stderr)

    print("finding deployment block...", file=sys.stderr)
    start = find_deployment_block(rpc, token, args.block)
    print(f"deployment block: {start}", file=sys.stderr)

    print("collecting Transfer logs...", file=sys.stderr)
    addrs, n_logs = collect_candidates(rpc, token, start, args.block)
    print(f"{n_logs} transfers, {len(addrs)} candidate addresses", file=sys.stderr)

    decimals = int(rpc.eth_call(token, SEL_DECIMALS, args.block), 16)
    total_supply = int(rpc.eth_call(token, SEL_TOTAL_SUPPLY, args.block), 16)

    print("reading balances...", file=sys.stderr)
    balances = read_balances(rpc, token, args.block, addrs)
    holders = sorted(((a, v) for a, v in balances.items() if v > 0), key=lambda kv: (-kv[1], kv[0]))
    total = sum(v for _, v in holders)

    print(f"token {token}  block {args.block}  decimals {decimals}")
    print(f"holders {len(holders)}  sum {total}  totalSupply {total_supply}  elapsed {time.time() - t0:.0f}s")
    if total != total_supply:
        print(f"FAIL: sum of balances differs from totalSupply by {total_supply - total}; no CSV written", file=sys.stderr)
        sys.exit(1)

    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["address", "balance_raw", "balance"])
        for a, v in holders:
            w.writerow([checksum_address(a), v, format_amount(v, decimals)])
    print(f"OK: sum of balances equals totalSupply; wrote {out_path}")


if __name__ == "__main__":
    main()
