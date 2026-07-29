#!/usr/bin/env python3
"""
Stage J2 — 回踩入场 A/B 回测验证

对比 baseline（现有趋势确认+立即入场）vs retracement（回踩入场），
重点看捕获效率是否改善，同时观察 win_rate/net_pnl 有没有被拖累。

用法：
  python archive/diag/diag_stepJ2_ab_test.py --sample 30 --seed 42 \
      --start 2024-01-01 --end 2026-07-22
"""
from __future__ import annotations

import argparse
import json
import random
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


def load_zz500_data(code, start, end):
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    daily_bars_raw = data.get("daily_bars", {})
    sorted_dates = sorted(daily_bars_raw.keys())
    daily_bars = {}
    daily_prev_closes = {}
    for i, date in enumerate(sorted_dates):
        if start and date < start:
            continue
        if end and date > end:
            continue
        bars = daily_bars_raw[date]
        if not bars:
            continue
        daily_bars[date] = bars
        if i > 0:
            prev_date = sorted_dates[i - 1]
            prev_bars = daily_bars_raw.get(prev_date, [])
            if prev_bars:
                daily_prev_closes[date] = prev_bars[-1]["close"]
            else:
                daily_prev_closes[date] = bars[0]["open"]
        else:
            daily_prev_closes[date] = bars[0]["open"]
    return daily_bars, daily_prev_closes


def calc_60d_amplitude(code, start_date, end_date=None, lookback=60):
    """J0+修复：用回测窗口内前60天数据（与 backtest_zz500.py 一致）"""
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    daily_bars_raw = data.get("daily_bars", {})
    sorted_dates = sorted(daily_bars_raw.keys())
    if end_date:
        in_range = [d for d in sorted_dates if start_date <= d <= end_date]
    else:
        in_range = [d for d in sorted_dates if d >= start_date]
    use_dates = in_range[:lookback]
    amplitudes = []
    prev_close = None
    for date in use_dates:
        bars = daily_bars_raw[date]
        if not bars:
            continue
        highs = [b["high"] for b in bars if b.get("high", 0) > 0]
        lows = [b["low"] for b in bars if b.get("low", 0) > 0]
        if not highs or not lows:
            continue
        day_high = max(highs)
        day_low = min(lows)
        if prev_close is None:
            prev_close = bars[0]["open"]
        if prev_close > 0:
            amplitudes.append((day_high - day_low) / prev_close)
        prev_close = bars[-1]["close"]
    if len(amplitudes) < 10:
        return None
    return sum(amplitudes) / len(amplitudes)


def compute_daily_hl(daily_bars):
    daily_hl = {}
    for date, bars in daily_bars.items():
        if not bars:
            continue
        highs = [b["high"] for b in bars if b.get("high", 0) > 0]
        lows = [b["low"] for b in bars if b.get("low", 0) > 0]
        if not highs or not lows:
            continue
        daily_hl[date] = {"high": max(highs), "low": min(lows)}
    return daily_hl


def percentile(data, p):
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def compute_capture_efficiency(result, daily_hl):
    buy_queue = []
    sell_queue = []
    same_day_ce = []
    cross_day_ce = []

    for dr in result.get("daily_results", []):
        date = dr["date"]
        day_hl = daily_hl.get(date)
        if not day_hl:
            continue
        day_range = day_hl["high"] - day_hl["low"]
        if day_range <= 0:
            continue

        for trade in dr.get("trades", []):
            direction = trade.get("direction")
            price = trade.get("fill_price", 0)
            shares = trade.get("shares", 0)
            if shares <= 0 or price <= 0:
                continue
            remaining = shares

            if direction == "buy":
                while remaining > 0 and sell_queue:
                    earliest = sell_queue[0]
                    pair_shares = min(remaining, earliest["shares"])
                    sell_price = earliest["price"]
                    buy_price = price
                    sell_date = earliest["date"]
                    buy_date = date

                    if sell_date == buy_date:
                        ce = (sell_price - buy_price) / day_range
                        same_day_ce.append(ce)
                    else:
                        sell_hl = daily_hl.get(sell_date)
                        buy_hl = daily_hl.get(buy_date)
                        if sell_hl and buy_hl:
                            combined_range = max(sell_hl["high"], buy_hl["high"]) - min(sell_hl["low"], buy_hl["low"])
                            if combined_range > 0:
                                ce = (sell_price - buy_price) / combined_range
                                cross_day_ce.append(ce)

                    earliest["shares"] -= pair_shares
                    remaining -= pair_shares
                    if earliest["shares"] <= 0:
                        sell_queue.pop(0)
                if remaining > 0:
                    buy_queue.append({"price": price, "date": date, "shares": remaining})
            else:
                while remaining > 0 and buy_queue:
                    earliest = buy_queue[0]
                    pair_shares = min(remaining, earliest["shares"])
                    buy_price = earliest["price"]
                    sell_price = price
                    buy_date = earliest["date"]
                    sell_date = date

                    if sell_date == buy_date:
                        ce = (sell_price - buy_price) / day_range
                        same_day_ce.append(ce)
                    else:
                        sell_hl = daily_hl.get(sell_date)
                        buy_hl = daily_hl.get(buy_date)
                        if sell_hl and buy_hl:
                            combined_range = max(sell_hl["high"], buy_hl["high"]) - min(sell_hl["low"], buy_hl["low"])
                            if combined_range > 0:
                                ce = (sell_price - buy_price) / combined_range
                                cross_day_ce.append(ce)

                    earliest["shares"] -= pair_shares
                    remaining -= pair_shares
                    if earliest["shares"] <= 0:
                        buy_queue.pop(0)
                if remaining > 0:
                    sell_queue.append({"price": price, "date": date, "shares": remaining})

    return same_day_ce, cross_day_ce


def run_backtest(code, start, end, signal_params):
    data = load_zz500_data(code, start, end)
    if not data:
        return None
    daily_bars, daily_prev_closes = data

    risk_params = load_risk_params()
    backtest_params = load_backtest_params()

    bt_params = BacktestParams(
        base_shares=3000,
        signal_params=signal_params,
        risk_params=risk_params,
    )
    bt_params.stop_loss_ratio = backtest_params.stop_loss_ratio
    bt_params.trailing_ratio = backtest_params.trailing_ratio
    bt_params.trailing_activation_pct = backtest_params.trailing_activation_pct
    bt_params.max_holding_bars = backtest_params.max_holding_bars
    bt_params.cooldown_bars = backtest_params.cooldown_bars
    bt_params = adapt_params_by_frequency(bt_params, "5min", 48)
    # J0+修复：adapt后重新应用mh24（避免被adapt覆盖为12）
    bt_params.max_holding_bars = backtest_params.max_holding_bars
    if bt_params.exposure_policy is not None:
        bt_params.exposure_policy.max_holding_bars = backtest_params.max_holding_bars

    result = backtest_multi_day(
        code=code,
        daily_bars=daily_bars,
        daily_prev_closes=daily_prev_closes,
        params=bt_params,
    )

    daily_hl = compute_daily_hl(daily_bars)
    same_day_ce, cross_day_ce = compute_capture_efficiency(result, daily_hl)
    summary = summarize_one_stock(code, result)

    return {
        "code": code,
        "summary": summary,
        "same_day_ce": same_day_ce,
        "cross_day_ce": cross_day_ce,
    }


def stats_from_ce_list(ce_list):
    if not ce_list:
        return {"count": 0, "mean": 0, "p25": 0, "p50": 0, "p75": 0, "min": 0, "max": 0}
    return {
        "count": len(ce_list),
        "mean": round(sum(ce_list) / len(ce_list), 4),
        "p25": round(percentile(ce_list, 0.25), 4),
        "p50": round(percentile(ce_list, 0.5), 4),
        "p75": round(percentile(ce_list, 0.75), 4),
        "min": round(min(ce_list), 4),
        "max": round(max(ce_list), 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Stage J2 — 回踩入场 A/B 回测验证")
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2026-07-22")
    # 回踩参数可命令行覆盖，便于调参
    parser.add_argument("--vwap_band", type=float, default=None, help="retracement_vwap_band（默认0.005=0.5%%）")
    parser.add_argument("--kdj_max", type=float, default=None, help="retracement_kdj_max（默认60）")
    parser.add_argument("--lookback", type=int, default=None, help="retracement_lookback（默认8）")
    parser.add_argument("--min_surge", type=float, default=None, help="retracement_min_surge（默认0.005）")
    args = parser.parse_args()

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    print(f"[INFO] 找到 {len(all_codes)} 只股票")

    screener_params = load_screener_params()
    min_amp = screener_params.min_amplitude_long
    print(f"[INFO] 振幅筛选: min_amplitude_long={min_amp}")
    filtered_codes = [c for c in all_codes if (lambda a: a is not None and a >= min_amp)(calc_60d_amplitude(c, args.start, args.end))]
    print(f"[INFO] 筛选后: {len(filtered_codes)} 只")

    random.seed(args.seed)
    codes = random.sample(filtered_codes, min(args.sample, len(filtered_codes)))
    print(f"[INFO] 抽样 {len(codes)} 只 (seed={args.seed})")
    print(f"[INFO] 区间: {args.start} ~ {args.end}\n")

    # baseline 参数
    baseline_params = load_signal_params()
    # 回踩入场参数
    retracement_params = load_signal_params()
    retracement_params.retracement_entry_enabled = True
    # 命令行覆盖
    if args.vwap_band is not None:
        retracement_params.retracement_vwap_band = args.vwap_band
    if args.kdj_max is not None:
        retracement_params.retracement_kdj_max = args.kdj_max
    if args.lookback is not None:
        retracement_params.retracement_lookback = args.lookback
    if args.min_surge is not None:
        retracement_params.retracement_min_surge = args.min_surge

    print(f"[INFO] Baseline: retracement_entry_enabled={baseline_params.retracement_entry_enabled}")
    print(f"[INFO] Retracement: retracement_entry_enabled={retracement_params.retracement_entry_enabled}")
    print(f"[INFO]   retracement_min_adx={retracement_params.retracement_min_adx}")
    print(f"[INFO]   retracement_vwap_band={retracement_params.retracement_vwap_band}")
    print(f"[INFO]   retracement_lookback={retracement_params.retracement_lookback}")
    print(f"[INFO]   retracement_min_surge={retracement_params.retracement_min_surge}")
    print(f"[INFO]   retracement_kdj_max={retracement_params.retracement_kdj_max}\n")

    baseline_results = []
    retracement_results = []
    start_time = time.time()

    for i, code in enumerate(codes):
        try:
            # Baseline
            bl = run_backtest(code, args.start, args.end, baseline_params)
            if bl:
                baseline_results.append(bl)

            # Retracement
            rt = run_backtest(code, args.start, args.end, retracement_params)
            if rt:
                retracement_results.append(rt)

            bl_ce = stats_from_ce_list(bl["same_day_ce"]) if bl else {"mean": 0}
            rt_ce = stats_from_ce_list(rt["same_day_ce"]) if rt else {"mean": 0}
            bl_pnl = bl["summary"].get("net_pnl_with_unrealized", 0) if bl else 0
            rt_pnl = rt["summary"].get("net_pnl_with_unrealized", 0) if rt else 0

            print(f"  [{i+1}/{len(codes)}] {code}: "
                  f"BL配对={bl['summary'].get('paired_trades',0) if bl else 0} "
                  f"CE={bl_ce['mean']*100:.1f}% "
                  f"盈亏={bl_pnl:+.0f}  |  "
                  f"RT配对={rt['summary'].get('paired_trades',0) if rt else 0} "
                  f"CE={rt_ce['mean']*100:.1f}% "
                  f"盈亏={rt_pnl:+.0f}")

        except Exception as e:
            print(f"  [{i+1}/{len(codes)}] {code}: ERROR - {e}")
            import traceback
            traceback.print_exc()

    elapsed = time.time() - start_time
    print(f"\n[INFO] 完成，耗时 {elapsed:.1f}s")

    # 汇总
    bl_all_ce = []
    rt_all_ce = []
    bl_all_pnl = 0
    rt_all_pnl = 0
    bl_total_pairs = 0
    rt_total_pairs = 0

    for r in baseline_results:
        bl_all_ce.extend(r["same_day_ce"])
        bl_all_pnl += r["summary"].get("net_pnl_with_unrealized", 0)
        bl_total_pairs += r["summary"].get("paired_trades", 0)

    for r in retracement_results:
        rt_all_ce.extend(r["same_day_ce"])
        rt_all_pnl += r["summary"].get("net_pnl_with_unrealized", 0)
        rt_total_pairs += r["summary"].get("paired_trades", 0)

    bl_stats = stats_from_ce_list(bl_all_ce)
    rt_stats = stats_from_ce_list(rt_all_ce)

    # 胜率统计
    bl_wins = sum(1 for r in baseline_results if r["summary"].get("net_pnl_with_unrealized", 0) > 0)
    rt_wins = sum(1 for r in retracement_results if r["summary"].get("net_pnl_with_unrealized", 0) > 0)

    print(f"\n{'='*70}")
    print(f"A/B 对比结果 (Stage J2)")
    print(f"{'='*70}")
    print(f"\n{'指标':<25} {'Baseline':>15} {'Retracement':>15} {'变化':>15}")
    print(f"{'-'*70}")
    print(f"{'样本股票数':<25} {len(baseline_results):>15} {len(retracement_results):>15}")
    print(f"{'总配对数':<25} {bl_total_pairs:>15} {rt_total_pairs:>15} {rt_total_pairs-bl_total_pairs:>+15}")
    print(f"{'总净盈亏':<25} {bl_all_pnl:>+15.0f} {rt_all_pnl:>+15.0f} {rt_all_pnl-bl_all_pnl:>+15.0f}")
    print(f"{'盈利股票数':<25} {bl_wins:>15} {rt_wins:>15}")

    print(f"\n{'捕获效率(同日)':<25} {'':>15} {'':>15}")
    print(f"{'  均值':<25} {bl_stats['mean']*100:>14.2f}% {rt_stats['mean']*100:>14.2f}% {(rt_stats['mean']-bl_stats['mean'])*100:>+14.2f}%")
    print(f"{'  P25':<25} {bl_stats['p25']*100:>14.2f}% {rt_stats['p25']*100:>14.2f}% {(rt_stats['p25']-bl_stats['p25'])*100:>+14.2f}%")
    print(f"{'  P50(中位数)':<25} {bl_stats['p50']*100:>14.2f}% {rt_stats['p50']*100:>14.2f}% {(rt_stats['p50']-bl_stats['p50'])*100:>+14.2f}%")
    print(f"{'  P75':<25} {bl_stats['p75']*100:>14.2f}% {rt_stats['p75']*100:>14.2f}% {(rt_stats['p75']-bl_stats['p75'])*100:>+14.2f}%")
    print(f"{'  最小':<25} {bl_stats['min']*100:>14.2f}% {rt_stats['min']*100:>14.2f}%")
    print(f"{'  最大':<25} {bl_stats['max']*100:>14.2f}% {rt_stats['max']*100:>14.2f}%")

    # 保存结果
    output = {
        "stage": "J2",
        "description": "回踩入场 A/B 回测验证",
        "start": args.start,
        "end": args.end,
        "sample_size": len(baseline_results),
        "params": {
            "retracement_min_adx": retracement_params.retracement_min_adx,
            "retracement_vwap_band": retracement_params.retracement_vwap_band,
            "retracement_lookback": retracement_params.retracement_lookback,
            "retracement_min_surge": retracement_params.retracement_min_surge,
            "retracement_kdj_max": retracement_params.retracement_kdj_max,
        },
        "baseline": {
            "total_pairs": bl_total_pairs,
            "total_pnl": round(bl_all_pnl, 2),
            "profitable_stocks": bl_wins,
            "capture_efficiency": bl_stats,
        },
        "retracement": {
            "total_pairs": rt_total_pairs,
            "total_pnl": round(rt_all_pnl, 2),
            "profitable_stocks": rt_wins,
            "capture_efficiency": rt_stats,
        },
        "per_stock": [],
    }

    for bl, rt in zip(baseline_results, retracement_results):
        bl_s = stats_from_ce_list(bl["same_day_ce"])
        rt_s = stats_from_ce_list(rt["same_day_ce"])
        output["per_stock"].append({
            "code": bl["code"],
            "baseline": {"pairs": bl["summary"].get("paired_trades", 0), "pnl": bl["summary"].get("net_pnl_with_unrealized", 0), "ce_mean": bl_s["mean"]},
            "retracement": {"pairs": rt["summary"].get("paired_trades", 0), "pnl": rt["summary"].get("net_pnl_with_unrealized", 0), "ce_mean": rt_s["mean"]},
        })

    out_dir = BACKTEST_OUTPUT_DIR
    out_path = out_dir / "diag_J2_ab_test.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
