"""
V3 Strategy 层
================

strategy_v3.py 作为 V3 架构的门面入口（Facade），保持与 strategy.py 的
evaluate_* 入口签名兼容，内部委托 V3 Engine 链。

G1 阶段：仅 Alpha 评分聚合器就位，evaluate_* 仍走原 strategy.py 逻辑。
         use_continuous_alpha=True 时，alpha 评分委托 score/alpha_score.py。
"""
