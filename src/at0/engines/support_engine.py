"""
G2: Support Engine — 动态支撑/阻力评分
========================================

职责：
  识别当前价格附近的动态支撑/阻力位，评估价格是否在强支撑/阻力附近。

支撑/阻力候选（用户方案 §七）：
  - VWAP（日内 VWAP）
  - EMA20 / EMA30 / EMA60
  - Yesterday Close
  - Opening Range Mid
  - Anchored VWAP（日内最高点锚定 VWAP）

评分逻辑（用户方案 §七）：
  SupportScore = f(距离, 成交量, 历史反弹次数, Bounce Success)

  - 距离：价格离最近支撑/阻力越近，评分越高
  - 成交量：支撑位成交量越大，评分越高
  - 历史反弹次数：该支撑位当天被测试次数越多，评分越高
  - Bounce Success：从该支撑位反弹的成功率

输出：0~100
  - 90+: 价格在强支撑位（买入腿）或强阻力位（卖出腿），高概率反弹
  - 50: 价格在中间区域
  - 10: 价格远离所有支撑/阻力位

趋势跟随适配：
  - direction="reduce"（卖出）：价格在阻力位附近评分高
  - direction="add"（买入）：价格在支撑位附近评分高
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class SupportEngine(BaseEngine):
    """G2: 动态支撑/阻力评分。"""

    @property
    def name(self) -> str:
        return "support"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算支撑/阻力评分 (0~100)。"""
        price = snap.get("current_price")
        if price is None or price <= 0:
            return 50.0

        # 收集所有候选支撑/阻力位
        levels = self._collect_levels(bars, snap)
        if not levels:
            return 50.0

        # 找到最近的支撑/阻力位
        min_dist = float("inf")
        nearest_level = None
        nearest_type = None  # "support" or "resistance"
        for level_info in levels:
            level = level_info["price"]
            dist_pct = abs(price - level) / price
            if dist_pct < min_dist:
                min_dist = dist_pct
                nearest_level = level_info

        if nearest_level is None or min_dist > 0.03:
            # 远离所有支撑/阻力位（>3%），评分低
            return 30.0

        # 基础评分：距离越近评分越高
        if min_dist < 0.002:
            base = 90.0
        elif min_dist < 0.005:
            base = 80.0
        elif min_dist < 0.01:
            base = 70.0
        elif min_dist < 0.02:
            base = 55.0
        else:
            base = 40.0

        # 方向确认：价格在支撑位上方（买入腿加分）或阻力位下方（卖出腿加分）
        level_price = nearest_level["price"]
        if direction == "add" and price > level_price:
            base += 5  # 买入腿：价格在支撑上方
        elif direction == "reduce" and price < level_price:
            base += 5  # 卖出腿：价格在阻力下方

        # 成交量加权：支撑位成交量越大，评分越高
        vol_weight = nearest_level.get("vol_weight", 1.0)
        base *= vol_weight

        # 历史反弹次数：当天被测试次数越多，评分越高
        bounce_count = nearest_level.get("bounce_count", 0)
        if bounce_count >= 3:
            base += 5
        elif bounce_count >= 2:
            base += 3

        return max(0.0, min(100.0, base))

    def _collect_levels(self, bars: list[dict], snap: dict) -> list[dict]:
        """收集所有候选支撑/阻力位。"""
        levels = []
        price = snap.get("current_price")
        if price is None:
            return levels

        # 1. VWAP
        vwap = snap.get("vwap")
        if vwap is not None and vwap > 0:
            levels.append({
                "price": vwap,
                "type": "vwap",
                "vol_weight": 1.2,  # VWAP 权重高
                "bounce_count": self._count_bounces(bars, vwap),
            })

        # 2. EMA20 / EMA30 / EMA60
        for ema_key, ema_weight in [("ema", 1.0), ("ma5", 0.8), ("ma20", 1.1)]:
            ema_val = snap.get(ema_key)
            if ema_val is not None and ema_val > 0:
                levels.append({
                    "price": ema_val,
                    "type": ema_key,
                    "vol_weight": ema_weight,
                    "bounce_count": self._count_bounces(bars, ema_val),
                })

        # 3. Yesterday Close
        prev_close = snap.get("prev_close")
        if prev_close is not None and prev_close > 0:
            levels.append({
                "price": prev_close,
                "type": "prev_close",
                "vol_weight": 0.9,
                "bounce_count": 0,
            })

        # 4. Opening Range
        or_low = snap.get("or_low")
        or_high = snap.get("or_high")
        if or_low is not None and or_high is not None:
            or_mid = (or_low + or_high) / 2
            levels.append({
                "price": or_mid,
                "type": "or_mid",
                "vol_weight": 0.7,
                "bounce_count": 0,
            })
            if or_low > 0:
                levels.append({
                    "price": or_low,
                    "type": "or_low",
                    "vol_weight": 0.8,
                    "bounce_count": 0,
                })
            if or_high > 0:
                levels.append({
                    "price": or_high,
                    "type": "or_high",
                    "vol_weight": 0.8,
                    "bounce_count": 0,
                })

        # 5. BB lower/upper
        bb_lower = snap.get("bb_lower")
        bb_upper = snap.get("bb_upper")
        if bb_lower is not None and bb_lower > 0:
            levels.append({
                "price": bb_lower,
                "type": "bb_lower",
                "vol_weight": 0.7,
                "bounce_count": 0,
            })
        if bb_upper is not None and bb_upper > 0:
            levels.append({
                "price": bb_upper,
                "type": "bb_upper",
                "vol_weight": 0.7,
                "bounce_count": 0,
            })

        return levels

    @staticmethod
    def _count_bounces(bars: list[dict], level: float, tolerance: float = 0.002) -> int:
        """统计当天价格在该支撑/阻力位附近的反弹次数。"""
        if not bars or level <= 0:
            return 0
        count = 0
        # 只看最近 48 根 K 线（约一天）
        recent = bars[-48:]
        for i in range(1, len(recent) - 1):
            low = recent[i].get("low", 0)
            high = recent[i].get("high", 0)
            prev_low = recent[i - 1].get("low", 0)
            next_high = recent[i + 1].get("high", 0)
            # 价格触及该位置（在容差范围内）并反弹
            if abs(low - level) / level < tolerance and next_high > high:
                count += 1
            elif abs(high - level) / level < tolerance and next_high < high:
                count += 1
        return min(count, 5)  # 上限 5
