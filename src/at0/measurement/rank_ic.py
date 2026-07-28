"""
轻量版 RankIC 计算
==================
输入：alpha 因子在开仓时刻的取值 + 该笔交易的 realized_pnl
输出：Spearman 秩相关系数（RankIC）

与 ic_analysis.pyc 的 compute_alpha_decay（横截面多 horizon Alpha Decay）不同，
本模块聚焦"逐笔交易"场景：开仓时刻因子值 vs 该笔平仓后的 realized_pnl，
用于评估因子对单笔交易盈亏的秩预测力。

Stage B 当初提出但一直未正式实现的部分，2026-07-28 Stage H 步骤4 补上。
纯 .py 实现，不经 __init__.py 的 _load_pyc，标准 import 直接可用。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Union

import pandas as pd


@dataclass
class RankICResult:
    """单因子逐笔 RankIC 计算结果。"""
    rank_ic: float      # Spearman 秩相关系数 ∈ [-1, 1]，正值=因子正向预测盈利
    p_value: float      # 双尾 p 值，<0.05 视为显著
    n: int              # 有效样本数（剔除 NaN 后）

    def to_dict(self) -> dict:
        return asdict(self)


def compute_rank_ic(
    factor_values: Union[list[float], pd.Series],
    realized_pnls: Union[list[float], pd.Series],
    *,
    min_samples: int = 30,
) -> Optional[RankICResult]:
    """计算因子值与交易盈亏的 Spearman 秩相关（RankIC）。

    参数
    ----
    factor_values : 开仓时刻的因子取值（如 vwap_dev / adx / vol_ratio）。
    realized_pnls : 对应交易的 realized_pnl（已平仓）。
                    长度需与 factor_values 一一对应。
    min_samples   : 最小样本数，低于此返回 None（不可结论）。

    返回
    ----
    RankICResult 或 None（样本不足或全为常数列）。

    说明
    ----
    Spearman = Pearson(x_rank, y_rank)，对异常值不敏感、不要求数值线性关系。
    实现用 pandas rank() + pearson，不依赖 scipy（pandas method='spearman' 内部
    也 lazy import scipy，本环境未安装）。p 值用 t 统计量→正态近似（大样本下
    t≈z），小样本下略显乐观，仅作显著性参考。
    """
    s = pd.DataFrame({
        "factor": pd.Series(factor_values, dtype="float64"),
        "pnl": pd.Series(realized_pnls, dtype="float64"),
    }).dropna()

    n = len(s)
    if n < min_samples:
        return None

    # 常数列无法计算相关（std=0），corr 返回 NaN
    if s["factor"].std() == 0 or s["pnl"].std() == 0:
        return None

    # Spearman = Pearson(rank(x), rank(y))，默认 method='pearson' 不依赖 scipy
    ranked = s.rank()
    rank_ic = float(ranked["factor"].corr(ranked["pnl"]))

    # p 值：t 统计量 → 正态近似（标准库，无需 scipy）
    from math import sqrt
    from statistics import NormalDist
    if abs(rank_ic) >= 1.0:
        p_value = 0.0
    else:
        df = n - 2
        t_stat = rank_ic * sqrt(df / (1.0 - rank_ic * rank_ic))
        p_value = 2.0 * (1.0 - NormalDist().cdf(abs(t_stat)))

    return RankICResult(rank_ic=rank_ic, p_value=p_value, n=n)
