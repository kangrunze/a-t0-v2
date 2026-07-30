"""
Qlib Dataset 工具 — 因子 IC 筛选 + 时间切分验证
================================================

V3 G1 前置工作：全因子 IC 筛选。
  - 复用 at0.measurement.rank_ic（逐笔 RankIC）扩展为横截面 IC
  - 输出每个因子在 valid segment 的 IC/ICIR，权重决策依据
  - 符合 memory 硬约束："Alpha 评分精简时仅通过 thresholds.yaml 设置权重为 0，不删除代码"
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class FactorICResult:
    """单因子 IC 分析结果。"""
    factor_name: str
    ic_mean: float          # 平均 IC（Spearman）
    ic_std: float           # IC 标准差
    icir: float             # IC / IC_STD（信息比率）
    ic_positive_ratio: float  # IC > 0 的比例
    n_periods: int          # 计算周期数
    p_value: float          # 显著性 p 值（近似）

    def to_dict(self) -> dict:
        return asdict(self)


def compute_cross_section_ic(
    feature_df: pd.DataFrame,
    label_series: pd.Series,
    freq: str = "D",
) -> dict[str, FactorICResult]:
    """计算横截面 IC（每个时间截面上，因子值 vs 未来收益的 Spearman 相关）。

    参数:
        feature_df: MultiIndex(instrument, datetime) × 因子列
        label_series: MultiIndex(instrument, datetime) → label 值
        freq: 重采样频率，'D'=日度横截面（推荐），'W'=周度

    返回: {factor_name: FactorICResult}
    """
    # 对齐
    aligned = feature_df.copy()
    aligned["_label_"] = label_series
    aligned = aligned.dropna(subset=["_label_"])

    # 按日期分组做横截面 Spearman
    date_key = aligned.index.get_level_values("datetime").normalize()

    results: dict[str, FactorICResult] = {}
    factor_cols = [c for c in aligned.columns if c != "_label_"]

    for col in factor_cols:
        sub = aligned[[col, "_label_"]].dropna()
        if len(sub) < 30:
            continue

        # 按日分组 Spearman
        ic_series = sub.groupby(date_key).apply(
            lambda g: _safe_spearman(g[col].values, g["_label_"].values)
        ).dropna()

        if len(ic_series) < 5:
            continue

        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std())
        icir = ic_mean / ic_std if ic_std > 1e-12 else float("nan")
        pos_ratio = float((ic_series > 0).mean())

        # t 检验近似 p 值
        if ic_std > 1e-12 and len(ic_series) > 2:
            t_stat = ic_mean / (ic_std / np.sqrt(len(ic_series)))
            # 正态近似 p 值（双尾）
            from math import erf, sqrt
            p = 2 * (1 - 0.5 * (1 + erf(abs(t_stat) / sqrt(2))))
        else:
            p = 1.0

        results[col] = FactorICResult(
            factor_name=col,
            ic_mean=ic_mean,
            ic_std=ic_std,
            icir=icir,
            ic_positive_ratio=pos_ratio,
            n_periods=len(ic_series),
            p_value=float(p),
        )

    return results


def _safe_spearman(a: np.ndarray, b: np.ndarray) -> float:
    """安全 Spearman，常数列或样本不足返回 NaN。"""
    if len(a) < 5:
        return float("nan")
    if np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    try:
        from scipy.stats import spearmanr
        r, _ = spearmanr(a, b)
        return float(r) if not np.isnan(r) else float("nan")
    except ImportError:
        # pandas fallback
        ra = pd.Series(a).rank()
        rb = pd.Series(b).rank()
        return float(np.corrcoef(ra, rb)[0, 1])


def rank_factors_by_ic(ic_results: dict[str, FactorICResult]) -> pd.DataFrame:
    """按 |ICIR| 降序排列因子，输出 DataFrame。"""
    rows = [r.to_dict() for r in ic_results.values()]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["abs_icir"] = df["icir"].abs()
    return df.sort_values("abs_icir", ascending=False).reset_index(drop=True)


def suggest_alpha_weights(
    ic_results: dict[str, FactorICResult],
    min_abs_icir: float = 0.3,
    max_weight: float = 0.30,
) -> dict[str, float]:
    """根据 IC 结果建议 Alpha 评分权重。

    策略：
      - |ICIR| < min_abs_icir 或 p > 0.1 → 权重 0（memory 约束：不删代码只设权重 0）
      - 其余按 ICIR 正比归一化，上限 max_weight
    """
    eligible = {
        name: r for name, r in ic_results.items()
        if abs(r.icir) >= min_abs_icir and r.p_value < 0.1
    }
    if not eligible:
        return {name: 0.0 for name in ic_results}

    # 按 ICIR 绝对值正比分配，符号决定方向（负向因子用负权重）
    total_abs = sum(abs(r.icir) for r in eligible.values())
    if total_abs < 1e-12:
        return {name: 0.0 for name in ic_results}

    weights = {}
    for name in ic_results:
        if name in eligible:
            r = eligible[name]
            raw = r.icir / total_abs * (1.0 if r.icir > 0 else -1.0)
            # 截断到 [-max_weight, max_weight]
            weights[name] = float(np.clip(raw, -max_weight, max_weight))
        else:
            weights[name] = 0.0

    return weights


__all__ = [
    "FactorICResult",
    "compute_cross_section_ic",
    "rank_factors_by_ic",
    "suggest_alpha_weights",
]
