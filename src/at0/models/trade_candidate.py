"""
TradeCandidate — V3 交易候选容器
==================================

职责：
  承载 Opportunity Engine 产出的交易候选，含七维度子评分 + 综合评分。

设计原则：
  1. frozen=True，Opportunity Engine 产出后不可变
  2. 子评分 0~100，综合评分 0~100
  3. 权重从 thresholds.yaml 读取，支持版本管理和 A/B 测试

V3 七维度（用户方案 §十二 Alpha 连续评分）：
  Trend        25%
  Wave         20%
  Support      15%
  Momentum     15%
  Liquidity    10%
  ExpectedMove 10%
  Risk          5%

G1 阶段：子评分用现有 features 计算（占位），后续 G2-G7 逐步替换为独立 Engine。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TradeCandidate:
    """V3 交易候选（含子评分 + 综合评分）。

    子评分 0~100，100 表示最强信号。
    alpha_score 加权平均后 clip [0, 100]。
    """

    # ── 七维度子评分（0~100）──
    trend_score: float = 0.0
    wave_score: float = 0.0
    support_score: float = 0.0
    momentum_score: float = 0.0
    liquidity_score: float = 0.0
    expected_move_score: float = 0.0
    risk_score: float = 0.0

    # ── 综合评分（加权平均）──
    alpha_score: float = 0.0

    # ── 方向 ──
    direction: str = "reduce"  # "reduce" 或 "add"

    # ── 诊断信息（用于 Measurement 层归因）──
    sub_scores_detail: dict = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        """是否可交易（alpha_score >= 阈值时为 True，阈值由调用方判断）。"""
        return self.alpha_score > 0
