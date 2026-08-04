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
from at0.paths import get_data_root


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
        st.metric("数据根目录", str(get_data_root()))

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

    # ── 研发进度趋势（时间维度，体现 median+IQR 铁律）──
    # 原仪表盘只看最近 10 次横截面，没有"净盈亏随时间变好还是变差"的趋势图。
    # 这里取最近 50 次完成运行，画总净盈亏折线 + 中位数线 + IQR 阴影，
    # 直观反映策略迭代方向是否符合 memory 中记录的 median+IQR 统计口径。
    _show_trend_section()

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


def _show_trend_section() -> None:
    """研发进度趋势：净盈亏随时间变化 + 中位数/IQR 区间。

    体现 memory 中记录的 median+IQR 铁律——策略评估不应只看单次净盈亏，
    应看跨运行的中位数是否稳步上移、IQR 是否收窄（稳定性提升）。
    """
    st.subheader("研发进度趋势（净盈亏中位数 + IQR）")
    st.caption("取最近 50 次完成运行，观察策略迭代方向：中位数是否上移？离散度是否收窄？")

    trend_runs = _cached_runs(50)
    completed = [r for r in trend_runs if r["status"] == "completed"]
    if len(completed) < 2:
        st.info("需要至少 2 次完成的运行才能显示趋势。")
        return

    rows = []
    for r in completed:
        s = _cached_run_summary(r["id"])
        if s:
            r_name = r.get("name") or ""
            rows.append({
                "时间": r["created_at"][:16],
                "总净盈亏": s["total_net"],
                "盈利比": s["profitable_ratio"],
                "运行": f"#{r['id']} {r_name}".strip(),
            })

    if len(rows) < 2:
        st.info("完成运行的有效汇总数据不足，无法绘制趋势。")
        return

    df_trend = pd.DataFrame(rows).sort_values("时间").reset_index(drop=True)

    median_val = df_trend["总净盈亏"].median()
    q1 = df_trend["总净盈亏"].quantile(0.25)
    q3 = df_trend["总净盈亏"].quantile(0.75)

    fig = go.Figure()
    # IQR 阴影带
    fig.add_trace(go.Scatter(
        x=list(df_trend["时间"]) + list(df_trend["时间"][::-1]),
        y=[q3] * len(df_trend) + [q1] * len(df_trend),
        fill="toself",
        fillcolor="rgba(99, 102, 241, 0.12)",
        line=dict(width=0),
        name=f"IQR ({q1:+.0f} ~ {q3:+.0f})",
        hoverinfo="skip",
    ))
    # 中位数参考线
    fig.add_hline(y=median_val, line_color="#f59e0b", line_dash="dash", line_width=1.5,
                  annotation_text=f"中位数 {median_val:+.0f}",
                  annotation_position="top left")
    # 零线
    fig.add_hline(y=0, line_color="gray", line_width=1)
    # 净盈亏折线
    fig.add_trace(go.Scatter(
        x=df_trend["时间"], y=df_trend["总净盈亏"],
        mode="lines+markers",
        name="总净盈亏",
        line=dict(color="#10b981", width=2),
        marker=dict(size=7),
        hovertemplate="%{x}<br>%{text}<br>净盈亏 %{y:+.2f}<extra></extra>",
        text=df_trend["运行"],
    ))
    fig.update_layout(
        height=380,
        hovermode="x unified",
        yaxis_title="总净盈亏",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10, r=20, t=30, b=30),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 趋势判定
    half = len(df_trend) // 2
    if half >= 1:
        first_half_med = df_trend["总净盈亏"].iloc[:half].median()
        second_half_med = df_trend["总净盈亏"].iloc[half:].median()
        delta = second_half_med - first_half_med
        if delta > 0:
            st.success(f"📈 中位数上移 {delta:+.0f}（前半段 {first_half_med:+.0f} → 后半段 {second_half_med:+.0f}），策略迭代方向正向。")
        else:
            st.warning(f"📉 中位数下移 {delta:+.0f}（前半段 {first_half_med:+.0f} → 后半段 {second_half_med:+.0f}），需关注迭代是否倒退。")