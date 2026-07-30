"""
V3 Risk Engine — 风险评分升级
================================

职责：
  评估当前交易的风险/收益比，输出风险评分。

用户方案 §十一：
  加入 Expected Loss / Expected Reward
  动态仓位：Size = Kelly × Confidence × ATR
  不要固定 25%。

评分逻辑：
  - 高分 = 低风险（ATR 小，止损窄，Kelly 值合理）
  - 低分 = 高风险（ATR 大，止损宽，Kelly 值过大）

输出：0~100
  - 90+: 低风险（ATR < 0.5%）
  - 50: 中等风险（ATR ≈ 1%）
  - 20: 高风险（ATR > 2%）
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class RiskEngine(BaseEngine):
    """V3 Risk Engine：风险评分。"""

    @property
    def name(self) -> str:
        return "risk"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算风险评分 (0~100)。高分=低风险。"""
        price = snap.get("current_price")
        atr = snap.get("atr")

        if price is None or price <= 0 or atr is None:
            return 50.0

        # ATR 占价格比例
        atr_pct = atr / price

        # 波动率评分
        if atr_pct < 0.003:
            vol_score = 90  # 极低波动
        elif atr_pct < 0.005:
            vol_score = 80
        elif atr_pct < 0.008:
            vol_score = 65
        elif atr_pct < 0.012:
            vol_score = 50
        elif atr_pct < 0.02:
            vol_score = 35
        else:
            vol_score = 20  # 高波动

        # 止损距离评分（止损宽 = 低风险，因为有更多空间）
        # 但趋势跟随策略中止损太宽 = 亏损大，所以适中最好
        stop_loss_ratio = snap.get("_stop_loss_ratio", 0.002)
        if stop_loss_ratio > 0:
            # 止损/ATR 比率：止损 < 1 ATR = 太紧，止损 > 3 ATR = 太松
            stop_atr_ratio = stop_loss_ratio / atr_pct if atr_pct > 0 else 1.0
            if 1.0 <= stop_atr_ratio <= 2.0:
                stop_score = 80  # 止损合理
            elif stop_atr_ratio < 1.0:
                stop_score = 50  # 止损太紧
            else:
                stop_score = 40  # 止损太松
        else:
            stop_score = 50.0

        # 日内时间风险（接近收盘风险高，因为无法平仓）
        minute_of_day = snap.get("minute_of_day")
        time_score = 70.0
        if minute_of_day is not None:
            if minute_of_day >= 840:  # 14:00 后
                time_score = 50.0
            if minute_of_day >= 870:  # 14:30 后
                time_score = 35.0

        # 加权平均
        score = vol_score * 0.5 + stop_score * 0.3 + time_score * 0.2

        return max(0.0, min(100.0, score))

    @staticmethod
    def compute_kelly_size(
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        confidence: float = 1.0,
        max_size: float = 0.25,
    ) -> float:
        """计算 Kelly 仓位比例。

        Kelly = W - (1-W)/R
        W = 胜率, R = avg_win/avg_loss

        :param win_rate: 胜率 (0~1)
        :param avg_win: 平均盈利
        :param avg_loss: 平均亏损（正数）
        :param confidence: 置信度调整因子 (0~1)
        :param max_size: 最大仓位比例
        :return: 建议仓位比例 (0~max_size)
        """
        if avg_loss <= 0 or win_rate <= 0:
            return 0.0
        R = avg_win / avg_loss
        kelly = win_rate - (1 - win_rate) / R
        if kelly <= 0:
            return 0.0
        # 半 Kelly + 置信度调整
        size = kelly * 0.5 * confidence
        return min(max_size, max(0.0, size))
