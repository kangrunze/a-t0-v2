"""V3 性能分析：定位 Engine 评分函数的瓶颈。"""
import sys
import time
import cProfile
import pstats
from pathlib import Path
from io import StringIO
from dataclasses import replace

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import load_multi_day_zz500
from at0.paths import ZZ500_5MIN_DIR
from at0.backtest import backtest_multi_day
from at0.config import load_signal_params, load_risk_params, load_backtest_params

code = "000032"
start = "2026-04-01"
end = "2026-07-22"

sp = replace(load_signal_params(), use_continuous_alpha=True,
             strategy_mode="trend_following", alpha_threshold_open=70.0)
rp = load_risk_params()
bp = replace(load_backtest_params(), signal_params=sp, risk_params=rp)

daily_bars, daily_prev, daily_meta = load_multi_day_zz500(code, start, end, ZZ500_5MIN_DIR)
print(f"days={len(daily_bars)}")

# cProfile 分析
pr = cProfile.Profile()
pr.enable()
result = backtest_multi_day(code, daily_bars, daily_prev, bp)
pr.disable()

stats = pstats.Stats(pr)
stats.sort_stats('cumulative')

# 打印 top 20 最耗时的函数
s = StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
ps.print_stats(30)
print(s.getvalue())

# 也打印按时间排序
s2 = StringIO()
ps2 = pstats.Stats(pr, stream=s2).sort_stats('tottime')
ps2.print_stats(20)
print(s2.getvalue())
