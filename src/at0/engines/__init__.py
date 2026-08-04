"""
V3 Engine 层
==============

各 Engine 独立可插拔，统一接口：
  score(bars, snap, direction) -> float  (0~100)

活跃 Engine（已在 _get_engines() 中注册）：
  - G2: SupportEngine    — 动态支撑/阻力评分
  - G4: WaveEngine       — 波浪位置评分
  - G5: ExpectedMoveEngine — 未来收益预测
  - G7: RegimeEngine     — 市场状态识别
  - RiskEngine           — 风险评分（Kelly sizing 支持）

其他 Engine（决策层内直接实例化，不在 _get_engines 注册表中）：
  - HoldConfidenceEngine — 持仓信心评分
  - PredictiveExitEngine — 趋势衰竭预测退出
  - DecisionEngineV4     — 统一决策层

废弃（保留文件但不导出，待清理）：
  - G3: OpportunityEngine — 综合机会评分（未接线，被 DecisionEngineV4 替代）
  - G6: ExecutionEngine    — 执行层评分（被 RiskEngine 替代）
"""
from .base import BaseEngine
from .support_engine import SupportEngine
from .wave_engine import WaveEngine
from .expected_move_engine import ExpectedMoveEngine
from .regime_engine import RegimeEngine
from .risk_engine import RiskEngine
from .hold_confidence_engine import HoldConfidenceEngine, check_trend_failure

__all__ = [
    "BaseEngine",
    "SupportEngine",
    "WaveEngine",
    "ExpectedMoveEngine",
    "RegimeEngine",
    "RiskEngine",
    "HoldConfidenceEngine",
    "check_trend_failure",
]
