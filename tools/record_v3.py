"""
record_v3.py - Recorder for Kalshi KXBTC15M (15-minute BTC up/down).

Every second it saves:
  - bid/ask mid from Coinbase, Kraken, Bitstamp, Gemini (and their median)
  - the open KXBTC15M market: ticker, strike, seconds left
  - Kalshi's live orderbook top: best YES bid + size, best NO bid + size
After each window it saves the result and measures the BRTI gap twice:
  - open gap  = Kalshi strike           - our 60 s average before the open
  - close gap = Kalshi settlement value - our 60 s average before the close

Places NO orders. Needs NO API key. Safe to run next to anything else.
Run:  python record_v3.py      Stop: Ctrl+C (files are saved every second)
Then: python backtest_v3.py
"""
import csv, os, statistics, time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import requests

# ============================ SETTINGS ============================
FOLDER = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "live"))   # ../data/live next to this tools folder
SERIES = "KXBTC15M"
TICK = 1.0                      # seconds between snapshots
STATUS_EVERY = 10               # print a status line every N seconds
# ==================================================================

BASE = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "btc-recorder-v3"}

def _kraken(j):
    r = next(iter(j["result"].values()))
    return float(r["b"][0]), float(r["a"][0])

EXCH = {
    "coinbase": ("https://api.exchange.coinbase.com/products/BTC-USD/ticker",
                 lambda j: (float(j["bid"]), float(j["ask"]))),
    "kraken":   ("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", _kraken),
    "bitstamp": ("https://www.bitstamp.net/api/v2/ticker/btcusd/",
                 lambda j: (float(j["bid"]), float(j["ask"]))),
    "gemini":   ("https://api.gemini.com/v1/pubticker/btcusd",
                 lambda j: (float(j["bid"]), float(j["ask"]))),
}
SESS = {n: requests.Session() for n in list(EXCH) + ["kmkt", "kbook", "kres"]}
for s in SESS.values():
    s.headers.update(UA)
POOL = ThreadPoolExecutor(max_workers=6)

REC_COLS = ["ts", "time_utc", "ticker", "secs_left", "strike", "proxy", "n_src",
            "coinbase", "kraken", "bitstamp", "gemini",
            "yes_bid", "yes_bid_sz", "no_bid", "no_bid_sz", "loop_ms"]
RES_COLS = ["ticker", "open_ts", "close_ts", "strike", "our_open_avg", "open_gap",
            "our_close_avg", "settle_value", "close_gap", "result"]


# ---------------------------- fetchers ----------------------------
def fetch_mid(name):
    url, parse = EXCH[name]
    try:
        bid, ask = parse(SESS[name].get(url, timeout=3).json())
        if bid > 0 and ask >= bid:
            return (bid + ask) / 2
    except Exception:
        pass
    return None

def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def fetch_market():
    """Soonest-closing open market: dict(ticker, strike, open_ts, close_ts)."""
    r = SESS["kmkt"].get(f"{BASE}/markets", params={"series_ticker": SERIES,
                         "status": "open", "limit": 5}, timeout=5)
    now, best = time.time(), None
    for m in r.json().get("markets", []):
        ct = _ts(m["close_time"])
        if ct > now and (best is None or ct < best["close_ts"]):
            sv = m.get("floor_strike")
            best = {"ticker": m["ticker"], "close_ts": ct,
                    "open_ts": _ts(m["open_time"]) if m.get("open_time") else ct - 900,
                    "strike": float(sv) if sv not in (None, "") else None}
    return best

def _best(ob, side):
    """Best bid (cents) and size for one side of a Kalshi book."""
    for key, scale in ((side + "_dollars", 100), (side, 1)):
        lv = []
        for x in ob.get(key) or []:
            try:
                p, q = round(float(x[0]) * scale), float(x[1])
            except (TypeError, ValueError, IndexError):
                continue
            if q > 0:
                lv.append((p, q))
        if lv:
            return max(lv)
    return None, None

def fetch_book(ticker):
    try:
        j = SESS["kbook"].get(f"{BASE}/markets/{ticker}/orderbook", timeout=3).json()
        ob = j.get("orderbook_fp") or j.get("orderbook") or {}
        yb, ys = _best(ob, "yes")
        nb, ns = _best(ob, "no")
        return yb, ys, nb, ns
    except Exception:
        return None, None, None, None

def fetch_result(ticker):
    m = SESS["kres"].get(f"{BASE}/markets/{ticker}", timeout=5).json().get("market", {})
    res = m.get("result")
    sv = m.get("expiration_value")
    try:
        sv = float(str(sv).replace(",", "")) if sv not in (None, "") else None
    except ValueError:
        sv = None
    return (res if res in ("yes", "no") else None), sv


# ---------------------------- helpers ----------------------------
def avg_between(hist, t0, t1):
    vals = [p for t, p in hist if t0 <= t < t1]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

def append_row(path, cols, row):
    # OneDrive can lock a file for a moment while it syncs: retry for ~5 s
    for attempt in range(10):
        try:
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(cols)
                w.writerow(row)
            return
        except PermissionError:
            time.sleep(0.5)
    print(f"  could not write {os.path.basename(path)} (file locked) - row skipped")

def fmt(x, nd=2):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


# ---------------------------- main ----------------------------
def main():
    os.makedirs(FOLDER, exist_ok=True)
    res_path = os.path.join(FOLDER, "results.csv")
    done = set()
    if os.path.exists(res_path):
        with open(res_path) as f:
            done = {r["ticker"] for r in csv.DictReader(f)}

    print(f"Recording to {FOLDER}  (no orders, no API key)  Ctrl+C to stop")
    hist = deque()                 # (ts, proxy) last 20 min
    windows = {}                   # ticker -> info
    pending = []                   # tickers waiting for a result
    mkt, mkt_checked, last_status, last_res_check = None, 0, 0, 0

    while True:
        t0 = time.time()
        try:
            # refresh market every 10 s, or right after a window closes
            if mkt is None or t0 - mkt_checked > 10 or t0 >= mkt["close_ts"]:
                try:
                    m = fetch_market()
                    mkt_checked = t0
                    if m:
                        mkt = m
                        if m["ticker"] not in windows:
                            windows[m["ticker"]] = dict(m)
                        elif windows[m["ticker"]].get("strike") is None:
                            windows[m["ticker"]]["strike"] = m["strike"]
                except Exception as e:
                    print("market fetch error:", str(e)[:80])

            ticker = mkt["ticker"] if mkt and t0 < mkt["close_ts"] else ""
            futs = [POOL.submit(fetch_mid, n) for n in EXCH]
            fb = POOL.submit(fetch_book, ticker) if ticker else None
            mids = [f.result() for f in futs]
            yb, ys, nb, ns = fb.result() if fb else (None, None, None, None)
            vals = [v for v in mids if v]
            proxy = statistics.median(vals) if vals else None
            now = time.time()
            if proxy:
                hist.append((now, proxy))
            while hist and hist[0][0] < now - 1200:
                hist.popleft()

            secs = round(mkt["close_ts"] - now, 1) if ticker else 0
            strike = mkt["strike"] if ticker else None
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            append_row(os.path.join(FOLDER, f"rec_{day}.csv"), REC_COLS, [
                round(now, 3), datetime.now(timezone.utc).strftime("%H:%M:%S"), ticker,
                secs, fmt(strike), fmt(proxy), len(vals), *[fmt(v) for v in mids],
                fmt(yb), fmt(ys, 0), fmt(nb), fmt(ns, 0), int((time.time() - t0) * 1000)])

            # windows that have closed -> compute our averages, queue for result
            for tk, w in list(windows.items()):
                if now >= w["close_ts"] + 2 and not w.get("closed"):
                    w["closed"] = True
                    w["our_open_avg"], n_open = avg_between(hist, w["open_ts"] - 60, w["open_ts"])
                    w["our_close_avg"], n_close = avg_between(hist, w["close_ts"] - 60, w["close_ts"])
                    if n_open < 30:
                        w["our_open_avg"] = None      # we weren't running before it opened
                    if n_close < 30:
                        w["our_close_avg"] = None
                    if tk not in done:
                        pending.append(tk)

            # look up results (every 15 s)
            if pending and now - last_res_check > 15:
                last_res_check = now
                for tk in pending[:]:
                    w = windows[tk]
                    try:
                        result, sv = fetch_result(tk)
                    except Exception:
                        continue
                    if result is None and now - w["close_ts"] < 1800:
                        continue
                    og = (w["strike"] - w["our_open_avg"]) if (w["strike"] and w["our_open_avg"]) else None
                    cg = (sv - w["our_close_avg"]) if (sv and w["our_close_avg"]) else None
                    append_row(res_path, RES_COLS, [tk, round(w["open_ts"]), round(w["close_ts"]),
                               fmt(w["strike"]), fmt(w["our_open_avg"]), fmt(og),
                               fmt(w["our_close_avg"]), fmt(sv), fmt(cg), result or "unknown"])
                    done.add(tk)
                    pending.remove(tk)
                    windows.pop(tk, None)
                    print(f"RESULT {tk} -> {result}  strike {fmt(w['strike'])}  settle {fmt(sv)}  "
                          f"open gap {fmt(og)}  close gap {fmt(cg)}")

            if now - last_status >= STATUS_EVERY:
                last_status = now
                ya = 100 - nb if nb else None
                print(f"{time.strftime('%H:%M:%S')} {ticker or '-'} left={int(secs)}s "
                      f"strike={fmt(strike)} btc={fmt(proxy)}({len(vals)}) "
                      f"yes bid/ask={fmt(yb, 0)}/{fmt(ya, 0)} loop={int((time.time()-t0)*1000)}ms")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print("error:", str(e)[:120])
        time.sleep(max(0.0, TICK - (time.time() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Data is saved.")
