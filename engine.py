from typing import Optional, Dict, Any, List, Tuple

import numpy as np
import pandas as pd
from config import StrategyConfig
from mechanics import (
    usd_notional_from_btc_margin,
    commission_btc_from_usd,
    funding_btc_from_usd,
    avg_entry_inverse_harmonic,
    liquidation_price_inverse,
    apply_slippage,
)
from buying_strategy import ladder

def run_backtest(price_df: pd.DataFrame,
                 cfg: StrategyConfig,
                 funding_daily_series: Optional[pd.Series] = None) -> Dict[str, Any]:
    """
    price_df columns: Date, Open, High, Low, Close (daily)
    Supports two contract models:
      - 'inverse' (default): coin-margined BTCUSD-like perp with inverse PnL in BTC

    funding_daily_series: optional pd.Series aligned to price_df with daily funding rate
                          (e.g., 0.0008 = 8 bps/day)

    Returns dict with:
      - perf: DataFrame of daily performance (includes diagnostics)
      - cycles: list of cycle dicts (includes start/last liq)
      - status: dict summary
    """
    model = getattr(cfg, 'contract_model', 'inverse')  # default to inverse
    df = price_df.copy().reset_index(drop=True)

    if funding_daily_series is None:
        df['FundingRateDaily'] = cfg.constant_funding_daily
    else:
        df['FundingRateDaily'] = funding_daily_series.values

    # Wallet state (BTC)
    free_margin = 0.0
    spot_btc = 0.0          # kept for output compatibility; always 0 (profits are withdrawn)
    withdrawals = []        # (date, BTC withdrawn at take-profit)
    pos_margin_btc = 0.0

    # Position state
    avg_entry = None
    in_cycle = False
    liquidated = False
    cycle_id = 0

    # Inverse path (USD contracts)
    pos_q_usd = 0.0

    # Records
    daily: List[Dict[str, Any]] = []
    starts: List[pd.Timestamp] = []
    ends: List[pd.Timestamp] = []
    cycles: List[Dict[str, Any]] = []

    for i, row in df.iterrows():
        date = row['Date']
        o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']
        fund_rate = row['FundingRateDaily']

        # 1) Start a new cycle (seed wallet and open)
        if not in_cycle and not (liquidated and cfg.stop_on_liquidation):
            cycle_id += 1

            # Seed this cycle: first cycle gets the initial margin; later cycles
            # continue with what is left (no external capital is ever added)
            free_margin = cfg.initial_margin_btc if cycle_id == 1 else min(cfg.initial_margin_btc, free_margin)
            pos_margin_btc = 0.0
            avg_entry = None
            pos_q_usd = 0.0

            # Deterministic open at Close with slippage/commission
            fill_price = apply_slippage(c, cfg.slippage_bps, buy=True)
            open_margin = min(cfg.open_margin_pct * cfg.initial_margin_btc, free_margin)

            q_add = usd_notional_from_btc_margin(open_margin, fill_price, cfg.leverage_limit)
            commission_btc = commission_btc_from_usd(q_add, fill_price, cfg.commission_rate)

            pos_q_usd = q_add
            pos_margin_btc += open_margin
            avg_entry = fill_price

            free_margin -= (open_margin + commission_btc)
            free_margin = max(0.0, free_margin)

            # Isolated margin: position margin plus the margin buffer (profits swept
            # above the initial margin) back the position; free margin does not
            isolated_wallet_btc = pos_margin_btc
            start_liq_price = liquidation_price_inverse(
                entry_price=avg_entry,
                q_usd=pos_q_usd,
                wallet_btc_excl_unreal=isolated_wallet_btc,  # Key change: isolated W
                maintenance_margin_rate=cfg.maintenance_margin_rate
            )

            in_cycle = True
            starts.append(date)
            cycles.append({
                'cycle': cycle_id,
                'start_date': date,
                'start_price': fill_price,
                'start_free_margin_btc': free_margin,
                'start_spot_btc': spot_btc,
                'start_pos_margin_btc': pos_margin_btc,
                'start_pos_q_usd': pos_q_usd,
                'start_equity_btc_excl_unreal': free_margin + spot_btc + pos_margin_btc,  # Full for equity, but liq uses isolated
                'start_liq_price': start_liq_price,
                'last_liq_price': start_liq_price
            })

        # 2) Funding (charged once): paid from free margin first; only the
        # remainder is taken from the isolated position margin
        if in_cycle:
            if model == 'inverse' and pos_q_usd > 0:
                fee_btc = funding_btc_from_usd(pos_q_usd, c, fund_rate)
                from_free = min(fee_btc, max(0.0, free_margin))
                free_margin -= from_free
                pos_margin_btc = max(0.0, pos_margin_btc - (fee_btc - from_free))

        # 3) Pre-trade liquidation (after funding, before TP/ladder)
        liq_price_pre = 0.0
        liq_denom_pre_btc = np.nan
        mm_btc_mark = 0.0
        if in_cycle and avg_entry is not None and model == 'inverse' and pos_q_usd > 0:
            # Isolated: position margin plus margin buffer (never used for ladder buys)
            isolated_wallet_btc = pos_margin_btc
            liq_denom_pre_btc = isolated_wallet_btc + (pos_q_usd / avg_entry)
            liq_price_pre = liquidation_price_inverse(
                entry_price=avg_entry,
                q_usd=pos_q_usd,
                wallet_btc_excl_unreal=isolated_wallet_btc,
                maintenance_margin_rate=cfg.maintenance_margin_rate
            )
            # Diagnostic maintenance margin at mark
            mm_btc_mark = cfg.maintenance_margin_rate * pos_q_usd / c

        # 4) Liquidation check (intraday) using PRE-trade liq
        if (in_cycle and isinstance(liq_price_pre, (int, float))
                and not np.isnan(liq_price_pre) and liq_price_pre > 0 and l <= liq_price_pre):
            ends.append(date)
            cycles[-1].update({
                'end_date': date,
                'end_price': c,
                'end_free_margin_btc': free_margin,
                'end_spot_btc': 0.0,
                'end_pos_margin_btc': 0.0,
                'end_pos_q_usd': 0.0,
                'end_equity_btc_excl_unreal': free_margin,
                'reason': 'LIQUIDATION',
                'duration_days': (date - cycles[-1]['start_date']).days
            })
            # Isolated margin: the position margin and the margin buffer are lost;
            # free margin (not yet deployed by the ladder) survives
            spot_btc = 0.0
            pos_margin_btc = 0.0
            pos_q_usd = 0.0
            avg_entry = None
            in_cycle = False
            liquidated = True

        if not in_cycle:
            # Flat day (after a liquidation with stop_on_liquidation=True):
            # keep recording equity so metrics cover the whole window
            if liquidated and cfg.stop_on_liquidation:
                daily.append({
                    'Date': date,
                    'Close': c,
                    'Equity_USD': free_margin * c,
                    'Free_Margin_BTC': free_margin,
                    'Spot_BTC': 0.0,
                    'Pos_Margin_BTC': 0.0,
                    'Pos_Q_USD': 0.0,
                    'Base_Equity_BTC': free_margin,
                    'Unreal_PnL_BTC': 0.0,
                    'Unreal_PnL_USD': 0.0,
                    'Avg_Entry': 0.0,
                    'Liq_Price': 0.0,
                    'Liq_Price_Pre': 0.0,
                    'Liq_Price_Post': 0.0,
                    # Diagnostics:
                    'Wallet_BTC_Excl_Unreal': free_margin,
                    'Q_over_Entry_BTC': 0.0,
                    'Liq_Denom_BTC_Pre': np.nan,
                    'Liq_Denom_BTC_Post': np.nan,
                    'MM_BTC_at_Mark': 0.0,
                    'Cycle': cycle_id
                })
            continue

        # 5) Take profit (pre-ladder)
        if c >= (avg_entry * (1.0 + cfg.take_profit_pct)):
            exit_price = apply_slippage(c, cfg.slippage_bps, buy=False)
            if model == 'inverse' and pos_q_usd > 0:
                realized_pnl_btc = pos_q_usd * (1.0 / avg_entry - 1.0 / exit_price)
                close_commission_btc = commission_btc_from_usd(pos_q_usd, exit_price, cfg.commission_rate)
            else:
                realized_pnl_btc = 0.0
                close_commission_btc = 0.0

            free_margin += pos_margin_btc + realized_pnl_btc
            free_margin -= close_commission_btc

            # Profits above the initial margin are withdrawn from the account:
            # they are neither margin nor ladder capital
            withdrawn_now = 0.0
            if free_margin > cfg.initial_margin_btc:
                withdrawn_now = free_margin - cfg.initial_margin_btc
                free_margin = cfg.initial_margin_btc
                withdrawals.append((date, withdrawn_now))

            ends.append(date)
            cycles[-1].update({
                'end_date': date,
                'end_price': exit_price,
                'end_free_margin_btc': free_margin,
                'end_spot_btc': spot_btc,
                'end_pos_margin_btc': 0.0,
                'end_pos_q_usd': 0.0,
                'end_equity_btc_excl_unreal': free_margin + spot_btc,
                'withdrawn_btc': withdrawn_now,
                'reason': 'TAKE-PROFIT',
                'duration_days': (date - cycles[-1]['start_date']).days
            })

            # Reset position
            pos_margin_btc = 0.0
            pos_q_usd = 0.0
            avg_entry = None
            in_cycle = False

            # Record flat day after TP
            base_equity_btc = free_margin + spot_btc  # no position margin
            equity_usd = base_equity_btc * c
            daily.append({
                'Date': date,
                'Close': c,
                'Equity_USD': equity_usd,
                'Free_Margin_BTC': free_margin,
                'Spot_BTC': spot_btc,
                'Pos_Margin_BTC': 0.0,
                'Pos_Q_USD': 0.0,
                'Base_Equity_BTC': base_equity_btc,
                'Unreal_PnL_BTC': 0.0,
                'Unreal_PnL_USD': 0.0,
                'Avg_Entry': 0.0,
                'Liq_Price': 0.0,
                'Liq_Price_Pre': liq_price_pre,
                'Liq_Price_Post': 0.0,
                # Diagnostics:
                'Wallet_BTC_Excl_Unreal': free_margin + spot_btc,
                'Q_over_Entry_BTC': 0.0,
                'Liq_Denom_BTC_Pre': liq_denom_pre_btc,
                'Liq_Denom_BTC_Post': np.nan,
                'MM_BTC_at_Mark': 0.0,
                'Cycle': cycle_id
            })
            continue

        # 7) Ladder (scale-in + optional extra injection)
        delta = (c / avg_entry) - 1.0 if (avg_entry and avg_entry > 0) else 0.0
        add_margin_btc, extra_injection_btc = ladder(delta, cfg.initial_margin_btc)
        # Extra injection: move collateral from free margin to position margin,
        # capped at the free margin available (no external capital)
        if extra_injection_btc > 0:
            extra_injection_btc = min(extra_injection_btc, max(0.0, free_margin))
            free_margin -= extra_injection_btc
            pos_margin_btc += extra_injection_btc
        # Now handle add (internal transfer + buy more Q)
        if model == 'inverse':
            # No q_cap to allow DCA even if over initial lev limit (fix: prevents blocking adds on drops)
            # Exposure cap in USD: Q_cap = (wallet_btc * leverage) * c  # Commented out for unlimited DCA
            if add_margin_btc > 0:
                fill = apply_slippage(c, cfg.slippage_bps, buy=True)
                # Planned add (no cap)
                q_usd_planned = usd_notional_from_btc_margin(add_margin_btc, fill, cfg.leverage_limit)
                q_usd_allowable = q_usd_planned  # No min with cap -- allow full planned
                if q_usd_allowable > 0:
                    # Margin required for the allowable notional
                    margin_needed = q_usd_allowable / (cfg.leverage_limit * fill)
                    # Commission for that notional
                    fee_btc = commission_btc_from_usd(q_usd_allowable, fill, cfg.commission_rate)
                    total_btc_needed = margin_needed + fee_btc
                    # If free margin is insufficient, scale the add down to what is
                    # available (zero when the ladder is fully deployed)
                    if total_btc_needed > free_margin:
                        scale = max(0.0, free_margin) / total_btc_needed
                        q_usd_allowable *= scale
                        margin_needed *= scale
                        fee_btc *= scale
                        total_btc_needed = margin_needed + fee_btc
                    # Now execute
                    free_margin -= total_btc_needed
                    pos_margin_btc += margin_needed
                    # Update position notional and average entry (inverse harmonic)
                    avg_entry = avg_entry_inverse_harmonic(pos_q_usd, avg_entry or fill,
                                                        q_usd_allowable, fill)
                    pos_q_usd += q_usd_allowable

        # 7) Post-trade liquidation (after ladder/avg_entry updates)
        liq_price_post = 0.0
        liq_denom_post_btc = np.nan
        if in_cycle and avg_entry is not None and model == 'inverse' and pos_q_usd > 0:
            wallet_btc_excl_unreal = free_margin + spot_btc + pos_margin_btc
            liq_denom_post_btc = wallet_btc_excl_unreal + (pos_q_usd / avg_entry)
            liq_price_post = liquidation_price_inverse(
                entry_price=avg_entry,
                q_usd=pos_q_usd,
                wallet_btc_excl_unreal=wallet_btc_excl_unreal,
                maintenance_margin_rate=cfg.maintenance_margin_rate
            )
            cycles[-1]['last_liq_price'] = liq_price_post

        # 8) Equity and PnL (end-of-day, post-trade state)
        base_equity_btc = free_margin + spot_btc + pos_margin_btc
        if in_cycle and avg_entry is not None and model == 'inverse' and pos_q_usd > 0:
            unreal_pnl_btc = pos_q_usd * (1.0 / avg_entry - 1.0 / c)
        else:
            unreal_pnl_btc = 0.0
        unreal_pnl_usd = unreal_pnl_btc * c
        equity_usd = base_equity_btc * c + unreal_pnl_usd

        # Diagnostics
        wallet_btc_excl_unreal = free_margin + spot_btc + pos_margin_btc
        q_over_entry = (pos_q_usd / avg_entry) if (model == 'inverse' and pos_q_usd > 0 and avg_entry) else 0.0

        # Record end-of-day row (Liq_Price == post-trade)
        daily.append({
            'Date': date,
            'Close': c,
            'Equity_USD': equity_usd,
            'Free_Margin_BTC': free_margin,
            'Spot_BTC': spot_btc,
            'Pos_Margin_BTC': pos_margin_btc,
            'Pos_Q_USD': pos_q_usd if model == 'inverse' else 0.0,
            'Base_Equity_BTC': base_equity_btc,
            'Unreal_PnL_BTC': unreal_pnl_btc,
            'Unreal_PnL_USD': unreal_pnl_usd,
            'Avg_Entry': avg_entry or 0.0,
            'Liq_Price': liq_price_post if in_cycle else 0.0,
            'Liq_Price_Pre': liq_price_pre if in_cycle else 0.0,
            'Liq_Price_Post': liq_price_post if in_cycle else 0.0,
            # Diagnostics:
            'Wallet_BTC_Excl_Unreal': wallet_btc_excl_unreal,
            'Q_over_Entry_BTC': q_over_entry,  # equals Q/E (BTC units)
            'Liq_Denom_BTC_Pre': liq_denom_pre_btc,
            'Liq_Denom_BTC_Post': liq_denom_post_btc,
            'MM_BTC_at_Mark': mm_btc_mark,
            'Cycle': cycle_id if in_cycle else 0
        })

        # 9) Optional forced close on last day (post-trade)
        is_last_day = (i == len(df) - 1)
        if cfg.forced_close_on_last_day and is_last_day:
            exit_price = apply_slippage(c, cfg.slippage_bps, buy=False)
            if model == 'inverse' and pos_q_usd > 0:
                realized_pnl_btc = pos_q_usd * (1.0 / avg_entry - 1.0 / exit_price)
                close_commission_btc = commission_btc_from_usd(pos_q_usd, exit_price, cfg.commission_rate)
            else:
                realized_pnl_btc = 0.0
                close_commission_btc = 0.0

            free_margin += pos_margin_btc + realized_pnl_btc
            free_margin -= close_commission_btc


            ends.append(date)
            cycles[-1].update({
                'end_date': date,
                'end_price': exit_price,
                'end_free_margin_btc': free_margin,
                'end_spot_btc': spot_btc,
                'end_pos_margin_btc': 0.0,
                'end_pos_q_usd': 0.0,
                'end_equity_btc_excl_unreal': free_margin + spot_btc,
                'reason': 'FORCED-CLOSE (END DATE)',
                'duration_days': (date - cycles[-1]['start_date']).days
            })

            # Reset position
            pos_margin_btc = 0.0
            pos_q_usd = 0.0
            avg_entry = None
            in_cycle = False

    perf = pd.DataFrame(daily)
    if not perf.empty:
        w = pd.Series(0.0, index=perf.index)
        if withdrawals:
            wd = pd.DataFrame(withdrawals, columns=['Date', 'w']).groupby('Date')['w'].sum()
            w = perf['Date'].map(wd).fillna(0.0)
        perf['Withdrawn_BTC'] = w.cumsum()
        perf['Account_Equity_USD'] = perf['Equity_USD']
        # Equity_USD = account + withdrawn profits (all metrics use the total)
        perf['Equity_USD'] = perf['Account_Equity_USD'] + perf['Withdrawn_BTC'] * perf['Close']
    status = {
        'liquidated': liquidated,
        'cycles_completed': sum(1 for c in cycles if 'end_date' in c),
        'total_cycles': len(cycles),
        'starts': starts,
        'ends': ends
    }
    return {'perf': perf, 'cycles': cycles, 'status': status}