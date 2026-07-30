"""
G6: Execution Engine — 执行层
==============================

职责：
  在 Alpha 信号产生后，决定"何时执行"而非"是否执行"。

用户方案 §十：
  Signal → Execution → Trade

  Execution 负责：
    - 距离 EMA20 0.6% 时等待 2 bars
    - Pullback 完成后再买
    - 这一层通常 CE 直接提升

设计：
  1. 信号产生后不立即执行，等待回踩/冲高确认
  2. 计算执行延迟（延迟多少根 bar）
  3. 计算执行价格偏移（等待价格回到什么位置再执行）
  4. 输出执行评分 + 延迟建议

输出：0~100
  - 90+: 立即执行（价格在理想位置）
  - 70: 短延迟（1-2 bars）
  - 50: 中等延迟（3-5 bars）
  - 20: 长延迟（>5 bars，可能错过机会）
  - 5: 不执行（等待过久，信号过期）

注意：
  G6 Execution Engine 不直接修改回测引擎的执行逻辑，
  而是提供"执行质量评分"，用于 Measurement 层归因。
  实际执行延迟由回测引擎的 cooldown_bars / pairing 逻辑控制。
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class ExecutionEngine(BaseEngine):
    """G6: 执行层评分。"""

    @property
    def name(self) -> str:
        return "execution"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算执行质量评分 (0~100)。

        评估当前 bar 是否是好的执行时机。
        """
        price = snap.get("current_price")
        if price is None or price <= 0:
            return 50.0

        # 1. 价格距 EMA20 的距离（回踩确认）
        ema = snap.get("ema")
        if ema is not None and ema > 0:
            dist_pct = (price - ema) / ema
            if direction == "add":
                # 买入腿：价格回踩到 EMA 附近（距离小）是好的执行时机
                if abs(dist_pct) < 0.003:
                    exec_score = 90  # 回踩到位
                elif abs(dist_pct) < 0.006:
                    exec_score = 75
                elif abs(dist_pct) < 0.01:
                    exec_score = 60
                else:
                    exec_score = 35  # 远离 EMA，追高
            else:
                # 卖出腿：价格冲高到 EMA 上方较远是好的执行时机
                if dist_pct > 0.01:
                    exec_score = 85  # 冲高到位
                elif dist_pct > 0.005:
                    exec_score = 70
                elif abs(dist_pct) < 0.003:
                    exec_score = 50  # 在 EMA 附近
                else:
                    exec_score = 30  # 价格在 EMA 下方
        else:
            exec_score = 50.0

        # 2. KDJ 位置确认（不过度追高/追低）
        kdj_k = snap.get("kdj_k")
        if kdj_k is not None:
            if direction == "add":
                if kdj_k < 30:
                    exec_score += 5  # KDJ 低位买入
                elif kdj_k > 70:
                    exec_score -= 10  # KDJ 高位追高
            else:
                if kdj_k > 70:
                    exec_score += 5  # KDJ 高位卖出
                elif kdj_k < 30:
                    exec_score -= 10  # KDJ 低位杀跌

        # 3. 量能确认
        vol_ratio = snap.get("vol_ratio") or snap.get("volume_ratio")
        if vol_ratio is not None:
            if vol_ratio > 1.5:
                exec_score += 5  # 放量确认
            elif vol_ratio < 0.7:
                exec_score -= 5  # 缩量，执行时机差

        # 4. 日内时间因子（避开开盘和收盘极端时段）
        minute_of_day = snap.get("minute_of_day")
        if minute_of_day is not None:
            # 9:30-9:45 = 570-585, 11:25-13:05 = 午休附近, 14:55-15:00 = 收盘
            if 585 <= minute_of_day <= 600:
                exec_score -= 5  # 开盘 15 分钟内波动大
            elif minute_of_day >= 885:
                exec_score -= 5  # 收盘前 5 分钟

        return max(0.0, min(100.0, exec_score))

    def compute_delay_bars(self, snap: dict, direction: str) -> int:
        """计算建议的执行延迟（根 bar 数）。

        0 = 立即执行
        1-2 = 短延迟
        3-5 = 中延迟
        >5 = 信号可能过期
        """
        price = snap.get("current_price")
        ema = snap.get("ema")
        if price is None or ema is None or ema <= 0:
            return 0

        dist_pct = abs(price - ema) / ema
        if direction == "add":
            # 买入腿：价格在 EMA 上方，等待回踩
            if price > ema and dist_pct > 0.005:
                return min(int(dist_pct / 0.003) + 1, 5)
        else:
            # 卖出腿：价格在 EMA 下方，等待冲高
            if price < ema and dist_pct > 0.005:
                return min(int(dist_pct / 0.003) + 1, 5)
        return 0
