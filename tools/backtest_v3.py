"""
backtest_v3.py - replays record_v3.py data and tests strategies on the SAME windows.

Run:  python backtest_v3.py
Reads: data/live  (rec_*.csv + results.csv)
Writes: backtest_report.txt in that folder

What it reports
 1. The BRTI gap: Kalshi strike vs our exchange average (measured every window)
 2. Accuracy: market price vs the model, split by time left in the window
 3. Strategies, each with a parameter sweep:
      taker     - buy the ask when model edge >= threshold, hold to settlement
      lastmin   - same, but only in the final 90 s (settlement average partly known)
      maker     - rest a bid below fair value, filled only if the market trades
                  THROUGH our price, hold to settlement
    Each line shows first-half vs second-half P&L. A real edge should be
    positive in both halves; a lucky one usually is not.

Fills are simulated from 1-second snapshots, so real results will be worse.
"""
import bisect, csv, glob, math, os, statistics, sys
from collections import defaultdict, deque

FOLDER = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "live"))   # ../data/live next to this tools folder
SINCE_TS = None          # only use data recorded at or after this time (unix seconds)
SINCE_TXT = ""

def parse_args(argv):
    """[folder] [--since "YYYY-MM-DD HH:MM"] [--last HOURS]   (times are this PC's local time)"""
    from datetime import datetime
    global FOLDER, SINCE_TS, SINCE_TXT
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--since" and args:
            v = args.pop(0).strip()
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    SINCE_TS = datetime.strptime(v, fmt).timestamp()
                    break
                except ValueError:
                    pass
            else:
                sys.exit(f'Could not read --since "{v}". Use "YYYY-MM-DD HH:MM", for example "2026-11-08 10:00".')
        elif a == "--last" and args:
            import time as _t
            SINCE_TS = _t.time() - float(args.pop(0)) * 3600
        elif not a.startswith("--"):
            FOLDER = a
    if SINCE_TS is not None:
        SINCE_TXT = datetime.fromtimestamp(SINCE_TS).strftime("%Y-%m-%d %H:%M")
    return FOLDER

parse_args(sys.argv[1:])

SETTLE_AVG_SECS = 60
TAKER_RATE = 0.07        # Kalshi taker fee multiplier
MAKER_RATE = 0.0175      # maker fee multiplier (check Kalshi's schedule; some markets are 0)
MIN_PRICE, MAX_PRICE, MAX_SPREAD = 5, 95, 6
MIN_WINDOWS_WARN = 96    # fewer than this = treat results as noise


# ---------------------------- loading ----------------------------
def num(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None

def load():
    rows = []
    for path in sorted(glob.glob(os.path.join(FOLDER, "rec_*.csv"))):
        with open(path) as f:
            for r in csv.DictReader(f):
                try:                        # skip a half-written last line
                    p = num(r.get("proxy"))
                    if p is None or not r.get("ts"):
                        continue
                    rows.append({"ts": float(r["ts"]), "tk": r.get("ticker") or "",
                                 "secs": num(r.get("secs_left")) or 0,
                                 "strike": num(r.get("strike")), "p": p,
                                 "yb": num(r.get("yes_bid")), "nb": num(r.get("no_bid"))})
                except (ValueError, TypeError):
                    continue
    if SINCE_TS is not None:
        rows = [r for r in rows if r["ts"] >= SINCE_TS]
    rows.sort(key=lambda r: r["ts"])
    res = {}
    rp = os.path.join(FOLDER, "results.csv")
    if os.path.exists(rp):
        with open(rp) as f:
            for r in csv.DictReader(f):
                if r["result"] in ("yes", "no") and (SINCE_TS is None or float(r["open_ts"]) >= SINCE_TS):
                    res[r["ticker"]] = {"y": 1 if r["result"] == "yes" else 0,
                                        "close_ts": float(r["close_ts"]), "open_ts": float(r["open_ts"]),
                                        "strike": num(r["strike"]), "open_gap": num(r["open_gap"]),
                                        "close_gap": num(r["close_gap"])}
    return rows, res


# ---------------------------- model ----------------------------
def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def add_vol(rows):
    """Realized vol per sqrt-second from points >=10 s apart over the last 15 min."""
    pts = deque()
    last_t = None
    for r in rows:
        if last_t is None or r["ts"] - last_t >= 10:
            pts.append((r["ts"], r["p"])); last_t = r["ts"]
        while pts and pts[0][0] < r["ts"] - 900:
            pts.popleft()
        s = n = 0
        for (t0, p0), (t1, p1) in zip(pts, list(pts)[1:]):
            lr = math.log(p1 / p0); s += lr * lr / (t1 - t0); n += 1
        r["sig"] = max(math.sqrt(s / n), 3e-5) if n >= 10 else None

def fair_yes(r, win, gap, k):
    """P(settlement average >= strike) at snapshot r."""
    A, secs, p = SETTLE_AVG_SECS, max(r["secs"], 0.001), r["p"] + gap
    if secs >= A:
        exp, tv = p, (secs - A) + A / 3
    else:
        i0 = bisect.bisect_left(win["ts"], win["close_ts"] - A)
        i1 = bisect.bisect_right(win["ts"], r["ts"])
        known = win["p"][i0:i1]
        avg = (sum(known) / len(known) + gap) if known else p
        exp, tv = ((A - secs) * avg + secs * p) / A, secs ** 3 / (3 * A * A)
    sd = k * r["sig"] * r["p"] * math.sqrt(max(tv, 1e-9))
    if sd <= 0:
        return 0.99 if exp >= win["strike"] else 0.01
    return min(0.995, max(0.005, phi((exp - win["strike"]) / sd)))

def fee(rate, c):
    p = c / 100
    return math.ceil(rate * p * (1 - p) * 100 - 1e-9)


# ---------------------------- windows ----------------------------
def build_windows(rows, res):
    by = defaultdict(list)
    for r in rows:
        if r["tk"] and r["sig"] is not None:
            by[r["tk"]].append(r)
    wins, gaps_seen = [], []
    for tk in sorted(by, key=lambda t: by[t][0]["ts"]):
        if tk not in res:
            continue
        rs = by[tk]
        info = res[tk]
        strike = info["strike"] or rs[0]["strike"]
        if strike is None or len(rs) < 300:
            continue
        # gap known at the open: this window's measured gap, smoothed with recent ones
        if info["open_gap"] is not None:
            gaps_seen.append(info["open_gap"])
        recent = gaps_seen[-8:]
        gap = statistics.median(recent) if recent else 0.0
        wins.append({"tk": tk, "rows": rs, "ts": [r["ts"] for r in rs], "p": [r["p"] for r in rs],
                     "strike": strike, "close_ts": info["close_ts"], "y": info["y"], "gap": gap})
    return wins


# ---------------------------- strategies ----------------------------
def asks(r):
    ya = 100 - r["nb"] if r["nb"] else None
    na = 100 - r["yb"] if r["yb"] else None
    return ya, na

def taker(win, k, min_edge, lo_secs=90, hi_secs=900, max_n=3, cooldown=30):
    trades, last = [], -1e9
    for r in win["rows"]:
        if not (lo_secs <= r["secs"] <= hi_secs) or r["ts"] - last < cooldown or len(trades) >= max_n:
            continue
        ya, na = asks(r)
        if not (ya and na and r["yb"] and r["nb"]) or ya - r["yb"] > MAX_SPREAD:
            continue
        P = fair_yes(r, win, win["gap"], k)
        for side, ask, pw in (("yes", ya, P), ("no", na, 1 - P)):
            if MIN_PRICE <= ask <= MAX_PRICE and pw - (ask + fee(TAKER_RATE, ask)) / 100 >= min_edge:
                won = (win["y"] == 1) == (side == "yes")
                trades.append(int(round((100 if won else 0) - ask - fee(TAKER_RATE, ask))))
                last = r["ts"]
                break
    return trades

def maker(win, k, margin, max_n=3, cooldown=30):
    """Rest a bid at fair - margin (cents) on whichever side is cheaper vs fair.
    Filled only if a later snapshot's ask is BELOW our price (traded through us)."""
    trades, last, order = [], -1e9, None
    for r in win["rows"]:
        ya, na = asks(r)
        if order:                                       # check fill of the resting order
            side, price = order
            ask_now = ya if side == "yes" else na
            if ask_now is not None and ask_now < price:
                won = (win["y"] == 1) == (side == "yes")
                trades.append(int(round((100 if won else 0) - price - fee(MAKER_RATE, price))))
                last, order = r["ts"], None
            else:
                order = None                            # cancel; re-quote below
        if len(trades) >= max_n or r["ts"] - last < cooldown or not (90 <= r["secs"] <= 900):
            continue
        if not (ya and na):
            continue
        P = fair_yes(r, win, win["gap"], k)
        qy, qn = int(P * 100 - margin), int((1 - P) * 100 - margin)
        cands = []
        if MIN_PRICE <= qy < ya:
            cands.append((P * 100 - qy, "yes", qy))
        if MIN_PRICE <= qn < na:
            cands.append(((1 - P) * 100 - qn, "no", qn))
        if cands:
            _, side, q = max(cands)
            order = (side, q)
    return trades


# ---------------------------- reporting ----------------------------
def summarize(name, per_win):
    allt = [t for ts in per_win for t in ts]
    if not allt:
        return None
    h = len(per_win) // 2
    a = sum(t for ts in per_win[:h] for t in ts)
    b = sum(t for ts in per_win[h:] for t in ts)
    wins = sum(1 for t in allt if t > 0)
    nwin = sum(1 for ts in per_win if ts)          # windows traded = independent bets
    if nwin < 48 or len(allt) < 50:
        tag = "(too few to judge)"
    else:
        tag = "CONSISTENT" if a > 0 and b > 0 else ""
    return (sum(allt), f"{name:<30} trades {len(allt):>4} in {nwin:>3} windows  "
            f"win {100*wins/len(allt):5.1f}%  total {sum(allt):+6d}c  per trade {sum(allt)/len(allt):+6.2f}c  "
            f"halves {a:+5d}c / {b:+5d}c {tag}")

def main():
    print(f"Reading {FOLDER} ..." + (f"  (only data since {SINCE_TXT})" if SINCE_TXT else ""))
    rows, res = load()
    if not rows:
        print(f"No data in {FOLDER}. Run record_v3.py first and let it record a few minutes."); return
    add_vol(rows)
    wins = build_windows(rows, res)
    out = []
    P = out.append
    hours = (rows[-1]["ts"] - rows[0]["ts"]) / 3600
    P(f"Data: {len(rows)} snapshots over {hours:.1f} h, {len(wins)} complete windows with results")
    if len(wins) < MIN_WINDOWS_WARN:
        P(f"WARNING: under {MIN_WINDOWS_WARN} windows (24 h). Treat every number below as noise.")
    if not wins:
        out.append(f"No finished windows with results yet ({len(res)} results saved). "
                   "Each window takes 15 min plus a minute for Kalshi to post the result.")
        print("\n".join(out)); return

    # 1. gap
    og = [r["open_gap"] for r in res.values() if r["open_gap"] is not None]
    cg = [r["close_gap"] for r in res.values() if r["close_gap"] is not None]
    P("\n== BRTI gap (Kalshi price minus our exchange median) ==")
    for name, g in (("open (strike)", og), ("close (settlement)", cg)):
        if g:
            P(f"  {name:<20} n={len(g):>3}  median {statistics.median(g):+7.2f}  "
              f"mean {statistics.mean(g):+7.2f}  stdev {statistics.pstdev(g):6.2f}")
        else:
            P(f"  {name:<20} no measurements yet")

    # 2. accuracy by time bucket
    P("\n== Accuracy (Brier score, lower is better) ==")
    buckets = [(600, 900), (300, 600), (90, 300), (0, 90)]
    for k in (1.0, 1.5, 2.0):
        line = f"  model k={k:<4}"
        for lo, hi in buckets:
            s = [(fair_yes(r, w, w["gap"], k) - w["y"]) ** 2
                 for w in wins for r in w["rows"][::10] if lo <= r["secs"] < hi]
            line += f"  {lo}-{hi}s: {sum(s)/len(s):.4f}" if s else f"  {lo}-{hi}s: n/a "
        P(line)
    line = "  market mid  "
    for lo, hi in buckets:
        s = [(((r["yb"] + 100 - r["nb"]) / 200) - w["y"]) ** 2
             for w in wins for r in w["rows"][::10] if lo <= r["secs"] < hi and r["yb"] and r["nb"]]
        line += f"  {lo}-{hi}s: {sum(s)/len(s):.4f}" if s else f"  {lo}-{hi}s: n/a "
    P(line)

    # 3. strategies
    P("\n== Strategies (1 contract per trade, fees included) ==")
    results = []
    for k in (1.0, 1.5, 2.0):
        for e in (0.04, 0.06, 0.08, 0.12):
            results.append(summarize(f"taker   k={k} edge>={e}", [taker(w, k, e) for w in wins]))
            results.append(summarize(f"lastmin k={k} edge>={e}",
                                     [taker(w, k, e, lo_secs=5, hi_secs=90, max_n=2, cooldown=10) for w in wins]))
        for m in (2, 4, 6, 10):
            results.append(summarize(f"maker   k={k} margin={m}c", [maker(w, k, m) for w in wins]))
    results = sorted([r for r in results if r], key=lambda x: -x[0])
    for _, line in results:
        P("  " + line)
    if not results:
        P("  no trades triggered")
    P("\nTrades in the same window share one outcome, so 'windows' is the real sample size.")
    P("CONSISTENT needs 48+ windows traded, 50+ trades, and profit in both halves.")
    P("A strategy is worth a live dry run only if it is CONSISTENT, has 50+ trades,")
    P("and stays positive when you re-run this after another full day of recording.")

    text = "\n".join(out)
    print(text)
    with open(os.path.join(FOLDER, "backtest_report.txt"), "w") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
