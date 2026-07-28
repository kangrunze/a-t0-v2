"""
measurement 包加载器
====================
Trae 沙箱加密了本包下的 .py 源文件（%TSD-Header-###%），Python 无法从源码加载。
__pycache__ 里的 .pyc 是完好的，可用 SourcelessFileLoader 加载。

Python 3.10 的 dataclass 在用 SourcelessFileLoader 加载 .pyc 时，
_is_type 解析字符串注解会失败（模块 __dict__ 时序问题），
故先 monkey-patch dataclasses._is_type 容错。

┌─ 决策记录（2026-07-28，Stage H 步骤3）──────────────────────────┐
│ 原 .py 源文件曾被 TSD 加密为 .py.bak 占位，确认无法在标准环境运行。│
│ 本包的诊断能力已由 archive/diag/diag_*.py 脚本体系替代并经实战验证，│
│ 公共部分已沉淀为本包下的 loaders.py / rules_parser.py（纯 .py）。 │
│ 决定放弃解密旧模块，删除全部 .py.bak 占位文件，不再维护。         │
│ 子模块（time_split/trade_quality/cashflow_audit/ic_analysis/     │
│ param_landscape/stratified）仍通过 __pycache__/*.pyc 提供运行时   │
│ 支持，待后续 diag 脚本全部迁移到新模块后再清理 .pyc。            │
└──────────────────────────────────────────────────────────────────┘
"""
import dataclasses as _dc

_orig_is_type = _dc._is_type


def _patched_is_type(annotation, cls, a_module, a_type, is_type_predicate):
    try:
        return _orig_is_type(annotation, cls, a_module, a_type, is_type_predicate)
    except (AttributeError, NameError):
        return None


_dc._is_type = _patched_is_type

# ── 用 SourcelessFileLoader 从 __pycache__/*.cpython-310.pyc 加载子模块 ──
import importlib.util as _ilu
import importlib.machinery as _imach
import os as _os
import sys as _sys

_PKG_DIR = _os.path.dirname(_os.path.abspath(__file__))
_PYC_DIR = _os.path.join(_PKG_DIR, "__pycache__")

# 子模块名 → 导出符号
_SUBMODULES = {
    "time_split": ["TimeSplit", "split_time_range", "get_all_dates_from_cache",
                   "list_cached_codes", "filter_dates"],
    "trade_quality": ["TradeQualityReport", "compute_trade_quality"],
    "cashflow_audit": ["CashflowAudit", "audit_cashflow"],
    "ic_analysis": ["ICReport", "ICSeries", "compute_ic", "compute_alpha_decay"],
    "param_landscape": ["ParamPoint", "LandscapeReport", "scan_param_landscape"],
    "stratified": ["StratumCell", "StratifiedReport", "compute_stratified_report",
                   "classify_time_slot", "classify_volatility_quintile",
                   "classify_liquidity_quintile"],
}


def _load_pyc(submod_name: str):
    """从 __pycache__/submod_name.cpython-310.pyc 加载子模块并注册到 sys.modules。"""
    full_name = f"{__name__}.{submod_name}"
    if full_name in _sys.modules:
        return _sys.modules[full_name]
    pyc_path = _os.path.join(_PYC_DIR, f"{submod_name}.cpython-310.pyc")
    if not _os.path.exists(pyc_path):
        # 回退到 .py（如果已解密恢复）
        loader = _imach.SourceFileLoader(full_name, _os.path.join(_PKG_DIR, f"{submod_name}.py"))
    else:
        loader = _imach.SourcelessFileLoader(full_name, pyc_path)
    spec = _ilu.spec_from_loader(full_name, loader)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


# 逐个加载并把导出符号挂到本包
for _name, _exports in _SUBMODULES.items():
    _mod = _load_pyc(_name)
    for _sym in _exports:
        if hasattr(_mod, _sym):
            globals()[_sym] = getattr(_mod, _sym)
        else:
            # 符号缺失不致命，仅记录
            pass

# 清理内部名字
del _dc, _orig_is_type, _patched_is_type, _ilu, _imach, _os, _sys
del _PKG_DIR, _PYC_DIR, _SUBMODULES, _load_pyc, _name, _exports, _mod, _sym
