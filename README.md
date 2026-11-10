# Kalshi Bot Learning

*15-minute BTC window recorder and research lab*

**Can a bot beat Kalshi's 15-minute Bitcoin market?**

This project set out to answer that with data instead of hope. I built a market recorder, a pricing model that learns, eight trading bot designs, a backtester, a strategy lab and a lag study. Then I tested them on 82 hours of second-by-second recordings (216 market windows), 16,000+ model samples and a small live run.

**The answer so far is no.** The market prices these contracts about right, reacts to Bitcoin within the same second, and every strategy that looked good on past data failed on data it hadn't seen.

This repo holds the research tools. They record and analyze only. **Nothing here places trades or needs an API key.**

> **Next project:** [kalshi-market-scanner](https://github.com/zacharyalitwin-alt/kalshi-market-scanner) asks whether *other* Kalshi markets (weather, oil, sports, economics) are priced as well as this one.

---

## Contents

- [The market](#the-market)
- [Results](#results)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Tools](#tools)
- [Data files](#data-files)
- [Lessons](#lessons)
- [Limits and next steps](#limits-and-next-steps)

---

## The market

Kalshi's `KXBTC15M` contracts ask one question every 15 minutes: *will Bitcoin be higher at the end of this window than at the start?*

| | |
|---|---|
| **Payout** | A YES contract pays $1 if Bitcoin finishes higher, $0 if not. NO is the opposite. |
| **Price** | 1¢ to 99¢. The price is the market's probability. |
| **Strike** | The 60-second average of the CF Benchmarks Bitcoin index (BRTI) in the minute before the window opens. |
| **Settle** | The 60-second average of the same index over the window's final minute. |
| **Taker fee** | `ceil(0.07 × contracts × P × (1 − P))` dollars per order, where `P` is the price in dollars. |

Because both ends are one-minute averages, the first and last minute of each window decide everything.

---

## Results

| Test | Data | What it found |
|---|---|---|
| **Model vs market** | 14,483 samples, ~200 windows | The market's price predicted outcomes better than my model (Brier score 0.1789 vs 0.1883), in both halves of the data. |
| **Trading on disagreement** | same | The more the model disagreed with the market, the more it lost: −1.2¢ per contract at a 5¢ gap, **−9.0¢** at a 20¢ gap. |
| **Strategy lab** | 241 windows, 6 ideas, 144 settings | **No winner.** No setting was strong on both the training data and the unseen test data. One lead to watch: "follow the move" ($25 move, hold) has been positive over the whole period in three runs in a row, but it hasn't passed the bar. |
| **New lab ideas** | 161 windows (Nov 6–8) | Order-book imbalance, a leading exchange, and buying in the final minute using the partly known settlement average: **no edge** on the data so far. Full-data run pending. |
| **Backtest** | 216 windows, 36 taker/maker/last-minute rules | No rule was profitable in both halves of the data. Every resting-bid (maker) rule lost 1–8¢ per trade. |
| **Lag study** | 435 Bitcoin jumps of $20+ in 2 s | **No usable lag.** 83% of Kalshi's price move happens in the same second as the jump. Trading 1 second later lost money after fees. |
| **Maker bot dry run** | 52 trades over 7 hours | −$30.95. Quick take-profit sales made +$21.54; positions held to settlement lost −$45.82. |

### The one interesting result

Late in a window, the model is a **better estimate** than the market's mid-price:

| Time left | Model error | Market mid error |
|---|---|---|
| 10–15 min | 0.2133 | **0.2111** |
| 5–10 min | 0.1641 | **0.1599** |
| 1.5–5 min | **0.0899** | 0.0968 |
| Last 90 s | **0.0267** | 0.0555 |

*(Brier score, lower is better.)*

But every strategy that traded on it lost 6–12¢ per trade. Near the close the order book thins out and the spread widens, so the mid-price looks wrong while the actual ask is already close to right. You can only trade at the ask, plus a fee, and that gap is larger than the model's advantage.

### Bottom line

This market is fast and priced about right. Beating it would take information or speed the market doesn't already have, and none of these tests found either.

---

## How it works

```
 Coinbase ┐
 Kraken   ├─► record_v3.py ──► rec_YYYYMMDD.csv  (one row per second)
 Bitstamp │        │
 Gemini   ┘        └─────────► results.csv       (one row per window)
 Kalshi book ──────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          ▼                          ▼                          ▼
   backtest_v3.py             strategy_lab.py             lag_study.py
   model accuracy,            tunes 164 settings on      how fast Kalshi
   rule-by-rule P&L           TRAIN, checks on TEST       follows BTC jumps
```

**Pricing model.** The chance of YES is

`P(yes) = Φ( (d + b) / (k · σ · √t) )`

where `d` is Bitcoin's distance from the strike, `σ` is recent per-second volatility, `t` is seconds left, `b` is the measured offset between our exchange median and Kalshi's index, and `k` is fitted from past windows.

**Testing rules.** Every result in this repo follows three rules:

1. **The benchmark is the market's own price**, not a coin flip.
2. **Settings are chosen on one part of the data and judged on a later part** they never saw. The strategy lab requires a strength score of 2.7+ on both.
3. **Every simulated trade pays Kalshi's fee and crosses the spread.**

---

## Quick start

Python 3.10 or newer.

```bash
git clone https://github.com/zacharyalitwin-alt/kalshi-bot-learning.git
cd kalshi-bot-learning
pip install -r requirements.txt
cd tools
python record_v3.py
```

Leave the recorder running. It writes to `data/live/`, about 1 MB a day. After 10+ hours, open a second terminal in `tools/` and run the analysis:

```bash
python backtest_v3.py
python strategy_lab.py
python lag_study.py
```

Reports are saved to `data/live/` as `backtest_report.txt`, `lab_report.txt` and `lag_report.txt`.

### How much data you need

| Recording | What it's good for |
|---|---|
| Under 10 hours | Nothing yet. The lab needs 40 finished windows. |
| 1 day (~96 windows) | A rough first look. |
| 3 days | Enough to tune on part and test on the rest. |
| 7 days | A result worth trusting either way. |

---

## Tools

| Tool | What it does | Output |
|---|---|---|
| `record_v3.py` | Reads 4 exchanges and Kalshi's order book every second. Records each window's strike and settle. | `rec_YYYYMMDD.csv`, `results.csv` |
| `backtest_v3.py` | Measures model vs market accuracy by time left, the index offset, and the P&L of taker, maker and last-minute rules. Also the shared data loader. | `backtest_report.txt` |
| `strategy_lab.py` | Tests 8 strategy ideas across 164 settings: edge, favorite, late, late edge (including the final minute), follow the move, fade the move, order-book imbalance and leading exchange. Picks the best on TRAIN and reports it on TEST. | `lab_report.txt`, `best_config.json` |
| `lag_study.py` | Finds Bitcoin jumps and measures how fast Kalshi's price follows. Then simulates trading the jump 1 second late. | `lag_report.txt` |

All three analysis tools take the same options:

```bash
python strategy_lab.py --since "2026-11-08 10:00"   # only data after this local time
python strategy_lab.py --last 24                    # only the last 24 hours
python strategy_lab.py path/to/other/data           # read a different folder
```

---

## Data files

### `rec_YYYYMMDD.csv`

One row about every second, one file per UTC day.

| Column | Meaning |
|---|---|
| `ts`, `time_utc` | Unix time, and clock time in UTC |
| `ticker` | Current Kalshi window, e.g. `KXBTC15M-26NOV081030-30` |
| `secs_left`, `strike` | Seconds until close, and the window's strike |
| `proxy`, `n_src` | Median Bitcoin price across exchanges, and how many answered |
| `coinbase`, `kraken`, `bitstamp`, `gemini` | Each exchange's mid price |
| `yes_bid`, `yes_bid_sz`, `no_bid`, `no_bid_sz` | Top of Kalshi's book. The YES ask is `100 − no_bid`. |
| `loop_ms` | How long that row took to collect |

### `results.csv`

One row per finished window:

| Column | Meaning |
|---|---|
| `ticker`, `open_ts`, `close_ts` | Window and its start and end times |
| `strike`, `settle_value` | Kalshi's official start and end values |
| `our_open_avg`, `our_close_avg` | Our exchange median averaged over the same minutes |
| `open_gap`, `close_gap` | Kalshi's value minus ours |
| `result` | `yes` or `no` |

---

## Lessons

1. **Measure before assuming.** An early estimate of the index offset, +$10 read off 2 windows, was wrong. Over 200+ windows it's about −$1, with a spread of ±$6.
2. **Benchmark against the market, not a coin flip.** Being right 60% of the time means nothing if the price already said 60%.
3. **Keep tuning data and test data apart.** Several settings looked strong on the data they were tuned on and lost on the next day's.
4. **A better estimate isn't a better trade.** The model beat the mid-price late in the window and still lost money, because the spread and fees cost more than the gap.
5. **Averaging down hides losses.** One live position grew from 10 to 30 contracts on the way down and lost $14.65 in a single window.
6. **Build the safety rails first.** Dry-run by default, loss limits and a simulated exchange caught bugs before they cost money.

---

## Limits and next steps

**Limits**

- The recorder samples about once a second, so effects faster than one second are invisible.
- 216 windows covers four days. Calm and volatile days behave differently.
- Fills are simulated from the top of the book. Real orders larger than the displayed size would do worse.

**Possible next studies**

- **Final-minute study.** Compare the model with the actual bid and ask, not the mid, in the last 5 minutes, to see if any of its accuracy advantage survives the spread.
- **Mispricing map.** Check whether the market is wrong under specific conditions, such as after large moves or on volatile days.

---

## Next project

**[kalshi-market-scanner](https://github.com/zacharyalitwin-alt/kalshi-market-scanner)** takes the question one step further. If the 15-minute Bitcoin market is priced about right, is every Kalshi market? The scanner records hundreds of markets across about 200 series as they approach close, then measures calibration and accuracy category by category, using the same rules as this project: benchmark against the market's price, choose on TRAIN, judge on unseen TEST, and include fees.

---

## What's not in this repo

- **API keys.** None of these tools need one. The `.gitignore` blocks key files.
- **The trading bots.** The eight bot designs are retired and kept private.
- **Recorded data.** `data/live/` grows every day and stays local.

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

For research and education only. Not investment advice. Prediction markets carry real risk of loss, and this project found no edge.
