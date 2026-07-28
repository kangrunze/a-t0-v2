#!/usr/bin/env python3
"""快速测试：单只股票回测 + 捕获效率计算验证"""
from __future__ import annotations

import json
import sys
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
)
from at0.cli import adapt_params_by_frequency


def load_data(code: str, start: str, end: str):
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
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


def compute_daily_hl(daily_bars: dict) -> dict:
    daily_hl = {}
    for date, bars in daily_bars.items():
        if not bars:
            continue
        highs = [b["high"] for b in bars if b.get("high", 0) > 0]
        lows = [b["low"] for b in bars if b.get("low", 0) > 0]
        if not highs or not lows:
            continue
        daily_hl[date] = {
            "high": max(highs),
            "low": min(lows),
        }
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
                        "capture_efficiency": round(ce, 4) if 'ce' in dir() else None,
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
                        "capture_efficiency": round(ce, 4) if 'ce' in dir() else None,
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
        "sample_pairs": all_pairs[:10],
    }


def main():
    code = "603119"
    start = "2026-04-01"
    end = "2026-04-30"

    print(f"测试股票: {code}, 区间: {start} ~ {end}")

    daily_bars, daily_prev_closes = load_data(code, start, end)
    print(f"交易日数: {len(daily_bars)}")

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

    result = backtest_multi_day(
        code=code,
        daily_bars=daily_bars,
        daily_prev_closes=daily_prev_closes,
        params=bt_params,
    )

    summary = summarize_one_stock(code, result)
    print(f"\n回测摘要:")
    print(f"  配对交易: {summary.get('paired_trades', 0)}")
    print(f"  胜率: {summary.get('win_rate', 0)*100:.1f}%")
    print(f"  净盈亏: {summary.get('net_pnl_with_unrealized', 0):+.2f}")

    print(f"\ndaily_results 数量: {len(result.get('daily_results', []))}")
    if result.get("daily_results"):
        first_day = result["daily_results"][0]
        print(f"首日 date: {first_day.get('date')}")
        print(f"首日 trades 数量: {len(first_day.get('trades', []))}")
        if first_day.get("trades"):
            print(f"首日首笔trade keys: {list(first_day['trades'][0].keys())}")
            print(f"首日首笔trade: {json.dumps(first_day['trades'][0], ensure_ascii=False, indent=2)}")

    daily_hl = compute_daily_hl(daily_bars)
    print(f"\n每日HL数量: {len(daily_hl)}")

    ce_stats = compute_capture_efficiency(result, daily_hl)
    print(f"\n捕获效率统计:")
    print(f"  总配对: {ce_stats['total_pairs']}")
    print(f"  同日配对: {ce_stats['same_day_pairs']}")
    print(f"  跨日配对: {ce_stats['cross_day_pairs']}")
    print(f"\n  同日配对CE:")
    print(f"    均值: {ce_stats['same_day_stats']['mean']*100:.2f}%")
    print(f"    P50:  {ce_stats['same_day_stats']['p50']*100:.2f}%")
    print(f"    P25:  {ce_stats['same_day_stats']['p25']*100:.2f}%")
    print(f"    P75:  {ce_stats['same_day_stats']['p75']*100:.2f}%")
    print(f"\n  样本配对:")
    for p in ce_stats["sample_pairs"][:5]:
        print(f"    {p['buy_date']} -> {p['sell_date']} "
              f"买:{p['buy_price']} 卖:{p['sell_price']} "
              f"盈亏:{p['pnl']:+.2f} CE:{p['capture_efficiency']*100 if p['capture_efficiency'] else 'N/A':.2f}% "
              f"同日:{p['same_day']}")


if __name__ == "__main__":
    main()
