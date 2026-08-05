"""
measurement 包 — 测量指标（Metrics）与报告
==========================================

本包在 2026-08-04（Sprint 2）从"黑盒 .pyc"恢复为可读 .py 源码：
六个子模块（time_split / trade_quality / cashflow_audit /
ic_analysis / param_landscape / stratified）已按 .pyc 中的 docstring
和现有使用模式重写为可读的 .py 源文件，git blame 可追溯。

┌─ Sprint 2 修复（2026-08-04）─────────────────────────────────────┐
│ 六个子模块已根据 .pyc 中的 docstring 和                             │
│ 现有使用模式（backtest.py / run_baseline_measurement.py 等）       │
│ 重写为可读的 .py 源文件。对应的 .cpython-310.pyc 已删除。           │
└──────────────────────────────────────────────────────────────────┘

┌─ Loader 清理（2026-08-05）───────────────────────────────────────┐
│ 移除旧的 SourcelessFileLoader shim（此前优先从 __pycache__/ 加载    │
│ .cpython-310.pyc，找不到才 fallback 到 .py）。                      │
│ 该 shim 的历史背景：Trae 沙箱曾把 .py 加密为 .py.bak，               │
│ 标准 Python 无法加载源码，只能依赖 .pyc。Sprint 2 源码恢复后        │
│ 前提已失效，但 shim 不检查 .pyc 是否比 .py 新，会静默加载过时的      │
│ 编译产物——失效模式恰是"改了代码但没生效"。                          │
│ 现已改为标准 from .xxx import ... 导入；                          │
│ 同时从仓库移除 6 个 TSD 加密的 .py.bak 占位与 __pycache__/*.pyc，    │
│ 并删除 .gitignore 中对应的"例外保留 .pyc"规则。                     │
└──────────────────────────────────────────────────────────────────┘
"""
from .time_split import (
    TimeSplit,
    split_time_range,
    get_all_dates_from_cache,
    list_cached_codes,
    filter_dates,
)
from .trade_quality import TradeQualityReport, compute_trade_quality
from .cashflow_audit import CashflowAudit, audit_cashflow
from .ic_analysis import ICReport, ICSeries, compute_ic, compute_alpha_decay
from .param_landscape import ParamPoint, LandscapeReport, scan_param_landscape
from .stratified import (
    StratumCell,
    StratifiedReport,
    compute_stratified_report,
    classify_time_slot,
    classify_volatility_quintile,
    classify_liquidity_quintile,
)

__all__ = [
    # time_split
    "TimeSplit", "split_time_range", "get_all_dates_from_cache",
    "list_cached_codes", "filter_dates",
    # trade_quality
    "TradeQualityReport", "compute_trade_quality",
    # cashflow_audit
    "CashflowAudit", "audit_cashflow",
    # ic_analysis
    "ICReport", "ICSeries", "compute_ic", "compute_alpha_decay",
    # param_landscape
    "ParamPoint", "LandscapeReport", "scan_param_landscape",
    # stratified
    "StratumCell", "StratifiedReport", "compute_stratified_report",
    "classify_time_slot", "classify_volatility_quintile",
    "classify_liquidity_quintile",
]
