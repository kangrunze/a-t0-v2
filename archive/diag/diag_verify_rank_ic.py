#!/usr/bin/env python3
"""
验证 rank_ic.py：从真实 report.json 抽取 (vwap_dev, realized_pnl) 配对，
计算逐笔 RankIC，验证新模块在真实数据上可用。

复用 Stage H 步骤1/2 的 loaders.py + rules_parser.py，形成三模块协同验证。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.measurement.loaders import (
    find_reports, load_report, find_open_leg, collect_open_legs,
)
from at0.measurement.rules_parser import parse_entry_factors
from at0.measurement.rank_ic import compute_rank_ic

REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
TAG = "g2_sl0003"
START = "2023-07-25"
END = "2026-07-22"


def collect_factor_pnl_pairs(reports, factor_key: str):
    """从 stopped 事件反推开仓腿，收集 (factor_value, realized_pnl) 配对。"""
    pairs = []
    for rp in reports:
        _, report = load_report(rp)
        all_open_legs = collect_open_legs(report)
        for dr in report.get("daily_results", []):
            date_str = dr.get("date")
            for ev in dr.get("risk_events", []):
                if ev.get("type") != "stopped":
                    continue
                realized = ev.get("realized_pnl")
                if realized is None:
                    continue
                open_leg = find_open_leg(
                    report, ev, date_str=date_str, all_open_legs=all_open_legs,
                )
                if open_leg is None:
                    continue
                factors = parse_entry_factors(open_leg.get("rules_fired", []))
                val = factors.get(factor_key)
                if val is None:
                    continue
                pairs.append((val, float(realized)))
    return pairs


def main():
    reports = find_reports(REPORT_DIR, TAG, START, END)
    if not reports:
        print(f"[ERROR] 未找到报告: {REPORT_DIR} tag={TAG}", file=sys.stderr)
        return 2

    print(f"找到 {len(reports)} 份 report.json (tag={TAG})")

    # vwap_dev
    pairs = collect_factor_pnl_pairs(reports, "vwap_dev")
    print(f"\nvwap_dev 配对样本数: {len(pairs)}")
    if pairs:
        r = compute_rank_ic([p[0] for p in pairs], [p[1] for p in pairs])
        if r:
            print(f"  rank_ic  = {r.rank_ic:+.4f}")
            print(f"  p_value  = {r.p_value:.4f}")
            print(f"  n        = {r.n}")
            print(f"  显著性   = {'显著(p<0.05)' if r.p_value < 0.05 else '不显著'}")
            print(f"  [对照] project_memory: vwap_dev ICIR=-0.994（横截面，无显著预测力）")

    # adx
    pairs_adx = collect_factor_pnl_pairs(reports, "adx")
    print(f"\nadx 配对样本数: {len(pairs_adx)}")
    if pairs_adx:
        r_adx = compute_rank_ic([p[0] for p in pairs_adx], [p[1] for p in pairs_adx])
        if r_adx:
            print(f"  rank_ic  = {r_adx.rank_ic:+.4f}")
            print(f"  p_value  = {r_adx.p_value:.4f}")
            print(f"  n        = {r_adx.n}")
            print(f"  [对照] project_memory: adx ICIR=-2.245（横截面，显著负预测力）")

    # vol_ratio
    pairs_vol = collect_factor_pnl_pairs(reports, "vol_ratio")
    print(f"\nvol_ratio 配对样本数: {len(pairs_vol)}")
    if pairs_vol:
        r_vol = compute_rank_ic([p[0] for p in pairs_vol], [p[1] for p in pairs_vol])
        if r_vol:
            print(f"  rank_ic  = {r_vol.rank_ic:+.4f}")
            print(f"  p_value  = {r_vol.p_value:.4f}")
            print(f"  n        = {r_vol.n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
