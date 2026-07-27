"""
regime 层 — 日线 Regime Engine（二期 Stage E）
=============================================
@deprecated Stage E 验证未通过，本模块已废弃，不再接入决策层。
保留代码作为失败记录，新增代码不应依赖本模块。

废弃原因（2026-07-27 Stage D 归因确认）：
  1. Stage E 验证 FAIL：82.1% 交易日被拦，被拦交易中仅 12.93% 原本亏损
     （方向与预期相反）。
  2. v2 修复（去掉 close>MA20 方向约束）后重跑，被拦交易亏损比例仍仅 14.15%，
     验收 E-2 仍未通过。
  3. Stage D 步骤3 独立相关性分析：daily_adx 与当天 5min 做T净盈亏的
     spearman 相关系数 +0.0104（近零），五分组对比无单调关系，
     ADX 信号信息量不足。
  4. Stage D 步骤1 Mann-Whitney U 检验：盈利组 vs 亏损组的 ADX 均值
     p=0.2123（不显著），Cliff's delta=+0.146（可忽略）。
     ADX 不具备区分盈亏股票的能力。

替代方案：Stage D 步骤3 改用 60日日均振幅作为选股筛选条件
（screener.py 的 min_amplitude_long 参数），样本外验证 6/6 全通过。

主计划二期升级 §1：逐日动态状态判定，只输出离散三态 Enum，不输出连续分数。

判据（只用2个信号，不多）：
  1. 日线 ADX（daily_adx）— 复用 features.dmi 算法，输入换成日K
  2. 是否站上/跌破 MA20（日线）

判定规则：
  - TRENDING : daily_adx > 阈值（趋势盘，不区分上下方向——策略是双向趋势跟随）
  - RANGING  : daily_adx < 阈值 且 |close - MA20| / MA20 < band（震荡盘 + 价格在均线附近）
  - NO_TRADE : 数据不足 / 停牌 / 一字板 / 不明朗（adx低但远离MA20）

历史变更：
  - v1（Stage E初版）：TRENDING 要求 close > MA20，导致下降趋势（close<MA20, adx高）
    被误判为 NO_TRADE。Stage E 验证 FAIL：82.1% 交易日被拦，被拦交易中仅 12.93%
    原本亏损（方向与预期相反）。
  - v2（本轮修复）：去掉 close > MA20 方向约束，只保留 daily_adx > 阈值。
    RANGING/NO_TRADE 判据不动，ADX 阈值不动。只改方向不对称这一处。

命名约定（E-3 验收）：
  - daily_adx         : 日线 ADX（本模块算）
  - tf_adx_threshold  : strategy.py 里 5min bar 的 ADX 阈值（已有，不混用）

接入方式（Stage E）：
  - backtest_multi_day 新增 regime_filter_enabled 参数
  - 当天 regime=NO_TRADE 时跳过 backtest_single_day（物理上不调用信号评估）
  - TRENDING/RANGING 的差异化处理留到 Stage F+，本阶段都放行

严格因果：算第 D 日的 regime，只用截至 D-1 日收盘的日K序列（D 日开盘前决策）。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from .features import dmi as _dmi, ma as _ma


class DailyRegime(str, Enum):
    """日线 Regime 三态（str Enum 便于序列化）。"""
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    NO_TRADE = "NO_TRADE"


# ═══════════════════════════════════════════════════════════════
# 默认参数（可由 thresholds.yaml 覆盖，先内置，Stage E 验证后再迁 yaml）
# ═══════════════════════════════════════════════════════════════
class RegimeParams:
    """Regime 判定参数。

    命名明确 daily_adx_threshold，与 strategy.py 的 tf_adx_threshold（5min）区分。
    """
    daily_adx_threshold: float = 25.0   # 日线 ADX 阈值（与 features.dmi period=14 对齐）
    ma_period: int = 20                 # 日线 MA20
    ranging_band: float = 0.02          # |close-MA20|/MA20 < 2% 视为"在均线附近"
    adx_period: int = 14                # DMI 周期（与 features.dmi 默认一致）
    # features.dmi 需 bars >= 2*period 才能算出 ADX（period+1 根算 DM/TR，
    # 再 EMA(period) 得 period 个值，再 EMA(period) 算 ADX 需 period 个 DX）
    # 故 min_daily_klines = 2 * adx_period + buffer = 30
    min_daily_klines: int = 30


# ═══════════════════════════════════════════════════════════════
# 日K合成（从 5min bars 聚合成日K）
# ═══════════════════════════════════════════════════════════════
def synthesize_daily_klines(daily_bars_5min: dict[str, list[dict]]) -> list[dict]:
    """从 5min daily_bars 合成日K列表（按日期升序）。

    输入: {date_str: [5min bars]}
    输出: [{"date": str, "open": float, "high": float, "low": float,
            "close": float, "volume": float}, ...] 按日期升序

    每日聚合规则：
      open  = 当日首根 5min bar 的 open
      high  = 当日所有 5min bar 的 high 最大值
      low   = 当日所有 5min bar 的 low 最小值
      close = 当日末根 5min bar 的 close
      volume= 当日所有 5min bar 的 volume 之和
    """
    klines: list[dict] = []
    for date_str in sorted(daily_bars_5min.keys()):
        bars = daily_bars_5min[date_str]
        if not bars:
            continue
        opens = [b.get("open", 0) for b in bars if b.get("open", 0) > 0]
        highs = [b.get("high", 0) for b in bars if b.get("high", 0) > 0]
        lows = [b.get("low", 0) for b in bars if b.get("low", 0) > 0]
        closes = [b.get("close", 0) for b in bars if b.get("close", 0) > 0]
        vols = [b.get("volume", 0) for b in bars]
        if not (opens and highs and lows and closes):
            continue
        klines.append({
            "date": date_str,
            "open": opens[0],
            "high": max(highs),
            "low": min(lows),
            "close": closes[-1],
            "volume": sum(vols),
        })
    return klines


# ═══════════════════════════════════════════════════════════════
# Regime 判定
# ═══════════════════════════════════════════════════════════════
def classify_daily_regime(
    daily_klines_up_to_yesterday: list[dict],
    params: Optional[RegimeParams] = None,
) -> DailyRegime:
    """判定今日的 regime（用截至昨日收盘的日K序列）。

    严格因果：只看 daily_klines_up_to_yesterday（不含今日），保证今日开盘前可决策。

    判据：
      1. 数据不足 / 停牌 / 一字板 → NO_TRADE
      2. daily_adx > 阈值 → TRENDING（不区分上下方向，策略是双向趋势跟随）
      3. daily_adx < 阈值 且 |close-MA20|/MA20 < band → RANGING
      4. 其他（adx低但远离MA20，不明朗）→ NO_TRADE

    参数:
      daily_klines_up_to_yesterday: 截至昨日收盘的日K列表（升序），每项含
        {date, open, high, low, close, volume}
      params: RegimeParams，None 用默认值

    返回: DailyRegime 枚举
    """
    p = params or RegimeParams()

    # ── 1. 数据不足 ──
    if len(daily_klines_up_to_yesterday) < p.min_daily_klines:
        return DailyRegime.NO_TRADE

    # ── 2. 异常：停牌 / 一字板（用最近一日日K判定）──
    last = daily_klines_up_to_yesterday[-1]
    last_close = last.get("close", 0)
    last_high = last.get("high", 0)
    last_low = last.get("low", 0)
    last_vol = last.get("volume", 0)

    if last_close <= 0 or last_high <= 0 or last_low <= 0:
        return DailyRegime.NO_TRADE
    # 停牌：成交量为0
    if last_vol <= 0:
        return DailyRegime.NO_TRADE
    # 一字板：最高=最低（且非0）
    if last_high == last_low:
        return DailyRegime.NO_TRADE

    # ── 3. 算日线 ADX（复用 features.dmi，输入日K）──
    # features.dmi 接受 bars list[dict]，需要 high/low/close 字段
    pdi, mdi, daily_adx = _dmi(daily_klines_up_to_yesterday, period=p.adx_period)
    if daily_adx is None:
        return DailyRegime.NO_TRADE

    # ── 4. 算 MA20（复用 features.ma）──
    ma20 = _ma(daily_klines_up_to_yesterday, period=p.ma_period)
    if ma20 is None or ma20 <= 0:
        return DailyRegime.NO_TRADE

    # ── 5. 判定 ──
    dev_ratio = abs(last_close - ma20) / ma20

    if daily_adx > p.daily_adx_threshold:
        return DailyRegime.TRENDING
    if daily_adx < p.daily_adx_threshold and dev_ratio < p.ranging_band:
        return DailyRegime.RANGING
    # 不明朗：adx低但远离MA20
    return DailyRegime.NO_TRADE


# ═══════════════════════════════════════════════════════════════
# 批量预算：给定日K序列，算每个交易日的 regime
# ═══════════════════════════════════════════════════════════════
def precompute_regimes_for_dates(
    daily_klines: list[dict],
    target_dates: list[str],
    params: Optional[RegimeParams] = None,
) -> dict[str, DailyRegime]:
    """为目标日期预计算 regime。

    对每个 target_date D，用 daily_klines 里 date < D 的子序列算 D 的 regime。
    严格因果：D 日的 regime 只用 D-1 及之前的日K。

    参数:
      daily_klines: 完整日K列表（升序），每项含 date 字段
      target_dates: 需要算 regime 的日期列表
      params: RegimeParams

    返回: {date_str: DailyRegime}
    """
    p = params or RegimeParams()
    # 按 date 建索引
    sorted_klines = sorted(daily_klines, key=lambda x: x.get("date", ""))
    regimes: dict[str, DailyRegime] = {}
    for d in target_dates:
        # 截至昨日（date < d）的日K
        up_to_yesterday = [k for k in sorted_klines if k.get("date", "") < d]
        regimes[d] = classify_daily_regime(up_to_yesterday, p)
    return regimes
