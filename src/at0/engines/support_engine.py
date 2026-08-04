"""
G2: Support Engine — 动态支撑/阻力评分
========================================

Stage S 重构（2026-08-03）：

核心改进：用户明确指出"上涨很多股票支撑根本不是VWAP，而是EMA20、
EMA30、Anchored VWAP、Morning Range Mid、Yesterday VWAP"。

支撑位候选（按强度排序）：
  1. Anchored VWAP（从当日最高点锚定）— 强支撑，趋势回踩锚点
  2. EMA20 — 趋势中常用支撑
  3. EMA30 — 趋势中常用支撑
  4. Yesterday VWAP — 前一日成本线
  5. Morning Range Mid — 开盘区间中点
  6. Yesterday Close — 昨收
  7. 日内 VWAP — 弱支撑（降权，上涨股票支撑不是VWAP）

评分逻辑：
  - 遍历所有支撑位，取最高分（价格靠近最强支撑位时得分最高）
  - 距离越近评分越高（距离衰减因子）
  - 方向确认：买入腿价格在支撑上方回踩加分；卖出腿价格在阻力下方冲高加分
  - 历史反弹次数：当天被测试次数越多，支撑越可靠

输出：0~100
  - 90+: 价格在强支撑位附近（Anchored VWAP / EMA20 / EMA30）
  - 70~85: 价格在中等支撑位附近（Yesterday VWAP / OR Mid / Prev Close）
  - 50~65: 价格在弱支撑位附近（日内 VWAP）
  - 30: 远离所有支撑位（>2%）
"""
from __future__ import annotations

from typing import Optional

from .base import BaseEngine


class SupportEngine(BaseEngine):
    """G2: 动态支撑/阻力评分。"""

    # 支撑位基础分（越高表示支撑越强）
    LEVEL_BASE_SCORE = {
        "anchored_vwap": 88.0,   # Anchored VWAP：从当日高点锚定，趋势回踩强支撑
        "ema20": 85.0,           # EMA20：趋势中常用支撑
        "ema30": 82.0,           # EMA30：趋势中常用支撑
        "yesterday_vwap": 78.0,  # 昨日 VWAP：前一日成本线
        "or_mid": 75.0,          # Morning Range Mid：开盘区间中点
        "prev_close": 72.0,      # 昨收：心理支撑位
        "vwap": 60.0,            # 日内 VWAP：降权（用户明确说上涨股票支撑不是 VWAP）
    }

    # 有效距离上限（超过此距离的支撑位不计入）
    MAX_DIST_PCT = 0.02  # 2%

    @property
    def name(self) -> str:
        return "support"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算支撑/阻力评分 (0~100)。

        :param bars: 完整 K 线序列（含历史，跨日），按时间升序
        :param snap: 当前 bar 的特征快照
        :param direction: "reduce"（卖出腿）或 "add"（买入腿）
        :return: 0~100 的子评分
        """
        price = snap.get("current_price")
        if price is None or price <= 0:
            return 50.0

        levels = self._collect_levels(bars, snap)
        if not levels:
            return 50.0

        best_score = 30.0  # 默认低分（远离所有支撑位）

        for level_info in levels:
            level = level_info["price"]
            if level is None or level <= 0:
                continue

            dist_pct = abs(price - level) / price
            if dist_pct > self.MAX_DIST_PCT:
                continue  # 超过 2% 跳过

            # 基础分（由支撑位类型决定）
            base = level_info["base_score"]

            # 距离衰减因子
            if dist_pct < 0.002:
                dist_factor = 1.0       # <0.2%：极近
            elif dist_pct < 0.005:
                dist_factor = 0.9       # <0.5%：近
            elif dist_pct < 0.01:
                dist_factor = 0.75      # <1.0%：中等
            else:
                dist_factor = 0.55      # <2.0%：远

            # 方向确认调整（趋势跟随适配）
            dir_adj = 0.0
            if direction == "add":
                # 买入腿：价格在支撑位上方（回踩买入）→ 加分
                if price > level:
                    dir_adj = 5.0
                else:
                    # 跌破支撑 → 减分（趋势可能破坏）
                    dir_adj = -10.0
            else:  # reduce
                # 卖出腿：价格在阻力位下方（冲高卖出）→ 加分
                if price < level:
                    dir_adj = 5.0
                else:
                    # 突破阻力 → 减分
                    dir_adj = -10.0

            # 历史反弹次数加分（支撑位被测试次数越多越可靠）
            bounce_adj = 0.0
            bounce_count = level_info.get("bounce_count", 0)
            if bounce_count >= 3:
                bounce_adj = 5.0
            elif bounce_count >= 2:
                bounce_adj = 3.0

            level_score = base * dist_factor + dir_adj + bounce_adj
            level_score = max(0.0, min(100.0, level_score))

            if level_score > best_score:
                best_score = level_score

        return best_score

    # ═══════════════════════════════════════════════════════════════
    # 支撑位收集
    # ═══════════════════════════════════════════════════════════════

    def _collect_levels(self, bars: list[dict], snap: dict) -> list[dict]:
        """收集所有候选支撑/阻力位。"""
        levels = []

        # 1. EMA20 (snap 中已有，ema_period=20)
        ema20 = snap.get("ema")
        if ema20 is not None and ema20 > 0:
            levels.append({
                "price": ema20,
                "type": "ema20",
                "base_score": self.LEVEL_BASE_SCORE["ema20"],
                "bounce_count": self._count_bounces(bars, ema20),
            })

        # 2. EMA30 (从 bars 计算，snap 中只有 EMA20)
        ema30 = self._compute_ema(bars, 30)
        if ema30 is not None and ema30 > 0:
            levels.append({
                "price": ema30,
                "type": "ema30",
                "base_score": self.LEVEL_BASE_SCORE["ema30"],
                "bounce_count": self._count_bounces(bars, ema30),
            })

        # 3. Anchored VWAP（从当日最高点锚定）
        today_bars = self._extract_today_bars(bars)
        anchored_vwap = self._compute_anchored_vwap(today_bars)
        if anchored_vwap is not None and anchored_vwap > 0:
            levels.append({
                "price": anchored_vwap,
                "type": "anchored_vwap",
                "base_score": self.LEVEL_BASE_SCORE["anchored_vwap"],
                "bounce_count": self._count_bounces(bars, anchored_vwap),
            })

        # 4. Yesterday VWAP（前一日累计 VWAP）
        yesterday_vwap = self._compute_yesterday_vwap(bars)
        if yesterday_vwap is not None and yesterday_vwap > 0:
            levels.append({
                "price": yesterday_vwap,
                "type": "yesterday_vwap",
                "base_score": self.LEVEL_BASE_SCORE["yesterday_vwap"],
                "bounce_count": 0,
            })

        # 5. Morning Range Mid（开盘区间中点）
        or_low = snap.get("or_low")
        or_high = snap.get("or_high")
        if or_low is not None and or_high is not None:
            or_mid = (or_low + or_high) / 2
            if or_mid > 0:
                levels.append({
                    "price": or_mid,
                    "type": "or_mid",
                    "base_score": self.LEVEL_BASE_SCORE["or_mid"],
                    "bounce_count": 0,
                })

        # 6. Yesterday Close（昨收）
        prev_close = snap.get("prev_close")
        if prev_close is not None and prev_close > 0:
            levels.append({
                "price": prev_close,
                "type": "prev_close",
                "base_score": self.LEVEL_BASE_SCORE["prev_close"],
                "bounce_count": 0,
            })

        # 7. 日内 VWAP（降权，用户明确说上涨股票支撑不是 VWAP）
        vwap = snap.get("vwap")
        if vwap is not None and vwap > 0:
            levels.append({
                "price": vwap,
                "type": "vwap",
                "base_score": self.LEVEL_BASE_SCORE["vwap"],
                "bounce_count": self._count_bounces(bars, vwap),
            })

        return levels

    # ═══════════════════════════════════════════════════════════════
    # EMA 计算（复用 features.ema 避免重复实现）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _compute_ema(bars: list[dict], period: int) -> Optional[float]:
        """计算 EMA(period)，基于收盘价。"""
        try:
            from ..features import ema as ema_func
            return ema_func(bars, period=period)
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════
    # Anchored VWAP（从当日最高点锚定）
    # ═══════════════════════════════════════════════════════════════

    def _compute_anchored_vwap(self, today_bars: list[dict]) -> Optional[float]:
        """计算 Anchored VWAP（从当日最高点锚定）。

        找到当日最高价的 K 线，从该 K 线开始累计 VWAP。
        这代表了"从当日高点回落后的平均成本"，是趋势回踩的强支撑。

        如果当前 bar 就是当日最高点（无回落），返回 None。
        """
        if len(today_bars) < 2:
            return None

        # 找当日最高价的 K 线索引
        anchor_idx = 0
        max_high = 0.0
        for i, b in enumerate(today_bars):
            h = b.get("high", 0)
            if h > max_high:
                max_high = h
                anchor_idx = i

        # 如果锚点就是最后一根 K 线（当前 bar 就是最高点），无有意义回落
        if anchor_idx >= len(today_bars) - 1:
            return None

        # 从锚点开始累计 VWAP
        anchored_bars = today_bars[anchor_idx:]
        if len(anchored_bars) < 2:
            return None

        try:
            from ..features import cumulative_vwap
            return cumulative_vwap(anchored_bars)
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════
    # Yesterday VWAP（前一日累计 VWAP）
    # ═══════════════════════════════════════════════════════════════

    def _compute_yesterday_vwap(self, bars: list[dict]) -> Optional[float]:
        """计算昨日累计 VWAP。"""
        yesterday_bars = self._extract_yesterday_bars(bars)
        if not yesterday_bars:
            return None
        try:
            from ..features import cumulative_vwap
            return cumulative_vwap(yesterday_bars)
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════
    # 当日 / 昨日 K线提取
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_today_bars(bars: list[dict]) -> list[dict]:
        """从完整 bars 中提取当日 K线。

        bars 的 time 格式: 'YYYY-MM-DD HH:MM:SS'。
        """
        if not bars:
            return []
        last_time = bars[-1].get("time", "")
        if len(last_time) < 10:
            return bars[-48:] if len(bars) >= 48 else bars

        today_str = last_time[:10]  # 'YYYY-MM-DD'
        today_bars = [b for b in bars if b.get("time", "")[:10] == today_str]
        return today_bars if today_bars else [bars[-1]]

    @staticmethod
    def _extract_yesterday_bars(bars: list[dict]) -> list[dict]:
        """提取昨日 K线（最后一个非今天的交易日）。"""
        if not bars:
            return []
        last_time = bars[-1].get("time", "")
        if len(last_time) < 10:
            return []

        today_str = last_time[:10]
        # 从后往前找第一个非今天的日期
        yesterday_str: Optional[str] = None
        yesterday_bars: list[dict] = []
        for b in reversed(bars):
            b_date = b.get("time", "")[:10]
            if b_date != today_str:
                if yesterday_str is None:
                    yesterday_str = b_date
                if b_date == yesterday_str:
                    yesterday_bars.append(b)
                else:
                    break
        yesterday_bars.reverse()
        return yesterday_bars

    # ═══════════════════════════════════════════════════════════════
    # 历史反弹次数统计
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _count_bounces(bars: list[dict], level: float, tolerance: float = 0.002) -> int:
        """统计当天价格在该支撑/阻力位附近的反弹次数。

        只看最近 48 根 K 线（约一天 5min 数据）。
        """
        if not bars or level <= 0:
            return 0
        count = 0
        recent = bars[-48:]
        for i in range(1, len(recent) - 1):
            low = recent[i].get("low", 0)
            high = recent[i].get("high", 0)
            next_high = recent[i + 1].get("high", 0)
            # 价格触及该位置（在容差范围内）并反弹
            if abs(low - level) / level < tolerance and next_high > high:
                count += 1
            elif abs(high - level) / level < tolerance and next_high < high:
                count += 1
        return min(count, 5)  # 上限 5
