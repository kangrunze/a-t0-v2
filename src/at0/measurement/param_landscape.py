"""
参数平台扫描 — 识别平台区 vs 尖峰（P1）
========================================
参数平台：扫参数邻域，看是宽平台还是尖峰。
尖峰 = 过拟合信号，直接否决该参数上生产。

用途：
  - 平台区宽 → 参数鲁棒，可上生产
  - 尖峰 → 过拟合，否决
  - 决定 P3 是否动态化该参数（平台区变宽才动态化）
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable


@dataclass
class ParamPoint:
    """单个参数取值下的回测结果点。"""
    param_value: float
    net_pnl: float
    n_trades: int
    win_rate: float
    profit_factor: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LandscapeReport:
    """参数景观扫描报告。"""
    param_name: str
    points: list[ParamPoint]
    peak_index: int
    platform_ratio: float
    is_peak_sharp: bool

    def to_dict(self) -> dict:
        return asdict(self)


def scan_param_landscape(
    param_name: str,
    param_values: list[float],
    backtest_fn: Callable[[float], dict],
    min_trades: int = 10,
) -> LandscapeReport:
    """扫描参数景观，判断参数平台宽度。

    参数
    ----
    param_name : 参数名称
    param_values : 要扫描的参数值列表
    backtest_fn : 回调函数，输入参数值，返回包含
                  net_pnl / n_trades / win_rate / profit_factor 的字典
    min_trades : 最小可信交易数（默认 10，<10 笔不可信）

    返回
    ----
    LandscapeReport
    """
    raw_points: list[ParamPoint] = []

    for val in param_values:
        result = backtest_fn(val)
        raw_points.append(ParamPoint(
            param_value=val,
            net_pnl=result.get("net_pnl", 0.0),
            n_trades=result.get("n_trades", 0),
            win_rate=result.get("win_rate", 0.0),
            profit_factor=result.get("profit_factor", 0.0),
        ))

    # 过滤 n_trades < min_trades 的点
    valid_points = [p for p in raw_points if p.n_trades >= min_trades]

    if not valid_points or len(valid_points) == 0:
        # 无有效点，返回空报告
        return LandscapeReport(
            param_name=param_name,
            points=raw_points,
            peak_index=-1,
            platform_ratio=0.0,
            is_peak_sharp=True,
        )

    # 找峰值（最高 net_pnl）
    peak_net_pnl = max(p.net_pnl for p in valid_points)
    # 找到第一个达到峰值的索引（在原始 raw_points 中的位置）
    peak_index = -1
    for i, p in enumerate(raw_points):
        if p.n_trades >= min_trades and p.net_pnl == peak_net_pnl:
            peak_index = i
            break

    # 计算平台比率：在峰值 90% 性能以内的点占比
    if peak_net_pnl == 0:
        # 所有点 net_pnl 均为 0，则所有点都在平台内
        threshold = 0.0
    elif peak_net_pnl > 0:
        threshold = 0.9 * peak_net_pnl
    else:
        # 峰值 net_pnl 为负，取 90% 方向（更靠近 0 的方向）
        threshold = 0.9 * peak_net_pnl  # 负数，如 -10 → -9

    n_within_platform = sum(
        1 for p in valid_points
        if (peak_net_pnl >= 0 and p.net_pnl >= threshold)
        or (peak_net_pnl < 0 and p.net_pnl <= threshold)
    )

    platform_ratio = n_within_platform / len(valid_points)
    is_peak_sharp = platform_ratio < 0.3

    return LandscapeReport(
        param_name=param_name,
        points=raw_points,
        peak_index=peak_index,
        platform_ratio=round(platform_ratio, 4),
        is_peak_sharp=is_peak_sharp,
    )