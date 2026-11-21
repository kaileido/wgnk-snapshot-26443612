"""The Option 3 CSV from the Option 1 CSV.

Usage: python3 scripts/option_3.py [OPTION1_CSV [OUT_CSV]]

Defaults: OPTION1_CSV = option-1.csv (the output of scripts/option_1.py), OUT_CSV = option-3.csv.

    python3 scripts/option_1.py
    python3 scripts/option_3.py

Rules, per row of the Option 1 CSV, R = prehack_rate_usd = 0.14272 USD per WGNK:
- Rows with wgnk_before_hack = 0 are dropped.
- V = prehack_value_usd, G = deduction_usd if deduction_reason contains gnk_burn, else 0.
- T3 = max(0, V - G); T3 = 0 if the Option 1 decision is unattributed_pool_liquidity or deduction_reason contains wgnk_token_contract; T3 = 0 if T3 < 1 USD.
- compensation_usdt = T3, compensation_wgnk = T3 / R rounded down to 9 decimals.
- decision is refund_wgnk where T3 > 0 and not_eligible otherwise.
- Rows are sorted by compensation_wgnk descending, then wgnk_before_hack descending, then address.
"""
import csv
import sys
from decimal import Decimal as D, ROUND_DOWN, getcontext

getcontext().prec = 60
WEI9 = D("0.000000001")
COLS = ["address", "wgnk_before_hack", "prehack_rate_usd", "prehack_value_usd", "gnk_burn_deduction_usd", "compensation_usdt", "compensation_wgnk", "decision"]


def option3_refund(r, g):
    """T3 in USD for one Option 1 row, g = its GNK burn deduction."""
    if r["decision"] == "unattributed_pool_liquidity" or "wgnk_token_contract" in r["deduction_reason"]:
        return D(0)
    t = max(D(0), D(r["prehack_value_usd"]) - g)
    return t if t >= 1 else D(0)


def main(src, dst):
    with open(src, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if D(r["wgnk_before_hack"]) == 0:
            continue
        g = D(r["deduction_usd"]) if "gnk_burn" in r["deduction_reason"] else D(0)
        t = option3_refund(r, g)
        wgnk = (t / D(r["prehack_rate_usd"])).quantize(WEI9, ROUND_DOWN)
        out.append({**r, "gnk_burn_deduction_usd": f"{g}" if g else "0", "compensation_usdt": f"{t:.6f}", "compensation_wgnk": f"{wgnk:.9f}",
                    "decision": "refund_wgnk" if t else "not_eligible"})
    out.sort(key=lambda x: (-D(x["compensation_wgnk"]), -D(x["wgnk_before_hack"]), x["address"].lower()))
    with open(dst, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLS)
        for x in out:
            w.writerow([x[c] for c in COLS])
    eligible = sum(x["decision"] == "refund_wgnk" for x in out)
    print("rows", len(out), "eligible", eligible, "not eligible", len(out) - eligible,
          "USDT", sum(D(x["compensation_usdt"]) for x in out), "WGNK", sum(D(x["compensation_wgnk"]) for x in out), "->", dst)


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) > 2:
        sys.exit(__doc__)
    main(*args, *["option-1.csv", "option-3.csv"][len(args):])
