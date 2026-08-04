"""
Trade Quality — 交易质量指标
============================

计算交易质量的核心指标：胜率、Profit Factor、盈亏比、期望收益、MAE/MFE 等。

导出符号:
  TradeQualityReport    — 交易质量报告 dataclass
  compute_trade_quality — 从交易记录和已关闭腿计算交易质量指标
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeQualityReport:
    """交易质量报告。

    Attributes:
        win_rate:      胜率（盈利配对交易数 / 总配对交易数）
        profit_factor: 总盈利 / 总亏损（PF）
        payoff_ratio:  平均盈利 / 平均亏损（盈亏比）
        expectancy:    每笔配对交易平均盈亏（元）
        avg_mae:       平均最大不利偏移（MAE）
        avg_mfe:       平均最大有利偏移（MFE）
        mae_mfe_ratio: MAE / MFE 比值
        expired_count: 超时腿数量
        expired_pnl:   超时腿总盈亏
        stopped_count: 止损腿数量
        stopped_pnl:   止损腿总盈亏
        n_trades:      参与统计的配对交易数
    """
    win_rate: float = 0.0
    profit_factor: float = 0.0
    payoff_ratio: float = 0.0
    expectancy: float = 0.0
    avg_mae: float = 0.0
    avg_mfe: float = 0.0
    mae_mfe_ratio: float = 0.0
    expired_count: int = 0
    expired_pnl: float = 0.0
    stopped_count: int = 0
    stopped_pnl: float = 0.0
    n_trades: int = 0

    def to_dict(self) -> dict:
        """转 dict（兼容 v2_reporter.py 的 HTML 渲染）。"""
        return {
            "n": self.n_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "payoff_ratio": self.payoff_ratio,
            "expectancy": self.expectancy,
            "avg_mae": self.avg_mae,
            "avg_mfe": self.avg_mfe,
            "mae_mfe_ratio": self.mae_mfe_ratio,
            "expired_count": self.expired_count,
            "expired_pnl": self.expired_pnl,
            "stopped_count": self.stopped_count,
            "stopped_pnl": self.stopped_pnl,
            "net_pnl": round(self.expectancy * self.n_trades, 2) if self.n_trades > 0 else 0.0,
        }


def compute_trade_quality(
    trades: list[dict],
    closed_legs: Optional[list[dict]] = None,
) -> TradeQualityReport:
    """计算交易质量指标。

    从 trades 中筛选已配对交易（paired=True 且 pnl 非零）计算胜率/PF/盈亏比/期望，
    从 closed_legs 中提取 MAE/MFE 和超时/止损统计。

    Args:
        trades: 交易记录列表，每条含 pnl / direction / paired 等字段。
        closed_legs: 已关闭腿记录列表，每条含 max_adverse / max_favorable /
                     status / paired_pnl 等字段。默认为 None。

    Returns:
        TradeQualityReport 实例。
    """
    if closed_legs is None:
        closed_legs = []

    # ── 1. 从 trades 计算胜率 / PF / 盈亏比 / 期望 ──
    paired_trades = [
        t for t in trades
        if t.get("paired") and t.get("pnl") is not None and t.get("pnl", 0) != 0
    ]
    pnls = [float(t["pnl"]) for t in paired_trades]

    report = TradeQualityReport()
    report.n_trades = len(pnls)

    if pnls:
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0

        report.win_rate = len(wins) / len(pnls)
        report.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        report.payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        report.expectancy = sum(pnls) / len(pnls)

    # ── 2. 从 closed_legs 计算 MAE / MFE / 超时 / 止损 ──
    if closed_legs:
        mae_values = [float(leg.get("max_adverse", 0.0)) for leg in closed_legs]
        mfe_values = [float(leg.get("max_favorable", 0.0)) for leg in closed_legs]

        report.avg_mae = sum(mae_values) / len(mae_values)
        report.avg_mfe = sum(mfe_values) / len(mfe_values)
        if report.avg_mfe > 0:
            report.mae_mfe_ratio = report.avg_mae / report.avg_mfe

        # 超时腿
        expired_legs = [leg for leg in closed_legs if leg.get("status") == "expired"]
        report.expired_count = len(expired_legs)
        report.expired_pnl = sum(float(leg.get("paired_pnl", 0.0)) for leg in expired_legs)

        # 止损腿
        stopped_legs = [leg for leg in closed_legs if leg.get("status") == "stopped"]
        report.stopped_count = len(stopped_legs)
        report.stopped_pnl = sum(float(leg.get("paired_pnl", 0.0)) for leg in stopped_legs)

    return report