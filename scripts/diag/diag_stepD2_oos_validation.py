"""
步骤2 — 样本外验证
=================
对样本2（50只新股票，与样本1的100只不重叠）做：
  1. 跑盈亏分组（用样本2的 batch_summary_zz50_sample2.json）
  2. 计算每只股票的日均振幅
  3. 用步骤1定下的 Youden 阈值（4.94%）做样本外分组：
     - 高振幅组（> 4.94%）
     - 低振幅组（<= 4.94%）
  4. 对比两组的净盈亏/胜率/盈利股票占比
  5. 重新做 Mann-Whitney U + Cliff's delta（用样本2数据独立验证）

如果样本2也显示振幅差异显著且方向一致，规则成立。
"""
from __future__ import annotations

import json
import sys
import importlib.util
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 加载 at0.paths
def _load_at0_paths():
    try:
        from at0.paths import DATA_ROOT, ZZ500_5MIN_DIR
        return DATA_ROOT, ZZ500_5MIN_DIR
    except (ValueError, ImportError):
        paths_path = PROJECT_ROOT / "src" / "at0" / "paths.py"
        spec = importlib.util.spec_from_file_location("_at0_paths", str(paths_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.DATA_ROOT, mod.ZZ500_5MIN_DIR

DATA_ROOT, ZZ500_5MIN_DIR = _load_at0_paths()

# 加载 at0.features.dmi
def _load_dmi():
    try:
        from at0.features import dmi
        return dmi
    except (ValueError, ImportError):
        feats_path = PROJECT_ROOT / "src" / "at0" / "features.py"
        spec = importlib.util.spec_from_file_location("_at0_feats", str(feats_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.dmi

dmi = _load_dmi()

# 复用步骤1的统计函数
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "diag"))
from diag_stepD1_stat_test import mann_whitney_u, cliffs_delta, auc_from_u, interpret_cliffs_delta

BATCH_SAMPLE2 = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz50_sample2.json"
STEP1_RESULT = PROJECT_ROOT / "outputs" / "backtest" / "stepD1_stat_test.json"
OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtest" / "stepD2_oos_validation.json"

START_DATE = "2026-04-27"
END_DATE = "2026-07-23"


def load_stock_amplitude(code: str) -> float | None:
    """计算单只股票样本期内的日均振幅（%）。"""
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    daily_bars = data.get("daily_bars", {})
    amps = []
    for date, bars in daily_bars.items():
        if not (START_DATE <= date <= END_DATE) or not bars:
            continue
        high = max(b["high"] for b in bars)
        low = min(b["low"] for b in bars)
        opn = bars[0]["open"]
        if opn > 0:
            amps.append((high - low) / opn * 100)
    return mean(amps) if amps else None


def main():
    print("=" * 78)
    print("步骤2 — 样本外验证 (50只新股票, 与样本1不重叠)")
    print("=" * 78)

    # 读取步骤1的阈值
    with open(STEP1_RESULT, "r", encoding="utf-8") as f:
        step1 = json.load(f)
    amp_threshold = step1["amplitude_mean"]["youden_threshold"]
    amp_j = step1["amplitude_mean"]["youden_j"]
    amp_auc = step1["amplitude_mean"]["auc"]
    print(f"\n步骤1 阈值: 振幅 > {amp_threshold:.2f}% (J={amp_j:.3f}, AUC={amp_auc:.3f})")

    # 读取样本2回测结果
    with open(BATCH_SAMPLE2, "r", encoding="utf-8") as f:
        batch2 = json.load(f)

    per_stock2 = batch2["per_stock"]
    print(f"样本2: {len(per_stock2)} 只股票")

    # 计算每只股票的振幅 + 盈亏
    stocks = []
    for s in per_stock2:
        code = s["code"]
        net = s.get("net_pnl_with_unrealized", s["net_pnl"])
        amp = load_stock_amplitude(code)
        if amp is None:
            continue
        stocks.append({
            "code": code,
            "net_pnl": net,
            "amplitude": amp,
            "win_rate": s["win_rate"],
            "paired_trades": s["paired_trades"],
            "is_profitable": net > 0,
        })

    print(f"有效股票: {len(stocks)} 只")

    # ── 验证1: 用步骤1阈值做样本外分组对比 ──
    print("\n" + "=" * 78)
    print("验证1: 用步骤1阈值（振幅 > 4.94%）做样本外分组")
    print("=" * 78)

    high_amp = [s for s in stocks if s["amplitude"] > amp_threshold]
    low_amp = [s for s in stocks if s["amplitude"] <= amp_threshold]

    print(f"\n高振幅组（> {amp_threshold:.2f}%）: {len(high_amp)} 只")
    print(f"低振幅组（<= {amp_threshold:.2f}%）: {len(low_amp)} 只")

    def group_summary(group, name):
        if not group:
            print(f"  {name}: 无数据")
            return {}
        nets = [s["net_pnl"] for s in group]
        wrs = [s["win_rate"] for s in group if s["win_rate"] > 0]
        profitable = sum(1 for s in group if s["is_profitable"])
        total_pnl = sum(nets)
        avg_pnl = mean(nets)
        med_pnl = median(nets)
        avg_wr = mean(wrs) if wrs else 0
        amps = [s["amplitude"] for s in group]
        print(f"  {name} (n={len(group)}):")
        print(f"    振幅: mean={mean(amps):.2f}%  median={median(amps):.2f}%")
        print(f"    净盈亏: 总和={total_pnl:+.2f}  均值={avg_pnl:+.2f}  中位={med_pnl:+.2f}")
        print(f"    胜率: 均值={avg_wr*100:.1f}%")
        print(f"    盈利股票: {profitable}/{len(group)} ({profitable/len(group)*100:.1f}%)")
        return {
            "n": len(group),
            "amplitude_mean": round(mean(amps), 2),
            "amplitude_median": round(median(amps), 2),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "median_pnl": round(med_pnl, 2),
            "avg_win_rate": round(avg_wr, 4),
            "profitable_count": profitable,
            "profitable_ratio": round(profitable / len(group), 4),
        }

    high_stats = group_summary(high_amp, "高振幅组")
    low_stats = group_summary(low_amp, "低振幅组")

    # ── 验证2: 样本2独立做 Mann-Whitney U + Cliff's delta ──
    print("\n" + "=" * 78)
    print("验证2: 样本2独立统计检验（盈利 vs 亏损的振幅差异）")
    print("=" * 78)

    profitable2 = [s["amplitude"] for s in stocks if s["is_profitable"]]
    losing2 = [s["amplitude"] for s in stocks if not s["is_profitable"]]

    print(f"\n样本2盈利组: {len(profitable2)} 只")
    print(f"样本2亏损组: {len(losing2)} 只")

    if profitable2 and losing2:
        print(f"  盈利组振幅: mean={mean(profitable2):.2f}%  median={median(profitable2):.2f}%")
        print(f"  亏损组振幅: mean={mean(losing2):.2f}%  median={median(losing2):.2f}%")

        U, p = mann_whitney_u(profitable2, losing2)
        delta = cliffs_delta(profitable2, losing2)
        auc = auc_from_u(profitable2, losing2, U)

        print(f"\n  Mann-Whitney U 检验:")
        print(f"    U = {U:.1f}  p = {p:.6f}  {'(显著)' if p < 0.05 else '(不显著)'}")
        print(f"  效应量:")
        print(f"    Cliff's delta = {delta:+.4f}  → {interpret_cliffs_delta(delta)}")
        print(f"    AUC           = {auc:.4f}")

        # 与样本1对比
        print(f"\n  与样本1对比:")
        print(f"    {'指标':<16} {'样本1':>12} {'样本2':>12} {'方向一致':>10}")
        print(f"    {'-'*56}")
        print(f"    {'p-value':<16} {step1['amplitude_mean']['p_value']:>12.6f} {p:>12.6f} {'✓' if (step1['amplitude_mean']['p_value'] < 0.05) == (p < 0.05) else '✗':>10}")
        print(f"    {'Cliff delta':<16} {step1['amplitude_mean']['cliffs_delta']:>+12.4f} {delta:>+12.4f} {'✓' if (step1['amplitude_mean']['cliffs_delta'] > 0) == (delta > 0) else '✗':>10}")
        print(f"    {'AUC':<16} {step1['amplitude_mean']['auc']:>12.4f} {auc:>12.4f} {'✓' if (step1['amplitude_mean']['auc'] > 0.6) == (auc > 0.6) else '✗':>10}")

        # ── 综合判断 ──
        print("\n" + "=" * 78)
        print("综合判断")
        print("=" * 78)
        criteria = [
            ("p < 0.05", p < 0.05),
            ("|delta| > 0.33 (中效应)", abs(delta) > 0.33),
            ("AUC > 0.65", auc > 0.65),
            ("方向与样本1一致", delta > 0),
            ("高振幅组盈利占比 > 低振幅组",
             high_stats.get("profitable_ratio", 0) > low_stats.get("profitable_ratio", 0)),
            ("高振幅组平均净盈亏 > 低振幅组",
             high_stats.get("avg_pnl", 0) > low_stats.get("avg_pnl", 0)),
        ]
        for label, passed in criteria:
            print(f"  [{'✓' if passed else '✗'}] {label}")
        passed_count = sum(1 for _, p in criteria if p)
        print(f"\n  通过 {passed_count}/{len(criteria)} 项")
        if passed_count >= 5:
            print("  → 样本外验证通过，可进入步骤3设计筛选规则")
        else:
            print("  → 样本外验证未充分通过，需进一步评估")

        output = {
            "sample2_info": {
                "n_stocks": len(stocks),
                "n_profitable": len(profitable2),
                "n_losing": len(losing2),
                "start": START_DATE,
                "end": END_DATE,
            },
            "step1_threshold": amp_threshold,
            "high_amplitude_group": high_stats,
            "low_amplitude_group": low_stats,
            "sample2_stat_test": {
                "mann_whitney_u": U,
                "p_value": p,
                "significant": p < 0.05,
                "cliffs_delta": delta,
                "auc": auc,
            },
            "comparison_with_sample1": {
                "p_value_sample1": step1["amplitude_mean"]["p_value"],
                "p_value_sample2": p,
                "delta_sample1": step1["amplitude_mean"]["cliffs_delta"],
                "delta_sample2": delta,
                "auc_sample1": step1["amplitude_mean"]["auc"],
                "auc_sample2": auc,
            },
            "criteria_passed": passed_count,
            "criteria_total": len(criteria),
        }
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n结构化结果 -> {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
