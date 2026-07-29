#!/usr/bin/env python3
"""
Stage J4 — 冲高确认参数敏感性检查

J4四组测试发现 CE 均值下降（2.26%→1.82%），与设计目标相反。
本脚本用多组 surge 参数验证：CE 下降是结构性问题还是参数问题。

只跑 baseline + 多组 J4-only（不跑 J2 组合），用 20 股小样本快速验证。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import replace as _replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock
from at0.strategy import SignalParams
from at0.config import load_signal_params, load_risk_params, load_backtest_params, load_screener_params
from at0.cli import adapt_params_by_frequency, BACKTEST_OUTPUT_DIR


def filter_codes_by_amplitude(codes, data_dir, start_date, end_date, threshold, window=60):
    passed = []
    for code in codes:
        path = data_dir / f"{code}.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        daily_bars = d.get("daily_bars", {})
        if not daily_bars:
            continue
        sorted_dates = sorted(daily_bars.keys())
        in_range = [dt for dt in sorted_dates if start_date <= dt <= end_date]
        use_dates = in_range[:window] if len(in_range) >= window else in_range
        amps = []
        prev_close = None
        for date in use_dates:
            bars = daily_bars[date]
            if not bars:
                continue
            high = max(b["high"] for b in bars)
            low = min(b["low"] for b in bars)
            if prev_close is None:
                prev_close = bars[0]["open"]
            if prev_close > 0:
                amps.append((high - low) / prev_close)
            prev_close = bars[-1]["close"]
        if not amps:
            continue
        if sum(amps) / len(amps) >= threshold:
            passed.append(code)
    return passed


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
    prev_day_close = None
    for date in sorted_dates:
        if date < start or date > end:
            continue
        bars = daily_bars_raw[date]
        if not bars:
            continue
        if prev_day_close is not None and prev_day_close > 0:
            pc = prev_day_close
        else:
            pc = float(bars[0].get("open", 0))
        if pc <= 0:
            prev_day_close = float(bars[-1].get("close", 0)) or None
            continue
        daily_bars[date] = bars
        daily_prev_closes[date] = pc
        prev_day_close = float(bars[-1].get("close", 0)) or None
    return daily_bars, daily_prev_closes


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
    ce_list = []
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
                        ce_list.append((sell_price - buy_price) / day_range)
                    else:
                        sell_hl = daily_hl.get(sell_date)
                        buy_hl = daily_hl.get(buy_date)
                        if sell_hl and buy_hl:
                            cr = max(sell_hl["high"], buy_hl["high"]) - min(sell_hl["low"], buy_hl["low"])
                            if cr > 0:
                                ce_list.append((sell_price - buy_price) / cr)
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
                        ce_list.append((sell_price - buy_price) / day_range)
                    else:
                        sell_hl = daily_hl.get(sell_date)
                        buy_hl = daily_hl.get(buy_date)
                        if sell_hl and buy_hl:
                            cr = max(sell_hl["high"], buy_hl["high"]) - min(sell_hl["low"], buy_hl["low"])
                            if cr > 0:
                                ce_list.append((sell_price - buy_price) / cr)
                    earliest["shares"] -= pair_shares
                    remaining -= pair_shares
                    if earliest["shares"] <= 0:
                        buy_queue.pop(0)
                if remaining > 0:
                    sell_queue.append({"price": price, "date": date, "shares": remaining})
    return ce_list


def build_params(j4=False, surge_vwap_band=0.02, surge_kdj_min=30.0,
                 surge_min_drop=0.005, surge_lookback=8):
    sp = load_signal_params()
    sp.surge_exit_enabled = j4
    if j4:
        sp.surge_vwap_band = surge_vwap_band
        sp.surge_kdj_min = surge_kdj_min
        sp.surge_min_drop = surge_min_drop
        sp.surge_lookback = surge_lookback
    rp = load_risk_params()
    bp = load_backtest_params()
    params = BacktestParams(base_shares=3000, signal_params=sp, risk_params=rp)
    params.stop_loss_ratio = bp.stop_loss_ratio
    params.trailing_ratio = bp.trailing_ratio
    params.trailing_activation_pct = bp.trailing_activation_pct
    params.cooldown_bars = bp.cooldown_bars
    params.max_holding_bars = bp.max_holding_bars
    params = adapt_params_by_frequency(params, "5min", 48)
    params = _replace(params, max_holding_bars=bp.max_holding_bars)
    if params.exposure_policy is not None:
        params.exposure_policy.max_holding_bars = bp.max_holding_bars
    return params


def run_group(codes, stock_data, params):
    results = []
    all_ce = []
    for code in codes:
        if code not in stock_data:
            continue
        try:
            daily_bars, daily_prev_closes = stock_data[code]
            result = backtest_multi_day(
                code=code, daily_bars=daily_bars,
                daily_prev_closes=daily_prev_closes, params=params,
            )
            r = summarize_one_stock(code, result)
            results.append(r)
            daily_hl = compute_daily_hl(daily_bars)
            ce_list = compute_capture_efficiency(result, daily_hl)
            all_ce.extend(ce_list)
        except Exception as e:
            print(f"  {code}: ERROR - {e}")
    # aggregate
    total_paired = sum(r.get("paired_trades", 0) for r in results)
    total_wins = sum(r.get("win_trades", 0) for r in results)
    total_losses = sum(r.get("loss_trades", 0) for r in results)
    total_net = sum(r.get("net_pnl_with_unrealized", 0) for r in results)
    total_win_pnl = sum(r.get("avg_win", 0) * r.get("win_trades", 0) for r in results)
    total_loss_pnl = sum(r.get("avg_loss", 0) * r.get("loss_trades", 0) for r in results)
    avg_win = total_win_pnl / total_wins if total_wins else 0
    avg_loss = total_loss_pnl / total_losses if total_losses else 0
    payoff = avg_win / abs(avg_loss) if avg_loss != 0 else 0
    win_rate = total_wins / total_paired if total_paired else 0
    ce_mean = sum(all_ce) / len(all_ce) if all_ce else 0
    ce_p50 = percentile(all_ce, 0.5) if all_ce else 0
    ce_min = min(all_ce) if all_ce else 0
    return {
        "paired": total_paired,
        "win_rate": round(win_rate, 4),
        "payoff": round(payoff, 4),
        "net_pnl": round(total_net, 0),
        "ce_mean": round(ce_mean, 4),
        "ce_p50": round(ce_p50, 4),
        "ce_min": round(ce_min, 4),
        "ce_count": len(all_ce),
    }


def main():
    parser = argparse.ArgumentParser(description="J4 参数敏感性检查")
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2023-07-25")
    parser.add_argument("--end", type=str, default="2026-07-22")
    args = parser.parse_args()

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    screener_params = load_screener_params()
    min_amp = screener_params.min_amplitude_long
    filtered = filter_codes_by_amplitude(all_codes, ZZ500_5MIN_DIR, args.start, args.end, min_amp)
    print(f"[INFO] 筛选后: {len(filtered)} 只")
    random.seed(args.seed)
    codes = random.sample(filtered, min(args.sample, len(filtered)))
    print(f"[INFO] 抽样 {len(codes)} 只")

    stock_data = {}
    for code in codes:
        data = load_zz500_data(code, args.start, args.end)
        if data and data[0]:
            stock_data[code] = data
    print(f"[INFO] 加载 {len(stock_data)} 只\n")

    # 参数组合：从松到紧
    configs = [
        ("baseline",       {"j4": False}),
        ("j4_loose",       {"j4": True, "surge_vwap_band": 0.02,  "surge_kdj_min": 30.0, "surge_min_drop": 0.005, "surge_lookback": 8}),
        ("j4_medium",      {"j4": True, "surge_vwap_band": 0.01,  "surge_kdj_min": 40.0, "surge_min_drop": 0.008, "surge_lookback": 8}),
        ("j4_tight",       {"j4": True, "surge_vwap_band": 0.005, "surge_kdj_min": 50.0, "surge_min_drop": 0.01,  "surge_lookback": 10}),
        ("j4_very_tight",  {"j4": True, "surge_vwap_band": 0.003, "surge_kdj_min": 60.0, "surge_min_drop": 0.015, "surge_lookback": 12}),
    ]

    results = {}
    t0 = time.time()
    for name, cfg in configs:
        print(f"运行 {name} ...", end="", flush=True)
        if cfg.get("j4"):
            params = build_params(j4=True, surge_vwap_band=cfg["surge_vwap_band"],
                                  surge_kdj_min=cfg["surge_kdj_min"],
                                  surge_min_drop=cfg["surge_min_drop"],
                                  surge_lookback=cfg["surge_lookback"])
        else:
            params = build_params(j4=False)
        r = run_group(codes, stock_data, params)
        results[name] = r
        print(f" 配对={r['paired']} 净盈亏={r['net_pnl']:+.0f} CE均值={r['ce_mean']*100:.2f}% CE中位={r['ce_p50']*100:.2f}%")

    elapsed = time.time() - t0
    print(f"\n总耗时 {elapsed:.0f}s ({elapsed/60:.1f}分钟)")

    # 汇总
    print(f"\n{'='*120}")
    print(f"J4 参数敏感性检查（{args.sample}股×3年）")
    print(f"{'='*120}")
    print(f"\n{'配置':<16} {'配对':>7} {'胜率':>7} {'payoff':>8} {'净盈亏':>12} {'CE均值':>8} {'CE中位':>8} {'CE最小':>8} {'CE样本':>7}")
    print(f"{'-'*120}")
    base_ce = results["baseline"]["ce_mean"]
    for name, _ in configs:
        r = results[name]
        delta = f"({(r['ce_mean']-base_ce)*100:+.2f}pp)" if name != "baseline" else ""
        print(f"{name:<16} {r['paired']:>7} {r['win_rate']*100:>6.1f}% {r['payoff']:>8.4f} {r['net_pnl']:>+12.0f} "
              f"{r['ce_mean']*100:>7.2f}% {r['ce_p50']*100:>7.2f}% {r['ce_min']*100:>7.2f}% {r['ce_count']:>7} {delta}")

    # 保存
    output = {
        "stage": "J4_param_sweep",
        "sample": args.sample,
        "configs": {name: results[name] for name, _ in configs},
    }
    out_path = BACKTEST_OUTPUT_DIR / "diag_J4_param_sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
