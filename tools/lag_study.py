"""
lag_study.py - does Kalshi's price react late when Bitcoin moves?

Reads the recorder files (rec_*.csv + results.csv) in data\\live and answers:
  1. When BTC jumps on the exchanges, how fast does Kalshi's price follow?
     (how much of its move is done 0, 1, 2, 5, 10, 30 seconds later)
  2. Could you profit from that? Buy at Kalshi's ask one second AFTER the jump
     (you can't trade the same instant you see it), then sell at the bid
     5/10/30/60 s later or hold to settlement. Kalshi taker fees included.
     Settings are chosen on the first 2/3 of the data and checked on the last 1/3.

Research only. Places no orders. No key needed.

    python lag_study.py
    python lag_study.py --since "2026-11-08 10:00"
    python lag_study.py --last 48

Limit: the recorder writes about one row per second, so lags shorter than
about one second are invisible here.
"""
import bisect, csv, glob, math, os, statistics, sys

import backtest_v3 as bt          # same folder; shares FOLDER and --since / --last

CONTRACTS   = 10                  # fee rounding is per order, so size matters a little
LATENCY     = 1.0                 # seconds between seeing the jump and the order filling
MOVE_SECS   = (2, 5)              # a jump = BTC change over this many seconds ...
MOVE_USD    = (10, 20, 35, 50)    # ... of at least this many dollars
HOLDS       = (5, 10, 30, 60, "settle")
MID_BAND    = (15, 85)            # only when the price is not near 0 or 100
SECS_BAND   = (90, 870)           # skip the first few seconds and the last 90 s
COOLDOWN    = 15                  # seconds between counted jumps
LAGS        = (-5, -2, 0, 1, 2, 3, 5, 10, 20, 30, 60)
MIN_TRADES  = 30
STRONG_T    = 2.0                 # how sure the test result must be (t-score)


def fee_cents(price_c):
    """Kalshi taker fee per contract in cents, rounded up per order of CONTRACTS."""
    p = price_c / 100
    return math.ceil(0.07 * CONTRACTS * p * (1 - p) * 100 - 1e-9) / CONTRACTS


def num(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None


def load():
    rows = []
    for path in sorted(glob.glob(os.path.join(bt.FOLDER, "rec_*.csv"))):
        with open(path, encoding="utf-8", errors="ignore", newline="") as f:
            text = f.read().replace("\x00", "")     # a crash can leave blank bytes
        for r in csv.DictReader(text.splitlines()):
            try:
                ts, p = num(r.get("ts")), num(r.get("proxy"))
                yb, nb = num(r.get("yes_bid")), num(r.get("no_bid"))
                if ts is None or p is None or not r.get("ticker"):
                    continue
                if bt.SINCE_TS is not None and ts < bt.SINCE_TS:
                    continue
                rows.append({"ts": ts, "tk": r["ticker"], "secs": num(r.get("secs_left")) or 0,
                             "p": p, "yb": yb, "nb": nb})
            except (ValueError, TypeError, KeyError):
                continue
    rows.sort(key=lambda r: r["ts"])
    res = {}
    rp = os.path.join(bt.FOLDER, "results.csv")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                if r.get("result") in ("yes", "no"):
                    res[r["ticker"]] = 1 if r["result"] == "yes" else 0
    by = {}
    for r in rows:
        by.setdefault(r["tk"], []).append(r)
    return by, res


def book(r):
    """(yes_bid, yes_ask, mid) in cents, or None if the book is missing or crossed."""
    if r["yb"] is None or r["nb"] is None:
        return None
    yb, ya = r["yb"], 100 - r["nb"]
    if ya < yb or yb <= 0 or ya >= 100:
        return None
    return yb, ya, (yb + ya) / 2


def at(rows, times, t):
    """Row at or just after time t (within 2 s), else None."""
    i = bisect.bisect_left(times, t)
    if i < len(rows) and rows[i]["ts"] - t <= 2:
        return rows[i]
    return None


def at_or_before(rows, times, t):
    i = bisect.bisect_right(times, t) - 1
    if i >= 0 and t - rows[i]["ts"] <= 2:
        return rows[i]
    return None


def find_jumps(rows, times, span, usd):
    out, last = [], -1e9
    for i, r in enumerate(rows):
        if r["ts"] - last < COOLDOWN:
            continue
        if not (SECS_BAND[0] <= r["secs"] <= SECS_BAND[1]):
            continue
        b = book(r)
        if not b or not (MID_BAND[0] <= b[2] <= MID_BAND[1]):
            continue
        prev = at_or_before(rows, times, r["ts"] - span)
        if prev is None or r["ts"] - prev["ts"] < span - 1:
            continue
        move = r["p"] - prev["p"]
        if abs(move) >= usd:
            out.append((i, 1 if move > 0 else -1, move))
            last = r["ts"]
    return out


def response_curve(by):
    """Average Kalshi mid move and BTC move (in the jump's direction) around $20+/2 s jumps."""
    k_moves = {L: [] for L in LAGS}
    b_moves = {L: [] for L in LAGS}
    spreads, n = [], 0
    for tk, rows in by.items():
        times = [r["ts"] for r in rows]
        for i, d, mv in find_jumps(rows, times, 2, 20):
            r0 = rows[i]
            prev = at_or_before(rows, times, r0["ts"] - 2)
            b_prev = book(prev) if prev else None
            if not b_prev:
                continue
            n += 1
            spreads.append(book(r0)[1] - book(r0)[0])
            for L in LAGS:
                rL = at(rows, times, r0["ts"] + L) if L >= 0 else at_or_before(rows, times, r0["ts"] + L)
                bL = book(rL) if rL else None
                if bL:
                    k_moves[L].append(d * (bL[2] - b_prev[2]))
                    b_moves[L].append(d * (rL["p"] - prev["p"]))
    return n, k_moves, b_moves, spreads


def trades(by, res, span, usd, hold):
    out = []
    for tk, rows in by.items():
        times = [r["ts"] for r in rows]
        for i, d, mv in find_jumps(rows, times, span, usd):
            r0 = rows[i]
            entry_row = at(rows, times, r0["ts"] + LATENCY)
            be = book(entry_row) if entry_row else None
            if not be:
                continue
            # buy YES at the yes ask (up move) or NO at the no ask (= 100 - yes bid)
            entry = be[1] if d > 0 else 100 - be[0]
            if not (2 <= entry <= 98):
                continue
            if hold == "settle":
                if tk not in res:
                    continue
                won = res[tk] == (1 if d > 0 else 0)
                pnl = (100 if won else 0) - entry - fee_cents(entry)
            else:
                xr = at(rows, times, entry_row["ts"] + hold)
                bx = book(xr) if xr else None
                if not bx:
                    continue
                exit_ = bx[0] if d > 0 else 100 - bx[1]      # sell at the bid
                pnl = exit_ - entry - fee_cents(entry) - fee_cents(exit_)
            out.append((r0["ts"], pnl))
    return out


def summary(tr):
    if not tr:
        return 0, 0.0, 0.0, 0.0
    p = [x for _, x in tr]
    n = len(p)
    mean = sum(p) / n
    sd = statistics.pstdev(p) if n > 1 else 0
    t = mean / sd * math.sqrt(n) if sd > 0 else 0.0
    return n, mean, 100 * sum(1 for x in p if x > 0) / n, t


def main():
    by, res = load()
    out = []
    P = out.append
    nwin = sum(1 for tk in by if tk in res)
    nrows = sum(len(v) for v in by.values())
    P(f"LAG STUDY  |  {nrows} snapshots, {len(by)} windows ({nwin} with results)"
      + (f"  |  since {bt.SINCE_TXT}" if bt.SINCE_TXT else ""))
    if nrows < 3600:
        P("Need at least an hour of recording. Keep the recorder running.")
        return finish(out)

    # ---- 1. how fast does Kalshi follow? ----
    n, km, bm, spreads = response_curve(by)
    P(f"\n== 1. How fast Kalshi follows a BTC jump of $20+ in 2 s ({n} jumps) ==")
    if n < 20:
        P("  Not enough jumps yet. Keep recording.")
    else:
        ref = statistics.mean(km[30]) if km[30] else 0
        P("  seconds after   Kalshi moved (c)   share of its 30 s move   BTC moved ($)")
        for L in LAGS:
            if not km[L]:
                continue
            k = statistics.mean(km[L]); b = statistics.mean(bm[L])
            share = f"{100 * k / ref:5.0f}%" if ref else "   n/a"
            P(f"  {L:+4d} s          {k:+8.2f}             {share}                {b:+8.2f}")
        P(f"  Typical spread at the jump: {statistics.median(spreads):.0f}c (yes ask - yes bid)")
        k0 = statistics.mean(km[0]) if km[0] else 0
        k2 = statistics.mean(km[2]) if km[2] else 0
        if ref > 0:
            if k0 / ref >= 0.8:
                P("  READ: Kalshi had already moved most of the way in the same second. No usable lag.")
            elif k2 / ref >= 0.8:
                P("  READ: Kalshi catches up within about 2 s. A lag exists but is very short.")
            else:
                P("  READ: Kalshi is still catching up after 2 s. That is the lag we are looking for.")

    # ---- 2. could you trade it? ----
    allts = sorted(r["ts"] for v in by.values() for r in v)
    split = allts[int(len(allts) * 2 / 3)] if allts else 0
    P(f"\n== 2. Trading the jump: buy at the ask {LATENCY:.0f} s later, {CONTRACTS} contracts, taker fees ==")
    P("  TRAIN = first 2/3 of the data (used for choosing), TEST = last 1/3 (never used for choosing)")
    grid = []
    for span in MOVE_SECS:
        for usd in MOVE_USD:
            for hold in HOLDS:
                tr = trades(by, res, span, usd, hold)
                trn = [x for x in tr if x[0] < split]
                tst = [x for x in tr if x[0] >= split]
                grid.append(((span, usd, hold), summary(trn), summary(tst), summary(tr)))
    P("\n  setting                         TRAIN: n  avg c/contract  win%   t   |  TEST: n  avg   win%   t")
    for (span, usd, hold), a, b, _ in sorted(grid, key=lambda g: -g[1][1])[:12]:
        h = "settle" if hold == "settle" else f"{hold}s"
        P(f"  ${usd:>3} in {span}s, exit {h:<6}       {a[0]:4d}  {a[1]:+7.2f}   {a[2]:5.1f}% {a[3]:+5.1f}  |  "
          f"{b[0]:4d} {b[1]:+6.2f} {b[2]:5.1f}% {b[3]:+5.1f}")
    ok = [g for g in grid if g[1][0] >= MIN_TRADES and g[1][1] > 0]
    best = max(ok, key=lambda g: g[1][3]) if ok else None
    P("")
    if best and best[2][0] >= MIN_TRADES and best[2][1] > 0 and best[2][3] >= STRONG_T:
        (span, usd, hold), a, b, _ = best
        P(f"WORTH A CLOSER LOOK: ${usd} in {span}s, exit {hold}: TEST avg {b[1]:+.2f}c/contract "
          f"over {b[0]} trades (t {b[3]:+.1f}).")
        P("  This is a lead, not proof. Re-run after more days and send the report to Claude.")
    else:
        if best:
            (span, usd, hold), a, b, _ = best
            P(f"NO TRADEABLE LAG: the best TRAIN setting (${usd} in {span}s, exit {hold}, "
              f"{a[1]:+.2f}c) did {b[1]:+.2f}c on TEST over {b[0]} trades.")
        else:
            P("NO TRADEABLE LAG: no setting made money on TRAIN with enough trades.")
        P("  After the spread and fees, reacting 1 s late does not pay.")
    finish(out)


def finish(out):
    print("\n".join(out))
    path = os.path.join(bt.FOLDER, "lag_report.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        print(f"\nReport saved: {path}")
    except OSError as e:
        print(f"\n(could not save report: {e})")


if __name__ == "__main__":
    bt.parse_args(sys.argv[1:])
    main()
