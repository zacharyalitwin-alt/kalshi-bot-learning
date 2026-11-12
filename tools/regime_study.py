"""
regime_study.py - does each bot do better in certain market conditions?

Before building a "manager" that puts one bot up to bat and benches the others,
this checks whether that would actually help on data the rules never saw.

For every finished window it records:
  - what each bot would have made in that window (fees included), and
  - market conditions known BEFORE the window opened:
      vol      BTC volatility over the previous 30 minutes
      trend    how far BTC moved over the previous hour ($, either direction)
      range    how much Kalshi's price swung in the previous window (cents)
      session  US hours (13:00-21:00 UTC) or off-hours
      form     did this bot make money over its previous 4 traded windows?
Each condition is split into low / middle / high (thresholds set on TRAIN only).

Then, using TRAIN windows only, it picks for each bot the one condition + bucket
where the bot did best, and checks that rule on the unseen TEST windows against
simply running the bot all the time. Finally it tests a manager that, in each
window, plays the bot whose rule says "on" (best TRAIN average if several do),
and benches everything otherwise.

    python regime_study.py
    python regime_study.py --since "2026-11-08 10:00"
    python regime_study.py --last 96

Research only. Places no orders.
"""
import bisect, math, os, random, statistics, sys

import backtest_v3 as bt
import strategy_lab as sl

TRAIN_FRAC = 0.60
MIN_ON = 25                 # windows a rule must switch on in TRAIN to count
STRONG_T = 2.0

# the bots, exactly as they run (cents per 1 contract, fees included)
def bot_follow(win, feats):
    tr = sl.run("follow", {"move": 25, "band": (30, 75), "exit": "hold"}, win, feats)
    return sum(p for p, _ in tr) / sl.CONTRACTS, len(tr)

def bot_taker(win, feats):
    tr = bt.taker(win, 1.0, 0.12)
    return float(sum(tr)), len(tr)

BOTS = {"follow (move 25c)": bot_follow, "taker (k1.0 edge.12)": bot_taker}


def indicators(win, prev, ts_all, p_all):
    """Conditions known before this window opened."""
    t0 = win["close_ts"] - 900
    out = {}
    i1 = bisect.bisect_left(ts_all, t0)
    i0 = bisect.bisect_left(ts_all, t0 - 1800)
    pts, last = [], None
    for j in range(i0, i1):
        if last is None or ts_all[j] - last >= 10:
            pts.append((ts_all[j], p_all[j])); last = ts_all[j]
    if len(pts) >= 60:
        lr = [math.log(b[1] / a[1]) ** 2 / (b[0] - a[0]) for a, b in zip(pts, pts[1:])]
        out["vol"] = math.sqrt(sum(lr) / len(lr)) * 1e4
    ih = bisect.bisect_left(ts_all, t0 - 3600)
    if i1 - ih > 1000 and ts_all[ih] <= t0 - 3300:
        out["trend"] = abs(p_all[i1 - 1] - p_all[ih])
    if prev is not None and win["close_ts"] - prev["close_ts"] <= 905:
        mids = [(r["yb"] + 100 - r["nb"]) / 2 for r in prev["rows"] if r["yb"] and r["nb"]]
        if len(mids) > 100:
            out["range"] = max(mids) - min(mids)
    hour = int((t0 % 86400) // 3600)
    out["session"] = 1.0 if 13 <= hour < 21 else 0.0
    return out


def bucket(x, cuts):
    if x is None:
        return None
    if cuts is None:                          # session: 0 / 1
        return int(x)
    return 0 if x < cuts[0] else (1 if x < cuts[1] else 2)


def tstat(v):
    if len(v) < 3:
        return 0.0
    sd = statistics.pstdev(v)
    return statistics.mean(v) / sd * math.sqrt(len(v)) if sd > 0 else 0.0


def fmt(v):
    if not v:
        return "  0 windows"
    return f"{len(v):3d} windows  total {sum(v):+7.0f}c  avg {statistics.mean(v):+5.1f}c  t {tstat(v):+4.1f}"


def study(rows_by_win, names, cut, shuffle=False, P=print):
    train, test = rows_by_win[:cut], rows_by_win[cut:]
    if shuffle:                               # null check: conditions no longer match their windows
        keys = [w["ind"] for w in rows_by_win]
        random.shuffle(keys)
        for w, k in zip(rows_by_win, keys):
            w["ind"] = k
    conds = ["vol", "trend", "range", "session", "form"]
    cuts = {}
    for c in conds:
        if c == "session":
            cuts[c] = None
            continue
        vals = sorted(w["ind"].get(c) for w in train if w["ind"].get(c) is not None) if c != "form" else None
        if c == "form":
            cuts[c] = None
        elif vals and len(vals) >= 30:
            cuts[c] = (vals[len(vals) // 3], vals[2 * len(vals) // 3])
    labels = {0: "low", 1: "middle", 2: "high"}

    rules = {}
    for b in names:
        always_tr = [w["pnl"][b] for w in train]
        always_te = [w["pnl"][b] for w in test]
        best = None
        for c in conds:
            if c not in cuts:
                continue
            nb = 2 if c in ("session", "form") else 3
            for k in range(nb):
                on = [w["pnl"][b] for w in train if bucket(w["ind"].get(c) if c != "form" else w["form"][b], cuts[c]) == k]
                if len(on) >= MIN_ON and (best is None or tstat(on) > best[2]):
                    best = (c, k, tstat(on), statistics.mean(on))
        rules[b] = best
        P(f"\n  {b}")
        P(f"    always on   TRAIN {fmt(always_tr)}")
        P(f"                TEST  {fmt(always_te)}")
        if best:
            c, k, t, m = best
            name = {"session": {0: "off-hours", 1: "US hours"}, "form": {0: "after losing", 1: "after winning"}}.get(c, labels)[k]
            on_te = [w["pnl"][b] for w in test if bucket(w["ind"].get(c) if c != "form" else w["form"][b], cuts[c]) == k]
            on_tr = [w["pnl"][b] for w in train if bucket(w["ind"].get(c) if c != "form" else w["form"][b], cuts[c]) == k]
            P(f"    best rule: play only when {c} is {name}")
            P(f"                TRAIN {fmt(on_tr)}")
            P(f"                TEST  {fmt(on_te)}")

    # the manager: in each window, play the bot whose rule is on (best TRAIN average), else bench all
    def manager(ws):
        out = []
        for w in ws:
            pick = None
            for b, r in rules.items():
                if not r:
                    continue
                c, k, t, m = r
                if bucket(w["ind"].get(c) if c != "form" else w["form"][b], cuts[c]) == k and m > 0 and t > 0:
                    if pick is None or m > pick[1]:
                        pick = (b, m)
            out.append(w["pnl"][pick[0]] if pick else 0.0)
        return out
    m_tr, m_te = manager(train), manager(test)
    played = sum(1 for w, v in zip(test, m_te) if v != 0)
    P(f"\n  MANAGER (one bot up to bat per window, or bench all)")
    P(f"                TRAIN total {sum(m_tr):+7.0f}c   TEST total {sum(m_te):+7.0f}c  (played in {played} of {len(test)} TEST windows)")
    best_single = max(names, key=lambda b: sum(w["pnl"][b] for w in test))
    return sum(m_te), {b: sum(w["pnl"][b] for w in test) for b in names}, m_te


def main():
    rows, res = bt.load()
    if not rows:
        print("No recordings found."); return
    bt.add_vol(rows)
    wins = bt.build_windows(rows, res)
    out = []
    P = out.append
    P(f"REGIME STUDY  |  {len(wins)} finished windows  |  P&L in cents per 1 contract, fees included")
    if len(wins) < 100:
        P("Need at least 100 finished windows (about 25 hours) for this to mean anything. Keep recording.")
        print("\n".join(out)); return
    ts_all = [r["ts"] for r in rows]
    p_all = [r["p"] for r in rows]
    names = list(BOTS)
    data, prev, hist = [], None, {b: [] for b in names}
    print(f"Scoring {len(wins)} windows ...")
    for w in wins:
        feats = sl.features(w)
        pnl, form = {}, {}
        for b, fn in BOTS.items():
            v, n = fn(w, feats)
            recent = [x for x in hist[b] if x is not None][-4:]
            form[b] = (1.0 if sum(recent) > 0 else 0.0) if len(recent) == 4 else None
            pnl[b] = v
            hist[b].append(v if n else None)
        data.append({"pnl": pnl, "form": form, "ind": indicators(w, prev, ts_all, p_all)})
        prev = w
    cut = int(len(data) * TRAIN_FRAC)
    P(f"TRAIN = first {cut} windows, TEST = last {len(data) - cut} (rules chosen on TRAIN only)")
    P("Each rule = one condition + one bucket. 12-13 rules tried per bot, so some will look good by luck.")
    m_te, single_te, m_list = study(data, names, cut, P=P)

    # null check: same study with conditions shuffled across windows
    random.seed(7)
    null = []
    for _ in range(20):
        shuffled = [{"pnl": d["pnl"], "form": d["form"], "ind": dict(d["ind"])} for d in data]
        v, _, _ = study(shuffled, names, cut, shuffle=True, P=lambda *a: None)
        null.append(v)
    better = sum(1 for v in null if v >= m_te)
    P(f"\n  LUCK CHECK: with the market conditions shuffled at random, the manager did as well or better")
    P(f"  in {better} of 20 tries. (A real regime effect should beat almost all of them.)")
    best_name = max(single_te, key=single_te.get)
    P("")
    if m_te > max(0, max(single_te.values())) and better <= 1:
        P(f"VERDICT: switching by market conditions HELPED on unseen data (manager {m_te:+.0f}c vs best single bot"
          f" {single_te[best_name]:+.0f}c, and it beat shuffled conditions). Worth building the manager, then forward-testing it.")
    elif m_te > 0 and m_te > max(single_te.values()):
        P(f"VERDICT: the manager beat every single bot on TEST ({m_te:+.0f}c), but shuffled conditions did as well"
          f" {better} of 20 times, so it may be luck. Keep recording and re-run.")
    else:
        P(f"VERDICT: no regime rule clearly helped on unseen data (manager {m_te:+.0f}c, best single bot"
          f" {best_name} {single_te[best_name]:+.0f}c). A manager would add rules without adding edge.")
    text = "\n".join(out)
    print(text)
    with open(os.path.join(bt.FOLDER, "regime_report.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\nReport saved: {os.path.join(bt.FOLDER, 'regime_report.txt')}")


if __name__ == "__main__":
    bt.parse_args(sys.argv[1:])
    main()
