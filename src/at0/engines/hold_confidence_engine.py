"""
HoldConfidenceEngine — V4 L6 持仓信心 + L7 趋势失败退出
=====================================================
用户方案：Alpha 够用，瓶颈在 Exit。L6/L7 替代 Trailing 切香肠退出：
  - L6 HoldConfidence: 每根K线重打分，信心衰减到阈值以下退出
    锚定趋势状态（ADX/EMA斜率/MACD/量能），NOT 盈亏回撤
  - L7 TrendFailure: 趋势硬失败（EMA20破位+ADX衰减+MACD反转+量能枯竭，2+信号即退出）

核心机制：
  - 趋势健康（信心高）→ 持有，忽略盘中利润回撤（Trailing 会切香肠，L6 不会）
  - 趋势衰减（信心低）→ 退出，不等 Trailing 触发
  - 趋势硬失败（L7）→ 立即退出，即使 +0.3% 也走

direction 约定（与 BaseEngine 一致）：
  - "reduce" = 评估 buy 仓（buy 仓用 reduce 信号平仓）
  - "add"    = 评估 sell 仓（sell 仓用 add 信号平仓）
"""
from __future__ import annotations

from typing import Optional

from .base import BaseEngine


class HoldConfidenceEngine(BaseEngine):
    """V4 L6: 持仓信心评分引擎。

    每根 K 线对持仓腿重打分（0~100），信心 < 阈值时退出。
    评分锚定趋势状态而非盈亏，避免 Trailing 切香肠。
    """

    @property
    def name(self) -> str:
        return "hold_confidence"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算持仓信心 0~100。

        五因子加权：
          1. EMA 斜率方向 (30%): 趋势方向与持仓一致→高信心
          2. ADX 趋势强度 (25%): ADX≥35→90, ≥25→75, <20→25
          3. 价格与 EMA20 关系 (20%): 价格在 EMA20 有利侧→高信心
          4. MACD 方向 (15%): MACD hist 与持仓方向一致→高信心
          5. 量能 (10%): 放量→趋势确认, 缩量→衰竭
        """
        price = snap.get("current_price")
        ema_val = snap.get("ema")
        adx = snap.get("adx")
        ema_slp = snap.get("ema_slope")
        macd_hist = snap.get("macd_hist")
        vol_ratio = snap.get("volume_ratio")

        # 数据不足返回中性，不误杀
        if price is None or ema_val is None:
            return 50.0

        is_buy = direction == "reduce"  # buy 仓
        scores: list[float] = []

        # 1. EMA 斜率方向（权重 30%）—— 趋势核心信号
        if ema_slp is not None:
            # 斜率放大：0.001(0.1%) → 5分偏移，0.005(0.5%) → 25分偏移
            if is_buy:
                s = 50.0 + max(-50.0, min(50.0, ema_slp * 5000.0))
            else:
                s = 50.0 - max(-50.0, min(50.0, ema_slp * 5000.0))
            scores.append(s)

        # 2. ADX 趋势强度（权重 25%）
        if adx is not None:
            if adx >= 35.0:
                s = 90.0
            elif adx >= 25.0:
                s = 75.0
            elif adx >= 20.0:
                s = 50.0
            else:
                s = 25.0  # 趋势消失
            scores.append(s)

        # 3. 价格与 EMA20 关系（权重 20%）
        if price > 0 and ema_val > 0:
            dev = (price - ema_val) / ema_val
            if is_buy:
                s = 50.0 + max(-50.0, min(50.0, dev * 1000.0))
            else:
                s = 50.0 - max(-50.0, min(50.0, dev * 1000.0))
            scores.append(s)

        # 4. MACD 方向（权重 15%）
        if macd_hist is not None:
            if is_buy:
                s = 80.0 if macd_hist > 0 else 30.0
            else:
                s = 80.0 if macd_hist < 0 else 30.0
            scores.append(s)

        # 5. 量能（权重 10%）
        if vol_ratio is not None:
            if vol_ratio >= 1.0:
                s = 80.0  # 放量，趋势确认
            elif vol_ratio >= 0.7:
                s = 60.0
            else:
                s = 30.0  # 缩量，趋势可能衰竭
            scores.append(s)

        if not scores:
            return 50.0
        return sum(scores) / len(scores)


def check_trend_failure(
    snap: dict,
    direction: str = "reduce",
) -> tuple[bool, str]:
    """V4 L7: 趋势硬失败检查。

    四项信号中 2+ 项触发即判定趋势失败（避免单信号误杀）：
      1. EMA20 破位: 价格穿越 EMA20 到不利侧
      2. ADX 衰减: ADX < 25（趋势消失）
      3. MACD 反转: MACD hist 与持仓方向相反
      4. 量能枯竭: volume_ratio < 0.6

    :return: (是否失败, 原因字符串)
    """
    price = snap.get("current_price")
    ema_val = snap.get("ema")
    adx = snap.get("adx")
    macd_hist = snap.get("macd_hist")
    vol_ratio = snap.get("volume_ratio")

    is_buy = direction == "reduce"  # buy 仓
    failure_count = 0
    reasons: list[str] = []

    # 1. EMA20 破位
    if price is not None and ema_val is not None and ema_val > 0:
        if is_buy and price < ema_val:
            failure_count += 1
            reasons.append("ema20_break")
        elif not is_buy and price > ema_val:
            failure_count += 1
            reasons.append("ema20_break")

    # 2. ADX 衰减
    if adx is not None and adx < 25.0:
        failure_count += 1
        reasons.append("adx_weak")

    # 3. MACD 反转
    if macd_hist is not None:
        if is_buy and macd_hist < 0:
            failure_count += 1
            reasons.append("macd_reverse")
        elif not is_buy and macd_hist > 0:
            failure_count += 1
            reasons.append("macd_reverse")

    # 4. 量能枯竭
    if vol_ratio is not None and vol_ratio < 0.6:
        failure_count += 1
        reasons.append("volume_dry")

    # 2+ 信号才判定失败（避免单信号误杀）
    if failure_count >= 2:
        return True, "+".join(reasons)
    return False, ""
