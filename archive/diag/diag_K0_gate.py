#!/usr/bin/env python3
"""
Stage K — K0闸门诊断
=====================
用修复后的VWAP重新算延续/回归比例，按|vwap_dev|幅度分桶。

通过标准：至少有一个偏离分桶出现"回归概率≥50% 且 回归期望值为正"，
才推进K1。如果所有分桶都是延续占优，标记Stage K暂缓。

输出每个分桶的：
  - P(延续) / P(回归)
  - E(延续幅度) / E(回归幅度)
  - 期望值 = P(回归)×E(回归幅度) - P(延续)×E(延续幅度)

数据：振幅筛选后的36只股票，2023-07-25 ~ 2026-07-22（3年）
VWAP：用 features.py 里修复后的 cumulative_vwap（typical_price × volume）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR

# 振幅筛选后的36只（步骤1复现的清单）
POOL_36 = [
    "001221", "001309", "001389", "002261", "300339", "300475", "300548",
    "300570", "300620", "300735", "300757", "300972", "301526", "301536",
    "301606", "301611", "600602", "601099", "603000", "603119", "603175",
    "603728", "688166", "688213", "688318", "688322", "688331", "688361",
    "688411", "688498", "688582", "688615", "688629", "688692", "688702",
    "688709",
]

# 分桶边界（|vwap_dev| 绝对值）
BUCKETS = [
    (0.005, 0.010, "0.5%-1.0%"),
    (0.010, 0.015, "1.0%-1.5%"),
    (0.015, 0.020, "1.5%-2.0%"),
    (0.020, 0.030, "2.0%-3.0%"),
    (0.030, 0.050, "3.0%-5.0%"),
    (0.050, 0.080, "5.0%-8.0%"),
    (0.080, 1.000, "8.0%+"),
]


def load_stock_bars(code: str) -> dict:
    """加载5min K线，返回 {date: [bars]}"""
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def compute_vwap_dev_for_day(bars: list[dict]) -> list[tuple[float, float]]:
    """计算当日每根bar的 (vwap_dev, 后续N根bar的收益方向)

    vwap_dev = (price - vwap) / vwap  （修复后的VWAP）
    后续收益 = 5根bar后的价格 vs 当前价格

    返回 [(vwap_dev, future_return), ...]
    future_return > 0 表示价格延续当前方向（如果vwap_dev>0且future_return>0=延续）
    """
    if not bars or len(bars) < 10:
        return []

    # 修复后的VWAP：typical_price × volume 累积
    total_tp_vol = 0.0
    total_vol = 0.0
    results = []
    lookforward = 6  # 30分钟后的价格（6根5min bar）

    for i, bar in enumerate(bars):
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        vol = float(bar.get("volume", 0))
        tp = (high + low + close) / 3.0

        if vol > 0:
            total_tp_vol += tp * vol
            total_vol += vol

        if total_vol == 0:
            continue

        vwap = total_tp_vol / total_vol
        if vwap <= 0:
            continue

        vwap_dev = (close - vwap) / vwap  # 正=价格在VWAP上方，负=下方

        # 后续收益
        if i + lookforward >= len(bars):
            continue
        future_close = float(bars[i + lookforward]["close"])
        future_return = (future_close - close) / close

        results.append((vwap_dev, future_return))

    return results


def classify_continuation_reversion(vwap_dev: float, future_return: float) -> tuple[str, float]:
    """分类：延续 vs 回归

    vwap_dev > 0（价格在VWAP上方）:
      - future_return > 0 → 延续（继续涨）
      - future_return < 0 → 回归（跌回VWAP）

    vwap_dev < 0（价格在VWAP下方）:
      - future_return < 0 → 延续（继续跌）
      - future_return > 0 → 回归（涨回VWAP）

    返回 (类型, 幅度)
    幅度定义：延续时=future_return的绝对值，回归时=future_return的绝对值
    """
    if vwap_dev > 0:
        if future_return > 0:
            return "continuation", abs(future_return)
        else:
            return "reversion", abs(future_return)
    else:
        if future_return < 0:
            return "continuation", abs(future_return)
        else:
            return "reversion", abs(future_return)


def main():
    print("=" * 90)
    print("Stage K — K0闸门诊断（修复后VWAP）")
    print("=" * 90)
    print(f"\n数据: 振幅筛选36只 × 3年（2023-07-25 ~ 2026-07-22）")
    print(f"VWAP: 修复后 cumulative_vwap（typical_price × volume）")
    print(f"前瞻窗口: 6根5min bar（30分钟）")
    print(f"分桶: {len(BUCKETS)} 个 |vwap_dev| 区间")

    # 收集所有 (vwap_dev, future_return) 对
    all_pairs = []
    for code in POOL_36:
        daily_bars = load_stock_bars(code)
        stock_count = 0
        for date, bars in daily_bars.items():
            if date < "2023-07-25" or date > "2026-07-22":
                continue
            pairs = compute_vwap_dev_for_day(bars)
            all_pairs.extend([(v, r) for v, r in pairs])
            stock_count += len(pairs)
        print(f"  {code}: {stock_count} 条记录")

    print(f"\n总记录数: {len(all_pairs)}")

    # 分桶统计
    print(f"\n{'=' * 90}")
    print(f"分桶统计：延续 vs 回归")
    print(f"{'=' * 90}")
    print(f"\n{'|vwap_dev|分桶':<15s} {'样本':>7s} {'P(延续)':>8s} {'P(回归)':>8s} "
          f"{'E(延续幅)':>10s} {'E(回归幅)':>10s} {'期望值':>10s} {'判断':>8s}")
    print(f"{'-'*15} {'-'*7} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*8}")

    any_reversion_dominant = False
    bucket_results = []

    for lo, hi, label in BUCKETS:
        # 该分桶内的所有记录（按 |vwap_dev| 筛选）
        in_bucket = [(v, r) for v, r in all_pairs if lo <= abs(v) < hi]
        n = len(in_bucket)
        if n == 0:
            print(f"{label:<15s} {0:>7d} {'N/A':>8s} {'N/A':>8s} "
                  f"{'N/A':>10s} {'N/A':>10s} {'N/A':>10s} {'N/A':>8s}")
            continue

        cont_amplitudes = []
        rev_amplitudes = []
        for v, r in in_bucket:
            typ, amp = classify_continuation_reversion(v, r)
            if typ == "continuation":
                cont_amplitudes.append(amp)
            else:
                rev_amplitudes.append(amp)

        p_cont = len(cont_amplitudes) / n
        p_rev = len(rev_amplitudes) / n
        e_cont = mean(cont_amplitudes) if cont_amplitudes else 0
        e_rev = mean(rev_amplitudes) if rev_amplitudes else 0
        expected_value = p_rev * e_rev - p_cont * e_cont

        # 判断
        if p_rev >= 0.50 and expected_value > 0:
            judgment = "✓ 回归占优"
            any_reversion_dominant = True
        elif p_rev >= 0.45 and expected_value > 0:
            judgment = "~ 接近"
        else:
            judgment = "✗ 延续占优"

        print(f"{label:<15s} {n:>7d} {p_cont*100:>7.1f}% {p_rev*100:>7.1f}% "
              f"{e_cont*100:>9.3f}% {e_rev*100:>9.3f}% "
              f"{expected_value*100:>+9.4f}% {judgment:>8s}")

        bucket_results.append({
            "bucket": label,
            "lo": lo, "hi": hi,
            "n": n,
            "p_continuation": round(p_cont, 4),
            "p_reversion": round(p_rev, 4),
            "e_continuation": round(e_cont, 6),
            "e_reversion": round(e_rev, 6),
            "expected_value": round(expected_value, 6),
            "judgment": judgment,
        })

    # 全局统计
    n_total = len(all_pairs)
    cont_all = sum(1 for v, r in all_pairs
                   if classify_continuation_reversion(v, r)[0] == "continuation")
    rev_all = n_total - cont_all
    print(f"\n{'=' * 90}")
    print(f"全局统计（所有偏离幅度合并）")
    print(f"{'=' * 90}")
    print(f"  总样本: {n_total}")
    print(f"  P(延续): {cont_all/n_total*100:.1f}%")
    print(f"  P(回归): {rev_all/n_total*100:.1f}%")

    # K0闸门判断
    print(f"\n{'=' * 90}")
    print(f"K0闸门判断")
    print(f"{'=' * 90}")
    if any_reversion_dominant:
        print(f"  ✓ 至少有一个分桶出现'回归概率≥50%且期望值为正'")
        print(f"  → 推进K1（均值回归触发条件设计）")
    else:
        # 检查是否有"接近"的分桶
        close_buckets = [b for b in bucket_results if "~" in b.get("judgment", "")]
        if close_buckets:
            print(f"  ~ 有 {len(close_buckets)} 个分桶接近临界（回归概率≥45%且期望值为正）")
            print(f"  → 可谨慎推进K1，但需重点关注这些分桶的触发频率")
        else:
            print(f"  ✗ 所有分桶都是延续占优")
            print(f"  → 均值回归在当前数据上无优势，Stage K建议暂缓")

    # 保存
    out = {
        "step": "K0_gate_diagnosis",
        "data": "振幅筛选36只 × 3年",
        "vwap": "修复后 cumulative_vwap",
        "lookforward_bars": 6,
        "buckets": bucket_results,
        "global": {
            "n_total": n_total,
            "p_continuation": round(cont_all / n_total, 4),
            "p_reversion": round(rev_all / n_total, 4),
        },
        "gate_passed": any_reversion_dominant,
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "K0_gate_diagnosis.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
