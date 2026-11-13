# Kalshi Bot Learning

*15-minute BTC window recorder and research lab*

**Can a bot beat Kalshi's 15-minute Bitcoin market?**

This project sets out to answer that with data instead of hope. It holds the tools: a market recorder, a backtester, a strategy lab, a lag study and a regime study. Run them on your own recordings to measure the market yourself.

This repo holds only the research tools. They record and analyze only. **Nothing here places trades or needs an API key, and no recorded data or results are published.**

> **Next project:** [kalshi-market-scanner](https://github.com/zacharyalitwin-alt/kalshi-market-scanner) asks whether *other* Kalshi markets (weather, oil, sports, economics) are priced as well as this one.

---

## Contents

- [The market](#the-market)
- [What the tools measure](#what-the-tools-measure)
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

## What the tools measure

| Test | Tool | Question it answers |
|---|---|---|
| **Model vs market** | `backtest_v3.py` | Does a pricing model predict outcomes better than the market's own price, and at which times in the window? |
| **Backtest** | `backtest_v3.py` | Would simple taker, maker and last-minute rules have made money after fees and spread, and in both halves of the data? |
| **Strategy lab** | `strategy_lab.py` | Does any of 8 strategy ideas hold up on unseen data after being tuned on earlier data? |
| **Regime study** | `regime_study.py` | Do strategies do better in certain market conditions (volatility, trend, time of day, recent form), and would switching between them help? |
| **Lag study** | `lag_study.py` | How fast does Kalshi's price follow a Bitcoin jump, and is there time to trade it? |

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

Leave the recorder running. It writes to `data/live/`, about 1 MB a day. On Windows you can double-click `start_recorder.bat` instead: it runs the recorder and restarts it if it stops, noting each restart in `data/live/recorder_restarts.txt`. After 10+ hours, open a second terminal in `tools/` and run the analysis:

```bash
python backtest_v3.py
python strategy_lab.py
python lag_study.py
python regime_study.py
```

Reports are saved to `data/live/` as `backtest_report.txt`, `lab_report.txt`, `lag_report.txt` and `regime_report.txt`.

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
| `regime_study.py` | Checks whether each bot does better in certain market conditions known before a window opens (BTC volatility, trend, Kalshi's previous range, time of day, recent form). Picks rules on TRAIN, tests them and a bot-switching manager on TEST, and runs a luck check with shuffled conditions. | `regime_report.txt` |
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

1. **Measure before assuming.** Estimate offsets and costs from many windows, not a few.
2. **Benchmark against the market, not a coin flip.** Being right 60% of the time means nothing if the price already said 60%.
3. **Keep tuning data and test data apart.** Settings that look strong on the data they were tuned on often fail on the next day's.
4. **A better estimate isn't a better trade.** A model can beat the mid-price and still lose money, because you trade at the ask and pay a fee.
5. **Averaging down hides losses.** Adding to a losing position makes one bad window much worse.
6. **Build the safety rails first.** Dry-run by default, loss limits and a simulated exchange catch bugs before they cost money.
7. **Count every trade.** A bot's settled trades alone can look far better than its real result if early exits are left out.

---

## Limits and next steps

**Limits**

- The recorder samples about once a second, so effects faster than one second are invisible.
- A few days of recording can't cover every kind of market. Calm and volatile days behave differently.
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
- **The trading bots.** The paper-trading bots used in this research (a model-based taker bot and a follow-the-move bot, with dry-run mode, loss limits and a simulated-exchange test harness) are available for purchase as research code. They have **not** shown a proven edge and lost money in testing after fees. No profit is promised. Contact: zacharyalitwin@gmail.com
- **Recorded data.** `data/live/` grows every day and stays local.

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

For research and education only. Not investment advice. Prediction markets carry real risk of loss.
