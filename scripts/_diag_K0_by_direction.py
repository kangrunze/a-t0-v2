#!/usr/bin/env python3
"""K0 补充：按方向（vwap_dev>0 vs <0）× ATR标准化分桶，检验不对称性。"""
from __future__ import annotations
import json, sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from at0.paths import ZZ500_5MIN_DIR
from at0.features import cumulative_vwap, intraday_atr, atr_relative, vwap_deviation

POOL_36 = [
    "001221", "001309", "001389", "002261", "300339", "300475", "300548",
    "300570", "300620", "300735", "300757", "300972", "301526", "301536",
    "301606", "301611", "600602", "601099", "603000", "603119", "603175",
    "603728", "688166", "688213", "688318", "688322", "688331", "688361",
    "688411", "688498", "688582", "688615", "688629", "688692", "688702",
    "688709",
]

ATR_BUCKETS = [
    (0.5, 1.0, "0.5-1.0x"), (1.0, 1.5, "1.0-1.5x"), (1.5, 2.0, "1.5-2.0x"),
    (2.0, 2.5, "2.0-2.5x"), (2.5, 3.0, "2.5-3.0x"), (3.0, 4.0, "3.0-4.0x"), (4.0, 999, "4.0x+"),
]
LOOKFORWARD = 6


def load_stock_bars(code):
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def compute_dev_and_future(bars):
    if not bars or len(bars) < 20:
        return []
    results = []
    for i in range(len(bars)):
        if i + LOOKFORWARD >= len(bars):
            break
        bars_up_to_i = bars[: i + 1]
        vwap = cumulative_vwap(bars_up_to_i)
        if vwap is None or vwap <= 0:
            continue
        close = float(bars[i]["close"])
        vwap_dev = vwap_deviation(close, vwap)
        if vwap_dev is None:
            continue
        atr = intraday_atr(bars_up_to_i, period=14)
        if atr is None or atr <= 0:
            continue
        atr_rel = atr_relative(atr, vwap)
        if atr_rel is None or atr_rel <= 0:
            continue
        z_score = abs(vwap_dev) / atr_rel
        future_close = float(bars[i + LOOKFORWARD]["close"])
        future_return = (future_close - close) / close
        results.append({"vwap_dev": vwap_dev, "z_score": z_score, "future_return": future_return})
    return results


def classify(vwap_dev, future_return):
    if vwap_dev > 0:
        return "continuation" if future_return > 0 else "reversion"
    else:
        return "continuation" if future_return < 0 else "reversion"


def analyze_direction(records, direction_label, direction_filter):
    """分析单方向（vwap_dev>0 或 <0）的分桶统计。"""
    subset = [r for r in records if direction_filter(r["vwap_dev"])]
    n_sub = len(subset)
    if n_sub == 0:
        return
    cont_all = sum(1 for r in subset if classify(r["vwap_dev"], r["future_return"]) == "continuation")
    print(f"\n{'=' * 100}")
    print(f"{direction_label}（样本 {n_sub}）")
    print(f"全局: P(延续)={cont_all/n_sub*100:.1f}%  P(回归)={(n_sub-cont_all)/n_sub*100:.1f}%")
    print(f"{'=' * 100}")
    print(f"\n{'z_score分桶':<14} {'样本':>7} {'P(延续)':>8} {'P(回归)':>8} "
          f"{'E(延续幅)':>10} {'E(回归幅)':>10} {'期望值':>10} {'判断':>10}")
    print(f"{'-'*14} {'-'*7} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    for lo, hi, label in ATR_BUCKETS:
        in_bucket = [r for r in subset if lo <= r["z_score"] < hi]
        n = len(in_bucket)
        if n == 0:
            print(f"{label:<14} {0:>7} {'N/A':>8} {'N/A':>8} {'N/A':>10} {'N/A':>10} {'N/A':>10} {'N/A':>10}")
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
        ev = p_rev * e_rev - p_cont * e_cont
        if p_rev >= 0.50 and ev > 0:
            judgment = "✓ 回归占优"
        elif p_rev >= 0.45 and ev > 0:
            judgment = "~ 接近"
        else:
            judgment = "✗ 延续占优"
        print(f"{label:<14} {n:>7} {p_cont*100:>7.1f}% {p_rev*100:>7.1f}% "
              f"{e_cont*100:>9.3f}% {e_rev*100:>9.3f}% {ev*100:>+9.4f}% {judgment:>10}")


def main():
    print("=" * 100)
    print("K0 补充：按方向 × ATR标准化分桶")
    print("=" * 100)

    all_records = []
    for code in POOL_36:
        daily_bars = load_stock_bars(code)
        for date, bars in daily_bars.items():
            if date < "2023-07-25" or date > "2026-07-22":
                continue
            all_records.extend(compute_dev_and_future(bars))

    print(f"总记录数: {len(all_records)}")

    # VWAP上方（vwap_dev > 0）：价格高于VWAP，回归=跌回
    analyze_direction(all_records, "VWAP上方（vwap_dev > 0，回归=价格下跌）", lambda v: v > 0)

    # VWAP下方（vwap_dev < 0）：价格低于VWAP，回归=价格上涨
    analyze_direction(all_records, "VWAP下方（vwap_dev < 0，回归=价格上涨）", lambda v: v < 0)


if __name__ == "__main__":
    main()
