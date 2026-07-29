#!/usr/bin/env python3
"""
MR1 买卖点位置深度分析
=====================
从 report.json 的 daily_results 提取配对交易，分析：
1. 开仓位置：sell 开仓时 fill_price 相对 vwap 的偏离度
2. 买卖价差分布：(sell_fill - buy_fill) / sell_fill
3. 持仓时长分布（holding_bars）
4. 平仓方式分布（stopped / paired / expired）
5. 止损单 vs 配对单的价差对比
6. 开仓深度 vs 买卖价差的关系（开仓越深价差越大？）
"""
from __future__ import annotations
import json
import statistics
from collections import Counter
from pathlib import Path

REPORT = Path(r"d:\project\a-t0-v2\outputs\backtest\689009_689009_MR1_默认_compare_2025-07-28_2026-07-22_report.json")


def load_daily_trades(path: Path) -> list[dict]:
    """从 report.json 提取所有交易，按时间顺序，附加 date 字段。"""
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    trades = []
    for dr in r.get("daily_results", []):
        date = dr.get("date", "")
        for t in dr.get("trades", []):
            t["date"] = date
            trades.append(t)
    return trades


def pair_trades(trades: list[dict]) -> list[dict]:
    """
    将 sell 开仓与后续 buy 平仓配对。
    MR 模式：sell 腿有 rules_fired（开仓），紧随的 buy 腿 rules_fired 为空（平仓）。
    每日可能有多个配对。按时间顺序 sell -> buy 配对。
    """
    pairs = []
    open_leg = None
    for t in trades:
        is_open = bool(t.get("rules_fired")) and t.get("status") == "open"
        is_pair = not t.get("rules_fired") or t.get("paired", False)

        if is_open and t.get("direction") == "sell":
            # 新的 sell 开仓
            if open_leg is not None:
                # 前一个 open 未配对（跨日 expired 等）
                pass
            open_leg = t
        elif open_leg is not None and t.get("direction") == "buy":
            # 配对平仓
            pairs.append({
                "sell": open_leg,
                "buy": t,
            })
            open_leg = None
    return pairs


def analyze_open_position(pairs: list[dict]) -> None:
    """分析 sell 开仓时 fill_price 相对 vwap 的偏离度。"""
    print("\n" + "=" * 70)
    print("1. MR 开仓位置（sell 相对 VWAP 偏离度，正=卖在VWAP上方）")
    print("=" * 70)
    devs = []
    for p in pairs:
        s = p["sell"]
        vwap = s.get("vwap", 0)
        if vwap and vwap > 0:
            dev = (s["fill_price"] - vwap) / vwap
            devs.append(dev)
    if not devs:
        print("无有效开仓数据")
        return
    devs_sorted = sorted(devs)
    n = len(devs)
    print(f"样本数: {n}")
    print(f"均值:   {statistics.mean(devs)*100:+.3f}%")
    print(f"中位数: {statistics.median(devs)*100:+.3f}%")
    print(f"P25:    {devs_sorted[n//4]*100:+.3f}%")
    print(f"P75:    {devs_sorted[3*n//4]*100:+.3f}%")
    print(f"最小:   {min(devs)*100:+.3f}%")
    print(f"最大:   {max(devs)*100:+.3f}%")
    # 分布
    bins = [(-99, -0.01), (-0.01, 0), (0, 0.005), (0.005, 0.01), (0.01, 0.02), (0.02, 0.05), (0.05, 99)]
    labels = ["< -1%", "-1%~0%", "0~0.5%", "0.5~1%", "1~2%", "2~5%", ">5%"]
    print("\n偏离度分布:")
    for (lo, hi), label in zip(bins, labels):
        cnt = sum(1 for d in devs if lo <= d < hi)
        bar = "#" * (cnt * 50 // max(n, 1))
        print(f"  {label:>10}: {cnt:>4} ({cnt/n*100:5.1f}%) {bar}")
    # 开仓方向正确性
    correct = sum(1 for d in devs if d > 0)
    print(f"\n开仓在VWAP上方（正确）: {correct}/{n} ({correct/n*100:.1f}%)")


def analyze_spread(pairs: list[dict]) -> None:
    """分析买卖价差 (sell_fill - buy_fill) / sell_fill。"""
    print("\n" + "=" * 70)
    print("2. 买卖价差分布 (sell - buy) / sell，正=盈利")
    print("=" * 70)
    spreads = []
    for p in pairs:
        s = p["sell"]["fill_price"]
        b = p["buy"]["fill_price"]
        if s > 0:
            spread = (s - b) / s
            spreads.append(spread)
    if not spreads:
        print("无有效配对")
        return
    n = len(spreads)
    spreads_sorted = sorted(spreads)
    print(f"样本数: {n}")
    print(f"均值:   {statistics.mean(spreads)*100:+.3f}%")
    print(f"中位数: {statistics.median(spreads)*100:+.3f}%")
    print(f"P25:    {spreads_sorted[n//4]*100:+.3f}%")
    print(f"P75:    {spreads_sorted[3*n//4]*100:+.3f}%")
    print(f"最小:   {min(spreads)*100:+.3f}%")
    print(f"最大:   {max(spreads)*100:+.3f}%")
    # 盈亏分布
    pos = sum(1 for s in spreads if s > 0)
    neg = sum(1 for s in spreads if s < 0)
    zero = sum(1 for s in spreads if s == 0)
    print(f"\n盈利: {pos} ({pos/n*100:.1f}%)  亏损: {neg} ({neg/n*100:.1f}%)  平: {zero}")
    # 价差分桶
    bins = [(-99, -0.01), (-0.01, 0), (0, 0.002), (0.002, 0.005), (0.005, 0.01), (0.01, 0.02), (0.02, 99)]
    labels = ["< -1%", "-1%~0%", "0~0.2%", "0.2~0.5%", "0.5~1%", "1~2%", ">2%"]
    print("\n价差分布:")
    for (lo, hi), label in zip(bins, labels):
        cnt = sum(1 for s in spreads if lo <= s < hi)
        bar = "#" * (cnt * 50 // max(n, 1))
        print(f"  {label:>10}: {cnt:>4} ({cnt/n*100:5.1f}%) {bar}")


def analyze_holding(pairs: list[dict]) -> None:
    """持仓时长分布。"""
    print("\n" + "=" * 70)
    print("3. 持仓时长分布（holding_bars，5min K线条数）")
    print("=" * 70)
    holds = [p["buy"].get("holding_bars", 0) for p in pairs]
    if not holds:
        print("无数据")
        return
    n = len(holds)
    print(f"样本数: {n}")
    print(f"均值:   {statistics.mean(holds):.1f} bars ({statistics.mean(holds)*5:.1f} min)")
    print(f"中位数: {statistics.median(holds):.1f} bars ({statistics.median(holds)*5:.1f} min)")
    print(f"最大:   {max(holds)} bars ({max(holds)*5} min)")
    # 分布
    print("\n持仓时长分布:")
    bins_map = [(0, 1, "0 (立即平仓)"), (1, 2, "1 bar (5min)"), (2, 5, "2-4 bars"),
                (5, 11, "5-10 bars"), (11, 21, "11-20 bars"), (21, 99, ">20 bars")]
    for lo, hi, label in bins_map:
        cnt = sum(1 for h in holds if lo <= h < hi)
        bar = "#" * (cnt * 50 // max(n, 1))
        print(f"  {label:>16}: {cnt:>4} ({cnt/n*100:5.1f}%) {bar}")


def analyze_exit_status(pairs: list[dict]) -> None:
    """平仓方式分布（stopped/paired/expired）。"""
    print("\n" + "=" * 70)
    print("4. 平仓方式分布")
    print("=" * 70)
    statuses = Counter(p["buy"].get("status", "unknown") for p in pairs)
    n = len(pairs)
    for st, cnt in statuses.most_common():
        print(f"  {st:>12}: {cnt:>4} ({cnt/n*100:5.1f}%)")

    # 按 status 分组看价差
    print("\n各平仓方式的买卖价差:")
    by_status = {}
    for p in pairs:
        st = p["buy"].get("status", "unknown")
        s = p["sell"]["fill_price"]
        b = p["buy"]["fill_price"]
        if s > 0:
            spread = (s - b) / s
            by_status.setdefault(st, []).append(spread)
    for st, sps in by_status.items():
        if sps:
            avg = statistics.mean(sps)
            print(f"  {st:>12}: 均值 {avg*100:+.3f}%  样本 {len(sps)}")


def analyze_open_depth_vs_spread(pairs: list[dict]) -> None:
    """开仓深度 vs 买卖价差关系。"""
    print("\n" + "=" * 70)
    print("5. 开仓深度 vs 买卖价差（开仓越深，价差应越大）")
    print("=" * 70)
    data = []
    for p in pairs:
        s = p["sell"]
        vwap = s.get("vwap", 0)
        if vwap and vwap > 0:
            open_dev = (s["fill_price"] - vwap) / vwap
            sell_p = s["fill_price"]
            buy_p = p["buy"]["fill_price"]
            if sell_p > 0:
                spread = (sell_p - buy_p) / sell_p
                data.append((open_dev, spread))
    if not data:
        print("无数据")
        return
    # 按开仓深度分桶
    bins = [(0, 0.005, "0~0.5%"), (0.005, 0.01, "0.5~1%"), (0.01, 0.02, "1~2%"),
            (0.02, 0.03, "2~3%"), (0.03, 0.05, "3~5%"), (0.05, 99, ">5%")]
    print(f"{'开仓深度':<12} {'样本':>6} {'均值价差':>10} {'中位价差':>10} {'胜率':>8}")
    print("-" * 60)
    for lo, hi, label in bins:
        group = [(od, sp) for od, sp in data if lo <= od < hi]
        if not group:
            continue
        sps = [sp for _, sp in group]
        avg = statistics.mean(sps)
        med = statistics.median(sps)
        wr = sum(1 for sp in sps if sp > 0) / len(sps)
        print(f"{label:<12} {len(group):>6} {avg*100:>+9.3f}% {med*100:>+9.3f}% {wr*100:>7.1f}%")


def analyze_stop_loss_impact(pairs: list[dict]) -> None:
    """止损单的损失分析。"""
    print("\n" + "=" * 70)
    print("6. 止损单损失分析（status=stopped）")
    print("=" * 70)
    stopped = [p for p in pairs if p["buy"].get("status") == "stopped"]
    if not stopped:
        print("无止损单")
        return
    losses = []
    for p in stopped:
        s = p["sell"]["fill_price"]
        b = p["buy"]["fill_price"]
        if s > 0:
            spread = (s - b) / s
            losses.append(spread)
    n = len(losses)
    total_loss_pct = sum(min(0, l) for l in losses) * 3000 * 59 / 100  # 粗略估算
    print(f"止损单数: {n}")
    print(f"止损单均值价差: {statistics.mean(losses)*100:+.3f}%")
    print(f"止损单中位价差: {statistics.median(losses)*100:+.3f}%")
    # 止损单持仓时长
    holds = [p["buy"].get("holding_bars", 0) for p in stopped]
    print(f"止损单持仓时长: 均值 {statistics.mean(holds):.1f} bars, 中位 {statistics.median(holds):.1f} bars")
    # 止损单开仓深度
    devs = []
    for p in stopped:
        s = p["sell"]
        vwap = s.get("vwap", 0)
        if vwap and vwap > 0:
            devs.append((s["fill_price"] - vwap) / vwap)
    if devs:
        print(f"止损单开仓深度: 均值 {statistics.mean(devs)*100:+.3f}%, 中位 {statistics.median(devs)*100:+.3f}%")


def main():
    trades = load_daily_trades(REPORT)
    print(f"总交易笔数: {len(trades)}")

    # 状态分布
    st_counter = Counter(t.get("status", "") for t in trades)
    print(f"status 分布: {dict(st_counter)}")

    pairs = pair_trades(trades)
    print(f"配对数: {len(pairs)}")

    analyze_open_position(pairs)
    analyze_spread(pairs)
    analyze_holding(pairs)
    analyze_exit_status(pairs)
    analyze_open_depth_vs_spread(pairs)
    analyze_stop_loss_impact(pairs)


if __name__ == "__main__":
    main()
