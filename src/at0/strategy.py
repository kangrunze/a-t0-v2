"""
strategy 层模块 — T+0 信号决策层。

包含 P0-5 三层信号引擎（极值层 / 确认层 / 环境层）：
  - 极值层（extreme）：VWAP 偏离 / BB / 开盘区间 + KDJ(主触发) + RSI(辅助) + MFI
  - 确认层（confirm）：缩量企稳 / 量能衰减 / 主动买卖压力
  - 环境层（filter）：涨跌停封板过滤 + 趋势过滤 + 市场层门控

regime（市场趋势状态：trend_up / trend_down / extreme / range）由 features 层
的 detect_market_regime 产出，本层仅消费，避免 risk 反向依赖 strategy。

L5 T+0 信号引擎（P0-5 三层决策结构）
======================================
特征计算 vs 信号决策两层分离：
  - 特征计算层（intraday_reference.py + stock_quote_features.py）：广算所有指标
  - 信号决策层（本模块）：按极值层/确认层/环境层三层组织

极值层（extreme，≥2项触发）: VWAP偏离 / BB / 开盘区间 + KDJ(主触发) + RSI(辅助) + MFI
确认层（confirm，≥1项触发）: 缩量企稳 / 量能衰减 / 主动买卖压力
环境层（filter，必须通过）: 涨跌停封板过滤 + 趋势过滤(P0-6 regime) + 市场层门控

设计原则（避免重蹈"规则堆了一堆但大部分没用"的覆祸）:
  1. 动量超买超卖 5 个指标(RSI/KDJ/CCI/BIAS/ROC)只挑 KDJ 作主触发 + RSI 辅助
  2. MACD/DMI 滞后性高，降级为趋势过滤（判断趋势盘/震荡盘），不作 1 分钟触发
  3. OBV 与 VOL Ratio 方向性重叠，OBV 不进决策层
  4. MFI 属资金超买超卖，归入极值层（P0-5 调整，原在量能层）
  5. 市场层(MarketSnapshot)作为门控：COLD 市场禁加仓
  6. 盘口特征(quote_feats)作为辅助：订单流代理进入确认层

触发规则（P0-5 三层结构，对齐方案 v1.1 Phase 3）:
  - 减仓信号: 极值≥2 + 确认≥1 + 环境通过
      极值层: 项1 VWAP偏离度≥+0.8×ATR / 项2 KDJ.K>80或RSI>70 / 项2b MFI>80
      确认层: 项3 5min缩量 或 主动卖占比>0.55
      环境层: 项4 未涨停封板 + 趋势过滤(extreme否决/trend_up加严)
  - 加仓信号: 极值≥2 + 确认≥1 + 环境通过
      极值层: 项1 VWAP偏离≤-0.8×ATR或跌破OR/BB / 项2 KDJ.K<20或RSI<30 / 项2b MFI<20
      确认层: 项3 连续缩量不创新低 或 主动买占比>0.55
      环境层: 项4 板块未退潮+未跌停 + 趋势过滤(extreme否决/trend_down加严)

独立性：只依赖 features 层的纯计算函数，不依赖 L1/L2/L3/L4。
L1/L2 熔断联动由调用方（risk）负责，本引擎只产出原始信号。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .features import compute_reference_snapshot, detect_market_regime
from .features import merge_with_reference_snapshot
from .features import atr_relative
from .features import (
    market_gate_for_add,
    market_gate_for_reduce,
    adjust_signal_weight,
)


# ═══════════════════════════════════════════════════════════════
# 信号参数
# ═══════════════════════════════════════════════════════════════
@dataclass
class SignalParams:
    """L5 信号参数。当前值为合成数据调优结果，需用真实分钟数据复验。"""
    # Layer A — 位置层
    vwap_dev_atr_multiplier: float = 0.8    # VWAP 偏离度阈值 = ±0.8 × ATR_intraday（相对值）
    bb_period: int = 20                     # 布林带周期
    bb_std: float = 2.0                     # 布林带标准差倍数
    ema_period: int = 20                    # EMA 周期（位置基准补充）

    # Layer B — 动量层
    rsi_period: int = 14
    rsi_overbought: float = 70.0            # RSI 超买（辅助，方案 v0.2 起始值）
    rsi_oversold: float = 30.0              # RSI 超卖（辅助，方案 v0.2 起始值）
    kdj_n: int = 9
    kdj_m1: int = 3
    kdj_m2: int = 3
    kdj_overbought: float = 80.0            # KDJ K 超买（主触发）
    kdj_oversold: float = 20.0              # KDJ K 超卖（主触发）

    # Layer B — 趋势过滤（不作触发，仅调整动量层权重）
    trend_filter_enabled: bool = True       # 是否启用 MACD/DMI 趋势过滤
    adx_trend_threshold: float = 25.0       # ADX > 此值视为趋势盘
    # P0-6: 极端趋势过滤参数
    adx_extreme_threshold: float = 40.0     # ADX ≥ 此值且价格远离VWAP时为极端趋势
    extreme_vwap_dev_multiplier: float = 2.0  # |VWAP偏离| ≥ 此值 × ATR相对值 时为极端偏离

    # Layer C — 量能层
    vol_ratio_lookback: int = 5             # 量比当前窗口
    vol_ratio_baseline: int = 20            # 量比基准窗口
    shrink_threshold: float = 0.8           # 缩量阈值：当前5min量 < 过去20min均量 × 0.8
    mfi_overbought: float = 80.0            # MFI 超买（资金超买）
    mfi_oversold: float = 20.0              # MFI 超卖（资金超卖）

    # Layer C — 订单流代理（来自 quote_feats，需 _quote_available=True）
    active_sell_pressure: float = 0.55      # 主动卖占比 > 此值视为卖压（减仓辅助）
    active_buy_pressure: float = 0.55       # 主动买占比 > 此值视为买盘（加仓辅助）

    # 触发阈值（P0-5: 三层结构）
    min_rules_to_trigger: int = 3           # 总分阈值（向后兼容；实际触发还受 extreme_min/confirm_min 约束）
    extreme_min: int = 2                    # 极值层至少满足项数
    confirm_min: int = 1                    # 确认层至少满足项数

    # ── 趋势跟随参数（2026-07-24 策略转向）──
    # 数据诊断：5min VWAP偏离后65%概率延续，29%概率回归
    # 均值回归策略在A股5min级别不可行，转为顺趋势方向开仓
    # 开仓：VWAP偏离方向 + ADX趋势确认 + KDJ同向 + 量能放大
    # 平仓：趋势反转（VWAP穿越/ADX回落/KDJ反向），止损纯风控兜底（分离止盈止损）
    tf_adx_threshold: float = 35.0        # ADX > 此值确认趋势存在（v5降频：30→35，成本占毛利8.5x需大幅降频）
    tf_vol_ratio_min: float = 2.0         # 量比 > 此值确认量能放大（v5降频：1.5→2.0）
    tf_trend_reverse_adx: float = 28.0    # ADX<此值视为趋势减弱（平仓条件，分离止盈止损后放宽让平仓信号能触发）
    tf_vwap_cross_threshold: float = 0.002  # |VWAP偏离| < 此值视为VWAP穿越（平仓条件，0.2%）
    tf_kdj_reverse_bars: int = 2          # KDJ连续N根反向确认（平仓条件，防单根噪声）

    # ── P2: Alpha 连续评分开关（主计划 §2.3，默认关闭）──
    # flag=false 时行为与改造前逐笔一致（主计划 §5 验收1）
    # flag=true 时启用连续 alpha_score（P2 阶段实现，当前为 stub）
    use_continuous_alpha: bool = False
    # Alpha 评分权重（等权起步，写入 thresholds.yaml 做版本管理）
    alpha_weight_vwap: float = 0.5
    alpha_weight_rsi: float = 0.5
    alpha_weight_kdj: float = 0.5
    alpha_weight_adx: float = 2.0
    alpha_weight_volume: float = 0.0
    # Alpha 触发阈值（连续评分模式下的开仓门槛）
    alpha_threshold_open: float = 80.0

    # Layer P — 平仓层（is_for_pairing=True 时使用）
    # 趋势跟随平仓：趋势反转信号（ADX回落/VWAP穿越/KDJ反向）
    pairing_vwap_dev_threshold: float = 0.008  # 平仓阈值下限（0.8%），open_vwap_dev 缺失时退化为此值
    pairing_no_new_extreme_bars: int = 2       # 连续 N 根 K 线不创新极值（轻量方向确认，防单根噪声误触发）
    min_capture_spread_for_pairing: float = 0.006   # 平仓动态阈值的成本锚点（0.6%，与 RiskParams.min_capture_spread 对齐）
    pairing_max_regression_ratio: float = 0.5       # 动态阈值上限比例（开仓深度的50%）

    # ── J2: 回踩入场参数（独立开关，2026-07-29 验证通过正式启用）──
    # 设计目标：在已确认的上涨趋势中，等价格回踩到VWAP附近再买入，
    # 而不是在趋势确认那一刻就追高入场。从而让买入点更贴近当日相对低点。
    # 验证：36股×3年×4组A/B，net_pnl+90%、CE均值+39%、win_rate维持69%
    # dataclass默认False（yaml缺失时安全兜底），thresholds.yaml已设为true
    retracement_entry_enabled: bool = False   # dataclass兜底=False；yaml生产配置=true
    retracement_vwap_band: float = 0.005      # |VWAP偏离| ≤ 此值视为"回踩到VWAP"（0.5%）
    retracement_lookback: int = 8             # 回看N根K线检查是否有过冲高
    retracement_min_surge: float = 0.005      # 回看期间最高价相对VWAP的最小偏离（0.5%）
    retracement_kdj_max: float = 60.0         # KDJ.K需低于此值（回踩时K回落，非追高）

    # ── J4: 冲高确认参数（卖出侧，独立开关，2026-07-29 验证后拒绝启用）──
    # 设计目标：在已确认的下跌趋势中，等价格冲高（反弹）到VWAP附近再卖出，
    # 而不是在趋势确认那一刻就杀跌卖出。从而让卖出点更贴近当日相对高点。
    # 与J2完全对称：J2买在回踩低位，J4卖在冲高高位。
    # 验证结论：36股×3年×4组A/B + 20股×5参数敏感性检查证明J4结构性无效：
    #   - J4单独使CE均值2.26%→1.82%（与设计目标相反）
    #   - 根因：趋势跟随卖出逻辑"卖在强势区"已接近CE最优，J4"先跌后反弹到VWAP再卖"卖点更低
    #   - 参数从松到紧：要么加量降质(CE降)，要么退化为baseline(无效)
    surge_exit_enabled: bool = False          # 拒绝启用（结构性无效）
    surge_vwap_band: float = 0.005            # |VWAP偏离| ≤ 此值视为"冲高到VWAP"（0.5%）
    surge_lookback: int = 8                   # 回看N根K线检查是否有过深跌
    surge_min_drop: float = 0.005             # 回看期间最低价相对VWAP的最小负偏离（0.5%）
    surge_kdj_min: float = 40.0               # KDJ.K需高于此值（冲高时K回升，非杀跌）

    # ── Stage K: 均值回归参数（独立模式，与趋势跟随互斥）──
    # K0闸门诊断：振幅筛选36只×3年，|vwap_dev|≥3%时回归概率55-64%且期望值为正
    # 触发逻辑：z-score标准化 + 衰竭确认 + 保守止盈止损
    # 模式选择：strategy_mode="trend_following"(默认) 或 "mean_reversion"
    # 100只调参最优值（2026-07-29）：z=1.0、rev_ratio=0.3，OOS验证稳定（详见thresholds.yaml注释）
    strategy_mode: str = "trend_following"
    mr_z_threshold: float = 1.0              # z-score阈值（100只调参最优：1.5→1.0）
    mr_lookback_bars: int = 12               # 回看窗口（计算价格极值、衰竭确认）
    mr_vol_ratio_max: float = 1.5            # 量比上限：量比>此值视为趋势仍在加速（不触发均值回归）
    # 平仓：基于开仓时偏离度的绝对回归目标（非实时z，避免VWAP漂移导致立即平仓）
    mr_take_profit_reversion_ratio: float = 0.3  # 100只调参最优：0.5→0.3（更紧平仓）
    mr_min_reversion: float = 0.001          # 最小回归幅度：|open_vwap_dev|-|当前vwap_dev| ≥ 此值才平仓（防立即平仓）


DEFAULT_PARAMS = SignalParams()


# ═══════════════════════════════════════════════════════════════
# 信号结果
# ═══════════════════════════════════════════════════════════════
@dataclass
class TSignal:
    """单个 T 信号。"""
    direction: str                          # "reduce" (减仓) 或 "add" (加仓/买回)
    rules_fired: list[str] = field(default_factory=list)  # 触发的规则描述
    rules_score: int = 0                    # 触发项数（总分 = extreme + confirm + filter）
    price: float = 0.0                      # 信号触发时的价格
    snapshot: dict = field(default_factory=dict)  # 完整指标快照（用于审计）
    layer_scores: dict = field(default_factory=dict)  # 三层各自触发项数 {extreme: n, confirm: n, filter: n}
    market_gate: Optional[dict] = None      # 市场层门控结果 {allowed, reason, weight}
    trend_context: Optional[str] = None     # 趋势过滤判定 "trend_up"/"trend_down"/"range"/"extreme"
    trigger_threshold: int = 3              # 触发阈值（从 params.min_rules_to_trigger 传入）
    # P0-5: 三层结构得分与门槛
    extreme_score: int = 0                  # 极值层触发项数
    confirm_score: int = 0                  # 确认层触发项数
    filter_passed: bool = True              # 环境层过滤是否通过
    extreme_min: int = 2                    # 极值层最少项数
    confirm_min: int = 1                    # 确认层最少项数
    # Layer P: 平仓层（is_for_pairing=True 时使用）
    is_pairing: bool = False                 # 是否为平仓评估（vs 新开仓评估）
    pairing_near_vwap: bool = False          # 平仓：价格已回归 VWAP 附近
    pairing_direction_confirmed: bool = False  # 平仓：轻量方向确认（不创新极值）
    # Stage K: 信号来源标记（trend_following / mean_reversion），用于分组统计
    signal_source: str = "trend_following"

    @property
    def triggered(self) -> bool:
        """
        触发条件：
        - 平仓分支（is_pairing=True）：环境层通过 + 价格回归VWAP附近 + 轻量方向确认
          （不走三层 confluence，因为均值回归的平仓定义是"价格回归均值"而非"对向出现极端"）
        - 开仓分支（is_pairing=False）：环境层通过 + 极值≥extreme_min + 确认≥confirm_min + 总分≥阈值
        """
        if not self.filter_passed:
            return False
        if self.is_pairing:
            # 平仓分支：距离判断 + 方向确认（不走三层 confluence）
            return self.pairing_near_vwap and self.pairing_direction_confirmed
        # 开仓分支：三层 confluence
        if self.extreme_score < self.extreme_min:
            return False
        if self.confirm_score < self.confirm_min:
            return False
        return self.rules_score >= self.trigger_threshold

    @property
    def t_type(self) -> str:
        """对应的 T 操作类型说明。"""
        if self.direction == "reduce":
            return "正T-卖出 / 反T-买回"
        return "反T-买入 / 正T-买回"


# ═══════════════════════════════════════════════════════════════
# 平仓层辅助：轻量方向确认（连续 N 根 K 线不创新极值）
# ═══════════════════════════════════════════════════════════════
def _no_new_extreme_recently(bars: list[dict], direction: str, lookback: int) -> bool:
    """
    轻量方向确认：最近 lookback 根 K 线不再创新极值。

    - direction="reduce"（卖出平 buy 仓）：最近 lookback 根 K 线的 high 都 ≤ 之前的高点
      （价格不再创新高，上行已停滞，适合获利了结卖出）
    - direction="add"（买回平 sell 仓）：最近 lookback 根 K 线的 low 都 ≥ 之前的低点
      （价格不再创新低，下行已停滞，适合获利了结买回）

    防止单根 K 线的噪声误触发平仓信号。
    """
    if len(bars) < lookback + 1:
        return False
    prior = bars[:-lookback]
    recent = bars[-lookback:]
    if direction == "reduce":
        prior_high = max(b["high"] for b in prior) if prior else 0
        return all(b["high"] <= prior_high for b in recent)
    else:  # add
        prior_low = min(b["low"] for b in prior) if prior else float("inf")
        return all(b["low"] >= prior_low for b in recent)


# ═══════════════════════════════════════════════════════════════
# 价格趋势确认（趋势跟随平仓的方向确认）
# ═══════════════════════════════════════════════════════════════
def _price_trend_confirmed(bars: list[dict], direction: str, lookback: int) -> bool:
    """
    检查最近 lookback 根 K 线收盘价是否多数同方向（趋势跟随平仓的方向确认）。

    - direction="up":  买入腿平仓（卖出），需价格上行确认趋势反转向上
    - direction="down": 卖出腿平仓（买回），需价格下行确认趋势反转向下

    60% 以上同方向即确认（避免单根噪声，比"全部同方向"更实用）。
    """
    if len(bars) < lookback + 1:
        return False
    recent = bars[-(lookback + 1):]
    up_count = sum(1 for i in range(1, len(recent))
                   if recent[i]["close"] > recent[i - 1]["close"])
    if direction == "up":
        return up_count >= lookback * 0.6
    else:  # down
        return (lookback - up_count) >= lookback * 0.6


# ═══════════════════════════════════════════════════════════════
# 平仓动态阈值计算（方案C1修正版）
# ═══════════════════════════════════════════════════════════════
# @deprecated 此函数为均值回归时代的平仓阈值计算，趋势跟随转向后全项目无调用。
# 保留以备未来回归策略复用，新增代码不应依赖此函数。
def _compute_pairing_threshold(
    open_vwap_dev: Optional[float],
    params: SignalParams,
    holding_ratio: float = 0.0,
) -> float:
    """
    计算平仓动态阈值（方案C1修正版 + 方案B时间衰减）。

    基础阈值 = max(floor, min(|open_dev| - cost, |open_dev| × max_regression_ratio))

    - floor (pairing_vwap_dev_threshold, 0.8%): 下限保护，避免阈值过小
    - |open_dev| - cost: 距离锚定成本，价格回归到"仍能覆盖成本"的位置即平仓
    - |open_dev| × max_regression_ratio (0.7): 上限保护，防止 open_dev 过大时
      阈值过松导致过早平仓（修正 C1 原始公式在 open_dev>4% 时的缺陷）

    方案B 时间衰减（holding_ratio > 0 时生效）：
      holding_ratio = holding_bars / max_holding_bars（0.0~1.0）
      - >0.8（接近超时）：阈值 ×0.5，让腿更容易平仓，避免被动 expired
      - >0.5（过半未平）：阈值 ×0.7，适度降低门槛促成平仓
      - ≤0.5：不衰减
    目的：把"被动 expired 大亏"转化为"主动平仓 小亏/小赚"。

    open_vwap_dev 为 None 时（未传入开仓信息，向后兼容），退化为固定 floor（仍受衰减影响）。

    回放验证依据: outputs/backtest/diagnose_formula_replay.json
      - C1 触发 34 条 / +10,838 元（vs C2 触发 10 条 / +11,011 元）
      - C1 触发数是 C2 的 3.4 倍，统计更稳；C2 盈亏集中度高不可信
      - C1 原始公式在 open_dev=5.17% 时阈值 4.57% 过松，单条亏损 -3818
        加 70% 上限后阈值降至 3.62%，可避免此类过早平仓
    """
    floor = params.pairing_vwap_dev_threshold
    if open_vwap_dev is None:
        base = floor
    else:
        open_dev_abs = abs(open_vwap_dev)
        cost_anchored = open_dev_abs - params.min_capture_spread_for_pairing
        max_allowed = open_dev_abs * params.pairing_max_regression_ratio
        base = max(floor, min(cost_anchored, max_allowed))

    # 方案B：时间衰减（接近超时时降低平仓门槛）
    if holding_ratio > 0.8:
        return base * 0.5
    elif holding_ratio > 0.5:
        return base * 0.7
    return base


# ═══════════════════════════════════════════════════════════════
# 趋势过滤判定（P0-6: 委托给 features 层的 detect_market_regime）
# ═══════════════════════════════════════════════════════════════
def _judge_trend_context(snap: dict, params: SignalParams, frequency: str = "1min") -> str:
    """
    判定当前趋势上下文（委托给 features 层的 detect_market_regime）。

    P0-6 整改：regime 检测逻辑归入 features 层（intraday_reference.py），
    strategy 和 risk 都可读，避免 risk 反向依赖 strategy。

    P0-6 修复（2026-07-24）：min_bars_for_trend 按频率自适应。
    旧实现硬编码 60 根，5min 数据一天 48 根永远 < 60，detect_market_regime
    直接返回 "range"，趋势过滤（trend_up/down/extreme）完全失效，逆势均值回归
    在强趋势中照常触发。改为：1min=60根（1小时），5min=12根（1小时，等价时长）。

    返回:
      "trend_up"   : 上升趋势盘
      "trend_down" : 下降趋势盘
      "extreme"    : 极端趋势（ADX极高 + 价格远离VWAP）
      "range"      : 震荡盘或数据不足

    用途（P0-6 趋势过滤）:
      - trend_up 时，减仓信号需更高确认（趋势可能继续上行，卖出逆势）
      - trend_down 时，加仓信号需更高确认（趋势可能继续下探，买入逆势）
      - extreme 时，暂停所有均值回归（硬否决）
      - range 时，不调整
    """
    if not params.trend_filter_enabled:
        return "range"

    # P0-6: 按频率自适应 min_bars_for_trend（等价 1 小时 K 线数）
    if frequency == "5min":
        effective_min_bars = 12   # 5min × 12 = 60 分钟
    else:
        effective_min_bars = 60   # 1min × 60 = 60 分钟

    return detect_market_regime(
        snap,
        adx_trend_threshold=params.adx_trend_threshold,
        adx_extreme_threshold=params.adx_extreme_threshold,
        extreme_vwap_dev_multiplier=params.extreme_vwap_dev_multiplier,
        min_bars_for_trend=effective_min_bars,
    )


# ═══════════════════════════════════════════════════════════════
# 减仓信号评估（正T-卖出 / 反T-买回）
# ═══════════════════════════════════════════════════════════════
def evaluate_reduce_signal(
    bars: list[dict],
    current_price: Optional[float] = None,
    prev_close: Optional[float] = None,
    is_limit_up_locked: bool = False,
    params: Optional[SignalParams] = None,
    quote_feats: Optional[dict] = None,
    is_for_pairing: bool = False,
    open_vwap_dev: Optional[float] = None,
    frequency: str = "1min",
    holding_ratio: float = 0.0,
) -> TSignal:
    """
    减仓信号评估（趋势跟随：下跌趋势中卖出开仓 / 趋势反转时平买仓）。

    数据诊断：5min VWAP偏离后65%概率延续，29%概率回归。
    均值回归策略在A股5min级别不可行，转为顺趋势方向开仓。

    - is_for_pairing=False（默认，新开仓）：下跌趋势跟随卖出
      极值层（满分3）：VWAP偏离为负 + ADX确认趋势 + KDJ死叉
      确认层（满分1）：量能放大
      环境层：未涨停封板（可成交）
    - is_for_pairing=True（为配对已有 buy 仓位）：趋势反转平仓
      趋势反转（偏离转正 / 穿越VWAP / ADX回落）+ 价格上行确认 + 未涨停封板

    趋势过滤（_judge_trend_context）保留但仅作信息记录，不再用于逆势加严
    （趋势跟随本身即顺趋势，无需对逆势开仓加严）。

    open_vwap_dev: @deprecated 开仓时刻的 vwap_dev（由调用方从 TradeLeg 传入）。
                  趋势跟随不再使用动态平仓阈值（_compute_pairing_threshold），
                  保留参数以兼容函数签名，函数体内不读取此值。
                  新增代码不应依赖此参数。
    """
    params = params or DEFAULT_PARAMS
    snap = compute_reference_snapshot(bars, current_price, prev_close, params=params)
    if not snap:
        return TSignal(direction="reduce")

    snap = merge_with_reference_snapshot(snap, quote_feats)

    # Stage K: 均值回归模式（与趋势跟随互斥，手动选择）
    if params.strategy_mode == "mean_reversion":
        filter_passed = not is_limit_up_locked
        mr_signal = _evaluate_mean_reversion(
            snap, bars, params, direction="reduce",
            is_for_pairing=is_for_pairing,
            filter_passed=filter_passed,
            open_vwap_dev=open_vwap_dev,
        )
        return mr_signal if mr_signal is not None else TSignal(direction="reduce")

    fired: list[str] = []
    extreme_score = 0  # 极值层计分（0-3）
    confirm_score = 0  # 确认层计分（0-1）
    price = snap["current_price"]
    vwap_dev = snap.get("vwap_dev")
    adx = snap.get("adx")
    k_val = snap.get("kdj_k")
    d_val = snap.get("kdj_d")
    vol_ratio = snap.get("volume_ratio")

    # 趋势过滤：仅作信息记录，趋势跟随不据此加严或否决
    trend_context = _judge_trend_context(snap, params, frequency)

    # 格式化辅助（防 None）
    vwap_dev_str = f"{vwap_dev*100:+.2f}%" if vwap_dev is not None else "N/A"
    adx_str = f"{adx:.1f}" if adx is not None else "N/A"

    # ═══ 平仓分支（is_for_pairing=True）：趋势反转 + 价格上行确认 ═══
    if is_for_pairing:
        filter_passed = not is_limit_up_locked
        # 趋势反转判断：偏离转正 / 穿越VWAP / ADX回落（任一即视为下跌趋势结束）
        trend_reversed = False
        if vwap_dev is not None and vwap_dev > 0:
            trend_reversed = True
        elif vwap_dev is not None and abs(vwap_dev) < params.tf_vwap_cross_threshold:
            trend_reversed = True
        elif adx is not None and adx < params.tf_trend_reverse_adx:
            trend_reversed = True
        near_vwap = trend_reversed  # 复用字段语义：趋势反转 ≈ 价格回到反转点
        # 方向确认：价格上行确认趋势反转向上（买入腿平仓卖出，需价格上行获利了结）
        dir_confirmed = _price_trend_confirmed(bars, "up", params.tf_kdj_reverse_bars)
        fired_pairing = []
        if near_vwap:
            fired_pairing.append(f"[平仓-趋势反转] vwap_dev={vwap_dev_str} adx={adx_str} 下跌趋势结束（偏离转正/穿越VWAP/ADX回落）")
        else:
            fired_pairing.append(f"[平仓-趋势反转] vwap_dev={vwap_dev_str} adx={adx_str} 下跌趋势延续（未反转）")
        if dir_confirmed:
            fired_pairing.append(f"[平仓-方向确认] 最近{params.tf_kdj_reverse_bars}根K线价格多数上行（反转向上）")
        else:
            fired_pairing.append(f"[平仓-方向确认] 最近{params.tf_kdj_reverse_bars}根K线价格未上行（仍在下探）")
        if filter_passed:
            fired_pairing.append("[环境4] 未涨停封板（可成交）")
        else:
            fired_pairing.append("[环境4] 涨停封板（硬否决）")
        return TSignal(
            direction="reduce",
            rules_fired=fired_pairing,
            rules_score=2 if (near_vwap and dir_confirmed and filter_passed) else 0,
            price=price,
            snapshot=snap,
            layer_scores={"pairing_distance": 1 if near_vwap else 0,
                          "pairing_direction": 1 if dir_confirmed else 0,
                          "filter": 1 if filter_passed else 0},
            trend_context=trend_context,
            trigger_threshold=2,  # 平仓分支走自己的触发逻辑，不走 trigger_threshold
            extreme_score=0,
            confirm_score=0,
            filter_passed=filter_passed,
            extreme_min=params.extreme_min,
            confirm_min=params.confirm_min,
            is_pairing=True,
            pairing_near_vwap=near_vwap,
            pairing_direction_confirmed=dir_confirmed,
        )

    # ── 极值层 项1: VWAP偏离为负（价格在VWAP下方，空头）──
    if vwap_dev is not None and vwap_dev < 0:
        fired.append(f"[极值1] VWAP偏离 {vwap_dev_str} < 0（价格在VWAP下方，空头）")
        extreme_score += 1

    # ── 极值层 项2: ADX确认趋势存在 ──
    if adx is not None and adx > params.tf_adx_threshold:
        fired.append(f"[极值2] ADX={adx_str} > {params.tf_adx_threshold}（趋势确认）")
        extreme_score += 1

    # ── 极值层 项3: KDJ死叉（K < D，动量向下）──
    if k_val is not None and d_val is not None and k_val < d_val:
        fired.append(f"[极值3] KDJ.K={k_val:.1f} < D={d_val:.1f}（死叉，动量向下）")
        extreme_score += 1

    # ── 确认层 项4: 量能放大 ──
    if vol_ratio is not None and vol_ratio > params.tf_vol_ratio_min:
        fired.append(f"[确认4] 量比 {vol_ratio:.2f} > {params.tf_vol_ratio_min}（量能放大）")
        confirm_score += 1

    # ── 环境层 项5: 涨停封板过滤（卖出需可成交）──
    filter_passed = not is_limit_up_locked
    if filter_passed:
        fired.append("[环境5] 未涨停封板（可成交）")
    else:
        fired.append("[环境5] 涨停封板（硬否决）")

    # 趋势上下文（信息记录，不调整阈值）
    fired.append(f"[趋势] {trend_context}（信息记录，趋势跟随不调整阈值）")

    # 计分：总分 = 极值 + 确认 + 过滤
    total_score = extreme_score + confirm_score + (1 if filter_passed else 0)
    if not filter_passed:
        total_score = 0

    # ═══ J4: 冲高确认（卖出侧分离模式：趋势确认 + 卖出价格确定 分离）═══
    # 设计（与J2对称）：下跌趋势确认后，等价格冲高（反弹）到VWAP附近再卖出
    #   - 趋势确认（历史）：过去N根K线有过深跌（recent_low < VWAP × (1-min_drop)）
    #   - 冲高确认（当前）：价格从深跌区回升到VWAP附近（-band ≤ vwap_dev < 0）
    #     + KDJ.K回升（K > kdj_min，确认是反弹而非深跌）
    #   - 两者都满足时直接触发卖出信号，即使当前K线的4规则投票未通过
    #   - 冲高条件不满足时，走原来的4规则投票逻辑（向后兼容）
    if params.surge_exit_enabled and filter_passed:
        vwap_val = snap.get("vwap")

        # 条件1（卖出价格）：VWAP偏离在负向上限内（价格从深跌回升到VWAP附近）
        surge_vwap_ok = (vwap_dev is not None and -params.surge_vwap_band <= vwap_dev < 0)
        # 条件2（卖出价格）：KDJ.K已回升（K从低位反弹）
        surge_kdj_ok = (k_val is not None and k_val > params.surge_kdj_min)
        # 条件3（趋势确认）：近期有过深跌（下跌趋势存在过）
        surge_lookback = params.surge_lookback
        recent_bars = bars[-surge_lookback:] if len(bars) >= surge_lookback else bars
        recent_low = min((b.get("low", float("inf")) for b in recent_bars), default=float("inf"))
        had_drop = (vwap_val is not None and vwap_val > 0
                    and recent_low < vwap_val * (1 - params.surge_min_drop))

        surge_ok = surge_vwap_ok and surge_kdj_ok and had_drop
        if surge_ok:
            # 冲高触发：强制提升所有分数到触发阈值
            total_score = max(total_score, params.min_rules_to_trigger)
            extreme_score = max(extreme_score, params.extreme_min)
            confirm_score = max(confirm_score, params.confirm_min)
            fired.append(f"[冲高确认-触发] vwap_dev={vwap_dev_str}"
                        f" K={'%.1f' % k_val if k_val else 'N/A'}"
                        f" 近{surge_lookback}根低点偏离VWAP "
                        f"{((recent_low/vwap_val-1)*100):.2f}%（≤-{params.surge_min_drop*100:.1f}%）"
                        f" → 分离模式触发，不依赖当前4规则投票")
        else:
            # 冲高条件不满足，记录原因（走原逻辑）
            reasons = []
            if not surge_vwap_ok:
                reasons.append(f"vwap_dev={vwap_dev_str}需在[-{params.surge_vwap_band*100:.1f}%, 0)")
            if not surge_kdj_ok:
                reasons.append(f"K={'%.1f' % k_val if k_val else 'N/A'}需>{params.surge_kdj_min}")
            if not had_drop:
                reasons.append(f"近{surge_lookback}根无深跌（需≤-{params.surge_min_drop*100:.1f}%）")
            fired.append(f"[冲高确认-未触发] {', '.join(reasons)} → 走4规则投票")

    # 趋势跟随：不额外加严，trigger_threshold = min_rules_to_trigger
    trigger_threshold = params.min_rules_to_trigger

    return TSignal(
        direction="reduce",
        rules_fired=fired,
        rules_score=total_score,
        price=price,
        snapshot=snap,
        layer_scores={"extreme": extreme_score, "confirm": confirm_score, "filter": 1 if filter_passed else 0},
        trend_context=trend_context,
        trigger_threshold=trigger_threshold,
        extreme_score=extreme_score,
        confirm_score=confirm_score,
        filter_passed=filter_passed,
        extreme_min=params.extreme_min,
        confirm_min=params.confirm_min,
    )


# ═══════════════════════════════════════════════════════════════
# Stage K: 均值回归信号评估
# ═══════════════════════════════════════════════════════════════
def _evaluate_mean_reversion(
    snap: dict,
    bars: list[dict],
    params: SignalParams,
    direction: str,  # "add" (买入侧) 或 "reduce" (卖出侧)
    is_for_pairing: bool,
    filter_passed: bool,
    open_vwap_dev: Optional[float] = None,
) -> Optional[TSignal]:
    """均值回归信号评估。

    触发逻辑（开仓）：
      1. z-score = |vwap_dev| / (atr/vwap) ≥ mr_z_threshold
      2. 衰竭确认：回看窗口内价格已从极端位回落（不再创新极值）
      3. 量比不能太高（趋势仍在加速时不触发）
      4. 方向：vwap_dev<0 → 买入(add)；vwap_dev>0 → 卖出(reduce)

    平仓逻辑（is_for_pairing=True）：
      z-score 回归到 mr_take_profit_z 以下（回到VWAP附近）
    """
    vwap_dev = snap.get("vwap_dev")
    atr = snap.get("atr")
    vwap = snap.get("vwap")
    vol_ratio = snap.get("volume_ratio")
    price = snap["current_price"]

    if vwap_dev is None or atr is None or vwap is None or vwap <= 0 or atr <= 0:
        return None

    # z-score 标准化（用 features 层公共函数，避免公式漂移）
    atr_rel = atr_relative(atr, vwap)
    if atr_rel is None or atr_rel <= 0:
        return None
    z_score = abs(vwap_dev) / atr_rel

    vwap_dev_str = f"{vwap_dev*100:+.2f}%"
    z_str = f"{z_score:.2f}"
    vol_str = f"{vol_ratio:.2f}" if vol_ratio is not None else "N/A"

    # ── 平仓分支：基于开仓时偏离度的绝对回归目标 ──
    if is_for_pairing:
        # 平仓条件：当前|vwap_dev| ≤ 开仓时|open_vwap_dev| × 回归比例
        # 且至少回归了 mr_min_reversion 的绝对幅度（过滤开仓后立即平仓）
        if open_vwap_dev is None or open_vwap_dev == 0:
            # 没有开仓基准，不触发平仓
            return TSignal(
                direction=direction, signal_source="mean_reversion",
                price=price, snapshot=snap,
            )
        open_abs = abs(open_vwap_dev)
        curr_abs = abs(vwap_dev)
        reversion_amount = open_abs - curr_abs  # 正=已回归
        target = open_abs * params.mr_take_profit_reversion_ratio
        near_vwap = curr_abs <= target and reversion_amount >= params.mr_min_reversion
        fired = [
            f"[MR平仓] 开仓偏离{open_vwap_dev*100:+.2f}% → 当前{vwap_dev*100:+.2f}%",
            f"  回归{reversion_amount*100:.2f}% 目标{target*100:.2f}% "
            f"({'达到' if near_vwap else '未达'}，需≥{params.mr_min_reversion*100:.1f}%)",
        ]
        return TSignal(
            direction=direction,
            rules_fired=fired,
            rules_score=2 if (near_vwap and filter_passed) else 0,
            price=price,
            snapshot=snap,
            layer_scores={},
            trigger_threshold=2,
            extreme_score=0,
            confirm_score=0,
            filter_passed=filter_passed,
            extreme_min=0,
            confirm_min=0,
            is_pairing=True,
            pairing_near_vwap=near_vwap,
            pairing_direction_confirmed=True,
            signal_source="mean_reversion",
        )

    # ── 开仓分支 ──
    # 方向判断：vwap_dev<0 → 买入(add)；vwap_dev>0 → 卖出(reduce)
    if direction == "add" and vwap_dev >= 0:
        return None  # 买入侧需要价格在VWAP下方
    if direction == "reduce" and vwap_dev <= 0:
        return None  # 卖出侧需要价格在VWAP上方

    fired = []
    score = 0

    # 条件1：z-score 超标（偏离幅度相对自身波动率足够大）
    z_ok = z_score >= params.mr_z_threshold
    if z_ok:
        fired.append(f"[MR极值] z={z_str} ≥ {params.mr_z_threshold}（偏离{vwap_dev_str}，标准化后超标）")
        score += 1
    else:
        return None  # z不达标直接返回，不继续评估

    # 条件2：衰竭确认（回看窗口内价格已从极端位回落）
    lookback = params.mr_lookback_bars
    recent_bars = bars[-lookback:] if len(bars) >= lookback else bars
    if direction == "add":
        # 买入侧：价格在VWAP下方，回看窗口内最低价 < 当前价（已从最低点回升）
        recent_low = min((float(b.get("low", 0)) for b in recent_bars), default=0)
        fatigue_ok = recent_low < price and recent_low > 0
        if fatigue_ok:
            fired.append(f"[MR衰竭] 近{lookback}根最低{recent_low:.2f} < 当前{price:.2f}（已从低点回升）")
            score += 1
    else:
        # 卖出侧：价格在VWAP上方，回看窗口内最高价 > 当前价（已从高点回落）
        recent_high = max((float(b.get("high", 0)) for b in recent_bars), default=0)
        fatigue_ok = recent_high > price and recent_high > 0
        if fatigue_ok:
            fired.append(f"[MR衰竭] 近{lookback}根最高{recent_high:.2f} > 当前{price:.2f}（已从高点回落）")
            score += 1

    # 条件3：量比不能太高（趋势仍在加速时不触发均值回归）
    vol_ok = vol_ratio is None or vol_ratio <= params.mr_vol_ratio_max
    if vol_ok:
        fired.append(f"[MR量能] 量比{vol_str} ≤ {params.mr_vol_ratio_max}（趋势未加速）")
        score += 1
    else:
        fired.append(f"[MR量能] 量比{vol_str} > {params.mr_vol_ratio_max}（趋势加速中，不触发）")

    # 环境层
    if filter_passed:
        fired.append("[MR环境] 可成交")
    else:
        fired.append("[MR环境] 不可成交（硬否决）")

    total_score = score + (1 if filter_passed else 0)
    if not filter_passed:
        total_score = 0

    return TSignal(
        direction=direction,
        rules_fired=fired,
        rules_score=total_score,
        price=price,
        snapshot=snap,
        layer_scores={"extreme": 1 if z_ok else 0,
                      "confirm": 1 if fatigue_ok else 0,
                      "filter": 1 if filter_passed else 0},
        trend_context=None,
        trigger_threshold=3,  # 需要z+衰竭+量能+环境都满足
        extreme_score=1 if z_ok else 0,
        confirm_score=1 if fatigue_ok else 0,
        filter_passed=filter_passed,
        extreme_min=1,
        confirm_min=1,
        signal_source="mean_reversion",
    )


# ═══════════════════════════════════════════════════════════════
# 加仓/买回信号评估（反T-买入 / 正T-买回）
# ═══════════════════════════════════════════════════════════════
def evaluate_add_signal(
    bars: list[dict],
    current_price: Optional[float] = None,
    prev_close: Optional[float] = None,
    is_limit_down_locked: bool = False,
    theme_retreated: bool = False,
    params: Optional[SignalParams] = None,
    quote_feats: Optional[dict] = None,
    is_for_pairing: bool = False,
    open_vwap_dev: Optional[float] = None,
    frequency: str = "1min",
    holding_ratio: float = 0.0,
) -> TSignal:
    """
    加仓/买回信号评估（趋势跟随：上涨趋势中买入开仓 / 趋势反转时平卖仓）。

    数据诊断：5min VWAP偏离后65%概率延续，29%概率回归。
    均值回归策略在A股5min级别不可行，转为顺趋势方向开仓。

    - is_for_pairing=False（默认，新开仓）：上涨趋势跟随买入
      极值层（满分3）：VWAP偏离为正 + ADX确认趋势 + KDJ金叉
      确认层（满分1）：量能放大
      环境层：板块未退潮 + 未跌停封板
    - is_for_pairing=True（为配对已有 sell 仓位）：趋势反转平仓
      趋势反转（偏离转负 / 穿越VWAP / ADX回落）+ 价格下行确认 + 未跌停封板

    趋势过滤（_judge_trend_context）保留但仅作信息记录，不再用于逆势加严。

    open_vwap_dev: @deprecated 开仓时刻的 vwap_dev（由调用方从 TradeLeg 传入）。
                  趋势跟随不再使用动态平仓阈值（_compute_pairing_threshold），
                  保留参数以兼容函数签名，函数体内不读取此值。
                  新增代码不应依赖此参数。
    """
    params = params or DEFAULT_PARAMS
    snap = compute_reference_snapshot(bars, current_price, prev_close, params=params)
    if not snap:
        return TSignal(direction="add")

    snap = merge_with_reference_snapshot(snap, quote_feats)

    # Stage K: 均值回归模式（与趋势跟随互斥，手动选择）
    if params.strategy_mode == "mean_reversion":
        filter_passed = (not theme_retreated) and (not is_limit_down_locked)
        mr_signal = _evaluate_mean_reversion(
            snap, bars, params, direction="add",
            is_for_pairing=is_for_pairing,
            filter_passed=filter_passed,
            open_vwap_dev=open_vwap_dev,
        )
        return mr_signal if mr_signal is not None else TSignal(direction="add")

    fired: list[str] = []
    extreme_score = 0  # 极值层计分（0-3）
    confirm_score = 0  # 确认层计分（0-1）
    price = snap["current_price"]
    vwap_dev = snap.get("vwap_dev")
    adx = snap.get("adx")
    k_val = snap.get("kdj_k")
    d_val = snap.get("kdj_d")
    vol_ratio = snap.get("volume_ratio")

    # 趋势过滤：仅作信息记录，趋势跟随不据此加严或否决
    trend_context = _judge_trend_context(snap, params, frequency)

    # 格式化辅助（防 None）
    vwap_dev_str = f"{vwap_dev*100:+.2f}%" if vwap_dev is not None else "N/A"
    adx_str = f"{adx:.1f}" if adx is not None else "N/A"

    # ═══ 平仓分支（is_for_pairing=True）：趋势反转 + 价格下行确认 ═══
    if is_for_pairing:
        filter_passed = (not theme_retreated) and (not is_limit_down_locked)
        # 趋势反转判断：偏离转负 / 穿越VWAP / ADX回落（任一即视为上涨趋势结束）
        trend_reversed = False
        if vwap_dev is not None and vwap_dev < 0:
            trend_reversed = True
        elif vwap_dev is not None and abs(vwap_dev) < params.tf_vwap_cross_threshold:
            trend_reversed = True
        elif adx is not None and adx < params.tf_trend_reverse_adx:
            trend_reversed = True
        near_vwap = trend_reversed  # 复用字段语义：趋势反转 ≈ 价格回到反转点
        # 方向确认：价格下行确认趋势反转向下（卖出腿平仓买回，需价格下行获利了结）
        dir_confirmed = _price_trend_confirmed(bars, "down", params.tf_kdj_reverse_bars)
        fired_pairing = []
        if near_vwap:
            fired_pairing.append(f"[平仓-趋势反转] vwap_dev={vwap_dev_str} adx={adx_str} 上涨趋势结束（偏离转负/穿越VWAP/ADX回落）")
        else:
            fired_pairing.append(f"[平仓-趋势反转] vwap_dev={vwap_dev_str} adx={adx_str} 上涨趋势延续（未反转）")
        if dir_confirmed:
            fired_pairing.append(f"[平仓-方向确认] 最近{params.tf_kdj_reverse_bars}根K线价格多数下行（反转向下）")
        else:
            fired_pairing.append(f"[平仓-方向确认] 最近{params.tf_kdj_reverse_bars}根K线价格未下行（仍在冲高）")
        if filter_passed:
            fired_pairing.append("[环境4] 板块未退潮且未跌停封板（可成交）")
        else:
            reason = []
            if theme_retreated:
                reason.append("板块退潮")
            if is_limit_down_locked:
                reason.append("跌停封板")
            fired_pairing.append(f"[环境4] {'+'.join(reason)}（硬否决）")
        return TSignal(
            direction="add",
            rules_fired=fired_pairing,
            rules_score=2 if (near_vwap and dir_confirmed and filter_passed) else 0,
            price=price,
            snapshot=snap,
            layer_scores={"pairing_distance": 1 if near_vwap else 0,
                          "pairing_direction": 1 if dir_confirmed else 0,
                          "filter": 1 if filter_passed else 0},
            trend_context=trend_context,
            trigger_threshold=2,  # 平仓分支走自己的触发逻辑，不走 trigger_threshold
            extreme_score=0,
            confirm_score=0,
            filter_passed=filter_passed,
            extreme_min=params.extreme_min,
            confirm_min=params.confirm_min,
            is_pairing=True,
            pairing_near_vwap=near_vwap,
            pairing_direction_confirmed=dir_confirmed,
        )

    # ═══ J2: 回踩入场（分离模式：趋势确认 + 入场价格确定 分离）═══
    # 设计（v2，2026-07-28）：真正分离"趋势确认"和"入场价格确定"
    #   - 趋势确认（历史）：过去N根K线有过冲高（recent_high > VWAP × (1+min_surge)）
    #     说明趋势存在过，不需要当前K线再次通过4规则投票
    #   - 入场价格确定（当前）：价格回踩到VWAP附近（0 < vwap_dev <= band）
    #     + KDJ.K回落（K < kdj_max，确认是回调而非追高）
    #   - 两者都满足时直接触发买入信号，即使当前K线的4规则投票未通过
    #   - 回踩条件不满足时，走原来的4规则投票逻辑（向后兼容）
    #
    # v1（已废弃）的问题：要求"当前K线同时满足趋势确认+回踩"，但趋势确认时
    # 价格已跑远（vwap_dev大），回踩时趋势确认又已失效（ADX回落），两者很少
    # 同时成立，导致配对数锐减63%、净盈亏下降85%。
    if params.retracement_entry_enabled:
        vwap_val = snap.get("vwap")

        # 条件1（入场价格）：VWAP偏离在上限内（价格回踩到VWAP附近，不追高）
        vwap_dev_ok = (vwap_dev is not None and 0 < vwap_dev <= params.retracement_vwap_band)
        # 条件2（入场价格）：KDJ.K未超买（K已从高位回落）
        kdj_ok = (k_val is not None and k_val < params.retracement_kdj_max)
        # 条件3（趋势确认）：近期有过冲高（趋势存在过，不是横盘）
        lookback = params.retracement_lookback
        recent_bars = bars[-lookback:] if len(bars) >= lookback else bars
        recent_high = max((b.get("high", 0) for b in recent_bars), default=0)
        had_surge = (vwap_val is not None and vwap_val > 0
                     and recent_high > vwap_val * (1 + params.retracement_min_surge))

        retracement_ok = vwap_dev_ok and kdj_ok and had_surge
    else:
        retracement_ok = False  # 未启用时不走回踩分支

    # ── 极值层 项1: VWAP偏离为正（价格在VWAP上方，多头）──
    if vwap_dev is not None and vwap_dev > 0:
        fired.append(f"[极值1] VWAP偏离 {vwap_dev_str} > 0（价格在VWAP上方，多头）")
        extreme_score += 1

    # ── 极值层 项2: ADX确认趋势存在 ──
    if adx is not None and adx > params.tf_adx_threshold:
        fired.append(f"[极值2] ADX={adx_str} > {params.tf_adx_threshold}（趋势确认）")
        extreme_score += 1

    # ── 极值层 项3: KDJ金叉（K > D，动量向上）──
    if k_val is not None and d_val is not None and k_val > d_val:
        fired.append(f"[极值3] KDJ.K={k_val:.1f} > D={d_val:.1f}（金叉，动量向上）")
        extreme_score += 1

    # ── 确认层 项4: 量能放大 ──
    if vol_ratio is not None and vol_ratio > params.tf_vol_ratio_min:
        fired.append(f"[确认4] 量比 {vol_ratio:.2f} > {params.tf_vol_ratio_min}（量能放大）")
        confirm_score += 1

    # ── 环境层 项5: 板块未退潮 + 未跌停封板 ──
    filter_passed = (not theme_retreated) and (not is_limit_down_locked)
    if filter_passed:
        fired.append("[环境5] 板块未退潮且未跌停封板（可成交）")
    else:
        reason = []
        if theme_retreated:
            reason.append("板块退潮")
        if is_limit_down_locked:
            reason.append("跌停封板")
        fired.append(f"[环境5] {'+'.join(reason)}（硬否决）")

    # 趋势上下文（信息记录，不调整阈值）
    fired.append(f"[趋势] {trend_context}（信息记录，趋势跟随不调整阈值）")

    # 计分：总分 = 极值 + 确认 + 过滤
    total_score = extreme_score + confirm_score + (1 if filter_passed else 0)
    if not filter_passed:
        total_score = 0

    # J2: 回踩入场（分离模式）
    # - 回踩条件满足 + 环境层通过 → 直接触发（不需要4规则投票通过）
    #   此时强制提升 extreme_score/confirm_score/total_score 到触发阈值
    #   （否则 TSignal.triggered 会因 extreme_score < extreme_min 而否决）
    # - 回踩条件不满足 → 走原来的4规则投票逻辑
    if params.retracement_entry_enabled and filter_passed:
        if retracement_ok:
            # 回踩触发：强制提升所有分数到触发阈值
            total_score = max(total_score, params.min_rules_to_trigger)
            extreme_score = max(extreme_score, params.extreme_min)
            confirm_score = max(confirm_score, params.confirm_min)
            fired.append(f"[回踩入场-触发] vwap_dev={vwap_dev_str}"
                        f" K={'%.1f' % k_val if k_val else 'N/A'}"
                        f" 近{params.retracement_lookback}根高点偏离VWAP "
                        f"{((recent_high/vwap_val-1)*100):.2f}%（≥{params.retracement_min_surge*100:.1f}%）"
                        f" → 分离模式触发，不依赖当前4规则投票")
        else:
            # 回踩条件不满足，记录原因（走原逻辑）
            reasons = []
            if not vwap_dev_ok:
                reasons.append(f"vwap_dev={vwap_dev_str}需在0~{params.retracement_vwap_band*100:.1f}%")
            if not kdj_ok:
                reasons.append(f"K={'%.1f' % k_val if k_val else 'N/A'}需<{params.retracement_kdj_max}")
            if not had_surge:
                reasons.append(f"近{params.retracement_lookback}根无冲高（需≥{params.retracement_min_surge*100:.1f}%）")
            fired.append(f"[回踩入场-未触发] {', '.join(reasons)} → 走4规则投票")

    # 趋势跟随：不额外加严，trigger_threshold = min_rules_to_trigger
    trigger_threshold = params.min_rules_to_trigger

    return TSignal(
        direction="add",
        rules_fired=fired,
        rules_score=total_score,
        price=price,
        snapshot=snap,
        layer_scores={"extreme": extreme_score, "confirm": confirm_score, "filter": 1 if filter_passed else 0},
        trend_context=trend_context,
        trigger_threshold=trigger_threshold,
        extreme_score=extreme_score,
        confirm_score=confirm_score,
        filter_passed=filter_passed,
        extreme_min=params.extreme_min,
        confirm_min=params.confirm_min,
    )


# ═══════════════════════════════════════════════════════════════
# 市场情绪权重 → 动态触发阈值（P1-1: 接入 adjust_signal_weight）
# ═══════════════════════════════════════════════════════════════
def _apply_weight_to_threshold(base_threshold: int, weight: float) -> int:
    """
    根据市场情绪权重动态调整触发阈值。

    - weight > 1.0 → 放宽触发（降低阈值，用 floor 取整）
    - weight < 1.0 → 收紧触发（提高阈值，用 ceil 取整）
    - weight = 1.0 → 不变

    结果钳制在 [2, 4] 范围内（最低2项即可，最高需全部4项）。
    """
    if weight > 1.0:
        adjusted = math.floor(base_threshold / weight)
    elif weight < 1.0:
        adjusted = math.ceil(base_threshold / weight)
    else:
        adjusted = base_threshold
    return max(2, min(4, adjusted))


# ═══════════════════════════════════════════════════════════════
# 综合评估
# ═══════════════════════════════════════════════════════════════
def evaluate_all_signals(
    bars: list[dict],
    current_price: Optional[float] = None,
    prev_close: Optional[float] = None,
    is_limit_up_locked: bool = False,
    is_limit_down_locked: bool = False,
    theme_retreated: bool = False,
    params: Optional[SignalParams] = None,
    market=None,
    quote_feats: Optional[dict] = None,
) -> dict:
    """
    综合评估减仓/加仓信号，返回两者及推荐方向。

    参数:
      market: MarketSnapshot（可选），用于市场层门控。COLD 市场禁加仓。
      quote_feats: 盘口特征 dict（可选），来自 stock_quote_features.fetch_quote_features。
                   注入后 Layer C 可使用订单流代理指标。

    返回:
    {
        "reduce_signal": TSignal,
        "add_signal": TSignal,
        "recommendation": "reduce" | "add" | "none" | "conflict",
        "snapshot": dict,
        "market_gate_add": dict,   # 加仓门控结果（仅 market 非空时）
        "market_gate_reduce": dict,# 减仓门控结果
    }
    """
    params = params or DEFAULT_PARAMS
    reduce_sig = evaluate_reduce_signal(
        bars, current_price, prev_close, is_limit_up_locked, params, quote_feats
    )
    add_sig = evaluate_add_signal(
        bars, current_price, prev_close, is_limit_down_locked, theme_retreated, params, quote_feats
    )

    # 市场层门控
    market_gate_add = None
    market_gate_reduce = None
    if market is not None:
        market_gate_add = {
            "allowed": market_gate_for_add(market)[0],
            "reason": market_gate_for_add(market)[1],
        }
        market_gate_reduce = {
            "allowed": market_gate_for_reduce(market)[0],
            "reason": market_gate_for_reduce(market)[1],
        }
        # P1-1: 市场情绪加权 → 动态调整触发阈值
        # 权重 > 1.0 放宽触发（降低阈值），权重 < 1.0 收紧触发（提高阈值）
        reduce_weight = adjust_signal_weight(market, "reduce")
        add_weight = adjust_signal_weight(market, "add")
        # P0-7 整改（2026-07-24）：基于"已加严的 trigger_threshold"叠加市场权重，
        # 而非从 base（params.min_rules_to_trigger）重算。
        # 旧实现用 base 重算会覆盖 evaluate_reduce/add_signal 内部按 trend_context
        # 已经 +1 的趋势加严结果（见 strategy.py:418-419 / 604-606），
        # 导致"上升趋势 + COLD 市场"双重作用下趋势保护丢失。
        reduce_sig.trigger_threshold = _apply_weight_to_threshold(
            reduce_sig.trigger_threshold, reduce_weight
        )
        add_sig.trigger_threshold = _apply_weight_to_threshold(
            add_sig.trigger_threshold, add_weight
        )
        market_gate_add["weight"] = add_weight
        market_gate_add["adjusted_threshold"] = add_sig.trigger_threshold
        market_gate_reduce["weight"] = reduce_weight
        market_gate_reduce["adjusted_threshold"] = reduce_sig.trigger_threshold
        # 把门控结果写入信号
        reduce_sig.market_gate = market_gate_reduce
        add_sig.market_gate = market_gate_add

    recommendation = "none"
    reduce_triggered = reduce_sig.triggered
    add_triggered = add_sig.triggered

    # 市场层门控覆盖：COLD 市场强制加仓不触发
    if market_gate_add is not None and not market_gate_add["allowed"]:
        add_triggered = False

    # ── P2: Alpha 连续评分分支（主计划 S2.3）──
    # use_continuous_alpha=True 时，用 alpha_score 替代三层布尔触发
    # use_continuous_alpha=False 时，走原来逻辑（flag=false 逐笔一致，主计划 S5 验收1）
    alpha_info = None
    if params.use_continuous_alpha:
        snap = reduce_sig.snapshot or add_sig.snapshot or {}
        # 计算两个方向的 alpha_score
        snap_reduce = dict(snap, _direction="reduce")
        snap_add = dict(snap, _direction="add")
        alpha_reduce, sub_reduce = compute_alpha_score(snap_reduce, params)
        alpha_add, sub_add = compute_alpha_score(snap_add, params)
        alpha_info = {
            "alpha_reduce": round(alpha_reduce, 2),
            "alpha_add": round(alpha_add, 2),
            "sub_reduce": sub_reduce,
            "sub_add": sub_add,
            "threshold": params.alpha_threshold_open,
        }
        # 用 alpha_score 替代布尔触发
        reduce_triggered = alpha_reduce >= params.alpha_threshold_open
        add_triggered = alpha_add >= params.alpha_threshold_open
        # 市场层门控仍然生效
        if market_gate_add is not None and not market_gate_add["allowed"]:
            add_triggered = False

    if reduce_triggered and add_triggered:
        recommendation = "conflict"
    elif reduce_triggered:
        recommendation = "reduce"
    elif add_triggered:
        recommendation = "add"

    result = {
        "reduce_signal": reduce_sig,
        "add_signal": add_sig,
        "recommendation": recommendation,
        "snapshot": reduce_sig.snapshot or add_sig.snapshot,
        "market_gate_add": market_gate_add,
        "market_gate_reduce": market_gate_reduce,
    }
    if alpha_info is not None:
        result["alpha_info"] = alpha_info
    return result


# ═══════════════════════════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # 测试 1：构造"冲高缩量"场景 → 应触发减仓信号
    print("=== Test 1: 冲高缩量（应触发 reduce）===")
    test_bars = []
    base_price = 10.00
    for i in range(40):
        if i < 20:
            p = base_price + i * 0.01
            vol = 10000 + i * 100
        else:
            p = base_price + 0.20 + (i - 20) * 0.015  # 冲高
            vol = 8000 - (i - 20) * 400  # 缩量
        test_bars.append({
            "time": f"09:{31 + i // 60}:{(31 + i) % 60:02d}",
            "open": p, "high": p + 0.02, "low": p - 0.02,
            "close": p + 0.005, "volume": vol, "amount": (p + 0.005) * vol,
        })

    result = evaluate_all_signals(test_bars, current_price=10.35, prev_close=10.00)
    print(f"  recommendation: {result['recommendation']}")
    print(f"  reduce_score: {result['reduce_signal'].rules_score}/4 — {result['reduce_signal'].rules_fired}")
    print(f"  reduce_layers: {result['reduce_signal'].layer_scores}")
    print(f"  trend_context: {result['reduce_signal'].trend_context}")
    print(f"  add_score: {result['add_signal'].rules_score}/4 — {result['add_signal'].rules_fired}")

    # 测试 2：构造"下探地量企稳"场景 → 应触发加仓信号
    print("\n=== Test 2: 下探地量企稳（应触发 add）===")
    test_bars2 = []
    base_price = 10.00
    for i in range(40):
        if i < 20:
            p = base_price - i * 0.015  # 下探
            vol = 10000 + i * 200  # 放量下跌
        else:
            p = base_price - 0.30 + (i - 20) * 0.002  # 企稳微升
            vol = 4000 - (i - 20) * 200  # 持续缩量
        test_bars2.append({
            "time": f"09:{31 + i // 60}:{(31 + i) % 60:02d}",
            "open": p, "high": p + 0.02, "low": p - 0.02,
            "close": p + 0.001, "volume": max(vol, 100), "amount": (p + 0.001) * max(vol, 100),
        })

    result2 = evaluate_all_signals(test_bars2, current_price=9.70, prev_close=10.00)
    print(f"  recommendation: {result2['recommendation']}")
    print(f"  add_score: {result2['add_signal'].rules_score}/4 — {result2['add_signal'].rules_fired}")
    print(f"  add_layers: {result2['add_signal'].layer_scores}")
    print(f"  trend_context: {result2['add_signal'].trend_context}")
    print(f"  reduce_score: {result2['reduce_signal'].rules_score}/4 — {result2['reduce_signal'].rules_fired}")

    # 测试 3：横盘无信号
    print("\n=== Test 3: 横盘（应为 none）===")
    test_bars3 = []
    for i in range(40):
        p = 10.00 + (i % 5 - 2) * 0.001
        test_bars3.append({
            "time": f"09:{31 + i // 60}:{(31 + i) % 60:02d}",
            "open": p, "high": p + 0.01, "low": p - 0.01,
            "close": p, "volume": 10000, "amount": p * 10000,
        })
    result3 = evaluate_all_signals(test_bars3, current_price=10.00, prev_close=10.00)
    print(f"  recommendation: {result3['recommendation']}")
    print(f"  reduce_score: {result3['reduce_signal'].rules_score}, add_score: {result3['add_signal'].rules_score}")

    # 测试 4：市场层门控（COLD 禁加仓）
    print("\n=== Test 4: 市场层门控（COLD 禁加仓）===")
    try:
        from market_layer import MarketSnapshot
        cold_market = MarketSnapshot(
            up_limit_count=10, down_limit_count=80, up_ratio=20,
            timestamp="2026-07-22T10:00:00",
        )
        # 用下探企稳数据 + COLD 市场 → add 应被门控拦截
        result4 = evaluate_all_signals(test_bars2, current_price=9.70, prev_close=10.00, market=cold_market)
        print(f"  recommendation: {result4['recommendation']} (COLD 市场应为 none 或 reduce)")
        print(f"  market_gate_add: {result4['market_gate_add']}")
        print(f"  add_triggered(门控前): {result4['add_signal'].triggered}")
    except ImportError:
        print("  [SKIP] market_layer 不可用")


# ═══════════════════════════════════════════════════════════════
# P2: Alpha 连续评分 stub（use_continuous_alpha=False 时不调用）
# ═══════════════════════════════════════════════════════════════
# 主计划 S2.3: 布尔判断 -> 连续 score 映射
# AlphaScore = sum(wi * scorei), clip [0, 100]
# P2 阶段实现, 当前为 stub (返回 0, 不影响 flag=false 路径)
# P1 测量层产出 IC 报告后, 根据 IC 显著性决定各 score_i 的保留/丢弃

def compute_alpha_score(snap, params):
    """
    计算连续 Alpha 评分 (P2 实现).

    主计划 S2.3: AlphaScore = Sum(wi * scorei) / Sum(wi), clip [0, 100]
    各子信号评分函数见 strategy_alpha.py (基于主计划 S1.2 映射公式).

    use_continuous_alpha=False 时不调用此函数 (主计划 S5 验收1: flag=false 逐笔一致).
    use_continuous_alpha=True 时替代布尔三层触发 (extreme/confirm/filter).

    :param snap: 特征快照 (含 vwap_dev, rsi, kdj_k, adx, vol_ratio, atr 等)
    :param params: SignalParams (含 alpha_weight_* 权重)
    :return: (alpha_score 0-100, sub_scores dict)
    """
    from .strategy_alpha import compute_alpha_score as _impl
    return _impl(snap, params)
