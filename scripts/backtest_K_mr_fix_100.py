#!/usr/bin/env python3
"""
Stage K — 均值回归修复后100只随机抽样调参（v2）
=========================================
修复内容：
  1. 平仓逻辑改为基于开仓时偏离度的绝对回归目标
  2. 删除 sl 硬编码 bug，MR 风控参数从 yaml 读取（mr_stop_loss_ratio 等）
  3. 网格搜索含 sl 维度（z × rev_ratio × sl）
  4. backtest.py 已接入 mr_stop_loss_ratio/mr_max_holding_bars/mr_cooldown_bars/mr_max_t_size_ratio

本轮：100只随机抽样，50只调参 + 50只验证
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

START_DATE = "2025-07-28"
END_DATE = "2026-07-22"

# 100只随机抽样（固定种子，可复现）
all_files = sorted(ZZ500_5MIN_DIR.glob("*.json"))
ALL_CODES = [f.stem for f in all_files if not f.stem.startswith("_")]
import random
random.seed(42)
SAMPLE_100 = sorted(random.sample(ALL_CODES, 100))

TRAIN_50 = SAMPLE_100[:50]
VALID_50 = SAMPLE_100[50:]


def make_override(z=1.0, rev_ratio=0.3, min_rev=0.001, sl=None, max_hold=None, cooldown=None):
    """构造 MR 模式参数覆盖。

    v2 修复：
      - 删除 sl=0.001 硬编码 bug（原 bug 导致所有止损精确 0.1%，MR 平仓从未触发）
      - sl/max_hold/cooldown 默认 None，让 yaml 的 mr_* 参数通过 BacktestParams.effective_* 生效
      - 显式传入时覆盖 yaml（用于网格搜索）
    """
    sp = {
        "strategy_mode": "mean_reversion",
        "mr_z_threshold": z,
        "mr_take_profit_reversion_ratio": rev_ratio,
        "mr_min_reversion": min_rev,
        "mr_lookback_bars": 12,
        "mr_vol_ratio_max": 1.5,
    }
    bp = {}
    if sl is not None:
        bp["mr_stop_loss_ratio"] = sl
    if max_hold is not None:
        bp["mr_max_holding_bars"] = max_hold
    if cooldown is not None:
        bp["mr_cooldown_bars"] = cooldown
    return {"sp": sp, "bp": bp}


def quick_stat(result):
    o = result.get("overall", {})
    return {
        "paired": o.get("paired_trades", 0),
        "win_rate": o.get("win_rate", 0),
        "payoff": o.get("payoff_ratio", 0),
        "net": o.get("net_pnl", 0),
        "profitable": o.get("profitable_stocks", 0),
    }


def step1_baseline():
    """100只基线（MR 风控参数全用 yaml 默认值，验证 effective_* 接入生效）。"""
    print("=" * 80)
    print("步骤1: 100只基线（yaml 默认 mr_stop_loss_ratio=0.0015 z=1.0 rev=0.3）")
    print("=" * 80)
    t0 = time.time()
    result = run_zz500_batch(
        codes=SAMPLE_100, start_date=START_DATE, end_date=END_DATE,
        data_dir=ZZ500_5MIN_DIR, base_shares=3000,
        tag="K_mr_fix_v2_baseline100", params_override=make_override(),
    )
    s = quick_stat(result)
    print(f"\n  基线: 配对{s['paired']} 胜率{s['win_rate']*100:.1f}% "
          f"payoff={s['payoff']:.2f} 净盈亏{s['net']:+.0f} "
          f"盈利{s['profitable']}/100 ({time.time()-t0:.0f}s)")
    return s


def step2_grid():
    """训练集50只三维网格搜索：z × rev_ratio × sl。

    v2 新增 sl 维度，验证 MR 平仓逻辑真正生效后哪个止损比例最优。
    sl 候选：0.002(趋势跟随值)、0.003、0.005（MR 赌回归，止损应更宽松给回归时间）
    """
    print(f"\n{'=' * 80}")
    print("步骤2: 训练集50只三维网格搜索（z × rev_ratio × sl）")
    print("=" * 80)
    grid = []
    for z in [1.0, 1.5, 2.0]:
        for rev in [0.3, 0.5]:
            for sl in [0.002, 0.003, 0.005]:
                grid.append((z, rev, sl))

    results = {}
    for z, rev, sl in grid:
        t0 = time.time()
        r = run_zz500_batch(
            codes=TRAIN_50, start_date=START_DATE, end_date=END_DATE,
            data_dir=ZZ500_5MIN_DIR, base_shares=3000,
            tag=f"K_mr_fix_v2_grid_z{z}_r{rev}_sl{sl}",
            params_override=make_override(z=z, rev_ratio=rev, sl=sl),
        )
        s = quick_stat(r)
        key = f"z{z}_r{rev}_sl{sl}"
        results[key] = {"z": z, "rev": rev, "sl": sl, **s}
        print(f"  z={z} rev={rev} sl={sl}: 配对{s['paired']} 胜率{s['win_rate']*100:.1f}% "
              f"payoff={s['payoff']:.2f} 净{s['net']:+.0f} ({time.time()-t0:.0f}s)")
    best = max(results.values(), key=lambda x: x["net"])
    print(f"\n  最优: z={best['z']} rev={best['rev']} sl={best['sl']} 净{best['net']:+.0f}")
    return best


def step3_validate(best):
    """验证集50只复核。"""
    print(f"\n{'=' * 80}")
    print(f"步骤3: 验证集50只复核（z={best['z']} rev={best['rev']} sl={best['sl']}）")
    print("=" * 80)
    r = run_zz500_batch(
        codes=VALID_50, start_date=START_DATE, end_date=END_DATE,
        data_dir=ZZ500_5MIN_DIR, base_shares=3000,
        tag="K_mr_fix_v2_validate50",
        params_override=make_override(z=best["z"], rev_ratio=best["rev"], sl=best["sl"]),
    )
    s = quick_stat(r)
    print(f"\n  验证: 配对{s['paired']} 胜率{s['win_rate']*100:.1f}% "
          f"payoff={s['payoff']:.2f} 净{s['net']:+.0f} 盈利{s['profitable']}/50")
    return s


def main():
    print("=" * 80)
    print("Stage K — 均值回归修复后100只调参 v2（sl 纳入网格）")
    print("=" * 80)
    print(f"样本: 100只随机抽样（train50 + valid50）")
    print(f"期间: {START_DATE} ~ {END_DATE}")
    print(f"修复: 删除 sl 硬编码 bug，MR 风控参数从 yaml 读取，网格含 sl 维度")

    t_start = time.time()
    baseline = step1_baseline()
    best = step2_grid()
    validation = step3_validate(best)
    elapsed = time.time() - t_start

    print(f"\n{'=' * 80}")
    print(f"完成（总耗时 {elapsed/60:.1f} 分钟）")
    print(f"{'=' * 80}")
    print(f"\n最终结果:")
    print(f"  最优参数: z={best['z']} rev_ratio={best['rev']} sl={best['sl']}")
    print(f"  训练集: 净{best['net']:+.0f} payoff={best['payoff']:.2f}")
    print(f"  验证集: 净{validation['net']:+.0f} payoff={validation['payoff']:.2f}")

    out = {
        "step": "K_mr_fix_v2_100stocks",
        "fixes": [
            "删除 sl=0.001 硬编码 bug（原 bug 导致 MR 平仓从未触发，全部被 0.1% 止损扫出）",
            "backtest.py 接入 mr_stop_loss_ratio 等 4 个 effective_* property",
            "网格搜索新增 sl 维度（0.002/0.003/0.005）",
        ],
        "sample": SAMPLE_100,
        "train": TRAIN_50, "valid": VALID_50,
        "baseline": baseline, "best": best, "validation": validation,
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "K_mr_fix_v2_100.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果: {out_path}")


if __name__ == "__main__":
    main()
