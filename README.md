# WGNK holder snapshot at Ethereum block 26443612

A reproducible list of every holder balance of the WGNK ERC-20 token at one fixed Ethereum block.

The repository also holds the compensation options built on this snapshot: [`COMPENSATION.md`](COMPENSATION.md) has their rules and CSV columns.

| | |
|---|---|
| Token | WGNK, `0x972a7a92d92796a98801a8818bcf91f1648f2f68` |
| Chain | Ethereum mainnet |
| Block | 26443612 |
| Block time | 2026-11-18 18:01:59 UTC |
| Decimals | 9 |
| Holders (balance > 0) | 1013 |
| Total supply at the block | 13090977714566352 raw units = 13090977.714566352 WGNK |

Block 26443612 is the last block before an unauthorized mint of 22,000,000 WGNK at block 26443613.

## Files

- `snapshot.py`: the script that produces the CSV. Python 3 standard library only, no API keys.
- `snapshot.csv`: the result, sorted by balance descending.

CSV columns: `address` (EIP-55 checksum form), `balance_raw` (integer, smallest unit), `balance` (exact decimal string, `balance_raw / 10^decimals`). Only addresses with a non-zero balance are listed.

## Run

```
python3 snapshot.py
```

Options (the defaults for `--token`, `--block` and `--rpc` reproduce this snapshot):

- `--token ADDRESS`: ERC-20 contract address.
- `--block N`: block number of the snapshot.
- `--rpc URL`: an Ethereum JSON-RPC endpoint that serves historical state (`eth_call`, `eth_getCode` at old blocks) and `eth_getLogs`. Repeat the option to add fallback endpoints. Defaults: `https://gateway.tenderly.co/public/mainnet`, then `https://eth.drpc.org`.
- `--out PATH`: output CSV path (default `snapshot.csv`). The CSV is written only after the check below passes.

A full run takes a few minutes, mostly because the free public endpoints limit batch sizes and request rates. The script adapts its `eth_getLogs` chunk size and its batch size when an endpoint rejects a request, and retries with the next endpoint.

## Method

1. Find the token's deployment block by binary search on `eth_getCode`.
2. Collect every `from` and `to` address of every `Transfer` event from the deployment block up to the target block (`eth_getLogs`).
3. Call `balanceOf(address)` for each of those addresses, and `totalSupply()` and `decimals()`, all at the target block (`eth_call`).
4. Write the non-zero balances to the CSV.

Reading the events and then calling `balanceOf` at the block is deliberate: summing a transfer list alone can miss addresses, whereas the contract's own `balanceOf` is authoritative.

## Check

The script sums all balances and compares the sum with `totalSupply()` at the same block. It exits with status 0 only if they are exactly equal, and with status 1 otherwise. All amounts are integers; no floating point is used. For this snapshot: 1687 candidate addresses, 17344 transfer events, 1013 holders, sum 13090977714566352 equals totalSupply 13090977714566352.

The check also guards against an incomplete event list: a missed holder would make the sum fall short of the supply.

## Checksum addresses

`hashlib` does not provide Keccak-256 (`sha3_256` is NIST SHA-3, which pads differently), so `snapshot.py` contains a small pure-Python Keccak-256 used only to compute the EIP-55 checksum casing. It was verified against the empty-input Keccak-256 vector and the EIP-55 reference addresses.

## Compensation

| Option | CSV | Command |
|---|---|---|
| Option 1 | [`option-1.csv`](option-1.csv) | `python3 scripts/option_1.py` |
| Option 2 | [`option-2.csv`](option-2.csv) | `python3 scripts/option_2.py` |
| Option 3 | [`option-3.csv`](option-3.csv) | `python3 scripts/option_3.py` |

- `scripts/option_1.py` writes `option-1.csv` from `data/` and `inputs/` without network access.
- `scripts/option_2.py` reads `option-1.csv` and writes `option-2.csv`.
- `scripts/option_3.py` reads `option-1.csv` and writes `option-3.csv`.

The pipeline behind `data/` and `inputs/`, run from the repository root (they read `snapshot.csv`, call an Ethereum JSON-RPC endpoint set in `RPC`, comma-separated, public endpoints by default, and keep intermediate JSON in `data/`):

1. `scripts/hack_window.py`: the blocks of the announcement and of the end of the window.
2. `scripts/fetch_transactions.py`: every WGNK transfer in the window with its transaction and receipt.
3. `scripts/liquidity_positions.py`: the WGNK in liquidity pools attributed to the owners of the positions.
4. `scripts/contract_codes.py`: which addresses are EOAs, EIP-7702 EOAs or contracts.
5. `scripts/prices.py`: the pre-hack rate and the ETH and WBTC prices.
6. `scripts/post_hack_buys.py`: the buys after the hack, with what was paid; writes `data/post-hack-buys.json`.
7. `scripts/trades_per_address.py`: buys and sales per address; writes `data/trades-per-address.csv`.

`data/post-hack-buys.json` and `data/trades-per-address.csv` are committed, so `scripts/option_1.py` runs without network. `inputs/` holds the per-transaction corrections and the bridge-burn verdicts that `scripts/option_1.py` reads; `limit-orders/` holds tables about the limit-order buys after the hack, and no script reads them.
