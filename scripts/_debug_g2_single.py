"""单跑300735 B组验证动态止损是否生效。"""
import sys, json, statistics
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from backtest_zz500 import run_zz500_single, filter_codes_by_amplitude
from at0.paths import ZZ500_5MIN_DIR

# 计算振幅
code = "300735"
path = ZZ500_5MIN_DIR / f"{code}.json"
with open(path, encoding="utf-8") as f:
    d = json.load(f)
daily_bars = d.get("daily_bars", {})
sorted_dates = sorted(daily_bars.keys())
in_range = [d for d in sorted_dates if "2025-07-28" <= d <= "2026-07-22"]
use_dates = in_range[:60]
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
stock_amp = sum(amps) / len(amps)
print(f"300735 振幅 = {stock_amp:.4f} ({stock_amp*100:.2f}%)")

# 用一个假的中位数（比300735高，这样缩放因子<1）
median_amp = 0.06  # 6%
amp_table = {code: stock_amp}

override = {
    "bp": {
        "stop_loss_ratio": 0.002,
        "dynamic_stop_enabled": True,
        "amplitude_table": amp_table,
        "pool_median_amplitude": median_amp,
    },
    "sp": {"strategy_mode": "trend_following"},
}

print(f"\n运行B组（动态止损 ON，median={median_amp})...")
r = run_zz500_single(
    code=code, start_date="2025-07-28", end_date="2026-07-22",
    data_dir=ZZ500_5MIN_DIR, base_shares=3000,
    tag="g2_debug", params_override=override,
)
print(f"\n结果: T={r.get('total_trades',0)} 净={r.get('net_pnl',0):+.0f}")
