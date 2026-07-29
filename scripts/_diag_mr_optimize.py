#!/usr/bin/env python3
"""独立诊断脚本：对 689009 MR 优化各组的 report.json 做买卖点位置分析。"""
from __future__ import annotations
import json
import statistics
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path(r"d:\project\a-t0-v2\outputs\backtest")

# 各组参数（与 backtest_689009_mr_optimize.py 对齐）
CASES = [
    ("A0_基准",           1.0, 0.0015, 0.001),
    ("A1_z15_sl003",      1.5, 0.003,  0.001),
    ("A2_z15_sl003_r003", 1.5, 0.003,  0.003),
    ("A3_z20_sl003_r003", 2.0, 0.003,  0.003),
    ("A4_z20_sl005_r003", 2.0, 0.005,  0.003),
    ("A5_z25_sl005_r003", 2.5, 0.005,  0.003),
    ("A6_z20_sl005_r001", 2.0, 0.005,  0.001),
    ("A7_z15_sl005_r003", 1.5, 0.005,  0.003),
    ("A8_z30_sl005_r003", 3.0, 0.005,  0.003),
]


def find_report(name):
    """查找某组的 report.json。文件名格式: 689009_689009_{name}_opt_*_report.json"""
    pattern = f"689009_*{name}*_report.json"
    matches = list(OUTPUT_DIR.glob(pattern))
    return matches[0] if matches else None


def diagnose(report_path):
    with open(report_path, encoding="utf-8") as f:
        r = json.load(f)

    net = r.get("net_pnl", 0)
    wr = r.get("win_rate", 0)
    total_trades = r.get("total_trades", 0)

    # 提取所有交易
    trades = []
    for dr in r.get("daily_results", []):
        date = dr.get("date", "")
        for t in dr.get("trades", []):
            t["date"] = date
            trades.append(t)

    # 配对 sell 开仓 -> buy 平仓
    pairs = []
    open_leg = None
    for t in trades:
        is_open = bool(t.get("rules_fired")) and t.get("status") == "open"
        if is_open and t.get("direction") == "sell":
            open_leg = t
        elif open_leg is not None and t.get("direction") == "buy":
            pairs.append({"sell": open_leg, "buy": t})
            open_leg = None

    if not pairs:
        return None

    # 开仓位置
    open_devs = []
    for p in pairs:
        s = p["sell"]
        vwap = s.get("vwap", 0)
        if vwap and vwap > 0:
            open_devs.append((s["fill_price"] - vwap) / vwap)

    # 买卖价差
    spreads = []
    for p in pairs:
        s = p["sell"]["fill_price"]
        b = p["buy"]["fill_price"]
        if s > 0:
            spreads.append((s - b) / s)

    # 持仓时长
    holds = [p["buy"].get("holding_bars", 0) for p in pairs]

    # 平仓方式分布
    st_counter = Counter(p["buy"].get("status", "unknown") for p in pairs)

    # 回归平仓 vs 止损平仓
    paired_spreads = []
    stopped_spreads = []
    for p in pairs:
        s = p["sell"]["fill_price"]
        b = p["buy"]["fill_price"]
        if s > 0:
            sp = (s - b) / s
            if p["buy"].get("status") == "stopped":
                stopped_spreads.append(sp)
            else:
                paired_spreads.append(sp)

    return {
        "total_trades": total_trades,
        "pairs": len(pairs),
        "net": net,
        "wr": wr,
        "open_dev_mean": statistics.mean(open_devs) * 100 if open_devs else 0,
        "open_dev_median": statistics.median(open_devs) * 100 if open_devs else 0,
        "spread_mean": statistics.mean(spreads) * 100 if spreads else 0,
        "spread_median": statistics.median(spreads) * 100 if spreads else 0,
        "hold_mean": statistics.mean(holds) if holds else 0,
        "hold_max": max(holds) if holds else 0,
        "status_dist": dict(st_counter),
        "paired_count": len(paired_spreads),
        "stopped_count": len(stopped_spreads),
        "paired_spread_mean": statistics.mean(paired_spreads) * 100 if paired_spreads else None,
        "stopped_spread_mean": statistics.mean(stopped_spreads) * 100 if stopped_spreads else None,
    }


def main():
    print("=" * 130)
    print("689009 MR 参数优化 — 买卖点位置诊断")
    print("=" * 130)
    print(f"{'方案':<22} {'z':>4} {'sl':>7} {'minr':>6} | {'总T':>4} {'净':>8} {'胜率':>6} | "
          f"{'开仓深%':>9} {'价差%':>8} {'持仓':>5} {'最大':>4} | "
          f"{'配对':>4} {'止损':>4} {'配对价差':>9} {'止损价差':>9}")
    print("-" * 130)

    rows = []
    for name, z, sl, min_rev in CASES:
        rp = find_report(name)
        if not rp:
            print(f"{name:<22} 找不到 report.json")
            continue
        d = diagnose(rp)
        if not d:
            print(f"{name:<22} 无配对数据")
            continue
        paired_str = f"{d['paired_spread_mean']:+.3f}" if d['paired_spread_mean'] is not None else "N/A"
        stopped_str = f"{d['stopped_spread_mean']:+.3f}" if d['stopped_spread_mean'] is not None else "N/A"
        print(f"{name:<22} {z:>4.1f} {sl:>7.4f} {min_rev:>6.3f} | "
              f"{d['total_trades']:>4} {d['net']:>+8.0f} {d['wr']*100:>5.1f}% | "
              f"{d['open_dev_mean']:>+9.3f} {d['spread_mean']:>+8.3f} {d['hold_mean']:>4.1f}b {d['hold_max']:>4}b | "
              f"{d['paired_count']:>4} {d['stopped_count']:>4} {paired_str:>9} {stopped_str:>9}")
        rows.append({"name": name, "z": z, "sl": sl, "min_rev": min_rev, **d})

    print("=" * 130)
    print("\n关键观察:")
    # 1. 回归平仓触发情况
    print("\n1. 回归平仓触发情况:")
    for r in rows:
        pc = r['paired_count']
        sc = r['stopped_count']
        total = pc + sc
        pct = pc / total * 100 if total else 0
        print(f"  {r['name']:<22}: 配对={pc:>3} 止损={sc:>3} 回归平仓占比={pct:.1f}%")

    # 2. 持仓时长变化
    print("\n2. 持仓时长变化（1 bar = 5min）:")
    for r in rows:
        print(f"  {r['name']:<22}: 均值={r['hold_mean']:.1f}b 最大={r['hold_max']}b")

    # 3. 开仓深度变化
    print("\n3. 开仓深度变化（% vs VWAP）:")
    for r in rows:
        print(f"  {r['name']:<22}: 均值={r['open_dev_mean']:+.3f}% 中位={r['open_dev_median']:+.3f}%")

    # 4. 价差变化
    print("\n4. 买卖价差变化（% of sell price）:")
    for r in rows:
        print(f"  {r['name']:<22}: 均值={r['spread_mean']:+.3f}% 中位={r['spread_median']:+.3f}%")


if __name__ == "__main__":
    main()
