"""
G5: Expected Move Engine — 未来收益预测
========================================

职责：
  预测未来 N 根 K 线的收益和波动范围，计算 Reward/Risk 比率。

用户方案 §八：
  预测：
    - Future Return（未来收益率）
    - Future Range（未来波动范围）
    - Future Volatility（未来波动率）

  计算：
    Reward = Expected Return
    Risk = Expected Range / 2（半波幅作为风险代理）
    RR = Reward / Risk

  如果 RR < 1.5，直接过滤。

预测方式：
  1. 优先使用 Qlib LightGBM 预测（已验证 IC=0.3937）
  2. 降级为统计模型：最近 60 根 K 线的动量 + 波动率外推
  3. 再降级为常数模型：返回中性预期

输出：0~100
  - 90+: RR > 3.0，极强信号
  - 70: RR ≈ 2.0
  - 50: RR ≈ 1.5（门槛）
  - 20: RR < 1.0
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class ExpectedMoveEngine(BaseEngine):
    """G5: 未来收益预测评分。"""

    # Qlib 预测缓存（由外部注入，避免每根 bar 都重新预测）
    _qlib_predictions: Optional[dict] = None  # {("code", datetime): predicted_return}

    @property
    def name(self) -> str:
        return "expected_move"

    @classmethod
    def set_qlib_predictions(cls, predictions: dict):
        """注入 Qlib 预测结果。

        predictions: {(code, datetime_str): predicted_return}
        """
        cls._qlib_predictions = predictions

    @classmethod
    def clear_qlib_predictions(cls):
        """清空 Qlib 预测缓存。"""
        cls._qlib_predictions = None

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算预期移动评分 (0~100)。"""
        # 尝试获取 Qlib 预测
        predicted_return = self._get_qlib_prediction(snap)

        if predicted_return is not None:
            # 使用 Qlib 预测
            rr = self._compute_rr_from_prediction(predicted_return, snap, direction)
        else:
            # 降级为统计模型
            rr = self._compute_rr_statistical(bars, snap, direction)

        if rr is None:
            return 50.0

        # RR 映射到 0~100
        # RR >= 3.0 → 95
        # RR = 2.0 → 75
        # RR = 1.5 → 50（门槛）
        # RR = 1.0 → 25
        # RR < 0.5 → 5
        if rr >= 3.0:
            score = 95.0
        elif rr >= 2.0:
            score = 50.0 + (rr - 1.5) * 45  # 1.5→50, 2.0→72.5, 但修正
            score = min(95.0, 75.0 + (rr - 2.0) * 20)
        elif rr >= 1.5:
            score = 50.0 + (rr - 1.5) * 50  # 1.5→50, 2.0→75
        elif rr >= 1.0:
            score = 25.0 + (rr - 1.0) * 50  # 1.0→25, 1.5→50
        elif rr >= 0.5:
            score = 5.0 + (rr - 0.5) * 40   # 0.5→5, 1.0→25
        else:
            score = max(0.0, 5.0 - (0.5 - rr) * 10)

        return max(0.0, min(100.0, score))

    def _get_qlib_prediction(self, snap: dict) -> Optional[float]:
        """从 Qlib 预测缓存中获取当前 bar 的预测值。"""
        if self._qlib_predictions is None:
            return None
        code = snap.get("code")
        dt = snap.get("datetime")
        if code is None or dt is None:
            return None
        # 尝试多种 key 格式
        for key in [(code, dt), (code, str(dt))]:
            if key in self._qlib_predictions:
                return self._qlib_predictions[key]
        return None

    @staticmethod
    def _compute_rr_from_prediction(
        predicted_return: float,
        snap: dict,
        direction: str,
    ) -> Optional[float]:
        """从 Qlib 预测的 future_return 计算 RR。"""
        # predicted_return 是未来 6 根 bar 的收益率
        # 方向确认：卖出腿需要负收益（价格下跌），买入腿需要正收益（价格上涨）
        # 但趋势跟随策略中：
        #   - reduce（卖出）：开仓后价格继续涨（正收益）→ 有利
        #   - add（买入）：开仓后价格继续跌（负收益）→ 有利
        # 这取决于策略设计，这里用绝对值作为 Reward
        reward = abs(predicted_return)

        # Risk = ATR / price（当前波动率作为风险代理）
        price = snap.get("current_price")
        atr = snap.get("atr")
        if price is None or atr is None or price <= 0:
            return None
        risk = atr / price

        if risk > 0:
            return reward / risk
        return None

    @staticmethod
    def _compute_rr_statistical(
        bars: list[dict],
        snap: dict,
        direction: str,
    ) -> Optional[float]:
        """降级统计模型：用最近动量 + 波动率外推。"""
        if len(bars) < 20:
            return None

        closes = [b.get("close", 0) for b in bars]
        price = snap.get("current_price") or (closes[-1] if closes else 0)
        if price <= 0:
            return None

        # 动量外推：最近 6 根的 ROC 作为预期收益
        if len(closes) >= 7 and closes[-7] > 0:
            roc_6 = (closes[-1] - closes[-7]) / closes[-7]
        else:
            roc_6 = 0.0
        reward = abs(roc_6)

        # 波动率：最近 20 根的收益率标准差
        if len(closes) >= 21:
            rets = [(closes[i] - closes[i-1]) / closes[i-1]
                    for i in range(len(closes) - 20, len(closes))
                    if closes[i-1] > 0]
            if rets:
                mean_ret = sum(rets) / len(rets)
                variance = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
                vol = math.sqrt(variance) * math.sqrt(6)  # 6 根 bar 的波动
            else:
                vol = 0.01
        else:
            vol = 0.01

        if vol > 0:
            return reward / vol
        return None
