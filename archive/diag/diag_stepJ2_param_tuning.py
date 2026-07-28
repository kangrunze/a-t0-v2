#!/usr/bin/env python3
"""
Stage J2 — 回踩入场参数调优 + A/B 回测

第一轮发现默认参数太松（min_adx=25 < baseline的35），导致交易过多、CE下降。
本轮收紧参数后重测。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock
from at0.strategy import SignalParams
from at0.risk import RiskParams
from at0.config import load_signal_params, load_risk_params, load_backtest_params, load_screener_params
from at0.cli import adapt_params_by_frequency, BACKTEST_OUTPUT_DIR
import random
# 直接内联需要的函数，避免模块导入问题
from diag_stepJ2_ab_test import (
    load_zz500_data, calc_60d_amplitude, compute_daily_hl,
    compute_capture_efficiency, stats_from_ce_list,
)


def run_backtest(code, start, end, signal_params):
    data = load_zz500_data(code, start, end)
    if not data:
        return None
    daily_bars, daily_prev_closes = data
    risk_params = load_risk_params()
    backtest_params = load_backtest_params()
    bt_params = BacktestParams(base_shares=3000, signal_params=signal_params, risk_params=risk_params)
    bt_params.stop_loss_ratio = backtest_params.stop_loss_ratio
    bt_params.trailing_ratio = backtest_params.trailing_ratio
    bt_params.trailing_activation_pct = backtest_params.trailing_activation_pct
    bt_params.max_holding_bars = backtest_params.max_holding_bars
    bt_params.cooldown_bars = backtest_params.cooldown_bars
    bt_params = adapt_params_by_frequency(bt_params, "5min", 48)
    result = backtest_multi_day(code=code, daily_bars=daily_bars, daily_prev_closes=daily_prev_closes, params=bt_params)
    daily_hl = compute_daily_hl(daily_bars)
    same_day_ce, cross_day_ce = compute_capture_efficiency(result, daily_hl)
    summary = summarize_one_stock(code, result)
    return {"code": code, "summary": summary, "same_day_ce": same_day_ce, "cross_day_ce": cross_day_ce}


def main():
    start = "2024-01-01"
    end = "2026-07-22"

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    screener_params = load_screener_params()
    min_amp = screener_params.min_amplitude_long
    filtered_codes = [c for c in all_codes if (lambda a: a is not None and a >= min_amp)(calc_60d_amplitude(c, start))]
    random.seed(42)
    codes = random.sample(filtered_codes, min(13, len(filtered_codes)))
    print(f"[INFO] {len(codes)} 只股票, {start} ~ {end}\n")

    baseline_params = load_signal_params()

    # 多组参数测试
    param_sets = [
        ("tight_v1", {
            "retracement_entry_enabled": True,
            "retracement_min_adx": 35.0,      # 与baseline的tf_adx_threshold一致
            "retracement_vwap_band": 0.003,    # 收紧到0.3%
            "retracement_lookback": 12,        # 回看12根(1小时)
            "retracement_min_surge": 0.01,     # 要求1%冲高
            "retracement_kdj_max": 50.0,       # K<50（更严格的回踩）
        }),
        ("tight_v2", {
            "retracement_entry_enabled": True,
            "retracement_min_adx": 35.0,
            "retracement_vwap_band": 0.002,    # 更紧0.2%
            "retracement_lookback": 12,
            "retracement_min_surge": 0.015,    # 1.5%冲高
            "retracement_kdj_max": 45.0,       # K<45
        }),
        ("tight_v3", {
            "retracement_entry_enabled": True,
            "retracement_min_adx": 30.0,       # 略低于baseline
            "retracement_vwap_band": 0.003,
            "retracement_lookback": 10,
            "retracement_min_surge": 0.008,    # 0.8%冲高
            "retracement_kdj_max": 55.0,
        }),
    ]

    # 先跑baseline
    print("Running baseline...")
    baseline_results = {}
    for i, code in enumerate(codes):
        bl = run_backtest(code, start, end, baseline_params)
        if bl:
            baseline_results[code] = bl
        if (i+1) % 5 == 0:
            print(f"  [{i+1}/{len(codes)}]")

    bl_all_ce = []
    bl_pnl = 0
    bl_pairs = 0
    for r in baseline_results.values():
        bl_all_ce.extend(r["same_day_ce"])
        bl_pnl += r["summary"].get("net_pnl_with_unrealized", 0)
        bl_pairs += r["summary"].get("paired_trades", 0)
    bl_stats = stats_from_ce_list(bl_all_ce)

    print(f"\nBaseline: 配对={bl_pairs}, CE均值={bl_stats['mean']*100:.2f}%, P50={bl_stats['p50']*100:.2f}%, 盈亏={bl_pnl:+.0f}")

    # 跑各组参数
    results = {"baseline": {"ce": bl_stats, "pnl": bl_pnl, "pairs": bl_pairs}}

    for name, overrides in param_sets:
        print(f"\nRunning {name}...")
        rt_params = load_signal_params()
        for k, v in overrides.items():
            setattr(rt_params, k, v)

        rt_all_ce = []
        rt_pnl = 0
        rt_pairs = 0
        rt_wins = 0

        for i, code in enumerate(codes):
            rt = run_backtest(code, start, end, rt_params)
            if rt:
                rt_all_ce.extend(rt["same_day_ce"])
                rt_pnl += rt["summary"].get("net_pnl_with_unrealized", 0)
                rt_pairs += rt["summary"].get("paired_trades", 0)
                if rt["summary"].get("net_pnl_with_unrealized", 0) > 0:
                    rt_wins += 1
            if (i+1) % 5 == 0:
                print(f"  [{i+1}/{len(codes)}]")

        rt_stats = stats_from_ce_list(rt_all_ce)
        results[name] = {"ce": rt_stats, "pnl": rt_pnl, "pairs": rt_pairs, "wins": rt_wins}
        print(f"{name}: 配对={rt_pairs}, CE均值={rt_stats['mean']*100:.2f}%, P50={rt_stats['p50']*100:.2f}%, 盈亏={rt_pnl:+.0f}, 盈利={rt_wins}/{len(codes)}")

    # 汇总对比
    print(f"\n{'='*80}")
    print(f"参数调优汇总")
    print(f"{'='*80}")
    print(f"{'配置':<15} {'配对数':>8} {'CE均值':>10} {'CE_P50':>10} {'CE_P75':>10} {'总盈亏':>12} {'盈利数':>8}")
    print(f"{'-'*80}")

    for name, r in results.items():
        ce = r["ce"]
        print(f"{name:<15} {r['pairs']:>8} {ce['mean']*100:>9.2f}% {ce['p50']*100:>9.2f}% {ce['p75']*100:>9.2f}% {r['pnl']:>+12.0f} {r.get('wins', '-'):>8}")

    bl = results["baseline"]
    print(f"\n{'vs Baseline':<15}")
    for name in ["tight_v1", "tight_v2", "tight_v3"]:
        r = results[name]
        d_ce = (r["ce"]["mean"] - bl["ce"]["mean"]) * 100
        d_p50 = (r["ce"]["p50"] - bl["ce"]["p50"]) * 100
        d_pnl = r["pnl"] - bl["pnl"]
        d_pairs = r["pairs"] - bl["pairs"]
        print(f"  {name}: CE均值{d_ce:+.2f}%, P50{d_p50:+.2f}%, 盈亏{d_pnl:+.0f}, 配对{d_pairs:+d}")


if __name__ == "__main__":
    main()
