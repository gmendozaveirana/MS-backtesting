# BTC Coin-M DCA Ladder Backtest

A deterministic backtester for a DCA ladder on coin-margined (inverse) BTC perpetual futures, with explicit contract mechanics: inverse PnL in BTC, funding, fees, slippage and isolated-margin liquidation. The objective of the strategy is to **accumulate BTC**, so results are reported in BTC as well as USD.

## Strategy

- **Capital:** 1 BTC in total. It is the only collateral and the only source of ladder buys; no external capital is ever added.
- **Entry:** open a long with `open_margin_pct` of the capital at leverage `leverage_limit`.
- **Ladder:** every day the close is below the average entry, add 1–5% of the capital depending on the distance to the average entry, plus an extra 5–10% between −20% and −60%. Adds are capped at the free margin; when it is exhausted the ladder stops buying.
- **Exit:** close the cycle at `take_profit_pct` above the average entry and start a new one.
- **Profits above 1 BTC** become a margin buffer: they back the position (lower liquidation price) but are never used for ladder buys.
- **Liquidation (isolated margin):** position margin and buffer are lost, undeployed free margin survives, and trading stops for the window.

## Data

- BTC-USD daily OHLC from Yahoo Finance, 2015-10-24 → 2026-11-07, UTC daily bars (`BTC-USD_Historical.csv`).
- Funding: constant 10% p.a. (no historical funding series).
- Costs: 4 bps commission per side, 0.5 bps slippage, 0.5% maintenance margin.

## Methodology

- **In-sample (IS):** 2023-03-20 → 2025-01-01, starting at the first weekly close above the weekly SMA50 and SMA200.
- **Out-of-sample (OOS):** 2025-01-02 → 2026-01-26, ending at the confirmed start of the bear market.
- **Grid (IS only):** leverage {2, 3, 4, 5} × take-profit {4, 5, 6, 8, 10%} × opening size {1–6%} = 120 combinations.
- **Selection rule, fixed before looking at OOS:** no liquidation, max drawdown in BTC ≤ 50%, then maximum final equity in BTC. The selected parameters are applied unchanged to OOS.

## Results

Selected parameters: leverage 5, take-profit 8%, opening size 6%.

| | IS strategy | IS hold 1 BTC | OOS strategy | OOS hold 1 BTC |
|---|---|---|---|---|
| Final equity (BTC) | **2.90** | 1.00 | **0.48** | 1.00 |
| Max drawdown (BTC) | −36% | 0% | −54% | 0% |
| CAGR (USD) | 260% | 98% | −54% | −8% |
| Sharpe (USD, daily) | 1.75 | 1.67 | −0.97 | 0.00 |
| Max drawdown (USD) | −47% | −26% | −67% | −32% |
| ES 95% (daily, USD) | −10.6% | −5.2% | −8.2% | −4.9% |

OOS final equity in BTC for the full grid:

| Leverage | Liquidated | Median | Best |
|---|---|---|---|
| 2 | 0/30 | 0.82 | 0.99 |
| 3 | 0/30 | 0.74 | 0.98 |
| 4 | 0/30 | 0.65 | 0.97 |
| 5 | 30/30 | 0.55 | 0.60 |

![Equity in BTC, IS and OOS](https://raw.githubusercontent.com/MendoVeirana/MS-backtesting/main/equity.curve.png)

## Findings

- In the IS bull market no combination was liquidated and final BTC rises monotonically with leverage, so the selection rule picks the highest leverage tested.
- The selected leverage was liquidated on 2025-02-28, eight weeks into the OOS window, when BTC fell ~25% from the start of the open cycle (USD 104k to an intraday low of USD 78k).
- No combination ends OOS above 1 BTC: once the market moves sideways or down, the ladder deploys its capital and the position loses BTC relative to holding.
- Conclusion: the IS edge is leveraged exposure to a bull regime, not a persistent edge. The OOS test is a single ~1-year window and a single regime, so it rejects the IS-selected parameters but does not establish performance for any other configuration.

## Limitations

- Daily bars: intraday path, gaps and liquidation timing within the day are approximated with the daily low.
- Close-based fills; no T+1 execution.
- Constant funding; real funding was higher in strong bull phases.
- Liquidation price follows a simplified isolated-margin formula; exchange-specific rules (tiers, insurance fund, ADL) are not modelled.
- 120 combinations tested on one ~21-month IS window: the selected parameters carry substantial estimation error.

## How to run

- Python 3.10+
- `pip install -r requirements.txt` (plus `jupyter` to run the notebook)
- Open `MS_backtesting.ipynb` and run all cells. Results are deterministic.

## Files

- `engine.py`: daily backtest loop (cycles, funding, ladder, take-profit, liquidation)
- `mechanics.py`: inverse-contract notional, fees, funding, average entry, liquidation price
- `buying_strategy.py`: ladder rules
- `metrics.py`: CAGR, Sharpe (incl. Lo-adjusted), Sortino, drawdowns, VaR/ES, BTC-denominated metrics
- `config.py`: strategy parameters
