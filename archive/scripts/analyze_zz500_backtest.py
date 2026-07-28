"""分析 zz500 全集回测汇总报告，输出关键统计与分组分析。"""
import json
from pathlib import Path
from collections import defaultdict

SUMMARY = Path(r"d:\project\a-t0-v2\outputs\backtest\batch_summary_full.json")
with open(SUMMARY, "r", encoding="utf-8") as f:
    data = json.load(f)

overall = data["overall"]
per_stock = data["per_stock"]

print("=" * 70)
print("整体汇总")
print("=" * 70)
print(f"  股票数:          {overall['stocks']}")
print(f"  总交易笔数:      {overall['total_trades']:,}")
print(f"  配对笔数:        {overall['paired_trades']:,} "
      f"({overall['paired_trades']/overall['total_trades']*100:.1f}%)")
print(f"  盈利笔数:        {overall['win_trades']:,}")
print(f"  整体胜率:        {overall['win_rate']*100:.2f}%")
print(f"  毛利润:          {overall['gross_pnl']:+,.2f}")
print(f"  总成本:          {overall['total_cost']:,.2f}")
print(f"  净盈亏(已实现):  {overall['net_pnl']:+,.2f}")
print(f"  未配对浮盈浮亏:  {overall['unrealized_pnl']:+,.2f}")
print(f"  净盈亏(含浮盈):  {overall['net_pnl_with_unrealized']:+,.2f}")
print(f"  超时腿数:        {overall['expired_legs_count']}")
print(f"  超时腿真实盈亏:  {overall['expired_legs_real_pnl']:+,.2f}")
print(f"  回测结束未配对腿: {overall['final_open_legs_count']}")
print(f"  盈利股票数:      {overall['profitable_stocks']}")
print(f"  亏损股票数:      {overall['losing_stocks']}")

# 单只股票统计
trades_list = [s["total_trades"] for s in per_stock]
pnl_list = [s["net_pnl"] for s in per_stock]
pnl_with_unreal = [s["net_pnl_with_unrealized"] for s in per_stock]
win_rates = [s["win_rate"] for s in per_stock if s["paired_trades"] > 0]

print()
print("=" * 70)
print("单股统计分布")
print("=" * 70)
print(f"  交易笔数:  min={min(trades_list)}, max={max(trades_list)}, "
      f"avg={sum(trades_list)/len(trades_list):.1f}")
print(f"  净盈亏:    min={min(pnl_list):+,.2f}, max={max(pnl_list):+,.2f}, "
      f"avg={sum(pnl_list)/len(pnl_list):+,.2f}")
print(f"  含浮盈:    min={min(pnl_with_unreal):+,.2f}, max={max(pnl_with_unreal):+,.2f}, "
      f"avg={sum(pnl_with_unreal)/len(pnl_with_unreal):+,.2f}")
print(f"  胜率:      min={min(win_rates)*100:.1f}%, max={max(win_rates)*100:.1f}%, "
      f"avg={sum(win_rates)/len(win_rates)*100:.1f}%")

# 盈利股票
profitable = [s for s in per_stock if s["net_pnl_with_unrealized"] > 0]
print()
print("=" * 70)
print(f"盈利股票明细（共 {len(profitable)} 只）")
print("=" * 70)
print(f"{'code':<8}{'trades':<8}{'paired':<8}{'win_rate':<10}{'net_pnl':<14}{'with_unreal':<14}")
for s in sorted(profitable, key=lambda x: x["net_pnl_with_unrealized"], reverse=True):
    wr = f"{s['win_rate']*100:.1f}%"
    print(f"{s['code']:<8}{s['total_trades']:<8}{s['paired_trades']:<8}{wr:<10}"
          f"{s['net_pnl']:+14.2f}{s['net_pnl_with_unrealized']:+14.2f}")

# 亏损最多 Top 10
print()
print("=" * 70)
print("亏损最多 Top 10")
print("=" * 70)
worst = sorted(per_stock, key=lambda x: x["net_pnl_with_unrealized"])[:10]
print(f"{'code':<8}{'trades':<8}{'paired':<8}{'win_rate':<10}{'net_pnl':<14}{'with_unreal':<14}")
for s in worst:
    wr = f"{s['win_rate']*100:.1f}%"
    print(f"{s['code']:<8}{s['total_trades']:<8}{s['paired_trades']:<8}{wr:<10}"
          f"{s['net_pnl']:+14.2f}{s['net_pnl_with_unrealized']:+14.2f}")

# 按板块分组（代码前缀）
print()
print("=" * 70)
print("按板块分组（代码前缀）")
print("=" * 70)
groups = defaultdict(list)
for s in per_stock:
    c = s["code"]
    if c.startswith("60"):
        groups["沪市主板(60x)"].append(s)
    elif c.startswith("68"):
        groups["科创板(68x)"].append(s)
    elif c.startswith("30"):
        groups["创业板(30x)"].append(s)
    elif c.startswith("00"):
        groups["深市主板(00x)"].append(s)
    elif c.startswith("002"):
        groups["中小板(002)"].append(s)
    else:
        groups["其他"].append(s)

print(f"{'板块':<18}{'股票数':<8}{'总交易':<10}{'配对':<10}{'胜率':<10}{'净盈亏':<18}{'含浮盈':<18}")
for name, items in sorted(groups.items(), key=lambda x: -len(x[1])):
    n = len(items)
    tt = sum(s["total_trades"] for s in items)
    pt = sum(s["paired_trades"] for s in items)
    wr = sum(s["win_trades"] for s in items) / pt * 100 if pt else 0
    npnl = sum(s["net_pnl"] for s in items)
    npr = sum(s["net_pnl_with_unrealized"] for s in items)
    print(f"{name:<18}{n:<8}{tt:<10}{pt:<10}{wr:<10.1f}{npnl:<+18,.2f}{npr:<+18,.2f}")

# 按胜率分桶
print()
print("=" * 70)
print("胜率分布")
print("=" * 70)
buckets = {"<20%": 0, "20-30%": 0, "30-40%": 0, "40-50%": 0, "50-60%": 0, ">=60%": 0}
for s in per_stock:
    if s["paired_trades"] == 0:
        continue
    wr = s["win_rate"]
    if wr < 0.2:
        buckets["<20%"] += 1
    elif wr < 0.3:
        buckets["20-30%"] += 1
    elif wr < 0.4:
        buckets["30-40%"] += 1
    elif wr < 0.5:
        buckets["40-50%"] += 1
    elif wr < 0.6:
        buckets["50-60%"] += 1
    else:
        buckets[">=60%"] += 1
for k, v in buckets.items():
    print(f"  {k:<10}: {v}")

# 按净盈亏分桶
print()
print("=" * 70)
print("净盈亏分布（含浮盈）")
print("=" * 70)
pnls = [s["net_pnl_with_unrealized"] for s in per_stock]
buckets = {
    "<-10000": 0, "-10000~-5000": 0, "-5000~-2000": 0,
    "-2000~-1000": 0, "-1000~0": 0, ">=0": 0,
}
for p in pnls:
    if p < -10000:
        buckets["<-10000"] += 1
    elif p < -5000:
        buckets["-10000~-5000"] += 1
    elif p < -2000:
        buckets["-5000~-2000"] += 1
    elif p < -1000:
        buckets["-2000~-1000"] += 1
    elif p < 0:
        buckets["-1000~0"] += 1
    else:
        buckets[">=0"] += 1
for k, v in buckets.items():
    print(f"  {k:<18}: {v}")
