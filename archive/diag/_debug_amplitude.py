#!/usr/bin/env python3
"""调试振幅筛选"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR

# 手动计算几只股票的60日振幅
codes = ["603119", "000001", "000002", "600000", "000937"]
start_date = "2023-07-25"

for code in codes:
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
    if not filepath.exists():
        print(f"{code}: 文件不存在")
        continue

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    daily_bars_raw = data.get("daily_bars", {})
    sorted_dates = sorted(daily_bars_raw.keys())
    print(f"\n{code}: 共 {len(sorted_dates)} 个交易日")

    amplitudes = []
    for date in sorted_dates:
        if date >= start_date:
            break
        bars = daily_bars_raw[date]
        if not bars:
            continue
        highs = [b["high"] for b in bars if b.get("high", 0) > 0]
        lows = [b["low"] for b in bars if b.get("low", 0) > 0]
        if not highs or not lows:
            continue
        day_high = max(highs)
        day_low = min(lows)
        prev_close = bars[0]["open"]
        if prev_close > 0:
            amp = (day_high - day_low) / prev_close
            amplitudes.append(amp)

    print(f"  start_date之前有 {len(amplitudes)} 个交易日的振幅数据")
    if len(amplitudes) >= 60:
        avg_amp = sum(amplitudes[-60:]) / 60
        print(f"  60日平均振幅: {avg_amp*100:.2f}%")
        print(f"  阈值0.0494 = 4.94%")
        print(f"  是否通过: {avg_amp >= 0.0494}")
    else:
        print(f"  不足60天数据")

    if amplitudes:
        print(f"  最近10天振幅: {[f'{a*100:.2f}%' for a in amplitudes[-10:]]}")
