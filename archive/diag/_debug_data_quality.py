#!/usr/bin/env python3
"""排查amount/volume不匹配问题"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR

# 检查多只股票多个日期的amount/volume关系
test_codes = ["603119", "000937", "300475", "688301", "300757"]

for code in test_codes:
    filepath = ZZ500_5MIN_DIR / f"{code}.json"
    if not filepath.exists():
        continue
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    daily_bars = data.get("daily_bars", {})
    sorted_dates = sorted(daily_bars.keys())

    print(f"\n{'='*70}")
    print(f"{code} ({data.get('name', '')})")
    print(f"数据范围: {sorted_dates[0]} ~ {sorted_dates[-1]}, 共{len(sorted_dates)}天")

    # 检查首日、中间日、最近日的数据
    check_dates = [sorted_dates[0], sorted_dates[len(sorted_dates)//2], sorted_dates[-1]]
    for date in check_dates:
        bars = daily_bars[date]
        if not bars:
            continue
        print(f"\n  日期: {date} ({len(bars)}根K线)")
        print(f"  {'时间':<22} {'open':>10} {'high':>10} {'low':>10} {'close':>10} {'volume':>12} {'amount':>14} {'amt/vol':>10} {'vs close':>10}")

        for i, bar in enumerate(bars[:5]):
            vol = bar.get("volume", 0)
            amt = bar.get("amount", 0)
            close = bar.get("close", 0)
            avg_price = amt / vol if vol > 0 else 0
            ratio = avg_price / close if close > 0 else 0
            print(f"  {bar['time']:<22} {bar['open']:>10.4f} {bar['high']:>10.4f} {bar['low']:>10.4f} {close:>10.4f} {vol:>12} {amt:>14.0f} {avg_price:>10.4f} {ratio:>10.2f}x")

    # 检查是否有复权标志
    meta = data.get("meta", {})
    print(f"\n  meta: {json.dumps(meta, ensure_ascii=False)}")
