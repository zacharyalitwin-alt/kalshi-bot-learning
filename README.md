# BTC-BOT
15 Min BTC Window Recorder, Trainer, and Trader
 Kalshi Bot Learning

A research project that asks one question: **can a bot beat Kalshi's 15-minute Bitcoin market?**

I built a market recorder, a pricing model that learns, eight trading bot designs, a backtester, a strategy lab and a lag study, then tested them on several days of recorded data and a small live run. So far the answer is **no**: the market's own price predicts the outcome better than my model, no strategy holds up on data it wasn't tuned on, and Kalshi reprices within the same second Bitcoin moves.

This repo holds the research tools. They record and analyze only. **Nothing here places trades or needs an API key.**

---

## The market

Kalshi's `KXBTC15M` contracts ask: *will Bitcoin be higher at the end of this 15-minute window than at the start?*

- A YES contract pays $1 if it is, $0 if not. Its price (1¢–99¢) is the market's probability.
- The **strike** is the 60-second average of the CF Benchmarks Bitcoin index (BRTI) before the window opens.
- The **settle** is the 60-second average of the same index over the window's final minute.
- Taker fee per order: `ceil(0.07 × contracts × P × (1 − P))` dollars, with `P` the price in dollars.

## Findings

| Test | Data | Result |
|---|---|---|
| Model vs market (Brier score, lower is better) | 14,483 samples, ~200 windows | Market **0.1789**, model 0.1883. The market won in both halves of the data. |
| Trade when model and market disagree | same | The bigger the disagreement, the bigger the loss: −1.2¢ per contract at 5¢ apart, **−9.0¢** at 20¢ apart. |
| Strategy lab: 5 ideas, 144 settings | 166 windows | **No winner.** Every idea's best training setting lost on the unseen test set (e.g. late-window edge: +27.3% → −13.0%). |
| Lag study: does Kalshi react late to BTC jumps? | 435 jumps of $20+ in 2 s | **No usable lag.** 83% of Kalshi's move happens in the same second. Trading 1 s later: best training setting +0.46¢ → −1.12¢ on test. |
| Maker bot, 7 h dry run | 52 trades, 27 windows | −$30.95. Take-profit sales made +$21.54; positions held to settlement lost −$45.82. |

The one-line takeaway: **this market is fast and priced about right.** Beating it needs information or speed the market doesn't already have.

## Repository layout

```
Kalshi Bot Learning/
├── README.md
├── requirements.txt
├── tools/
│   ├── record_v3.py      market recorder (run continuously)
│   ├── backtest_v3.py    replays recorded windows; shared data loader
│   ├── strategy_lab.py   tunes strategies on TRAIN, checks them on unseen TEST
│   └── lag_study.py      measures how fast Kalshi follows BTC moves
└── data/
    ├── live/             recorder output + reports (not committed, see below)
    ├── logs/             old bot logs
    └── learned/          merged model samples
```

## Setup

Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

Each tool has its data folder set near the top (`FOLDER = ...`). Change it to your own path, or pass a folder on the command line:

```bash
python strategy_lab.py path/to/data/live
```

## Usage

Run everything from the `tools` folder.

**1. Record the market** (leave it running; one row per second, about 1 MB a day):

```bash
python record_v3.py
```

**2. Analyze** (any time, in a second terminal):

```bash
python backtest_v3.py
python strategy_lab.py
python lag_study.py
```

All three accept the same filters:

```bash
python strategy_lab.py --since "2026-11-08 10:00"   # only data recorded after this local time
python strategy_lab.py --last 24                    # only the last 24 hours
```

Reports are written to `data/live/` as `backtest_report.txt`, `lab_report.txt` and `lag_report.txt`.

How much data you need:

| Recording | What it's good for |
|---|---|
| < 10 hours | Nothing yet. The lab needs 40 finished windows. |
| 1 day (~96 windows) | A rough first look. |
| 3 days | Enough to tune on part and test on the rest. |
| 7 days | A result worth trusting either way. |

## Data files

**`rec_YYYYMMDD.csv`** – one row about every second (UTC day per file):

| Column | Meaning |
|---|---|
| `ts`, `time_utc` | Unix time, and clock time in UTC |
| `ticker` | Current Kalshi window, e.g. `KXBTC15M-26NOV081030-30` |
| `secs_left`, `strike` | Seconds until close, and the window's strike |
| `proxy`, `n_src` | Median BTC price across exchanges, and how many answered |
| `coinbase`, `kraken`, `bitstamp`, `gemini` | Each exchange's mid price |
| `yes_bid`, `yes_bid_sz`, `no_bid`, `no_bid_sz` | Top of Kalshi's book (the YES ask is `100 − no_bid`) |
| `loop_ms` | How long that row took to collect |

**`results.csv`** – one row per finished window: strike, Kalshi's official settle value, our exchange averages at open and close, the gap between them, and `yes`/`no`.

## Method

- **Pricing model.** `P(yes) = Φ((d + b) / (k · σ · √t))`, where `d` is BTC's distance from the strike, `σ` is recent per-second volatility, `t` is time left, `b` is the measured offset between our exchange median and Kalshi's index, and `k` is fitted from past windows.
- **Benchmark.** Every model and strategy is compared with the market's own price, not with a coin flip.
- **Out-of-sample testing.** The strategy lab and lag study choose settings on the first part of the data (TRAIN) and report them on later data they never saw (TEST). A strategy only counts if it is strong on both (strength 2.7+ in the lab).
- **Costs included.** Every simulated trade pays Kalshi's fee and crosses the spread.
- **Known limit.** The recorder samples about once a second, so effects faster than one second are invisible.

## Lessons

1. **Measure before assuming.** An early price offset of +$10, read off 2 windows, turned out to be −$0.50 ± $4.60 over many.
2. **Use the market as the benchmark.** Beating the price already on the screen is the real test.
3. **Keep tuning and testing data apart.** Several settings looked strong on training data and failed the next day.
4. **Averaging down hides losses.** One live position grew from 10 to 30 contracts on the way down and lost $14.65 in a single window.
5. **Build the safety rails first.** Dry-run by default, loss limits and a simulated exchange caught bugs before they cost money.

## What's not in this repo

- **API keys.** None of these tools need one. Never commit a `.pem` key file; the `.gitignore` blocks them.
- **The trading bots.** The eight bot designs (taker, maker, late-window and others) are retired and kept out of this repo.
- **Raw recordings.** `data/live/` grows every day and stays local.

## Status

Recording continues. Possible next studies: whether the market prices the final minute correctly (half of the settle average is known at 30 s left), and a map of conditions where the market might be wrong.

## Disclaimer

Research and education only. Not investment advice. Prediction markets carry real risk of loss; this project found no edge.
