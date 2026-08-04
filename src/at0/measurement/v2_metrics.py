"""
Measurement V2 — 研究级测量基础设施
====================================
V4 结构优化阶段的研究基础设施（Stage M0 + M1）。

设计原则（机构级研究流程）：
  - 指标可扩展：METRIC_REGISTRY 注册式，新增指标不改报告生成器
  - 可归因：每笔交易记录所有指标，支持分档/分组聚合
  - 可复现：基于 report.json + K线数据离线计算，不依赖运行时状态

指标体系（Stage M1）：
  ┌─ 入场质量 ─────────────────────────────────────────┐
  │  Entry Delay      开仓K线 - 当日真实极值K线（K线数）│
  │  Execution Gain   (fill_price - ideal) / ideal (%) │
  │  Wave Capture     开仓落在第几波（1st/2nd/3rd+）    │
  └────────────────────────────────────────────────────┘
  ┌─ 退出质量 ─────────────────────────────────────────┐
  │  Exit Delay       平仓K线 - 持仓期间极值K线（K线数）│
  │  Remaining Move   开仓后最大有利偏移（%）           │
  └────────────────────────────────────────────────────┘
  ┌─ 捕获效率 ─────────────────────────────────────────┐
  │  Capture Eff.     captured / available (0~1)       │
  └────────────────────────────────────────────────────┘
  ┌─ 机会识别 ─────────────────────────────────────────┐
  │  Opportunity Lost 满足条件但未开仓的上涨段数量+幅度│
  └────────────────────────────────────────────────────┘

接口：
  compute_v2_metrics_for_report(report, daily_bars) -> 单股完整指标
  compute_v2_metrics_batch(report_dir, tag, ...)     -> 批量指标
  METRIC_REGISTRY                                    -> 指标注册表（可扩展）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional


# ═══════════════════════════════════════════════════════════════
# Stage M0: Metric Registry（可扩展指标注册表）
# ═══════════════════════════════════════════════════════════════

# 指标函数签名：(open_trade, close_trade, daily_bars, bars_between, day_bars) -> dict
MetricFunc = Callable[[dict, dict, dict, list[dict], list[dict]], dict]

METRIC_REGISTRY: dict[str, MetricFunc] = {}


def register_metric(name: str, func: MetricFunc) -> None:
    """注册一个指标计算函数到注册表。"""
    METRIC_REGISTRY[name] = func


# ═══════════════════════════════════════════════════════════════
# K线工具函数
# ═══════════════════════════════════════════════════════════════

def _find_bar_index(bars: list[dict], time_str: str) -> int:
    """在当日 bars 中找到 time 匹配的 K线索引。"""
    for i, b in enumerate(bars):
        if b.get("time", "") == time_str:
            return i
    return -1


def _get_bars_between(
    daily_bars: dict[str, list[dict]],
    open_trade: dict,
    close_trade: dict,
) -> list[dict]:
    """获取开仓到平仓期间的所有 K线（跨日时合并）。"""
    open_date = open_trade.get("date", "")
    close_date = close_trade.get("date", "")
    open_time = open_trade.get("time", "")
    close_time = close_trade.get("time", "")

    bars: list[dict] = []
    if open_date == close_date:
        day_bars = daily_bars.get(open_date, [])
        oi = _find_bar_index(day_bars, open_time)
        ci = _find_bar_index(day_bars, close_time)
        if oi >= 0 and ci >= 0:
            bars = day_bars[oi:ci + 1]
    else:
        all_dates = sorted(daily_bars.keys())
        try:
            oi_start = all_dates.index(open_date)
            ci_end = all_dates.index(close_date)
        except ValueError:
            return []
        for d in all_dates[oi_start:ci_end + 1]:
            day_bars = daily_bars.get(d, [])
            if d == open_date:
                oi = _find_bar_index(day_bars, open_time)
                if oi >= 0:
                    bars.extend(day_bars[oi:])
            elif d == close_date:
                ci = _find_bar_index(day_bars, close_time)
                if ci >= 0:
                    bars.extend(day_bars[:ci + 1])
            else:
                bars.extend(day_bars)
    return bars


def _reconstruct_pairs(report: dict) -> list[tuple[dict, dict]]:
    """从 report.json 重建 FIFO 配对交易对。"""
    all_trades = []
    for dr in report.get("daily_results", []):
        for t in dr.get("trades", []):
            all_trades.append(t)
    all_trades.sort(key=lambda t: (t.get("date", ""), t.get("time", "")))

    open_queue: list[dict] = []
    pairs: list[tuple[dict, dict]] = []

    for t in all_trades:
        is_open = not t.get("paired", False) and t.get("status") == "open"
        is_close = t.get("paired", False) and t.get("status") in (
            "stopped", "paired", "expired", "mr_take_profit")

        if is_open:
            open_queue.append(t)
        elif is_close:
            close_dir = t.get("direction", "")
            open_dir = "sell" if close_dir == "buy" else "buy"
            for i, ol in enumerate(open_queue):
                if ol.get("direction") == open_dir:
                    pairs.append((ol, t))
                    open_queue.pop(i)
                    break
    return pairs


# ═══════════════════════════════════════════════════════════════
# Stage M1-a: Capture Efficiency
# ═══════════════════════════════════════════════════════════════

def _metric_capture_efficiency(
    open_t: dict, close_t: dict,
    daily_bars: dict, bars_between: list[dict], day_bars: list[dict],
) -> dict:
    """CE = captured / available（0~1，越高越好）。"""
    open_price = open_t.get("fill_price", 0.0)
    close_price = close_t.get("fill_price", 0.0)

    if not bars_between or open_price <= 0 or close_price <= 0:
        return {"ce": 0.0, "captured": 0.0, "available": 0.0}

    highs = [b.get("high", 0.0) for b in bars_between if b.get("high", 0) > 0]
    lows = [b.get("low", 0.0) for b in bars_between if b.get("low", 0) > 0]
    if not highs or not lows:
        return {"ce": 0.0, "captured": 0.0, "available": 0.0}

    available = max(highs) - min(lows)
    captured = abs(close_price - open_price)
    ce = captured / available if available > 0 else 0.0
    return {"ce": round(ce, 4), "captured": round(captured, 4),
            "available": round(available, 4)}


register_metric("capture_efficiency", _metric_capture_efficiency)


# ═══════════════════════════════════════════════════════════════
# Stage M1-a: Entry Delay + Execution Gain
# ═══════════════════════════════════════════════════════════════

def _metric_entry_quality(
    open_t: dict, close_t: dict,
    daily_bars: dict, bars_between: list[dict], day_bars: list[dict],
) -> dict:
    """Entry Delay = 开仓K线 - 当日真实极值K线；Execution Gain = 偏离理想价格%。

    ⚠ 哨兵值约定（2026-07-31 修正）：不可用一律返回 None，**不得用 -1**。
    entry_delay_bars = -1 是合法取值（比当日极值早 1 根 K 线入场，属最优入场），
    旧版用 -1 兼作"无数据"哨兵，聚合时被一并过滤，会系统性剔除最好的入场样本、
    把 Entry Delay 分布整体推高 —— Stage W 的验收基线因此失真。
    """
    direction = open_t.get("direction", "buy")
    fill_price = open_t.get("fill_price", 0.0)
    open_time = open_t.get("time", "")

    _NA = {"entry_delay_bars": None, "execution_gain_pct": None, "ideal_price": None}

    if not day_bars or fill_price <= 0:
        return dict(_NA)

    open_idx = _find_bar_index(day_bars, open_time)
    if open_idx < 0:
        return dict(_NA)

    if direction == "buy":
        lows = [(b.get("low", 0.0), i) for i, b in enumerate(day_bars) if b.get("low", 0) > 0]
        if not lows:
            return dict(_NA)
        ideal_price, ext_idx = min(lows, key=lambda x: x[0])
        exec_gain = (fill_price - ideal_price) / ideal_price if ideal_price > 0 else 0.0
    else:
        highs = [(b.get("high", 0.0), i) for i, b in enumerate(day_bars) if b.get("high", 0) > 0]
        if not highs:
            return dict(_NA)
        ideal_price, ext_idx = max(highs, key=lambda x: x[0])
        exec_gain = (ideal_price - fill_price) / ideal_price if ideal_price > 0 else 0.0

    return {
        "entry_delay_bars": open_idx - ext_idx,
        "execution_gain_pct": round(exec_gain * 100, 4),
        "ideal_price": ideal_price,
    }


register_metric("entry_quality", _metric_entry_quality)


# ═══════════════════════════════════════════════════════════════
# Stage M1-a: Exit Delay（平仓滞后于持仓期间极值的K线数）
# ═══════════════════════════════════════════════════════════════

def _metric_exit_delay(
    open_t: dict, close_t: dict,
    daily_bars: dict, bars_between: list[dict], day_bars: list[dict],
) -> dict:
    """Exit Delay = 平仓K线 - 持仓期间极值K线。

    正值=卖晚了（极值已过才平仓）；负值=卖早了（极值还没到就平仓）。
    买入方向极值=最高点；卖出方向极值=最低点。

    ⚠ 哨兵值约定：不可用返回 None。旧版返回 0，而 0 是合法取值
    （恰好在极值K线平仓 = 完美退出），会把"无数据"混进"完美退出"里。
    """
    direction = open_t.get("direction", "buy")
    close_time = close_t.get("time", "")
    close_date = close_t.get("date", "")

    _NA = {"exit_delay_bars": None, "extreme_bar_idx": None, "close_bar_idx": None}

    if not bars_between:
        return dict(_NA)

    # 在 bars_between 中找极值K线
    if direction == "buy":
        # 买入仓：极值 = 最高点
        extremes = [(b.get("high", 0.0), i) for i, b in enumerate(bars_between) if b.get("high", 0) > 0]
        if not extremes:
            return dict(_NA)
        _, ext_idx_in_slice = max(extremes, key=lambda x: x[0])
    else:
        extremes = [(b.get("low", 0.0), i) for i, b in enumerate(bars_between) if b.get("low", 0) > 0]
        if not extremes:
            return dict(_NA)
        _, ext_idx_in_slice = min(extremes, key=lambda x: x[0])

    # 平仓K线在 slice 中的索引 = 最后一个
    close_idx_in_slice = len(bars_between) - 1
    exit_delay = close_idx_in_slice - ext_idx_in_slice
    return {"exit_delay_bars": exit_delay,
            "extreme_bar_idx": ext_idx_in_slice,
            "close_bar_idx": close_idx_in_slice}


register_metric("exit_delay", _metric_exit_delay)


# ═══════════════════════════════════════════════════════════════
# Stage M1-b: Remaining Move（开仓后最大有利偏移%）
# ═══════════════════════════════════════════════════════════════

def _metric_remaining_move(
    open_t: dict, close_t: dict,
    daily_bars: dict, bars_between: list[dict], day_bars: list[dict],
) -> dict:
    """Remaining Move = 开仓后到平仓期间的最大有利偏移（%）。

    买入方向：max(high - open_price) / open_price
    卖出方向：max(open_price - low) / open_price
    反映"开仓后还能涨/跌多少"，是 Expected Move Engine 的基线指标。
    """
    open_price = open_t.get("fill_price", 0.0)
    direction = open_t.get("direction", "buy")

    if not bars_between or open_price <= 0:
        return {"remaining_move_pct": 0.0, "max_favorable_price": 0.0}

    if direction == "buy":
        highs = [b.get("high", 0.0) for b in bars_between if b.get("high", 0) > 0]
        if not highs:
            return {"remaining_move_pct": 0.0, "max_favorable_price": 0.0}
        max_fav = max(highs)
        remaining = (max_fav - open_price) / open_price
    else:
        lows = [b.get("low", 0.0) for b in bars_between if b.get("low", 0) > 0]
        if not lows:
            return {"remaining_move_pct": 0.0, "max_favorable_price": 0.0}
        max_fav = min(lows)
        remaining = (open_price - max_fav) / open_price

    return {"remaining_move_pct": round(remaining * 100, 4),
            "max_favorable_price": max_fav}


register_metric("remaining_move", _metric_remaining_move)


# ═══════════════════════════════════════════════════════════════
# Stage M1-b: Excursion（MAE / MFE）—— Support Engine 的基线指标
# ═══════════════════════════════════════════════════════════════

def _metric_excursion(
    open_t: dict, close_t: dict,
    daily_bars: dict, bars_between: list[dict], day_bars: list[dict],
) -> dict:
    """MAE / MFE（相对开仓价的最大不利 / 有利偏移，%）。

    Support Engine 的验收基线：支撑位选得准 → MAE 变小、MAE/MFE 变低。
    买入方向：MAE = (open - min(low)) / open；MFE = (max(high) - open) / open
    卖出方向：方向取反。两者均以正数表示"幅度"。
    """
    open_price = open_t.get("fill_price", 0.0)
    direction = open_t.get("direction", "buy")

    if not bars_between or open_price <= 0:
        return {"mae_pct": 0.0, "mfe_pct": 0.0, "mae_mfe_ratio": 0.0}

    highs = [b.get("high", 0.0) for b in bars_between if b.get("high", 0) > 0]
    lows = [b.get("low", 0.0) for b in bars_between if b.get("low", 0) > 0]
    if not highs or not lows:
        return {"mae_pct": 0.0, "mfe_pct": 0.0, "mae_mfe_ratio": 0.0}

    if direction == "buy":
        mae = (open_price - min(lows)) / open_price
        mfe = (max(highs) - open_price) / open_price
    else:
        mae = (max(highs) - open_price) / open_price
        mfe = (open_price - min(lows)) / open_price

    mae = max(0.0, mae)
    mfe = max(0.0, mfe)
    return {
        "mae_pct": round(mae * 100, 4),
        "mfe_pct": round(mfe * 100, 4),
        "mae_mfe_ratio": round(mae / mfe, 4) if mfe > 0 else 0.0,
    }


register_metric("excursion", _metric_excursion)


# ═══════════════════════════════════════════════════════════════
# Stage M1-b: Trend Quality（持仓期间趋势推进的干净程度）
# ═══════════════════════════════════════════════════════════════

def _metric_trend_quality(
    open_t: dict, close_t: dict,
    daily_bars: dict, bars_between: list[dict], day_bars: list[dict],
) -> dict:
    """Trend Quality = 持仓期间朝有利方向推进的 K线占比（0~1）。

    含义：0.8 表示八成 K线在往正确方向走（干净趋势）；
          0.5 表示震荡（拿住只是运气）。
    Hold Confidence V2 / Predictive Exit 的验收基线：
      同样的 Exit Delay 下，Trend Quality 越高说明退出锚定的是趋势而非噪声。
    """
    direction = open_t.get("direction", "buy")
    if len(bars_between) < 2:
        return {"trend_quality": 0.0, "favorable_bars": 0, "total_bars": len(bars_between)}

    favorable = 0
    total = 0
    for prev, cur in zip(bars_between, bars_between[1:]):
        pc = prev.get("close", 0.0)
        cc = cur.get("close", 0.0)
        if pc <= 0 or cc <= 0:
            continue
        total += 1
        if (direction == "buy" and cc > pc) or (direction == "sell" and cc < pc):
            favorable += 1

    return {
        "trend_quality": round(favorable / total, 4) if total > 0 else 0.0,
        "favorable_bars": favorable,
        "total_bars": total,
    }


register_metric("trend_quality", _metric_trend_quality)


# ═══════════════════════════════════════════════════════════════
# Stage M1-c: Wave Capture（波浪位置识别）
# ═══════════════════════════════════════════════════════════════

def _identify_daily_waves(day_bars: list[dict], window: int = 3) -> list[dict]:
    """识别当日波浪（基于 swing high/low）。

    简化算法：用 window 根K线窗口找局部极值，
    相邻的 swing high 构成波浪边界。

    :return: [{"wave_idx": 1, "start_bar": i, "end_bar": j, "high": max_high}, ...]
    """
    if len(day_bars) < window * 2 + 1:
        return []

    # 找 swing high（局部高点）
    swing_highs: list[tuple[int, float]] = []
    for i in range(window, len(day_bars) - window):
        is_swing = True
        for j in range(i - window, i + window + 1):
            if day_bars[j].get("high", 0) > day_bars[i].get("high", 0):
                is_swing = False
                break
        if is_swing:
            swing_highs.append((i, day_bars[i].get("high", 0.0)))

    if not swing_highs:
        return []

    # 每两个相邻 swing high 之间算一波
    waves = []
    for k, (idx, high) in enumerate(swing_highs):
        prev_idx = swing_highs[k - 1][0] if k > 0 else 0
        waves.append({
            "wave_idx": k + 1,
            "start_bar": prev_idx,
            "end_bar": idx,
            "high": high,
        })
    return waves


def _metric_wave_capture(
    open_t: dict, close_t: dict,
    daily_bars: dict, bars_between: list[dict], day_bars: list[dict],
) -> dict:
    """Wave Capture = 开仓落在当日第几波。

    wave_number=1 表示在第一波开仓（最优），3+ 表示在第三波或更晚（追高）。
    wave_total = 当日总波数。

    ⚠ 哨兵值约定：不可用返回 None。波数从 1 起算，旧版返回 0 会混进分布，
    把 wave_number 的均值/中位数系统性拉低（看起来"总在第一波买"）。
    """
    open_time = open_t.get("time", "")
    open_idx = _find_bar_index(day_bars, open_time)

    _NA = {"wave_number": None, "wave_total": None, "wave_capture_pct": None}

    if open_idx < 0:
        return dict(_NA)

    waves = _identify_daily_waves(day_bars)
    if not waves:
        return dict(_NA)

    wave_total = len(waves)
    # 找开仓K线落在哪一波
    wave_number = wave_total  # 默认最后一波
    for w in waves:
        if open_idx <= w["end_bar"]:
            wave_number = w["wave_idx"]
            break

    # wave_capture_pct: 1.0=第一波（最优），0.0=最后一波（最差）
    capture_pct = 1.0 - (wave_number - 1) / max(wave_total, 1)
    return {"wave_number": wave_number, "wave_total": wave_total,
            "wave_capture_pct": round(capture_pct, 4)}


register_metric("wave_capture", _metric_wave_capture)


# ═══════════════════════════════════════════════════════════════
# Stage M1-d: Opportunity Lost（未交易的上涨机会）
# ═══════════════════════════════════════════════════════════════

def _find_traded_bars(report: dict) -> set[str]:
    """收集所有已开仓的 bar time（格式: 'YYYY-MM-DD HH:MM:SS'）。"""
    traded = set()
    for dr in report.get("daily_results", []):
        date_str = dr.get("date", "")
        for t in dr.get("trades", []):
            time_str = t.get("time", "")
            if time_str:
                # 标准化为完整 time（trade 的 time 可能是 'YYYY-MM-DD HH:MM:SS' 或 'HH:MM:SS'）
                if len(time_str) == 8 and date_str:
                    time_str = f"{date_str} {time_str}"
                traded.add(time_str)
    return traded


def _identify_opportunity_lost(
    day_bars: list[dict],
    traded_times: set[str],
    date_str: str,
    min_surge_bars: int = 3,
    min_surge_pct: float = 0.5,
) -> list[dict]:
    """识别当日未交易的上涨段（Opportunity Lost）。

    简化算法：找连续 min_surge_bars 根K线涨幅 > min_surge_pct% 的段，
    如果该段没有任何开仓交易，计为 Opportunity Lost。

    :return: [{"start_bar": i, "end_bar": j, "surge_pct": float}, ...]
    """
    if len(day_bars) < min_surge_bars:
        return []

    opportunities = []
    i = 0
    while i <= len(day_bars) - min_surge_bars:
        # 检查从 i 开始的 min_surge_bars 根K线是否连续上涨
        segment = day_bars[i:i + min_surge_bars]
        start_price = segment[0].get("open", 0.0)
        end_price = segment[-1].get("close", 0.0)
        if start_price <= 0:
            i += 1
            continue

        surge_pct = (end_price - start_price) / start_price * 100
        if surge_pct >= min_surge_pct:
            # 检查这段是否有交易
            has_trade = False
            for b in segment:
                bar_time = b.get("time", "")
                if bar_time in traded_times:
                    has_trade = True
                    break
            if not has_trade:
                opportunities.append({
                    "start_bar": i,
                    "end_bar": i + min_surge_bars - 1,
                    "surge_pct": round(surge_pct, 4),
                    "start_time": segment[0].get("time", ""),
                    "end_time": segment[-1].get("time", ""),
                })
            i += min_surge_bars  # 跳过已识别的段
        else:
            i += 1

    return opportunities


def compute_opportunity_lost_for_stock(
    report: dict,
    daily_bars: dict[str, list[dict]],
) -> dict:
    """计算单股的 Opportunity Lost 指标（非配对级别，按日统计）。

    :return: {"total_lost": int, "avg_surge_pct": float, "total_lost_pct": float}
    """
    traded_times = _find_traded_bars(report)
    total_lost = 0
    total_surge = 0.0
    per_day = []

    for date_str, day_bars in daily_bars.items():
        opps = _identify_opportunity_lost(day_bars, traded_times, date_str)
        if opps:
            total_lost += len(opps)
            day_surge = sum(o["surge_pct"] for o in opps)
            total_surge += day_surge
            per_day.append({"date": date_str, "lost_count": len(opps),
                            "total_surge_pct": round(day_surge, 4)})

    return {
        "total_lost": total_lost,
        "avg_surge_pct": round(total_surge / total_lost, 4) if total_lost > 0 else 0.0,
        "total_lost_pct": round(total_surge, 4),
        "per_day": per_day,
    }


# ═══════════════════════════════════════════════════════════════
# Stage M0: 分布统计工具（均值以外的口径 —— 机构报告必需）
# ═══════════════════════════════════════════════════════════════

def _percentile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位数（q ∈ [0,1]），输入必须已排序。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def describe(vals: list[float]) -> dict:
    """返回一组数值的完整分布刻画。

    均值会被少数极端交易带偏，机构报告必须同时看 median 和 IQR，
    否则「CE 提升」可能只是某一天某一只票的异常值。
    """
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0, "mean": 0.0, "median": 0.0, "p25": 0.0,
                "p75": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    s = sorted(vals)
    n = len(s)
    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / n
    return {
        "n": n,
        "mean": round(mean, 4),
        "median": round(_percentile(s, 0.5), 4),
        "p25": round(_percentile(s, 0.25), 4),
        "p75": round(_percentile(s, 0.75), 4),
        "std": round(var ** 0.5, 4),
        "min": round(s[0], 4),
        "max": round(s[-1], 4),
    }


def _rank(vals: list[float]) -> list[float]:
    """平均秩（处理并列）。"""
    idx = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and vals[idx[j + 1]] == vals[idx[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[idx[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_ic(xs: list[float], ys: list[float]) -> Optional[float]:
    """Spearman 秩相关（RankIC）。

    用途：回答"引擎打的分，和这笔交易的实际结果，是不是同向"。
    这是 Confidence / Expected Accuracy 的定量口径 —— 比"平均分多少"有意义得多。
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _rank(xs), _rank(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(num / (dx * dy), 4) if dx > 0 and dy > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
# Stage M1-e: 引擎归因（Confidence / Expected / Support / Trend Accuracy）
# ═══════════════════════════════════════════════════════════════

def _bucket_stats(rows: list[tuple[float, float, float]], n_buckets: int = 4) -> list[dict]:
    """按 score 分档统计 (score, pnl, ce)。

    :param rows: [(score, pnl, ce), ...]
    :return: 每档的 n / 平均分 / 胜率 / 平均 pnl / 平均 CE
    """
    rows = [r for r in rows if r[0] is not None]
    if len(rows) < n_buckets:
        return []
    rows.sort(key=lambda r: r[0])
    size = len(rows) / n_buckets
    out = []
    for b in range(n_buckets):
        lo = int(b * size)
        hi = int((b + 1) * size) if b < n_buckets - 1 else len(rows)
        chunk = rows[lo:hi]
        if not chunk:
            continue
        wins = sum(1 for _, p, _ in chunk if p > 0)
        out.append({
            "bucket": b + 1,
            "n": len(chunk),
            "score_lo": round(chunk[0][0], 2),
            "score_hi": round(chunk[-1][0], 2),
            "win_rate": round(wins / len(chunk), 4),
            "avg_pnl": round(sum(p for _, p, _ in chunk) / len(chunk), 2),
            "avg_ce": round(sum(c for _, _, c in chunk) / len(chunk), 4),
        })
    return out


def compute_attribution(per_trade: list[dict]) -> dict:
    """引擎归因：引擎评分 → 实际结果 的相关性 + 单调性。

    - confidence_accuracy: alpha 总分 vs 实际盈亏（RankIC + 分档）
    - expected_accuracy:   Expected Move RR vs 实际 Remaining Move（RankIC）
    - support_accuracy:    support 子评分 vs MAE（RankIC，期望为负 —— 支撑越好回撤越小）
    - wave_accuracy:       wave 子评分 vs CE（RankIC，期望为正）

    要求 trade_record 里带 engine_ctx（Stage M0 Trade Event Logger 写入）。
    若回测时 engines_active=False，这些字段会缺失或退化，函数返回 available=False。
    """
    rows_alpha: list[tuple[float, float, float]] = []
    rr_x: list[float] = []
    rr_y: list[float] = []
    sup_x: list[float] = []
    sup_y: list[float] = []
    wave_x: list[float] = []
    wave_y: list[float] = []
    engines_active = False

    for t in per_trade:
        ctx = t.get("engine_ctx") or {}
        if not ctx:
            continue
        engines_active = engines_active or bool(ctx.get("engines_active"))
        pnl = float(t.get("pnl", 0.0) or 0.0)
        ce = float((t.get("capture_efficiency") or {}).get("ce", 0.0) or 0.0)
        rm = float((t.get("remaining_move") or {}).get("remaining_move_pct", 0.0) or 0.0)
        mae = float((t.get("excursion") or {}).get("mae_pct", 0.0) or 0.0)
        subs = ctx.get("sub_scores") or {}

        alpha = ctx.get("alpha")
        if isinstance(alpha, (int, float)):
            rows_alpha.append((float(alpha), pnl, ce))

        rr = ctx.get("expected_rr")
        if isinstance(rr, (int, float)):
            rr_x.append(float(rr))
            rr_y.append(rm)

        if isinstance(subs.get("support"), (int, float)):
            sup_x.append(float(subs["support"]))
            sup_y.append(mae)
        if isinstance(subs.get("wave"), (int, float)):
            wave_x.append(float(subs["wave"]))
            wave_y.append(ce)

    return {
        "available": bool(rows_alpha or rr_x or sup_x or wave_x),
        "engines_active": engines_active,
        "confidence_accuracy": {
            "n": len(rows_alpha),
            "rank_ic_alpha_vs_pnl": spearman_ic([r[0] for r in rows_alpha],
                                                [r[1] for r in rows_alpha]),
            "buckets": _bucket_stats(rows_alpha),
        },
        "expected_accuracy": {
            "n": len(rr_x),
            "rank_ic_rr_vs_remaining_move": spearman_ic(rr_x, rr_y),
        },
        "support_accuracy": {
            "n": len(sup_x),
            # 期望为负：support 分越高 → MAE 越小
            "rank_ic_support_vs_mae": spearman_ic(sup_x, sup_y),
        },
        "wave_accuracy": {
            "n": len(wave_x),
            # 期望为正：wave 分越高 → CE 越高
            "rank_ic_wave_vs_ce": spearman_ic(wave_x, wave_y),
        },
    }


def compute_trade_quality_from_pairs(per_trade: list[dict]) -> dict:
    """从配对交易直接算结果指标，和过程指标放在同一张报告里。

    过程指标（CE / Delay）解释"为什么"，结果指标（PF / 胜率）解释"值不值"，
    两者必须同源同口径，否则无法归因。
    """
    pnls = [float(t.get("pnl", 0.0) or 0.0) for t in per_trade]
    if not pnls:
        return {"n": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "payoff_ratio": 0.0, "expectancy": 0.0,
                "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    avg_w = gp / len(wins) if wins else 0.0
    avg_l = gl / len(losses) if losses else 0.0
    return {
        "n": len(pnls),
        "win_rate": round(len(wins) / len(pnls), 4),
        "profit_factor": round(gp / gl, 4) if gl > 0 else 0.0,
        "payoff_ratio": round(avg_w / avg_l, 4) if avg_l > 0 else 0.0,
        "expectancy": round(sum(pnls) / len(pnls), 4),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "net_pnl": round(sum(pnls), 2),
    }


# 参与分布统计的核心指标字段（指标名 -> per_trade 中的取值路径）
DISTRIBUTION_FIELDS: dict[str, tuple[str, str]] = {
    "ce": ("capture_efficiency", "ce"),
    "entry_delay_bars": ("entry_quality", "entry_delay_bars"),
    "exit_delay_bars": ("exit_delay", "exit_delay_bars"),
    "execution_gain_pct": ("entry_quality", "execution_gain_pct"),
    "remaining_move_pct": ("remaining_move", "remaining_move_pct"),
    "wave_number": ("wave_capture", "wave_number"),
    "wave_capture_pct": ("wave_capture", "wave_capture_pct"),
    "mae_pct": ("excursion", "mae_pct"),
    "mfe_pct": ("excursion", "mfe_pct"),
    "trend_quality": ("trend_quality", "trend_quality"),
}


def collect_distributions(per_trade: list[dict]) -> dict:
    """对每个核心指标做完整分布刻画（mean/median/p25/p75/std）。"""
    out: dict[str, dict] = {}
    for name, (group, key) in DISTRIBUTION_FIELDS.items():
        vals = []
        for t in per_trade:
            g = t.get(group) or {}
            v = g.get(key)
            # 哨兵一律 None；-1 / 0 是 Entry/Exit Delay 的合法取值，不得过滤
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        out[name] = describe(vals)
    return out


# ═══════════════════════════════════════════════════════════════
# 单股 V2 指标聚合（遍历注册表）
# ═══════════════════════════════════════════════════════════════

def compute_v2_metrics_for_report(
    report: dict,
    daily_bars: dict[str, list[dict]],
) -> dict:
    """计算单股的完整 V2 测量指标（遍历 METRIC_REGISTRY）。

    :return: {
        "code": str,
        "pair_count": int,
        "opportunity_lost": {...},   # 按日统计
        "per_trade": [{...}],        # 每笔配对交易的所有指标
        "summary": {                 # 聚合统计
            "avg_ce": float,
            "avg_entry_delay_bars": float,
            "avg_exit_delay_bars": float,
            "avg_execution_gain_pct": float,
            "avg_remaining_move_pct": float,
            "avg_wave_number": float,
            "avg_wave_capture_pct": float,
        }
    }
    """
    code = report.get("code", "?")
    pairs = _reconstruct_pairs(report)

    per_trade = []
    # 按指标名收集所有有效值
    metric_values: dict[str, list] = {}

    for open_t, close_t in pairs:
        open_date = open_t.get("date", "")
        day_bars = daily_bars.get(open_date, [])
        bars_between = _get_bars_between(daily_bars, open_t, close_t)

        trade_record = {
            "date": open_date,
            "direction": open_t.get("direction"),
            "open_price": open_t.get("fill_price"),
            "close_price": close_t.get("fill_price"),
            "pnl": close_t.get("pnl", 0.0),
            "holding_bars": close_t.get("holding_bars", 0),
            # Stage M0 Trade Event Logger 写入的引擎上下文（可能缺失，兼容旧 report）
            "engine_ctx": open_t.get("engine_ctx"),
        }

        # 遍历注册表计算所有指标
        for metric_name, metric_func in METRIC_REGISTRY.items():
            result = metric_func(open_t, close_t, daily_bars, bars_between, day_bars)
            trade_record[metric_name] = result

            # 收集数值型字段用于聚合
            # 哨兵一律为 None（isinstance 检查已排除），禁止再用 v != -1 过滤：
            # -1 / 0 都是 Entry/Exit Delay 的合法取值。
            for k, v in result.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    metric_values.setdefault(k, []).append(v)

        per_trade.append(trade_record)

    # Opportunity Lost（按日统计，非配对级别）
    opp_lost = compute_opportunity_lost_for_stock(report, daily_bars)

    # 聚合统计
    def _avg(key: str) -> float:
        vals = metric_values.get(key, [])
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    summary = {
        "avg_ce": _avg("ce"),
        "avg_entry_delay_bars": _avg("entry_delay_bars"),
        "avg_exit_delay_bars": _avg("exit_delay_bars"),
        "avg_execution_gain_pct": _avg("execution_gain_pct"),
        "avg_remaining_move_pct": _avg("remaining_move_pct"),
        "avg_wave_number": _avg("wave_number"),
        "avg_wave_capture_pct": _avg("wave_capture_pct"),
        "avg_mae_pct": _avg("mae_pct"),
        "avg_mfe_pct": _avg("mfe_pct"),
        "avg_trend_quality": _avg("trend_quality"),
    }

    return {
        "code": code,
        "pair_count": len(pairs),
        "opportunity_lost": opp_lost,
        "per_trade": per_trade,
        "summary": summary,
        # Stage M1: 分布 + 结果指标（与过程指标同源）
        "distributions": collect_distributions(per_trade),
        "trade_quality": compute_trade_quality_from_pairs(per_trade),
    }


# ═══════════════════════════════════════════════════════════════
# 批量 V2 指标
# ═══════════════════════════════════════════════════════════════

def compute_v2_metrics_batch(
    report_dir: Path | str,
    tag: str,
    start_date: str,
    end_date: str,
    data_dir: Path | str,
) -> dict:
    """批量计算多只股票的 V2 测量指标。"""
    report_dir = Path(report_dir)
    data_dir = Path(data_dir)
    pattern = f"*_{tag}_{start_date}_{end_date}_report.json"
    report_files = sorted(report_dir.glob(pattern))

    if not report_files:
        print(f"[v2_metrics] 未找到 report 文件: {pattern}")
        return {}

    per_stock = []
    # 收集所有数值用于整体聚合
    all_vals: dict[str, list] = {}
    all_trades: list[dict] = []   # 全池配对交易（用于真实中位数/分布，而非"均值的均值"）
    total_lost = 0
    total_lost_surge = 0.0

    for rf in report_files:
        code = rf.name.split("_")[0]
        with open(rf, "r", encoding="utf-8") as f:
            report = json.load(f)

        stock_file = data_dir / f"{code}.json"
        if not stock_file.exists():
            print(f"[v2_metrics] {code} K线数据缺失，跳过")
            continue
        with open(stock_file, "r", encoding="utf-8") as f:
            stock_data = json.load(f)
        daily_bars = stock_data.get("daily_bars", {})

        metrics = compute_v2_metrics_for_report(report, daily_bars)
        per_stock.append(metrics)
        all_trades.extend(metrics.get("per_trade", []))

        # 收集 summary 值
        # 纳入条件是"这只股票有配对交易"，而不是"指标值 > 0"。
        # 旧写法 if v > 0 会剔除负的 Entry Delay / Execution Gain ——
        # 而负值恰恰代表提前入场、成交价优于理想价，是最该被统计的好样本。
        s = metrics["summary"]
        if metrics.get("pair_count", 0) > 0:
            for k, v in s.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    all_vals.setdefault(k, []).append(v)

        # Opportunity Lost 聚合
        ol = metrics["opportunity_lost"]
        total_lost += ol["total_lost"]
        total_lost_surge += ol["total_lost_pct"]

        print(f"[v2_metrics] {code}  配对={metrics['pair_count']:2d}  "
              f"CE={s['avg_ce']:.2%}  "
              f"ED={s['avg_entry_delay_bars']:.1f}K  "
              f"XD={s['avg_exit_delay_bars']:.1f}K  "
              f"RM={s['avg_remaining_move_pct']:.2f}%  "
              f"Wv={s['avg_wave_number']:.1f}/{metrics['per_trade'][0]['wave_capture']['wave_total'] if metrics['per_trade'] else 0}  "
              f"OL={ol['total_lost']}")

    def _avg(key: str) -> float:
        vals = all_vals.get(key, [])
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    overall = {
        "stocks": len(per_stock),
        "total_pairs": sum(s["pair_count"] for s in per_stock),
        "avg_ce": _avg("avg_ce"),
        "avg_entry_delay_bars": _avg("avg_entry_delay_bars"),
        "avg_exit_delay_bars": _avg("avg_exit_delay_bars"),
        "avg_execution_gain_pct": _avg("avg_execution_gain_pct"),
        "avg_remaining_move_pct": _avg("avg_remaining_move_pct"),
        "avg_wave_number": _avg("avg_wave_number"),
        "avg_wave_capture_pct": _avg("avg_wave_capture_pct"),
        "total_opportunity_lost": total_lost,
        "avg_opportunity_lost_surge_pct": round(total_lost_surge / total_lost, 4) if total_lost > 0 else 0.0,
    }

    # 全池分布（在所有配对交易上算，不是"每股均值再平均"）
    distributions = collect_distributions(all_trades)
    trade_quality = compute_trade_quality_from_pairs(all_trades)
    attribution = compute_attribution(all_trades)

    # 把 median 提到 overall 一级，方便 A/B 直接对比
    overall["median_ce"] = distributions["ce"]["median"]
    overall["median_entry_delay_bars"] = distributions["entry_delay_bars"]["median"]
    overall["median_exit_delay_bars"] = distributions["exit_delay_bars"]["median"]
    overall["avg_mae_pct"] = distributions["mae_pct"]["mean"]
    overall["avg_trend_quality"] = distributions["trend_quality"]["mean"]
    overall["win_rate"] = trade_quality["win_rate"]
    overall["profit_factor"] = trade_quality["profit_factor"]
    overall["net_pnl"] = trade_quality["net_pnl"]
    overall["engines_active"] = attribution.get("engines_active", False)

    return {
        "tag": tag,
        "start": start_date,
        "end": end_date,
        "overall": overall,
        "distributions": distributions,
        "trade_quality": trade_quality,
        "attribution": attribution,
        "per_stock": per_stock,
    }
