#!/usr/bin/env python3
"""
步骤1（诊断，不改代码）：
用 params_override 显式传入 thresholds.yaml 中已验证的参数：
  stop_loss_ratio=0.004, trailing_activation_pct=0.0
其余参数不变（仍用 dataclass 默认值），重跑 batch_summary_zz500_sample30
同样的30只股票、同样的日期区间(2026-04-27~2026-07-23)。

输出 win_rate / net_pnl / paired_trades / profitable_stocks 与基线对比。

注意：此脚本不改任何策略代码，仅通过 params_override 注入参数，
验证根因1（批量脚本未读 yaml 导致用了过时默认值）的影响。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import run_zz500_batch

# ── 基线30只股票（来自 outputs/backtest/batch_summary_zz500_sample30.json）──
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
TAG = "diag_step1_yaml_params"

# ── 步骤1: 仅覆盖 yaml 已验证的止损参数 ──
# thresholds.yaml 第106-108行:
#   stop_loss_ratio: 0.004            (dataclass 默认 0.008 ← 旧值)
#   trailing_activation_pct: 0.0      (dataclass 默认 0.005 ← 旧值)
# trailing_ratio 保持 dataclass 默认 0.5（yaml 也是 0.5，无 drift）
PARAMS_OVERRIDE_STEP1 = {
    "bp": {
        "stop_loss_ratio": 0.004,
        "trailing_activation_pct": 0.0,
    },
}


def load_baseline() -> dict:
    p = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz500_sample30.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def print_comparison(baseline: dict, step1: dict) -> None:
    b = baseline["overall"]
    s = step1["overall"]
    print("\n" + "=" * 78)
    print("步骤1 对比：yaml 验证参数 (stop_loss=0.004, trailing_act=0.0) vs 基线")
    print("=" * 78)
    print(f"{'指标':<22} {'基线(旧默认)':>18} {'步骤1(yaml)':>18} {'变化':>18}")
    print("-" * 78)
    rows = [
        ("total_trades",      b["total_trades"],      s["total_trades"],      "{:d}"),
        ("paired_trades",     b["paired_trades"],     s["paired_trades"],     "{:d}"),
        ("win_trades",        b["win_trades"],        s["win_trades"],        "{:d}"),
        ("win_rate",          b["win_rate"],          s["win_rate"],          "{:.4f}"),
        ("gross_pnl",         b["gross_pnl"],         s["gross_pnl"],         "{:+.2f}"),
        ("total_cost",        b["total_cost"],        s["total_cost"],        "{:.2f}"),
        ("net_pnl",           b["net_pnl"],           s["net_pnl"],           "{:+.2f}"),
        ("net_pnl_w_unreal",  b["net_pnl_with_unrealized"], s["net_pnl_with_unrealized"], "{:+.2f}"),
        ("profitable_stocks", b["profitable_stocks"], s["profitable_stocks"], "{:d}"),
        ("losing_stocks",     b["losing_stocks"],     s["losing_stocks"],     "{:d}"),
        ("expired_legs_count", b.get("expired_legs_count", 0), s.get("expired_legs_count", 0), "{:d}"),
    ]
    for name, bv, sv, fmt in rows:
        delta = sv - bv
        delta_str = f"{delta:+.2f}" if isinstance(sv, float) else f"{delta:+d}"
        print(f"{name:<22} {fmt.format(bv):>18} {fmt.format(sv):>18} {delta_str:>18}")
    print("=" * 78)


def main() -> int:
    print("=" * 78)
    print("步骤1: 诊断 — 用 yaml 验证参数重跑30股3个月")
    print("=" * 78)
    print(f"日期区间: {START_DATE} ~ {END_DATE}")
    print(f"数据目录: {DATA_DIR}")
    print(f"股票数: {len(BASELINE_CODES)}")
    print(f"params_override: {json.dumps(PARAMS_OVERRIDE_STEP1, ensure_ascii=False)}")
    print()

    if not DATA_DIR.exists():
        print(f"[ERROR] 数据目录不存在: {DATA_DIR}", file=sys.stderr)
        return 2

    baseline = load_baseline()
    print(f"[基线] win_rate={baseline['overall']['win_rate']:.4f}  "
          f"net_pnl={baseline['overall']['net_pnl']:+.2f}  "
          f"paired={baseline['overall']['paired_trades']}  "
          f"profitable={baseline['overall']['profitable_stocks']}")
    print()

    result = run_zz500_batch(
        codes=BASELINE_CODES,
        start_date=START_DATE,
        end_date=END_DATE,
        data_dir=DATA_DIR,
        base_shares=BASE_SHARES,
        tag=TAG,
        params_override=PARAMS_OVERRIDE_STEP1,
    )

    if not result:
        print("[ERROR] 批量回测返回空", file=sys.stderr)
        return 1

    print_comparison(baseline, result)

    # 保存对比结果
    out_path = PROJECT_ROOT / "outputs" / "backtest" / f"{TAG}_comparison.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "step": "step1_yaml_params",
            "description": "stop_loss_ratio=0.004, trailing_activation_pct=0.0 (yaml validated)",
            "baseline": baseline["overall"],
            "step1": result["overall"],
            "params_override": PARAMS_OVERRIDE_STEP1,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n对比结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
