#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
振幅筛选 A/B 对比（compare_amplitude_ab）
========================================
对比：
  A) baseline: 100 股全量（tag=sample100_3y）
  B) amp_on:   9 只通过 60 日振幅筛选（tag=sample100_3y_amp_on）

输出对比表 + 被筛掉 91 只在 A 里的盈亏验证。
"""
import json
from pathlib import Path

REPORT_DIR = Path(r"d:\project\a-t0-v2\outputs\backtest")
TAG_A = "sample100_3y"
TAG_B = "sample100_3y_amp_on"
POOL_DETAIL = Path(r"d:\project\a-t0-v2\outputs\amplitude_pool\amplitude_pool_detail.json")


def load_summary(tag):
    p = REPORT_DIR / f"batch_summary_{tag}.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    a = load_summary(TAG_A)
    b = load_summary(TAG_B)
    a_o = a["overall"]
    b_o = b["overall"]

    # 被筛掉的股票列表
    with open(POOL_DETAIL, "r", encoding="utf-8") as f:
        pool = json.load(f)
    filtered_codes = [r["code"] for r in pool["filtered"]]
    passed_codes = [r["code"] for r in pool["passed"]]

    # A 里被筛掉股票的盈亏
    a_per_stock = {s["code"]: s for s in a["per_stock"]}
    filtered_pnls = []
    misclassified_loss = []  # 被筛掉的盈利股（误伤）
    filtered_loss_pnl = 0
    filtered_profit_pnl = 0
    for code in filtered_codes:
        if code in a_per_stock:
            pnl = a_per_stock[code].get("net_pnl_with_unrealized", a_per_stock[code]["net_pnl"])
            filtered_pnls.append((code, pnl))
            if pnl > 0:
                misclassified_loss.append((code, pnl))
                filtered_profit_pnl += pnl
            else:
                filtered_loss_pnl += pnl

    # A 里通过筛选股票的盈亏（应与 B 一致）
    passed_pnls_in_a = []
    for code in passed_codes:
        if code in a_per_stock:
            pnl = a_per_stock[code].get("net_pnl_with_unrealized", a_per_stock[code]["net_pnl"])
            passed_pnls_in_a.append((code, pnl))

    # ── 对比表 ──
    print("=" * 80)
    print("振幅筛选 A/B 对比")
    print(f"A: {TAG_A} (100 股全量)")
    print(f"B: {TAG_B} (9 只通过 60 日振幅筛选 >= 4.94%)")
    print("=" * 80)

    def fmt_num(v):
        if isinstance(v, int):
            return f"{v:,}"
        return f"{v:,.2f}"

    # 手算派生指标
    a_paired_rate = a_o["paired_trades"] / a_o["total_trades"] * 100 if a_o["total_trades"] else 0
    b_paired_rate = b_o["paired_trades"] / b_o["total_trades"] * 100 if b_o["total_trades"] else 0
    a_cgr = a_o["total_cost"] / a_o["gross_pnl"] * 100 if a_o["gross_pnl"] else 0
    b_cgr = b_o["total_cost"] / b_o["gross_pnl"] * 100 if b_o["gross_pnl"] else 0

    rows = [
        ("股票数", a_o["stocks"], b_o["stocks"], f"{b_o['stocks']-a_o['stocks']:+,d}"),
        ("总交易笔数", a_o["total_trades"], b_o["total_trades"], f"{b_o['total_trades']-a_o['total_trades']:+,d}"),
        ("配对笔数", a_o["paired_trades"], b_o["paired_trades"], f"{b_o['paired_trades']-a_o['paired_trades']:+,d}"),
        ("配对率%", f"{a_paired_rate:.1f}%", f"{b_paired_rate:.1f}%", f"{b_paired_rate-a_paired_rate:+.1f}pp"),
        ("整体胜率%", f"{a_o['win_rate']*100:.1f}%", f"{b_o['win_rate']*100:.1f}%", f"{(b_o['win_rate']-a_o['win_rate'])*100:+.1f}pp"),
        ("毛利润", fmt_num(a_o["gross_pnl"]), fmt_num(b_o["gross_pnl"]), fmt_num(b_o["gross_pnl"]-a_o["gross_pnl"])),
        ("总成本", fmt_num(a_o["total_cost"]), fmt_num(b_o["total_cost"]), fmt_num(b_o["total_cost"]-a_o["total_cost"])),
        ("净盈亏(已实现)", fmt_num(a_o["net_pnl"]), fmt_num(b_o["net_pnl"]), fmt_num(b_o["net_pnl"]-a_o["net_pnl"])),
        ("净盈亏(含浮盈)", fmt_num(a_o["net_pnl_with_unrealized"]), fmt_num(b_o["net_pnl_with_unrealized"]), fmt_num(b_o["net_pnl_with_unrealized"]-a_o["net_pnl_with_unrealized"])),
        ("盈利股票数", f"{a_o['profitable_stocks']}/{a_o['stocks']}", f"{b_o['profitable_stocks']}/{b_o['stocks']}", ""),
        ("成本/毛利比%", f"{a_cgr:.1f}%", f"{b_cgr:.1f}%", f"{b_cgr-a_cgr:+.1f}pp"),
    ]

    print(f"\n{'指标':<24s} {'A(100股)':>14s} {'B(9股)':>14s} {'变化':>14s}")
    print(f"{'-'*24} {'-'*14} {'-'*14} {'-'*14}")
    for label, va, vb, diff in rows:
        print(f"{label:<24s} {str(va):>14s} {str(vb):>14s} {str(diff):>14s}")

    # ── 被筛掉股票验证 ──
    print(f"\n{'=' * 80}")
    print(f"被筛掉 {len(filtered_codes)} 只股票在 A 组里的盈亏验证")
    print(f"{'=' * 80}")

    total_filtered_pnl = sum(p for _, p in filtered_pnls)
    profit_in_filtered = [p for _, p in filtered_pnls if p > 0]
    loss_in_filtered = [p for _, p in filtered_pnls if p <= 0]

    print(f"  被筛掉股票总数: {len(filtered_pnls)}")
    print(f"  其中盈利股: {len(profit_in_filtered)} 只（误伤）")
    print(f"  其中亏损股: {len(loss_in_filtered)} 只")
    print(f"  被筛掉股票总盈亏: {total_filtered_pnl:+,.2f}")
    print(f"    盈利股贡献: {filtered_profit_pnl:+,.2f}")
    print(f"    亏损股贡献: {filtered_loss_pnl:+,.2f}")

    if misclassified_loss:
        print(f"\n  被误伤的 {len(misclassified_loss)} 只盈利股:")
        print(f"  {'股票':<10s} {'净盈亏':>12s}")
        for code, pnl in sorted(misclassified_loss, key=lambda x: -x[1]):
            print(f"  {code:<10s} {pnl:>+12.2f}")
        print(f"  误伤盈利合计: {filtered_profit_pnl:+,.2f}")

    # ── 通过筛选股票在 A 里的表现 ──
    print(f"\n{'=' * 80}")
    print(f"通过筛选 {len(passed_codes)} 只股票在 A 组里的盈亏")
    print(f"{'=' * 80}")
    print(f"  {'股票':<10s} {'A组净盈亏':>12s} {'B组净盈亏':>12s} {'差异':>12s}")
    print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*12}")
    b_per_stock = {s["code"]: s for s in b["per_stock"]}
    for code, pnl_a in sorted(passed_pnls_in_a, key=lambda x: -x[1]):
        pnl_b = b_per_stock.get(code, {}).get("net_pnl_with_unrealized", b_per_stock.get(code, {}).get("net_pnl", 0))
        print(f"  {code:<10s} {pnl_a:>+12.2f} {pnl_b:>+12.2f} {pnl_b-pnl_a:>+12.2f}")

    # ── 机制验证 ──
    print(f"\n{'=' * 80}")
    print("机制验证：改善来自哪里？")
    print(f"{'=' * 80}")
    a_net = a_o["net_pnl"]
    b_net = b_o["net_pnl"]
    print(f"  A 组总净盈亏(100股): {a_net:+,.2f}")
    print(f"  B 组总净盈亏(9股):   {b_net:+,.2f}")
    print(f"  被筛掉91只总盈亏:    {total_filtered_pnl:+,.2f}")
    print(f"  验证: B ≈ A - 被筛掉 = {a_net - total_filtered_pnl:+,.2f} (应接近 B)")

    # 误伤率
    if len(filtered_codes) > 0:
        misclassify_rate = len(misclassified_loss) / len(filtered_codes) * 100
        print(f"\n  误伤率: {len(misclassified_loss)}/{len(filtered_codes)} = {misclassify_rate:.1f}%")
        print(f"  误伤盈亏占比: {filtered_profit_pnl:+,.2f} / {abs(total_filtered_pnl):,.2f} = {abs(filtered_profit_pnl/total_filtered_pnl*100) if total_filtered_pnl != 0 else 0:.1f}%")

    print(f"\n{'=' * 80}")


if __name__ == "__main__":
    main()
