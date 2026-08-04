"""
Stage X: Predictive Exit Engine — 提前识别趋势衰竭
====================================================

核心思路（与 L6/L7 的互补关系）：
  L6 HoldConfidence / L7 TrendFailure 是 REACTIVE：趋势已死才退出。
  Predictive Exit 是 PROACTIVE：检测趋势即将结束的信号，提前退出。

四维度信号：
  1. 量能衰竭 (30%)：价格趋势延续但成交量萎缩 → 上涨动力不足
  2. 动量背离 (30%)：价格创高点但动量下降 → 趋势衰竭前兆
  3. 波动率高潮 (20%)：突然放量宽幅波动 → 趋势末期高潮
  4. 趋势成熟度 (20%)：趋势持续时间越长，反转概率越大

评分映射：
  ≥ 80: 强退出信号 — 趋势即将结束
  ≥ 65: 中等退出信号 — 考虑收紧退出
  ≥ 50: 轻微预警 — 注意观察
  < 50: 趋势正常 — 无需干预

与 L6/L7 配合：
  - 当 Predictive Exit ≥ 80 且未触发 L6/L7 时，提前退出（避免深度回调）
  - 当 Predictive Exit 65-79 时，保持不变（让 L6/L7 正常判断）
  - 当 Predictive Exit < 65 时，不做干预
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class PredictiveExitEngine(BaseEngine):
    """Stage X: 趋势衰竭预测评分 (0~100)。

    评分越高 = 趋势越可能结束 → 越应该退出。
    """

    # 四维度权重
    WEIGHTS = {
        "volume_exhaustion": 0.30,    # 量能衰竭
        "momentum_divergence": 0.30,  # 动量背离
        "volatility_climax": 0.20,    # 波动率高潮
        "trend_maturity": 0.20,       # 趋势成熟度
    }

    @property
    def name(self) -> str:
        return "predictive_exit"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算趋势衰竭预测评分 (0~100)。

        :param bars: 完整 K 线序列
        :param snap: 当前 bar 的特征快照
        :param direction: "reduce"（评估 buy 仓）或 "add"（评估 sell 仓）
        :return: 0~100，越高越应该退出
        """
        total_score = 0.0
        total_weight = 0.0

        # 1. 量能衰竭 (30%)
        ve = self._score_volume_exhaustion(bars, snap, direction)
        if ve is not None:
            total_score += ve * self.WEIGHTS["volume_exhaustion"]
            total_weight += self.WEIGHTS["volume_exhaustion"]

        # 2. 动量背离 (30%)
        md = self._score_momentum_divergence(bars, snap, direction)
        if md is not None:
            total_score += md * self.WEIGHTS["momentum_divergence"]
            total_weight += self.WEIGHTS["momentum_divergence"]

        # 3. 波动率高潮 (20%)
        vc = self._score_volatility_climax(bars, snap, direction)
        if vc is not None:
            total_score += vc * self.WEIGHTS["volatility_climax"]
            total_weight += self.WEIGHTS["volatility_climax"]

        # 4. 趋势成熟度 (20%)
        tm = self._score_trend_maturity(bars, direction)
        if tm is not None:
            total_score += tm * self.WEIGHTS["trend_maturity"]
            total_weight += self.WEIGHTS["trend_maturity"]

        if total_weight <= 0:
            return 50.0
        return max(0.0, min(100.0, total_score / total_weight))

    # ═══════════════════════════════════════════════════════════════
    # 1. 量能衰竭
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _score_volume_exhaustion(
        bars: list[dict],
        snap: dict,
        direction: str,
    ) -> Optional[float]:
        """量能衰竭评分 (0~100)。

        条件：价格趋势有利方向，但成交量在萎缩。
        量比 < 0.8 + 近5根量能衰减 → 衰竭信号。
        """
        vol_ratio = snap.get("volume_ratio")
        recent_5_vol = snap.get("recent_5_vol")
        prior_20_vol_avg = snap.get("prior_20_vol_avg")
        price = snap.get("current_price")
        ema_val = snap.get("ema")

        if any(v is None for v in [vol_ratio, recent_5_vol, prior_20_vol_avg,
                                    price, ema_val]) or prior_20_vol_avg <= 0:
            return None

        # 价格在 EMA 有利侧（对持仓方向有利）
        is_buy = direction == "reduce"
        price_ok = (is_buy and price > ema_val) or (not is_buy and price < ema_val)
        if not price_ok:
            # 价格已不利 → 不是衰竭，是趋势已死
            return 50.0

        # 量能衰减程度
        vol_ratio_recent = recent_5_vol / prior_20_vol_avg

        # 计算更近期 (3根) vs 中期 (8根) 的量能变化
        if len(bars) >= 8:
            recent_3_vol = sum(b.get("volume", 0) for b in bars[-3:])
            prior_8_vol = sum(b.get("volume", 0) for b in bars[-9:-3]) if len(bars) >= 9 else recent_3_vol
            vol_trend = (recent_3_vol / prior_8_vol) if prior_8_vol > 0 else 1.0
        else:
            vol_trend = 1.0

        # 评分：量比越低 + 量能趋势越衰减 → 分数越高（衰竭信号）
        exhaustion_score = 0.0

        # 绝对量比部分
        if vol_ratio_recent < 0.5:
            exhaustion_score += 40.0  # 极度缩量
        elif vol_ratio_recent < 0.7:
            exhaustion_score += 30.0  # 显著缩量
        elif vol_ratio_recent < 0.9:
            exhaustion_score += 15.0  # 轻度缩量
        elif vol_ratio_recent > 1.5:
            exhaustion_score += 10.0  # 放量不一定是衰竭

        # 量能趋势部分（近3根 vs 前8根）
        if vol_trend < 0.5:
            exhaustion_score += 40.0  # 量能急剧衰减
        elif vol_trend < 0.7:
            exhaustion_score += 30.0
        elif vol_trend < 0.9:
            exhaustion_score += 20.0
        elif vol_trend > 1.3:
            exhaustion_score -= 10.0  # 放量趋势，不是衰竭

        # 量比趋势微调
        if vol_ratio_recent < 0.7 and vol_trend < 0.7:
            exhaustion_score += 20.0  # 双重确认：量比低 + 量能衰减

        return max(0.0, min(100.0, exhaustion_score))

    # ═══════════════════════════════════════════════════════════════
    # 2. 动量背离
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _score_momentum_divergence(
        bars: list[dict],
        snap: dict,
        direction: str,
    ) -> Optional[float]:
        """动量背离评分 (0~100)。

        检查价格趋势与动量（ROC/MACD）是否背离。
        背离 = 价格创高点但动量下降 → 趋势衰竭。
        """
        if len(bars) < 15:
            return None

        closes = [b.get("close", 0) for b in bars]
        is_buy = direction == "reduce"

        # 计算近期价格趋势方向
        recent_high = max(closes[-8:])
        recent_low = min(closes[-8:])
        mid_high = max(closes[-15:-7]) if len(closes) >= 15 else recent_high
        mid_low = min(closes[-15:-7]) if len(closes) >= 15 else recent_low

        # 计算 ROC(6) 动量
        if len(closes) >= 7 and closes[-7] > 0:
            roc_current = (closes[-1] - closes[-7]) / closes[-7]
            roc_prior = (closes[-7] - closes[-13]) / closes[-13] if len(closes) >= 14 and closes[-13] > 0 else roc_current
        else:
            return None

        # 计算 MACD hist 方向
        macd_hist = snap.get("macd_hist")
        macd_dif = snap.get("macd_dif")
        macd_dea = snap.get("macd_dea")

        if is_buy:
            # 上涨趋势中的顶背离：价格新高但动量下降
            price_made_new_high = recent_high > mid_high * 1.005  # 价格创近期新高
            momentum_declining = roc_current < roc_prior * 0.8    # 动量下降
            macd_weakening = (macd_hist is not None and macd_hist < 0) or \
                             (macd_dif is not None and macd_dea is not None and macd_dif < macd_dea)

            if price_made_new_high and momentum_declining:
                score = 80.0  # 明确顶背离
            elif price_made_new_high and macd_weakening:
                score = 70.0  # 价格新高 + MACD 弱化
            elif momentum_declining and macd_weakening:
                score = 60.0  # 动量下降 + MACD 弱化（无新高）
            elif momentum_declining:
                score = 45.0  # 仅动量下降，轻度预警
            else:
                score = 20.0  # 正常趋势
        else:
            # 下跌趋势中的底背离：价格新低但动量上升
            price_made_new_low = recent_low < mid_low * 0.995
            momentum_rising = roc_current > roc_prior * 0.8
            macd_strengthening = (macd_hist is not None and macd_hist > 0) or \
                                 (macd_dif is not None and macd_dea is not None and macd_dif > macd_dea)

            if price_made_new_low and momentum_rising:
                score = 80.0
            elif price_made_new_low and macd_strengthening:
                score = 70.0
            elif momentum_rising and macd_strengthening:
                score = 60.0
            elif momentum_rising:
                score = 45.0
            else:
                score = 20.0

        return max(0.0, min(100.0, score))

    # ═══════════════════════════════════════════════════════════════
    # 3. 波动率高潮
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _score_volatility_climax(
        bars: list[dict],
        snap: dict,
        direction: str,
    ) -> Optional[float]:
        """波动率高潮评分 (0~100)。

        趋势末期常见特征：
        1. 当前 bar 为近期最大振幅之一（高潮柱）
        2. 成交量显著放大（高潮放量）
        3. 上影线/下影线较长（抛压/承接）
        """
        if len(bars) < 10:
            return None

        price = snap.get("current_price")
        vol_ratio = snap.get("volume_ratio")
        current_bar = bars[-1] if bars else None
        if current_bar is None or price is None or price <= 0:
            return None

        close = current_bar.get("close", 0)
        high = current_bar.get("high", 0)
        low = current_bar.get("low", 0)
        volume = current_bar.get("volume", 0)

        if high <= low or close <= 0:
            return None

        # 当前 bar 振幅
        current_range = (high - low) / price

        # 近期平均振幅（10根）
        ranges = []
        for b in bars[-10:]:
            bh = b.get("high", 0)
            bl = b.get("low", 0)
            bc = b.get("close", 0)
            if bh > bl and bc > 0:
                ranges.append((bh - bl) / bc)

        if not ranges:
            return None

        avg_range = sum(ranges) / len(ranges)
        if avg_range <= 0:
            return None

        range_ratio = current_range / avg_range

        # 上影线比例
        upper_shadow = (high - max(close, low)) / (high - low) if high > low else 0
        lower_shadow = (min(close, high) - low) / (high - low) if high > low else 0

        is_buy = direction == "reduce"
        climax_score = 0.0

        # 振幅放大
        if range_ratio >= 2.0:
            climax_score += 40.0  # 2倍平均振幅
        elif range_ratio >= 1.5:
            climax_score += 25.0
        elif range_ratio >= 1.2:
            climax_score += 15.0

        # 成交量放大
        if vol_ratio is not None:
            if vol_ratio >= 2.0:
                climax_score += 30.0  # 2倍均量
            elif vol_ratio >= 1.5:
                climax_score += 20.0
            elif vol_ratio >= 1.2:
                climax_score += 10.0

        # 影线（趋势末期的反转信号）
        if is_buy:
            # 上涨趋势中，长上影线 = 抛压
            if upper_shadow > 0.6:
                climax_score += 30.0
            elif upper_shadow > 0.4:
                climax_score += 15.0
        else:
            # 下跌趋势中，长下影线 = 承接
            if lower_shadow > 0.6:
                climax_score += 30.0
            elif lower_shadow > 0.4:
                climax_score += 15.0

        # 双重确认加分
        if range_ratio >= 1.5 and vol_ratio is not None and vol_ratio >= 1.5:
            climax_score += 20.0  # 放量 + 宽幅 = 强烈高潮信号

        return max(0.0, min(100.0, climax_score))

    # ═══════════════════════════════════════════════════════════════
    # 4. 趋势成熟度
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _score_trend_maturity(
        bars: list[dict],
        direction: str,
    ) -> Optional[float]:
        """趋势成熟度评分 (0~100)。

        趋势持续时间越长，反转概率越大。
        使用连续有利方向 K 线数衡量趋势年龄。
        """
        if len(bars) < 3:
            return None

        age = 0
        if direction == "reduce":
            # 连续上涨 K 线数
            for i in range(len(bars) - 1, 0, -1):
                if bars[i].get("high", 0) >= bars[i - 1].get("high", 0):
                    age += 1
                else:
                    break
        else:
            # 连续下跌 K 线数
            for i in range(len(bars) - 1, 0, -1):
                if bars[i].get("low", 0) <= bars[i - 1].get("low", 0):
                    age += 1
                else:
                    break

        # 5min K 线，趋势年龄与反转概率的关系
        # 0-4 根（~20min）：新鲜趋势，低反转概率
        # 5-9 根（~25-45min）：中等趋势，反转概率上升
        # 10-19 根（~50-95min）：老趋势，反转概率高
        # 20+ 根（~100min+）：极老趋势，反转概率极高
        if age <= 4:
            return 20.0  # 新鲜趋势
        elif age <= 9:
            return 20.0 + (age - 4) * 6.0  # 4→20, 9→50
        elif age <= 19:
            return 50.0 + (age - 9) * 3.0  # 9→50, 19→80
        else:
            return min(100.0, 80.0 + (age - 19) * 1.5)  # 封顶 100