# Project notes for Claude

Research project on Kalshi's 15-minute Bitcoin up/down market (`KXBTC15M`). See README.md for the full write-up and results.

## Rules

- **Research only.** Nothing in this repo places orders. Don't add trading code, order endpoints or API-key handling unless the owner explicitly asks.
- **Never commit secrets or data.** No `.pem` / key files, no `rec_*.csv`, `results.csv` or reports. `.gitignore` covers these; keep it that way.
- **Test the way the README says.** Benchmark against the market's own price, choose settings on TRAIN and report them on unseen TEST, and include Kalshi's taker fee `ceil(0.07 × C × P × (1 − P))` and the spread in every simulated trade.
- **Report results plainly.** If a test finds no edge, say so. Don't tune until something looks good.
- Explain things to the owner in plain language and give Windows Command Prompt commands (one per code block).

## Layout

- `tools/record_v3.py` – recorder; writes `data/live/rec_YYYYMMDD.csv` (one row per second, UTC day) and `data/live/results.csv` (one row per window).
- `tools/backtest_v3.py` – shared loader (`load()`, `parse_args()` with `--since` / `--last` / folder) plus accuracy and rule P&L.
- `tools/strategy_lab.py`, `tools/lag_study.py` – import `backtest_v3` from the same folder.
- All tools find `data/live/` relative to `tools/`. Standard library only, except `requests` for the recorder.

## Owner's setup

- Runs on Windows from a folder on the Desktop (inside OneDrive), with the same `tools/` and `data/live/` layout.
- The recorder retries writes for ~5 s because OneDrive can briefly lock files.
- The owner uploads `rec_*.csv`, `results.csv` and report files to the chat for analysis.

## Status (Nov 2026)

216 windows recorded. Model vs market, strategy lab, backtest and lag study all found no tradeable edge. The model beats the market's *mid* in the last 5 minutes but loses after spread and fees. Next idea on the list: a final-minute study comparing the model with the actual bid/ask.

## Updating the repo

When results change, update the README's Results section with the new numbers and window counts, and keep the honest bottom line.
