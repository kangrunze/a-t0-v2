"""快速验证动态止损是否在 backtest_single_day 内部生效。"""
import json
from pathlib import Path

# 300735: 振幅4.11%，低于中位数，应被缩放到 0.002 * (4.11/median) 
# A组（固定0.002）vs B组（动态）的止损单价差应不同
for group in ["A", "B"]:
    path = Path(rf"d:\project\a-t0-v2\outputs\backtest\300735_g2_{group}_300735_2025-07-28_2026-07-22_report.json")
    if not path.exists():
        print(f"{group}: 文件不存在")
        continue
    r = json.load(open(path, encoding="utf-8"))
    stopped = [t for dr in r["daily_results"] for t in dr.get("trades", []) if t.get("status") == "stopped"]
    print(f"\n{group}组 300735: stopped={len(stopped)}")
    for t in stopped[:5]:
        print(f"  dir={t['direction']} fill={t['fill_price']:.2f} pnl={t.get('pnl',0):+.2f}")
