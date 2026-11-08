"""Figures for the README (static PNG, light surface)."""
import os

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import TwoSlopeNorm, LinearSegmentedColormap
from matplotlib.patches import Rectangle

# Palette tokens (validated: blue/orange pass CVD and contrast checks on the surface)
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
AXIS = '#c3c2b7'
BLUE = '#2a78d6'
ORANGE = '#eb6834'
RED = '#e34948'
NEUTRAL = '#f0efec'
BLUE_LIGHT = '#cde2fb'

plt.rcParams.update({
    'figure.facecolor': SURFACE,
    'axes.facecolor': SURFACE,
    'savefig.facecolor': SURFACE,
    'axes.edgecolor': AXIS,
    'axes.labelcolor': INK_2,
    'axes.titlecolor': INK,
    'axes.titlesize': 11,
    'axes.titleweight': 'bold',
    'axes.labelsize': 9,
    'xtick.color': MUTED,
    'ytick.color': MUTED,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'axes.grid': True,
    'grid.color': GRID,
    'grid.linewidth': 0.6,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'legend.frameon': False,
    'legend.fontsize': 8,
    'font.family': 'DejaVu Sans',
})


def _btc_total(pf: pd.DataFrame) -> pd.Series:
    return pf['Equity_USD'] / pf['Close']


def _cycle_lines(ax, cycles):
    for c in cycles[1:]:
        ax.axvline(c['start_date'], color=GRID, linewidth=0.8, zorder=0)


def equity_figure(perf_is, cycles_is, perf_oos, cycles_oos, params, path):
    """Equity in BTC, drawdown in BTC and price/entry/liquidation, IS and OOS."""
    w_is = (perf_is['Date'].iloc[-1] - perf_is['Date'].iloc[0]).days
    w_oos = (perf_oos['Date'].iloc[-1] - perf_oos['Date'].iloc[0]).days
    fig, axes = plt.subplots(3, 2, figsize=(13, 9.5), sharey='row',
                             gridspec_kw={'width_ratios': [w_is, w_oos],
                                          'height_ratios': [1.3, 0.8, 1.1]})
    panels = [('In-sample', perf_is, cycles_is), ('Out-of-sample', perf_oos, cycles_oos)]
    for col, (label, pf, cycles) in enumerate(panels):
        pf = pf.copy()
        total = _btc_total(pf)
        withdrawn = pf['Withdrawn_BTC']
        d0, d1 = pf['Date'].iloc[0], pf['Date'].iloc[-1]

        # Row 1: equity in BTC
        ax = axes[0, col]
        _cycle_lines(ax, cycles)
        ax.axhline(1.0, color=MUTED, linestyle='--', linewidth=1.2, label='Hold 1 BTC')
        ax.plot(pf['Date'], withdrawn, color=ORANGE, linewidth=2, drawstyle='steps-post',
                label='Profits withdrawn (BTC)')
        ax.plot(pf['Date'], total, color=BLUE, linewidth=2, label='Total = account + withdrawn (BTC)')
        ax.annotate(f'{total.iloc[-1]:.2f} BTC', (d1, total.iloc[-1]), xytext=(4, 0),
                    textcoords='offset points', va='center', fontsize=8, color=INK)
        ax.set_title(f'{label}: {d0:%Y-%m-%d} to {d1:%Y-%m-%d}', loc='left')
        if col == 0:
            ax.set_ylabel('BTC')
            ax.legend(loc='upper left')

        # Row 2: drawdown in BTC
        ax = axes[1, col]
        dd = (total / total.cummax() - 1.0) * 100.0
        _cycle_lines(ax, cycles)
        ax.fill_between(pf['Date'], dd, 0, color=BLUE_LIGHT, linewidth=0)
        ax.plot(pf['Date'], dd, color=BLUE, linewidth=1.2)
        ax.axhline(-50, color=RED, linestyle='--', linewidth=1)
        i = int(dd.idxmin())
        ax.annotate(f'{dd.min():.0f}%', (pf['Date'].loc[i], dd.min()), xytext=(4, -2),
                    textcoords='offset points', fontsize=8, color=INK)
        if col == 0:
            ax.set_ylabel('Drawdown in BTC (%)')
            ax.text(d0, -50, ' max-drawdown limit −50%', color=INK_2, fontsize=8, va='bottom')

        # Row 3: price, average entry, liquidation price
        ax = axes[2, col]
        _cycle_lines(ax, cycles)
        ae = pf['Avg_Entry'].replace(0, np.nan)
        lq = pf['Liq_Price_Pre'].replace(0, np.nan)
        ax.plot(pf['Date'], pf['Close'] / 1000, color=INK, linewidth=1.2, label='BTC close')
        ax.plot(pf['Date'], ae / 1000, color=BLUE, linewidth=1.5, label='Average entry')
        ax.plot(pf['Date'], lq / 1000, color=RED, linewidth=1.5, label='Liquidation price')
        if col == 0:
            ax.set_ylabel('USD (thousands)')
            ax.legend(loc='upper left')
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        for r in (0, 1):
            axes[r, col].xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
            axes[r, col].tick_params(labelbottom=False)

    axes[2, 1].text(0.98, 0.04, f"OOS ends {perf_oos['Date'].iloc[-1]:%Y-%m-%d}:\nfirst weekly candle fully below SMA50W",
                    transform=axes[2, 1].transAxes, ha='right', va='bottom', fontsize=8, color=INK_2)
    fig.suptitle(f"Selected on IS: leverage {params['leverage_limit']:g}, take-profit "
                 f"{params['take_profit_pct']:.0%}, opening size {params['open_margin_pct']:.0%}"
                 "  ·  vertical hairlines = new cycle", x=0.01, ha='left', fontsize=9, color=INK_2)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    _save(fig, path)


def grid_figure(grid_is, grid_oos, params, path):
    """Final BTC for every (take-profit, opening size), per leverage, IS and OOS."""
    levs = sorted(grid_is['leverage_limit'].unique())
    tps = sorted(grid_is['take_profit_pct'].unique())
    opens = sorted(grid_is['open_margin_pct'].unique())
    vals = pd.concat([grid_is, grid_oos])['final_btc']
    cmap = LinearSegmentedColormap.from_list('div', [RED, NEUTRAL, BLUE])
    norm = TwoSlopeNorm(vmin=min(vals.min(), 0.5), vcenter=1.0, vmax=max(vals.max(), 1.5))
    fig, axes = plt.subplots(2, len(levs), figsize=(13, 6.4), sharex=True, sharey=True)
    for r, (label, g) in enumerate([('IS', grid_is), ('OOS', grid_oos)]):
        for c, L in enumerate(levs):
            ax = axes[r, c]
            ax.grid(False)
            sub = g[g['leverage_limit'] == L]
            for i, tp in enumerate(tps):
                for j, om in enumerate(opens):
                    row = sub[(sub['take_profit_pct'] == tp) & (sub['open_margin_pct'] == om)].iloc[0]
                    if row['liquidated']:
                        ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=NEUTRAL,
                                               edgecolor=SURFACE, linewidth=2, hatch='///'))
                        ax.text(j, i, 'liq.', ha='center', va='center', fontsize=7, color=INK_2)
                    else:
                        ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1,
                                               facecolor=cmap(norm(row['final_btc'])),
                                               edgecolor=SURFACE, linewidth=2))
                        ax.text(j, i, f"{row['final_btc']:.2f}", ha='center', va='center',
                                fontsize=7, color=INK)
                    if (L == params['leverage_limit'] and tp == params['take_profit_pct']
                            and om == params['open_margin_pct']):
                        ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False,
                                               edgecolor=INK, linewidth=1.8))
            ax.tick_params(length=0)
            ax.set_xlim(-0.5, len(opens) - 0.5)
            ax.set_ylim(-0.5, len(tps) - 0.5)
            ax.set_xticks(range(len(opens)), [f'{o:.0%}' for o in opens])
            ax.set_yticks(range(len(tps)), [f'{t:.0%}' for t in tps])
            for s in ax.spines.values():
                s.set_visible(False)
            if r == 0:
                ax.set_title(f'Leverage {L:g}', loc='left')
            if c == 0:
                ax.set_ylabel(f'{label}\ntake-profit')
            if r == 1:
                ax.set_xlabel('opening size (% of capital)')
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(sm, ax=axes, shrink=0.8, pad=0.02)
    cb.set_label('Final BTC (account + withdrawn); 1.0 = hold', color=INK_2, fontsize=8)
    cb.outline.set_visible(False)
    fig.suptitle('Final BTC per parameter set. Hatched = liquidated. Outlined = selected on IS.',
                 x=0.01, ha='left', fontsize=9, color=INK_2)
    _save(fig, path)


def cycles_figure(cycles_oos, path):
    """BTC gained or lost in each OOS cycle."""
    gains = [c['end_equity_btc_excl_unreal'] - c['start_equity_btc_excl_unreal']
             + c.get('withdrawn_btc', 0.0) for c in cycles_oos]
    labels = [f"C{c['cycle']}\n{c['start_date']:%d %b} – {c['end_date']:%d %b}" for c in cycles_oos]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    x = np.arange(len(gains))
    ax.bar(x, gains, width=0.6, color=[BLUE if g >= 0 else RED for g in gains])
    ax.axhline(0, color=AXIS, linewidth=1)
    for xi, g, c in zip(x, gains, cycles_oos):
        tag = 'TP' if c['reason'] == 'TAKE-PROFIT' else 'open at end'
        ax.annotate(f'{g:+.3f} BTC\n{tag}', (xi, g), xytext=(0, 4 if g >= 0 else -4),
                    textcoords='offset points', ha='center', va='bottom' if g >= 0 else 'top',
                    fontsize=8, color=INK)
    ax.set_xticks(x, labels)
    ax.set_ylabel('BTC gained in cycle')
    ax.grid(axis='x', visible=False)
    lo, hi = min(gains), max(gains)
    ax.set_ylim(lo * 1.35 if lo < 0 else 0, hi * 1.45)
    ax.set_title(f'Out-of-sample cycles: net {sum(gains):+.3f} BTC', loc='left')
    fig.tight_layout()
    _save(fig, path)


def _save(fig, path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches='tight')
