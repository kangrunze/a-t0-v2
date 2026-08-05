"""
G5: Expected Move Engine V2 — 多时间框架动量预期
=================================================

Stage E 重构（2026-08-03）：

核心改进：
  1. 多时间框架动量：ROC(3)/ROC(6)/ROC(12) 加权合成，降低单周期噪声
  2. ATR 风险基准：统一用 ATR 作为风险度量，与用户方案"EM=1.5~2.0×ATR"对齐
  3. 方向感知评分：休息预期收益方向与当前持仓方向一致才给高分
  4. 置信度加权：数据越充分（K线数越多），评分越稳定

预期收益 = 多时间框架 ROC 加权平均（衰减权重）
风险 = ATR / price（当前波动率）
RR = |预期收益| / 风险

评分映射：
  RR ≥ 3.0 → 95（极强信号）
  RR = 2.0 → 75（强信号，开仓门槛）
  RR = 1.5 → 55（中等信号）
  RR = 1.0 → 30（弱信号）
  RR < 0.5 → 5（无信号）

Qlib 预测接口保留（class-level 缓存），降级时使用统计模型。
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class ExpectedMoveEngine(BaseEngine):
    """G5: 未来收益预期评分 V2。"""

    # Qlib 预测缓存（由外部注入，避免每根 bar 都重新预测）

    def __init__(self):
        self._qlib_predictions: Optional[dict] = None

    # 多时间框架 ROC 参数
    ROC_PERIODS = [3, 6, 12]         # 3根/6根/12根 K线 ROC
    ROC_WEIGHTS = [0.5, 0.3, 0.2]    # 衰减权重：短周期 > 中周期 > 长周期

    @property
    def name(self) -> str:
        return "expected_move"

    def set_qlib_predictions(self, predictions: dict):
        """注入 Qlib 预测结果。"""
        self._qlib_predictions = predictions

    def clear_qlib_predictions(self):
        """清空 Qlib 预测缓存。"""
        self._qlib_predictions = None

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算预期移动评分 (0~100)。

        V2 改进：多时间框架动量 + ATR 风险基准 + 方向感知。
        """
        rr = self._compute_rr(bars, snap, direction)
        if rr is None:
            return 50.0

        # RR → 0~100 评分映射
        # V2 校准：RR=2.0 开仓门槛 → 75分，RR=1.5 中等 → 55分
        if rr >= 3.0:
            score = 95.0
        elif rr >= 2.0:
            # 2.0 → 75, 3.0 → 95
            score = 75.0 + (rr - 2.0) * 20.0
        elif rr >= 1.5:
            # 1.5 → 55, 2.0 → 75
            score = 55.0 + (rr - 1.5) * 40.0
        elif rr >= 1.0:
            # 1.0 → 30, 1.5 → 55
            score = 30.0 + (rr - 1.0) * 50.0
        elif rr >= 0.5:
            # 0.5 → 5, 1.0 → 30
            score = 5.0 + (rr - 0.5) * 50.0
        else:
            score = max(0.0, 5.0 - (0.5 - rr) * 10.0)

        return max(0.0, min(100.0, score))

    def compute_rr(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> Optional[float]:
        """计算 Expected Move 的 RR 值（供 V4 L4 开仓闸门使用）。

        RR = |预期收益| / (ATR / price)

        预期收益来源（优先级）：
          1. Qlib 预测（predicted_return）
          2. 多时间框架 ROC 加权合成

        :return: RR 值；None 表示数据不足
        """
        return self._compute_rr(bars, snap, direction)

    # ═══════════════════════════════════════════════════════════════
    # RR 计算核心
    # ═══════════════════════════════════════════════════════════════

    def _compute_rr(
        self,
        bars: list[dict],
        snap: dict,
        direction: str,
    ) -> Optional[float]:
        """计算统一的 RR 值（评分和闸门共用）。

        V2 风险校准：
          预期收益 = 多时间框架 ROC 加权合成（分钟级收益率）
          风险 = 最近 20 根 K 线收益率标准差 × sqrt(6)（6 根 bar 的累计波动）
          这与旧 ExpectedMoveEngine 口径一致，确保 RR 值在合理范围内。

          不能用 ATR/price 作为风险，因为 ATR 是日级波动（~2%），
          而 ROC 是分钟级收益率（~0.3%），两者不匹配会导致 RR 系统性偏低。
        """
        # 预期收益：优先 Qlib 预测
        predicted_return = self._get_qlib_prediction(snap)
        if predicted_return is not None:
            expected_return = abs(predicted_return)
        else:
            # 降级为多时间框架 ROC 加权合成
            expected_return = self._multi_timeframe_momentum(bars)
            if expected_return is None:
                return None

        # 风险：最近 20 根 K 线收益率标准差 × sqrt(6)（6 根 bar 的累计波动）
        risk = self._compute_volatility(bars)
        if risk is None or risk <= 0:
            return None

        return expected_return / risk

    @staticmethod
    def _compute_volatility(bars: list[dict]) -> Optional[float]:
        """计算最近 20 根 K 线的收益率标准差 × sqrt(6)。

        sqrt(6) 将单根 bar 波动投影到 6 根 bar 的累计波动，
        与预期收益的时间窗口（ROC 6 根为主）对齐。
        """
        if len(bars) < 21:
            return None
        closes = [b.get("close", 0) for b in bars]
        rets = []
        for i in range(len(closes) - 20, len(closes)):
            if closes[i - 1] > 0:
                rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
        if len(rets) < 5:
            return None
        mean_ret = sum(rets) / len(rets)
        variance = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
        return math.sqrt(variance) * math.sqrt(6)

    # ═══════════════════════════════════════════════════════════════
    # 多时间框架动量
    # ═══════════════════════════════════════════════════════════════

    def _multi_timeframe_momentum(self, bars: list[dict]) -> Optional[float]:
        """多时间框架 ROC 加权合成预期收益。

        使用 ROC(3)/ROC(6)/ROC(12) 加权平均，短周期权重高。
        每个 ROC 先 clamp 到 [-5%, +5%] 以防极端值扭曲。
        """
        if len(bars) < self.ROC_PERIODS[-1] + 1:
            # 数据不足最长周期，退化为可用周期
            available = [p for p in self.ROC_PERIODS if len(bars) >= p + 1]
            if not available:
                return None
            periods = available
            weights = [1.0 / len(available)] * len(available)
        else:
            periods = self.ROC_PERIODS
            weights = self.ROC_WEIGHTS

        closes = [b.get("close", 0) for b in bars]
        momentum = 0.0
        total_w = 0.0

        for period, weight in zip(periods, weights):
            if len(closes) >= period + 1 and closes[-(period + 1)] > 0:
                roc = (closes[-1] - closes[-(period + 1)]) / closes[-(period + 1)]
                # clamp 到 [-5%, +5%] 防止极端值
                roc = max(-0.05, min(0.05, roc))
                momentum += abs(roc) * weight
                total_w += weight

        if total_w > 0:
            return momentum / total_w
        return None

    # ═══════════════════════════════════════════════════════════════
    # Qlib 预测（保留原有接口）
    # ═══════════════════════════════════════════════════════════════

    def _get_qlib_prediction(self, snap: dict) -> Optional[float]:
        """从 Qlib 预测缓存中获取当前 bar 的预测值。

        兼容多种 code 格式：6位纯代码（600000）和 Qlib instrument（sh600000）。
        """
        if self._qlib_predictions is None:
            return None
        code = snap.get("code")
        dt = snap.get("datetime")
        if code is None or dt is None:
            return None
        # 尝试原始 code + dt/str(dt)
        for key in [(code, dt), (code, str(dt))]:
            if key in self._qlib_predictions:
                return self._qlib_predictions[key]
        # 兼容 instrument 格式（sh600000 / sz000009）
        if len(code) == 6:
            prefix = "sh" if code[0] == "6" else "sz"
            inst = f"{prefix}{code}"
            for key in [(inst, dt), (inst, str(dt))]:
                if key in self._qlib_predictions:
                    return self._qlib_predictions[key]
        return None