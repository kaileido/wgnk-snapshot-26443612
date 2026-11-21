"""Shared helpers for the WGNK compensation scripts: constants, a read-only JSON-RPC client with endpoint fallback and an on-disk cache, Keccak-256 and EIP-55.

Python 3 standard library only. Every script reads and writes JSON files in the data directory (default ./data, or the DATA environment variable).
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

WGNK = "0x972a7a92d92796a98801a8818bcf91f1648f2f68"
DECIMALS = 9
PRE = 26443612  # last block before the unauthorized mint
MINT = 26443613  # block of the 22,000,000 WGNK mint
SUPPLY_PRE = 13090977714566352  # totalSupply at PRE, raw units
RATE = "0.14272"  # USD per WGNK, pre-hack rate (see prices.py)
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x" + "0" * 40
RPCS = [u for u in os.environ.get("RPC", "").split(",") if u] or ["https://gateway.tenderly.co/public/mainnet", "https://ethereum-rpc.publicnode.com", "https://eth.drpc.org"]
DATA = Path(os.environ.get("DATA", "data"))
DATA.mkdir(parents=True, exist_ok=True)

# attacker cluster: the minter, its only funder, and its ETH sink
ATTACKER = {"0x6650a1cb0c3b970a0e4ced26984fe06db7844d80": "minter", "0x2ba3d1b5648d64f292cac81cbb0dbee18c1c25cd": "minter's funder", "0xde356f654e9b6ebcc7e3af869e5168885293434a": "minter's ETH sink"}
V3_POOLS = {"0x203ee836d417cf944133bbdd2c62b4bc7388c55d": "Uniswap V3 WGNK/USDT 0.3% pool", "0xdbc0edbced03c87a7f287f9949e01c012511f117": "Uniswap V3 WGNK/USDT 1% pool"}
V2_PAIR = "0xd0162a989355b507f696feec3b835f073fe092d3"
POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
POOLS = {**V3_POOLS, V2_PAIR: "WGNK/USDT V2-style pair", POOL_MANAGER: "Uniswap V4 PoolManager"}


def load(name, default=None):
    p = DATA / name
    if not p.exists():
        if default is not None:
            return default
        sys.exit(f"missing {p}: run the earlier script first")
    return json.loads(p.read_text())


def save(name, obj):
    p = DATA / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=0))
    tmp.replace(p)


def window():
    return load("window.json")


# ---------------------------------------------------------------- JSON-RPC

def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "User-Agent": "wgnk-compensation/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def batch(calls, size=20, reverts_ok=False):
    """calls: list of (method, params). Returns results in order. Batches shrink on rejection; every endpoint is tried before giving up. With reverts_ok, an eth_call that reverts gives None."""
    out, i = [], 0
    while i < len(calls):
        chunk = calls[i:i + size]
        payload = [{"jsonrpc": "2.0", "id": j, "method": m, "params": p} for j, (m, p) in enumerate(chunk)]
        res, last = None, None
        for attempt in range(3):
            for url in RPCS:
                try:
                    reply = _post(url, payload)
                    if isinstance(reply, dict):
                        raise RuntimeError(json.dumps(reply.get("error", reply))[:200])
                    by_id = {r.get("id"): r for r in reply}
                    if reverts_ok:
                        for r in by_id.values():
                            if "error" in r and "revert" in json.dumps(r["error"]).lower():
                                r["result"] = None
                    if any(by_id.get(j) is None or "result" not in by_id[j] for j in range(len(chunk))):
                        bad = next(by_id.get(j) for j in range(len(chunk)) if by_id.get(j) is None or "result" not in by_id[j])
                        raise RuntimeError(json.dumps((bad or {}).get("error", "missing reply"))[:200])
                    res = [by_id[j]["result"] for j in range(len(chunk))]
                    break
                except Exception as exc:  # any transport or RPC failure moves on to the next endpoint
                    last = f"{url}: {exc}"
            if res is not None:
                break
            time.sleep(1 + attempt)
        if res is None:
            if size > 1:
                size = max(1, size // 2)
                continue
            raise RuntimeError(f"{chunk[0][0]} failed on every endpoint: {last}")
        out += res
        i += len(chunk)
        time.sleep(0.2)
    return out


def rpc(method, params):
    return batch([(method, params)])[0]


def eth_call(to, data, block, frm=None):
    tx = {"to": to, "data": data}
    if frm:
        tx["from"] = frm
    return rpc("eth_call", [tx, hex(block)])


def get_logs(flt, start, end, size=50_000):
    """eth_getLogs over [start, end] in chunks that shrink when an endpoint rejects the range."""
    out, cur = [], start
    while cur <= end:
        to = min(cur + size - 1, end)
        try:
            logs = rpc("eth_getLogs", [{**flt, "fromBlock": hex(cur), "toBlock": hex(to)}])
        except RuntimeError:
            if size == 1:
                raise
            size = max(1, size // 4)
            continue
        out += logs
        cur = to + 1
    return out


def word(a):
    return a.lower()[2:].rjust(64, "0")


def s256(h):
    v = int(h, 16)
    return v - (1 << 256) if v >> 255 else v


def topic_addr(t):
    return "0x" + t[-40:].lower()


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


def sel(signature):
    return "0x" + keccak256(signature.encode()).hex()[:8]


def topic(signature):
    return "0x" + keccak256(signature.encode()).hex()


def fmt(raw, decimals=DECIMALS):
    """Exact decimal string of an integer amount in smallest units."""
    sign = "-" if raw < 0 else ""
    raw = abs(raw)
    return f"{sign}{raw // 10 ** decimals}.{raw % 10 ** decimals:0{decimals}d}" if decimals else f"{sign}{raw}"


def snapshot():
    """Pre-hack holder balances {address: raw} from the snapshot CSV (SNAPSHOT environment variable, default snapshot.csv in the current directory)."""
    import csv
    path = Path(os.environ.get("SNAPSHOT", "snapshot.csv"))
    snap = {r["address"].lower(): int(r["balance_raw"]) for r in csv.DictReader(path.open())}
    assert sum(snap.values()) == SUPPLY_PRE, "snapshot does not sum to the pre-hack totalSupply"
    return snap


def kind(codes, a):
    """eoa, 7702 (an EOA with an EIP-7702 delegation) or contract, from eth_getCode at the window end."""
    c = codes.get(a)
    if not c or c["code_len"] == 0:
        return "eoa"
    return "7702" if c["prefix"].startswith("0xef0100") else "contract"
