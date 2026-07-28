"""
步骤F5 — 500股全池A/B验证（样本外证据）
=========================================
在500股×3年全池上验证振幅筛选的有效性。

核心价值：500只中仅100只为训练集（seed=42抽样），其余400只为独立样本。
         本步骤是唯一能提供真正样本外证据的环节。

数据源：
  - batch_summary_zz500_3y_amp_on.json: 500股振幅筛选开启（36只通过）
  - batch_summary_zz500_3y_baseline.json: 500股无筛选baseline
  - amplitude_pool_detail.json: 训练集100只的振幅筛选结果（用于识别训练集股票）

⚠️ 最终是否写入默认配置，应主要依据本步骤的OOS样本证据，
   而非100股A/B的 +22,959。
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AMP_ON_SUMMARY = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz500_3y_amp_on.json"
BASELINE_SUMMARY = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz500_3y_baseline.json"
POOL_DETAIL = PROJECT_ROOT / "outputs" / "amplitude_pool" / "amplitude_pool_detail.json"
OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtest" / "stepF5_oos_500.json"


def load_summary(path: Path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_training_codes() -> set[str]:
    """从 amplitude_pool_detail.json 获取训练集100只股票代码。"""
    with open(POOL_DETAIL, "r", encoding="utf-8") as f:
        pool = json.load(f)
    return {r["code"] for r in pool["passed"] + pool["filtered"]}


def analyze_group(per_stock: list[dict], label: str) -> dict:
    """分析一组股票的盈亏。"""
    if not per_stock:
        return {"label": label, "n": 0}
    pnls = [s.get("net_pnl_with_unrealized", s["net_pnl"]) for s in per_stock]
    total = sum(pnls)
    profitable = sum(1 for p in pnls if p > 0)
    losing = sum(1 for p in pnls if p <= 0)
    gross = sum(s.get("gross_pnl", 0) for s in per_stock)
    cost = sum(s.get("total_cost", 0) for s in per_stock)
    cgr = cost / gross * 100 if gross else 0
    return {
        "label": label,
        "n": len(per_stock),
        "net_pnl": round(total, 2),
        "avg_pnl": round(mean(pnls), 2) if pnls else 0,
        "median_pnl": round(median(pnls), 2) if pnls else 0,
        "profitable": profitable,
        "losing": losing,
        "profitable_ratio": round(profitable / len(per_stock), 4),
        "gross_pnl": round(gross, 2),
        "total_cost": round(cost, 2),
        "cost_gross_ratio": round(cgr, 1),
    }


def print_group(g: dict):
    """打印组分析结果。"""
    print(f"  {g['label']}:")
    print(f"    股票数: {g['n']}")
    print(f"    净盈亏: {g['net_pnl']:+,.2f}  (均值={g['avg_pnl']:+,.2f}  中位={g['median_pnl']:+,.2f})")
    print(f"    盈利股: {g['profitable']}/{g['n']} ({g['profitable_ratio']*100:.1f}%)")
    print(f"    毛利润: {g['gross_pnl']:+,.2f}  总成本: {g['total_cost']:,.2f}  成本/毛利: {g['cost_gross_ratio']}%")


def main():
    print("=" * 90)
    print("步骤F5 — 500股全池A/B验证（样本外证据）")
    print("=" * 90)

    training_codes = get_training_codes()
    print(f"\n训练集股票数: {len(training_codes)}（用于区分训练集/样本外）")

    # ── amp_on 分析 ──
    amp_on = load_summary(AMP_ON_SUMMARY)
    if amp_on is None:
        print(f"[ERROR] 未找到 amp_on 汇总: {AMP_ON_SUMMARY}")
        return

    amp_stocks = amp_on["per_stock"]
    amp_overall = amp_on["overall"]
    print(f"\n{'=' * 90}")
    print(f"amp_on: 500股振幅筛选开启（阈值=4.94%）")
    print(f"{'=' * 90}")
    print(f"  通过筛选: {amp_overall['stocks']}/500 只 ({amp_overall['stocks']/500*100:.1f}%)")
    print(f"  净盈亏(含浮盈): {amp_overall['net_pnl_with_unrealized']:+,.2f}")
    print(f"  毛利润: {amp_overall['gross_pnl']:+,.2f}  总成本: {amp_overall['total_cost']:,.2f}")
    cgr = amp_overall['total_cost'] / amp_overall['gross_pnl'] * 100 if amp_overall['gross_pnl'] else 0
    print(f"  成本/毛利比: {cgr:.1f}%")
    print(f"  整体胜率: {amp_overall['win_rate']*100:.1f}%")
    print(f"  盈利股票: {amp_overall['profitable_stocks']}/{amp_overall['stocks']}")

    # 分离训练集和样本外
    amp_training = [s for s in amp_stocks if s["code"] in training_codes]
    amp_oos = [s for s in amp_stocks if s["code"] not in training_codes]

    print(f"\n{'─' * 90}")
    print(f"分离训练集 vs 样本外（通过筛选的 {len(amp_stocks)} 只）")
    print(f"{'─' * 90}")

    g_all = analyze_group(amp_stocks, "全部通过(36只)")
    g_train = analyze_group(amp_training, f"训练集通过({len(amp_training)}只)")
    g_oos = analyze_group(amp_oos, f"样本外通过({len(amp_oos)}只)")

    for g in [g_all, g_train, g_oos]:
        print_group(g)
        print()

    # ── 样本外证据判断 ──
    print(f"{'=' * 90}")
    print("样本外证据判断")
    print(f"{'=' * 90}")

    print(f"\n  样本外通过股票数: {g_oos['n']}")
    print(f"  样本外净盈亏: {g_oos['net_pnl']:+,.2f}")
    print(f"  样本外盈利占比: {g_oos['profitable']}/{g_oos['n']} ({g_oos['profitable_ratio']*100:.1f}%)")
    print(f"  样本外成本/毛利比: {g_oos['cost_gross_ratio']}%")

    # 判断标准
    criteria = [
        ("样本外净盈亏为正", g_oos["net_pnl"] > 0),
        ("样本外盈利占比 > 50%", g_oos["profitable_ratio"] > 0.5),
        ("样本外成本/毛利比 < 100%", g_oos["cost_gross_ratio"] < 100),
        ("样本外股票数 >= 20（分散度）", g_oos["n"] >= 20),
    ]
    print(f"\n  判断标准:")
    for label, passed in criteria:
        print(f"    [{'✓' if passed else '✗'}] {label}")
    passed_count = sum(1 for _, p in criteria if p)
    print(f"\n  通过 {passed_count}/{len(criteria)} 项")

    # ── baseline A/B 对比（如果存在）──
    baseline = load_summary(BASELINE_SUMMARY)
    if baseline is None:
        print(f"\n{'=' * 90}")
        print("[待补充] baseline 500股回测尚未完成，A/B对比暂缺")
        print(f"{'=' * 90}")
    else:
        print(f"\n{'=' * 90}")
        print(f"baseline vs amp_on A/B 对比（500股全池）")
        print(f"{'=' * 90}")

        base_stocks = baseline["per_stock"]
        base_overall = baseline["overall"]

        # 分离训练集和样本外
        base_training = [s for s in base_stocks if s["code"] in training_codes]
        base_oos = [s for s in base_stocks if s["code"] not in training_codes]

        gb_all = analyze_group(base_stocks, f"baseline全部({len(base_stocks)}只)")
        gb_train = analyze_group(base_training, f"baseline训练集({len(base_training)}只)")
        gb_oos = analyze_group(base_oos, f"baseline样本外({len(base_oos)}只)")

        print(f"\n  {'组别':<28s} {'数量':>6s} {'净盈亏':>14s} {'盈利占比':>10s} {'成本/毛利%':>10s}")
        print(f"  {'-'*28} {'-'*6} {'-'*14} {'-'*10} {'-'*10}")
        for g in [gb_all, gb_train, gb_oos, g_all, g_train, g_oos]:
            print(f"  {g['label']:<28s} {g['n']:>6d} {g['net_pnl']:>+14.2f} {g['profitable_ratio']*100:>9.1f}% {g['cost_gross_ratio']:>9.1f}%")

        # 误伤率分析（样本外）
        # 被筛掉的样本外股票 = baseline样本外 - amp_on样本外通过的
        oos_passed_codes = {s["code"] for s in amp_oos}
        oos_filtered = [s for s in base_oos if s["code"] not in oos_passed_codes]

        oos_filtered_profit = [s for s in oos_filtered
                                if s.get("net_pnl_with_unrealized", s["net_pnl"]) > 0]
        oos_filtered_loss = [s for s in oos_filtered
                              if s.get("net_pnl_with_unrealized", s["net_pnl"]) <= 0]

        print(f"\n  样本外被筛掉股票分析:")
        print(f"    被筛掉总数: {len(oos_filtered)}")
        print(f"    其中盈利（误伤）: {len(oos_filtered_profit)} ({len(oos_filtered_profit)/len(oos_filtered)*100:.1f}%)")
        print(f"    其中亏损: {len(oos_filtered_loss)} ({len(oos_filtered_loss)/len(oos_filtered)*100:.1f}%)")
        if oos_filtered:
            oos_filtered_pnl = sum(s.get("net_pnl_with_unrealized", s["net_pnl"]) for s in oos_filtered)
            print(f"    被筛掉总盈亏: {oos_filtered_pnl:+,.2f}")

    # 输出结构化结果
    output = {
        "amp_on_overall": amp_overall,
        "training_passed": g_train,
        "oos_passed": g_oos,
        "all_passed": g_all,
        "baseline_available": baseline is not None,
    }
    if baseline:
        output["baseline_overall"] = baseline["overall"]
        output["baseline_oos"] = gb_oos
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结构化结果 -> {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
