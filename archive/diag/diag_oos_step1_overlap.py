#!/usr/bin/env python3
"""
OOS步骤1 — 核实调参样本与训练样本的重叠
========================================
背景：J0-J4 的 stop_loss=0.002 / trailing_ratio=0.2 / J2 回踩参数
全部在"36股"上调出来。这36只是振幅筛选0.0494后的全池通过股票。
F5 振幅阈值验证时，把全池500只分成"训练集100只(seed=42抽样)"和
"样本外400只"，36只通过筛选的里有9只属训练集、27只属OOS。

本脚本现场复现三份清单并算重叠，回答：
  1. J0-J4 调参用的36只具体是哪些代码？
  2. 这36只与F5训练集100只的重叠 = 9只？OOS = 27只？
  3. J0-J4 调参时是否把F5的27只OOS也当训练集用了？（预期：是）

复现口径与 diag_stop_loss_grid_100stocks.py / backtest_zz500.py 完全一致：
  - 振幅筛选阈值: screener.min_amplitude_long = 0.0494
  - 振幅窗口: 回测期内前60个交易日
  - 训练集抽样: random.seed(42) + random.sample(全池, 100)
  - 回测区间: 2023-07-25 ~ 2026-07-22
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.config import load_screener_params

START = "2023-07-25"
END = "2026-07-22"
TRAIN_SAMPLE_SIZE = 100
SEED = 42
AMP_WINDOW = 60


def filter_codes_by_amplitude(codes: list[str], data_dir: Path,
                               start_date: str, end_date: str,
                               threshold: float, window: int = AMP_WINDOW) -> list[str]:
    """与 diag_stop_loss_grid_100stocks.py L59-101 / backtest_zz500.py L581-632 一致。"""
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
        if sum(amps) / len(amps) >= threshold:
            passed.append(code)
    return passed


def main():
    print("=" * 80)
    print("OOS步骤1 — 调参样本与训练样本重叠核实")
    print("=" * 80)

    sp = load_screener_params()
    min_amp = sp.min_amplitude_long
    print(f"\n振幅筛选阈值: min_amplitude_long = {min_amp}")
    print(f"振幅窗口: 回测期内前 {AMP_WINDOW} 个交易日")
    print(f"回测区间: {START} ~ {END}")
    print(f"训练集抽样: seed={SEED}, sample={TRAIN_SAMPLE_SIZE}")

    # 1. 全池
    all_codes = sorted([f.stem for f in ZZ500_5MIN_DIR.glob("*.json")])
    print(f"\n全池股票数: {len(all_codes)}")

    # 2. 复现训练集100只（F5 口径：seed=42 从全池抽样100）
    random.seed(SEED)
    training_100 = set(random.sample(all_codes, TRAIN_SAMPLE_SIZE))
    print(f"训练集(seed=42抽样): {len(training_100)} 只")

    # 3. 振幅筛选 → 36只（J0-J4 / F5 共用口径）
    print(f"\n正在运行振幅筛选（口径与 backtest_zz500.py 一致）...")
    passed_36 = filter_codes_by_amplitude(all_codes, ZZ500_5MIN_DIR, START, END, min_amp)
    passed_36_set = set(passed_36)
    print(f"振幅筛选通过: {len(passed_36)} 只")

    # 4. 重叠分析
    train_in_36 = passed_36_set & training_100          # 训练集 ∩ 36
    oos_in_36 = passed_36_set - training_100            # 36 − 训练集 = OOS

    print(f"\n{'=' * 80}")
    print(f"重叠分析（核心结论）")
    print(f"{'=' * 80}")
    print(f"  36只 ∩ 训练集100只 = {len(train_in_36)} 只 （训练集，F5 也用过）")
    print(f"  36只 − 训练集100只 = {len(oos_in_36)} 只 （F5 的样本外 OOS）")
    print(f"  重叠比例: {len(train_in_36)/len(passed_36)*100:.1f}% 训练 / {len(oos_in_36)/len(passed_36)*100:.1f}% OOS")

    print(f"\n{'─' * 80}")
    print(f"J0-J4 调参36只完整清单（按代码排序）:")
    print(f"{'─' * 80}")
    for i, code in enumerate(sorted(passed_36_set), 1):
        tag = "训练" if code in training_100 else "OOS "
        print(f"  {i:2d}. {code}  [{tag}]")

    print(f"\n{'─' * 80}")
    print(f"训练集重叠 {len(train_in_36)} 只:")
    print(f"{'─' * 80}")
    print(f"  {sorted(train_in_36)}")
    print(f"\n{'─' * 80}")
    print(f"OOS {len(oos_in_36)} 只（J0-J4 调参时当训练集用，但 F5 里是独立样本）:")
    print(f"{'─' * 80}")
    print(f"  {sorted(oos_in_36)}")

    # 5. 核心判断
    print(f"\n{'=' * 80}")
    print(f"核心判断")
    print(f"{'=' * 80}")
    expected_oos = 27
    expected_train = 9
    ok_oos = len(oos_in_36) == expected_oos
    ok_train = len(train_in_36) == expected_train
    print(f"  预期(F5): 训练集重叠={expected_train}, OOS={expected_oos}")
    print(f"  实际:     训练集重叠={len(train_in_36)}, OOS={len(oos_in_36)}")
    print(f"  [{'✓' if ok_train and ok_oos else '✗'}] 与F5的27只OOS结论一致")

    if ok_train and ok_oos:
        print(f"\n  结论: J0-J4 调参用的36只 == F5的36只（同一振幅筛选口径）。")
        print(f"        其中 {len(oos_in_36)} 只在F5里是独立OOS样本，但J0-J4把它们当训练集用了。")
        print(f"        → stop_loss=0.002 / trailing_ratio=0.2 / J2参数 缺独立OOS验证，存在过拟合风险。")
    else:
        print(f"\n  ⚠ 数量与F5预期不符，需排查原因（数据更新/筛选口径漂移）。")

    # 6. 保存清单供步骤3使用
    out_dir = PROJECT_ROOT / "outputs" / "oos_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "step1_sample_overlap.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "step": "oos_step1_sample_overlap",
            "amplitude_threshold": min_amp,
            "amplitude_window": AMP_WINDOW,
            "backtest_range": [START, END],
            "seed": SEED,
            "train_sample_size": TRAIN_SAMPLE_SIZE,
            "training_100": sorted(training_100),
            "passed_36": sorted(passed_36_set),
            "train_in_36": sorted(train_in_36),
            "oos_in_36": sorted(oos_in_36),
            "overlap_count": len(train_in_36),
            "oos_count": len(oos_in_36),
            "consistent_with_F5": ok_train and ok_oos,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n清单已保存: {out_path}")

    return 0 if ok_train and ok_oos else 1


if __name__ == "__main__":
    sys.exit(main())
