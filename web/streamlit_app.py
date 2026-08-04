"""
QuantWeb — A-T0 回测平台 Web 入口
==================================
Streamlit-based web application for configuring, running, and viewing backtest results.

Usage:
    streamlit run web/app.py --server.port 8501
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import streamlit as st
from at0.paths import get_data_root

st.set_page_config(
    page_title="QuantWeb — A-T0 回测平台",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 侧边栏导航 ──
st.sidebar.title("QuantWeb")
st.sidebar.caption("A-T0 回测平台 v0.1")

page = st.sidebar.radio(
    "导航",
    ["📊 综合仪表盘", "⚙️ 回测配置", "📈 结果查看", "🔬 策略对比", "🎯 Optuna 优化", "🔧 参数配置"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.markdown("**快速链接**")
# 快速链接用项目根目录动态拼接 file:// URI，避免硬编码 Windows 绝对路径
# （原写法 file:///d:/project/a-t0-v2/... 换机器/换盘符即失效）
_optuna_dir = (PROJECT_ROOT / "outputs" / "optuna").resolve()
_backtest_dir = (PROJECT_ROOT / "outputs" / "backtest").resolve()
_thresholds = (PROJECT_ROOT / "config" / "thresholds.yaml").resolve()
st.sidebar.markdown(f"- [Optuna 优化结果](file:///{_optuna_dir})")
st.sidebar.markdown(f"- [回测输出目录](file:///{_backtest_dir})")
st.sidebar.markdown(f"- [阈值配置](file:///{_thresholds})")

st.sidebar.markdown("---")
# 数据根目录：从 at0.paths 读取（支持 AT0_DATA_DIR 环境变量覆盖），
# 不再硬编码 d:\project\data
st.sidebar.caption(f"数据根目录: {get_data_root()}")


# ── 页面路由 ──
if page == "📊 综合仪表盘":
    from pages.dashboard import show
    show()
elif page == "⚙️ 回测配置":
    from pages.backtest import show
    show()
elif page == "📈 结果查看":
    from pages.results import show
    show()
elif page == "🔬 策略对比":
    from pages.compare import show
    show()
elif page == "🎯 Optuna 优化":
    from pages.optuna import show
    show()
elif page == "🔧 参数配置":
    from pages.config import show
    show()