#!/usr/bin/env python3
"""
Stage K — K4均值回归回测
========================
20只股票近一年，均值回归模式回测。

K4-1: 频率检查（默认参数，20只×1年）
K4-2: 基线效果
K4-3: 训练集10只网格搜索 z_threshold × stop_loss_ratio
K4-4: 验证集10只复核最优参数
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import run_zz500_batch
from at0.paths import ZZ500_5MIN_DIR

# ── 20只股票 ──
STOCKS_20 = [
    "001221", "001309", "002261", "300548", "300570",
    "300735", "300972", "301536", "600602", "603119",
    "688331", "688498", "688582", "688629", "688702",
    "300339", "300475", "300620", "301526", "603728",
]

START_DATE = "2025-07-28"
END_DATE = "2026-07-22"

# 均值回归override参数
def make_override(z: float = 1.5, sl: float = 0.0015) -> dict:
    return {
        "sp": {
            "strategy_mode": "mean_reversion",
            "mr_z_threshold": z,
            "mr_take_profit_z": 0.3,
            "mr_lookback_bars": 12,
            "mr_vol_ratio_max": 1.5,
        },
        "bp": {
            "stop_loss_ratio": sl,
            "max_holding_bars": 10,
            "cooldown_bars": 8,
        },
        "rp": {
            "max_t_size_ratio": 0.12,
        },
    }

TRAIN_STOCKS = STOCKS_20[:10]
VALID_STOCKS = STOCKS_20[10:]


def extract_summary(batch_result: dict) -> dict:
    """从batch结果提取汇总指标。"""
    overall = batch_result.get("overall", {})
    per_stock = batch_result.get("per_stock", [])
    return {
        "overall": overall,
        "per_stock_count": len(per_stock),
        "per_stock": per_stock,
    }


def K4_1_frequency():
    """K4-1: 频率检查。"""
    print("=" * 80)
    print("K4-1: 频率检查（20只×1年，默认参数 z=1.5 sl=0.0015）")
    print("=" * 80)

    result = run_zz500_batch(
        codes=STOCKS_20,
        start_date=START_DATE,
        end_date=END_DATE,
        data_dir=ZZ500_5MIN_DIR,
        base_shares=3000,
        tag="K4_mr_k41",
        params_override=make_override(),
    )
    summary = extract_summary(result)
    overall = summary["overall"]

    print(f"\n  K4-1 汇总:")
    print(f"  总配对: {overall.get('paired_trades', 0)}笔")
    print(f"  每只平均: {overall.get('paired_trades', 0)/20:.0f}笔")
    print(f"  净盈亏: {overall.get('net_pnl', 0):+.0f}")
    print(f"  胜率: {overall.get('win_rate', 0)*100:.1f}%")
    print(f"  payoff: {overall.get('payoff_ratio', 0):.4f}")

    # 频率检查
    per_stock = summary["per_stock"]
    too_few = [s for s in per_stock if s.get("paired_trades", 0) < 20]
    too_many = [s for s in per_stock if s.get("paired_trades", 0) > 300]
    if too_few:
        print(f"  ⚠ 触发过少(<20): {[s['code'] for s in too_few]}")
    if too_many:
        print(f"  ⚠ 触发过多(>300): {[s['code'] for s in too_many]}")
    if not too_few and not too_many:
        print(f"  ✓ 频率正常")

    return summary


def K4_3_grid():
    """K4-3: 训练集10只网格搜索。"""
    print(f"\n{'=' * 80}")
    print("K4-3: 网格搜索（训练集10只）")
    print("=" * 80)

    z_grid = [1.0, 1.5, 2.0, 2.5]
    sl_grid = [0.001, 0.0015, 0.002, 0.003]
    results = {}

    for z in z_grid:
        for sl in sl_grid:
            tag = f"K4_mr_grid_z{z}_sl{sl}"
            print(f"\n  >>> z={z} sl={sl}")
            result = run_zz500_batch(
                codes=TRAIN_STOCKS,
                start_date=START_DATE,
                end_date=END_DATE,
                data_dir=ZZ500_5MIN_DIR,
                base_shares=3000,
                tag=tag,
                params_override=make_override(z=z, sl=sl),
            )
            overall = result.get("overall", {})
            net = overall.get("net_pnl", 0)
            paired = overall.get("paired_trades", 0)
            payoff = overall.get("payoff_ratio", 0)
            key = f"z{z}_sl{sl}"
            results[key] = {"z": z, "sl": sl, "net": net, "paired": paired, "payoff": payoff}
            print(f"  >>> z={z} sl={sl}: 净盈亏{net:>+.0f} 配对{paired}笔 payoff={payoff:.4f}")

    # 最优
    best = max(results.values(), key=lambda x: x["net"])
    print(f"\n  最优: z={best['z']} sl={best['sl']} 净盈亏{best['net']:+.0f}")
    return best, results


def K4_4_validate(best):
    """K4-4: 验证集复核。"""
    print(f"\n{'=' * 80}")
    print(f"K4-4: 验证集复核（最优参数 z={best['z']} sl={best['sl']}）")
    print("=" * 80)

    result = run_zz500_batch(
        codes=VALID_STOCKS,
        start_date=START_DATE,
        end_date=END_DATE,
        data_dir=ZZ500_5MIN_DIR,
        base_shares=3000,
        tag="K4_mr_k44_validate",
        params_override=make_override(z=best["z"], sl=best["sl"]),
    )
    overall = result.get("overall", {})
    print(f"\n  验证集汇总:")
    print(f"  净盈亏: {overall.get('net_pnl', 0):+.0f}")
    print(f"  配对: {overall.get('paired_trades', 0)}笔")
    print(f"  胜率: {overall.get('win_rate', 0)*100:.1f}%")
    print(f"  payoff: {overall.get('payoff_ratio', 0):.4f}")
    print(f"  盈利股票: {overall.get('profitable_stocks', 0)}/10")
    return overall


def main():
    print("=" * 80)
    print("Stage K — K4 均值回归回测（20只×1年）")
    print("=" * 80)
    print(f"股票: {len(STOCKS_20)}只 (训练10 + 验证10)")
    print(f"期间: {START_DATE} ~ {END_DATE}")
    print(f"模式: mean_reversion")

    t_start = time.time()

    # K4-1 + K4-2: 频率检查 + 基线
    k41 = K4_1_frequency()

    # K4-3: 网格搜索
    best, grid_results = K4_3_grid()

    # K4-4: 验证集
    k44 = K4_4_validate(best)

    elapsed = time.time() - t_start
    print(f"\n{'=' * 80}")
    print(f"K4 完成（总耗时 {elapsed/60:.1f} 分钟）")
    print(f"{'=' * 80}")
    print(f"\n最终结果:")
    print(f"  最优参数: z={best['z']} sl={best['sl']}")
    print(f"  训练集净盈亏: {best['net']:+.0f}")
    print(f"  验证集净盈亏: {k44.get('net_pnl', 0):+.0f}")

    # 保存
    out = {
        "step": "K4_mean_reversion_backtest",
        "stocks": STOCKS_20,
        "train_stocks": TRAIN_STOCKS,
        "valid_stocks": VALID_STOCKS,
        "period": f"{START_DATE}~{END_DATE}",
        "k41_baseline": k41,
        "grid_results": grid_results,
        "best_params": best,
        "k44_validation": k44,
        "elapsed_minutes": round(elapsed / 60, 1),
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "K4_mr_backtest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
