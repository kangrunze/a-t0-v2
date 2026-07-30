"""
V3 核心数据模型
================

平台化设计的载体：
  - FeatureVector: 各 Engine 产出的特征向量容器（只读快照）
  - TradeCandidate: Opportunity Engine 产出的交易候选（含子评分 + 综合评分）

设计原则：
  1. 不可变（frozen=True），避免 Engine 间意外共享状态
  2. 字段全部 Optional，各 Engine 只填充自己负责的维度
  3. 向后兼容：新增 Engine 时只需新增字段，不影响旧 Engine
"""
from .feature_vector import FeatureVector
from .trade_candidate import TradeCandidate

__all__ = ["FeatureVector", "TradeCandidate"]
