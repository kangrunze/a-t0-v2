"""
G4: Wave Engine — 当日波浪位置评分
==================================
Stage W 重构（2026-07-31）：

核心职责：回答"当前价格处于当日第几波"，而非趋势老化度。
  - 第1波：95分（趋势刚开始，最优买点）
  - 第2波：72分（趋势确认，仍可买）
  - 第3波：38分（趋势成熟，谨慎）
  - 第4+波：0分（趋势末期/追高，不买）

波浪识别算法（基于 swing high/low）：
  1. 提取当日 K线序列
  2. 用 window 窗口找 swing high（局部高点）
  3. 相邻 swing high 之间算一波
  4. 判断当前 bar 落在第几波

趋势老化微调（±10分）：
  保留原 WaveEngine 的 Trend Age / Volume Decay 因子，
  作为波数基础分上的微调，不改变核心波数判定。

用户方案 §六 WaveScore 因子（作为微调项保留）：
  - Trend Age（趋势持续时间）：越长扣分越多
  - ATR Expansion：扩张=趋势加速，收缩=衰竭
  - Volume Decay：缩量=趋势衰竭
"""
from __future__ import annotations

import math
from typing import Optional

from .base import BaseEngine


class WaveEngine(BaseEngine):
    """G4: 当日波浪位置评分。

    核心：波数识别 → 波数→评分映射 → 趋势老化微调。
    """

    # 波数→基础评分映射（用户方案：第1波95/第2波72/第3波38/第4+波0）
    WAVE_SCORE_MAP = {1: 95.0, 2: 72.0, 3: 38.0}
    WAVE_SCORE_BEYOND = 0.0  # 第4波及以上：0分（不买）

    # 波浪识别参数
    SWING_WINDOW = 3  # swing high/low 识别窗口（前后各3根K线）
    MIN_BARS_FOR_WAVE = 10  # 识别波浪的最小K线数

    @property
    def name(self) -> str:
        return "wave"

    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算波浪位置评分 (0~100)。

        :param bars: 完整 K 线序列（含历史，跨日），按时间升序
        :param snap: 当前 bar 的特征快照
        :param direction: "reduce"（卖出腿）或 "add"（买入腿）
        :return: 0~100 的子评分
        """
        if len(bars) < self.MIN_BARS_FOR_WAVE:
            return 50.0  # 数据不足，中性

        # 1. 提取当日 K线
        today_bars = self._extract_today_bars(bars)
        if len(today_bars) < self.MIN_BARS_FOR_WAVE:
            return 50.0  # 当日数据不足，中性

        # 2. 识别当日波浪（swing high 序列）
        waves = self._identify_waves(today_bars)

        # 3. 判断当前 bar 落在第几波
        if not waves:
            # 当日无明确波浪（震荡市），给中性偏高分（鼓励首波尝试）
            return 60.0

        current_idx = len(today_bars) - 1  # 当日最后一根 = 当前 bar
        wave_number = self._locate_wave(current_idx, waves)

        # 4. 波数 → 基础评分
        base_score = self.WAVE_SCORE_MAP.get(
            wave_number, self.WAVE_SCORE_BEYOND)

        # 5. 趋势老化微调（±10分，不改变核心波数判定）
        age_adj = self._compute_age_adjustment(bars, direction)
        vol_adj = self._compute_volume_adjustment(bars)

        final = base_score + age_adj + vol_adj
        return max(0.0, min(100.0, final))

    # ═══════════════════════════════════════════════════════════════
    # 当日 K线提取
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_today_bars(bars: list[dict]) -> list[dict]:
        """从完整 bars 中提取当日 K线。

        bars 的 time 格式: 'YYYY-MM-DD HH:MM:SS'。
        取最后一根 bar 的日期，筛选当日所有 bar。
        """
        if not bars:
            return []
        last_time = bars[-1].get("time", "")
        if len(last_time) < 10:
            return bars[-48:] if len(bars) >= 48 else bars  # 退化为最近48根

        today_str = last_time[:10]  # 'YYYY-MM-DD'
        today_bars = [b for b in bars if b.get("time", "")[:10] == today_str]
        return today_bars if today_bars else [bars[-1]]

    # ═══════════════════════════════════════════════════════════════
    # 波浪识别（swing high 序列）
    # ═══════════════════════════════════════════════════════════════

    def _identify_waves(self, day_bars: list[dict]) -> list[dict]:
        """识别当日波浪（基于 swing high）。

        算法：用 SWING_WINDOW 窗口找局部高点（swing high），
        每个 swing high 标记一波的结束。

        :return: [{"wave_idx": 1, "end_bar": i, "high": price}, ...]
        """
        w = self.SWING_WINDOW
        if len(day_bars) < w * 2 + 1:
            return []

        swing_highs: list[tuple[int, float]] = []
        for i in range(w, len(day_bars) - w):
            is_swing = True
            center_high = day_bars[i].get("high", 0.0)
            if center_high <= 0:
                continue
            for j in range(i - w, i + w + 1):
                if j == i:
                    continue
                if day_bars[j].get("high", 0.0) > center_high:
                    is_swing = False
                    break
            if is_swing:
                swing_highs.append((i, center_high))

        if not swing_highs:
            return []

        # 构建波浪列表
        waves = []
        for k, (idx, high) in enumerate(swing_highs):
            waves.append({
                "wave_idx": k + 1,
                "end_bar": idx,
                "high": high,
            })
        return waves

    @staticmethod
    def _locate_wave(current_idx: int, waves: list[dict]) -> int:
        """判断当前 bar 落在第几波。

        :param current_idx: 当日 K线索引
        :param waves: _identify_waves 的输出
        :return: 波数（1, 2, 3, ...）；如果当前在最后一个 swing high 之后，返回 wave_total+1
        """
        for w in waves:
            if current_idx <= w["end_bar"]:
                return w["wave_idx"]
        # 当前 bar 在最后一个 swing high 之后 → 新一波
        return waves[-1]["wave_idx"] + 1

    # ═══════════════════════════════════════════════════════════════
    # 趋势老化微调（±10分）
    # ═══════════════════════════════════════════════════════════════

    def _compute_age_adjustment(self, bars: list[dict], direction: str) -> float:
        """趋势年龄微调（-5 ~ +5）。

        趋势新鲜（<8根）→ +3~+5
        趋势老化（>20根）→ -3~-5
        """
        trend_age = self._compute_trend_age(bars, direction)
        if trend_age <= 8:
            return 5.0 - trend_age * 0.2  # +3.4 ~ +5.0
        elif trend_age <= 20:
            return 2.0 - (trend_age - 8) * 0.5  # -4.0 ~ +2.0
        else:
            return max(-5.0, -4.0 - (trend_age - 20) * 0.2)

    def _compute_volume_adjustment(self, bars: list[dict]) -> float:
        """量能微调（-5 ~ +5）。

        放量 → +3~+5（趋势确认）
        缩量 → -3~-5（趋势衰竭）
        """
        vol_decay = self._compute_volume_decay(bars)
        if vol_decay > 1.2:
            return 5.0
        elif vol_decay > 1.0:
            return 3.0
        elif vol_decay > 0.7:
            return 0.0
        else:
            return -5.0

    # ═══════════════════════════════════════════════════════════════
    # 辅助计算（保留原 WaveEngine 的趋势老化因子）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _compute_trend_age(bars: list[dict], direction: str) -> int:
        """计算当前趋势持续的 K 线数。"""
        if len(bars) < 2:
            return 0
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
        return age

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
