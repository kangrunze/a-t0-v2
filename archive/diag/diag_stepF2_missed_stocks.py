"""
步骤F2 — 刻画18只误伤盈利股的特征
=================================
对 100 股 × 3 年 A/B 回测中被振幅筛选筛掉但实际盈利的 18 只股票（误伤组），
对比以下两组：
  - 通过组（9 只，60日振幅 >= 4.94%）
  - 亏损组（73 只，被筛掉且 net_pnl <= 0）

判断误伤组更像哪组，还是自成一类。

⚠️ 偏乐观风险提示：本分析在训练集上完成（与 Youden 阈值训练样本重叠），
   结论仅供诊断参考，不能作为样本外证据。样本外证据见 F5。

数据源：
  - amplitude_pool_detail.json: 60日振幅（prev_close 口径，与筛选一致）
  - batch_summary_sample100_3y.json: 3年期 net_pnl
  - stepD_attribution.json: 特征数据（ADX/成交量等，3个月期 2026-04-27~2026-07-23）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, median, stdev

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "diag"))

from diag_stepD1_stat_test import mann_whitney_u, cliffs_delta, auc_from_u, interpret_cliffs_delta

POOL_DETAIL = PROJECT_ROOT / "outputs" / "amplitude_pool" / "amplitude_pool_detail.json"
BASELINE_SUMMARY = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_sample100_3y.json"
ATTRIBUTION = PROJECT_ROOT / "outputs" / "backtest" / "stepD_attribution.json"
OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtest" / "stepF2_missed_stocks.json"


def get_sector(code: str) -> str:
    """从股票代码推断板块。"""
    if code.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    elif code.startswith(("000", "001", "002", "003")):
        return "深市主板"
    elif code.startswith(("300", "301")):
        return "创业板"
    elif code.startswith(("688", "689")):
        return "科创板"
    return "其他"


def load_data():
    """加载三个数据源并合并。"""
    with open(POOL_DETAIL, "r", encoding="utf-8") as f:
        pool = json.load(f)
    with open(BASELINE_SUMMARY, "r", encoding="utf-8") as f:
        baseline = json.load(f)
    with open(ATTRIBUTION, "r", encoding="utf-8") as f:
        attrib = json.load(f)

    # 振幅字典（prev_close 口径）
    amp_map = {}
    for r in pool["passed"] + pool["filtered"]:
        amp_map[r["code"]] = r["amp"]

    # net_pnl 字典
    pnl_map = {}
    for s in baseline["per_stock"]:
        pnl_map[s["code"]] = s.get("net_pnl_with_unrealized", s["net_pnl"])

    # 特征字典（stepD_attribution: profitable_per_stock + losing_per_stock）
    feat_map = {}
    for s in attrib.get("profitable_per_stock", []) + attrib.get("losing_per_stock", []):
        feat_map[s["code"]] = s

    # 分组
    passed_codes = [r["code"] for r in pool["passed"]]
    filtered_codes = [r["code"] for r in pool["filtered"]]

    passed_group = []
    missed_group = []   # 误伤：被筛掉但盈利
    losing_group = []   # 被筛掉且亏损

    for code in passed_codes:
        pnl = pnl_map.get(code, 0)
        amp = amp_map.get(code)
        feat = feat_map.get(code, {})
        passed_group.append(_build_record(code, pnl, amp, feat))

    for code in filtered_codes:
        pnl = pnl_map.get(code, 0)
        amp = amp_map.get(code)
        feat = feat_map.get(code, {})
        rec = _build_record(code, pnl, amp, feat)
        if pnl > 0:
            missed_group.append(rec)
        else:
            losing_group.append(rec)

    return passed_group, missed_group, losing_group


def _build_record(code, pnl, amp, feat):
    return {
        "code": code,
        "sector": get_sector(code),
        "net_pnl": pnl,
        "amp_60d": amp * 100 if amp is not None else None,  # 转为 %
        "adx_mean": feat.get("adx_mean"),
        "adx_trend_ratio": feat.get("adx_trend_ratio"),
        "volume_mean": feat.get("volume_mean"),
        "amplitude_3m": feat.get("amplitude_mean"),  # 3个月期振幅（open口径）
        "win_rate": feat.get("win_rate"),
        "paired_trades": feat.get("paired_trades"),
    }


def group_stats(group: list[dict], name: str) -> dict:
    """计算组内统计。"""
    if not group:
        return {"name": name, "n": 0}

    def safe_vals(key):
        return [r[key] for r in group if r.get(key) is not None]

    def safe_stats(vals):
        if not vals:
            return None
        return {
            "mean": round(mean(vals), 4),
            "median": round(median(vals), 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "std": round(stdev(vals), 4) if len(vals) > 1 else 0,
        }

    # 板块分布
    sector_counts = {}
    for r in group:
        s = r["sector"]
        sector_counts[s] = sector_counts.get(s, 0) + 1

    return {
        "name": name,
        "n": len(group),
        "net_pnl": safe_stats(safe_vals("net_pnl")),
        "amp_60d": safe_stats(safe_vals("amp_60d")),
        "adx_mean": safe_stats(safe_vals("adx_mean")),
        "adx_trend_ratio": safe_stats(safe_vals("adx_trend_ratio")),
        "volume_mean": safe_stats(safe_vals("volume_mean")),
        "amplitude_3m": safe_stats(safe_vals("amplitude_3m")),
        "sector_dist": sector_counts,
    }


def print_comparison(passed, missed, losing):
    """打印三组对比表。"""
    print("\n" + "=" * 90)
    print("F2: 三组特征对比（通过组 / 误伤组 / 亏损组）")
    print("⚠️ 训练集分析，结论有偏乐观风险，样本外证据见 F5")
    print("=" * 90)

    # ── 数量与盈亏概览 ──
    print(f"\n{'组别':<12s} {'数量':>6s} {'净盈亏总和':>14s} {'均值':>10s} {'中位':>10s}")
    print(f"{'-'*12} {'-'*6} {'-'*14} {'-'*10} {'-'*10}")
    for g, label in [(passed, "通过组"), (missed, "误伤组"), (losing, "亏损组")]:
        pnls = [r["net_pnl"] for r in g]
        total = sum(pnls)
        avg = mean(pnls) if pnls else 0
        med = median(pnls) if pnls else 0
        print(f"{label:<12s} {len(g):>6d} {total:>+14.2f} {avg:>+10.2f} {med:>+10.2f}")

    # ── 60日振幅（筛选口径）──
    print(f"\n{'─' * 90}")
    print("维度1: 60日日均振幅 %（筛选口径, prev_close）")
    print(f"{'─' * 90}")
    print(f"{'组别':<12s} {'mean':>8s} {'median':>8s} {'min':>8s} {'max':>8s} {'std':>8s}")
    print(f"{'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for g, label in [(passed, "通过组"), (missed, "误伤组"), (losing, "亏损组")]:
        vals = [r["amp_60d"] for r in g if r["amp_60d"] is not None]
        if vals:
            print(f"{label:<12s} {mean(vals):>8.2f} {median(vals):>8.2f} {min(vals):>8.2f} {max(vals):>8.2f} {stdev(vals) if len(vals)>1 else 0:>8.2f}")
        else:
            print(f"{label:<12s}  无数据")

    # ── ADX均值 ──
    print(f"\n{'─' * 90}")
    print("维度2: ADX均值（3个月期, 2026-04-27~2026-07-23）")
    print(f"{'─' * 90}")
    print(f"{'组别':<12s} {'mean':>8s} {'median':>8s} {'min':>8s} {'max':>8s} {'std':>8s}")
    print(f"{'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for g, label in [(passed, "通过组"), (missed, "误伤组"), (losing, "亏损组")]:
        vals = [r["adx_mean"] for r in g if r["adx_mean"] is not None]
        if vals:
            print(f"{label:<12s} {mean(vals):>8.2f} {median(vals):>8.2f} {min(vals):>8.2f} {max(vals):>8.2f} {stdev(vals) if len(vals)>1 else 0:>8.2f}")
        else:
            print(f"{label:<12s}  无数据")

    # ── 趋势日占比（ADX>25）──
    print(f"\n{'─' * 90}")
    print("维度3: 趋势日占比 (ADX>25 天数占比)")
    print(f"{'─' * 90}")
    print(f"{'组别':<12s} {'mean':>8s} {'median':>8s} {'min':>8s} {'max':>8s}")
    print(f"{'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for g, label in [(passed, "通过组"), (missed, "误伤组"), (losing, "亏损组")]:
        vals = [r["adx_trend_ratio"] for r in g if r["adx_trend_ratio"] is not None]
        if vals:
            print(f"{label:<12s} {mean(vals):>8.4f} {median(vals):>8.4f} {min(vals):>8.4f} {max(vals):>8.4f}")
        else:
            print(f"{label:<12s}  无数据")

    # ── 日均成交量 ──
    print(f"\n{'─' * 90}")
    print("维度4: 日均成交量（3个月期均值, 股）")
    print(f"{'─' * 90}")
    print(f"{'组别':<12s} {'mean':>16s} {'median':>16s} {'min':>16s} {'max':>16s}")
    print(f"{'-'*12} {'-'*16} {'-'*16} {'-'*16} {'-'*16}")
    for g, label in [(passed, "通过组"), (missed, "误伤组"), (losing, "亏损组")]:
        vals = [r["volume_mean"] for r in g if r["volume_mean"] is not None]
        if vals:
            print(f"{label:<12s} {mean(vals):>16.0f} {median(vals):>16.0f} {min(vals):>16.0f} {max(vals):>16.0f}")
        else:
            print(f"{label:<12s}  无数据")

    # ── 板块分布 ──
    print(f"\n{'─' * 90}")
    print("维度5: 板块分布")
    print(f"{'─' * 90}")
    all_sectors = set()
    for g in [passed, missed, losing]:
        for r in g:
            all_sectors.add(r["sector"])
    sectors = sorted(all_sectors)
    print(f"{'板块':<12s} {'通过组':>8s} {'误伤组':>8s} {'亏损组':>8s}")
    print(f"{'-'*12} {'-'*8} {'-'*8} {'-'*8}")
    for s in sectors:
        p = sum(1 for r in passed if r["sector"] == s)
        m = sum(1 for r in missed if r["sector"] == s)
        l = sum(1 for r in losing if r["sector"] == s)
        p_pct = p / len(passed) * 100 if passed else 0
        m_pct = m / len(missed) * 100 if missed else 0
        l_pct = l / len(losing) * 100 if losing else 0
        print(f"{s:<12s} {p:>3d}({p_pct:.0f}%) {m:>3d}({m_pct:.0f}%) {l:>3d}({l_pct:.0f}%)")


def stat_test_groups(group_a, group_b, key, label_a, label_b):
    """对两组做 Mann-Whitney U + Cliff's delta。"""
    vals_a = [r[key] for r in group_a if r.get(key) is not None]
    vals_b = [r[key] for r in group_b if r.get(key) is not None]
    if len(vals_a) < 2 or len(vals_b) < 2:
        return None
    U, p = mann_whitney_u(vals_a, vals_b)
    delta = cliffs_delta(vals_a, vals_b)
    auc = auc_from_u(vals_a, vals_b, U)
    print(f"\n  {label_a} vs {label_b} ({key}):")
    print(f"    {label_a}: mean={mean(vals_a):.4f}  median={median(vals_a):.4f}  n={len(vals_a)}")
    print(f"    {label_b}: mean={mean(vals_b):.4f}  median={median(vals_b):.4f}  n={len(vals_b)}")
    print(f"    U={U:.1f}  p={p:.6f}  {'(显著)' if p < 0.05 else '(不显著)'}")
    print(f"    Cliff's delta={delta:+.4f} → {interpret_cliffs_delta(delta)}")
    print(f"    AUC={auc:.4f}")
    return {"U": U, "p": p, "delta": delta, "auc": auc, "sig": p < 0.05}


def print_missed_detail(missed):
    """打印18只误伤股的明细。"""
    print(f"\n{'=' * 90}")
    print(f"误伤组 {len(missed)} 只明细（按 net_pnl 降序）")
    print(f"{'=' * 90}")
    print(f"{'代码':<8s} {'板块':<10s} {'net_pnl':>10s} {'60d振幅%':>10s} {'ADX均':>8s} {'趋势占比':>8s} {'成交量':>14s}")
    print(f"{'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*14}")
    for r in sorted(missed, key=lambda x: -x["net_pnl"]):
        amp = f"{r['amp_60d']:.2f}" if r["amp_60d"] is not None else "N/A"
        adx = f"{r['adx_mean']:.2f}" if r["adx_mean"] is not None else "N/A"
        trend = f"{r['adx_trend_ratio']:.4f}" if r["adx_trend_ratio"] is not None else "N/A"
        vol = f"{r['volume_mean']:,.0f}" if r["volume_mean"] is not None else "N/A"
        print(f"{r['code']:<8s} {r['sector']:<10s} {r['net_pnl']:>+10.2f} {amp:>10s} {adx:>8s} {trend:>8s} {vol:>14s}")


def main():
    print("=" * 90)
    print("步骤F2 — 刻画18只误伤盈利股特征")
    print("⚠️ 训练集分析（与 Youden 阈值训练样本重叠），结论有偏乐观风险")
    print("=" * 90)

    passed, missed, losing = load_data()
    print(f"\n分组结果:")
    print(f"  通过组: {len(passed)} 只（60日振幅 >= 4.94%）")
    print(f"  误伤组: {len(missed)} 只（被筛掉但 net_pnl > 0）")
    print(f"  亏损组: {len(losing)} 只（被筛掉且 net_pnl <= 0）")
    assert len(passed) + len(missed) + len(losing) == 100

    # 特征对比表
    print_comparison(passed, missed, losing)

    # 误伤股明细
    print_missed_detail(missed)

    # ── 统计检验 ──
    print(f"\n{'=' * 90}")
    print("统计检验: 误伤组 vs 亏损组 / 误伤组 vs 通过组")
    print(f"{'=' * 90}")

    tests = {}
    for key, label in [("amp_60d", "60日振幅"), ("adx_mean", "ADX均值"),
                        ("adx_trend_ratio", "趋势日占比"), ("volume_mean", "日均成交量")]:
        print(f"\n{'─' * 60}")
        print(f"特征: {label}")
        print(f"{'─' * 60}")
        r1 = stat_test_groups(missed, losing, key, "误伤组", "亏损组")
        r2 = stat_test_groups(missed, passed, key, "误伤组", "通过组")
        tests[label] = {"missed_vs_losing": r1, "missed_vs_passed": r2}

    # ── 综合判断 ──
    print(f"\n{'=' * 90}")
    print("综合判断")
    print(f"{'=' * 90}")

    # 判断逻辑：看误伤组在哪些维度上更接近通过组 vs 亏损组
    amp_test = tests.get("60日振幅", {}).get("missed_vs_losing")
    adx_test = tests.get("ADX均值", {}).get("missed_vs_losing")

    print("\n判断依据:")
    print("  1. 若误伤组 60日振幅显著高于亏损组 → 阈值切在了不合理位置（应放行）")
    print("  2. 若误伤组 ADX/趋势占比显著高于亏损组 → 存在振幅之外的维度")
    print("  3. 若误伤组在各维度上与亏损组无显著差异 → 运气好，不该作为可靠盈利来源")

    # 输出结构化结果
    output = {
        "warning": "训练集分析，与Youden阈值训练样本重叠，结论偏乐观",
        "groups": {
            "passed": group_stats(passed, "通过组"),
            "missed": group_stats(missed, "误伤组"),
            "losing": group_stats(losing, "亏损组"),
        },
        "missed_detail": sorted(missed, key=lambda x: -x["net_pnl"]),
        "stat_tests": tests,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结构化结果 -> {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
