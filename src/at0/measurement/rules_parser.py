"""
measurement.rules_parser
========================
rules_fired 文案解析（正则提取 vwap_dev / adx / vol_ratio / trend_context 等字段）。

从 diag_fixed_stop_entry_quality.py（parse_entry_factors）和
diag_exit_trigger_attribution.py（parse_vwap_adx）中抽取的公共解析逻辑。

两种解析模式：
  - parse_entry_factors: 开仓腿 rules_fired，提取 vwap_dev/adx/vol_ratio/trend_context
  - parse_exit_factors:  平仓腿 rules_fired（[平仓-趋势反转]消息），提取 vwap_dev/adx/pairing_direction

注意：
  - 本模块是纯抽取，不改变任何解析逻辑
  - 正则和行为与原脚本完全一致
"""
from __future__ import annotations

import re
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 开仓腿 rules_fired 解析（复用 diag_fixed_stop_entry_quality.py 逻辑）
# ═══════════════════════════════════════════════════════════════

def parse_entry_factors(rules_fired: list[str]) -> dict:
    """
    从开仓腿 rules_fired 解析 vwap_dev/adx/vol_ratio/trend_context。
    返回 dict，缺失字段为 None。

    解析的文案格式（与 strategy.py 开仓规则消息一致）：
      - "VWAP偏离 +1.23%"  → vwap_dev = 0.0123
      - "ADX=35.5"         → adx = 35.5
      - "量比 2.3"          → vol_ratio = 2.3
      - "[趋势] trend_up"  → trend_context = "trend_up"
    """
    text = " ".join(rules_fired) if rules_fired else ""
    result = {"vwap_dev": None, "adx": None, "vol_ratio": None, "trend_context": None}

    m = re.search(r"VWAP偏离 ([+-]?\d+\.\d+)%", text)
    if m:
        result["vwap_dev"] = float(m.group(1)) / 100

    m = re.search(r"ADX=(\d+\.\d+)", text)
    if m:
        result["adx"] = float(m.group(1))

    m = re.search(r"量比 (\d+\.\d+)", text)
    if m:
        result["vol_ratio"] = float(m.group(1))

    m = re.search(r"\[趋势\] (\w+)", text)
    if m:
        result["trend_context"] = m.group(1)

    return result


# ═══════════════════════════════════════════════════════════════
# 平仓腿 rules_fired 解析（复用 diag_exit_trigger_attribution.py 逻辑）
# ═══════════════════════════════════════════════════════════════

def parse_exit_factors(rules_fired: list[str]) -> tuple[
    Optional[float], Optional[float], Optional[str]
]:
    """
    从平仓腿 rules_fired 的 [平仓-趋势反转] 消息里解析 vwap_dev 和 adx 数值。

    消息格式：[平仓-趋势反转] vwap_dev=-1.72% adx=43.6 上涨趋势结束（...）

    返回 (vwap_dev_float, adx_float, pairing_direction)：
      - vwap_dev: 浮点数（如 -0.0172），缺失为 None
      - adx: 浮点数（如 43.6），缺失为 None
      - pairing_direction:
          "sell" — 上涨趋势结束（买入腿平仓，卖出获利了结后买回）
          "buy"  — 下跌趋势结束（卖出腿平仓，买入后卖出获利了结）
          None   — 无法判断方向
      - 若 rules_fired 中无 [平仓-趋势反转] 消息，返回 (None, None, None)
    """
    for msg in rules_fired:
        if "[平仓-趋势反转]" not in msg:
            continue
        # 解析 vwap_dev
        m_vwap = re.search(r"vwap_dev=([+-]?\d+\.?\d*)%", msg)
        vwap_dev = float(m_vwap.group(1)) / 100 if m_vwap else None
        # 解析 adx
        m_adx = re.search(r"adx=(\d+\.?\d*)", msg)
        adx = float(m_adx.group(1)) if m_adx else None
        # 判断方向（上涨趋势结束=sell平仓买回，下跌趋势结束=buy平仓卖出）
        if "上涨趋势结束" in msg:
            pairing_direction = "sell"
        elif "下跌趋势结束" in msg:
            pairing_direction = "buy"
        else:
            pairing_direction = None
        return vwap_dev, adx, pairing_direction
    return None, None, None
