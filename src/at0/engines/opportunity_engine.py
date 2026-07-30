"""
G3: Opportunity Engine — 综合机会评分
======================================

职责：
  将各维度子评分非线性组合，输出最终交易机会评分。

用户方案 §九：
  Opportunity = Trend × Wave × Support × ExpectedMove × Liquidity × Risk

设计：
  1. 非线性组合：低评分维度会拖累整体（乘法效应）
  2. 加权变体：允许某些维度权重更高（如 Trend 在趋势跟随中权重最高）
  3. 输出 0~100，作为最终开仓决策依据

评分映射（用户方案 §九）：
  92: 重仓
  81: 正常
  66: 轻仓
  55: 不交易
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class OpportunityEngine(BaseEngine):
    """G3: 综合机会评分。"""

    @property
    def name(self) -> str:
        return "opportunity"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算综合机会评分 (0~100)。

        从 snap 中读取各维度子评分（由 alpha_score.py 聚合时注入）。
        如果 snap 中没有子评分，返回中性 50。
        """
        sub_scores = snap.get("_sub_scores", {})
        if not sub_scores:
            return 50.0

        # 非线性组合：几何平均（乘法效应，低评分拖累整体）
        scores = [
            sub_scores.get("trend", 50),
            sub_scores.get("wave", 50),
            sub_scores.get("support", 50),
            sub_scores.get("momentum", 50),
            sub_scores.get("liquidity", 50),
            sub_scores.get("expected_move", 50),
            sub_scores.get("risk", 50),
        ]

        # 几何平均：所有维度先除以 100，相乘，再开 7 次方，再乘 100
        product = 1.0
        for s in scores:
            product *= max(0.01, s / 100.0)  # 避免 0
        geometric = (product ** (1.0 / len(scores))) * 100.0

        # 与算术平均混合（50% 几何 + 50% 算术），避免几何平均过度惩罚
        arithmetic = sum(scores) / len(scores)
        opportunity = 0.5 * geometric + 0.5 * arithmetic

        return max(0.0, min(100.0, opportunity))

    def position_size_hint(self, opportunity_score: float) -> str:
        """根据机会评分给出仓位建议。"""
        if opportunity_score >= 90:
            return "heavy"
        elif opportunity_score >= 80:
            return "normal"
        elif opportunity_score >= 65:
            return "light"
        else:
            return "skip"
