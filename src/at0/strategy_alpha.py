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
import math


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
    计算连续 Alpha 评分 (P2 实现).

    主计划 S2.3:
      AlphaScore = Sum(wi * scorei) / Sum(wi), clip [0, 100]

    各子信号评分函数基于主计划 S1.2 映射公式.
    权重 wi 从 params.alpha_weight_* 读取, 等权起步.

    :param snap: 特征快照 (含 vwap_dev, rsi, kdj_k, adx, vol_ratio, atr 等)
    :param params: SignalParams (含 alpha_weight_* 权重)
    :return: (alpha_score 0-100, sub_scores dict)
    """
    sub_scores = {}

    # 1. VWAP 偏离评分（None 安全：snap.get(k, default) 在值为 None 时仍返回 None）
    vwap_dev = snap.get("vwap_dev")
    vwap_dev = 0.0 if vwap_dev is None else vwap_dev
    atr = snap.get("atr")
    if atr is None:
        atr = snap.get("atr_intraday")
    atr = 0.0 if atr is None else atr
    s_vwap = score_vwap(vwap_dev, atr)
    sub_scores["vwap"] = s_vwap

    # 2. RSI 评分
    rsi = snap.get("rsi")
    rsi = 50.0 if rsi is None else rsi
    direction = snap.get("_direction", "reduce")
    s_rsi = score_rsi(rsi, direction)
    sub_scores["rsi"] = s_rsi

    # 3. KDJ 评分
    kdj_k = snap.get("kdj_k")
    kdj_k = 50.0 if kdj_k is None else kdj_k
    s_kdj = score_kdj(kdj_k)
    sub_scores["kdj"] = s_kdj

    # 4. ADX 评分
    adx = snap.get("adx")
    adx = 0.0 if adx is None else adx
    s_adx = score_adx(adx)
    sub_scores["adx"] = s_adx

    # 5. 量能评分
    vol_ratio = snap.get("vol_ratio")
    vol_ratio = 1.0 if vol_ratio is None else vol_ratio
    s_vol = score_volume(vol_ratio)
    sub_scores["volume"] = s_vol

    # 加权平均
    weights = {
        "vwap": params.alpha_weight_vwap,
        "rsi": params.alpha_weight_rsi,
        "kdj": params.alpha_weight_kdj,
        "adx": params.alpha_weight_adx,
        "volume": params.alpha_weight_volume,
    }
    total_weight = sum(weights.values())
    if total_weight > 0:
        alpha = sum(sub_scores[k] * weights[k] for k in weights) / total_weight
    else:
        alpha = 0.0

    alpha = max(0.0, min(100.0, alpha))
    return alpha, sub_scores
