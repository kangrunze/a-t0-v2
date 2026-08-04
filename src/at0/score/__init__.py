"""
V3 评分层
==========

Alpha 连续评分聚合器，替代旧的 3/4 布尔规则触发。

七维度加权（用户方案 §十二）：
  Trend        25%
  Wave         20%
  Support      15%
  Momentum     15%
  Liquidity    10%
  ExpectedMove 10%
  Risk          5%

G1 阶段：子评分用现有 features 计算（占位实现）。
G2-G7：逐步替换为独立 Engine 产出的子评分。

注意：AlphaScoreAggregator 已被 compute_alpha_score_v3 的 inline 聚合替代，
保留在 alpha_score.py 中仅作参考，不再从本模块导出。
"""
from .alpha_score import compute_alpha_score_v3

__all__ = ["compute_alpha_score_v3"]
