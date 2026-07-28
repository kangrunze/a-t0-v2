#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stopped 桶拆分诊断（diag_stopped_breakdown）
============================================
把上一轮归因诊断里 94.1% 的 status="stopped" 平仓腿，按实际触发机制拆分为：
  1. trailing_stop  : 移动止盈（max_favorable>0，从最高点回撤 trailing_ratio 触发）
  2. fixed_stop     : 固定止损（max_favorable==0，max_adverse >= stop_loss_ratio 触发）

注意：超时腿（type="expired"）不进 trades，只进 risk_events，且 status 无 "stopped" 标记。
所以 trades 里 status="stopped" 的全是止损类，不含超时。

拆分依据（与 execution.py check_stop_loss 逻辑一致）：
  trailing_activation_pct=0.0 → max_favorable > 0 即激活移动止盈
  - max_favorable > 0 → trailing_stop（移动止盈）
  - max_favorable == 0 → fixed_stop（固定止损，从未盈利即被打掉）

对两个子类分别统计：
  - 各自笔数占比
  - 各自净盈亏贡献
  - 各自卖出偏离度分布（p25/p50/p75，复用 diag_confirm_delay_loss 逻辑）

不改任何策略参数，纯诊断。
"""
import json
import statistics
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(r"d:\project\a-t0-v2")
REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
DATA_DIR = Path(r"D:\project\data\zz500_5min")
TAG = "sample100_3y"
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"

# 窗口
WINDOWS = [6, 12, 24]
WINDOW_LABELS = {6: "30min", 12: "1h", 24: "2h"}


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


def fmt_pct(x):
    if x != x:  # NaN
        return "  N/A"
    return f"{x*100:7.3f}%"


def load_stock_bars(code):
    path = DATA_DIR / f"{code}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("daily_bars", {})


def build_global_bar_sequence(daily_bars):
    flat_bars = []
    time_to_global_idx = {}
    for date_str in sorted(daily_bars.keys()):
        for bar in daily_bars[date_str]:
            time_to_global_idx[bar["time"]] = len(flat_bars)
            flat_bars.append(bar)
    return flat_bars, time_to_global_idx


def compute_deviation(trade, flat_bars, time_to_global_idx, n):
    """偏离度计算（复用 diag_confirm_delay_loss.py 逻辑）。"""
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
    else:
        high_max = max(b["high"] for b in future_bars)
        return (high_max - fill_price) / fill_price


def build_risk_event_index(daily_results):
    """
    把 risk_events 按 time 建索引，用于关联 trade 记录。
    返回 {time_str: risk_event_dict}
    """
    idx = {}
    for dr in daily_results:
        for re in dr.get("risk_events", []):
            t = re.get("time")
            if t:
                idx[t] = re
    return idx


def classify_stopped_leg(trade, risk_event):
    """
    判定一笔 status=stopped 的平仓腿是 trailing_stop 还是 fixed_stop。

    依据 execution.py check_stop_loss 逻辑：
      trailing_activation_pct=0.0 → max_favorable > 0 即激活移动止盈
      - max_favorable > 0 → trailing_stop
      - max_favorable == 0 → fixed_stop
    """
    if risk_event is None:
        return "unknown"
    max_fav = risk_event.get("max_favorable", 0)
    if max_fav is not None and max_fav > 0:
        return "trailing_stop"
    else:
        return "fixed_stop"


def main():
    print("=" * 70)
    print("stopped 桶拆分诊断")
    print(f"数据源: tag={TAG} (baseline, rev=2), 2023-07-25~2026-07-22")
    print(f"拆分依据: trailing_activation_pct=0.0 → max_favorable>0 即移动止盈")
    print("=" * 70)

    reports = sorted(REPORT_DIR.glob(f"*_{TAG}_{START_DATE}_{END_DATE}_report.json"))
    print(f"找到 {len(reports)} 份 report.json")

    # 收集所有 stopped 平仓腿
    stopped_legs = []  # {subclass, direction, pnl, dev_6/12/24, code, holding_bars}
    stocks_loaded = 0

    for rp in reports:
        code = rp.name.split("_")[0]
        with open(rp, "r", encoding="utf-8") as f:
            report = json.load(f)
        if report.get("total_trades", 0) == 0:
            continue

        daily_bars = load_stock_bars(code)
        if not daily_bars:
            continue
        flat_bars, time_to_idx = build_global_bar_sequence(daily_bars)

        daily_results = report.get("daily_results", [])
        risk_idx = build_risk_event_index(daily_results)

        for dr in daily_results:
            for t in dr.get("trades", []):
                if t.get("status") != "stopped":
                    continue
                time_str = t.get("time")
                risk_event = risk_idx.get(time_str)
                subclass = classify_stopped_leg(t, risk_event)
                rec = {
                    "code": code,
                    "subclass": subclass,
                    "direction": t["direction"],
                    "pnl": t.get("pnl", 0.0),
                    "holding_bars": t.get("holding_bars", 0),
                    "max_favorable": risk_event.get("max_favorable", 0) if risk_event else None,
                    "max_adverse": risk_event.get("max_adverse", 0) if risk_event else None,
                }
                for n in WINDOWS:
                    rec[f"dev_{n}"] = compute_deviation(t, flat_bars, time_to_idx, n)
                stopped_legs.append(rec)

        stocks_loaded += 1
        if stocks_loaded % 20 == 0:
            print(f"  已处理 {stocks_loaded} 只股票, 累积 {len(stopped_legs)} 条 stopped 腿")

    print(f"\n共加载 {stocks_loaded} 只股票, {len(stopped_legs)} 条 stopped 腿")

    if not stopped_legs:
        print("无 stopped 腿，退出。")
        return

    # ── 拆分1：占比 ──
    print(f"\n{'=' * 70}")
    print("拆分1：stopped 桶按触发机制拆分占比")
    print(f"{'=' * 70}")
    total = len(stopped_legs)
    from collections import Counter
    counts = Counter(r["subclass"] for r in stopped_legs)
    labels = {
        "trailing_stop": "移动止盈（max_favorable>0，回撤50%触发）",
        "fixed_stop": "固定止损（max_favorable=0，未盈即被打掉）",
        "unknown": "无法关联 risk_event",
    }
    print(f"  {'子类':<40s}  {'笔数':>6s}  {'占比':>7s}")
    print(f"  {'-'*40}  {'-'*6}  {'-'*7}")
    for sc in ["trailing_stop", "fixed_stop", "unknown"]:
        cnt = counts.get(sc, 0)
        pct = cnt / total * 100 if total > 0 else 0
        print(f"  {labels[sc]:<40s}  {cnt:>6d}  {pct:>6.1f}%")
    print(f"  {'合计':<40s}  {total:>6d}  {'100.0%':>7s}")

    # ── 拆分2：净盈亏贡献 ──
    print(f"\n{'=' * 70}")
    print("拆分2：各子类净盈亏贡献")
    print(f"{'=' * 70}")
    print(f"  {'子类':<40s}  {'总盈亏':>12s}  {'单笔均盈亏':>12s}  {'盈利笔数':>8s}  {'亏损笔数':>8s}")
    print(f"  {'-'*40}  {'-'*12}  {'-'*12}  {'-'*8}  {'-'*8}")
    for sc in ["trailing_stop", "fixed_stop", "unknown"]:
        subset = [r for r in stopped_legs if r["subclass"] == sc]
        if not subset:
            print(f"  {labels[sc]:<40s}  {'N/A':>12s}  {'N/A':>12s}  {'N/A':>8s}  {'N/A':>8s}")
            continue
        total_pnl = sum(r["pnl"] for r in subset)
        per_trade = total_pnl / len(subset)
        win = sum(1 for r in subset if r["pnl"] > 0)
        loss = sum(1 for r in subset if r["pnl"] <= 0)
        print(f"  {labels[sc]:<40s}  {total_pnl:>+12.2f}  {per_trade:>+12.4f}  {win:>8d}  {loss:>8d}")

    # ── 拆分3：偏离度分布 ──
    print(f"\n{'=' * 70}")
    print("拆分3：各子类卖出偏离度分布（p25/p50/p75）")
    print(f"{'=' * 70}")

    for n in WINDOWS:
        dev_key = f"dev_{n}"
        print(f"\n  窗口 N={n} ({WINDOW_LABELS[n]})")
        print(f"  {'子类':<40s}  {'N':>6s}  {'p25':>9s}  {'p50':>9s}  {'p75':>9s}  {'mean':>9s}")
        print(f"  {'-'*40}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}")
        for sc in ["trailing_stop", "fixed_stop", "unknown"]:
            vals = [r[dev_key] for r in stopped_legs
                    if r["subclass"] == sc and r.get(dev_key) is not None]
            if not vals:
                print(f"  {labels[sc]:<40s}  {0:>6d}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}")
                continue
            p25 = quantile(vals, 0.25)
            p50 = quantile(vals, 0.50)
            p75 = quantile(vals, 0.75)
            mean = statistics.mean(vals)
            print(f"  {labels[sc]:<40s}  {len(vals):>6d}  {fmt_pct(p25)}  {fmt_pct(p50)}  {fmt_pct(p75)}  {fmt_pct(mean)}")

    # ── 汇总交叉 ──
    print(f"\n{'=' * 70}")
    print("汇总交叉（N=12 窗口）")
    print(f"{'=' * 70}")
    dev_key = "dev_12"
    print(f"  {'子类':<40s}  {'占比':>7s}  {'总盈亏':>12s}  {'p50偏离度':>10s}")
    print(f"  {'-'*40}  {'-'*7}  {'-'*12}  {'-'*10}")
    for sc in ["trailing_stop", "fixed_stop", "unknown"]:
        subset = [r for r in stopped_legs if r["subclass"] == sc]
        cnt = len(subset)
        share = cnt / total * 100 if total > 0 else 0
        total_pnl = sum(r["pnl"] for r in subset)
        vals = [r[dev_key] for r in subset if r.get(dev_key) is not None]
        p50 = quantile(vals, 0.50) if vals else float("nan")
        print(f"  {labels[sc]:<40s}  {share:>6.1f}%  {total_pnl:>+12.2f}  {fmt_pct(p50):>10s}")

    print(f"\n{'=' * 70}")
    print("诊断完成。根据拆分结果决定下一轮改 stop_loss_ratio 还是 max_holding_bars。")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
