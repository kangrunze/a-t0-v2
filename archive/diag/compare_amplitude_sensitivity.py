#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
振幅阈值敏感性分析（compare_amplitude_sensitivity）
=====================================================
对比 5 个阈值的 A/B 结果：
  - baseline (无筛选, 100股)
  - 0.04     (tag=sample100_3y_amp040)
  - 0.045    (tag=sample100_3y_amp045)
  - 0.0494   (tag=sample100_3y_amp_on, 已有)
  - 0.05     (tag=sample100_3y_amp050)

输出对比表 + 各阈值的误伤率。
"""
import json
from pathlib import Path

REPORT_DIR = Path(r"d:\project\a-t0-v2\outputs\backtest")

THRESHOLDS = [
    ("baseline(无筛选)", None, "sample100_3y"),
    ("0.0400", 0.040, "sample100_3y_amp040"),
    ("0.0450", 0.045, "sample100_3y_amp045"),
    ("0.0494", 0.0494, "sample100_3y_amp_on"),
    ("0.0500", 0.050, "sample100_3y_amp050"),
]


def load_summary(tag):
    p = REPORT_DIR / f"batch_summary_{tag}.json"
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    print("=" * 90)
    print("振幅阈值敏感性分析")
    print("样本: 100股×3年 (2023-07-25~2026-07-22, seed=42)")
    print("=" * 90)

    # 加载所有汇总
    summaries = {}
    for label, threshold, tag in THRESHOLDS:
        s = load_summary(tag)
        if s is None:
            print(f"  [跳过] {label}: 未找到 {tag}")
            continue
        summaries[label] = (threshold, s)

    # baseline 的 per_stock 用于计算误伤
    baseline = summaries.get("baseline(无筛选)")
    if not baseline:
        print("baseline 缺失，无法计算误伤率")
        return
    baseline_per_stock = {s["code"]: s for s in baseline[1]["per_stock"]}

    # ── 主对比表 ──
    print(f"\n{'阈值':<16s} {'股票数':>6s} {'净盈亏':>12s} {'毛利润':>12s} {'总成本':>12s} {'成本/毛利%':>10s} {'胜率%':>7s} {'盈利股':>8s}")
    print(f"{'-'*16} {'-'*6} {'-'*12} {'-'*12} {'-'*12} {'-'*10} {'-'*7} {'-'*8}")

    rows_data = []
    for label, (threshold, s) in summaries.items():
        o = s["overall"]
        stocks = o["stocks"]
        net = o["net_pnl"]
        gross = o["gross_pnl"]
        cost = o["total_cost"]
        cgr = cost / gross * 100 if gross else 0
        win_rate = o["win_rate"] * 100
        profit_stocks = o["profitable_stocks"]
        print(f"{label:<16s} {stocks:>6d} {net:>+12.2f} {gross:>+12.2f} {cost:>12.2f} {cgr:>9.1f}% {win_rate:>6.1f}% {profit_stocks:>3d}/{stocks}")
        rows_data.append((label, threshold, s, stocks, net, gross, cost, cgr, win_rate, profit_stocks))

    # ── 误伤率分析 ──
    print(f"\n{'=' * 90}")
    print("误伤率分析（被筛掉的盈利股）")
    print(f"{'=' * 90}")

    # 需要从 amp_on 的 per_stock 反推被筛掉的股票
    # 更准确的方式：用 baseline 的 per_stock 减去各阈值的 per_stock
    print(f"\n{'阈值':<16s} {'通过':>6s} {'筛掉':>6s} {'筛掉盈利':>8s} {'误伤率%':>8s} {'误伤盈亏':>12s} {'筛掉总盈亏':>12s}")
    print(f"{'-'*16} {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*12} {'-'*12}")

    for label, (threshold, s) in summaries.items():
        if threshold is None:
            continue  # baseline 不算误伤
        o = s["overall"]
        passed_codes = {stock["code"] for stock in s["per_stock"]}
        filtered_codes = set(baseline_per_stock.keys()) - passed_codes

        misclassified_profit = 0
        misclassified_pnl = 0
        filtered_total_pnl = 0
        for code in filtered_codes:
            bs = baseline_per_stock[code]
            pnl = bs.get("net_pnl_with_unrealized", bs["net_pnl"])
            filtered_total_pnl += pnl
            if pnl > 0:
                misclassified_profit += 1
                misclassified_pnl += pnl

        misclassify_rate = misclassified_profit / len(filtered_codes) * 100 if filtered_codes else 0
        print(f"{label:<16s} {len(passed_codes):>6d} {len(filtered_codes):>6d} {misclassified_profit:>8d} {misclassify_rate:>7.1f}% {misclassified_pnl:>+12.2f} {filtered_total_pnl:>+12.2f}")

    # ── 通过/筛掉明细（0.0494 为例）──
    print(f"\n{'=' * 90}")
    print("0.0494 阈值下的通过股票明细（和其他阈值的对比）")
    print(f"{'=' * 90}")

    # 收集各阈值通过的股票
    passed_by_threshold = {}
    for label, (threshold, s) in summaries.items():
        if threshold is None:
            continue
        passed_by_threshold[label] = {stock["code"] for stock in s["per_stock"]}

    # 0.0494 通过的 9 只
    base_passed = passed_by_threshold.get("0.0494", set())
    if base_passed:
        print(f"\n0.0494 通过的 {len(base_passed)} 只股票在 baseline 里的盈亏:")
        print(f"  {'股票':<10s} {'baseline净盈亏':>14s}")
        for code in sorted(base_passed):
            bs = baseline_per_stock[code]
            pnl = bs.get("net_pnl_with_unrealized", bs["net_pnl"])
            print(f"  {code:<10s} {pnl:>+14.2f}")

    # 各阈值新增/减少的股票相对 0.0494
    print(f"\n相对 0.0494 的差异:")
    for label, passed in passed_by_threshold.items():
        if label == "0.0494":
            continue
        added = passed - base_passed
        removed = base_passed - passed
        if added:
            print(f"  {label} 比 0.0494 多通过 {len(added)} 只: {sorted(added)}")
        if removed:
            print(f"  {label} 比 0.0494 少通过 {len(removed)} 只: {sorted(removed)}")
        if not added and not removed:
            print(f"  {label} 与 0.0494 通过的股票完全相同")

    print(f"\n{'=' * 90}")


if __name__ == "__main__":
    main()
