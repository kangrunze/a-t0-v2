"""
HoldConfidenceEngine V2 — V4 L6 持仓信心 + L7 趋势失败退出
==========================================================

Stage H 重构（2026-08-03）：

核心改进（解决 100 股验证中 avg_loss 放大问题）：
  1. 加权聚合替代简单平均：EMA 斜率 35% + ADX 25% + EMA20 距离 20% + MACD 12% + 量能 8%
  2. 量价背离检测：价格上涨但成交量萎缩 = 背离信号，降低信心（不仅看量比）
  3. 趋势成熟度：趋势持续时间越长，信心逐渐衰减（非 abrupt 退出）
  4. 多时间框架 MACD：检查 MACD 快速方向 + 方向变化趋势
  5. L7 趋势失败 ≥3 信号触发（原 ≥2），减少误杀

V1 问题回顾（100股×1年验证，2026-07-30）：
  baseline:        avg_loss -120.72
  L4+L6(th70)/L7: avg_loss -138.71 (-14.9%)
  L4+L6(th50)/L7: avg_loss -139.85 (-15.8%)
  根因：L6 信心评分过于敏感，频繁触发退出 → 止损前退出 → avg_loss 膨胀

V2 修复策略：
  - 信心评分更稳定（衰减权重 + 加权聚合，减少单根 K 线噪声）
  - 趋势失败 ≥3 信号才退出（降低误杀率）
  - 量价背离作为第 5 个信号，补充而非替代原有信号
  - 趋势成熟度缓降（不 abrupt 退出）

direction 约定（与 BaseEngine 一致）：
  - "reduce" = 评估 buy 仓（buy 仓用 reduce 信号平仓）
  - "add"    = 评估 sell 仓（sell 仓用 add 信号平仓）
"""
from __future__ import annotations

from typing import Optional

from .base import BaseEngine


class HoldConfidenceEngine(BaseEngine):
    """V4 L6: 持仓信心评分引擎 V2。

    每根 K 线对持仓腿重打分（0~100），信心 < 阈值时退出。
    V2 改进：加权聚合 + 量价背离 + 趋势成熟度，降低误杀率。
    """

    # 五因子权重（V2：加权聚合，非简单平均）
    WEIGHTS = {
        "ema_slope": 0.35,    # EMA 斜率方向（趋势核心）
        "adx": 0.25,          # ADX 趋势强度
        "ema20_dist": 0.20,   # 价格与 EMA20 距离
        "macd": 0.12,         # MACD 方向
        "volume": 0.08,       # 量能（含量价背离）
    }

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

        V2 五因子加权聚合：
          1. EMA 斜率方向 (35%): 趋势方向与持仓一致→高信心，斜率放大
          2. ADX 趋势强度 (25%): ADX≥35→90, ≥25→75, <20→30
          3. 价格与 EMA20 关系 (20%): 价格在 EMA20 有利侧→高信心
          4. MACD 方向 (12%): MACD hist 方向一致 + 方向变化趋势
          5. 量能 + 量价背离 (8%): 放量+无背离→高信心，缩量+背离→低信心
        """
        price = snap.get("current_price")
        ema_val = snap.get("ema")
        adx = snap.get("adx")
        ema_slp = snap.get("ema_slope")
        macd_hist = snap.get("macd_hist")
        vol_ratio = snap.get("volume_ratio")
        recent_5_vol = snap.get("recent_5_vol")
        prior_20_vol_avg = snap.get("prior_20_vol_avg")

        if price is None or ema_val is None:
            return 50.0

        is_buy = direction == "reduce"
        total_score = 0.0
        total_weight = 0.0

        # 1. EMA 斜率方向（权重 35%）
        if ema_slp is not None:
            # 斜率放大：0.001(0.1%) → +5分偏移，0.005(0.5%) → +25分偏移
            raw_slope = ema_slp * 5000.0  # 放大 5000 倍
            raw_slope = max(-50.0, min(50.0, raw_slope))
            if is_buy:
                s = 50.0 + raw_slope
            else:
                s = 50.0 - raw_slope
            total_score += s * self.WEIGHTS["ema_slope"]
            total_weight += self.WEIGHTS["ema_slope"]

        # 2. ADX 趋势强度（权重 25%）
        if adx is not None:
            if adx >= 35.0:
                s = 90.0
            elif adx >= 25.0:
                s = 75.0
            elif adx >= 20.0:
                s = 55.0
            elif adx >= 15.0:
                s = 40.0
            else:
                s = 25.0  # 趋势消失
            total_score += s * self.WEIGHTS["adx"]
            total_weight += self.WEIGHTS["adx"]

        # 3. 价格与 EMA20 关系（权重 20%）
        if price > 0 and ema_val > 0:
            dev = (price - ema_val) / ema_val
            clamp_dev = max(-50.0, min(50.0, dev * 1000.0))
            if is_buy:
                s = 50.0 + clamp_dev
            else:
                s = 50.0 - clamp_dev
            total_score += s * self.WEIGHTS["ema20_dist"]
            total_weight += self.WEIGHTS["ema20_dist"]

        # 4. MACD 方向（权重 12%）
        if macd_hist is not None:
            if is_buy:
                s = 80.0 if macd_hist > 0 else 35.0
            else:
                s = 80.0 if macd_hist < 0 else 35.0
            total_score += s * self.WEIGHTS["macd"]
            total_weight += self.WEIGHTS["macd"]

        # 5. 量能 + 量价背离（权重 8%）
        vol_score = 50.0
        if vol_ratio is not None:
            if vol_ratio >= 1.5:
                vol_score = 85.0  # 显著放量，趋势确认
            elif vol_ratio >= 1.0:
                vol_score = 70.0  # 正常放量
            elif vol_ratio >= 0.7:
                vol_score = 55.0  # 轻度缩量
            else:
                vol_score = 35.0  # 明显缩量

            # 量价背离检测：价格有利方向但成交量萎缩
            if recent_5_vol is not None and prior_20_vol_avg is not None and prior_20_vol_avg > 0:
                vol_ratio_recent = recent_5_vol / prior_20_vol_avg
                # 价格有利方向（对持仓方向有利）但缩量 → 背离
                price_ok = (is_buy and price > ema_val) or (not is_buy and price < ema_val)
                if price_ok and vol_ratio_recent < 0.8:
                    # 背离：价格上行但量能萎缩 → 扣分
                    vol_score -= 15.0
                elif not price_ok and vol_ratio_recent > 1.2:
                    # 反背离：价格不利但放量 → 扣分（趋势可能反转）
                    vol_score -= 10.0

        total_score += vol_score * self.WEIGHTS["volume"]
        total_weight += self.WEIGHTS["volume"]

        # 趋势成熟度微调（-5 ~ 0）：趋势过老时逐渐降低信心
        age_adj = self._compute_age_adjustment(bars, direction)
        total_score += age_adj

        if total_weight <= 0:
            return 50.0
        return max(0.0, min(100.0, total_score / total_weight))

    # ═══════════════════════════════════════════════════════════════
    # 趋势成熟度衰减
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _compute_age_adjustment(bars: list[dict], direction: str) -> float:
        """趋势年龄微调（-5 ~ 0）。

        趋势新鲜（<8根）→ 0（不衰减）
        趋势老化（>20根）→ -5（逐渐衰减，缓慢退出）
        """
        if len(bars) < 2:
            return 0.0
        age = 0
        if direction == "reduce":
            for i in range(len(bars) - 1, 0, -1):
                if bars[i].get("high", 0) >= bars[i - 1].get("high", 0):
                    age += 1
                else:
                    break
        else:
            for i in range(len(bars) - 1, 0, -1):
                if bars[i].get("low", 0) <= bars[i - 1].get("low", 0):
                    age += 1
                else:
                    break
        if age <= 8:
            return 0.0
        elif age <= 20:
            return -(age - 8) * 0.3  # 8→0, 20→-3.6
        else:
            return -5.0  # 封顶 -5


def check_trend_failure_v2(
    snap: dict,
    direction: str = "reduce",
    adx_weak_threshold: float = 15.0,
) -> tuple[bool, str]:
    """V4 L7 V2: 趋势硬失败检查。

    V2 改进：5 项信号中 ≥3 项触发才判定失败（原 ≥2），减少误杀。
    新增第 5 项信号：量价背离。

    五项信号：
      1. EMA20 破位: 价格穿越 EMA20 到不利侧
      2. ADX 衰减: ADX < adx_weak_threshold（V2 默认 15，原 20）
      3. MACD 反转: MACD hist 与持仓方向相反
      4. 量能枯竭: volume_ratio < 0.5
      5. 量价背离: 价格有利方向但成交量萎缩

    V2 默认 adx_weak_threshold=15.0（原 20.0），进一步降低误杀。

    :return: (是否失败, 原因字符串)
    """
    price = snap.get("current_price")
    ema_val = snap.get("ema")
    adx = snap.get("adx")
    macd_hist = snap.get("macd_hist")
    vol_ratio = snap.get("volume_ratio")
    recent_5_vol = snap.get("recent_5_vol")
    prior_20_vol_avg = snap.get("prior_20_vol_avg")

    is_buy = direction == "reduce"
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

    # 2. ADX 衰减（V2 默认 15，原 20，减少误杀）
    if adx is not None and adx < adx_weak_threshold:
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

    # 4. 量能枯竭（V2 更严格：<0.5 才算枯竭，原 <0.6）
    if vol_ratio is not None and vol_ratio < 0.5:
        failure_count += 1
        reasons.append("volume_dry")

    # 5. 量价背离（新增 V2 信号）
    if (price is not None and ema_val is not None and ema_val > 0
            and recent_5_vol is not None and prior_20_vol_avg is not None
            and prior_20_vol_avg > 0):
        vol_ratio_recent = recent_5_vol / prior_20_vol_avg
        price_ok = (is_buy and price > ema_val) or (not is_buy and price < ema_val)
        if price_ok and vol_ratio_recent < 0.7:
            # 价格有利方向但量能显著萎缩 → 背离
            failure_count += 1
            reasons.append("vol_divergence")

    # V2: ≥3 信号才判定失败（原 ≥2，减少误杀）
    if failure_count >= 3:
        return True, "+".join(reasons)
    return False, ""


def count_trend_failure_signals_v2(
    snap: dict,
    direction: str = "reduce",
    adx_weak_threshold: float = 15.0,
) -> int:
    """V4 L7 V2: 计算趋势失败信号数（0~5），供 Trend-Adaptive Trailing 使用。

    V2 改进：新增量价背离信号，信号数 0~5。
    默认 adx_weak_threshold=15.0（原 20.0），减少误杀。
    """
    price = snap.get("current_price")
    ema_val = snap.get("ema")
    adx = snap.get("adx")
    macd_hist = snap.get("macd_hist")
    vol_ratio = snap.get("volume_ratio")
    recent_5_vol = snap.get("recent_5_vol")
    prior_20_vol_avg = snap.get("prior_20_vol_avg")

    is_buy = direction == "reduce"
    failure_count = 0

    # 1. EMA20 破位
    if price is not None and ema_val is not None and ema_val > 0:
        if is_buy and price < ema_val:
            failure_count += 1
        elif not is_buy and price > ema_val:
            failure_count += 1

    # 2. ADX 衰减
    if adx is not None and adx < adx_weak_threshold:
        failure_count += 1

    # 3. MACD 反转
    if macd_hist is not None:
        if is_buy and macd_hist < 0:
            failure_count += 1
        elif not is_buy and macd_hist > 0:
            failure_count += 1

    # 4. 量能枯竭
    if vol_ratio is not None and vol_ratio < 0.5:
        failure_count += 1

    # 5. 量价背离
    if (price is not None and ema_val is not None and ema_val > 0
            and recent_5_vol is not None and prior_20_vol_avg is not None
            and prior_20_vol_avg > 0):
        vol_ratio_recent = recent_5_vol / prior_20_vol_avg
        price_ok = (is_buy and price > ema_val) or (not is_buy and price < ema_val)
        if price_ok and vol_ratio_recent < 0.7:
            failure_count += 1

    return failure_count


# ── 向后兼容别名 ──
check_trend_failure = check_trend_failure_v2
count_trend_failure_signals = count_trend_failure_signals_v2