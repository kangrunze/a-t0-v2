"""
P2: Alpha 连续评分映射函数（主计划 S1.2 / S2.3）
===================================================

主计划 S2.3:
  AlphaScore = Sum(wi * scorei), clip [0, 100]
  权重 wi 等权起步, 写入 thresholds.yaml 做版本管理
  各 score_i 的分段映射折点本身也是参数, 纳入参数平台分析

主计划 S1.2 映射公式:
  score_vwap(vwap_dev): 0ATR->100, -0.3ATR->80, -0.6ATR->60, -1.0ATR->20
  score_rsi(rsi): 30->90, 40->70, 50->50, 60->20, 70->0
  score_kdj: f(K distance to 20/80, J turning, KD angle)
  score_adx: 15->10, 20->30, 25->60, 35->90
  score_volume/moneyflow: vol_ratio, MFI normalized

P2 实现: 根据 P1 IC 报告, 保留 IC 显著的信号, 丢弃不显著的
"""
from __future__ import annotations
from typing import Optional


def score_vwap(vwap_dev: float, atr: float = 0.0) -> float:
    """
    VWAP 偏离评分 (趋势跟随: 偏离越大, 顺势开仓信号越强)

    主计划 S1.2 映射:
      0 ATR -> 0 (无偏离, 无信号)
      0.3 ATR -> 40
      0.6 ATR -> 70
      1.0 ATR -> 90
      1.5+ ATR -> 100 (饱和)

    趋势跟随逻辑: 偏离越大, 趋势越强, 评分越高
    """
    if atr <= 0:
        # 无 ATR 时用绝对偏离度
        abs_dev = abs(vwap_dev)
        if abs_dev < 0.002:
            return 0.0
        elif abs_dev < 0.005:
            return 40.0
        elif abs_dev < 0.008:
            return 70.0
        elif abs_dev < 0.012:
            return 90.0
        else:
            return 100.0

    dev_atr = abs(vwap_dev) / atr
    if dev_atr < 0.1:
        return 0.0
    elif dev_atr < 0.3:
        return 40.0
    elif dev_atr < 0.6:
        return 70.0
    elif dev_atr < 1.0:
        return 90.0
    else:
        return 100.0


def score_rsi(rsi: float, direction: str = "reduce") -> float:
    """
    RSI 评分 (趋势跟随: RSI 同方向确认)

    主计划 S1.2 映射:
      30->90, 40->70, 50->50, 60->20, 70->0

    趋势跟随修正:
      - direction="reduce"(卖出): RSI>60 趋势向上, 评分高
      - direction="add"(买入): RSI<40 趋势向下, 评分高
    """
    if direction == "reduce":
        # 卖出腿: RSI 越高越确认上升趋势
        if rsi >= 70:
            return 90.0
        elif rsi >= 60:
            return 70.0
        elif rsi >= 50:
            return 40.0
        else:
            return 10.0
    else:
        # 买入腿: RSI 越低越确认下降趋势
        if rsi <= 30:
            return 90.0
        elif rsi <= 40:
            return 70.0
        elif rsi <= 50:
            return 40.0
        else:
            return 10.0


def score_kdj(kdj_k: float, kdj_j: Optional[float] = None) -> float:
    """
    KDJ 评分 (趋势跟随: K/D 同方向确认)

    主计划 S1.2: f(K distance to 20/80, J turning, KD angle)

    趋势跟随:
      - K>80: 超买区域, 卖出信号强
      - K<20: 超卖区域, 买入信号强
      - 中间区域: 信号弱
    """
    if kdj_k >= 80:
        return 90.0
    elif kdj_k >= 70:
        return 70.0
    elif kdj_k <= 20:
        return 90.0
    elif kdj_k <= 30:
        return 70.0
    elif 40 <= kdj_k <= 60:
        return 30.0
    else:
        return 50.0


def score_adx(adx: float) -> float:
    """
    ADX 趋势强度评分

    主计划 S1.2 映射:
      15->10, 20->30, 25->60, 35->90

    趋势跟随: ADX 越高趋势越强, 评分越高
    """
    if adx < 15:
        return 10.0
    elif adx < 20:
        return 30.0
    elif adx < 25:
        return 50.0
    elif adx < 30:
        return 70.0
    elif adx < 35:
        return 85.0
    else:
        return 100.0


def score_volume(vol_ratio: float) -> float:
    """
    量比评分 (量能放大确认)

    主计划 S1.2: vol_ratio normalized

    趋势跟随: 量比越大, 量能放大越确认趋势
    """
    if vol_ratio < 0.8:
        return 10.0
    elif vol_ratio < 1.0:
        return 30.0
    elif vol_ratio < 1.5:
        return 60.0
    elif vol_ratio < 2.0:
        return 80.0
    else:
        return 100.0


def compute_alpha_score(snap: dict, params) -> tuple[float, dict]:
    """
    计算连续 Alpha 评分 (V3 重构).

    V3 架构（G1 阶段）：
      委托 score/alpha_score.py 的 AlphaScoreAggregator，
      七维度加权（Trend 25% / Wave 20% / Support 15% / Momentum 15%
                  / Liquidity 10% / ExpectedMove 10% / Risk 5%）。

    旧 P2 实现（5 维度 vwap/rsi/kdj/adx/volume）保留为 score_* 函数，
    但 compute_alpha_score 已委托 V3 七维度聚合器。

    use_continuous_alpha=False 时不调用此函数 (主计划 S5 验收1: flag=false 逐笔一致).
    use_continuous_alpha=True 时替代布尔三层触发 (extreme/confirm/filter).

    :param snap: 特征快照 (含 vwap_dev, rsi, kdj_k, adx, vol_ratio, atr 等)
    :param params: SignalParams (V3 权重从 thresholds.yaml v3_alpha_weights 读取)
    :return: (alpha_score 0-100, sub_scores dict)
    """
    from .score.alpha_score import compute_alpha_score_v3

    # snap 中 _direction 由 evaluate_all_signals 注入（reduce/add）
    direction = snap.get("_direction", "reduce")
    return compute_alpha_score_v3(snap, params, direction=direction)
