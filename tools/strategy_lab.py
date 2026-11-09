"""
strategy_lab.py - learns which strategy works on YOUR recorded Kalshi KXBTC15M data.

Run:   python strategy_lab.py
Needs: backtest_v3.py in the same folder, and record_v3.py data in
       data/live  (rec_*.csv + results.csv)
Writes (in that folder):
       lab_report.txt    - every strategy ranked, in % return and $ at 20 contracts
       best_config.json  - the winner, only if it also wins on data it never saw

How it learns without fooling itself
 1. Windows are split by time: the first 60% is TRAIN, the last 40% is TEST.
 2. Each strategy idea is tried with many settings on TRAIN only.
 3. The best TRAIN settings are then run once on TEST, data they never saw.
    A real edge stays positive on TEST. A lucky one usually does not.
 4. Nothing is crowned unless it is STRONG on both parts: "strength" measures
    how unlikely the result is to be luck (2.7+ needed, about a 1% fluke chance).
    Tested on fake markets with no edge: plain "positive on both" crowned a
    lucky fluke 3 times out of 5, which is why the bar is this high.

Strategy ideas tested (entry), each with several exit styles:
  edge      - buy when our BTC model says the price is too low (what the bots do now)
  favorite  - buy the side the market favors (70-90c) with time left
  late      - final 1.5-4.5 min, buy a 90-96c near-certainty if BTC agrees
  lateedge  - final minutes only, buy the side our model prices higher than the
              market and hold to settlement (your own log, 06 Nov: the model beat
              the market in the last 5 min but not before; this checks if it pays)
  follow    - buy in the direction the market just moved fast
  fade      - buy the side that just dropped fast (bet on a bounce)
Exits: hold to settlement, or take profit / stop at several distances.

Fills come from 1-second snapshots at the best price with no queue or size
limit, so real results will be worse. This tool does not trade.
"""
import json, math, os, sys
import backtest_v3 as bt

FOLDER = bt.FOLDER
CONTRACTS = 20            # size used for fees and $ figures (% return is per dollar spent)
STEP = 2                  # use every 2nd snapshot (the bots poll every 2 s)
TRAIN_FRAC = 0.60
MIN_TRADES_TRAIN = 30
MIN_TRADES_TEST = 15
MIN_T = 2.7               # strength needed on TRAIN and TEST (t-stat per window; ~1% luck chance
                          # each, after allowing for picking the best of 6 ideas)
MAX_SPREAD = 6
COOLDOWN = 10             # seconds after an exit
MAX_PER_WINDOW = 6
NO_ENTRY_SECS = 90
END_SECS = 45             # in the last 45 s: sell, unless model gives our side 85%+
KS = (1.0, 1.5, 2.0)

EXITS = {"hold": None, "tp15/sl10": (15, 10), "tp25/sl12": (25, 12),
         "tp40/sl20": (40, 20), "tp60/sl30": (60, 30)}


def fee(price, n):
    """Kalshi taker fee in cents for n contracts at price (cents), rounded up per order."""
    p = price / 100
    return math.ceil(0.07 * n * p * (1 - p) * 100 - 1e-9)


# ---------------------------- features ----------------------------
def features(win):
    """Thinned snapshots with everything a strategy needs, computed once."""
    out, mids = [], []
    for i, r in enumerate(win["rows"]):
        if i % STEP or not (r["yb"] and r["nb"]):
            continue
        yb, nb = r["yb"], r["nb"]
        mid = (yb + 100 - nb) / 2
        mids.append((r["ts"], mid))
        old = [m for t, m in mids if t <= r["ts"] - 30]
        mom = mid - old[-1] if old else 0.0           # YES mid change over ~30 s, cents
        P = {k: bt.fair_yes(r, win, win["gap"], k) for k in KS}
        out.append({"ts": r["ts"], "secs": r["secs"], "yb": yb, "nb": nb,
                    "ya": 100 - nb, "na": 100 - yb, "P": P, "mom": mom})
    return out


# ---------------------------- strategies ----------------------------
def want(kind, p, f):
    """Return (side, ask) to buy now, or None."""
    secs, P = f["secs"], f["P"][p.get("k", 1.5)]
    if 100 - f["yb"] - f["nb"] > MAX_SPREAD:
        return None
    sides = (("yes", f["ya"], P, f["mom"]), ("no", f["na"], 1 - P, -f["mom"]))
    if kind == "late":
        if not (100 <= secs <= 270):
            return None
        for side, ask, pw, _ in sides:
            if 90 <= ask <= 96 and pw >= p["need_p"]:
                return side, ask
        return None
    if kind == "lateedge":
        lo_s, hi_s = p["secs"]
        if not (lo_s <= secs <= hi_s):
            return None
        best = None
        for side, ask, pw, _ in sides:
            if 5 <= ask <= 95:
                edge = pw - (ask + fee(ask, CONTRACTS) / CONTRACTS) / 100
                if edge >= p["edge"] and (best is None or edge > best[2]):
                    best = (side, ask, edge)
        return best[:2] if best else None
    if secs < NO_ENTRY_SECS:
        return None
    best = None
    for side, ask, pw, mom in sides:          # mom > 0 = moved toward this side
        lo, hi = p.get("band", (20, 80))
        if not (lo <= ask <= hi):
            continue
        if kind == "edge":
            edge = pw - (ask + fee(ask, CONTRACTS) / CONTRACTS) / 100
            ok = edge >= p["edge"]
        elif kind == "favorite":
            ok = ask >= p["fav"] and secs >= 180 and (pw >= ask / 100 or not p["model_ok"])
        elif kind == "follow":
            ok = mom >= p["move"]
        elif kind == "fade":
            ok = mom <= -p["move"]
        else:
            ok = False
        if ok and (best is None or ask < best[1]):
            best = (side, ask)
    return best


def run(kind, p, win, feats):
    """Simulate one window. Returns list of (pnl_cents, cost_cents) at CONTRACTS size."""
    n, ex = CONTRACTS, EXITS[p["exit"]]
    trades, pos, last_exit, count = [], None, -1e9, 0
    for f in feats:
        if pos:
            side, entry = pos
            bid = f["yb"] if side == "yes" else f["nb"]
            pw = f["P"][p.get("k", 1.5)] if side == "yes" else 1 - f["P"][p.get("k", 1.5)]
            sell = False
            if ex:
                tp, sl = ex
                if bid >= min(entry + tp, 98) or bid <= entry - sl:
                    sell = True
                elif f["secs"] <= END_SECS and pw < 0.85:
                    sell = True
            if sell and bid:
                pnl = (bid - entry) * n - fee(entry, n) - fee(bid, n)
                trades.append((pnl, entry * n + fee(entry, n)))
                pos, last_exit = None, f["ts"]
            continue
        if count >= MAX_PER_WINDOW or f["ts"] - last_exit < COOLDOWN:
            continue
        w = want(kind, p, f)
        if w:
            pos = w; count += 1
    if pos:                                   # held to settlement
        side, entry = pos
        won = (win["y"] == 1) == (side == "yes")
        trades.append(((100 if won else 0) * n - entry * n - fee(entry, n), entry * n + fee(entry, n)))
    return trades


def grid():
    g = []
    for ex in EXITS:
        for k in (1.5, 2.0):
            for e in (0.04, 0.08):
                for band in ((20, 65), (25, 75), (40, 90)):
                    g.append(("edge", {"k": k, "edge": e, "band": band, "exit": ex}))
        for fav in (70, 80, 90):
            for mo in (True, False):
                g.append(("favorite", {"fav": fav, "model_ok": mo, "band": (fav, 95), "exit": ex}))
        for mv in (8, 15, 25):
            g.append(("follow", {"move": mv, "band": (30, 75), "exit": ex}))
            g.append(("fade", {"move": mv, "band": (20, 65), "exit": ex}))
    for k in (1.0, 1.5):
        for secs in ((90, 300), (30, 180), (30, 120)):
            for e in (0.05, 0.10, 0.15):
                g.append(("lateedge", {"k": k, "secs": secs, "edge": e, "exit": "hold"}))
    for need in (0.95, 0.97, 0.99):
        for ex in ("hold", "tp60/sl30"):
            g.append(("late", {"need_p": need, "k": 1.5, "exit": ex}))
    return g


# ---------------------------- scoring ----------------------------
def score(per_win):
    t = [x for w in per_win for x in w]
    if not t:
        return {"n": 0, "wins_traded": 0, "ret": 0.0, "usd": 0.0, "winrate": 0.0, "dd": 0.0, "t": 0.0}
    # strength: trades in one window share one outcome, so test per-window results
    wr = [sum(a for a, _ in w) / max(1, sum(c for _, c in w)) for w in per_win if w]
    if len(wr) > 2:
        m = sum(wr) / len(wr)
        sd = (sum((x - m) ** 2 for x in wr) / (len(wr) - 1)) ** 0.5
        tstat = m / (sd / len(wr) ** 0.5) if sd > 0 else 0.0
    else:
        tstat = 0.0
    pnl = sum(a for a, _ in t); cost = sum(c for _, c in t)
    eq = peak = dd = 0
    for a, _ in t:
        eq += a; peak = max(peak, eq); dd = min(dd, eq - peak)
    return {"n": len(t), "wins_traded": sum(1 for w in per_win if w),
            "ret": 100 * pnl / cost if cost else 0.0, "usd": pnl / 100,
            "winrate": 100 * sum(1 for a, _ in t if a > 0) / len(t), "dd": dd / 100,
            "t": tstat}


def label(kind, p):
    s = {k: v for k, v in p.items()}
    return f"{kind:<8} " + " ".join(f"{k}={v}" for k, v in s.items())


def fmt(s):
    return (f"{s['n']:>4} trades/{s['wins_traded']:>3} win  win {s['winrate']:5.1f}%  "
            f"return {s['ret']:+6.1f}%  ${s['usd']:+8.2f}  worst dip ${s['dd']:7.2f}  strength {s['t']:+5.1f}")


def main():
    print(f"Reading {FOLDER} ...")
    rows, res = bt.load()
    if not rows:
        print("No recordings found. Run record_v3.py first."); return
    bt.add_vol(rows)
    wins = bt.build_windows(rows, res)
    out = []; P = out.append
    P(f"STRATEGY LAB  |  {len(wins)} finished windows  |  {CONTRACTS} contracts per trade, fees included")
    if len(wins) < 40:
        P("Need at least 40 finished windows (10 h of recording) to learn anything. Keep recording.")
        print("\n".join(out)); _save(out); return
    if len(wins) < 192:
        P(f"NOTE: {len(wins)} windows is a small sample. 192+ (2 days) is much more trustworthy.")
    print(f"Preparing {len(wins)} windows ...")
    feats = [features(w) for w in wins]
    cut = int(len(wins) * TRAIN_FRAC)
    P(f"TRAIN = first {cut} windows, TEST = last {len(wins) - cut} windows (never used for choosing)\n")

    g = grid()
    print(f"Testing {len(g)} strategy settings ...")
    scored = []
    for i, (kind, p) in enumerate(g):
        per = [run(kind, p, w, f) for w, f in zip(wins, feats)]
        scored.append((kind, p, score(per[:cut]), score(per[cut:]), score(per)))
        if i % 25 == 0:
            print(f"  {i}/{len(g)}")

    # best settings per idea, picked on TRAIN only, then checked on TEST
    P("== Best settings of each idea (chosen on TRAIN, checked on TEST) ==")
    champs = []
    for kind in ("edge", "favorite", "late", "lateedge", "follow", "fade"):
        c = [s for s in scored if s[0] == kind and s[2]["n"] >= MIN_TRADES_TRAIN]
        if not c:
            P(f"  {kind:<8} not enough trades to judge"); continue
        best = max(c, key=lambda s: s[2]["ret"])
        champs.append(best)
        P(f"  {label(best[0], best[1])}")
        P(f"      TRAIN {fmt(best[2])}")
        P(f"      TEST  {fmt(best[3])}")

    passed = [s for s in champs if s[2]["t"] >= MIN_T and s[3]["t"] >= MIN_T
              and s[3]["n"] >= MIN_TRADES_TEST]
    P("")
    if passed:
        win_ = max(passed, key=lambda s: min(s[2]["t"], s[3]["t"]))
        P(f"WINNER: {label(win_[0], win_[1])}")
        P(f"  TRAIN {win_[2]['ret']:+.1f}% (strength {win_[2]['t']:+.1f}), "
          f"unseen TEST {win_[3]['ret']:+.1f}% (strength {win_[3]['t']:+.1f}); both above {MIN_T}")
        cfg = {"strategy": win_[0], "params": win_[1], "contracts": CONTRACTS,
               "train": win_[2], "test": win_[3], "windows": len(wins)}
        with open(os.path.join(FOLDER, "best_config.json"), "w") as fjs:
            json.dump(cfg, fjs, indent=2)
        P("  saved to best_config.json. Re-run after more recording; a real edge keeps passing.")
    else:
        P(f"NO WINNER: nothing was strong enough (strength {MIN_T}+) on both TRAIN and unseen TEST.")
        P("  That means no edge is proven yet. Keep recording and re-run; do not go live.")
        try:
            os.remove(os.path.join(FOLDER, "best_config.json"))
        except OSError:
            pass

    P("\n== All settings, whole period (for reference only, these are NOT proof) ==")
    for s in sorted(scored, key=lambda s: -s[4]["ret"]):
        if s[4]["n"]:
            P(f"  {label(s[0], s[1]):<62} {fmt(s[4])}")
    _save(out)
    print("\n".join(out[:40]))
    print(f"\nFull report: {os.path.join(FOLDER, 'lab_report.txt')}")


def _save(out):
    with open(os.path.join(FOLDER, "lab_report.txt"), "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    FOLDER = bt.parse_args(sys.argv[1:])
    if bt.SINCE_TS is not None:
        print(f"Using only data recorded since {bt.SINCE_TXT} (this PC's time)")
    main()
