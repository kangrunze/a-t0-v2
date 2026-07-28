#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证 rules_parser.py 的 parse_entry_factors / parse_exit_factors
与 diag_fixed_stop_entry_quality.py / diag_exit_trigger_attribution.py 原逻辑一致。

方法：在同一批 report.json 上，对每条 trade 的 rules_fired，
分别用原脚本的解析函数和新 rules_parser 的函数解析，逐条比较结果。
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"d:\project\a-t0-v2")
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.measurement.loaders import find_reports, load_report
from at0.measurement.rules_parser import parse_entry_factors, parse_exit_factors

REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
TAG = "g2_sl0004"
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"


# ═══════════════════════════════════════════════════════════════
# 原脚本逻辑（直接复制）
# ═══════════════════════════════════════════════════════════════

def original_parse_entry_factors(rules_fired):
    """原 diag_fixed_stop_entry_quality.py parse_entry_factors。"""
    text = " ".join(rules_fired) if rules_fired else ""
    result = {"vwap_dev": None, "adx": None, "vol_ratio": None, "trend_context": None}

    m = re.search(r"VWAP偏离 ([+-]?\d+\.\d+)%", text)
    if m:
        result["vwap_dev"] = float(m.group(1)) / 100

    m = re.search(r"ADX=(\d+\.\d+)", text)
    if m:
        result["adx"] = float(m.group(1))

    m = re.search(r"量比 (\d+\.\d+)", text)
    if m:
        result["vol_ratio"] = float(m.group(1))

    m = re.search(r"\[趋势\] (\w+)", text)
    if m:
        result["trend_context"] = m.group(1)

    return result


def original_parse_exit_factors(rules_fired):
    """原 diag_exit_trigger_attribution.py parse_vwap_adx。"""
    for msg in rules_fired:
        if "[平仓-趋势反转]" not in msg:
            continue
        m_vwap = re.search(r"vwap_dev=([+-]?\d+\.?\d*)%", msg)
        vwap_dev = float(m_vwap.group(1)) / 100 if m_vwap else None
        m_adx = re.search(r"adx=(\d+\.?\d*)", msg)
        adx = float(m_adx.group(1)) if m_adx else None
        if "上涨趋势结束" in msg:
            pairing_direction = "sell"
        elif "下跌趋势结束" in msg:
            pairing_direction = "buy"
        else:
            pairing_direction = None
        return vwap_dev, adx, pairing_direction
    return None, None, None


def main():
    print("=" * 70)
    print("rules_parser.py 验证")
    print(f"数据源: tag={TAG}, {START_DATE}~{END_DATE}")
    print("=" * 70)

    reports = find_reports(REPORT_DIR, TAG, START_DATE, END_DATE)
    print(f"找到 {len(reports)} 份 report.json")

    # ── 验证 parse_entry_factors ──
    print(f"\n{'─' * 70}")
    print("验证 parse_entry_factors（开仓腿 rules_fired）")
    print(f"{'─' * 70}")

    entry_total = 0
    entry_mismatch = 0
    entry_all_none = 0  # 边界情况：两者都全 None（条件未触发）

    for rp in reports:
        code, report = load_report(rp)
        if report.get("total_trades", 0) == 0:
            continue

        for dr in report.get("daily_results", []):
            for t in dr.get("trades", []):
                rules_fired = t.get("rules_fired", [])
                if not rules_fired:
                    continue

                entry_total += 1
                orig = original_parse_entry_factors(rules_fired)
                new = parse_entry_factors(rules_fired)

                if orig != new:
                    entry_mismatch += 1
                    print(f"  [MISMATCH] {code} time={t.get('time')}")
                    print(f"    原逻辑: {orig}")
                    print(f"    新模块: {new}")
                elif all(v is None for v in orig.values()):
                    entry_all_none += 1

    print(f"  总 rules_fired 数: {entry_total}")
    print(f"  其中全 None（条件未触发）: {entry_all_none}")
    print(f"  不一致数: {entry_mismatch}")
    if entry_mismatch == 0:
        print("  ✓ parse_entry_factors 验证通过")
    else:
        print("  ✗ parse_entry_factors 验证失败")

    # ── 验证 parse_exit_factors ──
    print(f"\n{'─' * 70}")
    print("验证 parse_exit_factors（平仓腿 [平仓-趋势反转] 消息）")
    print(f"{'─' * 70}")

    exit_total = 0
    exit_has_reverse = 0  # 含 [平仓-趋势反转] 的 rules_fired 数
    exit_mismatch = 0
    exit_all_none = 0

    for rp in reports:
        code, report = load_report(rp)
        if report.get("total_trades", 0) == 0:
            continue

        for dr in report.get("daily_results", []):
            for t in dr.get("trades", []):
                rules_fired = t.get("rules_fired", [])
                if not rules_fired:
                    continue

                exit_total += 1
                has_reverse = any("[平仓-趋势反转]" in msg for msg in rules_fired)
                if has_reverse:
                    exit_has_reverse += 1

                orig = original_parse_exit_factors(rules_fired)
                new = parse_exit_factors(rules_fired)

                if orig != new:
                    exit_mismatch += 1
                    print(f"  [MISMATCH] {code} time={t.get('time')}")
                    print(f"    原逻辑: {orig}")
                    print(f"    新模块: {new}")
                elif orig == (None, None, None):
                    exit_all_none += 1

    print(f"  总 rules_fired 数: {exit_total}")
    print(f"  其中含 [平仓-趋势反转]: {exit_has_reverse}")
    print(f"  其中返回 (None,None,None): {exit_all_none}")
    print(f"  不一致数: {exit_mismatch}")
    if exit_mismatch == 0:
        print("  ✓ parse_exit_factors 验证通过")
    else:
        print("  ✗ parse_exit_factors 验证失败")

    # ── 总结 ──
    print(f"\n{'=' * 70}")
    total_mismatch = entry_mismatch + exit_mismatch
    if total_mismatch == 0:
        print(f"✓ 全部验证通过：entry({entry_total}) + exit({exit_total}) 条 rules_fired，0 不一致")
    else:
        print(f"✗ 验证失败：共 {total_mismatch} 条不一致")
        sys.exit(1)
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
