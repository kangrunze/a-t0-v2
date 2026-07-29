#!/usr/bin/env python3
"""
Stage K — 均值回归买卖点位置诊断
================================
用户观察：买卖点仍接近，未达"低买高卖"。
诊断内容：
  1. 买卖点相对日内高低点位置
  2. 每笔交易盈亏分布
  3. 平仓原因分布（止盈/止损/到期）
  4. 持仓时间分布
  5. 买卖差价分布
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR

TAG = "K_mr_no_amp_full500"
REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
START_DATE = "2025-07-28"
END_DATE = "2026-07-22"


def load_report(code: str) -> dict | None:
    path = REPORT_DIR / f"{code}_{TAG}_{START_DATE}_{END_DATE}_report.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_daily_bars(code: str) -> dict:
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def compute_daily_hl(daily_bars: dict) -> dict:
    """计算每日高低点。"""
    hl = {}
    for date, bars in daily_bars.items():
        if not bars:
            continue
        highs = [float(b.get("high", 0)) for b in bars if b.get("high", 0)]
        lows = [float(b.get("low", 0)) for b in bars if b.get("low", 0)]
        if highs and lows:
            hl[date] = {"high": max(highs), "low": min(lows)}
    return hl


def extract_pairs(report: dict, daily_hl: dict) -> list[dict]:
    """提取买卖配对，含日内高低点。"""
    pairs = []
    for dr in report.get("daily_results", []):
        date_str = dr.get("date")
        hl = daily_hl.get(date_str)
        if not hl:
            continue
        day_high, day_low = float(hl["high"]), float(hl["low"])
        trades = dr.get("trades", [])
        buys, sells = [], []
        for t in trades:
            direction = t.get("direction")
            price = t.get("fill_price")
            if direction == "buy" and price:
                buys.append(t)
            elif direction == "sell" and price:
                sells.append(t)
        # 同日配对
        buy_queue = list(buys)
        for sell in sells:
            if not buy_queue:
                break
            buy = buy_queue.pop(0)
            bp, sp = float(buy["fill_price"]), float(sell["fill_price"])
            # 提取平仓原因
            sell_rules = sell.get("rules_fired", [])
            exit_reason = "unknown"
            sell_text = " ".join(sell_rules) if sell_rules else ""
            if "止损" in sell_text or "[MR平仓]" not in sell_text and "stopped" in str(sell.get("reason", "")):
                exit_reason = "stop_loss"
            elif "[MR平仓]" in sell_text:
                exit_reason = "take_profit"
            elif "expired" in str(sell.get("reason", "")).lower() or "到期" in sell_text:
                exit_reason = "expired"
            # 持仓时间
            buy_time = buy.get("time", "")
            sell_time = sell.get("time", "")
            pairs.append({
                "buy_price": bp,
                "sell_price": sp,
                "day_high": day_high,
                "day_low": day_low,
                "buy_time": buy_time,
                "sell_time": sell_time,
                "exit_reason": exit_reason,
                "pnl": sp - bp,
            })
    return pairs


def analyze_stock(code: str) -> dict:
    report = load_report(code)
    if report is None:
        return {"code": code, "available": False}
    daily_bars = load_daily_bars(code)
    daily_hl = compute_daily_hl(daily_bars)
    pairs = extract_pairs(report, daily_hl)
    if not pairs:
        return {"code": code, "available": False}

    spreads = []
    sell_positions = []
    buy_positions = []
    ces = []
    pnls = []
    exit_reasons = Counter()
    holding_bars = []

    for p in pairs:
        bp, sp = p["buy_price"], p["sell_price"]
        high, low = p["day_high"], p["day_low"]
        if bp <= 0 or high == low:
            continue
        spread = (sp - bp) / bp
        sell_pos = (sp - low) / (high - low)
        buy_pos = (bp - low) / (high - low)
        ce = (sp - bp) / (high - low)
        spreads.append(spread)
        sell_positions.append(sell_pos)
        buy_positions.append(buy_pos)
        ces.append(ce)
        pnls.append(p["pnl"])
        exit_reasons[p["exit_reason"]] += 1
        # 持仓时间（简单用time差，可能不精确）
        try:
            bt = p["buy_time"].split(":")
            st = p["sell_time"].split(":")
            bmin = int(bt[0]) * 60 + int(bt[1])
            smin = int(st[0]) * 60 + int(st[1])
            if smin >= bmin:
                holding_bars.append((smin - bmin) / 5)  # 5min/bar
        except (ValueError, IndexError, AttributeError):
            pass

    return {
        "code": code,
        "available": True,
        "n_pairs": len(pairs),
        "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0,
        "spread_mean": round(mean(spreads), 4) if spreads else None,
        "spread_median": round(median(spreads), 4) if spreads else None,
        "sell_pos_mean": round(mean(sell_positions), 4) if sell_positions else None,
        "buy_pos_mean": round(mean(buy_positions), 4) if buy_positions else None,
        "ce_mean": round(mean(ces), 4) if ces else None,
        "avg_pnl": round(mean(pnls), 2) if pnls else None,
        "exit_reasons": dict(exit_reasons),
        "avg_holding_bars": round(mean(holding_bars), 1) if holding_bars else None,
    }


def main():
    # 取有代表性的股票：用户看的689009 + 随机抽样20只
    import random
    all_reports = list(REPORT_DIR.glob(f"*_{TAG}_{START_DATE}_{END_DATE}_report.json"))
    all_codes = sorted([r.name.split("_")[0] for r in all_reports])
    random.seed(42)
    sample_codes = ["689009"] + random.sample([c for c in all_codes if c != "689009"], 19)

    print("=" * 90)
    print("均值回归买卖点位置诊断（20只抽样）")
    print("=" * 90)
    print(f"\n{'代码':<8s} {'配对':>5s} {'胜率':>7s} {'差价均':>8s} {'买点位':>8s} {'卖点位':>8s} {'CE均':>8s} {'均盈亏':>8s} {'均持仓':>6s}")
    print(f"{'-'*8} {'-'*5} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")

    all_spreads = []
    all_sell_pos = []
    all_buy_pos = []
    all_ces = []
    all_pnls = []
    all_exit_reasons = Counter()
    all_holding = []
    all_win = 0
    all_total = 0

    for code in sample_codes:
        r = analyze_stock(code)
        if not r.get("available"):
            continue
        wr = r.get('win_rate', 0) or 0
        sp = r.get('spread_mean', 0) or 0
        bp = r.get('buy_pos_mean', 0) or 0
        slp = r.get('sell_pos_mean', 0) or 0
        ce = r.get('ce_mean', 0) or 0
        pnl = r.get('avg_pnl', 0) or 0
        hb = r.get('avg_holding_bars', 0) or 0
        print(f"{code:<8s} {r['n_pairs']:>5d} {wr*100:>6.1f}% "
              f"{sp*100:>7.3f}% {bp:>8.4f} "
              f"{slp:>8.4f} {ce:>8.4f} "
              f"{pnl:>+8.1f} {hb:>6.1f}")
        # 收集池化数据
        report = load_report(code)
        daily_bars = load_daily_bars(code)
        daily_hl = compute_daily_hl(daily_bars)
        pairs = extract_pairs(report, daily_hl)
        for p in pairs:
            bp, sp = p["buy_price"], p["sell_price"]
            high, low = p["day_high"], p["day_low"]
            if bp <= 0 or high == low:
                continue
            all_spreads.append((sp - bp) / bp)
            all_sell_pos.append((sp - low) / (high - low))
            all_buy_pos.append((bp - low) / (high - low))
            all_ces.append((sp - bp) / (high - low))
            all_pnls.append(p["pnl"])
            all_exit_reasons[p["exit_reason"]] += 1
            if p["pnl"] > 0:
                all_win += 1
            all_total += 1

    print(f"\n{'=' * 90}")
    print(f"池化汇总（{all_total} 笔）")
    print(f"{'=' * 90}")

    print(f"\n1. 胜率: {all_win/all_total*100:.1f}%")
    print(f"2. 买卖差价 (sell-buy)/buy:")
    print(f"   均值: {mean(all_spreads)*100:.3f}%   中位: {median(all_spreads)*100:.3f}%")
    print(f"3. 买点位置 (0=最低 1=最高): 均值 {mean(all_buy_pos):.4f}")
    print(f"4. 卖点位置 (0=最低 1=最高): 均值 {mean(all_sell_pos):.4f}")
    print(f"5. 捕获效率 CE: 均值 {mean(all_ces):.4f}")
    print(f"6. 平均盈亏: {mean(all_pnls):+.2f}")

    print(f"\n7. 平仓原因分布:")
    for reason, count in all_exit_reasons.most_common():
        print(f"   {reason:>15s}: {count:>5d} ({count/all_total*100:.1f}%)")

    print(f"\n8. 盈亏分布:")
    big_win = sum(1 for p in all_pnls if p > 50)
    small_win = sum(1 for p in all_pnls if 0 < p <= 50)
    small_loss = sum(1 for p in all_pnls if -50 <= p <= 0)
    big_loss = sum(1 for p in all_pnls if p < -50)
    print(f"   大赚(>+50):  {big_win:>5d} ({big_win/all_total*100:.1f}%)")
    print(f"   小赚(0~50):  {small_win:>5d} ({small_win/all_total*100:.1f}%)")
    print(f"   小亏(-50~0): {small_loss:>5d} ({small_loss/all_total*100:.1f}%)")
    print(f"   大亏(<-50):  {big_loss:>5d} ({big_loss/all_total*100:.1f}%)")

    # 核心判断
    print(f"\n{'=' * 90}")
    print(f"核心发现")
    print(f"{'=' * 90}")
    print(f"""
  买点位置: {mean(all_buy_pos):.4f} (0=日内最低, 1=日内最高)
  卖点位置: {mean(all_sell_pos):.4f}
  买卖点距离: {abs(mean(all_sell_pos)-mean(all_buy_pos)):.4f}

  vs 趋势跟随基准（之前诊断）:
    趋势跟随: 买点0.62 卖点0.64 CE=0.021
    均值回归:  买点{mean(all_buy_pos):.2f} 卖点{mean(all_sell_pos):.2f} CE={mean(all_ces):.3f}

  平仓原因中止损占比: {all_exit_reasons.get('stop_loss',0)/all_total*100:.1f}%
""")


if __name__ == "__main__":
    main()
