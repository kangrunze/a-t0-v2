#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
止损占比 vs 日均振幅 相关性诊断（diag_stop_ratio_vs_amplitude）
================================================================
对 100 只股票（3年窗口），计算每只股票的：
  1. 止损占比 = 固定止损笔数 / (固定止损笔数 + 移动止盈笔数)
  2. 日均振幅 = mean((high - low) / open) × 100   （复用 diag_stepD 口径）

然后：
  1. Spearman 相关系数（止损占比 vs 日均振幅）
  2. 按日均振幅四分位分组，各组平均止损占比
  3. 盈利组 vs 亏损组 的平均止损占比对比

不改任何策略参数，纯诊断。
"""
import json
import statistics
from pathlib import Path
from collections import Counter, defaultdict

# ── 路径配置 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(r"d:\project\a-t0-v2")
REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
DATA_DIR = Path(r"D:\project\data\zz500_5min")
TAG = "sample100_3y"
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"


def quantile(data, q):
    if not data:
        return float("nan")
    s = sorted(data)
    n = len(s)
    if n == 1:
        return s[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def spearman_rank(x, y):
    """计算 Spearman 秩相关系数。"""
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    # 排名（平均排名处理并列）
    def rank(arr):
        indexed = sorted(enumerate(arr), key=lambda t: t[1])
        ranks = [0.0] * len(arr)
        i = 0
        while i < len(indexed):
            j = i
            while j + 1 < len(indexed) and indexed[j+1][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-based
            for k in range(i, j+1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks
    rx = rank(x)
    ry = rank(y)
    # Pearson on ranks
    n = len(x)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((r - mean_rx) ** 2 for r in rx)
    var_y = sum((r - mean_ry) ** 2 for r in ry)
    if var_x == 0 or var_y == 0:
        return float("nan")
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def load_stock_daily_bars(code):
    path = DATA_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    daily_bars = d.get("daily_bars", {})
    return {d: bars for d, bars in daily_bars.items() if START_DATE <= d <= END_DATE}


def compute_amplitude_mean(daily_bars):
    """
    日均振幅 = mean((high - low) / open) × 100
    复用 diag_stepD_attribution.py L124/L159 口径。
    """
    amps = []
    for date, bars in daily_bars.items():
        if not bars:
            continue
        high = max(b["high"] for b in bars)
        low = min(b["low"] for b in bars)
        opn = bars[0]["open"]
        if opn > 0:
            amps.append((high - low) / opn)
    return statistics.mean(amps) * 100 if amps else None


def main():
    print("=" * 70)
    print("止损占比 vs 日均振幅 相关性诊断")
    print(f"数据源: tag={TAG}, {START_DATE}~{END_DATE}")
    print("=" * 70)

    reports = sorted(REPORT_DIR.glob(f"*_{TAG}_{START_DATE}_{END_DATE}_report.json"))
    print(f"找到 {len(reports)} 份 report.json")

    # 收集每只股票的：固定止损笔数、移动止盈笔数、净盈亏、日均振幅
    stock_data = []  # {code, fixed_stop, trailing_stop, stop_ratio, net_pnl, amplitude}
    stocks_loaded = 0

    for rp in reports:
        code = rp.name.split("_")[0]
        with open(rp, "r", encoding="utf-8") as f:
            report = json.load(f)
        if report.get("total_trades", 0) == 0:
            continue

        # 统计固定止损 vs 移动止盈（从 risk_events 的 max_favorable 判断）
        fixed_stop = 0
        trailing_stop = 0
        for dr in report.get("daily_results", []):
            for ev in dr.get("risk_events", []):
                if ev.get("type") != "stopped":
                    continue
                max_fav = ev.get("max_favorable", 0)
                if max_fav is None or max_fav == 0:
                    fixed_stop += 1
                else:
                    trailing_stop += 1

        total_stop = fixed_stop + trailing_stop
        if total_stop == 0:
            continue

        stop_ratio = fixed_stop / total_stop
        net_pnl = report.get("net_pnl_with_unrealized", report.get("net_pnl", 0))

        # 日均振幅
        daily_bars = load_stock_daily_bars(code)
        amplitude = compute_amplitude_mean(daily_bars)
        if amplitude is None:
            continue

        stock_data.append({
            "code": code,
            "fixed_stop": fixed_stop,
            "trailing_stop": trailing_stop,
            "total_stop": total_stop,
            "stop_ratio": stop_ratio,
            "net_pnl": net_pnl,
            "amplitude": amplitude,
        })

        stocks_loaded += 1
        if stocks_loaded % 20 == 0:
            print(f"  已处理 {stocks_loaded} 只股票")

    print(f"\n共加载 {len(stock_data)} 只股票")

    if len(stock_data) < 5:
        print("样本不足，退出。")
        return

    # ════════════════════════════════════════════════════════════
    # 1. Spearman 相关系数
    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("1. 止损占比 vs 日均振幅 Spearman 相关系数")
    print(f"{'=' * 70}")

    amplitudes = [s["amplitude"] for s in stock_data]
    stop_ratios = [s["stop_ratio"] for s in stock_data]
    net_pnls = [s["net_pnl"] for s in stock_data]

    rho_amp = spearman_rank(amplitudes, stop_ratios)
    rho_pnl = spearman_rank(net_pnls, stop_ratios)
    rho_amp_pnl = spearman_rank(amplitudes, net_pnls)

    print(f"  止损占比 vs 日均振幅: ρ = {rho_amp:+.4f}")
    print(f"  止损占比 vs 净盈亏:   ρ = {rho_pnl:+.4f}")
    print(f"  日均振幅 vs 净盈亏:   ρ = {rho_amp_pnl:+.4f}")

    print(f"\n  日均振幅分布: p25={quantile(amplitudes,0.25):.2f}%  "
          f"p50={quantile(amplitudes,0.50):.2f}%  p75={quantile(amplitudes,0.75):.2f}%")
    print(f"  止损占比分布: p25={quantile(stop_ratios,0.25)*100:.1f}%  "
          f"p50={quantile(stop_ratios,0.50)*100:.1f}%  p75={quantile(stop_ratios,0.75)*100:.1f}%")

    # ════════════════════════════════════════════════════════════
    # 2. 按日均振幅四分位分组
    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("2. 按日均振幅四分位分组的平均止损占比")
    print(f"{'=' * 70}")

    q25 = quantile(amplitudes, 0.25)
    q50 = quantile(amplitudes, 0.50)
    q75 = quantile(amplitudes, 0.75)

    groups = [
        ("Q1 低振幅", [s for s in stock_data if s["amplitude"] <= q25]),
        ("Q2 中低", [s for s in stock_data if q25 < s["amplitude"] <= q50]),
        ("Q3 中高", [s for s in stock_data if q50 < s["amplitude"] <= q75]),
        ("Q4 高振幅", [s for s in stock_data if s["amplitude"] > q75]),
    ]

    print(f"  振幅分位: Q25={q25:.2f}%  Q50={q50:.2f}%  Q75={q75:.2f}%")
    print(f"\n  {'分组':<12s}  {'股票数':>6s}  {'振幅均值':>10s}  {'止损占比均值':>12s}  {'净盈亏均值':>12s}")
    print(f"  {'-'*12}  {'-'*6}  {'-'*10}  {'-'*12}  {'-'*12}")
    for label, grp in groups:
        if not grp:
            continue
        amp_mean = statistics.mean(s["amplitude"] for s in grp)
        ratio_mean = statistics.mean(s["stop_ratio"] for s in grp)
        pnl_mean = statistics.mean(s["net_pnl"] for s in grp)
        print(f"  {label:<12s}  {len(grp):>6d}  {amp_mean:>9.2f}%  {ratio_mean*100:>11.1f}%  {pnl_mean:>+12.2f}")

    # ════════════════════════════════════════════════════════════
    # 3. 盈利组 vs 亏损组 的止损占比对比
    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("3. 盈利组 vs 亏损组 止损占比对比")
    print(f"{'=' * 70}")

    profitable = [s for s in stock_data if s["net_pnl"] > 0]
    losing = [s for s in stock_data if s["net_pnl"] <= 0]

    print(f"  盈利组: {len(profitable)} 只 | 亏损组: {len(losing)} 只")

    if profitable and losing:
        p_ratio = statistics.mean(s["stop_ratio"] for s in profitable)
        l_ratio = statistics.mean(s["stop_ratio"] for s in losing)
        p_amp = statistics.mean(s["amplitude"] for s in profitable)
        l_amp = statistics.mean(s["amplitude"] for s in losing)

        print(f"\n  {'指标':<20s}  {'盈利组':>12s}  {'亏损组':>12s}  {'差异':>12s}")
        print(f"  {'-'*20}  {'-'*12}  {'-'*12}  {'-'*12}")
        print(f"  {'止损占比均值':<20s}  {p_ratio*100:>11.1f}%  {l_ratio*100:>11.1f}%  {(l_ratio-p_ratio)*100:>+11.1f}pp")
        print(f"  {'日均振幅均值':<20s}  {p_amp:>11.2f}%  {l_amp:>11.2f}%  {l_amp-p_amp:>+11.2f}pp")
        print(f"  {'止损占比中位数':<20s}  {statistics.median(s['stop_ratio'] for s in profitable)*100:>11.1f}%  "
              f"{statistics.median(s['stop_ratio'] for s in losing)*100:>11.1f}%")

        # 分位数对比
        print(f"\n  止损占比分位数对比:")
        for label, grp in [("盈利组", profitable), ("亏损组", losing)]:
            ratios = [s["stop_ratio"] for s in grp]
            print(f"    {label}: p25={quantile(ratios,0.25)*100:.1f}%  "
                  f"p50={quantile(ratios,0.50)*100:.1f}%  p75={quantile(ratios,0.75)*100:.1f}%")

    # ════════════════════════════════════════════════════════════
    # 散点数据（便于人工判断）
    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("附：振幅最低10只 vs 振幅最高10只 的止损占比")
    print(f"{'=' * 70}")

    by_amp = sorted(stock_data, key=lambda s: s["amplitude"])
    print(f"\n  振幅最低 10 只:")
    print(f"  {'股票':<10s}  {'振幅%':>8s}  {'止损占比':>10s}  {'净盈亏':>10s}")
    for s in by_amp[:10]:
        print(f"  {s['code']:<10s}  {s['amplitude']:>7.2f}%  {s['stop_ratio']*100:>9.1f}%  {s['net_pnl']:>+10.2f}")

    print(f"\n  振幅最高 10 只:")
    for s in by_amp[-10:]:
        print(f"  {s['code']:<10s}  {s['amplitude']:>7.2f}%  {s['stop_ratio']*100:>9.1f}%  {s['net_pnl']:>+10.2f}")

    print(f"\n{'=' * 70}")
    print("诊断完成。根据相关性结果决定是否推进振幅筛选 A/B 验证。")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
