#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
固定止损入场质量诊断（diag_fixed_stop_entry_quality）
=====================================================
对 baseline 里 3,869 笔固定止损（max_favorable=0）的平仓腿，反查其开仓腿的
入场质量，回答三个问题：
  1. 入场方向 vs trend_context 是否一致（有多少笔逆趋势开仓）
  2. 入场时 vwap_dev/adx/vol_ratio 因子分布，和移动止盈组对比
  3. 按股票分组看是否集中

关联方法：risk_event(type=stopped) 保留了开仓腿指纹（direction + fill_price），
用它在同 code 的 trades 列表里反查 paired=false / status=open 的开仓腿。

不改任何策略参数，纯诊断。
"""
import json
import re
import statistics
from pathlib import Path
from collections import Counter, defaultdict

# ── 路径配置 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(r"d:\project\a-t0-v2")
REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
TAG = "sample100_3y"
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"


def quantile(data, q):
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


def fmt(x, w=9):
    if x is None:
        return "N/A".rjust(w)
    if x != x:  # NaN
        return "N/A".rjust(w)
    return f"{x:>{w}.3f}"


def parse_entry_factors(rules_fired):
    """
    从开仓腿 rules_fired 解析 vwap_dev/adx/vol_ratio/trend_context。
    返回 dict，缺失字段为 None。
    """
    text = " ".join(rules_fired) if rules_fired else ""
    result = {"vwap_dev": None, "adx": None, "vol_ratio": None, "trend_context": None}

    m = re.search(r"VWAP偏离 ([+-]?\d+\.\d+)%", text)
    if m:
        result["vwap_dev"] = float(m.group(1)) / 100

    m = re.search(r"ADX=(\d+\.\d+)", text)
    if m:
        result["adx"] = float(m.group(1))

    m = re.search(r"量比 (\d+\.\d+)", text)
    if m:
        result["vol_ratio"] = float(m.group(1))

    m = re.search(r"\[趋势\] (\w+)", text)
    if m:
        result["trend_context"] = m.group(1)

    return result


def classify_direction_consistency(entry_direction, trend_context):
    """
    判断入场方向与 trend_context 是否一致。

    开仓 direction="sell" = 反T卖出（看空），direction="buy" = 反T买入（看多）
    trend_context: trend_up(上升) / trend_down(下降) / extreme(极端) / range(震荡)

    一致：
      sell + trend_down（看空+下降趋势）
      buy + trend_up（看多+上升趋势）
      sell/buy + range（震荡盘，方向中性，算一致）
      sell/buy + extreme（极端趋势，方向中性，算一致）
    不一致（逆势）：
      sell + trend_up（看空但上升趋势）
      buy + trend_down（看多但下降趋势）
    """
    if trend_context is None:
        return "unknown"
    if trend_context in ("range", "extreme"):
        return "consistent_neutral"
    if entry_direction == "sell" and trend_context == "trend_down":
        return "consistent"
    if entry_direction == "buy" and trend_context == "trend_up":
        return "consistent"
    return "counter_trend"  # 逆势


def find_open_leg(trades_by_code_date, code, risk_event):
    """
    用 risk_event 的 direction + fill_price 指纹，在同 code 的 trades 里反查开仓腿。
    返回开仓腿 trade dict 或 None。
    """
    open_dir = risk_event.get("direction")
    fill_price = risk_event.get("fill_price")
    ev_time = risk_event.get("time", "")

    # 在该股票所有日期的 trades 里找
    candidates = []
    for date_key, day_trades in trades_by_code_date.get(code, {}).items():
        for t in day_trades:
            if (t.get("direction") == open_dir
                and t.get("fill_price") == fill_price
                and not t.get("paired", False)
                and t.get("status") == "open"
                and t.get("time", "") <= ev_time):
                candidates.append(t)

    if not candidates:
        return None
    # 取时间最近的（最晚的但 <= ev_time）
    candidates.sort(key=lambda t: t.get("time", ""))
    return candidates[-1]


def main():
    print("=" * 70)
    print("固定止损入场质量诊断")
    print(f"数据源: tag={TAG} (baseline, rev=2), 2023-07-25~2026-07-22")
    print("=" * 70)

    reports = sorted(REPORT_DIR.glob(f"*_{TAG}_{START_DATE}_{END_DATE}_report.json"))
    print(f"找到 {len(reports)} 份 report.json")

    # 两类样本：fixed_stop（max_favorable=0）vs trailing_stop（max_favorable>0）
    fixed_stop_entries = []   # 固定止损组的开仓腿信息
    trailing_stop_entries = []  # 移动止盈组的开仓腿信息（对照组）
    stocks_loaded = 0
    unmatched_count = 0

    for rp in reports:
        code = rp.name.split("_")[0]
        with open(rp, "r", encoding="utf-8") as f:
            report = json.load(f)
        if report.get("total_trades", 0) == 0:
            continue

        # 按 code+date 索引 trades，便于反查开仓腿
        trades_by_date = {}
        for dr in report.get("daily_results", []):
            date_str = dr["date"]
            trades_by_date[date_str] = dr.get("trades", [])

        # 全局索引（跨日查找用）
        all_open_legs = []
        for dr in report.get("daily_results", []):
            for t in dr.get("trades", []):
                if not t.get("paired", False) and t.get("status") == "open":
                    all_open_legs.append(t)

        # 遍历 risk_events，分类止损腿
        for dr in report.get("daily_results", []):
            date_str = dr["date"]
            day_trades = dr.get("trades", [])
            day_risk_events = dr.get("risk_events", [])

            for ev in day_risk_events:
                if ev.get("type") != "stopped":
                    continue
                max_fav = ev.get("max_favorable", 0)
                is_fixed_stop = (max_fav is None or max_fav == 0)

                # 反查开仓腿
                open_leg = None
                open_dir = ev.get("direction")
                fill_price = ev.get("fill_price")
                ev_time = ev.get("time", "")
                # 先在当日找
                for t in day_trades:
                    if (t.get("direction") == open_dir
                        and t.get("fill_price") == fill_price
                        and not t.get("paired", False)
                        and t.get("status") == "open"
                        and t.get("time", "") <= ev_time):
                        open_leg = t
                        break
                # 当日没找到，跨日找
                if open_leg is None:
                    for t in all_open_legs:
                        if (t.get("direction") == open_dir
                            and t.get("fill_price") == fill_price
                            and t.get("time", "") <= ev_time):
                            open_leg = t
                            break

                if open_leg is None:
                    unmatched_count += 1
                    continue

                factors = parse_entry_factors(open_leg.get("rules_fired", []))
                direction = open_leg.get("direction")
                consistency = classify_direction_consistency(direction, factors["trend_context"])

                rec = {
                    "code": code,
                    "date": date_str,
                    "entry_direction": direction,
                    "entry_time": open_leg.get("time"),
                    "entry_fill_price": open_leg.get("fill_price"),
                    "rules_score": open_leg.get("rules_score", 0),
                    "stop_pnl": ev.get("realized_pnl", 0),
                    "max_adverse": ev.get("max_adverse", 0),
                    "max_favorable": ev.get("max_favorable", 0),
                    "holding_bars": ev.get("holding_bars", 0),
                    "vwap_dev": factors["vwap_dev"],
                    "adx": factors["adx"],
                    "vol_ratio": factors["vol_ratio"],
                    "trend_context": factors["trend_context"],
                    "consistency": consistency,
                }

                if is_fixed_stop:
                    fixed_stop_entries.append(rec)
                else:
                    trailing_stop_entries.append(rec)

        stocks_loaded += 1
        if stocks_loaded % 20 == 0:
            print(f"  已处理 {stocks_loaded} 只股票, fixed_stop={len(fixed_stop_entries)}, trailing_stop={len(trailing_stop_entries)}")

    print(f"\n共加载 {stocks_loaded} 只股票")
    print(f"固定止损组（max_favorable=0）: {len(fixed_stop_entries)} 笔")
    print(f"移动止盈组（max_favorable>0）: {len(trailing_stop_entries)} 笔")
    print(f"未匹配到开仓腿: {unmatched_count} 笔")

    if not fixed_stop_entries:
        print("无固定止损样本，退出。")
        return

    # ════════════════════════════════════════════════════════════
    # 项1：入场方向 vs trend_context 一致性
    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("项1：入场方向 vs trend_context 一致性（固定止损组）")
    print(f"{'=' * 70}")

    consist_counts = Counter(r["consistency"] for r in fixed_stop_entries)
    total = len(fixed_stop_entries)
    labels = {
        "consistent": "一致（sell+下降 / buy+上升）",
        "consistent_neutral": "中性（range / extreme）",
        "counter_trend": "逆势（sell+上升 / buy+下降）",
        "unknown": "无法解析 trend_context",
    }
    print(f"  {'类别':<35s}  {'笔数':>6s}  {'占比':>7s}")
    print(f"  {'-'*35}  {'-'*6}  {'-'*7}")
    for k in ["consistent", "consistent_neutral", "counter_trend", "unknown"]:
        cnt = consist_counts.get(k, 0)
        pct = cnt / total * 100 if total > 0 else 0
        print(f"  {labels[k]:<35s}  {cnt:>6d}  {pct:>6.1f}%")

    # trend_context 分布对比
    print(f"\n  trend_context 分布对比（固定止损 vs 移动止盈）:")
    fs_ctx = Counter(r["trend_context"] for r in fixed_stop_entries if r["trend_context"])
    ts_ctx = Counter(r["trend_context"] for r in trailing_stop_entries if r["trend_context"])
    fs_total = sum(fs_ctx.values()) or 1
    ts_total = sum(ts_ctx.values()) or 1
    print(f"  {'trend_context':<15s}  {'固定止损占比':>12s}  {'移动止盈占比':>12s}")
    print(f"  {'-'*15}  {'-'*12}  {'-'*12}")
    for ctx in ["trend_up", "trend_down", "extreme", "range"]:
        fs_pct = fs_ctx.get(ctx, 0) / fs_total * 100
        ts_pct = ts_ctx.get(ctx, 0) / ts_total * 100
        print(f"  {ctx:<15s}  {fs_pct:>11.1f}%  {ts_pct:>11.1f}%")

    # ════════════════════════════════════════════════════════════
    # 项2：入场因子数值分布对比
    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("项2：入场时因子数值分布对比（固定止损 vs 移动止盈）")
    print(f"{'=' * 70}")

    for factor in ["vwap_dev", "adx", "vol_ratio"]:
        fs_vals = [r[factor] for r in fixed_stop_entries if r[factor] is not None]
        ts_vals = [r[factor] for r in trailing_stop_entries if r[factor] is not None]
        print(f"\n  {factor}:")
        print(f"  {'组别':<12s}  {'N':>6s}  {'p25':>9s}  {'p50':>9s}  {'p75':>9s}  {'mean':>9s}")
        print(f"  {'-'*12}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}")
        for label, vals in [("固定止损", fs_vals), ("移动止盈", ts_vals)]:
            if not vals:
                print(f"  {label:<12s}  {0:>6d}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}")
                continue
            p25 = quantile(vals, 0.25)
            p50 = quantile(vals, 0.50)
            p75 = quantile(vals, 0.75)
            mean = statistics.mean(vals)
            print(f"  {label:<12s}  {len(vals):>6d}  {fmt(p25*100 if factor!='adx' else p25)}  {fmt(p50*100 if factor!='adx' else p50)}  {fmt(p75*100 if factor!='adx' else p75)}  {fmt(mean*100 if factor!='adx' else mean)}")
        if factor == "vwap_dev":
            print(f"  （vwap_dev 单位 %，adx/vol_ratio 为原值）")

    # 阈值压线分析（adx 阈值35, vol_ratio 阈值2.0）
    print(f"\n  阈值压线分析（adx>35, vol_ratio>2.0 才触发）:")
    for factor, threshold in [("adx", 35.0), ("vol_ratio", 2.0)]:
        fs_vals = [r[factor] for r in fixed_stop_entries if r[factor] is not None]
        ts_vals = [r[factor] for r in trailing_stop_entries if r[factor] is not None]
        fs_margin = [(v - threshold) / threshold * 100 for v in fs_vals]
        ts_margin = [(v - threshold) / threshold * 100 for v in ts_vals]
        print(f"\n  {factor} 超出阈值幅度（%）: 阈值={threshold}")
        print(f"  {'组别':<12s}  {'p25':>9s}  {'p50':>9s}  {'p75':>9s}")
        print(f"  {'-'*12}  {'-'*9}  {'-'*9}  {'-'*9}")
        for label, vals in [("固定止损", fs_margin), ("移动止盈", ts_margin)]:
            if not vals:
                print(f"  {label:<12s}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}")
                continue
            print(f"  {label:<12s}  {fmt(quantile(vals,0.25))}  {fmt(quantile(vals,0.50))}  {fmt(quantile(vals,0.75))}")

    # ════════════════════════════════════════════════════════════
    # 项3：按股票分组看集中度
    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("项3：按股票分组看固定止损笔数集中度")
    print(f"{'=' * 70}")

    code_counts = Counter(r["code"] for r in fixed_stop_entries)
    code_pnls = defaultdict(float)
    for r in fixed_stop_entries:
        code_pnls[r["code"]] += r["stop_pnl"]

    total_stocks = len(code_counts)
    total_fixed = len(fixed_stop_entries)
    print(f"  涉及股票数: {total_stocks} / 100")
    print(f"  固定止损笔数: {total_fixed}")
    print(f"  平均每只股票: {total_fixed/total_stocks:.1f} 笔")

    # Top 10 集中股票
    print(f"\n  Top 10 固定止损笔数最多的股票:")
    print(f"  {'股票':<10s}  {'笔数':>6s}  {'占比':>7s}  {'累计亏损':>12s}")
    print(f"  {'-'*10}  {'-'*6}  {'-'*7}  {'-'*12}")
    for code, cnt in code_counts.most_common(10):
        share = cnt / total_fixed * 100
        print(f"  {code:<10s}  {cnt:>6d}  {share:>6.1f}%  {code_pnls[code]:>+12.2f}")

    # 集中度指标
    top10_share = sum(c for _, c in code_counts.most_common(10)) / total_fixed * 100
    top20_share = sum(c for _, c in code_counts.most_common(20)) / total_fixed * 100
    print(f"\n  Top 10 股票占固定止损笔数: {top10_share:.1f}%")
    print(f"  Top 20 股票占固定止损笔数: {top20_share:.1f}%")

    # 分布形态
    counts_sorted = sorted(code_counts.values(), reverse=True)
    p50_stock = quantile(counts_sorted, 0.50)
    p75_stock = quantile(counts_sorted, 0.75)
    p90_stock = quantile(counts_sorted, 0.90)
    print(f"  每只股票固定止损笔数分位: p50={p50_stock:.0f} p75={p75_stock:.0f} p90={p90_stock:.0f}")

    if top10_share > 50:
        print(f"\n  → 集中度较高（Top10>{50}%），问题可能在选股池")
    else:
        print(f"\n  → 集中度较低（Top10<{50}%），均匀分布在多数股票上，是入场逻辑系统性问题")

    print(f"\n{'=' * 70}")
    print("诊断完成。根据三项结果决定下一步方向。")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
