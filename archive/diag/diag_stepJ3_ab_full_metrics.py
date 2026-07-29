#!/usr/bin/env python3
"""
Stage J3 — 回踩入场扩量A/B对比（完整指标）

在36股×3年上对比baseline vs retracement的win_rate/net_pnl/payoff_ratio/
avg_win/avg_loss/成本毛利比，验证J2没有拖累这些指标到不可接受的程度。

同时分析新增交易（retracement多出来的部分）的质量。
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
from at0.risk import RiskParams
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
        avg_amp = sum(amps) / len(amps)
        if avg_amp >= threshold:
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


def build_params(j2_enabled=False, vwap_band=0.02, kdj_max=70.0):
    sp = load_signal_params()
    sp.retracement_entry_enabled = j2_enabled
    if j2_enabled:
        sp.retracement_vwap_band = vwap_band
        sp.retracement_kdj_max = kdj_max

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


def aggregate(results):
    if not results:
        return {}
    total_paired = sum(r.get("paired_trades", 0) for r in results)
    total_trades = sum(r.get("total_trades", 0) for r in results)
    total_wins = sum(r.get("win_trades", 0) for r in results)
    total_losses = sum(r.get("loss_trades", 0) for r in results)
    total_net = sum(r.get("net_pnl_with_unrealized", 0) for r in results)
    total_gross = sum(r.get("gross_pnl", 0) for r in results)
    total_cost = sum(r.get("total_cost", 0) for r in results)
    total_win_pnl = sum(r.get("avg_win", 0) * r.get("win_trades", 0) for r in results)
    total_loss_pnl = sum(r.get("avg_loss", 0) * r.get("loss_trades", 0) for r in results)
    avg_win = total_win_pnl / total_wins if total_wins else 0
    avg_loss = total_loss_pnl / total_losses if total_losses else 0
    payoff = avg_win / abs(avg_loss) if avg_loss != 0 else 0
    win_rate = total_wins / total_paired if total_paired else 0
    cost_gross_ratio = total_cost / abs(total_gross) if total_gross != 0 else 0
    profitable = sum(1 for r in results if r.get("net_pnl_with_unrealized", 0) > 0)
    return {
        "stocks": len(results),
        "total_trades": total_trades,
        "paired_trades": total_paired,
        "win_trades": total_wins,
        "loss_trades": total_losses,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(payoff, 4),
        "gross_pnl": round(total_gross, 2),
        "total_cost": round(total_cost, 2),
        "net_pnl": round(total_net, 2),
        "cost_gross_ratio": round(cost_gross_ratio, 4),
        "profitable_stocks": profitable,
    }


def main():
    parser = argparse.ArgumentParser(description="Stage J3 — 回踩入场扩量A/B完整指标对比")
    parser.add_argument("--sample", type=int, default=36)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2023-07-25")
    parser.add_argument("--end", type=str, default="2026-07-22")
    parser.add_argument("--vwap_band", type=float, default=0.02)
    parser.add_argument("--kdj_max", type=float, default=70.0)
    args = parser.parse_args()

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    screener_params = load_screener_params()
    min_amp = screener_params.min_amplitude_long
    print(f"[INFO] 振幅筛选: {min_amp}")
    filtered = filter_codes_by_amplitude(all_codes, ZZ500_5MIN_DIR, args.start, args.end, min_amp)
    print(f"[INFO] 筛选后: {len(filtered)} 只")
    random.seed(args.seed)
    codes = random.sample(filtered, min(args.sample, len(filtered)))
    print(f"[INFO] 抽样 {len(codes)} 只")

    # 加载数据
    stock_data = {}
    for code in codes:
        data = load_zz500_data(code, args.start, args.end)
        if data and data[0]:
            stock_data[code] = data
    print(f"[INFO] 加载 {len(stock_data)} 只\n")

    # 跑两组
    groups = {"baseline": False, "retracement": True}
    results = {}

    for name, j2_on in groups.items():
        print(f"\n{'='*70}")
        print(f"运行 {name} (J2={'ON' if j2_on else 'OFF'})")
        print(f"{'='*70}")
        params = build_params(j2_enabled=j2_on, vwap_band=args.vwap_band, kdj_max=args.kdj_max)
        group_results = []
        for i, code in enumerate(codes):
            if code not in stock_data:
                continue
            try:
                daily_bars, daily_prev_closes = stock_data[code]
                result = backtest_multi_day(
                    code=code, daily_bars=daily_bars,
                    daily_prev_closes=daily_prev_closes, params=params,
                )
                r = summarize_one_stock(code, result)
                group_results.append(r)
            except Exception as e:
                print(f"  [{i+1}] {code}: ERROR - {e}")
            if (i + 1) % 12 == 0:
                print(f"  [{i+1}/{len(codes)}] 完成")
        results[name] = group_results

    # 汇总对比
    bl = aggregate(results["baseline"])
    rt = aggregate(results["retracement"])

    print(f"\n{'='*100}")
    print(f"J3 A/B 完整指标对比（36股×3年, 新VWAP, sl=0.002/tr=0.2/tap=0/cd24/mh24）")
    print(f"{'='*100}")
    print(f"\n{'指标':<25} {'Baseline':>18} {'Retracement':>18} {'变化':>18}")
    print(f"{'-'*80}")
    print(f"{'样本股票数':<25} {bl.get('stocks',0):>18} {rt.get('stocks',0):>18}")
    print(f"{'总配对数':<25} {bl.get('paired_trades',0):>18} {rt.get('paired_trades',0):>18} {rt.get('paired_trades',0)-bl.get('paired_trades',0):>+18}")
    print(f"{'胜率':<25} {bl.get('win_rate',0)*100:>17.2f}% {rt.get('win_rate',0)*100:>17.2f}% {(rt.get('win_rate',0)-bl.get('win_rate',0))*100:>+17.2f}%")
    print(f"{'avg_win':<25} {bl.get('avg_win',0):>+18.0f} {rt.get('avg_win',0):>+18.0f} {rt.get('avg_win',0)-bl.get('avg_win',0):>+18.0f}")
    print(f"{'avg_loss':<25} {bl.get('avg_loss',0):>+18.0f} {rt.get('avg_loss',0):>+18.0f} {rt.get('avg_loss',0)-bl.get('avg_loss',0):>+18.0f}")
    print(f"{'payoff_ratio':<25} {bl.get('payoff_ratio',0):>18.4f} {rt.get('payoff_ratio',0):>18.4f} {rt.get('payoff_ratio',0)-bl.get('payoff_ratio',0):>+18.4f}")
    print(f"{'毛盈亏':<25} {bl.get('gross_pnl',0):>+18.0f} {rt.get('gross_pnl',0):>+18.0f} {rt.get('gross_pnl',0)-bl.get('gross_pnl',0):>+18.0f}")
    print(f"{'总成本':<25} {bl.get('total_cost',0):>+18.0f} {rt.get('total_cost',0):>+18.0f} {rt.get('total_cost',0)-bl.get('total_cost',0):>+18.0f}")
    print(f"{'成本/毛利比':<25} {bl.get('cost_gross_ratio',0)*100:>17.2f}% {rt.get('cost_gross_ratio',0)*100:>17.2f}% {(rt.get('cost_gross_ratio',0)-bl.get('cost_gross_ratio',0))*100:>+17.2f}%")
    print(f"{'净盈亏':<25} {bl.get('net_pnl',0):>+18.0f} {rt.get('net_pnl',0):>+18.0f} {rt.get('net_pnl',0)-bl.get('net_pnl',0):>+18.0f}")
    print(f"{'盈利股票数':<25} {bl.get('profitable_stocks',0):>18} {rt.get('profitable_stocks',0):>18}")

    # 新增交易质量分析
    bl_pairs = bl.get("paired_trades", 0)
    rt_pairs = rt.get("paired_trades", 0)
    new_pairs = rt_pairs - bl_pairs
    bl_net = bl.get("net_pnl", 0)
    rt_net = rt.get("net_pnl", 0)
    new_net = rt_net - bl_net
    print(f"\n{'='*80}")
    print(f"新增交易质量分析")
    print(f"{'='*80}")
    print(f"  新增配对数: {new_pairs} ({new_pairs/rt_pairs*100:.1f}% of retracement)")
    print(f"  新增净盈亏: {new_net:+.0f} ({new_net/rt_net*100:.1f}% of retracement)")
    if new_pairs > 0:
        print(f"  新增交易平均盈亏: {new_net/new_pairs:+.1f} 元/笔")
        print(f"  baseline平均盈亏: {bl_net/bl_pairs:+.1f} 元/笔")
        ratio = (new_net/new_pairs) / (bl_net/bl_pairs) if bl_net > 0 else 0
        print(f"  新增/baseline质量比: {ratio:.2f}x")
        if ratio > 0.8:
            print(f"  ✓ 新增交易质量与baseline相当（>{0.8}x）")
        elif ratio > 0.5:
            print(f"  △ 新增交易质量略低于baseline（{ratio:.2f}x）")
        else:
            print(f"  ✗ 新增交易质量明显低于baseline（{ratio:.2f}x）")

    # 逐股对比
    print(f"\n{'='*100}")
    print(f"逐股对比")
    print(f"{'='*100}")
    print(f"\n{'代码':<10} {'BL-配对':>8} {'RT-配对':>8} {'BL-胜率':>8} {'RT-胜率':>8} {'BL-payoff':>10} {'RT-payoff':>10} {'BL-净盈亏':>12} {'RT-净盈亏':>12}")
    print(f"{'-'*100}")
    for b, r in zip(results["baseline"], results["retracement"]):
        code = b["code"]
        print(f"{code:<10} {b.get('paired_trades',0):>8} {r.get('paired_trades',0):>8} "
              f"{b.get('win_rate',0)*100:>7.1f}% {r.get('win_rate',0)*100:>7.1f}% "
              f"{b.get('payoff_ratio',0):>10.3f} {r.get('payoff_ratio',0):>10.3f} "
              f"{b.get('net_pnl_with_unrealized',0):>+12.0f} {r.get('net_pnl_with_unrealized',0):>+12.0f}")

    # 保存
    output = {
        "stage": "J3",
        "baseline": bl,
        "retracement": rt,
        "new_pairs": new_pairs,
        "new_net": new_net,
    }
    out_path = BACKTEST_OUTPUT_DIR / "diag_J3_ab_full_metrics.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
