"""
G7: Regime Engine — 市场状态识别
==================================

职责：
  识别当前市场状态（趋势/震荡/极端），为其他 Engine 提供环境上下文。

用户方案 §二：
  Regime Engine 在 Trend Engine 之前，提供市场环境判定。

市场状态（4 种）：
  - trend_up: 上升趋势（ADX > 25, DI+ > DI-）
  - trend_down: 下降趋势（ADX > 25, DI- > DI+）
  - range: 震荡（ADX < 20）
  - extreme: 极端趋势（ADX > 40, 价格远离 VWAP）

数据驱动设计（用户方案）：
  从规则判断转向聚类/树模型数据驱动。
  G1 阶段先用规则判定（复用 detect_market_regime），
  后续可接入 HMM/GMM 聚类模型。

输出：0~100
  - 90+: 强趋势环境（trend_up/down，ADX 高）
  - 50: 中性环境（range）
  - 10: 极端环境（excessive，风险高）

  注意：Regime Score 不是"好/坏"，而是"趋势强度"。
  趋势跟随策略中，高 Regime Score = 适合开仓。
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class RegimeEngine(BaseEngine):
    """G7: 市场状态识别评分。"""

    @property
    def name(self) -> str:
        return "regime"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算市场状态评分 (0~100)。

        评分映射：
          - trend_up/down + ADX > 35: 90~100
          - trend_up/down + ADX 25~35: 70~85
          - range + ADX < 20: 40~50
          - extreme: 15~25（风险高，减分）
        """
        adx = snap.get("adx")
        pdi = snap.get("pdi")  # DI+
        mdi = snap.get("mdi")  # DI-
        vwap_dev = snap.get("vwap_dev")
        atr = snap.get("atr")
        price = snap.get("current_price")

        if adx is None:
            return 50.0

        # 极端趋势检测
        is_extreme = False
        if adx >= 40 and vwap_dev is not None and atr is not None and atr > 0:
            dev_atr = abs(vwap_dev) / atr
            if dev_atr >= 2.0:
                is_extreme = True

        if is_extreme:
            return 20.0  # 极端趋势，风险高

        # 趋势强度评分
        if adx >= 35:
            base = 90.0
        elif adx >= 25:
            base = 70.0 + (adx - 25) * 1.0  # 25→70, 35→80
        elif adx >= 20:
            base = 50.0 + (adx - 20) * 4.0  # 20→50, 25→70
        else:
            base = 30.0 + adx  # 0→30, 20→50

        # 方向确认：DI+ > DI- 为上升趋势，适合 reduce（卖出腿在上升趋势中）
        # DI- > DI+ 为下降趋势，适合 add（买入腿在下降趋势中）
        if pdi is not None and mdi is not None:
            if direction == "reduce" and pdi > mdi:
                base += 5  # 上升趋势中卖出腿加分
            elif direction == "add" and mdi > pdi:
                base += 5  # 下降趋势中买入腿加分
            elif direction == "reduce" and mdi > pdi:
                base -= 10  # 下降趋势中卖出腿减分
            elif direction == "add" and pdi > mdi:
                base -= 10  # 上升趋势中买入腿减分

        return max(0.0, min(100.0, base))

    @staticmethod
    def classify_regime(snap: dict) -> str:
        """分类市场状态（供其他 Engine 查询）。

        返回: "trend_up" / "trend_down" / "range" / "extreme"
        """
        adx = snap.get("adx", 0)
        pdi = snap.get("pdi", 0)
        mdi = snap.get("mdi", 0)
        vwap_dev = snap.get("vwap_dev", 0)
        atr = snap.get("atr", 0)

        if adx >= 40 and atr > 0 and abs(vwap_dev) / atr >= 2.0:
            return "extreme"
        if adx >= 25:
            return "trend_up" if pdi >= mdi else "trend_down"
        return "range"
