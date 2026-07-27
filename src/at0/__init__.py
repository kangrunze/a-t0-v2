"""
A-T0 — A股T+0底仓做T回测系统
============================

公共 API：
  - 配置加载：load_signal_params / load_risk_params / load_backtest_params / ...
  - 参数 dataclass：SignalParams / RiskParams / BacktestParams / ScreenerParams
  - 成本/敞口：CostModel / ExposurePolicy
  - 路径：DATA_ROOT / PROJECT_ROOT / MINUTE_LOCAL_DIR / ZZ500_5MIN_DIR

回测引擎、策略、特征等内部模块请按需 `from at0.xxx import` 显式导入，
避免包级别 Facade 引入过长的 import 链。
"""
from __future__ import annotations

# ── 版本 ──
__version__ = "1.1.0"

# ── 配置加载器（统一从 config/thresholds.yaml 读取）──
from .config import (
    load_signal_params,
    load_risk_params,
    load_backtest_params,
    load_screener_params,
    load_cost_model,
    load_exposure_policy,
)

# ── 参数 dataclass ──
from .strategy import SignalParams
from .risk import RiskParams, CostModel, ExposurePolicy
from .backtest import BacktestParams
from .screener import ScreenerParams

# ── 路径常量 ──
from .paths import (
    DATA_ROOT,
    PROJECT_ROOT,
    MINUTE_LOCAL_DIR,
    ZZ500_5MIN_DIR,
    MINUTE_BARS_DIR,
    MULTI_DAY_CACHE_DIR,
)

__all__ = [
    "__version__",
    # 配置加载
    "load_signal_params",
    "load_risk_params",
    "load_backtest_params",
    "load_screener_params",
    "load_cost_model",
    "load_exposure_policy",
    # 参数 dataclass
    "SignalParams",
    "RiskParams",
    "BacktestParams",
    "ScreenerParams",
    "CostModel",
    "ExposurePolicy",
    # 路径
    "DATA_ROOT",
    "PROJECT_ROOT",
    "MINUTE_LOCAL_DIR",
    "ZZ500_5MIN_DIR",
    "MINUTE_BARS_DIR",
    "MULTI_DAY_CACHE_DIR",
]
