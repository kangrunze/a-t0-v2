"""
FeatureVector — V3 平台化特征向量容器
========================================

职责：
  承载各 Engine 产出的连续因子，作为 Alpha 评分的统一输入。

设计原则：
  1. frozen=True，Engine 间不共享可变状态
  2. 字段全部 Optional，各 Engine 只填充自己负责的维度
  3. from_snapshot() 从现有 features.py 的 snap dict 构造（向后兼容）

V3 七维度对应字段（G1 阶段先用现有 features 填充，G2-G7 逐步替换为独立 Engine）：
  - Trend:       adx, pdi, mdi, macd_hist, ema, ma5, ma20
  - Wave:        roc, bias, cci（G4 Wave Engine 实现后替换）
  - Support:     vwap, vwap_dev, bb_lower, bb_upper, or_low（G2 Support Engine 实现后替换）
  - Momentum:    rsi, kdj_k, kdj_d, kdj_j, mfi
  - Liquidity:   vol_ratio, obv, recent_5_vol
  - ExpectedMove: 无（G5 Expected Move Engine 实现后填充）
  - Risk:        atr, atr_relative（G6 Execution Engine 实现后填充）

Time: minute_of_day（日内时间因子）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FeatureVector:
    """V3 平台化特征向量（只读快照）。

    所有字段 Optional，缺失时由 Alpha 评分层做 None 安全处理。
    from_snapshot() 从 features.py 的 snap dict 构造，保持向后兼容。
    """

    # ── 原始价格/上下文 ──
    current_price: Optional[float] = None
    prev_close: Optional[float] = None

    # ── Trend 维度（G7 Regime Engine + 现有 features）──
    adx: Optional[float] = None
    pdi: Optional[float] = None       # DI+
    mdi: Optional[float] = None       # DI-
    macd_hist: Optional[float] = None
    ema: Optional[float] = None
    ma5: Optional[float] = None
    ma20: Optional[float] = None

    # ── Wave 维度（G4 Wave Engine 实现后填充；当前用动量代理）──
    roc: Optional[float] = None
    bias: Optional[float] = None
    cci: Optional[float] = None

    # ── Support 维度（G2 Support Engine 实现后填充；当前用 VWAP/BB）──
    vwap: Optional[float] = None
    vwap_dev: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_mid: Optional[float] = None
    or_low: Optional[float] = None
    or_high: Optional[float] = None

    # ── Momentum 维度 ──
    rsi: Optional[float] = None
    kdj_k: Optional[float] = None
    kdj_d: Optional[float] = None
    kdj_j: Optional[float] = None
    mfi: Optional[float] = None

    # ── Liquidity 维度 ──
    vol_ratio: Optional[float] = None
    obv: Optional[float] = None
    recent_5_vol: Optional[float] = None
    prior_20_vol_avg: Optional[float] = None

    # ── ExpectedMove 维度（G5 实现后填充）──
    expected_return: Optional[float] = None
    expected_range: Optional[float] = None

    # ── Risk 维度（G6 实现后填充）──
    atr: Optional[float] = None
    atr_relative: Optional[float] = None

    # ── Time 维度 ──
    minute_of_day: Optional[int] = None
    bars_count: int = 0

    # ── 方向标记（Alpha 评分需区分 reduce/add）──
    direction: str = "reduce"

    @classmethod
    def from_snapshot(cls, snap: dict, direction: str = "reduce") -> "FeatureVector":
        """从 features.py 的 snap dict 构造 FeatureVector。

        向后兼容：snap 中缺失的字段保持 None。
        direction: "reduce"（卖出腿）或 "add"（买入腿），Alpha 评分需区分。
        """
        return cls(
            current_price=snap.get("current_price"),
            prev_close=snap.get("prev_close"),
            adx=snap.get("adx"),
            pdi=snap.get("pdi"),
            mdi=snap.get("mdi"),
            macd_hist=snap.get("macd_hist"),
            ema=snap.get("ema"),
            ma5=snap.get("ma5"),
            ma20=snap.get("ma20"),
            roc=snap.get("roc"),
            bias=snap.get("bias"),
            cci=snap.get("cci"),
            vwap=snap.get("vwap"),
            vwap_dev=snap.get("vwap_dev"),
            bb_lower=snap.get("bb_lower"),
            bb_upper=snap.get("bb_upper"),
            bb_mid=snap.get("bb_mid"),
            or_low=snap.get("or_low"),
            or_high=snap.get("or_high"),
            rsi=snap.get("rsi"),
            kdj_k=snap.get("kdj_k"),
            kdj_d=snap.get("kdj_d"),
            kdj_j=snap.get("kdj_j"),
            mfi=snap.get("mfi"),
            vol_ratio=snap.get("volume_ratio"),  # snap 中叫 volume_ratio
            obv=snap.get("obv"),
            recent_5_vol=snap.get("recent_5_vol"),
            prior_20_vol_avg=snap.get("prior_20_vol_avg"),
            atr=snap.get("atr"),
            atr_relative=snap.get("atr_relative"),
            minute_of_day=snap.get("minute_of_day"),
            bars_count=snap.get("bars_count", 0),
            direction=direction,
        )
