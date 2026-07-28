#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确认延迟损耗诊断（diag_confirm_delay_loss）
============================================
量化信号触发点距离实际极值点的偏离度，判断趋势确认逻辑（ADX35+量比2+KDJ连续2根反转）
是否过度保守。

数据源：复用 sample100_3y（baseline, cooldown=3, thresholds.yaml）的 report.json
K线源：D:\project\data\zz500_5min\{code}.json

指标定义：
  买入偏离度 = (buy_fill_price - min(low in future N bars)) / min(low in future N bars)
              —— 偏离度越大，买得离真实低点越远（确认延迟吃掉的空间）
  卖出偏离度 = (max(high in future N bars) - sell_fill_price) / sell_fill_price
              —— 偏离度越大，卖得离真实高点越远
  窗口 N = 6/12/24（30min / 1h / 2h），三个窗口都要

分组：
  - 整体
  - 盈利交易 vs 亏损交易（用所属配对交易的 pnl 判断）
  - 持仓时长：<6根 / 6-12根 / >12根

对照：round_trip_cost = 0.0027 (0.27%)，判断延迟损耗是"可忽略"还是"和成本同量级或更大"

注意：
  - 不改任何策略参数
  - 不只报均值，必须看分位数 p25/p50/p75
  - 不跳过"盈利组 vs 亏损组"对比
  - 诊断完停下汇报，不自行决定下一步
"""
import json
import statistics
import sys
from pathlib import Path
from collections import deque

# ── 路径配置 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(r"d:\project\a-t0-v2")
REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
DATA_DIR = Path(r"D:\project\data\zz500_5min")
DEFAULT_TAG = "sample100_3y"
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"

# 报告文件名模式
REPORT_PATTERN = "{code}_{tag}_{start}_{end}_report.json"

# 对照基准
ROUND_TRIP_COST = 0.0027  # 0.27%

# 窗口（5min bar 数）
WINDOWS = [6, 12, 24]
WINDOW_LABELS = {6: "30min", 12: "1h", 24: "2h"}


def quantile(data, q):
    """计算分位数 q∈[0,1]，用线性插值法。"""
    if not data:
        return float("nan")
    s = sorted(data)
    n = len(s)
    if n == 1:
        return s[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def fmt_pct(x):
    if x != x:  # NaN
        return "  N/A"
    return f"{x*100:7.3f}%"


def load_stock_bars(code):
    """加载一只股票的 daily_bars，返回 {date: [bar,...]}。"""
    path = DATA_DIR / f"{code}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("daily_bars", {})


def build_global_bar_sequence(daily_bars):
    """
    把按日分组的 bars 展平为全局序列，用于跨日查找"未来 N 根 bar"。

    返回:
      flat_bars: list[bar]  —— 全局 bar 序列
      time_to_global_idx: dict[str, int]  —— bar time -> 全局索引
    """
    flat_bars = []
    time_to_global_idx = {}
    for date_str in sorted(daily_bars.keys()):
        for bar in daily_bars[date_str]:
            time_to_global_idx[bar["time"]] = len(flat_bars)
            flat_bars.append(bar)
    return flat_bars, time_to_global_idx


def fifo_pair_trades(daily_results):
    """
    FIFO 配对：把 daily_results[].trades[] 里的开仓腿(paired=false)和平仓腿(paired=true)
    配对起来。

    返回: list of dict:
      {entry: trade, exit: trade, holding_bars: int, pnl: float}
    """
    # 先把所有 trade 按时间排序收集
    all_trades = []
    for dr in daily_results:
        date = dr["date"]
        for t in dr.get("trades", []):
            t["_date"] = date
            all_trades.append(t)

    # 按时间排序（time 字段是 "YYYY-MM-DD HH:MM:SS"，可直接字符串排序）
    all_trades.sort(key=lambda t: t.get("time", ""))

    pairs = []
    open_queues = {"buy": deque(), "sell": deque()}  # 待配对的开仓腿

    for t in all_trades:
        direction = t["direction"]
        is_paired = t.get("paired", False)

        if not is_paired:
            # 开仓腿入队
            open_queues[direction].append(t)
        else:
            # 平仓腿：从反向队列取开仓腿配对
            open_dir = "sell" if direction == "buy" else "buy"
            if open_queues[open_dir]:
                entry_leg = open_queues[open_dir].popleft()
                pairs.append({
                    "entry": entry_leg,
                    "exit": t,
                    "pnl": t.get("pnl", 0.0),
                })
            # 如果队列空（跨日遗留等），跳过不配对

    return pairs


def compute_holding_bars(pair, time_to_global_idx):
    """用开仓腿到平仓腿的全局 bar 差算持仓时长。"""
    entry_time = pair["entry"].get("time")
    exit_time = pair["exit"].get("time")
    if entry_time not in time_to_global_idx or exit_time not in time_to_global_idx:
        return None
    return time_to_global_idx[exit_time] - time_to_global_idx[entry_time]


def compute_deviation(trade, flat_bars, time_to_global_idx, n):
    """
    计算单条 trade 的偏离度。

    direction=buy:  偏离度 = (fill_price - min(low in future n bars)) / min(low)
    direction=sell: 偏离度 = (max(high in future n bars) - fill_price) / fill_price

    返回 float 或 None（数据不足时）。
    """
    time_str = trade.get("time")
    if time_str not in time_to_global_idx:
        return None

    idx = time_to_global_idx[time_str]
    future_bars = flat_bars[idx + 1: idx + 1 + n]
    if not future_bars:
        return None

    fill_price = trade.get("fill_price")
    if fill_price is None or fill_price <= 0:
        return None

    direction = trade["direction"]
    if direction == "buy":
        low_min = min(b["low"] for b in future_bars)
        if low_min <= 0:
            return None
        return (fill_price - low_min) / low_min
    else:  # sell
        high_max = max(b["high"] for b in future_bars)
        return (high_max - fill_price) / fill_price


def categorize_holding(hb):
    """持仓时长分组。"""
    if hb is None:
        return "unknown"
    if hb < 6:
        return "<6根(30min)"
    elif hb <= 12:
        return "6-12根(1h)"
    else:
        return ">12根(2h+)"


def collect_deviations(tag=DEFAULT_TAG):
    """
    遍历所有 100 只股票的 report.json，收集每条 trade 的偏离度。

    返回 list of dict:
      {direction, pnl, holding_bars, holding_cat,
       dev_6, dev_12, dev_24}
    """
    # 找出所有 report 文件
    reports = sorted(REPORT_DIR.glob(f"*_{tag}_{START_DATE}_{END_DATE}_report.json"))
    print(f"找到 {len(reports)} 份 report.json (tag={tag})")

    records = []
    stocks_loaded = 0

    for rp in reports:
        code = rp.name.split("_")[0]

        # 加载 report
        with open(rp, "r", encoding="utf-8") as f:
            report = json.load(f)

        if report.get("total_trades", 0) == 0:
            continue

        # 加载 K 线
        daily_bars = load_stock_bars(code)
        if not daily_bars:
            print(f"  [跳过] {code}: 无 K 线数据")
            continue

        flat_bars, time_to_idx = build_global_bar_sequence(daily_bars)

        # FIFO 配对
        pairs = fifo_pair_trades(report.get("daily_results", []))
        if not pairs:
            continue

        # 对每笔配对交易，计算开仓腿和平仓腿的偏离度
        for pair in pairs:
            holding_bars = compute_holding_bars(pair, time_to_idx)
            holding_cat = categorize_holding(holding_bars)
            pnl = pair["pnl"]
            is_profit = pnl > 0

            for leg_key in ("entry", "exit"):
                leg = pair[leg_key]
                direction = leg["direction"]

                rec = {
                    "code": code,
                    "direction": direction,
                    "is_profit_pair": is_profit,
                    "pnl": pnl,
                    "holding_bars": holding_bars,
                    "holding_cat": holding_cat,
                    "leg_role": leg_key,  # entry=开仓, exit=平仓
                }

                for n in WINDOWS:
                    rec[f"dev_{n}"] = compute_deviation(leg, flat_bars, time_to_idx, n)

                records.append(rec)

        stocks_loaded += 1
        if stocks_loaded % 10 == 0:
            print(f"  已处理 {stocks_loaded} 只股票, 累积 {len(records)} 条 leg 记录")

    print(f"共加载 {stocks_loaded} 只股票, {len(records)} 条 leg 记录")
    return records


def print_table(title, groups, records, dev_key):
    """打印一张分组分位数表。"""
    print(f"\n{'─' * 70}")
    print(f"  {title}  [{dev_key}]")
    print(f"{'─' * 70}")
    print(f"  {'分组':<22s}  {'N':>6s}  {'p25':>9s}  {'p50':>9s}  {'p75':>9s}  {'mean':>9s}")
    print(f"  {'-'*22}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}")

    for group_name, filter_fn in groups:
        subset = [r for r in records if filter_fn(r) and r.get(dev_key) is not None]
        vals = [r[dev_key] for r in subset]
        if not vals:
            print(f"  {group_name:<22s}  {len(vals):>6d}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}")
            continue
        p25 = quantile(vals, 0.25)
        p50 = quantile(vals, 0.50)
        p75 = quantile(vals, 0.75)
        mean = statistics.mean(vals)
        print(f"  {group_name:<22s}  {len(vals):>6d}  {fmt_pct(p25)}  {fmt_pct(p50)}  {fmt_pct(p75)}  {fmt_pct(mean)}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="确认延迟损耗诊断")
    parser.add_argument("--tag", default=DEFAULT_TAG,
                        help=f"回测 tag (默认 {DEFAULT_TAG})")
    args = parser.parse_args()

    print("=" * 70)
    print("确认延迟损耗诊断")
    print(f"数据源: tag={args.tag}, 2023-07-25~2026-07-22")
    print(f"窗口: {', '.join(f'N={n}({WINDOW_LABELS[n]})' for n in WINDOWS)}")
    print(f"对照: round_trip_cost = {ROUND_TRIP_COST*100:.2f}%")
    print("=" * 70)

    records = collect_deviations(args.tag)
    if not records:
        print("无数据，退出。")
        return

    buy_records = [r for r in records if r["direction"] == "buy"]
    sell_records = [r for r in records if r["direction"] == "sell"]

    print(f"\n买入信号 leg 数: {len(buy_records)}")
    print(f"卖出信号 leg 数: {len(sell_records)}")

    # 分组定义
    groups = [
        ("整体", lambda r: True),
        ("盈利交易", lambda r: r["is_profit_pair"]),
        ("亏损交易", lambda r: not r["is_profit_pair"]),
        ("持仓<6根(30min)", lambda r: r["holding_cat"] == "<6根(30min)"),
        ("持仓6-12根(1h)", lambda r: r["holding_cat"] == "6-12根(1h)"),
        ("持仓>12根(2h+)", lambda r: r["holding_cat"] == ">12根(2h+)"),
    ]

    # ── 买入偏离度表 ──
    for n in WINDOWS:
        dev_key = f"dev_{n}"
        print_table(
            f"买入偏离度（买点距离未来{WINDOW_LABELS[n]}最低价）",
            groups,
            buy_records,
            dev_key,
        )

    # ── 卖出偏离度表 ──
    for n in WINDOWS:
        dev_key = f"dev_{n}"
        print_table(
            f"卖出偏离度（卖点距离未来{WINDOW_LABELS[n]}最高价）",
            groups,
            sell_records,
            dev_key,
        )

    # ── 对照：延迟损耗 vs 成本 ──
    print(f"\n{'=' * 70}")
    print(f"  对照：延迟损耗 vs round_trip_cost ({ROUND_TRIP_COST*100:.2f}%)")
    print(f"{'=' * 70}")

    for label, recs in [("买入偏离度", buy_records), ("卖出偏离度", sell_records)]:
        print(f"\n  {label} 中位数 (p50):")
        for n in WINDOWS:
            vals = [r[f"dev_{n}"] for r in recs if r.get(f"dev_{n}") is not None]
            if not vals:
                continue
            p50 = quantile(vals, 0.50)
            ratio = p50 / ROUND_TRIP_COST if ROUND_TRIP_COST > 0 else float("inf")
            print(f"    N={n:>2d} ({WINDOW_LABELS[n]:>5s}): p50={fmt_pct(p50)}  "
                  f"= {ratio:.2f}x 成本")

    # 盈利 vs 亏损对比
    print(f"\n  盈利 vs 亏损交易偏离度中位数对比 (N=12):")
    for label, recs in [("买入", buy_records), ("卖出", sell_records)]:
        prof = [r["dev_12"] for r in recs if r.get("dev_12") is not None and r["is_profit_pair"]]
        loss = [r["dev_12"] for r in recs if r.get("dev_12") is not None and not r["is_profit_pair"]]
        if prof and loss:
            p50_prof = quantile(prof, 0.50)
            p50_loss = quantile(loss, 0.50)
            print(f"    {label}: 盈利={fmt_pct(p50_prof)}  亏损={fmt_pct(p50_loss)}  "
                  f"差值={fmt_pct(p50_loss - p50_prof)}")

    print(f"\n{'=' * 70}")
    print("诊断完成。请根据上方表格判断确认逻辑是否过度保守。")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
