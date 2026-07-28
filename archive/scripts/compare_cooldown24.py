#!/usr/bin/env python3
"""对比 baseline (cooldown=3) vs cooldown24 的 100股×3年回测结果。"""
import json
import statistics
from pathlib import Path

BASELINE = Path(r"d:\project\a-t0-v2\outputs\backtest\batch_summary_sample100_3y.json")
COOLDOWN24 = Path(r"d:\project\a-t0-v2\outputs\backtest\batch_summary_sample100_3y_cooldown24.json")

b = json.load(open(BASELINE, "r", encoding="utf-8"))
c = json.load(open(COOLDOWN24, "r", encoding="utf-8"))
bo, co = b["overall"], c["overall"]

print("=" * 70)
print("Baseline (cooldown=3) vs Cooldown24 — 100股×3年 (2023-07-25~2026-07-22)")
print("=" * 70)

def row(label, bv, cv, fmt="{:.2f}", delta_fmt="{:+.2f}"):
    try:
        delta = float(cv) - float(bv)
        pct = delta / abs(float(bv)) * 100 if float(bv) != 0 else 0
        print(f"  {label:<20s}  {fmt.format(bv):>14s}  ->  {fmt.format(cv):>14s}  ({delta_fmt.format(delta)}, {pct:+.1f}%)")
    except (TypeError, ValueError):
        print(f"  {label:<20s}  {bv}  ->  {cv}")

row("总交易笔数", bo["total_trades"], co["total_trades"], "{:d}", "{:+d}")
row("配对笔数", bo["paired_trades"], co["paired_trades"], "{:d}", "{:+d}")
bpr = bo["paired_trades"] / bo["total_trades"] * 100
cpr = co["paired_trades"] / co["total_trades"] * 100
print(f"  {'配对率':<20s}  {bpr:>13.1f}%  ->  {cpr:>13.1f}%  ({cpr-bpr:+.1f}pp)")
print(f"  {'整体胜率':<20s}  {bo['win_rate']*100:>13.1f}%  ->  {co['win_rate']*100:>13.1f}%  ({(co['win_rate']-bo['win_rate'])*100:+.1f}pp)")
row("毛利润", bo["gross_pnl"], co["gross_pnl"], "{:+.2f}")
row("总成本", bo["total_cost"], co["total_cost"], "{:.2f}")
b_ratio = bo["total_cost"] / bo["gross_pnl"] * 100
c_ratio = co["total_cost"] / co["gross_pnl"] * 100
print(f"  {'成本/毛利比':<20s}  {b_ratio:>13.1f}%  ->  {c_ratio:>13.1f}%  ({c_ratio-b_ratio:+.1f}pp)")
row("净盈亏(已实现)", bo["net_pnl"], co["net_pnl"], "{:+.2f}")
row("净盈亏(含浮盈)", bo["net_pnl_with_unrealized"], co["net_pnl_with_unrealized"], "{:+.2f}")
b_per = bo["net_pnl"] / bo["paired_trades"]
c_per = co["net_pnl"] / co["paired_trades"]
print(f"  {'单笔均盈亏(已实现)':<20s}  {b_per:>+14.2f}  ->  {c_per:>+14.2f}  ({c_per-b_per:+.2f})")
b_cost_per = bo["total_cost"] / bo["paired_trades"]
c_cost_per = co["total_cost"] / co["paired_trades"]
print(f"  {'单笔均成本':<20s}  {b_cost_per:>14.2f}  ->  {c_cost_per:>14.2f}  ({c_cost_per-b_cost_per:+.2f})")

bp = [s for s in b["per_stock"] if "error" not in s]
cp = [s for s in c["per_stock"] if "error" not in s]
bp_p = [s for s in bp if s.get("net_pnl_with_unrealized", 0) > 0]
cp_p = [s for s in cp if s.get("net_pnl_with_unrealized", 0) > 0]
print(f"  {'盈利股票数':<20s}  {len(bp_p):>8d}/100  ->  {len(cp_p):>8d}/100")
bpnls = [s.get("net_pnl_with_unrealized", 0) for s in bp]
cpnls = [s.get("net_pnl_with_unrealized", 0) for s in cp]
print(f"  {'股票PnL中位数':<20s}  {statistics.median(bpnls):>+14.2f}  ->  {statistics.median(cpnls):>+14.2f}  ({statistics.median(cpnls)-statistics.median(bpnls):+.2f})")
print(f"  {'股票PnL均值':<20s}  {statistics.mean(bpnls):>+14.2f}  ->  {statistics.mean(cpnls):>+14.2f}  ({statistics.mean(cpnls)-statistics.mean(bpnls):+.2f})")
