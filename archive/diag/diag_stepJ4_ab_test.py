#!/usr/bin/env python3
"""
Stage J4 — 冲高确认（卖出侧）A/B 验证

与J2对称：在卖出侧加"冲高确认"分离模式。
下跌趋势确认后，等价格冲高到VWAP附近再卖出，而不是在趋势确认那一刻杀跌卖出。

四组对比：
  A) baseline（J2关闭, J4关闭）
  B) J2 only（J2开启, J4关闭）
  C) J4 only（J2关闭, J4开启）
  D) J2+J4（两者都开启）

用法：
  python archive/diag/diag_stepJ4_ab_test.py --sample 36 --seed 42
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


def build_params(j2=False, j4=False, vwap_band=0.02, kdj_max=70.0,
                 surge_vwap_band=0.02, surge_kdj_min=30.0):
    sp = load_signal_params()
    sp.retracement_entry_enabled = j2
    if j2:
        sp.retracement_vwap_band = vwap_band
        sp.retracement_kdj_max = kdj_max
    sp.surge_exit_enabled = j4
    if j4:
        sp.surge_vwap_band = surge_vwap_band
        sp.surge_kdj_min = surge_kdj_min

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
    """配对交易捕获效率：(卖出价-买入价)/(当日最高-当日最低)。"""
    buy_queue = []
    sell_queue = []
    same_day_ce = []

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
                                same_day_ce.append(ce)

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
                                same_day_ce.append(ce)

                    earliest["shares"] -= pair_shares
                    remaining -= pair_shares
                    if earliest["shares"] <= 0:
                        buy_queue.pop(0)
                if remaining > 0:
                    sell_queue.append({"price": price, "date": date, "shares": remaining})

    return same_day_ce


def ce_stats(ce_list):
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


def aggregate(results, ce_lists=None):
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
    out = {
        "stocks": len(results),
        "paired_trades": total_paired,
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
    # 捕获效率（核心指标，与 win_rate/net_pnl 并列）
    if ce_lists:
        all_ce = []
        for cl in ce_lists:
            all_ce.extend(cl)
        out["capture_efficiency"] = ce_stats(all_ce)
    return out


def main():
    parser = argparse.ArgumentParser(description="Stage J4 — 冲高确认A/B验证")
    parser.add_argument("--sample", type=int, default=36)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2023-07-25")
    parser.add_argument("--end", type=str, default="2026-07-22")
    parser.add_argument("--vwap_band", type=float, default=0.02)
    parser.add_argument("--kdj_max", type=float, default=70.0)
    parser.add_argument("--surge_vwap_band", type=float, default=0.02)
    parser.add_argument("--surge_kdj_min", type=float, default=30.0)
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

    # 四组对比
    groups = {
        "A_baseline": {"j2": False, "j4": False},
        "B_j2_only": {"j2": True, "j4": False},
        "C_j4_only": {"j2": False, "j4": True},
        "D_j2_j4": {"j2": True, "j4": True},
    }

    all_results = {}
    total_start = time.time()

    for name, config in groups.items():
        print(f"\n{'='*70}")
        print(f"运行 {name} (J2={'ON' if config['j2'] else 'OFF'}, J4={'ON' if config['j4'] else 'OFF'})")
        print(f"{'='*70}")
        params = build_params(
            j2=config["j2"], j4=config["j4"],
            vwap_band=args.vwap_band, kdj_max=args.kdj_max,
            surge_vwap_band=args.surge_vwap_band, surge_kdj_min=args.surge_kdj_min,
        )
        group_results = []
        group_ce_lists = []
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
                # 捕获效率
                daily_hl = compute_daily_hl(daily_bars)
                ce_list = compute_capture_efficiency(result, daily_hl)
                group_ce_lists.append(ce_list)
            except Exception as e:
                print(f"  [{i+1}] {code}: ERROR - {e}")
            if (i + 1) % 12 == 0:
                print(f"  [{i+1}/{len(codes)}] 完成")
        all_results[name] = aggregate(group_results, ce_lists=group_ce_lists)
        agg = all_results[name]
        ce = agg.get("capture_efficiency", {})
        print(f"  → 配对={agg.get('paired_trades',0)} 胜率={agg.get('win_rate',0)*100:.1f}% "
              f"payoff={agg.get('payoff_ratio',0):.4f} 净盈亏={agg.get('net_pnl',0):+.0f} "
              f"盈利={agg.get('profitable_stocks',0)}/{agg.get('stocks',0)} "
              f"CE均值={ce.get('mean',0)*100:.2f}% CE中位={ce.get('p50',0)*100:.2f}%")

    total_elapsed = time.time() - total_start
    print(f"\n\n总耗时 {total_elapsed:.0f}s ({total_elapsed/60:.1f}分钟)")

    # 汇总对比
    print(f"\n{'='*140}")
    print(f"J4 四组对比结果（{args.sample}股×3年, 新VWAP, sl=0.002/tr=0.2/tap=0/cd24/mh24）")
    print(f"{'='*140}")
    print(f"\n{'组':<15} {'J2':>5} {'J4':>5} {'配对数':>8} {'胜率':>8} {'avg_win':>10} {'avg_loss':>10} {'payoff':>8} {'成本毛利比':>10} {'净盈亏':>12} {'CE均值':>8} {'CE中位':>8} {'盈利':>6}")
    print(f"{'-'*140}")
    for name, config in groups.items():
        agg = all_results[name]
        j2_str = "ON" if config["j2"] else "OFF"
        j4_str = "ON" if config["j4"] else "OFF"
        ce = agg.get("capture_efficiency", {})
        print(f"{name:<15} {j2_str:>5} {j4_str:>5} {agg.get('paired_trades',0):>8} "
              f"{agg.get('win_rate',0)*100:>7.1f}% {agg.get('avg_win',0):>+10.0f} {agg.get('avg_loss',0):>+10.0f} "
              f"{agg.get('payoff_ratio',0):>8.4f} {agg.get('cost_gross_ratio',0)*100:>9.1f}% "
              f"{agg.get('net_pnl',0):>+12.0f} {ce.get('mean',0)*100:>7.2f}% {ce.get('p50',0)*100:>7.2f}% "
              f"{agg.get('profitable_stocks',0):>3}/{agg.get('stocks',0)}")

    # 增量分析
    print(f"\n{'='*90}")
    print(f"增量分析（含捕获效率CE）")
    print(f"{'='*90}")
    a = all_results["A_baseline"]
    b = all_results["B_j2_only"]
    c = all_results["C_j4_only"]
    d = all_results["D_j2_j4"]

    def ce_mean(x):
        return x.get("capture_efficiency", {}).get("mean", 0)
    def ce_p50(x):
        return x.get("capture_efficiency", {}).get("p50", 0)

    print(f"\nJ2贡献（B-A）：配对{b['paired_trades']-a['paired_trades']:+d} 净盈亏{b['net_pnl']-a['net_pnl']:+.0f} "
          f"payoff{b['payoff_ratio']-a['payoff_ratio']:+.4f} CE均值{ce_mean(b)-ce_mean(a):+.4f} CE中位{ce_p50(b)-ce_p50(a):+.4f}")
    print(f"J4贡献（C-A）：配对{c['paired_trades']-a['paired_trades']:+d} 净盈亏{c['net_pnl']-a['net_pnl']:+.0f} "
          f"payoff{c['payoff_ratio']-a['payoff_ratio']:+.4f} CE均值{ce_mean(c)-ce_mean(a):+.4f} CE中位{ce_p50(c)-ce_p50(a):+.4f}")
    print(f"J2+J4协同（D-A）：配对{d['paired_trades']-a['paired_trades']:+d} 净盈亏{d['net_pnl']-a['net_pnl']:+.0f} "
          f"payoff{d['payoff_ratio']-a['payoff_ratio']:+.4f} CE均值{ce_mean(d)-ce_mean(a):+.4f} CE中位{ce_p50(d)-ce_p50(a):+.4f}")
    print(f"J4增量（D-B，J2基础上加J4）：配对{d['paired_trades']-b['paired_trades']:+d} 净盈亏{d['net_pnl']-b['net_pnl']:+.0f} "
          f"payoff{d['payoff_ratio']-b['payoff_ratio']:+.4f} CE均值{ce_mean(d)-ce_mean(b):+.4f} CE中位{ce_p50(d)-ce_p50(b):+.4f}")
    print(f"J2增量（D-C，J4基础上加J2）：配对{d['paired_trades']-c['paired_trades']:+d} 净盈亏{d['net_pnl']-c['net_pnl']:+.0f} "
          f"payoff{d['payoff_ratio']-c['payoff_ratio']:+.4f} CE均值{ce_mean(d)-ce_mean(c):+.4f} CE中位{ce_p50(d)-ce_p50(c):+.4f}")

    # 保存
    output = {
        "stage": "J4",
        "groups": all_results,
        "config": {
            "vwap_band": args.vwap_band,
            "kdj_max": args.kdj_max,
            "surge_vwap_band": args.surge_vwap_band,
            "surge_kdj_min": args.surge_kdj_min,
        },
    }
    out_path = BACKTEST_OUTPUT_DIR / "diag_J4_ab_test.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
