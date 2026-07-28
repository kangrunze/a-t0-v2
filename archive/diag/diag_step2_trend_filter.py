#!/usr/bin/env python3
"""
步骤2（在步骤1基础上，仅新增一个变量）：
在步骤1的参数基础上（stop_loss_ratio=0.004, trailing_activation_pct=0.0），
额外打开 hard_trend_filter_add=True, hard_trend_filter_reduce=True，
其余不变，重跑同样的30股3个月样本。输出对比。

一次一个自由度：本步骤只新增 trend filter 这一个变量，
用于验证根因2（趋势硬门控默认关闭，策略在震荡市也跑）的影响。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import run_zz500_batch

BASELINE_CODES = [
    "300140", "603119", "000878", "300024", "000951",
    "002056", "600879", "300957", "002065", "601019",
    "603568", "603766", "002261", "301498", "688561",
    "000997", "601865", "300136", "300972", "600312",
    "688180", "600909", "600602", "603225", "000060",
    "688331", "600298", "600098", "600256", "301200",
]

START_DATE = "2026-04-27"
END_DATE = "2026-07-23"
DATA_DIR = Path(r"D:\project\data\zz500_5min")
BASE_SHARES = 3000
TAG = "diag_step2_trend_filter"

# 步骤2 = 步骤1参数 + 打开趋势硬门控
PARAMS_OVERRIDE_STEP2 = {
    "bp": {
        "stop_loss_ratio": 0.004,
        "trailing_activation_pct": 0.0,
        "hard_trend_filter_add": True,
        "hard_trend_filter_reduce": True,
    },
}


def load_step1() -> dict:
    p = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_diag_step1_yaml_params.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_baseline() -> dict:
    p = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz500_sample30.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def print_comparison(baseline: dict, step1: dict, step2: dict) -> None:
    b = baseline["overall"]
    s1 = step1["overall"]
    s2 = step2["overall"]
    print("\n" + "=" * 92)
    print("步骤2 对比：步骤1 + hard_trend_filter_add/reduce=True")
    print("=" * 92)
    print(f"{'指标':<22} {'基线(旧默认)':>16} {'步骤1(yaml)':>16} {'步骤2(+trend)':>16} {'2vs1变化':>14}")
    print("-" * 92)
    rows = [
        ("total_trades",      b["total_trades"],      s1["total_trades"],      s2["total_trades"],      "{:d}"),
        ("paired_trades",     b["paired_trades"],     s1["paired_trades"],     s2["paired_trades"],     "{:d}"),
        ("win_trades",        b["win_trades"],        s1["win_trades"],        s2["win_trades"],        "{:d}"),
        ("win_rate",          b["win_rate"],          s1["win_rate"],          s2["win_rate"],          "{:.4f}"),
        ("gross_pnl",         b["gross_pnl"],         s1["gross_pnl"],         s2["gross_pnl"],         "{:+.2f}"),
        ("total_cost",        b["total_cost"],        s1["total_cost"],        s2["total_cost"],        "{:.2f}"),
        ("net_pnl",           b["net_pnl"],           s1["net_pnl"],           s2["net_pnl"],           "{:+.2f}"),
        ("net_pnl_w_unreal",  b["net_pnl_with_unrealized"], s1["net_pnl_with_unrealized"], s2["net_pnl_with_unrealized"], "{:+.2f}"),
        ("profitable_stocks", b["profitable_stocks"], s1["profitable_stocks"], s2["profitable_stocks"], "{:d}"),
        ("losing_stocks",     b["losing_stocks"],     s1["losing_stocks"],     s2["losing_stocks"],     "{:d}"),
        ("expired_legs_count", b.get("expired_legs_count", 0), s1.get("expired_legs_count", 0), s2.get("expired_legs_count", 0), "{:d}"),
    ]
    for name, bv, s1v, s2v, fmt in rows:
        delta = s2v - s1v
        delta_str = f"{delta:+.2f}" if isinstance(s2v, float) else f"{delta:+d}"
        print(f"{name:<22} {fmt.format(bv):>16} {fmt.format(s1v):>16} {fmt.format(s2v):>16} {delta_str:>14}")
    print("=" * 92)


def main() -> int:
    print("=" * 78)
    print("步骤2: 步骤1参数 + hard_trend_filter_add/reduce=True")
    print("=" * 78)
    print(f"日期区间: {START_DATE} ~ {END_DATE}")
    print(f"数据目录: {DATA_DIR}")
    print(f"params_override: {json.dumps(PARAMS_OVERRIDE_STEP2, ensure_ascii=False)}")
    print()

    if not DATA_DIR.exists():
        print(f"[ERROR] 数据目录不存在: {DATA_DIR}", file=sys.stderr)
        return 2

    baseline = load_baseline()
    step1 = load_step1()
    print(f"[基线]   win_rate={baseline['overall']['win_rate']:.4f}  "
          f"net_pnl={baseline['overall']['net_pnl']:+.2f}  "
          f"profitable={baseline['overall']['profitable_stocks']}")
    print(f"[步骤1]  win_rate={step1['overall']['win_rate']:.4f}  "
          f"net_pnl={step1['overall']['net_pnl']:+.2f}  "
          f"profitable={step1['overall']['profitable_stocks']}")
    print()

    result = run_zz500_batch(
        codes=BASELINE_CODES,
        start_date=START_DATE,
        end_date=END_DATE,
        data_dir=DATA_DIR,
        base_shares=BASE_SHARES,
        tag=TAG,
        params_override=PARAMS_OVERRIDE_STEP2,
    )

    if not result:
        print("[ERROR] 批量回测返回空", file=sys.stderr)
        return 1

    print_comparison(baseline, step1, result)

    out_path = PROJECT_ROOT / "outputs" / "backtest" / f"{TAG}_comparison.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "step": "step2_trend_filter",
            "description": "step1 + hard_trend_filter_add=True + hard_trend_filter_reduce=True",
            "baseline": baseline["overall"],
            "step1": step1["overall"],
            "step2": result["overall"],
            "params_override": PARAMS_OVERRIDE_STEP2,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n对比结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
