"""
QuantWeb — 综合仪表盘
=====================
显示系统整体状态、最近回测结果、Optuna 优化进度等。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from web.results_db import get_runs, get_run_summary, get_run_results, get_run


@st.cache_data(ttl=30)
def _cached_runs(limit: int = 10):
    return get_runs(limit=limit)


@st.cache_data(ttl=30)
def _cached_run_summary(run_id: int):
    return get_run_summary(run_id)


@st.cache_data(ttl=30)
def _cached_run_results(run_id: int):
    return get_run_results(run_id)


def show() -> None:
    st.title("📊 综合仪表盘")
    st.caption("A-T0 回测平台 — 系统概览")

    if st.button("🔄 刷新数据"):
        _cached_runs.clear()
        _cached_run_summary.clear()
        _cached_run_results.clear()
        st.rerun()

    # 最近回测运行
    runs = _cached_runs(10)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总回测次数", len(runs))
    with col2:
        completed = sum(1 for r in runs if r["status"] == "completed")
        st.metric("已完成", completed)
    with col3:
        if runs:
            profitable = sum(1 for r in runs if _get_net_pnl(r["id"]) > 0)
            st.metric("盈利运行", profitable)
        else:
            st.metric("盈利运行", 0)
    with col4:
        st.metric("数据根目录", "d:\\project\\data")

    # 最近运行列表
    st.subheader("最近回测运行")

    if not runs:
        st.info("还没有回测运行记录。前往「⚙️ 回测配置」运行第一次回测。")
        return

    run_data = []
    for r in runs:
        summary = _cached_run_summary(r["id"])
        status_display = r["status"]
        if r["status"] == "running" and r.get("progress"):
            r_progress = r['progress']
            status_display = f"运行中 ({r_progress})"
        r_id = r['id']
        s_profitable_ratio = summary['profitable_ratio'] if summary else 0
        s_avg_ce = summary['avg_ce'] if summary else 0
        r_duration_s = r['duration_s']
        run_data.append({
            "ID": r["id"],
            "名称": r["name"] or f"Run #{r_id}",
            "时间": r["created_at"][:19],
            "状态": status_display,
            "股票数": summary["stocks"] if summary else 0,
            "净盈亏": summary["total_net"] if summary else 0,
            "盈利比": f"{s_profitable_ratio*100:.1f}%" if summary else "N/A",
            "平均CE": f"{s_avg_ce*100:.1f}%" if summary else "N/A",
            "耗时": f"{r_duration_s:.1f}s" if r.get("duration_s") else "N/A",
        })

    df = pd.DataFrame(run_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 最近一次运行的净盈亏分布
    if runs:
        latest = runs[0]
        if latest["status"] == "completed":
            results = _cached_run_results(latest["id"])
            if results:
                run_id = latest["id"]
                run_label = latest.get("name") or f"Run #{run_id}"
                st.subheader(f"最近运行: {run_label} — 个股净盈亏分布")

                df_results = pd.DataFrame(results)
                fig = px.bar(
                    df_results.sort_values("net_pnl", ascending=True),
                    x="code", y="net_pnl",
                    title="个股净盈亏排行",
                    color="net_pnl",
                    color_continuous_scale=["red", "gray", "green"],
                )
                st.plotly_chart(fig, use_container_width=True)

                # 胜率 vs 净盈亏散点图
                fig2 = px.scatter(
                    df_results,
                    x="win_rate", y="net_pnl",
                    title="胜率 vs 净盈亏",
                    hover_data=["code"],
                    labels={"win_rate": "胜率", "net_pnl": "净盈亏"},
                )
                st.plotly_chart(fig2, use_container_width=True)

                # 汇总指标
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    net_pnl_sum = df_results['net_pnl'].sum()
                    st.metric("总净盈亏", f"{net_pnl_sum:+.2f}")
                with col2:
                    win_rate_mean = df_results['win_rate'].mean()
                    st.metric("平均胜率", f"{win_rate_mean*100:.1f}%")
                with col3:
                    profit_count = len(df_results[df_results['net_pnl'] > 0])
                    total_count = len(df_results)
                    st.metric("盈利股票数",
                              f"{profit_count}/{total_count}")
                with col4:
                    ce_values = [r for r in df_results["avg_ce"] if r and r > 0]
                    avg_ce = sum(ce_values) / len(ce_values) if ce_values else 0
                    st.metric("平均 CE", f"{avg_ce*100:.1f}%")


def _get_net_pnl(run_id: int) -> float:
    summary = _cached_run_summary(run_id)
    return summary["total_net"] if summary else 0.0