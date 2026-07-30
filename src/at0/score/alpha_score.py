"""
AlphaScoreAggregator — V3 Alpha 连续评分聚合器
================================================

职责：
  将 FeatureVector 的七维度因子映射为 0~100 的子评分，加权聚合为 alpha_score。

设计原则：
  1. 各维度评分函数独立，可单独替换（G2-G7 逐步替换为独立 Engine）
  2. 权重从 thresholds.yaml 读取，支持版本管理和 A/B 测试
  3. None 安全：缺失因子不影响其他维度评分
  4. 向后兼容：use_continuous_alpha=False 时完全不调用本模块

G1 占位实现：
  - Trend:        ADX + MACD histogram + EMA 斜率
  - Wave:         ROC + CCI（G4 Wave Engine 实现后替换为 Trend Age / HH Count 等）
  - Support:      VWAP 偏离 + BB 位置（G2 Support Engine 实现后替换为动态支撑）
  - Momentum:     RSI + KDJ + MFI
  - Liquidity:    量比 + OBV 斜率
  - ExpectedMove: 无（G5 实现后填充，当前权重转移给 Trend）
  - Risk:         ATR 相对值（G6 实现后填充，当前权重转移给 Trend）

趋势跟随逻辑（与现有 strategy_alpha.py 一致）：
  - direction="reduce"(卖出): 偏离越大、RSI 越高、KDJ 越高，评分越高
  - direction="add"(买入): 偏离越大(负向)、RSI 越低、KDJ 越低，评分越高
"""
from __future__ import annotations

from typing import Optional

from ..models.feature_vector import FeatureVector
from ..models.trade_candidate import TradeCandidate


# ═══════════════════════════════════════════════════════════════
# 默认权重（用户方案 §十二）
# ═══════════════════════════════════════════════════════════════
DEFAULT_WEIGHTS = {
    "trend": 0.25,
    "wave": 0.20,
    "support": 0.15,
    "momentum": 0.15,
    "liquidity": 0.10,
    "expected_move": 0.10,
    "risk": 0.05,
}


# ═══════════════════════════════════════════════════════════════
# Trend 维度评分（G7 Regime Engine 实现后替换）
# ═══════════════════════════════════════════════════════════════
def score_trend(fv: FeatureVector) -> float:
    """趋势质量评分（0~100）。

    G1 占位：ADX 趋势强度 + MACD histogram 方向 + EMA 位置
    - ADX > 35: 趋势极强 (90~100)
    - ADX 25~35: 趋势明确 (60~85)
    - ADX < 20: 无趋势 (10~30)
    """
    score = 50.0  # 基准

    # ADX 趋势强度
    if fv.adx is not None:
        if fv.adx >= 35:
            score += 30
        elif fv.adx >= 25:
            score += 15
        elif fv.adx < 20:
            score -= 20

    # MACD histogram 方向确认
    if fv.macd_hist is not None:
        if fv.direction == "reduce" and fv.macd_hist > 0:
            score += 10  # 卖出腿：MACD 正值确认上升趋势
        elif fv.direction == "add" and fv.macd_hist < 0:
            score += 10  # 买入腿：MACD 负值确认下降趋势
        else:
            score -= 5

    # EMA 位置确认
    if fv.ema is not None and fv.current_price is not None:
        if fv.direction == "reduce" and fv.current_price > fv.ema:
            score += 5  # 卖出腿：价格在 EMA 上方
        elif fv.direction == "add" and fv.current_price < fv.ema:
            score += 5  # 买入腿：价格在 EMA 下方

    return max(0.0, min(100.0, score))


# ═══════════════════════════════════════════════════════════════
# Wave 维度评分（G4 Wave Engine 实现后替换）
# ═══════════════════════════════════════════════════════════════
def score_wave(fv: FeatureVector) -> float:
    """波浪位置评分（0~100）。

    G1 占位：ROC 动量 + CCI 超买超卖
    G4 实现后替换为：Trend Age / HH Count / HL Count / ATR Expansion / Momentum Decay
    """
    score = 50.0

    # ROC 动量
    if fv.roc is not None:
        if fv.direction == "reduce" and fv.roc > 0:
            score += 15  # 卖出腿：正动量确认上升
        elif fv.direction == "add" and fv.roc < 0:
            score += 15  # 买入腿：负动量确认下降
        else:
            score -= 10

    # CCI 超买超卖
    if fv.cci is not None:
        if fv.direction == "reduce" and fv.cci > 100:
            score += 15  # 超买区，卖出信号
        elif fv.direction == "add" and fv.cci < -100:
            score += 15  # 超卖区，买入信号
        elif -100 <= fv.cci <= 100:
            score -= 5   # 中间区域，信号弱

    return max(0.0, min(100.0, score))


# ═══════════════════════════════════════════════════════════════
# Support 维度评分（G2 Support Engine 实现后替换）
# ═══════════════════════════════════════════════════════════════
def score_support(fv: FeatureVector) -> float:
    """支撑/阻力强度评分（0~100）。

    G1 占位：VWAP 偏离度 + BB 位置
    G2 实现后替换为：动态支撑（VWAP/EMA20/EMA60/Yesterday Close）+ Bounce Success
    """
    score = 50.0

    # VWAP 偏离度（趋势跟随：偏离越大，顺势信号越强）
    if fv.vwap_dev is not None:
        abs_dev = abs(fv.vwap_dev)
        if fv.atr is not None and fv.atr > 0:
            dev_atr = abs_dev / fv.atr
            if dev_atr >= 1.0:
                score += 30
            elif dev_atr >= 0.6:
                score += 20
            elif dev_atr >= 0.3:
                score += 10
            else:
                score -= 10  # 偏离太小，无信号
        else:
            if abs_dev >= 0.012:
                score += 30
            elif abs_dev >= 0.008:
                score += 20
            elif abs_dev >= 0.005:
                score += 10
            else:
                score -= 10

    # BB 位置确认
    if fv.bb_upper is not None and fv.bb_lower is not None and fv.current_price is not None:
        bb_range = fv.bb_upper - fv.bb_lower
        if bb_range > 0:
            pos = (fv.current_price - fv.bb_lower) / bb_range
            if fv.direction == "reduce" and pos > 0.8:
                score += 10  # 卖出腿：触及上轨
            elif fv.direction == "add" and pos < 0.2:
                score += 10  # 买入腿：触及下轨

    return max(0.0, min(100.0, score))


# ═══════════════════════════════════════════════════════════════
# Momentum 维度评分
# ═══════════════════════════════════════════════════════════════
def score_momentum(fv: FeatureVector) -> float:
    """动量评分（0~100）。

    RSI + KDJ + MFI 综合
    """
    score = 50.0

    # RSI
    if fv.rsi is not None:
        if fv.direction == "reduce":
            if fv.rsi >= 70:
                score += 20
            elif fv.rsi >= 60:
                score += 10
            elif fv.rsi < 50:
                score -= 15
        else:  # add
            if fv.rsi <= 30:
                score += 20
            elif fv.rsi <= 40:
                score += 10
            elif fv.rsi > 50:
                score -= 15

    # KDJ
    if fv.kdj_k is not None:
        if fv.direction == "reduce":
            if fv.kdj_k >= 80:
                score += 15
            elif fv.kdj_k >= 70:
                score += 8
            elif fv.kdj_k < 40:
                score -= 10
        else:  # add
            if fv.kdj_k <= 20:
                score += 15
            elif fv.kdj_k <= 30:
                score += 8
            elif fv.kdj_k > 60:
                score -= 10

    # MFI 资金超买超卖
    if fv.mfi is not None:
        if fv.direction == "reduce" and fv.mfi > 80:
            score += 10
        elif fv.direction == "add" and fv.mfi < 20:
            score += 10

    return max(0.0, min(100.0, score))


# ═══════════════════════════════════════════════════════════════
# Liquidity 维度评分
# ═══════════════════════════════════════════════════════════════
def score_liquidity(fv: FeatureVector) -> float:
    """流动性评分（0~100）。

    量比 + OBV
    趋势跟随：量比越大，量能放大越确认趋势
    """
    score = 50.0

    if fv.vol_ratio is not None:
        if fv.vol_ratio >= 2.0:
            score += 30
        elif fv.vol_ratio >= 1.5:
            score += 20
        elif fv.vol_ratio >= 1.0:
            score += 5
        else:
            score -= 15  # 缩量，信号弱

    return max(0.0, min(100.0, score))


# ═══════════════════════════════════════════════════════════════
# ExpectedMove 维度评分（G5 实现后填充，当前返回中性 50）
# ═══════════════════════════════════════════════════════════════
def score_expected_move(fv: FeatureVector) -> float:
    """预期移动评分（0~100）。

    G5 Expected Move Engine 实现后填充。
    当前占位：返回中性 50（权重转移给 Trend）。
    """
    if fv.expected_return is not None:
        # G5 实现后：根据 RR 比率评分
        return 50.0
    return 50.0


# ═══════════════════════════════════════════════════════════════
# Risk 维度评分（G6 实现后填充，当前返回中性 50）
# ═══════════════════════════════════════════════════════════════
def score_risk(fv: FeatureVector) -> float:
    """风险评分（0~100）。

    G6 Execution Engine 实现后填充。
    当前占位：返回中性 50（权重转移给 Trend）。
    """
    if fv.atr is not None and fv.current_price is not None and fv.current_price > 0:
        # 简单风险代理：ATR 占价格比例，越大风险越高（评分越低）
        atr_pct = fv.atr / fv.current_price
        if atr_pct < 0.005:
            return 70.0  # 低波动，风险可控
        elif atr_pct < 0.01:
            return 50.0
        else:
            return 30.0  # 高波动，风险较高
    return 50.0


# ═══════════════════════════════════════════════════════════════
# AlphaScoreAggregator — 七维度加权聚合
# ═══════════════════════════════════════════════════════════════
class AlphaScoreAggregator:
    """Alpha 连续评分聚合器。

    将 FeatureVector 的七维度因子映射为子评分，加权聚合为 alpha_score。

    用法:
        aggregator = AlphaScoreAggregator(weights)
        candidate = aggregator.aggregate(feature_vector)
        print(candidate.alpha_score)
    """

    def __init__(self, weights: Optional[dict] = None):
        """初始化。

        weights: 七维度权重 dict，None 时用 DEFAULT_WEIGHTS。
                 权重从 thresholds.yaml 读取（config 层负责加载）。
        """
        self.weights = weights if weights is not None else DEFAULT_WEIGHTS.copy()

    def aggregate(self, fv: FeatureVector) -> TradeCandidate:
        """聚合七维度子评分，输出 TradeCandidate。"""
        trend_s = score_trend(fv)
        wave_s = score_wave(fv)
        support_s = score_support(fv)
        momentum_s = score_momentum(fv)
        liquidity_s = score_liquidity(fv)
        expected_move_s = score_expected_move(fv)
        risk_s = score_risk(fv)

        sub_scores = {
            "trend": trend_s,
            "wave": wave_s,
            "support": support_s,
            "momentum": momentum_s,
            "liquidity": liquidity_s,
            "expected_move": expected_move_s,
            "risk": risk_s,
        }

        # 加权平均
        total_weight = sum(self.weights.values())
        if total_weight > 0:
            alpha = sum(sub_scores[k] * self.weights.get(k, 0) for k in sub_scores) / total_weight
        else:
            alpha = 0.0

        alpha = max(0.0, min(100.0, alpha))

        return TradeCandidate(
            trend_score=trend_s,
            wave_score=wave_s,
            support_score=support_s,
            momentum_score=momentum_s,
            liquidity_score=liquidity_s,
            expected_move_score=expected_move_s,
            risk_score=risk_s,
            alpha_score=alpha,
            direction=fv.direction,
            sub_scores_detail=sub_scores,
        )


# ═══════════════════════════════════════════════════════════════
# 便捷函数：从 snap dict 直接计算 alpha_score（向后兼容）
# ═══════════════════════════════════════════════════════════════
# 性能优化（2026-07-30）：全局缓存 yaml 权重，避免每根 bar 重复解析 yaml。
# cProfile 显示 95s 中 92.8s 花在 yaml.safe_load 上（2736 次调用）。
# 缓存后只需 1 次 yaml 解析，预计提速 40x+。
_cached_weights = None
_cached_weights_mtime = None


def _load_v3_weights_from_yaml() -> dict:
    """从 thresholds.yaml 读取 v3_alpha_weights，带文件 mtime 缓存。

    缓存策略：首次调用解析 yaml 并缓存结果 + 文件 mtime。
    后续调用检查 mtime，未变则直接返回缓存。
    """
    global _cached_weights, _cached_weights_mtime
    try:
        from ..paths import PROJECT_ROOT
        yaml_path = PROJECT_ROOT / "config" / "thresholds.yaml"
        mtime = yaml_path.stat().st_mtime
        if _cached_weights is not None and _cached_weights_mtime == mtime:
            return _cached_weights

        from ..config import _load_yaml
        data = _load_yaml()
        if data and "signal" in data:
            sig = data["signal"]
            w = sig.get("v3_alpha_weights")
            if isinstance(w, dict) and w:
                merged = DEFAULT_WEIGHTS.copy()
                merged.update(w)
                _cached_weights = merged
                _cached_weights_mtime = mtime
                return merged
    except Exception:
        pass
    _cached_weights = DEFAULT_WEIGHTS.copy()
    return _cached_weights


# ═══════════════════════════════════════════════════════════════
# V3 Engine 实例（延迟初始化，避免循环导入）
# ═══════════════════════════════════════════════════════════════
_engines = None


def _get_engines():
    """延迟初始化 V3 Engine 实例（单例）。"""
    global _engines
    if _engines is None:
        from ..engines import (
            SupportEngine,
            WaveEngine,
            ExpectedMoveEngine,
            RegimeEngine,
            RiskEngine,
        )
        _engines = {
            "support": SupportEngine(),
            "wave": WaveEngine(),
            "expected_move": ExpectedMoveEngine(),
            "regime": RegimeEngine(),
            "risk": RiskEngine(),
        }
    return _engines


def compute_alpha_score_v3(
    snap: dict,
    params,
    direction: str = "reduce",
) -> tuple[float, dict]:
    """从 features.py 的 snap dict 计算 V3 alpha_score。

    向后兼容接口：strategy.py 的 compute_alpha_score 委托此函数。
    返回 (alpha_score 0~100, sub_scores dict)。

    V3 集成 G2-G7 各 Engine：
      - Trend:        G1 占位（ADX + MACD + EMA）
      - Wave:         G4 WaveEngine（Trend Age / HH Count / ATR Expansion / Momentum Decay / Volume Decay）
      - Support:      G2 SupportEngine（动态支撑/阻力）
      - Momentum:     G1 占位（RSI + KDJ + MFI）
      - Liquidity:    G1 占位（量比）
      - ExpectedMove: G5 ExpectedMoveEngine（Qlib 预测 / 统计外推）
      - Risk:         RiskEngine（ATR + 止损 + 时间）

    :param snap: features.py 的特征快照（V3: 含 _bars 引用完整 K 线序列）
    :param params: SignalParams（V3 权重从 thresholds.yaml v3_alpha_weights 读取）
    :param direction: "reduce" 或 "add"
    """
    fv = FeatureVector.from_snapshot(snap, direction=direction)
    weights = _load_v3_weights_from_yaml()

    # V3: 如果 snap 中有 _bars，使用 Engine 计算（需要完整 K 线序列的维度）
    bars = snap.get("_bars", [])
    engines = _get_engines() if bars else {}

    # Trend 维度：G1 占位 + G7 RegimeEngine 加权
    trend_s = score_trend(fv)
    if engines and "regime" in engines:
        regime_s = engines["regime"].score(bars, snap, direction)
        # Trend = 60% G1占位 + 40% Regime
        trend_s = trend_s * 0.6 + regime_s * 0.4

    # Wave 维度：G4 WaveEngine（替换 G1 占位）
    if engines and "wave" in engines:
        wave_s = engines["wave"].score(bars, snap, direction)
    else:
        wave_s = score_wave(fv)

    # Support 维度：G2 SupportEngine（替换 G1 占位）
    if engines and "support" in engines:
        support_s = engines["support"].score(bars, snap, direction)
    else:
        support_s = score_support(fv)

    # Momentum 维度：G1 占位
    momentum_s = score_momentum(fv)

    # Liquidity 维度：G1 占位
    liquidity_s = score_liquidity(fv)

    # ExpectedMove 维度：G5 ExpectedMoveEngine（替换 G1 占位）
    if engines and "expected_move" in engines:
        expected_move_s = engines["expected_move"].score(bars, snap, direction)
    else:
        expected_move_s = score_expected_move(fv)

    # Risk 维度：RiskEngine（替换 G1 占位）
    if engines and "risk" in engines:
        risk_s = engines["risk"].score(bars, snap, direction)
    else:
        risk_s = score_risk(fv)

    sub_scores = {
        "trend": trend_s,
        "wave": wave_s,
        "support": support_s,
        "momentum": momentum_s,
        "liquidity": liquidity_s,
        "expected_move": expected_move_s,
        "risk": risk_s,
    }

    # 加权平均
    total_weight = sum(weights.values())
    if total_weight > 0:
        alpha = sum(sub_scores[k] * weights.get(k, 0) for k in sub_scores) / total_weight
    else:
        alpha = 0.0

    alpha = max(0.0, min(100.0, alpha))

    return alpha, sub_scores
