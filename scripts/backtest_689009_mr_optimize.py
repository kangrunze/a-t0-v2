#!/usr/bin/env python3
"""
689009 MR 参数优化测试
=====================
验证方向：提高开仓门槛 z + 放宽止损 sl + 拉大最小回归 min_rev，
让回归平仓优先于止损，给均值回归留足时间。

参数网格：
  z_threshold: [1.0, 1.5, 2.0, 2.5]      # 开仓深度门槛
  stop_loss:   [0.0015, 0.003, 0.005]     # 止损宽度
  min_reversion: [0.001, 0.003]           # 最小回归幅度（平仓门槛）

基准：MR1 z=1.0 sl=0.0015 min_rev=0.001（已跑：净+769，145笔全止损）
"""
from __future__ import annotations

import sys
import time
import json
import statistics
from pathlib import Path
from collections import Counter
from dataclasses import replace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import run_zz500_single
from at0.paths import ZZ500_5MIN_DIR

CODE = "689009"
START = "2025-07-28"
END = "2026-07-22"

OUTPUT_DIR = Path(r"d:\project\a-t0-v2\outputs\backtest")


def make_override(z, sl, min_rev):
    """构造 MR 参数 override。"""
    sp = {
        "strategy_mode": "mean_reversion",
        "mr_z_threshold": z,
        "mr_take_profit_reversion_ratio": 0.3,
        "mr_min_reversion": min_rev,
    }
    bp = {"mr_stop_loss_ratio": sl}
    return {"sp": sp, "bp": bp}


def run_case(name, z, sl, min_rev):
    """跑单个case。"""
    t0 = time.time()
    override = make_override(z, sl, min_rev)
    result = run_zz500_single(
        code=CODE, start_date=START, end_date=END,
        data_dir=ZZ500_5MIN_DIR, base_shares=3000,
        tag=f"689009_{name}_opt",
        params_override=override,
    )
    elapsed = time.time() - t0
    if not result:
        return {"name": name, "z": z, "sl": sl, "min_rev": min_rev, "error": "no result"}

    return {
        "name": name, "z": z, "sl": sl, "min_rev": min_rev,
        "total": result.get("total_trades", 0),
        "net": result.get("net_pnl", 0),
        "wr": result.get("win_rate", 0),
        "expired": result.get("expired_legs_count", 0),
        "cost_red": result.get("total_cost_reduction", 0),
        "cost_paid": result.get("total_cost_paid", 0),
        "elapsed": elapsed,
        # 找对应的 report.json 路径（用于后续诊断）
        "report_tag": f"689009_{name}_opt",
    }


def diagnose_entry_exit(report_tag):
    """从 report.json 提取买卖点位置诊断指标。"""
    # 找 report.json
    pattern = f"{report_tag}_*_report.json"
    matches = list(OUTPUT_DIR.glob(pattern))
    if not matches:
        return None
    report_path = matches[0]

    with open(report_path, encoding="utf-8") as f:
        r = json.load(f)

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

    # 回归平仓 vs 止损平仓 的价差
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
        "pairs": len(pairs),
        "open_dev_mean": statistics.mean(open_devs) * 100 if open_devs else 0,
        "open_dev_median": statistics.median(open_devs) * 100 if open_devs else 0,
        "spread_mean": statistics.mean(spreads) * 100 if spreads else 0,
        "spread_median": statistics.median(spreads) * 100 if spreads else 0,
        "hold_mean": statistics.mean(holds) if holds else 0,
        "hold_median": statistics.median(holds) if holds else 0,
        "status_dist": dict(st_counter),
        "paired_count": len(paired_spreads),
        "stopped_count": len(stopped_spreads),
        "paired_spread_mean": statistics.mean(paired_spreads) * 100 if paired_spreads else None,
        "stopped_spread_mean": statistics.mean(stopped_spreads) * 100 if stopped_spreads else None,
    }


def main():
    print("=" * 80)
    print(f"689009 MR 参数优化测试 ({START} ~ {END})")
    print("=" * 80)

    # 参数网格（共 8 组 + 基准 = 9 组）
    cases = [
        # (name, z, sl, min_rev)
        ("A0_基准",       1.0, 0.0015, 0.001),
        ("A1_z15_sl003",  1.5, 0.003,  0.001),
        ("A2_z15_sl003_r003", 1.5, 0.003, 0.003),
        ("A3_z20_sl003_r003", 2.0, 0.003, 0.003),
        ("A4_z20_sl005_r003", 2.0, 0.005, 0.003),
        ("A5_z25_sl005_r003", 2.5, 0.005, 0.003),
        ("A6_z20_sl005_r001", 2.0, 0.005, 0.001),
        ("A7_z15_sl005_r003", 1.5, 0.005, 0.003),
        ("A8_z30_sl005_r003", 3.0, 0.005, 0.003),
    ]

    results = []
    for name, z, sl, min_rev in cases:
        print(f"\n[{name}] z={z} sl={sl} min_rev={min_rev} ...", flush=True)
        r = run_case(name, z, sl, min_rev)
        if "error" not in r:
            print(f"  → 总T={r['total']} 净={r['net']:+.0f} 胜率={r['wr']*100:.1f}% "
                  f"降成本={r['cost_red']:+.0f} ({r['elapsed']:.0f}s)")
        else:
            print(f"  → ERROR: {r['error']}")
        results.append(r)

    # 诊断买卖点位置
    print("\n" + "=" * 80)
    print("买卖点位置诊断（开仓深度 / 价差 / 持仓 / 平仓方式）")
    print("=" * 80)
    diag_results = []
    for r in results:
        if "error" in r:
            continue
        d = diagnose_entry_exit(r["report_tag"])
        if d:
            d["name"] = r["name"]
            d["z"] = r["z"]
            d["sl"] = r["sl"]
            d["min_rev"] = r["min_rev"]
            d["net"] = r["net"]
            d["wr"] = r["wr"]
            diag_results.append(d)

    # 汇总表
    print("\n" + "=" * 80)
    print("汇总对比")
    print("=" * 80)
    print(f"{'方案':<22} {'z':>4} {'sl':>7} {'minr':>6} | {'总T':>4} {'净':>8} {'胜率':>6} | "
          f"{'开仓深%':>8} {'价差%':>8} {'持仓':>6} | {'配对':>4} {'止损':>4} {'配对价差':>8}")
    print("-" * 110)
    for d in diag_results:
        paired_str = f"{d['paired_spread_mean']:+.3f}" if d['paired_spread_mean'] is not None else "N/A"
        print(f"{d['name']:<22} {d['z']:>4.1f} {d['sl']:>7.4f} {d['min_rev']:>6.3f} | "
              f"{d['pairs']*2:>4} {d['net']:>+8.0f} {d['wr']*100:>5.1f}% | "
              f"{d['open_dev_mean']:>+8.3f} {d['spread_mean']:>+8.3f} {d['hold_mean']:>5.1f}b | "
              f"{d['paired_count']:>4} {d['stopped_count']:>4} {paired_str:>8}")

    print("=" * 80)
    print("\n说明:")
    print("  总T = 配对数×2（sell开仓 + buy平仓）")
    print("  开仓深% = sell 相对 VWAP 偏离度均值（正=卖在VWAP上方）")
    print("  价差% = (sell-buy)/sell 均值（正=盈利）")
    print("  持仓 = holding_bars 均值（1 bar = 5min）")
    print("  配对 = 正常回归平仓数；止损 = 止损平仓数")
    print("  配对价差 = 回归平仓的价差均值（None=无回归平仓触发）")


if __name__ == "__main__":
    main()
