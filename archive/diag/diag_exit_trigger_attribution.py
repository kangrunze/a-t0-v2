#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平仓触发原因归因诊断（diag_exit_trigger_attribution）
=====================================================
对 baseline（rev=2）的 100股×3年样本，逐笔回放平仓判定逻辑，标注每笔平仓
具体被哪个条件命中触发，然后和卖出偏离度交叉对比，回答"下一轮该动 ADX 阈值
还是 VWAP 阈值"。

触发原因分类（按代码 strategy.py 的 if/elif 优先级）：
  - vwap_sign   : vwap_dev 符号反转（buy平仓: vwap_dev>0; sell平仓: vwap_dev<0）
  - vwap_cross  : |vwap_dev| < tf_vwap_cross_threshold (0.002)
  - adx_drop    : adx < tf_trend_reverse_adx (28.0)
  - timeout     : status="stopped"（超时/止损腿，rules_fired 为空）

注意：
  - rules_fired 消息里写"偏离转负/穿越VWAP/ADX回落"三个原因，但实际 trend_reversed
    只由 if/elif 链的第一个命中条件决定。本脚本从消息解析 vwap_dev/adx 数值回推。
  - rules_score=2 表示 near_vwap and dir_confirmed and filter_passed 都满足（真触发）；
    rules_score=0 表示 trend_reversed=False 或 dir_confirmed=False（未真触发，但仍
    可能因 max_holding_bars 超时被强平 → 归类为 timeout）。

不改任何策略参数，纯诊断。
"""
import json
import re
import statistics
from pathlib import Path
from collections import deque, Counter

# ── 路径配置 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(r"d:\project\a-t0-v2")
REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
DATA_DIR = Path(r"D:\project\data\zz500_5min")
TAG = "sample100_3y"
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"

# 阈值（与 thresholds.yaml 一致，用于回推判定）
TF_VWAP_CROSS_THRESHOLD = 0.002
TF_TREND_REVERSE_ADX = 28.0

# 窗口（5min bar 数）—— 复用 diag_confirm_delay_loss 的偏离度计算
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


def parse_vwap_adx(rules_fired):
    """
    从 rules_fired 的 [平仓-趋势反转] 消息里解析 vwap_dev 和 adx 数值。
    消息格式：[平仓-趋势反转] vwap_dev=-1.72% adx=43.6 上涨趋势结束（...）

    返回 (vwap_dev_float, adx_float, direction_of_pairing_leg)
    direction 通过消息文案区分：
      "上涨趋势结束" → sell 平仓（买入腿平仓，卖出获利了结后买回）
      "下跌趋势结束" → buy 平仓（卖出腿平仓，买入后卖出获利了结）
    """
    for msg in rules_fired:
        if "[平仓-趋势反转]" not in msg:
            continue
        # 解析 vwap_dev
        m_vwap = re.search(r"vwap_dev=([+-]?\d+\.?\d*)%", msg)
        vwap_dev = float(m_vwap.group(1)) / 100 if m_vwap else None
        # 解析 adx
        m_adx = re.search(r"adx=(\d+\.?\d*)", msg)
        adx = float(m_adx.group(1)) if m_adx else None
        # 判断方向（上涨趋势结束=sell平仓买回，下跌趋势结束=buy平仓卖出）
        if "上涨趋势结束" in msg:
            pairing_direction = "sell"  # 卖出腿平仓（买入腿开仓卖出，现在买回）
        elif "下跌趋势结束" in msg:
            pairing_direction = "buy"   # 买入腿平仓（卖出腿开仓买入，现在卖出）
        else:
            pairing_direction = None
        return vwap_dev, adx, pairing_direction
    return None, None, None


def classify_trigger(trade):
    """
    判定一笔平仓腿的实际触发原因。

    返回原因字符串：
      - vwap_sign  : vwap_dev 符号反转（优先级最高）
      - vwap_cross : |vwap_dev| < 0.002
      - adx_drop   : adx < 28.0
      - timeout    : status=stopped 或 rules_fired 为空（超时/止损强平）
      - no_reverse : trend_reversed=False（rules_score=0，未真触发但被配对，罕见）
    """
    status = trade.get("status", "")
    rules_fired = trade.get("rules_fired", [])
    rules_score = trade.get("rules_score", 0)

    # 超时/止损腿
    if status == "stopped" or not rules_fired:
        return "timeout"

    # 解析 vwap_dev 和 adx
    vwap_dev, adx, pairing_dir = parse_vwap_adx(rules_fired)
    if vwap_dev is None and adx is None:
        return "timeout"  # 无趋势反转信息，按超时处理

    # 按 if/elif 优先级回推（与 strategy.py L409-414 / L573-578 一致）
    if pairing_dir == "buy":
        # buy 平仓（卖出腿平仓）：vwap_dev > 0
        if vwap_dev is not None and vwap_dev > 0:
            return "vwap_sign"
        elif vwap_dev is not None and abs(vwap_dev) < TF_VWAP_CROSS_THRESHOLD:
            return "vwap_cross"
        elif adx is not None and adx < TF_TREND_REVERSE_ADX:
            return "adx_drop"
        else:
            return "no_reverse"
    elif pairing_dir == "sell":
        # sell 平仓（买入腿平仓）：vwap_dev < 0
        if vwap_dev is not None and vwap_dev < 0:
            return "vwap_sign"
        elif vwap_dev is not None and abs(vwap_dev) < TF_VWAP_CROSS_THRESHOLD:
            return "vwap_cross"
        elif adx is not None and adx < TF_TREND_REVERSE_ADX:
            return "adx_drop"
        else:
            return "no_reverse"
    else:
        return "no_reverse"


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
    """
    偏离度计算（复用 diag_confirm_delay_loss.py 逻辑）。
    direction=buy:  (fill_price - min(low in future n bars)) / min(low)
    direction=sell: (max(high in future n bars) - fill_price) / fill_price
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
    else:
        high_max = max(b["high"] for b in future_bars)
        return (high_max - fill_price) / fill_price


def main():
    print("=" * 70)
    print("平仓触发原因归因诊断")
    print(f"数据源: tag={TAG} (baseline, rev=2), 2023-07-25~2026-07-22")
    print(f"阈值: tf_vwap_cross_threshold={TF_VWAP_CROSS_THRESHOLD}, "
          f"tf_trend_reverse_adx={TF_TREND_REVERSE_ADX}")
    print("=" * 70)

    reports = sorted(REPORT_DIR.glob(f"*_{TAG}_{START_DATE}_{END_DATE}_report.json"))
    print(f"找到 {len(reports)} 份 report.json")

    # 收集所有平仓腿的触发原因 + 偏离度
    exit_legs = []  # 每条: {trigger, direction, dev_6, dev_12, dev_24, pnl, code}
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

        for dr in report.get("daily_results", []):
            for t in dr.get("trades", []):
                if not t.get("paired"):
                    continue  # 只看平仓腿
                trigger = classify_trigger(t)
                rec = {
                    "code": code,
                    "trigger": trigger,
                    "direction": t["direction"],
                    "pnl": t.get("pnl", 0.0),
                    "status": t.get("status", ""),
                }
                for n in WINDOWS:
                    rec[f"dev_{n}"] = compute_deviation(t, flat_bars, time_to_idx, n)
                exit_legs.append(rec)

        stocks_loaded += 1
        if stocks_loaded % 20 == 0:
            print(f"  已处理 {stocks_loaded} 只股票, 累积 {len(exit_legs)} 条平仓腿")

    print(f"\n共加载 {stocks_loaded} 只股票, {len(exit_legs)} 条平仓腿")

    # ── 步骤1：触发原因占比 ──
    print(f"\n{'=' * 70}")
    print("步骤1：平仓触发原因占比")
    print(f"{'=' * 70}")
    trigger_counts = Counter(r["trigger"] for r in exit_legs)
    total = len(exit_legs)
    trigger_labels = {
        "vwap_sign": "vwap_dev符号反转（偏离转正/转负）",
        "vwap_cross": "|vwap_dev|<0.2%（穿越VWAP）",
        "adx_drop": "adx<28.0（ADX回落）",
        "timeout": "超时/止损（status=stopped）",
        "no_reverse": "trend_reversed=False（未真触发）",
    }
    print(f"  {'触发原因':<40s}  {'笔数':>6s}  {'占比':>7s}")
    print(f"  {'-'*40}  {'-'*6}  {'-'*7}")
    for trig in ["vwap_sign", "vwap_cross", "adx_drop", "timeout", "no_reverse"]:
        cnt = trigger_counts.get(trig, 0)
        pct = cnt / total * 100 if total > 0 else 0
        print(f"  {trigger_labels[trig]:<40s}  {cnt:>6d}  {pct:>6.1f}%")
    print(f"  {'合计':<40s}  {total:>6d}  {'100.0%':>7s}")

    # ── 步骤2：按触发原因分组的偏离度分布 ──
    print(f"\n{'=' * 70}")
    print("步骤2：按触发原因分组的卖出偏离度分布（p25/p50/p75）")
    print(f"{'=' * 70}")

    for n in WINDOWS:
        dev_key = f"dev_{n}"
        print(f"\n  窗口 N={n} ({WINDOW_LABELS[n]})")
        print(f"  {'触发原因':<40s}  {'N':>6s}  {'p25':>9s}  {'p50':>9s}  {'p75':>9s}  {'mean':>9s}")
        print(f"  {'-'*40}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}")
        for trig in ["vwap_sign", "vwap_cross", "adx_drop", "timeout", "no_reverse"]:
            vals = [r[dev_key] for r in exit_legs
                    if r["trigger"] == trig and r.get(dev_key) is not None]
            if not vals:
                print(f"  {trigger_labels[trig]:<40s}  {0:>6d}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}  {'N/A':>9s}")
                continue
            p25 = quantile(vals, 0.25)
            p50 = quantile(vals, 0.50)
            p75 = quantile(vals, 0.75)
            mean = statistics.mean(vals)
            print(f"  {trigger_labels[trig]:<40s}  {len(vals):>6d}  {fmt_pct(p25)}  {fmt_pct(p50)}  {fmt_pct(p75)}  {fmt_pct(mean)}")

    # ── 步骤3：交叉对比 ──
    print(f"\n{'=' * 70}")
    print("步骤3：交叉对比（占比最高 vs 偏离度最大）—— N=12 窗口")
    print(f"{'=' * 70}")

    dev_key = "dev_12"
    print(f"\  {'触发原因':<40s}  {'占比':>7s}  {'p50偏离度':>10s}  {'是否瓶颈':>10s}")
    print(f"  {'-'*40}  {'-'*7}  {'-'*10}  {'-'*10}")
    max_p50 = 0
    max_share = 0
    summary = []
    for trig in ["vwap_sign", "vwap_cross", "adx_drop", "timeout", "no_reverse"]:
        cnt = trigger_counts.get(trig, 0)
        share = cnt / total * 100 if total > 0 else 0
        vals = [r[dev_key] for r in exit_legs
                if r["trigger"] == trig and r.get(dev_key) is not None]
        p50 = quantile(vals, 0.50) if vals else float("nan")
        summary.append((trig, share, p50))
        if p50 == p50:  # not NaN
            max_p50 = max(max_p50, p50)
        max_share = max(max_share, share)

    for trig, share, p50 in summary:
        is_bottleneck = (share == max_share) or (p50 == max_p50)
        flag = "  ← 瓶颈" if is_bottleneck else ""
        print(f"  {trigger_labels[trig]:<40s}  {share:>6.1f}%  {fmt_pct(p50):>10s}{flag}")

    print(f"\n{'=' * 70}")
    print("诊断完成。根据上方交叉对比决定下一轮改 ADX 还是 VWAP。")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
