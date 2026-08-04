"""
分层回测报告 — 按 Regime / 时段 / 行业 / 流动性 / 振幅 / 市值切片
=================================================================

分层：regime × 时间段（起步两维）
每格标注样本量。<5 笔的格子标记"不可结论"

用法:
    from at0.measurement.stratified import compute_stratified_report

    report = compute_stratified_report(
        trades, dim1_name="direction_pnl", dim2_name="time_slot",
        dim1_values=regime_map,
    )
    for cell in report.cells:
        if cell.is_conclusive:
            print(cell.dim1_label, cell.dim2_label, cell.win_rate)
"""
from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd


# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════


@dataclass
class StratumCell:
    """分层回测中的一个格子（dim1 × dim2 交叉）。"""
    dim1_label: str
    dim2_label: str
    n_paired: int
    n_wins: int
    win_rate: float
    net_pnl: float
    is_conclusive: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StratifiedReport:
    """完整分层回测报告。"""
    dim1_name: str
    dim2_name: str
    cells: list[StratumCell]

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
# 分类工具函数
# ═══════════════════════════════════════════════════════════════


def _parse_time_hhmm(time_str: str) -> Optional[tuple[int, int]]:
    """从时间字符串中提取小时和分钟。

    支持格式:
      - "HH:MM" / "HH:MM:SS"
      - "YYYY-MM-DD HH:MM:SS"
      - "YYYY-MM-DD HH:MM"
    """
    if not time_str:
        return None
    try:
        # 尝试提取 HH:MM 部分
        if " " in time_str:
            time_part = time_str.split(" ")[1]
        else:
            time_part = time_str
        parts = time_part.split(":")
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        pass
    return None


def classify_time_slot(time_str: str, bars_per_day: int = 240) -> str:
    """将时间分类到交易时段。

    中国 A 股交易时段（bars_per_day 预留，当前未使用）:
      - opening:   9:30 ~ 10:00（开盘集合竞价后前30分钟）
      - morning:  10:00 ~ 11:30（上午盘）
      - afternoon: 13:00 ~ 14:00（下午盘前段）
      - closing:  14:00 ~ 15:00（尾盘）

    参数
    ----
    time_str : str
        时间字符串，支持 "HH:MM"、"HH:MM:SS" 或完整 datetime 格式。
    bars_per_day : int
        每日 K 线数（预留参数，当前未使用）。

    返回
    ----
    str — "opening" / "morning" / "afternoon" / "closing" / "unknown"
    """
    parsed = _parse_time_hhmm(time_str)
    if parsed is None:
        return "unknown"
    hour, minute = parsed
    minutes_since_midnight = hour * 60 + minute

    # 9:30 ~ 10:00 → 开盘
    if 570 <= minutes_since_midnight < 600:
        return "opening"
    # 10:00 ~ 11:30 → 上午盘
    if 600 <= minutes_since_midnight <= 690:
        return "morning"
    # 13:00 ~ 14:00 → 下午盘前段
    if 780 <= minutes_since_midnight < 840:
        return "afternoon"
    # 14:00 ~ 15:00 → 尾盘
    if 840 <= minutes_since_midnight <= 900:
        return "closing"

    return "unknown"


def _classify_quintile(
    value: float,
    pool_values: list[float],
    label_fmt: str = "Q{}",
) -> str:
    """将给定值相对于总体分位数分为五等份。

    参数
    ----
    value : float
        待分类的值。
    pool_values : list[float]
        总体样本列表。
    label_fmt : str
        标签格式，如 "Q{}" 会生成 "Q1" ~ "Q5"。

    返回
    ----
    str — 分位标签，如 "Q1" ~ "Q5"，或 "unknown"（无法计算时）。
    """
    if not pool_values or len(pool_values) < 5:
        return "unknown"
    try:
        cleaned = [v for v in pool_values if isinstance(v, (int, float))]
        if len(cleaned) < 5:
            return "unknown"
        # 用 pd.qcut 计算分位边界
        _, bins = pd.qcut(cleaned, q=5, retbins=True, duplicates="drop")
        n_bins = len(bins) - 1
        labels = [label_fmt.format(i + 1) for i in range(n_bins)]

        # 落到哪个 bin
        idx = pd.cut([value], bins=bins, labels=labels, include_lowest=True)[0]
        return str(idx) if pd.notna(idx) else "unknown"
    except Exception:
        return "unknown"


def classify_liquidity_quintile(amount: float, pool_amounts: list[float]) -> str:
    """按成交额/流动性将值分为五等份。

    参数
    ----
    amount : float
        待分类的流动性值。
    pool_amounts : list[float]
        总体流动性样本列表。

    返回
    ----
    str — "Q1"（最低流动性）~ "Q5"（最高流动性），或 "unknown"。
    """
    return _classify_quintile(amount, pool_amounts)


def classify_volatility_quintile(amplitude: float, pool_amplitudes: list[float]) -> str:
    """按振幅/波动率将值分为五等份。

    参数
    ----
    amplitude : float
        待分类的振幅值。
    pool_amplitudes : list[float]
        总体振幅样本列表。

    返回
    ----
    str — "Q1"（最低波动）~ "Q5"（最高波动），或 "unknown"。
    """
    return _classify_quintile(amplitude, pool_amplitudes)


# ═══════════════════════════════════════════════════════════════
# 分层报告计算
# ═══════════════════════════════════════════════════════════════


def compute_stratified_report(
    trades: list[dict],
    dim1_name: str = "regime",
    dim2_name: str = "time_slot",
    dim1_values: Optional[dict[str, str]] = None,
    dim2_values: Optional[dict[str, str]] = None,
    min_sample: int = 5,
) -> StratifiedReport:
    """计算分层回测报告。

    将交易按 dim1 × dim2 两个维度分组，每组统计样本量、胜率、盈亏。

    参数
    ----
    trades : list[dict]
        交易列表，每条交易至少包含 "time"、"pnl"、"paired" 字段。
    dim1_name : str
        第一维度名称（用作标签，也用作 trade dict 的取值键）。
    dim2_name : str
        第二维度名称。
    dim1_values : dict[str, str] | None
        第一维度的显式映射 {trade_index_str: label}。
        为 None 时从 trade[dim1_name] 取值。
    dim2_values : dict[str, str] | None
        第二维度的显式映射 {trade_index_str: label}。
        为 None 时从 trade[dim2_name] 取值；若 dim2_name="time_slot"
        则自动从 trade["time"] 推断时段。
    min_sample : int
        最小样本量，低于此标记 is_conclusive=False。

    返回
    ----
    StratifiedReport
    """
    # ── 逐笔确定维度值 ──
    cell_groups: dict[tuple[str, str], list[dict]] = {}

    for idx, t in enumerate(trades):
        # 仅统计已配对交易
        if not t.get("paired", False):
            continue

        # dim1
        if dim1_values is not None:
            d1 = dim1_values.get(str(idx), "unknown")
        else:
            d1 = str(t.get(dim1_name, "unknown"))

        # dim2
        if dim2_values is not None:
            d2 = dim2_values.get(str(idx), "unknown")
        elif dim2_name == "time_slot":
            # 自动从 time 字段推断时段
            d2 = classify_time_slot(t.get("time", ""))
        else:
            d2 = str(t.get(dim2_name, "unknown"))

        key = (d1, d2)
        if key not in cell_groups:
            cell_groups[key] = []
        cell_groups[key].append(t)

    # ── 聚合每个格子 ──
    cells: list[StratumCell] = []
    for (d1_label, d2_label), group in cell_groups.items():
        n_paired = len(group)
        n_wins = sum(1 for t in group if t.get("pnl", 0) > 0)
        net_pnl = sum(t.get("pnl", 0) for t in group)
        win_rate = n_wins / n_paired if n_paired > 0 else 0.0
        is_conclusive = n_paired >= min_sample

        cells.append(StratumCell(
            dim1_label=d1_label,
            dim2_label=d2_label,
            n_paired=n_paired,
            n_wins=n_wins,
            win_rate=round(win_rate, 4),
            net_pnl=round(net_pnl, 4),
            is_conclusive=is_conclusive,
        ))

    # 按 dim1 标签排序，再按 dim2 标签排序
    cells.sort(key=lambda c: (c.dim1_label, c.dim2_label))

    return StratifiedReport(
        dim1_name=dim1_name,
        dim2_name=dim2_name,
        cells=cells,
    )