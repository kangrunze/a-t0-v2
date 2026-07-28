#!/usr/bin/env python3
"""
Stage J0 — Regime识别诊断

目标：复现603119 2026-04-21当天的逐bar中间值（MACD/ADX/DI/VWAP偏离等），
找出trend_up/extreme为什么没被触发。

同时扩展到全样本统计trend_up/trend_down/extreme的实际触发条件分布。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.features import compute_reference_snapshot, detect_market_regime
from at0.strategy import SignalParams


def load_day_data(code: str, date: str):
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    daily_bars_raw = data.get("daily_bars", {})
    sorted_dates = sorted(daily_bars_raw.keys())

    if date not in daily_bars_raw:
        return None, None, None

    bars = daily_bars_raw[date]
    idx = sorted_dates.index(date)

    prev_close = None
    if idx > 0:
        prev_date = sorted_dates[idx - 1]
        prev_bars = daily_bars_raw.get(prev_date, [])
        if prev_bars:
            prev_close = prev_bars[-1]["close"]

    if prev_close is None:
        prev_close = bars[0]["open"]

    return bars, prev_close, sorted_dates


def diagnose_single_day(code: str, date: str):
    """诊断单只股票单日的逐bar regime状态。"""
    bars, prev_close, _ = load_day_data(code, date)
    if bars is None:
        print(f"[ERROR] 未找到 {code} {date} 的数据")
        return

    print(f"\n{'='*80}")
    print(f"Regime诊断: {code} {date}")
    print(f"{'='*80}")
    print(f"K线数量: {len(bars)}")
    print(f"前收盘: {prev_close:.4f}")

    day_high = max(b["high"] for b in bars)
    day_low = min(b["low"] for b in bars)
    print(f"当日最高: {day_high:.4f}")
    print(f"当日最低: {day_low:.4f}")
    print(f"当日振幅: {(day_high - day_low)/prev_close*100:.2f}%")

    params = SignalParams()
    print(f"\n参数:")
    print(f"  adx_trend_threshold: {params.adx_trend_threshold}")
    print(f"  adx_extreme_threshold: {params.adx_extreme_threshold}")
    print(f"  extreme_vwap_dev_multiplier: {params.extreme_vwap_dev_multiplier}")

    print(f"\n{'='*80}")
    print(f"逐Bar Regime状态 (5min数据，min_bars=12)")
    print(f"{'='*80}")
    print(f"{'时间':<18} {'收盘':>8} {'VWAP偏离':>10} {'ADX':>8} {'+DI':>8} {'-DI':>8} "
          f"{'MACD-DIF':>10} {'MACD-DEA':>10} {'ATR':>8} {'Regime':<12}")
    print(f"{'-'*80}")

    regime_counts = {}
    max_adx = 0
    max_vwap_dev = 0

    for i in range(len(bars)):
        current_bars = bars[:i+1]
        snap = compute_reference_snapshot(current_bars, prev_close)

        regime = detect_market_regime(
            snap,
            adx_trend_threshold=params.adx_trend_threshold,
            adx_extreme_threshold=params.adx_extreme_threshold,
            extreme_vwap_dev_multiplier=params.extreme_vwap_dev_multiplier,
            min_bars_for_trend=12,  # 5min
        )

        regime_counts[regime] = regime_counts.get(regime, 0) + 1

        adx = snap.get("adx", 0) or 0
        vwap_dev = snap.get("vwap_dev", 0) or 0
        if adx > max_adx:
            max_adx = adx
        if abs(vwap_dev) > max_vwap_dev:
            max_vwap_dev = abs(vwap_dev)

        if i < 12 or i % 4 == 0 or regime != "range":
            time_str = current_bars[-1].get("time", "")[-8:]
            close = current_bars[-1].get("close", 0)
            pdi = snap.get("pdi", 0) or 0
            mdi = snap.get("mdi", 0) or 0
            macd_dif = snap.get("macd_dif", 0) or 0
            macd_dea = snap.get("macd_dea", 0) or 0
            atr = snap.get("atr", 0) or 0

            print(f"{time_str:<18} {close:>8.4f} {vwap_dev*100:>9.3f}% {adx:>8.2f} "
                  f"{pdi:>8.2f} {mdi:>8.2f} "
                  f"{macd_dif:>10.6f} {macd_dea:>10.6f} {atr:>8.4f} {regime:<12}")

    print(f"\n{'='*80}")
    print(f"统计汇总:")
    print(f"{'='*80}")
    for r, c in sorted(regime_counts.items()):
        print(f"  {r}: {c} bars ({c/len(bars)*100:.1f}%)")
    print(f"\n  最大ADX: {max_adx:.2f}")
    print(f"  最大|VWAP偏离|: {max_vwap_dev*100:.3f}%")

    if max_adx < params.adx_trend_threshold:
        print(f"\n  → 原因: ADX最大值({max_adx:.2f}) < 趋势阈值({params.adx_trend_threshold})")
        print(f"    全天都被判定为range，没有任何趋势状态被触发")
    else:
        print(f"\n  → ADX曾超过趋势阈值，检查MACD和DI方向...")

    return regime_counts, max_adx, max_vwap_dev


def diagnose_full_sample(sample_size: int = 50, seed: int = 42):
    """全样本统计trend_up/trend_down/extreme的触发分布。"""
    import random

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    random.seed(seed)
    codes = random.sample(all_codes, min(sample_size, len(all_codes)))

    print(f"\n{'#'*80}")
    print(f"全样本Regime分布统计 (样本数: {len(codes)}, seed={seed})")
    print(f"{'#'*80}")

    total_bars = 0
    regime_total = {"trend_up": 0, "trend_down": 0, "extreme": 0, "range": 0}
    stocks_with_trend = 0

    params = SignalParams()

    for idx, code in enumerate(codes):
        filepath = ZZ500_5MIN_DIR / f"{code}.json"
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        daily_bars_raw = data.get("daily_bars", {})
        sorted_dates = sorted(daily_bars_raw.keys())

        stock_regime = {"trend_up": 0, "trend_down": 0, "extreme": 0, "range": 0}
        stock_bars = 0
        has_trend = False

        for date_idx, date in enumerate(sorted_dates):
            bars = daily_bars_raw[date]
            if not bars:
                continue

            prev_close = None
            if date_idx > 0:
                prev_date = sorted_dates[date_idx - 1]
                prev_bars = daily_bars_raw.get(prev_date, [])
                if prev_bars:
                    prev_close = prev_bars[-1]["close"]
            if prev_close is None:
                prev_close = bars[0]["open"]

            for i in range(12, len(bars)):  # 从第12根开始统计
                current_bars = bars[:i+1]
                snap = compute_reference_snapshot(current_bars, prev_close)
                regime = detect_market_regime(
                    snap,
                    adx_trend_threshold=params.adx_trend_threshold,
                    adx_extreme_threshold=params.adx_extreme_threshold,
                    extreme_vwap_dev_multiplier=params.extreme_vwap_dev_multiplier,
                    min_bars_for_trend=12,
                )
                stock_regime[regime] += 1
                stock_bars += 1
                if regime in ("trend_up", "trend_down", "extreme"):
                    has_trend = True

        if has_trend:
            stocks_with_trend += 1

        for k, v in stock_regime.items():
            regime_total[k] += v
        total_bars += stock_bars

        if (idx + 1) % 10 == 0 or idx == len(codes) - 1:
            print(f"  [{idx+1}/{len(codes)}] {code}: trend_bars比例="
                  f"{(stock_regime['trend_up']+stock_regime['trend_down']+stock_regime['extreme'])/max(stock_bars,1)*100:.1f}%")

    print(f"\n{'='*80}")
    print(f"全样本汇总 (共 {total_bars} 根有效bar):")
    print(f"{'='*80}")
    for r in ["trend_up", "trend_down", "extreme", "range"]:
        print(f"  {r}: {regime_total[r]} bars ({regime_total[r]/total_bars*100:.2f}%)")

    trend_bars = regime_total["trend_up"] + regime_total["trend_down"] + regime_total["extreme"]
    print(f"\n  趋势类bar合计: {trend_bars} ({trend_bars/total_bars*100:.2f}%)")
    print(f"  有趋势出现的股票: {stocks_with_trend}/{len(codes)} ({stocks_with_trend/len(codes)*100:.1f}%)")

    return regime_total, total_bars, stocks_with_trend


def main():
    print("=" * 80)
    print("Stage J0 — Regime识别诊断")
    print("=" * 80)

    # 步骤1: 复现603119 2026-04-21
    diagnose_single_day("603119", "2026-04-21")

    # 步骤2: 全样本统计
    diagnose_full_sample(sample_size=50, seed=42)


if __name__ == "__main__":
    main()
