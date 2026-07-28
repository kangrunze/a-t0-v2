#!/usr/bin/env python3
"""
步骤4 调试：定位 diag_step4_ic_analysis.py 0 样本原因。

打印：
- daily_bars 结构（天数、第一天 bar 数、第一根 bar 字段）
- daily_prev 第一天值
- 在 i=WARMUP=30 处调用 compute_reference_snapshot，打印 snap 的 key 和值
- 检查 FACTORS 5 个字段名是否都在 snap 里
- 验证过滤条件 n < WARMUP + max(HORIZONS) + 1 = 51 是否把所有日都跳过
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from at0.features import compute_reference_snapshot
from backtest_zz500 import load_multi_day_zz500

CODE = "300140"
START_DATE = "2026-04-27"
END_DATE = "2026-07-23"
DATA_DIR = Path(r"D:\project\data\zz500_5min")

WARMUP = 30
HORIZONS = [1, 3, 5, 10, 20]
FACTORS = {
    "vwap_dev":   "vwap_dev",
    "rsi":        "rsi",
    "kdj_k":      "kdj_k",
    "adx":        "adx",
    "vol_ratio":  "volume_ratio",
}

THRESHOLD = WARMUP + max(HORIZONS) + 1  # 51


def main() -> int:
    print("=" * 78)
    print(f"调试：{CODE}  {START_DATE} ~ {END_DATE}")
    print("=" * 78)

    daily_bars, daily_prev, _ = load_multi_day_zz500(
        CODE, START_DATE, END_DATE, DATA_DIR,
    )
    if not daily_bars:
        print("[ERROR] daily_bars 为空")
        return 2

    dates = sorted(daily_bars.keys())
    print(f"daily_bars 天数: {len(dates)}")
    print(f"首日: {dates[0]}, 末日: {dates[-1]}")

    first_date = dates[0]
    first_bars = daily_bars[first_date]
    print(f"\n[首日] {first_date}  bar 数 = {len(first_bars)}")
    print(f"[首日] 第一根 bar: {first_bars[0]}")
    print(f"[首日] 最后一根 bar: {first_bars[-1]}")

    print(f"\n[daily_prev] 首日 prev_close = {daily_prev.get(first_date)}")

    # ── 关键：验证 n < 51 的过滤 ──
    print("\n" + "-" * 78)
    print(f"过滤条件：n < WARMUP + max(HORIZONS) + 1 = {THRESHOLD}")
    print(f"每根 bar 是 5min，A股一天 4 小时 = 48 根 bar")
    counts_per_day = [len(daily_bars[d]) for d in dates]
    n_pass = sum(1 for c in counts_per_day if c >= THRESHOLD)
    n_fail = sum(1 for c in counts_per_day if c < THRESHOLD)
    print(f"  通过天数 (n>={THRESHOLD}): {n_pass}")
    print(f"  跳过天数 (n<{THRESHOLD}): {n_fail}")
    print(f"  每日 bar 数 (前5天): {counts_per_day[:5]}")
    print(f"  每日 bar 数 最大/最小/中位: {max(counts_per_day)} / {min(counts_per_day)} / {sorted(counts_per_day)[len(counts_per_day)//2]}")

    # ── 在 i=WARMUP=30 处调用 compute_reference_snapshot ──
    # 注意：单日 48 根 bar，i=30 + max(HORIZONS)=20 → i+20=50 > 48，前瞻越界
    print("\n" + "-" * 78)
    print(f"在 i=WARMUP={WARMUP} 处调用 compute_reference_snapshot")
    bars = first_bars
    n = len(bars)
    print(f"首日 n={n}, range(WARMUP, n - max(HORIZONS)) = range({WARMUP}, {n - max(HORIZONS)})")
    print(f"  → range 上界 = {n - max(HORIZONS)} (若 <= WARMUP 则采样循环为空)")

    if WARMUP < n:
        i = WARMUP
        bars_up_to = bars[: i + 1]
        print(f"  调用 compute_reference_snapshot(bars[:{i+1}], prev_close={daily_prev.get(first_date)})")
        snap = compute_reference_snapshot(bars_up_to, prev_close=daily_prev.get(first_date))
        print(f"  snap 共 {len(snap)} 个 key")
        print("  snap 字段值:")
        for k, v in snap.items():
            if isinstance(v, float):
                if math.isnan(v):
                    print(f"    {k}: NaN")
                else:
                    print(f"    {k}: {v:.6f}")
            else:
                print(f"    {k}: {v}")

        # ── 检查 FACTORS 字段 ──
        print("\n  FACTORS 字段检查:")
        for fname, skey in FACTORS.items():
            val = snap.get(skey)
            isnone = val is None
            isnan = isinstance(val, float) and math.isnan(val)
            status = "OK"
            if isnone:
                status = "MISSING (None)"
            elif isnan:
                status = "NaN"
            print(f"    {fname} -> snap[{skey!r}] = {val}  [{status}]")
    else:
        print(f"  首日 n={n} < WARMUP={WARMUP}, 无法在 i=30 处采样")

    # ── 单日内可采样的 i 范围 ──
    print("\n" + "-" * 78)
    print(f"单日采样范围 (stride=2):")
    for d in dates[:3]:
        nb = len(daily_bars[d])
        lo = WARMUP
        hi = nb - max(HORIZONS)  # exclusive
        samples = list(range(lo, hi, 2)) if hi > lo else []
        print(f"  {d}: n={nb}, range({lo},{hi}) -> {len(samples)} 个 i 值 {samples[:5]}{'...' if len(samples)>5 else ''}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
