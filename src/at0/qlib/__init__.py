"""
AT0 Qlib 适配层（V3 研究层嵌入）
================================

定位（V3 重构方案 Part 5）：
  Qlib 作为 Research Layer 嵌入，负责因子工程 + 模型训练 + FutureReturn 预测。
  AT0 自研回测/Execution/Risk 保留不动。

模块组成：
  - loader   — zz500_5min JSON → Qlib MultiIndex DataFrame
  - adapter  — DataHandlerLP + Alpha158 因子计算（核心入口）
  - dataset  — Feature + Label(Future Return) 严格时间切分
  - model    — LightGBM/常量模型 + 降级链

降级链（V3 Part 5）：
  Qlib 已装 + 模型可用 → 用 Qlib Alpha158 + LightGBM
  Qlib 已装 + 模型未训 → 退化为 ATR×常数 的简单 ExpectedMove
  Qlib 未装           → is_qlib_available()=False，调用方回退 features.py

设计原则：
  - 不替换 AT0 数据层：JSON 仍由 data.py 读，adapter 仅做格式转换
  - 不改回测时间推进：Qlib 输出预测，backtest 按既有逻辑消费
  - 可选依赖：qlib 未安装时本包 import 不报错，运行时降级
"""
from __future__ import annotations

# ── 可选依赖检测（惰性，import 时不报错）──
def is_qlib_available() -> bool:
    """检测 qlib 是否可用。降级链入口。

    捕获所有异常（含 TypeError/AttributeError 等），
    因为 qlib 0.9.7 + pydantic 在 Python 3.12 下可能抛非 ImportError。
    """
    try:
        import qlib  # noqa: F401
        import pandas  # noqa: F401
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def is_lightgbm_available() -> bool:
    """检测 LightGBM 是否可用。模型层降级用。"""
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception:
        return False


__all__ = ["is_qlib_available", "is_lightgbm_available"]
