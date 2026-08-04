"""
IC / RankIC / Alpha Decay 分析（P1 核心）

IC = corr(score_t, forward_return_{t+k})
RankIC 用秩相关抗异常值
Alpha Decay 画 k=1/3/5/10/20 的 IC 衰减曲线
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
from scipy.stats import pearsonr, spearmanr


@dataclass
class ICSeries:
    """单 horizon 的 IC 计算结果。"""
    pearson: float      # Pearson 线性相关系数
    spearman: float     # Spearman 秩相关系数（RankIC）
    n: int              # 有效样本数

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ICReport:
    """多 horizon Alpha Decay 分析报告。"""
    signal_name: str
    ic_by_horizon: dict[int, ICSeries]  # {horizon: ICSeries}
    alpha_decay: list[float]            # 各 horizon 的 Spearman IC 值（衰减曲线）

    def to_dict(self) -> dict:
        return {
            "signal_name": self.signal_name,
            "ic_by_horizon": {str(k): v.to_dict() for k, v in self.ic_by_horizon.items()},
            "alpha_decay": self.alpha_decay,
        }


def compute_ic(
    scores: list[float],
    forward_returns: list[float],
) -> ICSeries:
    """计算 Pearson 和 Spearman 相关系数（IC / RankIC）。

    参数
    ----
    scores : 因子值序列
    forward_returns : 前瞻收益序列（与 scores 一一对应）

    返回
    ----
    ICSeries 对象，含 pearson / spearman / n。

    说明
    ----
    - Pearson IC = corr(score, forward_return)
    - Spearman RankIC = corr(rank(score), rank(forward_return))
    - 样本数 < 3 或常数列时返回 0.0 的相关系数（不可结论）。
    """
    n = len(scores)
    if n < 3:
        return ICSeries(pearson=0.0, spearman=0.0, n=n)

    # 转为 numpy 数组并过滤 NaN
    x = np.asarray(scores, dtype=np.float64)
    y = np.asarray(forward_returns, dtype=np.float64)
    mask = ~(np.isnan(x) | np.isnan(y))
    x = x[mask]
    y = y[mask]
    n = len(x)

    if n < 3:
        return ICSeries(pearson=0.0, spearman=0.0, n=n)

    # 常数列无法计算相关
    if np.std(x) == 0 or np.std(y) == 0:
        return ICSeries(pearson=0.0, spearman=0.0, n=n)

    try:
        p, _ = pearsonr(x, y)
        s, _ = spearmanr(x, y)
    except (ValueError, RuntimeError):
        p, s = 0.0, 0.0

    return ICSeries(
        pearson=0.0 if np.isnan(p) else float(p),
        spearman=0.0 if np.isnan(s) else float(s),
        n=n,
    )


def compute_alpha_decay(
    scores: list[float],
    returns_by_horizon: dict[int, list[float]],
    signal_name: str = "",
    horizons: Optional[list[int]] = None,
) -> ICReport:
    """计算多 horizon 的 IC 衰减（Alpha Decay）。

    对每个 horizon 计算 scores 与对应前瞻收益的 Spearman RankIC，
    返回 ICReport 含 ic_by_horizon 字典和 alpha_decay 衰减曲线。

    参数
    ----
    scores : 因子值序列（所有 horizon 共享同一组 scores）
    returns_by_horizon : {horizon: [forward_returns]} 各周期前瞻收益
    signal_name : 信号名称（用于报告标识）
    horizons : 需要计算的 horizon 列表；为 None 时使用
               returns_by_horizon 的键升序排列

    返回
    ----
    ICReport 对象。
    """
    if horizons is None:
        horizons = sorted(returns_by_horizon.keys())

    ic_by_horizon: dict[int, ICSeries] = {}
    alpha_decay: list[float] = []

    for h in horizons:
        fwd_rets = returns_by_horizon.get(h, [])
        ic = compute_ic(scores, fwd_rets)
        ic_by_horizon[h] = ic
        alpha_decay.append(ic.spearman)

    return ICReport(
        signal_name=signal_name,
        ic_by_horizon=ic_by_horizon,
        alpha_decay=alpha_decay,
    )