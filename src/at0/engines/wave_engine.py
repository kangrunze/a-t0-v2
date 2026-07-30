"""
G4: Wave Engine — 波浪位置评分
================================

职责：
  评估当前价格处于波浪的哪个位置，避免在波浪末期追高/追低。

用户方案 §六：
  WaveScore 基于：
    - Trend Age（趋势持续时间）：越长扣分越多（趋势可能衰竭）
    - HH Count（连续创新高次数）：越多扣分越多（可能见顶）
    - HL Count（Higher Low 次数）：趋势健康度
    - ATR Expansion（波动率扩张/收缩）：扩张=趋势加速，收缩=趋势衰竭
    - Momentum Decay（动量衰减）：最近动量 vs 之前动量
    - Volume Decay（成交量衰减）：缩量=趋势衰竭

评分逻辑：
  100: 波浪起点（趋势刚开始，Trend Age 短，HH 少，量能放大）
  50: 波浪中段
  0: 波浪末期（Trend Age 长，HH 过多，量能衰减，动量衰减）

趋势跟随适配：
  - direction="reduce"：价格在波浪高位（HH 多），适合卖出
  - direction="add"：价格在波浪低位（HL 少），适合买入
  但 Wave Score 本身是位置评分，不区分方向。方向由 alpha 聚合层处理。
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class WaveEngine(BaseEngine):
    """G4: 波浪位置评分。"""

    @property
    def name(self) -> str:
        return "wave"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算波浪位置评分 (0~100)。"""
        if len(bars) < 10:
            return 50.0

        # 1. Trend Age：当前趋势持续了多少根 K 线
        trend_age = self._compute_trend_age(bars, direction)
        # 趋势持续 1-8 根：新鲜，高分
        # 趋势持续 9-20 根：成熟，中分
        # 趋势持续 >20 根：老化，低分
        if trend_age <= 8:
            age_score = 90 - trend_age * 3  # 87~90
        elif trend_age <= 20:
            age_score = 60 - (trend_age - 8) * 3  # 24~57
        else:
            age_score = max(10, 24 - (trend_age - 20))  # 递减

        # 2. HH Count：连续创新高/新低次数
        hh_count = self._count_consecutive_extremes(bars, direction)
        # HH 少（1-3）：趋势早期，高分
        # HH 多（>6）：可能见顶，低分
        if hh_count <= 3:
            hh_score = 90 - hh_count * 5  # 75~90
        elif hh_count <= 6:
            hh_score = 60 - (hh_count - 3) * 8  # 36~57
        else:
            hh_score = max(10, 36 - (hh_count - 6) * 5)

        # 3. ATR Expansion：波动率是否在扩张
        atr_expansion = self._compute_atr_expansion(bars)
        if atr_expansion > 1.2:
            atr_score = 80  # 波动扩张，趋势加速
        elif atr_expansion > 0.9:
            atr_score = 60  # 波动稳定
        else:
            atr_score = 30  # 波动收缩，趋势衰竭

        # 4. Momentum Decay：最近动量 vs 之前动量
        momentum_decay = self._compute_momentum_decay(bars)
        if momentum_decay > 0.8:
            mom_score = 80  # 动量增强
        elif momentum_decay > 0.5:
            mom_score = 50  # 动量稳定
        else:
            mom_score = 20  # 动量衰减

        # 5. Volume Decay：成交量是否在衰减
        vol_decay = self._compute_volume_decay(bars)
        if vol_decay > 1.0:
            vol_score = 80  # 放量
        elif vol_decay > 0.7:
            vol_score = 50  # 量能稳定
        else:
            vol_score = 25  # 缩量

        # 加权平均
        score = (
            age_score * 0.25 +
            hh_score * 0.25 +
            atr_score * 0.15 +
            mom_score * 0.20 +
            vol_score * 0.15
        )

        return max(0.0, min(100.0, score))

    @staticmethod
    def _compute_trend_age(bars: list[dict], direction: str) -> int:
        """计算当前趋势持续的 K 线数。

        reduce 方向：从最近一根开始往前数，直到价格不再创新高
        add 方向：从最近一根开始往前数，直到价格不再创新低
        """
        if len(bars) < 2:
            return 0
        age = 0
        if direction == "reduce":
            # 上升趋势：连续创新高
            for i in range(len(bars) - 1, 0, -1):
                if bars[i].get("high", 0) >= bars[i - 1].get("high", 0):
                    age += 1
                else:
                    break
        else:
            # 下降趋势：连续创新低
            for i in range(len(bars) - 1, 0, -1):
                if bars[i].get("low", 0) <= bars[i - 1].get("low", 0):
                    age += 1
                else:
                    break
        return age

    @staticmethod
    def _count_consecutive_extremes(bars: list[dict], direction: str) -> int:
        """统计连续创新高/新低的次数。"""
        if len(bars) < 3:
            return 0
        count = 0
        if direction == "reduce":
            highest = 0
            for i in range(len(bars) - 1, -1, -1):
                high = bars[i].get("high", 0)
                if high > highest:
                    highest = high
                    count += 1
                elif count > 0:
                    break
        else:
            lowest = float("inf")
            for i in range(len(bars) - 1, -1, -1):
                low = bars[i].get("low", 0)
                if low < lowest:
                    lowest = low
                    count += 1
                elif count > 0:
                    break
        return min(count, 10)

    @staticmethod
    def _compute_atr_expansion(bars: list[dict]) -> float:
        """计算 ATR 扩张比率（最近 5 根 TR 均值 / 之前 20 根 TR 均值）。"""
        if len(bars) < 25:
            return 1.0
        trs = []
        for i in range(1, len(bars)):
            high = bars[i].get("high", 0)
            low = bars[i].get("low", 0)
            prev_close = bars[i - 1].get("close", 0)
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        recent_avg = sum(trs[-5:]) / 5 if len(trs) >= 5 else 1.0
        prior_avg = sum(trs[-20:-5]) / 15 if len(trs) >= 20 else recent_avg
        if prior_avg > 0:
            return recent_avg / prior_avg
        return 1.0

    @staticmethod
    def _compute_momentum_decay(bars: list[dict]) -> float:
        """计算动量衰减比率（最近 5 根 ROC / 之前 5 根 ROC）。"""
        if len(bars) < 12:
            return 0.5
        closes = [b.get("close", 0) for b in bars]
        # 最近 5 根 ROC
        recent_roc = (closes[-1] - closes[-6]) / closes[-6] if closes[-6] > 0 else 0
        # 之前 5 根 ROC
        prior_roc = (closes[-6] - closes[-11]) / closes[-11] if closes[-11] > 0 else 0
        if abs(prior_roc) > 0.001:
            return abs(recent_roc) / abs(prior_roc)
        return 0.5

    @staticmethod
    def _compute_volume_decay(bars: list[dict]) -> float:
        """计算成交量衰减比率（最近 5 根均量 / 之前 20 根均量）。"""
        if len(bars) < 25:
            return 1.0
        vols = [b.get("volume", 0) for b in bars]
        recent_avg = sum(vols[-5:]) / 5 if len(vols) >= 5 else 1.0
        prior_avg = sum(vols[-20:-5]) / 15 if len(vols) >= 20 else recent_avg
        if prior_avg > 0:
            return recent_avg / prior_avg
        return 1.0
