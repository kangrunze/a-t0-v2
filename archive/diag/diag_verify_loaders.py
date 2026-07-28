#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证 loaders.py 的 find_open_leg 与 diag_fixed_stop_entry_quality.py 原逻辑一致。

方法：在同一批 report.json 上，分别用原脚本 main() 里的 inline 反推逻辑
和新 loaders.find_open_leg() 反推开仓腿，逐笔比较结果是否完全一致。
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"d:\project\a-t0-v2")
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.measurement.loaders import (
    find_reports,
    load_report,
    collect_open_legs,
    find_open_leg,
    find_open_leg_in_day,
    find_open_leg_cross_day,
)

REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
TAG = "g2_sl0004"
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"


# ═══════════════════════════════════════════════════════════════
# 原脚本 inline 逻辑（直接从 diag_fixed_stop_entry_quality.py main() 复制）
# ═══════════════════════════════════════════════════════════════

def original_find_open_leg(report, date_str, day_trades, all_open_legs, risk_event):
    """
    原 diag_fixed_stop_entry_quality.py main() 里的 inline 反推逻辑。
    先在当日 day_trades 里找，没找到再在 all_open_legs 里跨日找。
    """
    open_leg = None
    open_dir = risk_event.get("direction")
    fill_price = risk_event.get("fill_price")
    ev_time = risk_event.get("time", "")

    # 先在当日找
    for t in day_trades:
        if (t.get("direction") == open_dir
            and t.get("fill_price") == fill_price
            and not t.get("paired", False)
            and t.get("status") == "open"
            and t.get("time", "") <= ev_time):
            open_leg = t
            break

    # 当日没找到，跨日找
    if open_leg is None:
        for t in all_open_legs:
            if (t.get("direction") == open_dir
                and t.get("fill_price") == fill_price
                and t.get("time", "") <= ev_time):
                open_leg = t
                break

    return open_leg


def main():
    print("=" * 70)
    print("loaders.py find_open_leg 验证")
    print(f"数据源: tag={TAG}, {START_DATE}~{END_DATE}")
    print("=" * 70)

    reports = find_reports(REPORT_DIR, TAG, START_DATE, END_DATE)
    print(f"找到 {len(reports)} 份 report.json")

    # 验证全部 9 份
    print(f"验证全部 {len(reports)} 份")

    total_events = 0
    matched_count = 0
    unmatched_count = 0
    mismatch_count = 0
    both_none_count = 0

    for rp in reports:
        code, report = load_report(rp)
        if report.get("total_trades", 0) == 0:
            continue

        # 构建 all_open_legs（与原脚本一致）
        all_open_legs = []
        for dr in report.get("daily_results", []):
            for t in dr.get("trades", []):
                if not t.get("paired", False) and t.get("status") == "open":
                    all_open_legs.append(t)

        # 遍历 risk_events
        for dr in report.get("daily_results", []):
            date_str = dr["date"]
            day_trades = dr.get("trades", [])
            day_risk_events = dr.get("risk_events", [])

            for ev in day_risk_events:
                if ev.get("type") != "stopped":
                    continue

                total_events += 1

                # 原逻辑
                orig_leg = original_find_open_leg(
                    report, date_str, day_trades, all_open_legs, ev
                )

                # 新 loader
                new_leg = find_open_leg(
                    report, ev, date_str=date_str, all_open_legs=all_open_legs
                )

                # 比较
                if orig_leg is None and new_leg is None:
                    both_none_count += 1
                elif orig_leg is None and new_leg is not None:
                    mismatch_count += 1
                    print(f"  [MISMATCH] {code} {date_str} ev_time={ev.get('time')}")
                    print(f"    原逻辑: None  新loader: time={new_leg.get('time')}")
                elif orig_leg is not None and new_leg is None:
                    mismatch_count += 1
                    print(f"  [MISMATCH] {code} {date_str} ev_time={ev.get('time')}")
                    print(f"    原逻辑: time={orig_leg.get('time')}  新loader: None")
                else:
                    # 两者都非 None，比较 identity（用 time + fill_price）
                    if (orig_leg.get("time") == new_leg.get("time")
                        and orig_leg.get("fill_price") == new_leg.get("fill_price")):
                        matched_count += 1
                    else:
                        mismatch_count += 1
                        print(f"  [MISMATCH] {code} {date_str} ev_time={ev.get('time')}")
                        print(f"    原逻辑: time={orig_leg.get('time')} fill={orig_leg.get('fill_price')}")
                        print(f"    新loader: time={new_leg.get('time')} fill={new_leg.get('fill_price')}")

                if orig_leg is None:
                    unmatched_count += 1

    print(f"\n{'=' * 70}")
    print(f"验证结果:")
    print(f"  总 stopped 事件数: {total_events}")
    print(f"  两者都匹配到同一开仓腿: {matched_count}")
    print(f"  两者都未匹配到: {both_none_count}")
    print(f"  原逻辑未匹配到(总计): {unmatched_count}")
    print(f"  不一致数: {mismatch_count}")
    print(f"{'=' * 70}")

    if mismatch_count == 0:
        print("✓ 验证通过：新 loader 与原脚本反推逻辑结果完全一致")
    else:
        print("✗ 验证失败：存在不一致，需排查")
        sys.exit(1)


if __name__ == "__main__":
    main()
