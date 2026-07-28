"""
measurement.loaders
===================
统一的 report.json 加载与开仓腿反推关联逻辑。

从 diag_fixed_stop_entry_quality.py 等 diag 脚本中抽取的公共代码，
供后续诊断脚本复用，避免重复实现数据加载和反推逻辑。

参考实现：方案A（按 risk_event 指纹匹配），即 diag_fixed_stop_entry_quality.py
main() 里的 inline 反推逻辑（先当日、后跨日，均取首个匹配）。

注意：
  - 本模块是纯抽取，不改变任何诊断逻辑
  - 行为与 diag_fixed_stop_entry_quality.py main() 里的反推逻辑完全一致
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional


# ═══════════════════════════════════════════════════════════════
# report.json 发现与加载
# ═══════════════════════════════════════════════════════════════

def find_reports(
    report_dir: Path | str,
    tag: str,
    start_date: str,
    end_date: str,
) -> list[Path]:
    """
    按tag+日期区间查找所有 report.json 文件。

    文件名模式: {code}_{tag}_{start_date}_{end_date}_report.json
    返回排序后的 Path 列表。
    """
    report_dir = Path(report_dir)
    pattern = f"*_{tag}_{start_date}_{end_date}_report.json"
    return sorted(report_dir.glob(pattern))


def load_report(report_path: Path | str) -> tuple[str, dict]:
    """
    加载单份 report.json。

    返回 (code, report_dict)。
    code 从文件名首段提取（与 diag 脚本一致：rp.name.split("_")[0]）。
    """
    report_path = Path(report_path)
    code = report_path.name.split("_")[0]
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    return code, report


def iter_reports(
    report_dir: Path | str,
    tag: str,
    start_date: str,
    end_date: str,
    *,
    skip_empty: bool = True,
) -> Iterator[tuple[str, dict]]:
    """
    遍历所有 report.json，yield (code, report)。

    skip_empty=True 时跳过 total_trades==0 的报告（与 diag 脚本一致）。
    """
    for rp in find_reports(report_dir, tag, start_date, end_date):
        code, report = load_report(rp)
        if skip_empty and report.get("total_trades", 0) == 0:
            continue
        yield code, report


# ═══════════════════════════════════════════════════════════════
# trades / risk_events 索引
# ═══════════════════════════════════════════════════════════════

def index_trades_by_date(report: dict) -> dict[str, list[dict]]:
    """
    按 date 索引 trades，返回 {date_str: [trade, ...]}。
    """
    result = {}
    for dr in report.get("daily_results", []):
        date_str = dr["date"]
        result[date_str] = dr.get("trades", [])
    return result


def collect_open_legs(report: dict) -> list[dict]:
    """
    收集所有未配对的开仓腿（paired=False 且 status="open"）。

    与 diag_fixed_stop_entry_quality.py main() 里的 all_open_legs 构建逻辑一致。
    """
    open_legs = []
    for dr in report.get("daily_results", []):
        for t in dr.get("trades", []):
            if not t.get("paired", False) and t.get("status") == "open":
                open_legs.append(t)
    return open_legs


def build_risk_event_index(report: dict) -> dict[str, dict]:
    """
    把 risk_events 按 time 建索引，返回 {time_str: risk_event}。

    用于通过平仓腿 time 关联 risk_event（复用 diag_stopped_breakdown.py 逻辑）。
    """
    idx = {}
    for dr in report.get("daily_results", []):
        for re in dr.get("risk_events", []):
            t = re.get("time")
            if t:
                idx[t] = re
    return idx


# ═══════════════════════════════════════════════════════════════
# 开仓腿反推（方案A：按 risk_event 指纹匹配）
# ═══════════════════════════════════════════════════════════════

def find_open_leg_in_day(
    day_trades: list[dict],
    risk_event: dict,
) -> Optional[dict]:
    """
    在当日 trades 里用 risk_event 的 direction + fill_price 指纹反查开仓腿。

    匹配条件（与 diag_fixed_stop_entry_quality.py main() 当日查找一致）:
      - direction 一致
      - fill_price 一致
      - paired=False（未配对）
      - status="open"
      - time <= risk_event.time

    返回首个匹配的 trade dict，或 None。
    """
    open_dir = risk_event.get("direction")
    fill_price = risk_event.get("fill_price")
    ev_time = risk_event.get("time", "")

    for t in day_trades:
        if (t.get("direction") == open_dir
            and t.get("fill_price") == fill_price
            and not t.get("paired", False)
            and t.get("status") == "open"
            and t.get("time", "") <= ev_time):
            return t
    return None


def find_open_leg_cross_day(
    all_open_legs: list[dict],
    risk_event: dict,
) -> Optional[dict]:
    """
    跨日查找开仓腿（当日未匹配时使用）。

    匹配条件（与 diag_fixed_stop_entry_quality.py main() 跨日查找一致）:
      - direction 一致
      - fill_price 一致
      - time <= risk_event.time

    注意: 跨日查找时不检查 paired/status（all_open_legs 已预过滤为 paired=False+status=open）。
    返回首个匹配的 trade dict，或 None。
    """
    open_dir = risk_event.get("direction")
    fill_price = risk_event.get("fill_price")
    ev_time = risk_event.get("time", "")

    for t in all_open_legs:
        if (t.get("direction") == open_dir
            and t.get("fill_price") == fill_price
            and t.get("time", "") <= ev_time):
            return t
    return None


def find_open_leg(
    report: dict,
    risk_event: dict,
    *,
    date_str: Optional[str] = None,
    all_open_legs: Optional[list[dict]] = None,
) -> Optional[dict]:
    """
    用 risk_event 的 direction + fill_price 指纹反查开仓腿（方案A）。

    查找顺序（与 diag_fixed_stop_entry_quality.py main() 完全一致）:
      1. 若提供 date_str，先在当日 trades 里找（find_open_leg_in_day）
      2. 当日未找到，在所有未配对开仓腿里跨日找（find_open_leg_cross_day）

    参数:
      report:       report.json 的 dict
      risk_event:   risk_event dict（需含 direction/fill_price/time）
      date_str:     可选，指定当日日期；None 时跳过当日查找直接跨日
      all_open_legs: 可选，预构建的未配对开仓腿列表；None 时从 report 构建

    返回开仓腿 trade dict 或 None。
    """
    # 1. 当日查找
    if date_str is not None:
        for dr in report.get("daily_results", []):
            if dr["date"] == date_str:
                day_trades = dr.get("trades", [])
                leg = find_open_leg_in_day(day_trades, risk_event)
                if leg is not None:
                    return leg
                break

    # 2. 跨日查找
    if all_open_legs is None:
        all_open_legs = collect_open_legs(report)
    return find_open_leg_cross_day(all_open_legs, risk_event)


# ═══════════════════════════════════════════════════════════════
# K线数据加载（复用 diag_confirm_delay_loss / diag_stopped_breakdown 逻辑）
# ═══════════════════════════════════════════════════════════════

def load_stock_bars(code: str, data_dir: Path | str) -> Optional[dict]:
    """
    加载一只股票的 daily_bars，返回 {date: [bar,...]} 或 None。

    文件路径: {data_dir}/{code}.json
    """
    path = Path(data_dir) / f"{code}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("daily_bars", {})


def build_global_bar_sequence(daily_bars: dict) -> tuple[list[dict], dict[str, int]]:
    """
    把按日分组的 bars 展平为全局序列，用于跨日查找"未来 N 根 bar"。

    返回:
      flat_bars: list[bar]  —— 全局 bar 序列（按日期升序）
      time_to_global_idx: dict[str, int]  —— bar time -> 全局索引
    """
    flat_bars = []
    time_to_global_idx = {}
    for date_str in sorted(daily_bars.keys()):
        for bar in daily_bars[date_str]:
            time_to_global_idx[bar["time"]] = len(flat_bars)
            flat_bars.append(bar)
    return flat_bars, time_to_global_idx
