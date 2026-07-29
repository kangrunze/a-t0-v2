#!/usr/bin/env python3
"""
Stage J0 — VWAP 修复独立隔离验证

背景：J0 修复了 cumulative_vwap 的计算 bug（amount/volume → typical_price×volume）。
这个 bug 可能已经存在很长时间，影响面覆盖 VWAP 偏离度、regime 判定、移动止盈、
回踩入场设计等核心逻辑。在继续 J2 之前，必须先把这个修复单独隔离验证。

本脚本通过猴补丁切换 VWAP 实现，在 baseline（振幅筛选0.0494 + Stage G 定案止损参数，
不含 J2 回踩入场）上做 A/B 对比。

三步验证：
  Step 1: baseline 旧VWAP vs 新VWAP 的 A/B 回测（win_rate/net_pnl/payoff_ratio/成本毛利比）
  Step 2: 量化 vwap_dev 字段修复前后的数值差异分布（p25/p50/p75/mean 偏移量）
  Step 3: 小样本复核止损 0.004 结论在修复后是否稳健

用法：
  # 全量三步验证
  python archive/diag/diag_stepJ0_vwap_fix_verification.py --sample 30 --start 2024-01-01 --end 2026-07-22

  # 仅步骤2（vwap_dev分布，快速）
  python archive/diag/diag_stepJ0_vwap_fix_verification.py --step 2 --sample 30

  # 仅步骤3（止损网格，固定小样本）
  python archive/diag/diag_stepJ0_vwap_fix_verification.py --step 3 --sample 10
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0 import features as features_module
from at0 import backtest as backtest_module
from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock
from at0.strategy import SignalParams
from at0.risk import RiskParams
from at0.config import (
    load_signal_params,
    load_risk_params,
    load_backtest_params,
    load_screener_params,
)
from at0.cli import adapt_params_by_frequency, BACKTEST_OUTPUT_DIR


# ═══════════════════════════════════════════════════════════════
# VWAP 实现：旧（buggy）vs 新（fixed）
# ═══════════════════════════════════════════════════════════════

def cumulative_vwap_buggy(bars: list[dict]) -> Optional[float]:
    """
    旧 VWAP（buggy）：amount / volume。

    baostock 的 amount 字段未随 OHLC 同步复权，导致历史数据的 VWAP 严重失真。
    这是 J0 修复前的实现，仅用于 A/B 对比，不应在生产环境使用。
    """
    if not bars:
        return None
    total_amount = 0.0
    total_volume = 0.0
    for b in bars:
        vol = b.get("volume", 0)
        if vol <= 0:
            continue
        amt = b.get("amount", 0)
        if amt <= 0:
            continue
        total_amount += amt
        total_volume += vol
    if total_volume <= 0:
        closes = [b.get("close", 0) for b in bars if b.get("close", 0) > 0]
        return sum(closes) / len(closes) if closes else None
    return total_amount / total_volume


def cumulative_vwap_fixed(bars: list[dict]) -> Optional[float]:
    """
    新 VWAP（fixed）：typical_price × volume / volume。

    typical_price = (high + low + close) / 3
    不依赖 amount 字段，避免复权不同步问题。
    """
    if not bars:
        return None
    total_pv = 0.0
    total_vol = 0.0
    for b in bars:
        vol = b.get("volume", 0)
        if vol <= 0:
            continue
        h = b.get("high", 0)
        l = b.get("low", 0)
        c = b.get("close", 0)
        if h <= 0 and l <= 0 and c <= 0:
            continue
        tp = (h + l + c) / 3.0
        if tp <= 0:
            continue
        total_pv += tp * vol
        total_vol += vol
    if total_vol <= 0:
        closes = [b.get("close", 0) for b in bars if b.get("close", 0) > 0]
        return sum(closes) / len(closes) if closes else None
    return total_pv / total_vol


def patch_vwap(mode: str) -> None:
    """
    切换 VWAP 实现（猴补丁）。

    compute_reference_snapshot 在 features.py 内部调用模块级 cumulative_vwap，
    替换 features_module.cumulative_vwap 即可影响所有调用链。
    backtest_module 也导入了 cumulative_vwap（虽未直接调用），一并替换以防遗漏。
    """
    if mode == "old":
        features_module.cumulative_vwap = cumulative_vwap_buggy
        backtest_module.cumulative_vwap = cumulative_vwap_buggy
    elif mode == "new":
        features_module.cumulative_vwap = cumulative_vwap_fixed
        backtest_module.cumulative_vwap = cumulative_vwap_fixed
    else:
        raise ValueError(f"Unknown VWAP mode: {mode}")


# ═══════════════════════════════════════════════════════════════
# 数据加载（与 diag_stepJ1 一致）
# ═══════════════════════════════════════════════════════════════

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


def calc_60d_amplitude(code: str, start_date: str, lookback: int = 60):
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    daily_bars_raw = data.get("daily_bars", {})
    sorted_dates = sorted(daily_bars_raw.keys())
    amplitudes = []
    for date in sorted_dates:
        if date >= start_date:
            break
        bars = daily_bars_raw[date]
        if not bars:
            continue
        highs = [b["high"] for b in bars if b.get("high", 0) > 0]
        lows = [b["low"] for b in bars if b.get("low", 0) > 0]
        if not highs or not lows:
            continue
        day_high = max(highs)
        day_low = min(lows)
        prev_close = bars[0]["open"]
        if prev_close > 0:
            amplitudes.append((day_high - day_low) / prev_close)
    if len(amplitudes) < lookback:
        return None
    return sum(amplitudes[-lookback:]) / lookback


# ═══════════════════════════════════════════════════════════════
# Step 1: baseline 旧VWAP vs 新VWAP 的 A/B 回测
# ═══════════════════════════════════════════════════════════════

def run_backtest(code: str, start: str, end: str, signal_params: SignalParams,
                 stop_loss_ratio: Optional[float] = None):
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
    bt_params.stop_loss_ratio = stop_loss_ratio if stop_loss_ratio is not None else backtest_params.stop_loss_ratio
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
    return summarize_one_stock(code, result)


def run_step1(codes: list[str], start: str, end: str):
    """Step 1: baseline 旧VWAP vs 新VWAP A/B 回测"""
    print(f"\n{'='*70}")
    print(f"Step 1: baseline 旧VWAP(amount/vol) vs 新VWAP(tp×vol) A/B 回测")
    print(f"{'='*70}")
    print(f"样本: {len(codes)} 只 | 区间: {start} ~ {end}\n")

    # baseline 参数（确保 J2 关闭）
    signal_params = load_signal_params()
    signal_params.retracement_entry_enabled = False  # 显式关闭 J2
    print(f"[INFO] retracement_entry_enabled={signal_params.retracement_entry_enabled} (J2 已关闭)")
    print(f"[INFO] stop_loss_ratio 来自 thresholds.yaml\n")

    # 旧 VWAP 回测
    print("[INFO] 运行旧 VWAP (amount/volume) ...")
    patch_vwap("old")
    old_results = []
    for i, code in enumerate(codes):
        try:
            r = run_backtest(code, start, end, signal_params)
            if r:
                old_results.append(r)
                print(f"  [{i+1}/{len(codes)}] {code}: 配对={r.get('paired_trades',0)} "
                      f"胜率={r.get('win_rate',0)*100:.1f}% 净盈亏={r.get('net_pnl_with_unrealized',0):+.0f} "
                      f"payoff={r.get('payoff_ratio',0):.3f}")
        except Exception as e:
            print(f"  [{i+1}/{len(codes)}] {code}: ERROR - {e}")

    # 新 VWAP 回测
    print(f"\n[INFO] 运行新 VWAP (typical_price×volume) ...")
    patch_vwap("new")
    new_results = []
    for i, code in enumerate(codes):
        try:
            r = run_backtest(code, start, end, signal_params)
            if r:
                new_results.append(r)
                print(f"  [{i+1}/{len(codes)}] {code}: 配对={r.get('paired_trades',0)} "
                      f"胜率={r.get('win_rate',0)*100:.1f}% 净盈亏={r.get('net_pnl_with_unrealized',0):+.0f} "
                      f"payoff={r.get('payoff_ratio',0):.3f}")
        except Exception as e:
            print(f"  [{i+1}/{len(codes)}] {code}: ERROR - {e}")

    # 汇总对比
    def aggregate(results):
        if not results:
            return {}
        total_paired = sum(r.get("paired_trades", 0) for r in results)
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

    old_agg = aggregate(old_results)
    new_agg = aggregate(new_results)

    print(f"\n{'='*70}")
    print(f"Step 1 汇总对比")
    print(f"{'='*70}")
    print(f"\n{'指标':<25} {'旧VWAP(buggy)':>18} {'新VWAP(fixed)':>18} {'变化':>18}")
    print(f"{'-'*70}")
    print(f"{'样本股票数':<25} {old_agg.get('stocks',0):>18} {new_agg.get('stocks',0):>18}")
    print(f"{'总配对数':<25} {old_agg.get('paired_trades',0):>18} {new_agg.get('paired_trades',0):>18} {new_agg.get('paired_trades',0)-old_agg.get('paired_trades',0):>+18}")
    print(f"{'胜率':<25} {old_agg.get('win_rate',0)*100:>17.2f}% {new_agg.get('win_rate',0)*100:>17.2f}% {(new_agg.get('win_rate',0)-old_agg.get('win_rate',0))*100:>+17.2f}%")
    print(f"{'avg_win':<25} {old_agg.get('avg_win',0):>+18.0f} {new_agg.get('avg_win',0):>+18.0f} {new_agg.get('avg_win',0)-old_agg.get('avg_win',0):>+18.0f}")
    print(f"{'avg_loss':<25} {old_agg.get('avg_loss',0):>+18.0f} {new_agg.get('avg_loss',0):>+18.0f} {new_agg.get('avg_loss',0)-old_agg.get('avg_loss',0):>+18.0f}")
    print(f"{'payoff_ratio':<25} {old_agg.get('payoff_ratio',0):>18.4f} {new_agg.get('payoff_ratio',0):>18.4f} {new_agg.get('payoff_ratio',0)-old_agg.get('payoff_ratio',0):>+18.4f}")
    print(f"{'毛盈亏':<25} {old_agg.get('gross_pnl',0):>+18.0f} {new_agg.get('gross_pnl',0):>+18.0f} {new_agg.get('gross_pnl',0)-old_agg.get('gross_pnl',0):>+18.0f}")
    print(f"{'总成本':<25} {old_agg.get('total_cost',0):>+18.0f} {new_agg.get('total_cost',0):>+18.0f} {new_agg.get('total_cost',0)-old_agg.get('total_cost',0):>+18.0f}")
    print(f"{'成本/毛利比':<25} {old_agg.get('cost_gross_ratio',0)*100:>17.2f}% {new_agg.get('cost_gross_ratio',0)*100:>17.2f}% {(new_agg.get('cost_gross_ratio',0)-old_agg.get('cost_gross_ratio',0))*100:>+17.2f}%")
    print(f"{'净盈亏(含浮盈)':<25} {old_agg.get('net_pnl',0):>+18.0f} {new_agg.get('net_pnl',0):>+18.0f} {new_agg.get('net_pnl',0)-old_agg.get('net_pnl',0):>+18.0f}")
    print(f"{'盈利股票数':<25} {old_agg.get('profitable_stocks',0):>18} {new_agg.get('profitable_stocks',0):>18}")

    # 逐股对比
    print(f"\n{'='*70}")
    print(f"逐股对比")
    print(f"{'='*70}")
    print(f"\n{'代码':<10} {'旧-配对':>8} {'新-配对':>8} {'旧-净盈亏':>12} {'新-净盈亏':>12} {'变化':>12} {'旧-payoff':>10} {'新-payoff':>10}")
    print(f"{'-'*82}")
    for o, n in zip(old_results, new_results):
        code = o["code"]
        op = o.get("paired_trades", 0)
        np_ = n.get("paired_trades", 0)
        on = o.get("net_pnl_with_unrealized", 0)
        nn = n.get("net_pnl_with_unrealized", 0)
        opr = o.get("payoff_ratio", 0)
        npr = n.get("payoff_ratio", 0)
        print(f"{code:<10} {op:>8} {np_:>8} {on:>+12.0f} {nn:>+12.0f} {nn-on:>+12.0f} {opr:>10.3f} {npr:>10.3f}")

    return {"old": old_agg, "new": new_agg, "old_per_stock": old_results, "new_per_stock": new_results}


# ═══════════════════════════════════════════════════════════════
# Step 2: vwap_dev 字段修复前后数值差异分布
# ═══════════════════════════════════════════════════════════════

def percentile(data, p):
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def run_step2(codes: list[str], start: str, end: str):
    """Step 2: 量化 vwap_dev 修复前后的数值差异分布"""
    print(f"\n{'='*70}")
    print(f"Step 2: vwap_dev 字段修复前后数值差异分布")
    print(f"{'='*70}")
    print(f"样本: {len(codes)} 只 | 区间: {start} ~ {end}\n")

    old_devs = []
    new_devs = []
    deltas = []  # new - old
    sign_flips = 0  # 符号反转数
    magnitude_ratio_gt3x = 0  # 新旧幅度差超3倍
    total_samples = 0

    for i, code in enumerate(codes):
        data = load_zz500_data(code, start, end)
        if not data:
            continue
        daily_bars, _ = data
        for date, bars in daily_bars.items():
            if not bars:
                continue
            # 在每日的第 12、24、36、48 根 K 线处采样（5min 数据，对应 10:30/11:30/14:00/15:00）
            for idx in [11, 23, 35, 47]:
                if idx >= len(bars):
                    break
                bars_up_to = bars[:idx + 1]
                close = bars_up_to[-1]["close"]
                old_vwap = cumulative_vwap_buggy(bars_up_to)
                new_vwap = cumulative_vwap_fixed(bars_up_to)
                if old_vwap is None or new_vwap is None or old_vwap <= 0 or new_vwap <= 0:
                    continue
                old_dev = (close - old_vwap) / old_vwap
                new_dev = (close - new_vwap) / new_vwap
                old_devs.append(old_dev)
                new_devs.append(new_dev)
                deltas.append(new_dev - old_dev)
                total_samples += 1
                # 符号反转检测
                if old_dev * new_dev < 0:
                    sign_flips += 1
                # 幅度差超3倍（排除两者都接近0的情况）
                if abs(old_dev) > 0.001 and abs(new_dev) > 0.001:
                    ratio = max(abs(old_dev), abs(new_dev)) / min(abs(old_dev), abs(new_dev))
                    if ratio > 3:
                        magnitude_ratio_gt3x += 1
        print(f"  [{i+1}/{len(codes)}] {code}: 采样完成")

    if not old_devs:
        print("[ERROR] 无有效数据")
        return None

    # 统计
    old_stats = {
        "mean": sum(old_devs) / len(old_devs),
        "p25": percentile(old_devs, 0.25),
        "p50": percentile(old_devs, 0.5),
        "p75": percentile(old_devs, 0.75),
        "min": min(old_devs),
        "max": max(old_devs),
    }
    new_stats = {
        "mean": sum(new_devs) / len(new_devs),
        "p25": percentile(new_devs, 0.25),
        "p50": percentile(new_devs, 0.5),
        "p75": percentile(new_devs, 0.75),
        "min": min(new_devs),
        "max": max(new_devs),
    }
    delta_stats = {
        "mean": sum(deltas) / len(deltas),
        "p25": percentile(deltas, 0.25),
        "p50": percentile(deltas, 0.5),
        "p75": percentile(deltas, 0.75),
        "min": min(deltas),
        "max": max(deltas),
    }

    print(f"\n{'='*70}")
    print(f"Step 2 汇总：vwap_dev 分布对比（共 {total_samples} 个采样点）")
    print(f"{'='*70}")
    print(f"\n{'统计量':<15} {'旧VWAP(buggy)':>18} {'新VWAP(fixed)':>18} {'差值(new-old)':>18}")
    print(f"{'-'*70}")
    for key in ["mean", "p25", "p50", "p75", "min", "max"]:
        print(f"{key:<15} {old_stats[key]*100:>17.2f}% {new_stats[key]*100:>17.2f}% {delta_stats[key]*100:>+17.2f}%")

    print(f"\n{'='*70}")
    print(f"关键失真指标")
    print(f"{'='*70}")
    print(f"  符号反转数: {sign_flips}/{total_samples} ({sign_flips/total_samples*100:.1f}%)")
    print(f"    （旧vwap_dev正/负 → 新vwap_dev负/正，信号方向完全相反）")
    print(f"  幅度差超3倍: {magnitude_ratio_gt3x}/{total_samples} ({magnitude_ratio_gt3x/total_samples*100:.1f}%)")
    print(f"    （排除两者都<0.1%的情况）")

    # 按 vwap_dev 绝对值分桶统计（看不同偏离区的失真程度）
    print(f"\n{'='*70}")
    print(f"按新vwap_dev绝对值分桶的失真程度")
    print(f"{'='*70}")
    buckets = [(0, 0.005), (0.005, 0.01), (0.01, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, float("inf"))]
    print(f"\n{'新|vwap_dev|区间':<20} {'样本数':>8} {'旧|vwap_dev|均值':>18} {'新|vwap_dev|均值':>18} {'均值比(旧/新)':>15} {'符号反转率':>12}")
    print(f"{'-'*90}")
    for lo, hi in buckets:
        bucket_old = []
        bucket_new = []
        bucket_flips = 0
        for o, n in zip(old_devs, new_devs):
            if lo <= abs(n) < hi:
                bucket_old.append(abs(o))
                bucket_new.append(abs(n))
                if o * n < 0:
                    bucket_flips += 1
        if bucket_old:
            old_mean = sum(bucket_old) / len(bucket_old)
            new_mean = sum(bucket_new) / len(bucket_new)
            ratio = old_mean / new_mean if new_mean > 0 else float("inf")
            flip_rate = bucket_flips / len(bucket_old)
            hi_str = f"{hi*100:.1f}%" if hi != float("inf") else "inf"
            print(f"[{lo*100:.1f}%, {hi_str}){'':<5} {len(bucket_old):>8} {old_mean*100:>17.2f}% {new_mean*100:>17.2f}% {ratio:>15.2f} {flip_rate*100:>11.1f}%")

    return {
        "total_samples": total_samples,
        "old_stats": {k: round(v, 6) for k, v in old_stats.items()},
        "new_stats": {k: round(v, 6) for k, v in new_stats.items()},
        "delta_stats": {k: round(v, 6) for k, v in delta_stats.items()},
        "sign_flips": sign_flips,
        "sign_flip_rate": round(sign_flips / total_samples, 4),
        "magnitude_ratio_gt3x": magnitude_ratio_gt3x,
        "magnitude_ratio_gt3x_rate": round(magnitude_ratio_gt3x / total_samples, 4),
    }


# ═══════════════════════════════════════════════════════════════
# Step 3: 小样本复核止损 0.004 结论
# ═══════════════════════════════════════════════════════════════

def run_step3(codes: list[str], start: str, end: str):
    """Step 3: 小样本复核止损最优值（0.002/0.004/0.008）在新VWAP下是否稳健"""
    print(f"\n{'='*70}")
    print(f"Step 3: 止损最优值在新VWAP下的稳健性复核")
    print(f"{'='*70}")
    print(f"样本: {len(codes)} 只 | 区间: {start} ~ {end}")
    print(f"止损网格: [0.002, 0.004, 0.008]\n")

    # 确保用新 VWAP
    patch_vwap("new")
    print("[INFO] VWAP 模式: new (fixed)\n")

    signal_params = load_signal_params()
    signal_params.retracement_entry_enabled = False  # 显式关闭 J2

    stop_loss_grid = [0.002, 0.004, 0.008]
    grid_results = {sl: [] for sl in stop_loss_grid}

    for i, code in enumerate(codes):
        for sl in stop_loss_grid:
            try:
                r = run_backtest(code, start, end, signal_params, stop_loss_ratio=sl)
                if r:
                    grid_results[sl].append(r)
            except Exception as e:
                print(f"  [{i+1}/{len(codes)}] {code} sl={sl}: ERROR - {e}")
        print(f"  [{i+1}/{len(codes)}] {code}: " + " | ".join(
            f"sl={sl} 净={sum(r.get('net_pnl_with_unrealized',0) for r in grid_results[sl][-1:]) if grid_results[sl] else 0:+.0f}"
            for sl in stop_loss_grid
        ))

    # 汇总
    print(f"\n{'='*70}")
    print(f"Step 3 汇总：止损网格对比（新VWAP）")
    print(f"{'='*70}")
    print(f"\n{'指标':<20} {'sl=0.002':>15} {'sl=0.004':>15} {'sl=0.008':>15}")
    print(f"{'-'*65}")

    agg_by_sl = {}
    for sl in stop_loss_grid:
        results = grid_results[sl]
        if not results:
            agg_by_sl[sl] = {}
            continue
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
        agg_by_sl[sl] = {
            "paired_trades": total_paired,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "payoff_ratio": round(payoff, 4),
            "net_pnl": round(total_net, 2),
            "profitable_stocks": sum(1 for r in results if r.get("net_pnl_with_unrealized", 0) > 0),
        }

    for key, label in [("paired_trades", "总配对数"), ("win_rate", "胜率"), ("avg_win", "avg_win"),
                       ("avg_loss", "avg_loss"), ("payoff_ratio", "payoff_ratio"), ("net_pnl", "净盈亏"),
                       ("profitable_stocks", "盈利股票数")]:
        vals = []
        for sl in stop_loss_grid:
            v = agg_by_sl.get(sl, {}).get(key, 0)
            if key == "win_rate":
                vals.append(f"{v*100:.2f}%")
            elif key in ("avg_win", "avg_loss", "net_pnl"):
                vals.append(f"{v:+.0f}")
            elif key == "payoff_ratio":
                vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        print(f"{label:<20} {vals[0]:>15} {vals[1]:>15} {vals[2]:>15}")

    # 找最优
    best_sl = max(stop_loss_grid, key=lambda sl: agg_by_sl.get(sl, {}).get("net_pnl", float("-inf")))
    print(f"\n[结论] 新VWAP下净盈亏最优止损: {best_sl}")
    print(f"  原结论: 0.004（Stage G 定案）")
    print(f"  新结论: {best_sl}")
    if best_sl == 0.004:
        print(f"  ✓ 止损 0.004 结论在修复后依然稳健")
    else:
        print(f"  ✗ 止损最优值发生变化，需扩大样本复核")

    return {"agg_by_sl": agg_by_sl, "best_sl": best_sl, "grid_results": grid_results}


# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Stage J0 — VWAP 修复独立隔离验证")
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2026-07-22")
    parser.add_argument("--step", type=int, default=0, help="只跑指定步骤(1/2/3)，0=全部")
    args = parser.parse_args()

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    print(f"[INFO] 找到 {len(all_codes)} 只股票")

    screener_params = load_screener_params()
    min_amp = screener_params.min_amplitude_long
    print(f"[INFO] 振幅筛选: min_amplitude_long={min_amp}")
    filtered_codes = [c for c in all_codes
                      if (lambda a: a is not None and a >= min_amp)(calc_60d_amplitude(c, args.start))]
    print(f"[INFO] 筛选后: {len(filtered_codes)} 只")

    random.seed(args.seed)
    sample_size = min(args.sample, len(filtered_codes))
    codes = random.sample(filtered_codes, sample_size)
    print(f"[INFO] 抽样 {len(codes)} 只 (seed={args.seed})")
    print(f"[INFO] 区间: {args.start} ~ {args.end}")

    output = {
        "stage": "J0_vwap_fix_verification",
        "start": args.start,
        "end": args.end,
        "sample_size": len(codes),
        "codes": codes,
        "step1": None,
        "step2": None,
        "step3": None,
    }

    start_time = time.time()

    # Step 1
    if args.step == 0 or args.step == 1:
        step1_result = run_step1(codes, args.start, args.end)
        output["step1"] = step1_result

    # Step 2
    if args.step == 0 or args.step == 2:
        step2_result = run_step2(codes, args.start, args.end)
        output["step2"] = step2_result

    # Step 3（用更小的样本，因为要跑3个止损值）
    if args.step == 0 or args.step == 3:
        step3_codes = codes[:min(10, len(codes))]
        step3_result = run_step3(step3_codes, args.start, args.end)
        output["step3"] = step3_result

    elapsed = time.time() - start_time
    print(f"\n[INFO] 全部完成，耗时 {elapsed:.1f}s")

    # 保存
    out_dir = BACKTEST_OUTPUT_DIR
    out_path = out_dir / "diag_J0_vwap_fix_verification.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 移除 per_stock 中的非序列化字段（保留汇总）
    save_output = {
        "stage": output["stage"],
        "start": output["start"],
        "end": output["end"],
        "sample_size": output["sample_size"],
        "codes": output["codes"],
        "step1": output["step1"].get("old") if output.get("step1") else None,
        "step1_new": output["step1"].get("new") if output.get("step1") else None,
        "step2": output.get("step2"),
        "step3": {k: v for k, v in (output.get("step3") or {}).items() if k != "grid_results"},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(save_output, f, ensure_ascii=False, indent=2, default=str)
    print(f"结果已保存: {out_path}")


if __name__ == "__main__":
    main()
