# Compensation options

Each option has its own CSV in the repository root, built on `snapshot.csv`.

## Option 1: WGNK held before the hack and bought after it, minus sales after the hack

`option-1.csv` has one row per address that held or traded WGNK, snapshot block 26443612, with its compensation in WGNK and the value of that amount in USDT.

### CSV columns

| column | meaning |
|---|---|
| `address` | the address (EIP-55 checksum form) |
| `wgnk_refund` | compensation in WGNK: T / 0.14272, rounded down to 9 decimals |
| `usdt_refund` | `wgnk_refund` x 0.14272, rounded to 6 decimals: its value in USDT |
| `sold_after_hack_usdt` | USDT received for WGNK sold after the hack |
| `wgnk_before_hack` | WGNK held at the end of block 26443612, plus the WGNK of its liquidity positions |
| `prehack_rate_usd` | 0.14272 |
| `bought_after_hack_wgnk` | WGNK bought after the hack |
| `buys_after_hack_count` | number of those buys |
| `usd_paid_after_hack` | USD paid for those buys |
| `sold_after_hack_wgnk` | WGNK sold or sent away after the hack |
| `sells_after_hack_count` | number of those sales |
| `balance_at_0030_utc_wgnk` | balance at block 26445549 (information only) |
| `decision` | `refund_wgnk` (eligible) or the reason for no refund |
| `reason` | the facts for the address and the calculation of its amount |
| `own_second_address` | the address's own second addresses, separated by `;` |
| `bought_after_announcement_wgnk` | the part of the buys made after block 26445519 |
| `first_buy_after_hack_tx` | the first buy after the hack |
| `evidence_tx` | the largest sale, otherwise the largest buy, otherwise the transaction that opened its largest liquidity position |
| `refund_for_prehack_wgnk` | the part of `wgnk_refund` for WGNK held before the hack |
| `refund_for_post_hack_buys_wgnk` | the rest of `wgnk_refund`, for WGNK bought after the hack |
| `prehack_value_usd` | `wgnk_before_hack` x 0.14272, rounded to 6 decimals |
| `matched_wgnk` | WGNK bought and sold again after the hack |
| `counted_paid_usd` | USD paid for the buys that count (0 for bots and contracts) |
| `counted_sold_usdt` | USDT received for the sales that count |
| `deduction_usd` | `prehack_value_usd` + `counted_paid_usd` - `counted_sold_usdt` - `roundtrip_profit_usd` - `usdt_refund` |
| `deduction_reason` | why there is a deduction, codes joined with `+`: `gnk_burn`, `below_1_usd`, `round_trip_no_prehack`, `wgnk_token_contract`, `rounding` |
| `roundtrip_profit_usd` | round-trip profit subtracted |

### Rules

- One row per address that held or traded WGNK, snapshot block 26443612.
- WGNK held before the hack is valued at 0.14272 USDT per WGNK.
- The amount paid for WGNK bought after the hack is added; the amount received for WGNK sold after the hack is subtracted.
- Round-trip profit after the hack (bought cheaper, sold dearer) is subtracted.
- GNK already paid out for a burn is subtracted.
- Bots and contracts get nothing for WGNK bought after the hack.
- An address is eligible only when its CSV decision is refund_wgnk; refunds under 1 USD are not paid.

Totals: 1,238 addresses, 804 eligible, 434 not eligible; compensation 13,853,398.271234433 WGNK, 1,977,157.001269 USDT (sums of `wgnk_refund` and `usdt_refund` over all rows).
