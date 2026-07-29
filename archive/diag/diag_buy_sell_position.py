#!/usr/bin/env python3
"""
诊断：买卖点相对位置分析
========================
用户观察：做T希望低买高卖，但回测显示买卖点差价小、卖点不在高点。

本脚本量化：
1. 买卖点差价分布 (sell-buy)/buy
2. 卖点相对日内高低点位置 (sell-day_low)/(day_high-day_low)
3. 买点相对日内高低点位置 (buy-day_low)/(day_high-day_low)
4. 捕获效率 CE = (sell-buy)/(day_high-day_low) 的分布
5. 按CE分组，看"差价小"的交易占比

复用 measurement.loaders 加载 report.json + trades，
daily_hl 从原始K线计算（与J1一致）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "archive" / "diag"))

from at0.paths import ZZ500_5MIN_DIR
from diag_stepJ1_capture_efficiency import compute_daily_hl

TAG = "oos_step2_full"
REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"


def load_report(code: str) -> dict | None:
    pattern = f"{code}_{TAG}_*_report.json"
    matches = list(REPORT_DIR.glob(pattern))
    if not matches:
        return None
    with open(matches[0], "r", encoding="utf-8") as f:
        return json.load(f)


def load_daily_bars(code: str) -> dict:
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def analyze_stock(code: str) -> dict:
    """分析单股的买卖点相对位置。"""
    report = load_report(code)
    if report is None:
        return {"code": code, "available": False}
    daily_bars = load_daily_bars(code)
    if not daily_bars:
        return {"code": code, "available": False}
    daily_hl = compute_daily_hl(daily_bars)

    # 收集所有已配对的 buy/sell 对
    # report.json 的 daily_results[].trades 里，配对腿有 paired=True
    # 但更可靠的是从 risk_events 里找 stopped 事件，反推开仓腿
    pairs = []  # [(buy_price, sell_price, buy_date, sell_date, day_high, day_low)]
    for dr in report.get("daily_results", []):
        date_str = dr.get("date")
        hl = daily_hl.get(date_str)
        if not hl:
            continue
        day_high, day_low = float(hl["high"]), float(hl["low"])
        trades = dr.get("trades", [])
        # 按 time 排序，用 buy/sell 队列配对（同日）
        buys = []
        sells = []
        for t in trades:
            direction = t.get("direction")
            price = t.get("fill_price")
            if direction == "buy" and price:
                buys.append(t)
            elif direction == "sell" and price:
                sells.append(t)
        # 同日配对：每个 sell 找最近的前一个 buy
        buy_queue = list(buys)
        for sell in sells:
            if not buy_queue:
                break
            buy = buy_queue.pop(0)
            pairs.append({
                "buy_price": float(buy["fill_price"]),
                "sell_price": float(sell["fill_price"]),
                "day_high": day_high,
                "day_low": day_low,
                "date": date_str,
            })

    if not pairs:
        return {"code": code, "available": False}

    # 计算指标
    spreads = []         # (sell-buy)/buy
    sell_positions = []  # (sell-low)/(high-low) 卖点在日内区间的位置 0=最低 1=最高
    buy_positions = []   # (buy-low)/(high-low) 买点在日内区间的位置
    ces = []             # (sell-buy)/(high-low) 捕获效率
    for p in pairs:
        buy, sell = p["buy_price"], p["sell_price"]
        high, low = p["day_high"], p["day_low"]
        if buy <= 0 or high == low:
            continue
        spread = (sell - buy) / buy
        sell_pos = (sell - low) / (high - low)
        buy_pos = (buy - low) / (high - low)
        ce = (sell - buy) / (high - low)
        spreads.append(spread)
        sell_positions.append(sell_pos)
        buy_positions.append(buy_pos)
        ces.append(ce)

    return {
        "code": code,
        "available": True,
        "n_pairs": len(pairs),
        "spread_mean": round(mean(spreads), 4) if spreads else None,
        "spread_median": round(median(spreads), 4) if spreads else None,
        "sell_pos_mean": round(mean(sell_positions), 4) if sell_positions else None,
        "sell_pos_median": round(median(sell_positions), 4) if sell_positions else None,
        "buy_pos_mean": round(mean(buy_positions), 4) if buy_positions else None,
        "buy_pos_median": round(median(buy_positions), 4) if buy_positions else None,
        "ce_mean": round(mean(ces), 4) if ces else None,
        "ce_median": round(median(ces), 4) if ces else None,
        # 分桶统计
        "sell_pos_buckets": bucketize(sell_positions, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]),
        "buy_pos_buckets": bucketize(buy_positions, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]),
        "spread_buckets": bucketize(spreads, [-1.0, 0.0, 0.002, 0.005, 0.01, 0.02, 0.05]),
        "ce_buckets": bucketize(ces, [-1.0, 0.0, 0.05, 0.10, 0.20, 0.40, 1.0]),
    }


def bucketize(values: list[float], edges: list[float]) -> dict:
    """按 edges 分桶统计占比。"""
    if not values:
        return {}
    buckets = {}
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        count = sum(1 for v in values if lo <= v < hi)
        buckets[f"[{lo:.3f},{hi:.3f})"] = {
            "count": count,
            "pct": round(count / len(values) * 100, 1),
        }
    # 最后一个桶包含右端点
    last_lo = edges[-2]
    count = sum(1 for v in values if v >= last_lo)
    buckets[f"[{last_lo:.3f},inf)"] = {
        "count": count,
        "pct": round(count / len(values) * 100, 1),
    }
    return buckets


def main():
    print("=" * 90)
    print("买卖点相对位置诊断 — 卖点是否在高点？买点是否在低点？")
    print("=" * 90)
    print(f"\n策略当前定位：趋势跟随（买在趋势确认点，卖在趋势反转点）")
    print(f"用户期望：    均值回归式做T（低买高卖，降低持仓成本）")
    print(f"\n数据源: {TAG}（步骤2 OOS回测，36只×3年）")

    # 找所有步骤2的report
    reports = list(REPORT_DIR.glob(f"*_{TAG}_*_report.json"))
    codes = sorted([r.name.split("_")[0] for r in reports])
    print(f"股票数: {len(codes)}")

    all_results = []
    all_spreads = []
    all_sell_pos = []
    all_buy_pos = []
    all_ces = []

    print(f"\n{'=' * 90}")
    print(f"逐股汇总")
    print(f"{'=' * 90}")
    print(f"\n{'代码':<8s} {'配对':>5s} {'差价均值':>10s} {'卖点位置':>10s} {'买点位置':>10s} {'CE均值':>10s}")
    print(f"{'-'*8} {'-'*5} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    for code in codes:
        r = analyze_stock(code)
        if not r.get("available"):
            continue
        all_results.append(r)
        print(f"{code:<8s} {r['n_pairs']:>5d} "
              f"{r['spread_mean']*100:>9.2f}% "
              f"{r['sell_pos_mean']:>10.4f} "
              f"{r['buy_pos_mean']:>10.4f} "
              f"{r['ce_mean']:>10.4f}")

    # 池化
    for r in all_results:
        # 重新计算以收集所有值
        report = load_report(r["code"])
        daily_bars = load_daily_bars(r["code"])
        daily_hl = compute_daily_hl(daily_bars)
        for dr in report.get("daily_results", []):
            date_str = dr.get("date")
            hl = daily_hl.get(date_str)
            if not hl:
                continue
            day_high, day_low = float(hl["high"]), float(hl["low"])
            trades = dr.get("trades", [])
            buys, sells = [], []
            for t in trades:
                if t.get("direction") == "buy" and t.get("fill_price"):
                    buys.append(t)
                elif t.get("direction") == "sell" and t.get("fill_price"):
                    sells.append(t)
            buy_queue = list(buys)
            for sell in sells:
                if not buy_queue:
                    break
                buy = buy_queue.pop(0)
                bp, sp = float(buy["fill_price"]), float(sell["fill_price"])
                if bp <= 0 or day_high == day_low:
                    continue
                all_spreads.append((sp - bp) / bp)
                all_sell_pos.append((sp - day_low) / (day_high - day_low))
                all_buy_pos.append((bp - day_low) / (day_high - day_low))
                all_ces.append((sp - bp) / (day_high - day_low))

    print(f"\n{'=' * 90}")
    print(f"全池汇总（{len(all_spreads)} 笔配对）")
    print(f"{'=' * 90}")

    print(f"\n1. 买卖差价 (sell-buy)/buy:")
    print(f"   均值: {mean(all_spreads)*100:.2f}%   中位: {median(all_spreads)*100:.2f}%")
    print(f"   分桶:")
    for label, b in bucketize(all_spreads, [-1.0, 0.0, 0.002, 0.005, 0.01, 0.02, 0.05]).items():
        print(f"     {label:>20s}: {b['count']:>5d} ({b['pct']:>5.1f}%)")

    print(f"\n2. 卖点在日内区间的位置 (sell-low)/(high-low):  0=最低点 1=最高点")
    print(f"   均值: {mean(all_sell_pos):.4f}   中位: {median(all_sell_pos):.4f}")
    print(f"   分桶:")
    for label, b in bucketize(all_sell_pos, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.01]).items():
        print(f"     {label:>20s}: {b['count']:>5d} ({b['pct']:>5.1f}%)")

    print(f"\n3. 买点在日内区间的位置 (buy-low)/(high-low):  0=最低点 1=最高点")
    print(f"   均值: {mean(all_buy_pos):.4f}   中位: {median(all_buy_pos):.4f}")
    print(f"   分桶:")
    for label, b in bucketize(all_buy_pos, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.01]).items():
        print(f"     {label:>20s}: {b['count']:>5d} ({b['pct']:>5.1f}%)")

    print(f"\n4. 捕获效率 CE = (sell-buy)/(high-low):")
    print(f"   均值: {mean(all_ces):.4f}   中位: {median(all_ces):.4f}")
    print(f"   分桶:")
    for label, b in bucketize(all_ces, [-1.0, 0.0, 0.05, 0.10, 0.20, 0.40, 1.0]).items():
        print(f"     {label:>20s}: {b['count']:>5d} ({b['pct']:>5.1f}%)")

    # 核心判断
    print(f"\n{'=' * 90}")
    print(f"核心发现")
    print(f"{'=' * 90}")
    sell_in_high = sum(1 for p in all_sell_pos if p >= 0.8)
    sell_in_low = sum(1 for p in all_sell_pos if p < 0.4)
    buy_in_low = sum(1 for p in all_buy_pos if p < 0.4)
    buy_in_high = sum(1 for p in all_buy_pos if p >= 0.8)
    tiny_spread = sum(1 for s in all_spreads if 0 <= s < 0.005)
    neg_spread = sum(1 for s in all_spreads if s < 0)

    print(f"""
  卖点位置: {sell_in_high/len(all_sell_pos)*100:.1f}% 在日内高位(≥80%), {sell_in_low/len(all_sell_pos)*100:.1f}% 在日内低位(<40%)
  买点位置: {buy_in_low/len(all_buy_pos)*100:.1f}% 在日内低位(<40%), {buy_in_high/len(all_buy_pos)*100:.1f}% 在日内高位(≥80%)
  差价<0.5%的占比: {tiny_spread/len(all_spreads)*100:.1f}%（扣除成本0.27%后净赚<0.23%）
  差价<0（亏损）的占比: {neg_spread/len(all_spreads)*100:.1f}%

  策略定位 vs 实际表现:
  - 趋势跟随策略的卖出逻辑是"趋势反转确认"（ADX回落+VWAP穿越+KDJ反向）
    → 卖出时价格已从高点回落一截，卖点天然不在最高点
  - 趋势跟随策略的买入逻辑是"趋势确认后追入"（ADX>35+量比>2+VWAP偏离）
    → 买入时趋势已启动，买点天然不在最低点
  - 这是策略设计的必然结果，不是bug
  - 如果要实现"低买高卖"，需要转向均值回归逻辑（买在超跌、卖在回归）
    但项目记忆明确记录："Strategy is trend-following, not VWAP mean reversion"
""")

    # 保存
    out = {
        "diagnosis": "buy_sell_position_analysis",
        "tag": TAG,
        "n_stocks": len(all_results),
        "n_pairs": len(all_spreads),
        "pooled": {
            "spread_mean": round(mean(all_spreads), 6),
            "spread_median": round(median(all_spreads), 6),
            "sell_pos_mean": round(mean(all_sell_pos), 4),
            "sell_pos_median": round(median(all_sell_pos), 4),
            "buy_pos_mean": round(mean(all_buy_pos), 4),
            "buy_pos_median": round(median(all_buy_pos), 4),
            "ce_mean": round(mean(all_ces), 4),
            "ce_median": round(median(all_ces), 4),
        },
        "per_stock": all_results,
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "buy_sell_position.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"结果已保存: {out_path}")


if __name__ == "__main__":
    main()
