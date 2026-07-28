#!/usr/bin/env python3
"""调试603119 2026-04-21的VWAP计算"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.features import compute_reference_snapshot, cumulative_vwap, vwap_deviation

filepath = ZZ500_5MIN_DIR / "603119.json"
with open(filepath, "r", encoding="utf-8") as f:
    data = json.load(f)

bars = data["daily_bars"]["2026-04-21"]
print(f"K线数量: {len(bars)}")
print(f"首根K线: {bars[0]}")

# 手动计算累计VWAP
total_vol = 0
total_amt = 0
for i, bar in enumerate(bars[:20]):
    vol = bar.get("volume", 0)
    amt = bar.get("amount", 0)
    total_vol += vol
    total_amt += amt
    if total_vol > 0:
        vwap = total_amt / total_vol
    else:
        vwap = 0
    close = bar.get("close", 0)
    dev = (close - vwap) / vwap if vwap > 0 else 0
    print(f"  bar {i+1}: time={bar['time']}, close={close:.4f}, vol={vol}, amt={amt:.0f}, "
          f"vwap={vwap:.4f}, dev={dev*100:.2f}%")

# 用compute_reference_snapshot验证
print(f"\n用compute_reference_snapshot验证:")
prev_close = 58.6464
for i in [0, 5, 10, 15, 20, 30]:
    current_bars = bars[:i+1]
    snap = compute_reference_snapshot(current_bars, prev_close)
    vwap = snap.get("vwap", 0)
    vwap_dev = snap.get("vwap_dev", 0)
    close = current_bars[-1]["close"]
    print(f"  bar {i+1}: close={close:.4f}, vwap={vwap:.4f}, vwap_dev={vwap_dev*100:.2f}%")
