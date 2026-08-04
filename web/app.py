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
st.sidebar.markdown("- [Optuna 优化结果](file:///d:/project/a-t0-v2/outputs/optuna/)")
st.sidebar.markdown("- [回测输出目录](file:///d:/project/a-t0-v2/outputs/backtest/)")
st.sidebar.markdown("- [阈值配置](file:///d:/project/a-t0-v2/config/thresholds.yaml)")

st.sidebar.markdown("---")
st.sidebar.caption(f"数据根目录: d:\\project\\data")


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