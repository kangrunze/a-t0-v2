#!/usr/bin/env python3
"""
Stage J0+ — 止损网格搜索（新VWAP基线，100股×3年）

背景：J0 修复了 VWAP 计算bug后，Step3 小样本（10股）复核发现止损最优值
从 0.004 变为 0.002。但 10 股样本量远小于建立 0.004 结论时的规模
（100股×3年，cd24/mh24）。本轮在同等规模上重新做完整止损网格搜索，
覆盖比之前更宽的范围。

基线配置：
  - 新VWAP（已修复，typical_price×volume）
  - 振幅筛选 0.0494（回测窗口内前60天，与 backtest_zz500.py 一致）
  - cooldown_bars=24（cd24）
  - max_holding_bars=24（mh24，adapt后重新应用）
  - J2 回踩入场关闭（retracement_entry_enabled=False）
  - 其他参数从 thresholds.yaml 加载

止损网格：0.002 / 0.003 / 0.004 / 0.006 / 0.008 / 0.01 / 0.015 / 0.02

产出指标：win_rate / avg_win / avg_loss / payoff_ratio / 净盈亏 / 成本毛利比 / 配对率

用法：
  python archive/diag/diag_stop_loss_grid_100stocks.py --sample 100 --seed 42 \
      --start 2023-07-25 --end 2026-07-22

  # 快速小样本预跑（确认脚本可用）
  python archive/diag/diag_stop_loss_grid_100stocks.py --sample 10 --seed 42
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
# 振幅筛选（与 backtest_zz500.py filter_codes_by_amplitude 一致）
# ═══════════════════════════════════════════════════════════════

def filter_codes_by_amplitude(codes: list[str], data_dir: Path,
                               start_date: str, end_date: str,
                               threshold: float, window: int = 60) -> list[str]:
    """
    按 60 日日均振幅过滤股票池（与 backtest_zz500.py L581-632 一致）。

    振幅口径：(high - low) / prev_close
    窗口：回测期前 window 个交易日（本地数据从 start_date 开始，无更早数据）
    """
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
# 数据加载（与 backtest_zz500.py load_multi_day_zz500 一致）
# ═══════════════════════════════════════════════════════════════

def load_zz500_data(code: str, start: str, end: str):
    """加载单股多日5min数据，返回 (daily_bars, daily_prev_closes)。"""
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
# 构建回测参数（cd24/mh24，adapt后重新应用mh24）
# ═══════════════════════════════════════════════════════════════

def build_params(stop_loss_ratio: float) -> BacktestParams:
    """
    构建回测参数：
    - 新VWAP（features.py 已修复，不需要猴补丁）
    - J2 关闭
    - cd24（cooldown_bars=24）
    - mh24（max_holding_bars=24，adapt后重新应用）
    - stop_loss_ratio 由参数传入
    - 其他参数从 thresholds.yaml 加载
    """
    sp = load_signal_params()
    sp.retracement_entry_enabled = False  # 显式关闭 J2

    rp = load_risk_params()
    bp = load_backtest_params()

    params = BacktestParams(
        base_shares=3000,
        signal_params=sp,
        risk_params=rp,
    )
    # 从 thresholds.yaml 加载的默认值
    params.stop_loss_ratio = stop_loss_ratio
    params.trailing_ratio = bp.trailing_ratio
    params.trailing_activation_pct = bp.trailing_activation_pct
    params.cooldown_bars = 24          # cd24
    params.max_holding_bars = 24       # mh24（adapt后会被改成12，需重新应用）

    # 先按频率适配 warmup/eod
    params = adapt_params_by_frequency(params, "5min", 48)
    # 适配后重新应用 mh24（避免被 adapt 覆盖，与 backtest_zz500.py L318-324 一致）
    params = _replace(params, max_holding_bars=24)
    if params.exposure_policy is not None:
        params.exposure_policy.max_holding_bars = 24

    return params


# ═══════════════════════════════════════════════════════════════
# 单股回测（复用已加载的数据）
# ═══════════════════════════════════════════════════════════════

def run_single_backtest(code: str, daily_bars: dict, daily_prev_closes: dict,
                        params: BacktestParams) -> dict:
    """用已加载的数据跑单股回测，返回 summarize_one_stock 结果。"""
    result = backtest_multi_day(
        code=code,
        daily_bars=daily_bars,
        daily_prev_closes=daily_prev_closes,
        params=params,
    )
    return summarize_one_stock(code, result)


# ═══════════════════════════════════════════════════════════════
# 聚合
# ═══════════════════════════════════════════════════════════════

def aggregate(results: list[dict]) -> dict:
    """聚合100股结果，产出完整指标。"""
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
    parser = argparse.ArgumentParser(description="Stage J0+ — 止损网格搜索（新VWAP，100股×3年）")
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2023-07-25")
    parser.add_argument("--end", type=str, default="2026-07-22")
    args = parser.parse_args()

    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    print(f"[INFO] 找到 {len(all_codes)} 只股票")

    screener_params = load_screener_params()
    min_amp = screener_params.min_amplitude_long
    print(f"[INFO] 振幅筛选: min_amplitude_long={min_amp}（回测窗口内前60天）")

    # 振幅筛选
    print(f"[INFO] 正在筛选...")
    filtered_codes = filter_codes_by_amplitude(
        all_codes, ZZ500_5MIN_DIR, args.start, args.end, min_amp
    )
    print(f"[INFO] 筛选后: {len(filtered_codes)} 只")

    # 抽样
    random.seed(args.seed)
    sample_size = min(args.sample, len(filtered_codes))
    codes = random.sample(filtered_codes, sample_size)
    print(f"[INFO] 抽样 {len(codes)} 只 (seed={args.seed})")
    print(f"[INFO] 区间: {args.start} ~ {args.end}")

    # 止损网格
    stop_loss_grid = [0.002, 0.003, 0.004, 0.006, 0.008, 0.01, 0.015, 0.02]
    print(f"[INFO] 止损网格: {stop_loss_grid}")
    print(f"[INFO] cd24/mh24, J2关闭, 新VWAP(已修复)\n")

    # 一次性加载所有股票数据（避免重复IO）
    print(f"[INFO] 开始加载数据...")
    load_start = time.time()
    stock_data = {}  # code -> (daily_bars, daily_prev_closes)
    for i, code in enumerate(codes):
        data = load_zz500_data(code, args.start, args.end)
        if data and data[0]:
            stock_data[code] = data
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(codes)}] 已加载 {len(stock_data)} 只")
    load_time = time.time() - load_start
    print(f"[INFO] 数据加载完成: {len(stock_data)}/{len(codes)} 只, 耗时 {load_time:.1f}s\n")

    # 网格搜索
    grid_results = {}
    total_start = time.time()

    for sl_idx, sl in enumerate(stop_loss_grid):
        print(f"\n{'='*70}")
        print(f"[{sl_idx+1}/{len(stop_loss_grid)}] 止损={sl} ({sl*100:.1f}%)")
        print(f"{'='*70}")

        params = build_params(sl)
        sl_results = []
        sl_start = time.time()

        for i, code in enumerate(codes):
            if code not in stock_data:
                continue
            try:
                daily_bars, daily_prev_closes = stock_data[code]
                r = run_single_backtest(code, daily_bars, daily_prev_closes, params)
                sl_results.append(r)
            except Exception as e:
                print(f"  [{i+1}/{len(codes)}] {code}: ERROR - {e}")

            if (i + 1) % 20 == 0:
                elapsed = time.time() - sl_start
                eta = elapsed / (i + 1) * (len(codes) - i - 1)
                print(f"  [{i+1}/{len(codes)}] 已完成, 用时 {elapsed:.0f}s, ETA {eta:.0f}s")

        sl_elapsed = time.time() - sl_start
        agg = aggregate(sl_results)
        grid_results[sl] = {
            "agg": agg,
            "per_stock": sl_results,
        }

        print(f"\n  止损={sl} 完成 ({sl_elapsed:.0f}s): "
              f"配对={agg.get('paired_trades',0)} "
              f"胜率={agg.get('win_rate',0)*100:.1f}% "
              f"payoff={agg.get('payoff_ratio',0):.4f} "
              f"净盈亏={agg.get('net_pnl',0):+.0f} "
              f"成本毛利比={agg.get('cost_gross_ratio',0)*100:.1f}% "
              f"盈利={agg.get('profitable_stocks',0)}/{agg.get('stocks',0)}")

    total_elapsed = time.time() - total_start
    print(f"\n\n[INFO] 全部网格搜索完成, 总耗时 {total_elapsed:.0f}s ({total_elapsed/60:.1f}分钟)")

    # 汇总对比表
    print(f"\n{'='*100}")
    print(f"止损网格搜索结果汇总（新VWAP, 100股×3年, cd24/mh24, 振幅筛选0.0494）")
    print(f"{'='*100}")
    print(f"\n{'止损':>8} {'配对数':>8} {'配对率':>8} {'胜率':>8} {'avg_win':>10} {'avg_loss':>10} {'payoff':>8} {'毛盈亏':>12} {'总成本':>10} {'成本毛利比':>10} {'净盈亏':>12} {'盈利':>6}")
    print(f"{'-'*100}")
    for sl in stop_loss_grid:
        agg = grid_results[sl]["agg"]
        print(f"{sl*100:>7.1f}% {agg.get('paired_trades',0):>8} {agg.get('pair_rate',0)*100:>7.1f}% {agg.get('win_rate',0)*100:>7.1f}% "
              f"{agg.get('avg_win',0):>+10.0f} {agg.get('avg_loss',0):>+10.0f} {agg.get('payoff_ratio',0):>8.4f} "
              f"{agg.get('gross_pnl',0):>+12.0f} {agg.get('total_cost',0):>+10.0f} {agg.get('cost_gross_ratio',0)*100:>9.1f}% "
              f"{agg.get('net_pnl',0):>+12.0f} {agg.get('profitable_stocks',0):>3}/{agg.get('stocks',0)}")

    # 找最优
    best_sl = max(stop_loss_grid, key=lambda sl: grid_results[sl]["agg"].get("net_pnl", float("-inf")))
    print(f"\n[结论] 净盈亏最优止损: {best_sl} ({best_sl*100:.1f}%)")
    print(f"  净盈亏: {grid_results[best_sl]['agg']['net_pnl']:+.0f}")
    print(f"  payoff: {grid_results[best_sl]['agg']['payoff_ratio']:.4f}")
    print(f"  成本毛利比: {grid_results[best_sl]['agg']['cost_gross_ratio']*100:.1f}%")
    print(f"  原定案: 0.003（thresholds.yaml 当前值）")
    print(f"  新最优: {best_sl}")
    if best_sl == 0.003:
        print(f"  ✓ 止损 0.003 结论在修复后依然稳健")
    else:
        print(f"  ⚠ 止损最优值发生变化，需审视（不自动写入yaml）")

    # 保存结果
    output = {
        "stage": "J0+_stop_loss_grid",
        "description": "止损网格搜索（新VWAP基线, 100股×3年, cd24/mh24）",
        "start": args.start,
        "end": args.end,
        "sample_size": len(stock_data),
        "config": {
            "vwap": "fixed (typical_price×volume)",
            "amplitude_filter": min_amp,
            "cooldown_bars": 24,
            "max_holding_bars": 24,
            "j2_retracement_entry": False,
        },
        "stop_loss_grid": stop_loss_grid,
        "results": {str(sl): grid_results[sl]["agg"] for sl in stop_loss_grid},
        "best_sl": best_sl,
        "total_elapsed_sec": round(total_elapsed, 1),
    }
    out_dir = BACKTEST_OUTPUT_DIR
    out_path = out_dir / "diag_J0_stop_loss_grid_100stocks.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
