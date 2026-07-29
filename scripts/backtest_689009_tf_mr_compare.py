#!/usr/bin/env python3
"""
689009 TF vs MR 对比测试
=====================
针对689009这只高波动假突破股票，对比两种模式的表现：

TF（趋势跟随）：
  - TF1: 当前默认参数（tf_adx=35, reverse_adx=28, min_cap=0.0072）
  - TF2: 调参后（tf_adx=42, reverse_adx=25, min_cap=0.01, cooldown=48）

MR（均值回归，修复后）：
  - MR1: z=1.0 rev=0.3 sl=0.002（yaml默认）
  - MR2: z=1.5 rev=0.3 sl=0.003（更严格开仓+更宽止损）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from dataclasses import replace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import run_zz500_single
from at0.paths import ZZ500_5MIN_DIR

CODE = "689009"
START = "2025-07-28"
END = "2026-07-22"


def tf_override_1():
    """TF 当前默认参数。"""
    return {"sp": {"strategy_mode": "trend_following"}}


def tf_override_2():
    """TF 调参后：提高ADX门槛+拉大反转缓冲+提高最小价差+加倍冷却。"""
    return {
        "sp": {
            "strategy_mode": "trend_following",
            "tf_adx_threshold": 42.0,
            "tf_trend_reverse_adx": 25.0,
        },
        "bp": {
            "cooldown_bars": 48,
        },
        "rp": {
            "min_capture_spread": 0.01,
        },
    }


def mr_override_1():
    """MR 修复后默认参数（yaml mr_stop_loss_ratio=0.0015生效）。"""
    return {
        "sp": {
            "strategy_mode": "mean_reversion",
            "mr_z_threshold": 1.0,
            "mr_take_profit_reversion_ratio": 0.3,
            "mr_min_reversion": 0.001,
        },
    }


def mr_override_2():
    """MR 更严格开仓+更宽止损（适合高波动股）。"""
    return {
        "sp": {
            "strategy_mode": "mean_reversion",
            "mr_z_threshold": 1.5,
            "mr_take_profit_reversion_ratio": 0.3,
            "mr_min_reversion": 0.001,
        },
        "bp": {
            "mr_stop_loss_ratio": 0.003,
        },
    }


def run_case(name, override):
    """跑单个case并返回关键指标。"""
    t0 = time.time()
    result = run_zz500_single(
        code=CODE, start_date=START, end_date=END,
        data_dir=ZZ500_5MIN_DIR, base_shares=3000,
        tag=f"689009_{name}_compare",
        params_override=override,
    )
    elapsed = time.time() - t0
    if not result:
        return {"name": name, "error": "no result"}

    total = result.get("total_trades", 0)
    net = result.get("net_pnl", 0)
    wr = result.get("win_rate", 0)
    expired = result.get("expired_legs_count", 0)
    cost_red = result.get("total_cost_reduction", 0)
    cost_paid = result.get("total_cost_paid", 0)

    print(f"\n{'='*60}")
    print(f"{name}: 总T={total} 净={net:+.0f} 胜率={wr*100:.1f}% "
          f"expired={expired} 降成本={cost_red:+.0f} ({elapsed:.0f}s)")
    print(f"{'='*60}")
    return {
        "name": name, "total": total, "net": net, "win_rate": wr,
        "expired": expired, "cost_reduction": cost_red, "cost_paid": cost_paid,
    }


def main():
    print("=" * 70)
    print(f"689009 TF vs MR 对比测试 ({START} ~ {END})")
    print("=" * 70)

    results = []
    results.append(run_case("TF1_默认", tf_override_1()))
    results.append(run_case("TF2_调参", tf_override_2()))
    results.append(run_case("MR1_默认", mr_override_1()))
    results.append(run_case("MR2_高波动", mr_override_2()))

    print("\n" + "=" * 70)
    print("对比汇总")
    print("=" * 70)
    print(f"{'方案':<14} {'总T':>6} {'净盈亏':>10} {'胜率':>8} {'expired':>8} {'降成本':>10}")
    print("-" * 70)
    for r in results:
        if "error" in r:
            print(f"{r['name']:<14} ERROR")
            continue
        print(f"{r['name']:<14} {r['total']:>6} {r['net']:>+10.0f} "
              f"{r['win_rate']*100:>7.1f}% {r['expired']:>8} {r['cost_reduction']:>+10.0f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
