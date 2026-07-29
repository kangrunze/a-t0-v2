#!/usr/bin/env python3
"""
Stage J1 — 捕获效率基准统计

对当前baseline（振幅筛选0.0494+已定案的止损参数）跑回测，
计算每笔已配对交易的捕获效率，输出分布统计（p25/p50/p75/mean）。

捕获效率定义：
  capture_efficiency = (sell_price - buy_price) / (day_high - day_low)

用法：
  python archive/diag/diag_stepJ1_capture_efficiency.py --sample 50 --seed 42 \
      --start 2023-07-25 --end 2026-07-22 --tag baseline
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
from at0.backtest import (
    BacktestParams,
    backtest_multi_day,
    summarize_one_stock,
)
from at0.strategy import SignalParams
from at0.risk import RiskParams
from at0.config import (
    load_signal_params,
    load_risk_params,
    load_backtest_params,
    load_screener_params,
)
from at0.cli import adapt_params_by_frequency, BACKTEST_OUTPUT_DIR


def load_zz500_data(code: str, start: str, end: str):
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


def calc_60d_amplitude(code: str, start_date: str, end_date: str = None, lookback: int = 60):
    """计算60日日均振幅。

    J0+修复：用回测窗口内前60天数据（与 backtest_zz500.py 一致），
    而非回测开始日之前的60天（本地数据从start_date开始，没有更早数据）。
    """
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    daily_bars_raw = data.get("daily_bars", {})
    sorted_dates = sorted(daily_bars_raw.keys())
    # J0+修复：用回测窗口内的日期
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
    if len(amplitudes) < 10:  # 至少10天数据
        return None
    return sum(amplitudes) / len(amplitudes)


def compute_daily_hl(daily_bars: dict) -> dict:
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
    all_pairs = []

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
            time_str = trade.get("time", "")
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

                    ce = None
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

                    all_pairs.append({
                        "buy_date": buy_date,
                        "sell_date": sell_date,
                        "buy_price": round(buy_price, 4),
                        "sell_price": round(sell_price, 4),
                        "shares": pair_shares,
                        "pnl": round((sell_price - buy_price) * pair_shares, 2),
                        "capture_efficiency": round(ce, 4) if ce is not None else None,
                        "same_day": sell_date == buy_date,
                    })

                    earliest["shares"] -= pair_shares
                    remaining -= pair_shares
                    if earliest["shares"] <= 0:
                        sell_queue.pop(0)
                if remaining > 0:
                    buy_queue.append({"price": price, "date": date, "time": time_str, "shares": remaining})

            else:  # sell
                while remaining > 0 and buy_queue:
                    earliest = buy_queue[0]
                    pair_shares = min(remaining, earliest["shares"])
                    buy_price = earliest["price"]
                    sell_price = price
                    buy_date = earliest["date"]
                    sell_date = date

                    ce = None
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

                    all_pairs.append({
                        "buy_date": buy_date,
                        "sell_date": sell_date,
                        "buy_price": round(buy_price, 4),
                        "sell_price": round(sell_price, 4),
                        "shares": pair_shares,
                        "pnl": round((sell_price - buy_price) * pair_shares, 2),
                        "capture_efficiency": round(ce, 4) if ce is not None else None,
                        "same_day": sell_date == buy_date,
                    })

                    earliest["shares"] -= pair_shares
                    remaining -= pair_shares
                    if earliest["shares"] <= 0:
                        buy_queue.pop(0)
                if remaining > 0:
                    sell_queue.append({"price": price, "date": date, "time": time_str, "shares": remaining})

    all_ce = same_day_ce + cross_day_ce
    return {
        "total_pairs": len(all_pairs),
        "same_day_pairs": len(same_day_ce),
        "cross_day_pairs": len(cross_day_ce),
        "same_day_stats": {
            "mean": round(sum(same_day_ce) / len(same_day_ce), 4) if same_day_ce else 0,
            "p25": round(percentile(same_day_ce, 0.25), 4),
            "p50": round(percentile(same_day_ce, 0.5), 4),
            "p75": round(percentile(same_day_ce, 0.75), 4),
            "min": round(min(same_day_ce), 4) if same_day_ce else 0,
            "max": round(max(same_day_ce), 4) if same_day_ce else 0,
        },
        "all_stats": {
            "mean": round(sum(all_ce) / len(all_ce), 4) if all_ce else 0,
            "p25": round(percentile(all_ce, 0.25), 4),
            "p50": round(percentile(all_ce, 0.5), 4),
            "p75": round(percentile(all_ce, 0.75), 4),
            "min": round(min(all_ce), 4) if all_ce else 0,
            "max": round(max(all_ce), 4) if all_ce else 0,
        },
        "same_day_ce_list": same_day_ce,
        "cross_day_ce_list": cross_day_ce,
    }


def run_single_stock(code: str, start: str, end: str):
    data = load_zz500_data(code, start, end)
    if not data:
        return None
    daily_bars, daily_prev_closes = data

    signal_params = load_signal_params()
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
    ce_stats = compute_capture_efficiency(result, daily_hl)
    summary = summarize_one_stock(code, result)

    return {
        "code": code,
        "ce_stats": ce_stats,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Stage J1 — 捕获效率基准统计")
    parser.add_argument("--sample", type=int, default=50, help="抽样股票数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--start", type=str, default="2024-01-01", help="起始日期")
    parser.add_argument("--end", type=str, default="2026-07-22", help="结束日期")
    parser.add_argument("--tag", type=str, default="baseline", help="输出标签")
    parser.add_argument("--all", action="store_true", help="用全部股票")
    parser.add_argument("--no-amplitude-filter", action="store_true", help="禁用振幅筛选")
    args = parser.parse_args()

    amplitude_filter = not args.no_amplitude_filter

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    print(f"[INFO] 找到 {len(all_codes)} 只股票的5min数据")

    if amplitude_filter:
        screener_params = load_screener_params()
        min_amp = screener_params.min_amplitude_long
        print(f"[INFO] 振幅筛选: min_amplitude_long={min_amp}")
        filtered_codes = []
        for code in all_codes:
            amp = calc_60d_amplitude(code, args.start, args.end, 60)
            if amp is not None and amp >= min_amp:
                filtered_codes.append(code)
        print(f"[INFO] 振幅筛选后: {len(filtered_codes)} 只通过")
        codes = filtered_codes
    else:
        print(f"[INFO] 振幅筛选: 禁用")
        codes = all_codes

    if not args.all and args.sample > 0:
        random.seed(args.seed)
        codes = random.sample(codes, min(args.sample, len(codes)))
        print(f"[INFO] 抽样 {len(codes)} 只股票 (seed={args.seed})")

    print(f"[INFO] 回测区间: {args.start} ~ {args.end}")
    print(f"[INFO] 开始回测...\n")

    per_stock = []
    all_same_day_ce = []
    all_cross_day_ce = []
    total_pnl = 0

    start_time = time.time()

    for i, code in enumerate(codes):
        try:
            res = run_single_stock(code, args.start, args.end)
            if not res:
                print(f"  [{i+1}/{len(codes)}] {code}: no data")
                continue

            ce = res["ce_stats"]
            s = res["summary"]
            per_stock.append({
                "code": code,
                "paired_trades": s.get("paired_trades", 0),
                "win_rate": s.get("win_rate", 0),
                "net_pnl_with_unrealized": s.get("net_pnl_with_unrealized", 0),
                "total_pairs": ce["total_pairs"],
                "same_day_pairs": ce["same_day_pairs"],
                "cross_day_pairs": ce["cross_day_pairs"],
                "same_day_stats": ce["same_day_stats"],
                "all_stats": ce["all_stats"],
                "same_day_ce_list": ce["same_day_ce_list"],
                "cross_day_ce_list": ce["cross_day_ce_list"],
            })

            total_pnl += s.get("net_pnl_with_unrealized", 0)

            print(f"  [{i+1}/{len(codes)}] {code}: "
                  f"配对={ce['total_pairs']}, "
                  f"同日CE均值={ce['same_day_stats']['mean']*100:.1f}%, "
                  f"盈亏={s.get('net_pnl_with_unrealized', 0):+.0f}")

        except Exception as e:
            print(f"  [{i+1}/{len(codes)}] {code}: ERROR - {e}")
            import traceback
            traceback.print_exc()
            continue

    elapsed = time.time() - start_time
    print(f"\n[INFO] 回测完成，耗时 {elapsed:.1f}s")
    print(f"[INFO] 成功股票: {len(per_stock)}/{len(codes)}")

    all_same_day_flat = []
    all_cross_day_flat = []
    total_pairs = 0
    total_same_day = 0
    total_cross_day = 0

    for s in per_stock:
        total_pairs += s["total_pairs"]
        total_same_day += s["same_day_pairs"]
        total_cross_day += s["cross_day_pairs"]
        all_same_day_flat.extend(s["same_day_ce_list"])
        all_cross_day_flat.extend(s["cross_day_ce_list"])

    all_ce_flat = all_same_day_flat + all_cross_day_flat

    overall_same_day = {
        "count": len(all_same_day_flat),
        "mean": round(sum(all_same_day_flat) / len(all_same_day_flat), 4) if all_same_day_flat else 0,
        "p25": round(percentile(all_same_day_flat, 0.25), 4),
        "p50": round(percentile(all_same_day_flat, 0.5), 4),
        "p75": round(percentile(all_same_day_flat, 0.75), 4),
        "min": round(min(all_same_day_flat), 4) if all_same_day_flat else 0,
        "max": round(max(all_same_day_flat), 4) if all_same_day_flat else 0,
    }

    overall_all = {
        "count": len(all_ce_flat),
        "mean": round(sum(all_ce_flat) / len(all_ce_flat), 4) if all_ce_flat else 0,
        "p25": round(percentile(all_ce_flat, 0.25), 4),
        "p50": round(percentile(all_ce_flat, 0.5), 4),
        "p75": round(percentile(all_ce_flat, 0.75), 4),
        "min": round(min(all_ce_flat), 4) if all_ce_flat else 0,
        "max": round(max(all_ce_flat), 4) if all_ce_flat else 0,
    }

    output = {
        "stage": "J1",
        "tag": args.tag,
        "description": "捕获效率基准统计（baseline，振幅筛选+已定案止损参数）",
        "start": args.start,
        "end": args.end,
        "sample_size": len(per_stock),
        "amplitude_filter": amplitude_filter,
        "total_pairs": total_pairs,
        "total_same_day_pairs": total_same_day,
        "total_cross_day_pairs": total_cross_day,
        "total_net_pnl_with_unrealized": round(total_pnl, 2),
        "capture_efficiency_same_day": overall_same_day,
        "capture_efficiency_all": overall_all,
        "per_stock": [
            {k: v for k, v in s.items() if k not in ("same_day_ce_list", "cross_day_ce_list")}
            for s in per_stock
        ],
    }

    out_dir = BACKTEST_OUTPUT_DIR
    out_path = out_dir / f"diag_J1_capture_efficiency_{args.tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"捕获效率基准统计结果 (Stage J1 Baseline)")
    print(f"{'='*60}")
    print(f"样本股票数: {len(per_stock)}")
    print(f"总配对数: {total_pairs}")
    print(f"同日配对数: {total_same_day}")
    print(f"跨日配对数: {total_cross_day}")
    print(f"总净盈亏（含浮盈）: {total_pnl:+,.2f} 元")
    print(f"\n同日配对捕获效率分布:")
    print(f"  均值: {overall_same_day['mean']*100:.2f}%")
    print(f"  P25:  {overall_same_day['p25']*100:.2f}%")
    print(f"  P50:  {overall_same_day['p50']*100:.2f}%")
    print(f"  P75:  {overall_same_day['p75']*100:.2f}%")
    print(f"  最小: {overall_same_day['min']*100:.2f}%")
    print(f"  最大: {overall_same_day['max']*100:.2f}%")
    print(f"\n全部配对捕获效率分布（含跨日）:")
    print(f"  均值: {overall_all['mean']*100:.2f}%")
    print(f"  P25:  {overall_all['p25']*100:.2f}%")
    print(f"  P50:  {overall_all['p50']*100:.2f}%")
    print(f"  P75:  {overall_all['p75']*100:.2f}%")
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
