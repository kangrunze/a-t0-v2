"""
V3 Engine 层
==============

各 Engine 独立可插拔，统一接口：
  score(bars, snap, direction) -> float  (0~100)

G1-G7 各 Engine：
  - G2: SupportEngine    — 动态支撑/阻力评分
  - G3: OpportunityEngine — 综合机会评分（非线性组合）
  - G4: WaveEngine       — 波浪位置评分
  - G5: ExpectedMoveEngine — 未来收益预测
  - G6: ExecutionEngine  — 执行层评分
  - G7: RegimeEngine     — 市场状态识别
  - RiskEngine           — 风险评分（Kelly sizing 支持）
"""
from .base import BaseEngine
from .support_engine import SupportEngine
from .opportunity_engine import OpportunityEngine
from .wave_engine import WaveEngine
from .expected_move_engine import ExpectedMoveEngine
from .execution_engine import ExecutionEngine
from .regime_engine import RegimeEngine
from .risk_engine import RiskEngine
from .hold_confidence_engine import HoldConfidenceEngine, check_trend_failure

__all__ = [
    "BaseEngine",
    "SupportEngine",
    "OpportunityEngine",
    "WaveEngine",
    "ExpectedMoveEngine",
    "ExecutionEngine",
    "RegimeEngine",
    "RiskEngine",
    "HoldConfidenceEngine",
    "check_trend_failure",
]
