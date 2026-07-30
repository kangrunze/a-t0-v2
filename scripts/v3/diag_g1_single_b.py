"""V3 G1 单股 B组诊断：检查 alpha 评分模式下的交易笔数和性能。"""
import sys
from pathlib import Path
from dataclasses import replace

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import load_multi_day_zz500
from at0.paths import ZZ500_5MIN_DIR
from at0.backtest import backtest_multi_day
from at0.config import load_signal_params, load_risk_params, load_backtest_params

code = "000032"
start = "2024-01-01"
end = "2026-07-22"

sp = replace(load_signal_params(), use_continuous_alpha=True,
             strategy_mode="trend_following", alpha_threshold_open=60.0)
rp = load_risk_params()
bp = replace(load_backtest_params(), signal_params=sp, risk_params=rp)

daily_bars, daily_prev, daily_meta = load_multi_day_zz500(code, start, end, ZZ500_5MIN_DIR)
print(f"days={len(daily_bars)}")

import time
t0 = time.time()
result = backtest_multi_day(code, daily_bars, daily_prev, bp)
elapsed = time.time() - t0

trades = []
for dr in result.get("daily_results", []):
    trades.extend(dr.get("trades", []))

print(f"elapsed={elapsed:.1f}s, trades={len(trades)}")
print(f"net_pnl={result.get('net_pnl', 0):.2f}")
print(f"win_rate={result.get('win_rate', 0)*100:.1f}%")
