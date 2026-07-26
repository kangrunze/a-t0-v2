"""对比优化前 vs 优化后的回测结果"""
import json
from pathlib import Path
from collections import defaultdict

BASE_FILE = Path(r"d:\project\a-t0-v2\outputs\backtest\batch_summary_full.json")
OPT_FILE = Path(r"d:\project\a-t0-v2\outputs\backtest\batch_summary_opt_full.json")

with open(BASE_FILE, "r", encoding="utf-8") as f:
    base = json.load(f)
with open(OPT_FILE, "r", encoding="utf-8") as f:
    opt = json.load(f)

b_ov = base["overall"]
o_ov = opt["overall"]

print("=" * 90)
print("整体对比（500只 × 3年）")
print("=" * 90)
print(f"{'指标':<25}{'优化前':>20}{'优化后':>20}{'变化':>20}")
print("-" * 90)

def row(name, b, o, fmt="{:+.2f}", pct=False):
    if pct:
        chg = f"{(o-b)/abs(b)*100:+.1f}%" if b != 0 else "N/A"
        print(f"{name:<25}{b:>20,.2f}{o:>20,.2f}{chg:>20}")
    else:
        diff = o - b
        print(f"{name:<25}{b:>{20}}{o:>{20}}{fmt.format(diff):>{20}}")

row("总交易笔数", b_ov["total_trades"], o_ov["total_trades"], fmt="{:+d}")
row("配对笔数", b_ov["paired_trades"], o_ov["paired_trades"], fmt="{:+d}")
row("盈利笔数", b_ov["win_trades"], o_ov["win_trades"], fmt="{:+d}")
row("整体胜率", b_ov["win_rate"]*100, o_ov["win_rate"]*100, fmt="{:+.2f}pp")
row("毛利润", b_ov["gross_pnl"], o_ov["gross_pnl"], pct=True)
row("总成本", b_ov["total_cost"], o_ov["total_cost"], pct=True)
row("净盈亏(已实现)", b_ov["net_pnl"], o_ov["net_pnl"], pct=True)
row("超时腿数", b_ov["expired_legs_count"], o_ov["expired_legs_count"], fmt="{:+d}")
row("超时腿真实盈亏", b_ov["expired_legs_real_pnl"], o_ov["expired_legs_real_pnl"], pct=True)
row("净盈亏(含浮盈)", b_ov["net_pnl_with_unrealized"], o_ov["net_pnl_with_unrealized"], pct=True)
row("盈利股票数", b_ov["profitable_stocks"], o_ov["profitable_stocks"], fmt="{:+d}")
row("亏损股票数", b_ov["losing_stocks"], o_ov["losing_stocks"], fmt="{:+d}")

# 配对率对比
b_pair_rate = b_ov["paired_trades"] / b_ov["total_trades"] * 100
o_pair_rate = o_ov["paired_trades"] / o_ov["total_trades"] * 100
row("配对率", b_pair_rate, o_pair_rate, fmt="{:+.2f}pp")

# 平均单笔盈亏对比
b_avg = b_ov["net_pnl_with_unrealized"] / b_ov["paired_trades"]
o_avg = o_ov["net_pnl_with_unrealized"] / o_ov["paired_trades"]
row("平均单笔盈亏(配对)", b_avg, o_avg, fmt="{:+.2f}")

# 平均每只股票盈亏
b_per = b_ov["net_pnl_with_unrealized"] / b_ov["stocks"]
o_per = o_ov["net_pnl_with_unrealized"] / o_ov["stocks"]
row("平均每股盈亏", b_per, o_per, fmt="{:+.2f}")

print()
print("=" * 90)
print("关键改善指标")
print("=" * 90)
print(f"盈利股票数:  {b_ov['profitable_stocks']:>3d} → {o_ov['profitable_stocks']:>3d}  "
      f"(+{o_ov['profitable_stocks']-b_ov['profitable_stocks']} 只, "
      f"{(o_ov['profitable_stocks']-b_ov['profitable_stocks'])/b_ov['profitable_stocks']*100:+.0f}%)")
print(f"净盈亏:      {b_ov['net_pnl_with_unrealized']:>+15,.2f} → {o_ov['net_pnl_with_unrealized']:>+15,.2f}  "
      f"({(o_ov['net_pnl_with_unrealized']-b_ov['net_pnl_with_unrealized'])/abs(b_ov['net_pnl_with_unrealized'])*100:+.1f}%)")
print(f"超时腿数:    {b_ov['expired_legs_count']:>6d} → {o_ov['expired_legs_count']:>6d}  "
      f"({(o_ov['expired_legs_count']-b_ov['expired_legs_count'])/b_ov['expired_legs_count']*100:+.1f}%)")
print(f"总交易笔数:  {b_ov['total_trades']:>6d} → {o_ov['total_trades']:>6d}  "
      f"({(o_ov['total_trades']-b_ov['total_trades'])/b_ov['total_trades']*100:+.1f}%)  ← 降频效果")

# 按板块对比
print()
print("=" * 90)
print("按板块对比")
print("=" * 90)
def by_board(per_stock):
    g = defaultdict(lambda: {"n":0, "trades":0, "paired":0, "wins":0, "net":0.0, "profit":0})
    for s in per_stock:
        c = s["code"]
        if c.startswith("60") or c.startswith("00"):
            bk = "主板(60x/00x)"
        elif c.startswith("68"):
            bk = "科创板(68x)"
        elif c.startswith("30"):
            bk = "创业板(30x)"
        else:
            bk = "其他"
        g[bk]["n"] += 1
        g[bk]["trades"] += s.get("total_trades", 0)
        g[bk]["paired"] += s.get("paired_trades", 0)
        g[bk]["wins"] += s.get("win_trades", 0)
        g[bk]["net"] += s.get("net_pnl_with_unrealized", s.get("net_pnl", 0))
        if s.get("net_pnl_with_unrealized", s.get("net_pnl", 0)) > 0:
            g[bk]["profit"] += 1
    return g

bg = by_board(base["per_stock"])
og = by_board(opt["per_stock"])
print(f"{'板块':<14}{'股票':<6}{'':>3}{'交易':<8}{'配对':<8}{'胜率':<10}{'净盈亏':<16}{'盈利股':<8}")
print("-" * 90)
for bk in ["主板(60x/00x)", "科创板(68x)", "创业板(30x)"]:
    b = bg[bk]; o = og[bk]
    bwr = b["wins"]/b["paired"]*100 if b["paired"] else 0
    owr = o["wins"]/o["paired"]*100 if o["paired"] else 0
    print(f"{bk:<14}{b['n']:<6}前 {b['trades']:<8}{b['paired']:<8}{bwr:<10.1f}{b['net']:<+16,.2f}{b['profit']:<8}")
    print(f"{'':<14}{'':<6}后 {o['trades']:<8}{o['paired']:<8}{owr:<10.1f}{o['net']:<+16,.2f}{o['profit']:<8}")
    print()

# 优化后盈利股票 Top 20
print("=" * 90)
print("优化后盈利股票 Top 20")
print("=" * 90)
profitable = [s for s in opt["per_stock"] if s.get("net_pnl_with_unrealized", 0) > 0]
profitable.sort(key=lambda x: x["net_pnl_with_unrealized"], reverse=True)
print(f"{'code':<8}{'trades':<8}{'paired':<8}{'win_rate':<10}{'net_pnl':<14}{'with_unreal':<14}")
for s in profitable[:20]:
    wr = f"{s['win_rate']*100:.1f}%"
    print(f"{s['code']:<8}{s['total_trades']:<8}{s['paired_trades']:<8}{wr:<10}"
          f"{s['net_pnl']:+14.2f}{s['net_pnl_with_unrealized']:+14.2f}")

# 优化后亏损 Top 10
print()
print("=" * 90)
print("优化后亏损 Top 10")
print("=" * 90)
worst = sorted(opt["per_stock"], key=lambda x: x.get("net_pnl_with_unrealized", 0))[:10]
print(f"{'code':<8}{'trades':<8}{'paired':<8}{'win_rate':<10}{'net_pnl':<14}{'with_unreal':<14}")
for s in worst:
    wr = f"{s['win_rate']*100:.1f}%"
    print(f"{s['code']:<8}{s['total_trades']:<8}{s['paired_trades']:<8}{wr:<10}"
          f"{s['net_pnl']:+14.2f}{s['net_pnl_with_unrealized']:+14.2f}")

# 胜率分布对比
print()
print("=" * 90)
print("胜率分布对比")
print("=" * 90)
buckets_b = {"<20%":0, "20-30%":0, "30-40%":0, "40-50%":0, "50-60%":0, ">=60%":0}
buckets_o = {"<20%":0, "20-30%":0, "30-40%":0, "40-50%":0, "50-60%":0, ">=60%":0}
for s in base["per_stock"]:
    if s.get("paired_trades",0) == 0: continue
    wr = s["win_rate"]
    k = "<20%" if wr<0.2 else "20-30%" if wr<0.3 else "30-40%" if wr<0.4 else "40-50%" if wr<0.5 else "50-60%" if wr<0.6 else ">=60%"
    buckets_b[k] += 1
for s in opt["per_stock"]:
    if s.get("paired_trades",0) == 0: continue
    wr = s["win_rate"]
    k = "<20%" if wr<0.2 else "20-30%" if wr<0.3 else "30-40%" if wr<0.4 else "40-50%" if wr<0.5 else "50-60%" if wr<0.6 else ">=60%"
    buckets_o[k] += 1
print(f"{'胜率区间':<12}{'优化前':<10}{'优化后':<10}{'变化':<10}")
for k in ["<20%","20-30%","30-40%","40-50%","50-60%",">=60%"]:
    print(f"{k:<12}{buckets_b[k]:<10}{buckets_o[k]:<10}{buckets_o[k]-buckets_b[k]:>+10d}")
