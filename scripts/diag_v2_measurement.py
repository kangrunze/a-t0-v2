#!/usr/bin/env python3
"""
V2 Measurement 完整诊断脚本
============================
对 A/B 测试结果计算完整 V2 测量指标，生成 JSON + CSV + HTML 报告。

用法:
    python scripts/diag_v2_measurement.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.measurement.v2_metrics import compute_v2_metrics_batch
from at0.measurement.v2_reporter import generate_v2_report

REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
DATA_DIR = Path(r"D:\project\data\zz500_5min")
START = "2026-04-01"
END = "2026-07-22"

# 三组 A/B 测试 tag
GROUPS = [
    ("A_tf_baseline", "v3_b4_5_a_tf_baseline"),
    ("B5_optimized", "v3_b5_optimized"),
]


def main() -> int:
    results = {}
    for label, tag in GROUPS:
        print(f"\n{'=' * 70}")
        print(f"  {label} (tag={tag})")
        print(f"{'=' * 70}")
        result = compute_v2_metrics_batch(REPORT_DIR, tag, START, END, DATA_DIR)
        if not result:
            print(f"  [跳过] 无数据")
            continue
        results[label] = result

        # 生成完整报告（JSON + CSV + HTML）
        paths = generate_v2_report(result, REPORT_DIR, prefix="v2_report")
        print(f"  JSON -> {paths['json']}")
        print(f"  CSV  -> {paths['csv']}")
        print(f"  HTML -> {paths['html']}")

    # 汇总对比表
    print(f"\n{'=' * 70}")
    print("  V2 Measurement 基线对比")
    print(f"{'=' * 70}")

    if not results:
        print("  无数据")
        return 1

    groups = list(results.values())
    n_groups = len(groups)
    header_parts = [f"{'指标':<22}"]
    for label, _ in GROUPS:
        if label in results:
            header_parts.append(f"{label:>18}")
    header = " | ".join(header_parts)
    print(header)
    print("-" * len(header))

    base = results.get(GROUPS[0][0], {}).get("overall", {})

    rows = [
        ("配对交易数", "total_pairs", "int"),
        ("平均 CE", "avg_ce", "pct"),
        ("平均 Entry Delay (K)", "avg_entry_delay_bars", "num"),
        ("平均 Exit Delay (K)", "avg_exit_delay_bars", "num"),
        ("平均 Exec Gain (%)", "avg_execution_gain_pct", "signed"),
        ("平均 Remaining Move (%)", "avg_remaining_move_pct", "num"),
        ("平均 Wave Number", "avg_wave_number", "num"),
        ("平均 Wave Capture", "avg_wave_capture_pct", "pct"),
        ("Opportunity Lost (次)", "total_opportunity_lost", "int"),
        ("OL 平均涨幅 (%)", "avg_opportunity_lost_surge_pct", "num"),
    ]

    for label, key, fmt in rows:
        row_parts = [f"{label:<22}"]
        for g_label, _ in GROUPS:
            if g_label not in results:
                continue
            v = results[g_label].get("overall", {}).get(key, 0)
            if fmt == "pct":
                row_parts.append(f"{v * 100:>17.2f}%")
            elif fmt == "signed":
                row_parts.append(f"{v:>+16.2f}%")
            elif fmt == "int":
                row_parts.append(f"{v:>18.0f}")
            else:
                row_parts.append(f"{v:>18.2f}")
        print(" | ".join(row_parts))

    # 保存汇总
    summary_path = REPORT_DIR / "v2_metrics_baseline_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "start": START,
            "end": END,
            "groups": {label: r.get("overall", {}) for label, r in results.items()},
        }, f, ensure_ascii=False, indent=2)
    print(f"\n汇总 -> {summary_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
