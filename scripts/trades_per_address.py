"""Per-wallet table (pre-hack balance, post-hack buys and sales, bot and contract labels) with the earlier allocation under the rules below, written to data/trades-per-address.csv; option_1.py applies the current refund rules to it and writes option-1.csv.

Rule 1: every pre-hack holder gets back its WGNK balance at the end of block PRE (pool balances are split among liquidity providers by liquidity_positions.py; what cannot be attributed stays with the pool contract). The attacker cluster is excluded.
Rule 2: WGNK bought in the window by a wallet that made no sale in the window is re-counted at the pre-hack rate: new WGNK = USD paid for those buys / RATE (USD only measures what the buys cost; the result is WGNK in the new contract).
Rule 3: a wallet that sold any WGNK in the window gets nothing for its window buys (a pre-hack holder keeps its rule 1 refund). Any WGNK sent to another address is a sale, except a bridge burn (a transfer to the zero address) and a transfer to the wallet's own second address.
Rule 4: bots, contracts (routers, aggregators, solvers, bridges, trading contracts) get nothing under rule 2.
Rule 5: total WGNK allocation in the new contract = rule 1 + rule 2.

Own second address: an address (an EOA, not a contract) that is not itself a buyer, got at least 95% of its window WGNK inflow from the wallet, and still holds WGNK at the window end beyond its own pre-hack balance (bridge burns count as held). Only the part it still holds is not a sale.
"""
import collections
import csv
from decimal import Decimal as D, ROUND_DOWN, getcontext
from common import *

getcontext().prec = 60
W = window()
END, ANN = W["end"], W["announce"]
RATE_D = D(RATE)
snap = snapshot()
LP = load("lp-positions.json")
B = load("post-hack-buys.json")
CODES = load("codes.json")
R = load("receipts.json")
logs = load("transfers.json")
DUMP_BLOCKS = {26443627, 26443628}  # the attacker's 8,500,000 WGNK dump and the block after it
BACKRUNNER = "0x94fe33075ccda7a456f948828a14e7b7c0707b9b"  # bought right behind the attacker's dump in the same block and sold it all
# names of contract buyers with verified source on the public block explorer; every other contract buyer had no verified source (or, for the three marked, the check did not answer)
CONTRACT_LABEL = {"0x9008d19f58aabd9ed0d60971565aa8510560ab41": "the CoW Protocol settlement contract (a solver)",
                  "0x3ee18b2214aff97000d974cf647e7c347e8fa585": "the Wormhole token bridge", "0x3bdb21ea8793acbf0ce18954865b7572694d037f": "the KyberSwap zap router"}
UNCONFIRMED = {"0xbadc0de76438f9524d42c219b390636196bfbdfc", "0xa243d7d2e2e7984511331dbbf2cf3acfe7dccc96", "0x52b335fd4d229c4b4ffa6190526e4b5fb8e3fb09"}


def wg(raw):
    return fmt(raw)


def usd_str(x):
    return format(x.normalize(), "f") if x else "0"


# ---------------------------------------------------------------- rule 1 base: pre-hack balance with pool WGNK split among liquidity providers
pre = collections.Counter({a: v for a, v in snap.items() if a not in POOLS})
lp_note = collections.defaultdict(list)
lp_tx = {}
for pool, p in LP["pools"].items():
    pre[pool] += p["residual"]
    for r in p["rows"]:
        pre[r["owner"]] += r["wgnk"]
        lp_note[r["owner"]].append((r["wgnk"], POOLS[pool]))
        lp_tx.setdefault(r["owner"], (r["wgnk"], r["tx"]))
        if r["wgnk"] > lp_tx[r["owner"]][0]:
            lp_tx[r["owner"]] = (r["wgnk"], r["tx"])
assert sum(pre.values()) == SUPPLY_PRE, "LP attribution changed the total"

# ---------------------------------------------------------------- replay to the window end
bal = collections.Counter(snap)
burned = collections.Counter()
inflow = collections.defaultdict(collections.Counter)  # receiver -> sender -> raw, in the window
net_tx = collections.defaultdict(collections.Counter)  # tx -> address -> net raw
sent_tx = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))  # tx -> sender -> receiver -> raw
for l in logs:
    a, c, v, h = topic_addr(l["topics"][1]), topic_addr(l["topics"][2]), int(l["data"], 16), l["transactionHash"]
    bal[a] -= v
    bal[c] += v
    inflow[c][a] += v
    net_tx[h][a] -= v
    net_tx[h][c] += v
    sent_tx[h][a][c] += v
    if c == ZERO:
        burned[a] += v

# ---------------------------------------------------------------- buys per wallet
buys = collections.defaultdict(list)
for x in B["buys"]:
    buys[x["buyer"]].append(x)
buyers = set(buys)
rows_addr = sorted({a for a, v in pre.items() if v} | buyers)

bo = load("balanceof-end.json", {})
need = [a for a in rows_addr if a not in bo]
for a, r in zip(need, batch([("eth_call", [{"to": WGNK, "data": sel("balanceOf(address)") + word(a)}, hex(END)]) for a in need])):
    bo[a] = int(r, 16)
save("balanceof-end.json", bo)
mismatch = [a for a in rows_addr if bo[a] != bal[a]]
print("balanceOf at the window end vs replayed balance, mismatches:", len(mismatch))
assert not mismatch

# attacker link test: any token transfer between a buyer and the attacker cluster in a window transaction
linked = {}
for h, r in R.items():
    for l in r["logs"]:
        tp = l["topics"]
        if tp and tp[0] == TRANSFER and len(tp) == 3:
            x, y = topic_addr(tp[1]), topic_addr(tp[2])
            for p, q in ((x, y), (y, x)):
                if p in buyers and q in ATTACKER:
                    linked.setdefault(p, h)
print("buyers linked to the attacker cluster:", len(linked))


def outflows(w):
    """(tx, net raw lost, {receiver: raw sent}) for every window tx in which w's net WGNK went down."""
    out = []
    for h, m in net_tx.items():
        if m.get(w, 0) < 0:
            out.append((h, -m[w], dict(sent_tx[h][w])))
    return sorted(out)


def second_addresses(w, outs):
    sent = collections.Counter()
    for _, _, dst in outs:
        sent.update(dst)
    own = {}
    for c, v in sent.items():
        if c == ZERO or c in POOLS or c in ATTACKER or c in buyers or kind(CODES, c) == "contract":
            continue
        tot = sum(inflow[c].values())
        if tot and inflow[c][w] * 100 >= 95 * tot:
            take = min(v, max(0, bal[c] + burned[c] - snap.get(c, 0)))
            if take > 0:
                own[c] = (v, take)
    return own


rows = []
for w in rows_addr:
    k = kind(CODES, w)
    my = sorted(buys.get(w, []), key=lambda x: (x["block"], x["tx"]))
    bought = sum(x["wgnk"] for x in my)
    paid = sum((D(x["usd_paid"]) for x in my), D(0))
    late = sum(x["wgnk"] for x in my if x["after_announcement"])
    outs = outflows(w) if w not in POOLS else []  # a pool's outflows are swaps and withdrawals, not sales by the pool
    own2 = second_addresses(w, outs)
    sold, n_sells, sale_txs = 0, 0, []
    for h, lost, dst in outs:
        keep = dst.get(ZERO, 0) + sum(own2[c][0] for c in dst if c in own2)
        s = max(0, lost - keep)
        if s:
            sold, n_sells = sold + s, n_sells + 1
            sale_txs.append((s, h))
    for c, (v, take) in own2.items():
        if v > take:  # the second address passed part of it on: that part is a sale
            sold += v - take
            n_sells += 1
    refund = pre.get(w, 0)
    is_pool = w in POOLS
    bot = None
    if my and w not in linked:
        if k == "contract":
            bot = CONTRACT_LABEL.get(w) or ("a contract whose source verification could not be confirmed, treated as a trading contract" if w in UNCONFIRMED else "a contract without verified source (a trading or MEV contract)")
        elif w == BACKRUNNER:
            bot = "a bot that bought right behind the attacker's dump in the same block"
        elif my[0]["block"] in DUMP_BLOCKS and D(my[0]["usd_paid"]) < D("0.1") * RATE_D * D(my[0]["wgnk"]) / D(10) ** 9:
            bot = f"a bot that bought in block {my[0]['block']} (the attacker's dump) at under 10% of the pre-hack rate"
    comp2 = 0
    per_pool = collections.Counter()
    for v, n in lp_note.get(w, []):
        per_pool[n] += v
    lp_txt = "".join(f", {wg(v)} WGNK of it as liquidity in the {n}" for n, v in per_pool.most_common())
    if w in ATTACKER or w in linked:
        decision, refund = "attacker_excluded", 0
        reason = f"Part of the attacker cluster ({ATTACKER[w]}), excluded." if w in ATTACKER else f"Exchanged tokens with the attacker cluster in {linked[w]}, excluded."
    elif is_pool:
        decision = "unattributed_pool_liquidity"
        reason = ("The pair has no LP token or mint events, so its whole pre-hack WGNK balance cannot be attributed to liquidity providers and its payee is not known." if w == V2_PAIR else
                  f"WGNK left in the {POOLS[w]} after crediting every readable liquidity position (protocol fees, rounding, direct transfers), payee not known.")
    elif not my:
        decision = "refund_prehack_balance"
        reason = f"Held {wg(refund)} WGNK before the hack{lp_txt}, refunded in full" + (f" (the {wg(sold)} WGNK it sold or sent after the hack do not reduce it)." if sold else ".")
    elif bot:
        decision = "refund_prehack_only_bot_or_contract" if refund else "no_compensation_bot_or_contract"
        reason = ("Pre-hack balance refunded, but post-hack buys get nothing" if refund else "Post-hack buys get nothing") + f" because the buyer is {bot}."
    elif sold:
        decision = "refund_prehack_only_sold_after_hack" if refund else "no_compensation_sold_after_hack"
        reason = ("Pre-hack balance refunded, but post-hack buys get nothing" if refund else "Post-hack buys get nothing") + f" because it sold or sent {wg(sold)} WGNK after the hack."
    else:
        comp2 = int((paid / RATE_D * D(10) ** 9).to_integral_value(ROUND_DOWN))
        decision = "refund_prehack_and_reprice_buys" if refund else "reprice_buys"
        unpaid_n = sum(1 for x in my if D(x["usd_paid"]) == 0)
        reason = ("Pre-hack balance refunded and post-hack buys" if refund else "Post-hack buys") + f" repriced as {usd_str(paid)} USD paid / {RATE} = {wg(comp2)} WGNK, with no sale after the hack"
        if own2:
            reason += " (WGNK moved to its own second address counts as held)"
        if unpaid_n:
            reason += f"; no payment was found for {unpaid_n} of its {len(my)} buys"
        reason += "."
    total = refund + comp2
    ev = ""
    if w in linked:
        ev = linked[w]
    elif decision in ("no_compensation_sold_after_hack", "refund_prehack_only_sold_after_hack") and sale_txs:
        ev = max(sale_txs)[1]
    elif my:
        ev = max(my, key=lambda x: x["wgnk"])["tx"]
    elif w in lp_tx:
        ev = lp_tx[w][1]
    rows.append({"address": checksum_address(w), "wgnk_before_hack": wg(pre.get(w, 0)), "prehack_rate_usd": RATE, "bought_after_hack_wgnk": wg(bought), "buys_after_hack_count": len(my),
                 "usd_paid_after_hack": usd_str(paid), "sold_after_hack_wgnk": wg(sold), "sells_after_hack_count": n_sells, "balance_at_0030_utc_wgnk": wg(bo[w]),
                 "compensation_for_post_hack_buys_wgnk": wg(comp2), "total_compensation_wgnk": wg(total), "decision": decision, "reason": reason,
                 "own_second_address": ";".join(checksum_address(c) for c in own2), "bought_after_announcement_wgnk": wg(late), "first_buy_after_hack_tx": my[0]["tx"] if my else "", "evidence_tx": ev,
                 "_total": total, "_refund": refund, "_comp2": comp2, "_pre": pre.get(w, 0), "_kind": k, "_unpaid": sum(1 for x in my if D(x["usd_paid"]) == 0), "_unpriced": sum(1 for x in my if x["unpriced_tokens"]),
                 "_third": sum(1 for x in my if x["paid_by"] == "third party in the same tx")})

rows.sort(key=lambda r: (-r["_total"], -int(r["_pre"]), r["address"].lower()))
COLS = ["address", "wgnk_before_hack", "prehack_rate_usd", "bought_after_hack_wgnk", "buys_after_hack_count", "usd_paid_after_hack", "sold_after_hack_wgnk", "sells_after_hack_count",
        "balance_at_0030_utc_wgnk", "compensation_for_post_hack_buys_wgnk", "total_compensation_wgnk", "decision", "reason", "own_second_address",
        "bought_after_announcement_wgnk", "first_buy_after_hack_tx", "evidence_tx"]
out = Path(os.environ.get("OUT", DATA / "trades-per-address.csv"))
with out.open("w", newline="") as f:
    wr = csv.writer(f, lineterminator="\n")
    wr.writerow(COLS)
    for r in rows:
        wr.writerow([r[c] for c in COLS])
save("compensation-rows.json", rows)

# ---------------------------------------------------------------- totals and the supply check
s = sum(r["_pre"] for r in rows)
print("rows", len(rows), "->", out)
print(f"supply check: sum of wgnk_before_hack = {wg(s)}, snapshot totalSupply = {wg(SUPPLY_PRE)}:", "PASS" if s == SUPPLY_PRE else "FAIL")
by = collections.defaultdict(lambda: [0, 0, 0, 0])
for r in rows:
    t = by[r["decision"]]
    t[0] += 1
    t[1] += r["_refund"]
    t[2] += r["_comp2"]
    t[3] += r["_total"]
r1, r2 = sum(r["_refund"] for r in rows), sum(r["_comp2"] for r in rows)
for d, (n, a, b, c) in sorted(by.items()):
    print(f"{d:40s} rows {n:5d}  rule1 {wg(a):>22s}  rule2 {wg(b):>20s}  total {wg(c):>22s} WGNK")
for name, v in (("rule 1", r1), ("rule 2", r2), ("total", r1 + r2)):
    print(f"{name}: {wg(v)} WGNK")
assert s == SUPPLY_PRE
