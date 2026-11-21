"""Option 1: the refund per wallet, only in WGNK in the new contract, and the CSV option-1.csv (OUT overrides the path); the usdt_refund column is that WGNK refund valued at the pre-hack rate, nobody receives USDT.

Inputs (read only, paths relative to the repository root; DATA defaults to data, as in common.py):
- DATA/trades-per-address.csv, written by trades_per_address.py: A = wgnk_before_hack, P = usd_paid_after_hack, b, s, the trades_per_address.py decision and the information columns, per wallet.
- DATA/post-hack-buys.json, written by post_hack_buys.py: its "outflows" give usd_received per transaction in which a wallet's net WGNK went down; S is their sum per wallet. Its buys must reproduce P of trades-per-address.csv for every wallet (checked below).
- inputs/audit-corrections.json: per-transaction corrections A, B, D and F below.
- inputs/bridge-burn-verdicts.csv: the review verdict of every bridge burn, for correction C.

Rules, R = 0.14272 USD per WGNK:
- Post-hack buys that were sold again are matched by WGNK quantity and count neither as a buy nor as a sale: b = bought_after_hack_wgnk, s = sold_after_hack_wgnk (trades_per_address.py columns), m = min(b, s); P_eff = P * (b - m) / b (0 if b = 0), the unsold part of the buys; S_eff = S * (s - m) / s (0 if s = 0), the sales beyond what was bought after the hack.
- V = A * R. T = max(0, V + P_eff - S_eff) USD; T = 0 if T < 1 USD. wgnk_refund = T / R. usdt_refund = wgnk_refund * R (= T): the value of the WGNK refund at the pre-hack rate, nobody receives USDT.
- T equals max(0, P_eff - S_eff) + max(0, V - max(0, S_eff - P_eff)); refund() asserts that on every row.
- Bots and contracts (trades_per_address.py decisions *_bot_or_contract) get nothing for their post-hack buys: their P_eff counts as 0 here, so T = max(0, V - S_eff).
- Round-trip profit: a wallet that bought cheaper and sold dearer after the hack already earned on the matched coins, and that profit comes off its refund. R = S * m / s - P * m / b (the money received for the m matched WGNK minus the money paid for them, with the real P and S, also for bots and contracts); if R > 0, T = max(0, V + P_eff - S_eff - R), before the GNK deduction and the 1 USD threshold; if R <= 0 the loss is not refunded and nothing changes. Pool rows are not matched, so R = 0 there. roundtrip_profit_usd is the part of R that was taken off: R, but not more than max(0, V + P_eff - S_eff), since the refund cannot go below 0 (this gives the same T as the full R); it is 0 on the rows that get nothing by the token-contract or no-pre-hack round-trip rule, whatever R is. A wallet whose refund drops to 0 only because of R gets the decision no_refund_roundtrip_profit.
- A wallet with no WGNK before the hack (A = 0) that both bought and sold WGNK after the hack (b > 0 and s > 0) gets no refund at all, however much it sold (no_refund_round_trip).
- The balance at 00:30 UTC is information only.
- Split of wgnk_refund (derived columns): sales beyond the matched quantity are sales of the pre-hack balance, so the pre-hack part is min(T, max(0, V - S_eff)) USD and the post-hack part is T - that; refund_for_prehack_wgnk = pre-hack part / R rounded down to 9 decimals and refund_for_post_hack_buys_wgnk = wgnk_refund - refund_for_prehack_wgnk, so the two add up to wgnk_refund exactly and the rounding remainder (under 1e-9 WGNK) falls on the post-hack part. Bots (P_eff counts as 0) and pool rows (no buys) therefore get only a pre-hack part.

Corrections, the per-transaction facts in inputs/audit-corrections.json:
- A. A buy paid with a token the scripts cannot price is valued at the buyer's share of the USD that reached the WGNK pools in the same receipt (unpriced_buys); P includes it.
- B. WGNK bought after the hack and sent without payment to an address that burned it in a rejected burn or still holds it at block 26445549 is not sold (transfers_not_sales): its quantity leaves s and the sells count.
- C. A burn of the row's address with verdict accept or already processed in inputs/bridge-burn-verdicts.csv was compensated in GNK: G = clean_covered for accept, the whole amount for already processed (110,000 WGNK released though only 7,574.33 was pre-hack), and T = max(0, V + P_eff - S_eff - G * R) before the 1 USD threshold.
- D. A deposit into Uniswap V4 liquidity is not a sale (lp_deposits): its quantity leaves s and the sells count; no position of these rows was withdrawn before the cutoff.
- E. The WGNK token contract's own address gets nothing (no_refund_bot_or_contract).
- F. A sale through a relayer whose proceeds were paid elsewhere gets its pool-side value as proceeds (relayer_sales); S includes it.
"""
import collections
import csv
import json
import os
from decimal import Decimal as D, ROUND_DOWN, getcontext
from pathlib import Path

getcontext().prec = 60
DATA = Path(os.environ.get("DATA", "data"))
V2_CSV = DATA / "trades-per-address.csv"
OUT = Path(os.environ.get("OUT", "option-1.csv"))
CORR = json.loads(Path("inputs/audit-corrections.json").read_text())
VERDICTS = Path("inputs/bridge-burn-verdicts.csv")
WGNK_TOKEN = "0x972a7a92d92796a98801a8818bcf91f1648f2f68"
R = D("0.14272")
ONE = D(1)
WEI9, CENT6 = D("0.000000001"), D("0.000001")
BOT = {"no_compensation_bot_or_contract", "refund_prehack_only_bot_or_contract"}
KNOWN = {"refund_prehack_balance", "no_compensation_sold_after_hack", "reprice_buys", "refund_prehack_and_reprice_buys", "refund_prehack_only_sold_after_hack",
         "unattributed_pool_liquidity"} | BOT
COLS = ["address", "wgnk_refund", "usdt_refund", "sold_after_hack_usdt", "wgnk_before_hack", "prehack_rate_usd", "bought_after_hack_wgnk", "buys_after_hack_count",
        "usd_paid_after_hack", "sold_after_hack_wgnk", "sells_after_hack_count", "balance_at_0030_utc_wgnk", "decision", "reason", "own_second_address",
        "bought_after_announcement_wgnk", "first_buy_after_hack_tx", "evidence_tx", "refund_for_prehack_wgnk", "refund_for_post_hack_buys_wgnk",
        "prehack_value_usd", "matched_wgnk", "counted_paid_usd", "counted_sold_usdt", "deduction_usd", "deduction_reason", "roundtrip_profit_usd"]


def refund(V, P, S, rate, G=D(0), Rp=D(0)):
    """(T before the threshold, T, wgnk_refund, usdt_refund) for pre-hack value V, post-hack payments P, sales S, value G already compensated in GNK and round-trip profit Rp taken off, all in USD."""
    t_base = max(D(0), V + P - S - Rp)
    assert t_base == max(D(0), P - S - Rp) + max(D(0), V - max(D(0), S + Rp - P)), (V, P, S, Rp)
    t_raw = max(D(0), t_base - G)
    t = t_raw if t_raw >= ONE else D(0)
    wgnk = (t / rate).quantize(WEI9, ROUND_DOWN)
    return t_raw, t, wgnk, (wgnk * rate).quantize(CENT6)


def matched(P, S, b, s):
    """(P_eff, S_eff): post-hack payments P and sales S in USD without the part of the trades where WGNK bought after the hack (b) was sold again (s), matched by quantity."""
    m = min(b, s)
    return (P * (b - m) / b if b else D(0)), (S * (s - m) / s if s else D(0))


def roundtrip_profit(P, S, b, s):
    """R: the USD received for the m = min(b, s) WGNK bought after the hack and sold again minus the USD paid for them, with post-hack payments P for b WGNK and sales S for s WGNK; negative for a loss."""
    m = min(b, s)
    return (S * m / s if s else D(0)) - (P * m / b if b else D(0))


def split_refund(V, S_eff, T, wgnk, rate):
    """(refund_for_prehack_wgnk, refund_for_post_hack_buys_wgnk) of the refund wgnk = T / rate: sales beyond the matched quantity are sales of the pre-hack balance, so the pre-hack part is min(T, max(0, V - S_eff)) USD; the post-hack part is the rest, so the rounding remainder falls on it."""
    pre = (min(T, max(D(0), V - S_eff)) / rate).quantize(WEI9, ROUND_DOWN)
    return pre, wgnk - pre


# worked example 1: 1000 WGNK at 0.14 (140 USD), nothing bought, sold after the hack for 40 USD -> 100 USD = 714.2857 WGNK, USDT column 100
ex = refund(D(1000) * D("0.14"), *matched(D(0), D(40), D(0), D(1000)), D("0.14"))
assert ex == (D(100), D(100), D("714.285714285"), D(100)), ex
# worked example 2: 1000 WGNK before the hack (142.72 USD), bought 350 WGNK for 50 USD, sold 140 of them for 20 USD -> P_eff 30, S_eff 0 -> 172.72 USD = 1,210.201793721 WGNK, USDT column 172.72
assert matched(D(50), D(20), D(350), D(140)) == (D(30), D(0))
ex2 = refund(D(1000) * R, *matched(D(50), D(20), D(350), D(140)), R)
assert ex2 == (D("172.72"), D("172.72"), D("1210.201793721"), D("172.72")), ex2
# its split: the pre-hack balance 142.72 USD = 1,000 WGNK, the unsold buys the rest, 210.201793721 WGNK
assert split_refund(D(1000) * R, D(0), ex2[1], ex2[2], R) == (D(1000), D("210.201793721"))
# worked example 3, the round trip: bought 32,983.944 WGNK for 884.94 USD and sold the same 32,983.944 WGNK for 804.72 USD -> 0
ex3 = refund(D(0), *matched(D("884.94"), D("804.72"), D("32983.944"), D("32983.944")), R)
assert ex3 == (D(0), D(0), D(0), D(0)), ex3
# worked example 4 (B): bought 1000 WGNK for 100 USD and sent all of it without payment to an address that burned it in a rejected burn -> the transfer is not a sale, s = 1000 - 1000 = 0 -> 100 USD = 700.672645739 WGNK
ex4 = refund(D(0), *matched(D(100), D(0), D(1000), D(1000) - D(1000)), R)
assert ex4 == (D(100), D(100), D("700.672645739"), D(100)), ex4
# worked example 5 (C): 1000 WGNK before the hack, 100 of them burned in an accepted burn -> 1000 x 0.14272 - 100 x 0.14272 = 128.448 USD = 900 WGNK; with the burn of 0x5c2f1496 (110,000 already processed against 1,081 USD of balance) -> 0
ex5 = refund(D(1000) * R, D(0), D(0), R, D(100) * R)
assert ex5 == (D("128.448"), D("128.448"), D(900), D("128.448000")), ex5
assert refund(D("7574.329679719") * R, D(0), D(0), R, D(110000) * R)[1] == 0
# worked example 7, round-trip profit: 1000 WGNK before the hack (142.72 USD), bought 100 WGNK for 5 USD and sold the same 100 WGNK for 15 USD -> R = 10 -> 132.72 USD = 929.932735426 WGNK; bought them for 15 and sold for 5 -> R = -10, the loss is not refunded -> 142.72 USD
assert roundtrip_profit(D(5), D(15), D(100), D(100)) == 10 and roundtrip_profit(D(15), D(5), D(100), D(100)) == -10
ex7 = refund(D(1000) * R, *matched(D(5), D(15), D(100), D(100)), R, D(0), D(10))
assert ex7 == (D("132.72"), D("132.72"), D("929.932735426"), D("132.720000")), ex7
assert refund(D(1000) * R, *matched(D(15), D(5), D(100), D(100)), R)[1] == D("142.72")


def bought_and_sold_without_prehack(A, b, s):
    """True for a wallet with no WGNK before the hack (A) that both bought (b) and sold (s) WGNK after the hack: it gets no refund at all."""
    return A == 0 and b > 0 and s > 0


# worked example 6: no pre-hack balance, bought 1000 WGNK for 100 USD and sold 10 of them for 1 USD -> no refund (the matching alone would give 99 USD); with 1 WGNK before the hack the matching applies
assert refund(D(0), *matched(D(100), D(1), D(1000), D(10)), R)[1] == 99 and bought_and_sold_without_prehack(D(0), D(1000), D(10))
assert not bought_and_sold_without_prehack(D(1), D(1000), D(10)) and not bought_and_sold_without_prehack(D(0), D(1000), D(0))

rows = list(csv.DictReader(V2_CSV.open()))
assert len(rows) == 1238 and {r["decision"] for r in rows} <= KNOWN
B = json.loads((DATA / "post-hack-buys.json").read_text())
paid, nbuys, unpaid = collections.defaultdict(D), collections.Counter(), collections.Counter()
for x in B["buys"]:
    paid[x["buyer"]] += D(x["usd_paid"])
    nbuys[x["buyer"]] += 1
    unpaid[x["buyer"]] += D(x["usd_paid"]) == 0
for r in rows:
    a = r["address"].lower()
    assert paid[a] == D(r["usd_paid_after_hack"]) and nbuys[a] == int(r["buys_after_hack_count"]), f"data/post-hack-buys.json does not reproduce P of trades-per-address.csv for {a}"
S_of = collections.defaultdict(D)
for x in B["outflows"]:
    S_of[x["address"]] += D(x["usd_received"])

buy_of = {(x["tx"], x["buyer"]): x for x in B["buys"]}
out_of = {(x["tx"], x["address"]): x for x in B["outflows"]}
P_add, unpriced_n = collections.defaultdict(D), collections.Counter()
for c in CORR["unpriced_buys"]:  # A
    x = buy_of[(c["tx"], c["address"])]
    assert D(x["usd_paid"]) == 0 and x["unpriced_tokens"], c
    P_add[c["address"]] += D(c["usd"])
    unpriced_n[c["address"]] += 1
not_sold, not_sold_n = collections.defaultdict(D), collections.Counter()
transfer_kind, lp_rows = collections.defaultdict(set), set()
for k, c in [("transfer", c) for c in CORR["transfers_not_sales"]] + [("lp", c) for c in CORR["lp_deposits"]]:  # B and D
    x = out_of[(c["tx"], c["address"])]
    assert D(x["usd_received"]) == 0 and D(x["wgnk"]) / D(10**9) == D(c["wgnk"]), c
    not_sold[c["address"]] += D(c["wgnk"])
    not_sold_n[c["address"]] += 1
    if k == "lp":
        lp_rows.add(c["address"])
    else:
        transfer_kind[c["address"]].add(c["receiver"])
S_add = collections.defaultdict(D)
for c in CORR["relayer_sales"]:  # F
    assert D(out_of[(c["tx"], c["address"])]["usd_received"]) == 0, c
    S_add[c["address"]] += D(c["usd"])
gnk_wgnk = collections.defaultdict(D)
for v in csv.DictReader(VERDICTS.open()):  # C
    if v["verdict"] in ("accept", "already processed"):
        gnk_wgnk[v["sender"].lower()] += D(v["clean_covered"] if v["verdict"] == "accept" else v["amount"])


BOT_LABEL = {"the CoW Protocol settlement contract (a solver).": "CoW Protocol solver", "the Wormhole token bridge.": "Wormhole bridge", "the KyberSwap zap router.": "KyberSwap zap router",
             "a contract without verified source (a trading or MEV contract).": "contract without verified source",
             "a contract whose source verification could not be confirmed, treated as a trading contract.": "contract with unconfirmed source",
             "a bot that bought right behind the attacker's dump in the same block.": "bot right behind the attacker's dump"}


def money(x):
    return f"{x:,.2f}"


def bot_label(r):
    """Short label of a bot or contract from its trades_per_address.py reason."""
    label = r["reason"].split("because the buyer is ")[1]
    return BOT_LABEL.get(label) or "bot behind the attacker's dump in block " + label.split("block ")[1].split(" ")[0]


def lp_pool(r):
    """The pool named in the trades_per_address.py reason of a pre-hack holder with liquidity, or ''."""
    en = r["reason"]
    if r["decision"] != "refund_prehack_balance" or " as liquidity in the " not in en:
        return ""
    parts = en.split("WGNK before the hack, ")[1].split(", refunded in full")[0].split(", ")
    assert len(parts) == 1, parts
    return parts[0].split("liquidity in the ")[1]


def total_sum(V, P, S, T, rt, G=D(0), Rp=D(0)):
    """'Pre-hack balance 140.00 + buys 50.00 - sales 40.00 = 150.00 USD' with only the nonzero terms; rt: part of the post-hack trades was a round trip, so P and S are the effective terms; G: value already compensated in GNK; Rp: round-trip profit taken off."""
    Rp = Rp if money(Rp) != "0.00" else D(0)  # a profit under half a cent is left out of the sentence
    terms = [f"{k} {money(x)}" for k, x in (("pre-hack balance", V), ("unsold part of buys" if rt else "buys", P)) if x]
    s = " + ".join(terms) + (f" - {sales_label(rt)} {money(S)}" if S else "") + (f" - round-trip profit {money(Rp)}" if Rp else "") + (f" - compensated in GNK {money(G)}" if G else "")
    s = s[0].upper() + s[1:]
    return f"{s} USD" if len(terms) == 1 and not S and not G and not Rp else f"{s} = {money(T)} USD"


def under_one(t):
    """'Total 0.42 USD, under 1 USD.', rounded down so 0.999 does not print as 1.00."""
    return f"Total {money(t.quantize(D('0.01'), ROUND_DOWN))} USD, under 1 USD."


def sales_label(rt):
    return "sales beyond what was bought" if rt else "sales"


def sold_covers(V, P, S, rt):
    held = " and ".join(k for k, x in (("the balance", V), ("unsold buys" if rt else "buys", P)) if x)
    sales = sales_label(rt).capitalize()
    return f"{sales} {money(S)} USD are at least {held} {money(V + P)} USD." if held else f"{sales} {money(S)} USD, with no pre-hack balance and no paid buys."


def round_trip(S):
    """The sentence for post-hack buys that were all sold again; S = 0 means the trades_per_address.py sold quantity left only by unpaid transfers."""
    return "Bought after the hack and moved what it bought to another address without payment, the trade is not counted." if not S else "Bought after the hack and sold what it bought, the trade is not counted."


TRANSFER_EN = {"holds": "The transfer to an address that still holds the WGNK is not a sale.", "burned_rejected": "The transfer to an address that burned the WGNK in a rejected burn is not a sale."}


def correction_notes(fix):
    """The sentences for the audit corrections A, B, C, D and F of one row (fix: its entries)."""
    notes = ["Its bridge burn was already compensated in GNK."] if fix["G"] else []
    if fix["unpriced"]:
        notes.append("Buys paid with a token without a price are valued at the USDT that reached the pool.")
    notes += [TRANSFER_EN[k] for k in sorted(fix["transfers"])]
    if fix["lp"]:
        notes.append("A deposit into Uniswap V4 liquidity is not a sale.")
    if fix["relayer"]:
        notes.append("The sale went through a relayer, its proceeds are what the pool paid.")
    return notes


def short_reason(r, decision, V, P_elig, S, t_raw, unpaid_n, bot, rt, all_sold, S_raw, fix, Rp):
    """One short sentence on why this row got what it got, using only what the other columns do not show; P_elig and S are the effective terms, rt: some post-hack WGNK was bought and sold again, all_sold: all of it; fix: the row's audit corrections; Rp: the round-trip profit taken off."""
    if fix["token"]:
        return "The WGNK sits on the WGNK token contract itself, nobody can claim a refund."
    if decision == "unattributed_pool_liquidity":
        en = r["reason"]
        s = "The pair has no LP token, its owners cannot be determined." if en.startswith("The pair has no LP token") else "Residual of the " + en.split("WGNK left in the ")[1].split(" after crediting")[0] + " with no position owner."
        return s if t_raw >= ONE else s + " " + under_one(t_raw)
    notes, G = [], fix["G"]
    if decision == "no_refund_round_trip":
        notes.append("No WGNK before the hack, bought and sold after the hack, no refund.")
    elif decision in ("refund_wgnk", "no_refund_burn_compensated_in_gnk", "no_refund_roundtrip_profit"):
        notes.append(total_sum(V, P_elig, S, t_raw, rt, G, Rp) + ".")
        if lp_pool(r):
            notes.append(f"Part of the balance is liquidity in the {lp_pool(r)}.")
    elif decision == "no_refund_below_threshold":
        notes.append(total_sum(V, P_elig, S, t_raw.quantize(D("0.01"), ROUND_DOWN), rt, G, Rp) + ", under 1 USD." if rt and (V or P_elig or S) else under_one(t_raw))
    elif decision == "no_refund_sold_covers":
        notes.append(sold_covers(V, P_elig, S, rt))
    if all_sold and not bot and decision != "no_refund_round_trip":
        notes.append(round_trip(S_raw))
    notes += correction_notes(fix)
    if bot:
        notes.append(f"Bot or contract: {bot_label(r)}, post-hack buys not counted.")
        if decision == "no_refund_bot_or_contract" and V:
            notes.append(f"{sales_label(rt).capitalize()} cover the pre-hack balance of {money(V)} USD." if S >= V else total_sum(V, D(0), S, t_raw, rt, D(0), Rp) + ", under 1 USD.")
    elif unpaid_n:
        notes.append(f"No payment found for {unpaid_n} buy{'s' if unpaid_n != 1 else ''}.")
    if r["own_second_address"]:
        notes.append("A transfer to its own second address is not a sale.")
    return " ".join(notes)


out = []
for r in rows:
    a, d2 = r["address"].lower(), r["decision"]
    A, P, S = D(r["wgnk_before_hack"]), D(r["usd_paid_after_hack"]) + P_add[a], S_of[a] + S_add[a]
    V = A * R
    bot = d2 in BOT
    b, s = D(r["bought_after_hack_wgnk"]), D(r["sold_after_hack_wgnk"]) - not_sold[a]
    P_eff, S_eff = (P, S) if d2 == "unattributed_pool_liquidity" else matched(P, S, b, s)
    P_elig = D(0) if bot else P_eff
    fix = {"token": a == WGNK_TOKEN, "G": gnk_wgnk[a] * R, "unpriced": unpriced_n[a], "transfers": transfer_kind[a], "lp": a in lp_rows, "relayer": bool(S_add[a])}
    # the round-trip profit, with the real P and S also for bots; taken off only up to what is left, so the refund stays >= 0 (same T as the full R)
    R_full = D(0) if d2 == "unattributed_pool_liquidity" else roundtrip_profit(P, S, b, s)
    R_cut = min(max(D(0), R_full), max(D(0), V + P_elig - S_eff))
    if fix["token"] or bought_and_sold_without_prehack(A, b, s):
        R_cut = D(0)
    t_raw, T, wgnk, usdt = refund(V, P_elig, S_eff, R, fix["G"], R_cut)
    if fix["token"] or bought_and_sold_without_prehack(A, b, s):
        t_raw = T = wgnk = usdt = D(0)
    pre_w, post_w = split_refund(V, S_eff, T, wgnk, R)
    assert pre_w >= 0 and post_w >= 0 and pre_w + post_w == wgnk and (post_w == 0 or not (bot or d2 == "unattributed_pool_liquidity")), a
    if d2 == "unattributed_pool_liquidity":
        decision = d2
    else:
        if wgnk:
            decision = "refund_wgnk"
        elif bot or fix["token"]:
            decision = "no_refund_bot_or_contract"
        elif bought_and_sold_without_prehack(A, b, s):
            decision = "no_refund_round_trip"
        elif R_cut and refund(V, P_elig, S_eff, R, fix["G"])[1]:
            decision = "no_refund_roundtrip_profit"
        elif fix["G"] and refund(V, P_elig, S_eff, R, D(0), R_cut)[1]:
            decision = "no_refund_burn_compensated_in_gnk"
        elif S_eff and S_eff >= V + P_elig:
            decision = "no_refund_sold_covers"
        else:
            decision = "no_refund_below_threshold"
    fixed = {"usd_paid_after_hack": str(P)} if P_add[a] else {}
    if not_sold[a]:
        fixed |= {"sold_after_hack_wgnk": f"{s:.9f}", "sells_after_hack_count": str(int(r["sells_after_hack_count"]) - not_sold_n[a])}
    # the equation columns, all in 6-decimal USD so that V + counted_paid - counted_sold - roundtrip_profit - deduction = usdt_refund holds exactly on the CSV values; the deduction is what lies between V + P_eff - S_eff - R and the refund
    V6, P6, S6 = V.quantize(CENT6), P_elig.quantize(CENT6), S_eff.quantize(CENT6)
    R6 = max(D(0), V6 + P6 - S6) if R_cut and R_cut == V + P_elig - S_eff else R_cut.quantize(CENT6)  # where R takes all that is left, it takes all of the rounded columns too
    ded = V6 + P6 - S6 - R6 - usdt
    if fix["token"]:
        why = ["wgnk_token_contract"]
    elif bought_and_sold_without_prehack(A, b, s):
        why = ["round_trip_no_prehack"]
    else:
        raw = V + P_elig - S_eff - R_cut
        why = (["gnk_burn"] if fix["G"] and raw > 0 else []) + (["below_1_usd"] if 0 < t_raw < ONE else []) + (["sales_exceed"] if raw < 0 else [])
        if not why and ded:
            why = ["rounding"]
    if not ded:  # a code only where an amount was taken off
        why = []
    out.append({**{c: r[c] for c in COLS if c in r}, **fixed, "wgnk_refund": f"{wgnk:.9f}", "usdt_refund": f"{usdt:.6f}", "sold_after_hack_usdt": f"{S.quantize(CENT6):.6f}", "decision": decision,
                "refund_for_prehack_wgnk": f"{pre_w:.9f}", "refund_for_post_hack_buys_wgnk": f"{post_w:.9f}",
                "prehack_value_usd": f"{V6:.6f}", "matched_wgnk": f"{(D(0) if d2 == 'unattributed_pool_liquidity' else min(b, s)):.9f}", "counted_paid_usd": f"{P6:.6f}", "counted_sold_usdt": f"{S6:.6f}", "deduction_usd": f"{ded:.6f}", "deduction_reason": "+".join(why),
                "roundtrip_profit_usd": f"{R6:.6f}",
                "reason": short_reason(r, decision, V, P_elig, S_eff, t_raw, unpaid[a] - unpriced_n[a], bot, bool(b and s), bool(b and s >= b), S, fix, R_cut), "_key": (-wgnk, -A, a)})

row_of = {x["address"].lower(): x for x in out}
# B on its row: 0x8851a692 bought 14,236.137069384 WGNK for 300 USD and sent all of it to 0xbb8b862c, which passed it to 0x55bbc99e, which burned it in two rejected burns -> not sold -> 300 USD = 2,102.017937219 WGNK
assert (row_of["0x8851a6924db7ed6c60ef86377c4ecfe8db4dc89b"]["wgnk_refund"], row_of["0x8851a6924db7ed6c60ef86377c4ecfe8db4dc89b"]["sold_after_hack_wgnk"]) == ("2102.017937219", "0.000000000")
# C on its row: 0x5c2f1496 had its 110,000 WGNK burn already processed in GNK -> 0
assert (row_of["0x5c2f1496c50d884ecbc496a3d47c82da268a3129"]["wgnk_refund"], row_of["0x5c2f1496c50d884ecbc496a3d47c82da268a3129"]["decision"]) == ("0.000000000", "no_refund_burn_compensated_in_gnk")
# the new rule on its row: 0x5ece1060 had no WGNK before the hack, bought 162,247.879 WGNK after the hack for 2,100 USD and sold 11,939.768 of them -> 0 (13,631.317913294 WGNK before this rule)
assert (row_of["0x5ece1060e462d803d1c8f6b66e10118f06e3089b"]["wgnk_refund"], row_of["0x5ece1060e462d803d1c8f6b66e10118f06e3089b"]["decision"]) == ("0.000000000", "no_refund_round_trip")
# the round-trip profit rule on its row: 0x5c4eab54 had 6,206.193115 USD of WGNK before the hack, paid 1,510.356339 USD for 173,530.714230523 WGNK after it and sold 174,000 WGNK for 5,813.806960 USDT -> 6,206.193115 + 1,510.356339 - 5,813.806960 = 1,902.742494 USD (6,190.513018 before this rule)
x5c = row_of["0x5c4eab549a528bf15c47a01e174500010997235c"]
assert abs(D(x5c["usdt_refund"]) - D("1902.742494")) <= D("0.000002") and abs(D(x5c["wgnk_refund"]) - D("1902.742494") / R) < D("0.00002") and x5c["decision"] == "refund_wgnk", x5c
out.sort(key=lambda x: x["_key"])
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(COLS)
    for x in out:
        w.writerow([x[c] for c in COLS])

by = collections.defaultdict(lambda: [0, D(0), D(0), D(0)])
for x in out:
    t = by[x["decision"]]
    t[0] += 1
    t[1] += D(x["wgnk_refund"])
    t[2] += D(x["usdt_refund"])
    t[3] += D(x["sold_after_hack_usdt"])
for k, (n, wg, us, s) in sorted(by.items()):
    print(f"{k:28s} rows {n:5d}  wgnk {wg:>24f}  usdt equivalent {us:>14f}  S {s:>14f}")
print("rows", len(out), "WGNK to allocate", sum(D(x["wgnk_refund"]) for x in out), "USDT equivalent at the pre-hack rate", sum(D(x["usdt_refund"]) for x in out), "->", OUT.name)
print("split of the refund: pre-hack balance", sum(D(x["refund_for_prehack_wgnk"]) for x in out), "+ post-hack buys", sum(D(x["refund_for_post_hack_buys_wgnk"]) for x in out), "WGNK")
print("worked example 1: 1000 WGNK at 0.14, sold for 40 USD -> T", ex[1], "USD =", ex[2], "WGNK, USDT column", ex[3], ": PASS")
print("worked example 2: 1000 WGNK before the hack, bought 350 WGNK for 50 USD, sold 140 WGNK for 20 USD -> T", ex2[1], "USD =", ex2[2], "WGNK (1000 for the pre-hack balance + 210.201793721 for the buys), USDT column", ex2[3], ": PASS")
print("worked example 3: bought 32,983.944 WGNK for 884.94 USD, sold 32,983.944 WGNK for 804.72 USD -> T", ex3[1], "USD =", f"{ex3[2]:f}", "WGNK, USDT column", ex3[3], ": PASS")
print("worked example 4 (B): bought 1000 WGNK for 100 USD, sent to an address that burned it in a rejected burn -> T", ex4[1], "USD =", ex4[2], "WGNK : PASS")
print("worked example 5 (C): 1000 WGNK before the hack, 100 burned in an accepted burn -> T", ex5[1], "USD =", ex5[2], "WGNK; 0x5c2f1496 -> 0 : PASS")
print("worked example 6: no pre-hack balance, bought 1000 WGNK for 100 USD, sold 10 WGNK -> no refund (0 WGNK); with 1 WGNK before the hack the matching applies : PASS")
print("worked example 7: 1000 WGNK before the hack, bought 100 WGNK for 5 USD and sold them for 15 USD -> round-trip profit 10 USD -> T", ex7[1], "USD =", ex7[2], "WGNK; bought for 15 and sold for 5 -> 142.72 USD, the loss is not refunded : PASS")
print("round-trip profit on 0x5c4eab54:", x5c["roundtrip_profit_usd"], "USD taken off ->", x5c["usdt_refund"], "USD =", x5c["wgnk_refund"], "WGNK")
