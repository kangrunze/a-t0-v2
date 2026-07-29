#!/usr/bin/env python3
"""
Stage J0++ — 通用参数网格搜索（新VWAP基线）

在止损0.002（已确认最优）的基础上，对其他参数做网格搜索。
数据只加载一次，支持任意参数的网格。

支持参数：
  --trailing_activation_pct 0,0.003,0.005,0.008
  --trailing_ratio 0.3,0.4,0.5,0.6,0.7
  --cooldown_bars 12,24,36
  --max_holding_bars 12,24,36
  --tf_adx_threshold 30,35,40
  --min_capture_spread 0.004,0.006,0.008

用法：
  python archive/diag/diag_param_grid.py --param trailing_activation_pct --values 0,0.003,0.005,0.008
  python archive/diag/diag_param_grid.py --param trailing_ratio --values 0.3,0.4,0.5,0.6,0.7
  python archive/diag/diag_param_grid.py --param cooldown_bars --values 12,24,36
  python archive/diag/diag_param_grid.py --param max_holding_bars --values 12,24,36
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
from at0.config import (
    load_signal_params,
    load_risk_params,
    load_backtest_params,
    load_screener_params,
)
from at0.cli import adapt_params_by_frequency, BACKTEST_OUTPUT_DIR


# ═══════════════════════════════════════════════════════════════
# 振幅筛选（与 backtest_zz500.py 一致）
# ═══════════════════════════════════════════════════════════════

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
        in_range_dates = [dt for dt in sorted_dates if start_date <= dt <= end_date]
        use_dates = in_range_dates[:window] if len(in_range_dates) >= window else in_range_dates
        amps = []
        prev_close = None
        for date in use_dates:
            bars = daily_bars[date]
            if not bars:
                continue
            high = max(b["high"] for b in bars)
            low = min(b["low"] for b in bars)
            if prev_close and prev_close > 0:
                amps.append((high - low) / prev_close)
            prev_close = bars[-1]["close"]
        if not amps:
            continue
        avg_amp = sum(amps) / len(amps)
        if avg_amp >= threshold:
            passed.append(code)
    return passed


# ═══════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# 构建参数（新VWAP, sl=0.002, cd24/mh24, J2关闭）
# ═══════════════════════════════════════════════════════════════

def build_params(param_name=None, param_value=None):
    """
    构建回测参数。
    基线：sl=0.002, cd24, mh24, J2关闭, 新VWAP
    可通过 param_name/param_value 覆盖单个参数。
    """
    sp = load_signal_params()
    sp.retracement_entry_enabled = False  # J2关闭

    rp = load_risk_params()
    bp = load_backtest_params()

    # 默认值（sl=0.002是J0+网格搜索确认的最优）
    stop_loss_ratio = 0.002
    trailing_ratio = bp.trailing_ratio
    trailing_activation_pct = bp.trailing_activation_pct
    cooldown_bars = 24
    max_holding_bars = 24
    tf_adx_threshold = sp.tf_adx_threshold
    min_capture_spread = rp.min_capture_spread

    # 应用参数覆盖
    if param_name == "trailing_activation_pct":
        trailing_activation_pct = param_value
    elif param_name == "trailing_ratio":
        trailing_ratio = param_value
    elif param_name == "cooldown_bars":
        cooldown_bars = param_value
    elif param_name == "max_holding_bars":
        max_holding_bars = param_value
    elif param_name == "tf_adx_threshold":
        tf_adx_threshold = param_value
    elif param_name == "min_capture_spread":
        min_capture_spread = param_value
    elif param_name == "stop_loss_ratio":
        stop_loss_ratio = param_value

    # 应用到signal_params
    sp.tf_adx_threshold = tf_adx_threshold

    # 应用到risk_params
    rp.min_capture_spread = min_capture_spread

    params = BacktestParams(
        base_shares=3000,
        signal_params=sp,
        risk_params=rp,
    )
    params.stop_loss_ratio = stop_loss_ratio
    params.trailing_ratio = trailing_ratio
    params.trailing_activation_pct = trailing_activation_pct
    params.cooldown_bars = cooldown_bars
    params.max_holding_bars = max_holding_bars

    # 先按频率适配 warmup/eod
    params = adapt_params_by_frequency(params, "5min", 48)
    # 适配后重新应用 mh24（避免被 adapt 覆盖）
    if param_name == "max_holding_bars":
        params = _replace(params, max_holding_bars=param_value)
        if params.exposure_policy is not None:
            params.exposure_policy.max_holding_bars = param_value
    else:
        params = _replace(params, max_holding_bars=max_holding_bars)
        if params.exposure_policy is not None:
            params.exposure_policy.max_holding_bars = max_holding_bars

    return params


# ═══════════════════════════════════════════════════════════════
# 聚合
# ═══════════════════════════════════════════════════════════════

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
    pair_rate = total_paired / total_trades if total_trades else 0
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
        "pair_rate": round(pair_rate, 4),
        "profitable_stocks": profitable,
    }


# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Stage J0++ — 通用参数网格搜索")
    parser.add_argument("--param", type=str, required=True,
                        help="参数名: trailing_activation_pct/trailing_ratio/cooldown_bars/max_holding_bars/tf_adx_threshold/min_capture_spread")
    parser.add_argument("--values", type=str, required=True,
                        help="逗号分隔的值列表，如 0,0.003,0.005,0.008")
    parser.add_argument("--sample", type=int, default=36)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2023-07-25")
    parser.add_argument("--end", type=str, default="2026-07-22")
    args = parser.parse_args()

    # 解析参数值
    values = []
    for v in args.values.split(","):
        v = v.strip()
        try:
            values.append(int(v))
        except ValueError:
            try:
                values.append(float(v))
            except ValueError:
                values.append(v)

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    print(f"[INFO] 找到 {len(all_codes)} 只股票")

    screener_params = load_screener_params()
    min_amp = screener_params.min_amplitude_long
    print(f"[INFO] 振幅筛选: min_amplitude_long={min_amp}")

    filtered_codes = filter_codes_by_amplitude(
        all_codes, ZZ500_5MIN_DIR, args.start, args.end, min_amp
    )
    print(f"[INFO] 筛选后: {len(filtered_codes)} 只")

    random.seed(args.seed)
    sample_size = min(args.sample, len(filtered_codes))
    codes = random.sample(filtered_codes, sample_size)
    print(f"[INFO] 抽样 {len(codes)} 只 (seed={args.seed})")
    print(f"[INFO] 区间: {args.start} ~ {args.end}")
    print(f"[INFO] 参数: {args.param}")
    print(f"[INFO] 网格值: {values}")
    print(f"[INFO] 基线: sl=0.002, cd24/mh24, J2关闭, 新VWAP\n")

    # 一次性加载数据
    print(f"[INFO] 开始加载数据...")
    stock_data = {}
    for i, code in enumerate(codes):
        data = load_zz500_data(code, args.start, args.end)
        if data and data[0]:
            stock_data[code] = data
    print(f"[INFO] 数据加载完成: {len(stock_data)}/{len(codes)} 只\n")

    # 网格搜索
    grid_results = {}
    total_start = time.time()

    for val_idx, val in enumerate(values):
        print(f"\n{'='*70}")
        print(f"[{val_idx+1}/{len(values)}] {args.param}={val}")
        print(f"{'='*70}")

        params = build_params(args.param, val)
        val_results = []
        val_start = time.time()

        for i, code in enumerate(codes):
            if code not in stock_data:
                continue
            try:
                daily_bars, daily_prev_closes = stock_data[code]
                result = backtest_multi_day(
                    code=code,
                    daily_bars=daily_bars,
                    daily_prev_closes=daily_prev_closes,
                    params=params,
                )
                r = summarize_one_stock(code, result)
                val_results.append(r)
            except Exception as e:
                print(f"  [{i+1}/{len(codes)}] {code}: ERROR - {e}")

            if (i + 1) % 20 == 0:
                elapsed = time.time() - val_start
                eta = elapsed / (i + 1) * (len(codes) - i - 1)
                print(f"  [{i+1}/{len(codes)}] 已完成, 用时 {elapsed:.0f}s, ETA {eta:.0f}s")

        val_elapsed = time.time() - val_start
        agg = aggregate(val_results)
        grid_results[str(val)] = agg

        print(f"\n  {args.param}={val} 完成 ({val_elapsed:.0f}s): "
              f"配对={agg.get('paired_trades',0)} "
              f"胜率={agg.get('win_rate',0)*100:.1f}% "
              f"payoff={agg.get('payoff_ratio',0):.4f} "
              f"净盈亏={agg.get('net_pnl',0):+.0f} "
              f"成本毛利比={agg.get('cost_gross_ratio',0)*100:.1f}% "
              f"盈利={agg.get('profitable_stocks',0)}/{agg.get('stocks',0)}")

    total_elapsed = time.time() - total_start
    print(f"\n\n[INFO] 全部完成, 总耗时 {total_elapsed:.0f}s ({total_elapsed/60:.1f}分钟)")

    # 汇总
    print(f"\n{'='*100}")
    print(f"参数网格搜索结果: {args.param}")
    print(f"{'='*100}")
    print(f"\n{args.param:>15} {'配对数':>8} {'胜率':>8} {'avg_win':>10} {'avg_loss':>10} {'payoff':>8} {'毛盈亏':>12} {'成本毛利比':>10} {'净盈亏':>12} {'盈利':>6}")
    print(f"{'-'*100}")
    for val in values:
        agg = grid_results[str(val)]
        print(f"{str(val):>15} {agg.get('paired_trades',0):>8} {agg.get('win_rate',0)*100:>7.1f}% "
              f"{agg.get('avg_win',0):>+10.0f} {agg.get('avg_loss',0):>+10.0f} {agg.get('payoff_ratio',0):>8.4f} "
              f"{agg.get('gross_pnl',0):>+12.0f} {agg.get('cost_gross_ratio',0)*100:>9.1f}% "
              f"{agg.get('net_pnl',0):>+12.0f} {agg.get('profitable_stocks',0):>3}/{agg.get('stocks',0)}")

    # 找最优
    best_val = max(values, key=lambda v: grid_results[str(v)].get("net_pnl", float("-inf")))
    print(f"\n[结论] {args.param} 净盈亏最优值: {best_val}")
    print(f"  净盈亏: {grid_results[str(best_val)]['net_pnl']:+.0f}")
    print(f"  payoff: {grid_results[str(best_val)]['payoff_ratio']:.4f}")

    # 保存
    output = {
        "stage": "J0++_param_grid",
        "param": args.param,
        "start": args.start,
        "end": args.end,
        "sample_size": len(stock_data),
        "baseline_config": {
            "vwap": "fixed",
            "stop_loss_ratio": 0.002,
            "cooldown_bars": 24,
            "max_holding_bars": 24,
            "j2_retracement_entry": False,
        },
        "grid_values": values,
        "results": grid_results,
        "best_value": best_val,
        "total_elapsed_sec": round(total_elapsed, 1),
    }
    out_dir = BACKTEST_OUTPUT_DIR
    out_path = out_dir / f"diag_J0pp_grid_{args.param}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
