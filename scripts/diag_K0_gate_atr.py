#!/usr/bin/env python3
"""
K0 闸门诊断（ATR标准化版）
=========================
用修复后的VWAP，重新计算 vwap_dev 偏离后 N根bar 内延续 vs 回归 VWAP 的比例。
**分桶改为 ATR 标准化**：|vwap_dev| / atr_relative（该股票自身波动率的倍数），
而非固定百分比，以消除不同股票波动率差异。

口径对齐原始 "65%延续/29%回归" 分析：
  - 前瞻窗口 N=6 根 5min bar（30分钟）
  - vwap_dev > 0 且 future_return > 0 → 延续（继续涨）
  - vwap_dev > 0 且 future_return ≤ 0 → 回归（跌回VWAP）
  - vwap_dev < 0 对称处理

数据：振幅筛选后的36只股票，2023-07-25 ~ 2026-07-22（3年）
VWAP：复用 features.py 的 cumulative_vwap（typical_price × volume，修复版）
ATR：复用 features.py 的 intraday_atr + atr_relative（全局统一公式）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.features import cumulative_vwap, intraday_atr, atr_relative, vwap_deviation

# 振幅筛选后的36只
POOL_36 = [
    "001221", "001309", "001389", "002261", "300339", "300475", "300548",
    "300570", "300620", "300735", "300757", "300972", "301526", "301536",
    "301606", "301611", "600602", "601099", "603000", "603119", "603175",
    "603728", "688166", "688213", "688318", "688322", "688331", "688361",
    "688411", "688498", "688582", "688615", "688629", "688692", "688702",
    "688709",
]

# ATR 标准化分桶（|vwap_dev| / atr_relative 的倍数）
# z=1.0 表示偏离度等于1倍ATR相对值
ATR_BUCKETS = [
    (0.5, 1.0, "0.5-1.0x ATR"),
    (1.0, 1.5, "1.0-1.5x ATR"),
    (1.5, 2.0, "1.5-2.0x ATR"),
    (2.0, 2.5, "2.0-2.5x ATR"),
    (2.5, 3.0, "2.5-3.0x ATR"),
    (3.0, 4.0, "3.0-4.0x ATR"),
    (4.0, 999, "4.0x+ ATR"),
]

LOOKFORWARD = 6  # 30分钟后的价格（6根5min bar）


def load_stock_bars(code: str) -> dict:
    """加载5min K线，返回 {date: [bars]}"""
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def compute_dev_and_future(bars: list[dict]) -> list[dict]:
    """
    计算当日每根bar的 vwap_dev、atr_relative、z_score、future_return。

    复用 features.py 的函数确保公式统一：
      - cumulative_vwap(bars[:i+1]) → 截至当前bar的VWAP
      - intraday_atr(bars[:i+1], period=14) → 日内ATR
      - atr_relative(atr, vwap) = ATR / VWAP
      - vwap_deviation(close, vwap) = (close - vwap) / vwap
      - z_score = |vwap_dev| / atr_relative（标准化偏离度）

    返回 [{vwap_dev, atr_rel, z_score, future_return}, ...]
    """
    if not bars or len(bars) < 20:  # ATR需要14+1根warmup
        return []

    results = []
    for i in range(len(bars)):
        if i + LOOKFORWARD >= len(bars):
            break  # 需要后续N根bar计算future_return

        bars_up_to_i = bars[: i + 1]

        # 复用 features.py 的 cumulative_vwap（修复版：typical_price × volume）
        vwap = cumulative_vwap(bars_up_to_i)
        if vwap is None or vwap <= 0:
            continue

        close = float(bars[i]["close"])

        # 复用 features.py 的 vwap_deviation
        vwap_dev = vwap_deviation(close, vwap)
        if vwap_dev is None:
            continue

        # 复用 features.py 的 intraday_atr（period=14）
        atr = intraday_atr(bars_up_to_i, period=14)
        if atr is None or atr <= 0:
            continue

        # 复用 features.py 的 atr_relative（ATR / VWAP）
        atr_rel = atr_relative(atr, vwap)
        if atr_rel is None or atr_rel <= 0:
            continue

        # z_score = |vwap_dev| / atr_relative（标准化偏离度）
        z_score = abs(vwap_dev) / atr_rel

        # future_return
        future_close = float(bars[i + LOOKFORWARD]["close"])
        future_return = (future_close - close) / close

        results.append({
            "vwap_dev": vwap_dev,
            "atr_rel": atr_rel,
            "z_score": z_score,
            "future_return": future_return,
        })

    return results


def classify(vwap_dev: float, future_return: float) -> str:
    """分类：延续 vs 回归（口径对齐原始分析）。"""
    if vwap_dev > 0:
        return "continuation" if future_return > 0 else "reversion"
    else:
        return "continuation" if future_return < 0 else "reversion"


def main():
    print("=" * 100)
    print("K0 闸门诊断（ATR标准化分桶版）")
    print("=" * 100)
    print(f"数据: 振幅筛选36只 × 3年（2023-07-25 ~ 2026-07-22）")
    print(f"VWAP: 修复后 cumulative_vwap（typical_price × volume，复用 features.py）")
    print(f"ATR: intraday_atr(period=14) + atr_relative（复用 features.py）")
    print(f"前瞻窗口: {LOOKFORWARD}根5min bar（30分钟）")
    print(f"分桶: |vwap_dev| / atr_relative 的 ATR 倍数（z-score标准化）")

    # 收集所有记录
    all_records = []
    for code in POOL_36:
        daily_bars = load_stock_bars(code)
        stock_count = 0
        for date, bars in daily_bars.items():
            if date < "2023-07-25" or date > "2026-07-22":
                continue
            records = compute_dev_and_future(bars)
            all_records.extend(records)
            stock_count += len(records)
        print(f"  {code}: {stock_count} 条记录")

    n_total = len(all_records)
    print(f"\n总记录数: {n_total}")

    if n_total == 0:
        print("无数据，退出")
        return

    # z_score 分布概览
    z_scores = [r["z_score"] for r in all_records]
    z_sorted = sorted(z_scores)
    print(f"\nz_score 分布: 均值={mean(z_scores):.2f} 中位={z_sorted[n_total//2]:.2f} "
          f"P25={z_sorted[n_total//4]:.2f} P75={z_sorted[3*n_total//4]:.2f} "
          f"最大={max(z_scores):.2f}")

    # 分桶统计
    print(f"\n{'=' * 100}")
    print(f"分桶统计：延续 vs 回归（ATR标准化）")
    print(f"{'=' * 100}")
    print(f"\n{'z_score分桶':<16} {'样本':>7} {'P(延续)':>8} {'P(回归)':>8} "
          f"{'E(延续幅)':>10} {'E(回归幅)':>10} {'期望值':>10} {'判断':>10}")
    print(f"{'-'*16} {'-'*7} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    any_reversion_dominant = False
    bucket_results = []

    for lo, hi, label in ATR_BUCKETS:
        in_bucket = [r for r in all_records if lo <= r["z_score"] < hi]
        n = len(in_bucket)
        if n == 0:
            print(f"{label:<16} {0:>7} {'N/A':>8} {'N/A':>8} "
                  f"{'N/A':>10} {'N/A':>10} {'N/A':>10} {'N/A':>10}")
            continue

        cont_amps = []
        rev_amps = []
        for r in in_bucket:
            typ = classify(r["vwap_dev"], r["future_return"])
            amp = abs(r["future_return"])
            if typ == "continuation":
                cont_amps.append(amp)
            else:
                rev_amps.append(amp)

        p_cont = len(cont_amps) / n
        p_rev = len(rev_amps) / n
        e_cont = mean(cont_amps) if cont_amps else 0
        e_rev = mean(rev_amps) if rev_amps else 0
        expected_value = p_rev * e_rev - p_cont * e_cont

        # 判断标准：回归概率≥50%且期望值为正
        if p_rev >= 0.50 and expected_value > 0:
            judgment = "✓ 回归占优"
            any_reversion_dominant = True
        elif p_rev >= 0.45 and expected_value > 0:
            judgment = "~ 接近"
        else:
            judgment = "✗ 延续占优"

        print(f"{label:<16} {n:>7} {p_cont*100:>7.1f}% {p_rev*100:>7.1f}% "
              f"{e_cont*100:>9.3f}% {e_rev*100:>9.3f}% "
              f"{expected_value*100:>+9.4f}% {judgment:>10}")

        bucket_results.append({
            "bucket": label,
            "z_lo": lo, "z_hi": hi,
            "n": n,
            "p_continuation": round(p_cont, 4),
            "p_reversion": round(p_rev, 4),
            "e_continuation": round(e_cont, 6),
            "e_reversion": round(e_rev, 6),
            "expected_value": round(expected_value, 6),
            "judgment": judgment,
        })

    # 全局统计（对齐原始 65%/29% 口径）
    cont_all = sum(1 for r in all_records if classify(r["vwap_dev"], r["future_return"]) == "continuation")
    rev_all = n_total - cont_all
    print(f"\n{'=' * 100}")
    print(f"全局统计（所有偏离幅度合并，对齐原始65%/29%口径）")
    print(f"{'=' * 100}")
    print(f"  总样本: {n_total}")
    print(f"  P(延续): {cont_all/n_total*100:.1f}%")
    print(f"  P(回归): {rev_all/n_total*100:.1f}%")

    # 按方向拆分（vwap_dev>0 vs <0）
    pos_records = [r for r in all_records if r["vwap_dev"] > 0]
    neg_records = [r for r in all_records if r["vwap_dev"] < 0]
    pos_cont = sum(1 for r in pos_records if classify(r["vwap_dev"], r["future_return"]) == "continuation")
    neg_cont = sum(1 for r in neg_records if classify(r["vwap_dev"], r["future_return"]) == "continuation")
    print(f"\n  按方向拆分:")
    print(f"    vwap_dev>0（价格在VWAP上方）: {len(pos_records)} 样本, P(延续)={pos_cont/max(len(pos_records),1)*100:.1f}%")
    print(f"    vwap_dev<0（价格在VWAP下方）: {len(neg_records)} 样本, P(延续)={neg_cont/max(len(neg_records),1)*100:.1f}%")

    # K0 闸门判断
    print(f"\n{'=' * 100}")
    print(f"K0 闸门判断")
    print(f"{'=' * 100}")
    if any_reversion_dominant:
        dom_buckets = [b for b in bucket_results if "✓" in b.get("judgment", "")]
        print(f"  ✓ 有 {len(dom_buckets)} 个分桶出现'回归概率≥50%且期望值为正':")
        for b in dom_buckets:
            print(f"    - {b['bucket']}: P(回归)={b['p_reversion']*100:.1f}%, 期望值={b['expected_value']*100:+.4f}%")
        print(f"  → 存在回归概率显著超过延续的偏离区间")
        print(f"  → 建议启动 Stage K（均值回归分支）")
    else:
        close_buckets = [b for b in bucket_results if "~" in b.get("judgment", "")]
        if close_buckets:
            print(f"  ~ 有 {len(close_buckets)} 个分桶接近临界（回归概率≥45%且期望值为正）:")
            for b in close_buckets:
                print(f"    - {b['bucket']}: P(回归)={b['p_reversion']*100:.1f}%, 期望值={b['expected_value']*100:+.4f}%")
            print(f"  → 回归概率未显著超过延续，但接近临界")
            print(f"  → 建议转向 Dynamic Risk（按振幅动态化止损止盈）")
        else:
            print(f"  ✗ 所有分桶都是延续占优")
            print(f"  → 不存在回归概率显著超过延续的偏离区间")
            print(f"  → 建议转向 Dynamic Risk（按振幅动态化止损止盈）")

    # 保存
    out = {
        "step": "K0_gate_atr_normalized",
        "data": "振幅筛选36只 × 3年",
        "vwap": "修复后 cumulative_vwap（features.py）",
        "atr": "intraday_atr(14) + atr_relative（features.py）",
        "lookforward_bars": LOOKFORWARD,
        "bucket_type": "ATR标准化 z-score",
        "buckets": bucket_results,
        "global": {
            "n_total": n_total,
            "p_continuation": round(cont_all / n_total, 4),
            "p_reversion": round(rev_all / n_total, 4),
        },
        "by_direction": {
            "vwap_dev_positive": {
                "n": len(pos_records),
                "p_continuation": round(pos_cont / max(len(pos_records), 1), 4),
            },
            "vwap_dev_negative": {
                "n": len(neg_records),
                "p_continuation": round(neg_cont / max(len(neg_records), 1), 4),
            },
        },
        "gate_passed": any_reversion_dominant,
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "K0_gate_atr_normalized.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
