"""
QuantWeb — 策略对比页面
=======================
并排对比多次回测运行的结果，支持 A/B 测试对比和分组聚合。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from web.results_db import get_runs, get_run_summary, get_run_results


@st.cache_data(ttl=30)
def _cached_runs(limit: int = 50):
    return get_runs(limit=limit)


@st.cache_data(ttl=30)
def _cached_run_summary(run_id: int):
    return get_run_summary(run_id)


@st.cache_data(ttl=30)
def _cached_run_results(run_id: int):
    return get_run_results(run_id)


def show() -> None:
    st.title("🔬 策略对比")
    st.caption("并排对比多次回测运行的性能指标，支持 A/B 测试对比")

    if st.button("🔄 刷新数据"):
        _cached_runs.clear()
        _cached_run_summary.clear()
        _cached_run_results.clear()
        st.rerun()

    runs = _cached_runs(50)
    completed_runs = [r for r in runs if r["status"] == "completed"]

    if len(completed_runs) < 1:
        st.info("需要至少 1 次完成的回测运行才能进行对比。")
        return

    # ── 模式选择 ──
    mode = st.radio(
        "对比模式",
        ["📋 手动选择运行对比", "🏷️ 按标签分组对比"],
        horizontal=True,
        index=0,
    )

    if mode == "📋 手动选择运行对比":
        _show_manual_compare(completed_runs)
    else:
        _show_grouped_compare(completed_runs)


def _show_manual_compare(completed_runs: list[dict]) -> None:
    """手动选择运行进行对比。"""
    run_options = {}
    for r in completed_runs:
        rid = r['id']
        rname = r.get('name', '')
        rcreated = r['created_at'][:19]
        run_options[f"#{rid} {rname} ({rcreated})"] = rid

    selected_labels = st.multiselect(
        "选择要对比的运行（可选 2-4 个）",
        list(run_options.keys()),
        default=list(run_options.keys())[:min(2, len(run_options))],
    )

    if len(selected_labels) < 2:
        st.info("请选择至少 2 个运行进行对比")
        return

    selected_ids = [run_options[label] for label in selected_labels]
    _display_compare_results(selected_ids, completed_runs)


def _show_grouped_compare(completed_runs: list[dict]) -> None:
    """按标签分组对比。"""
    # 获取所有标签
    tags = sorted(set(r.get("tag", "") or "未标记" for r in completed_runs))
    selected_tags = st.multiselect(
        "选择要对比的标签组",
        tags,
        default=tags[:min(3, len(tags))],
    )

    if len(selected_tags) < 2:
        st.info("请选择至少 2 个标签组进行对比")
        return

    # 按标签分组
    grouped_ids = {}
    for tag in selected_tags:
        tag_key = "未标记" if tag == "未标记" else tag
        group_runs = [r for r in completed_runs
                      if (r.get("tag") or "未标记") == tag_key]
        if group_runs:
            # 取每组最新的运行
            latest = group_runs[0]
            grouped_ids[tag_key] = latest["id"]

    if len(grouped_ids) < 2:
        st.info("每个标签组需要至少 1 个完成的运行")
        return

    st.success(f"对比标签组: {', '.join(grouped_ids.keys())}")
    _display_compare_results(list(grouped_ids.values()), completed_runs,
                             label_map=grouped_ids)


def _display_compare_results(
    selected_ids: list[int],
    completed_runs: list[dict],
    label_map: dict[str, int] | None = None,
) -> None:
    """显示对比结果。"""
    # ── 汇总指标对比表 ──
    st.subheader("汇总指标对比")

    compare_data = []
    for rid in selected_ids:
        run = next(r for r in completed_runs if r["id"] == rid)
        summary = _cached_run_summary(rid)
        label = f"#{rid}"
        if label_map:
            # 反向查找标签
            for tag, rid2 in label_map.items():
                if rid2 == rid:
                    label = f"[{tag}] #{rid}"
                    break
        else:
            rcreated = run.get('created_at', '?')[:10]
            label = f"#{rid} ({rcreated})"

        if summary:
            s_profitable = summary['profitable_ratio']
            s_win_rate = summary['avg_win_rate']
            s_avg_ce = summary['avg_ce']
            compare_data.append({
                "运行": label,
                "标签": run.get("tag") or "-",
                "股票数": summary["stocks"],
                "总配对交易": summary["total_paired"],
                "总净盈亏": summary["total_net"],
                "盈利股票比": f"{s_profitable*100:.1f}%",
                "平均胜率": f"{s_win_rate*100:.1f}%",
                "平均 CE": f"{s_avg_ce*100:.1f}%",
                "耗时": f"{run.get('duration_s', 0):.1f}s",
            })

    df_compare = pd.DataFrame(compare_data)
    st.dataframe(df_compare, use_container_width=True, hide_index=True)

    # ── 雷达图 ──
    st.subheader("多维雷达图")

    radar_data = []
    for rid in selected_ids:
        summary = _cached_run_summary(rid)
        label = f"Run #{rid}"
        if label_map:
            for tag, rid2 in label_map.items():
                if rid2 == rid:
                    label = tag
                    break
        if summary:
            radar_data.append({
                "id": rid,
                "label": label,
                "NetPnl": summary["total_net"] / 1000,  # 缩放
                "WinRate": summary["avg_win_rate"] * 100,
                "CE": summary["avg_ce"] * 100,
                "ProfitableRatio": summary["profitable_ratio"] * 100,
            })

    if len(radar_data) >= 2:
        fig = go.Figure()
        categories = ["NetPnl (K)", "WinRate (%)", "CE (%)", "ProfitableRatio (%)"]
        for rd in radar_data:
            fig.add_trace(go.Scatterpolar(
                r=[rd["NetPnl"], rd["WinRate"], rd["CE"], rd["ProfitableRatio"]],
                theta=categories,
                fill="toself",
                name=rd["label"],
            ))
        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            title="策略多维对比",
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── 个股净盈亏对比 ──
    st.subheader("个股净盈亏对比（Top 20）")

    all_stock_data = []
    for rid in selected_ids:
        results = _cached_run_results(rid)
        label = f"Run #{rid}"
        if label_map:
            for tag, rid2 in label_map.items():
                if rid2 == rid:
                    label = tag
                    break
        for r in results[:20]:
            all_stock_data.append({
                "运行": label,
                "股票": r["code"],
                "净盈亏": r["net_pnl"],
            })

    if all_stock_data:
        df_stocks = pd.DataFrame(all_stock_data)
        fig2 = go.Figure()
        for rid in selected_ids:
            label = f"Run #{rid}"
            if label_map:
                for tag, rid2 in label_map.items():
                    if rid2 == rid:
                        label = tag
                        break
            df_run = df_stocks[df_stocks["运行"] == label]
            fig2.add_trace(go.Bar(
                name=label,
                x=df_run["股票"],
                y=df_run["净盈亏"],
            ))
        fig2.update_layout(
            title="个股净盈亏对比",
            barmode="group",
            xaxis_title="股票代码",
            yaxis_title="净盈亏",
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ── 指标对比散点图 ──
    st.subheader("散点矩阵对比")
    scatter_col1, scatter_col2 = st.columns(2)
    with scatter_col1:
        x_metric = st.selectbox(
            "X 轴指标",
            ["net_pnl", "win_rate", "profit_factor", "payoff_ratio", "avg_ce"],
            index=0,
            format_func=lambda x: {"net_pnl": "净盈亏", "win_rate": "胜率",
                                   "profit_factor": "PF", "payoff_ratio": "盈亏比",
                                   "avg_ce": "CE"}.get(x, x),
        )
    with scatter_col2:
        y_metric = st.selectbox(
            "Y 轴指标",
            ["win_rate", "net_pnl", "profit_factor", "payoff_ratio", "avg_ce"],
            index=1,
            format_func=lambda x: {"net_pnl": "净盈亏", "win_rate": "胜率",
                                   "profit_factor": "PF", "payoff_ratio": "盈亏比",
                                   "avg_ce": "CE"}.get(x, x),
        )

    scatter_data = []
    for rid in selected_ids:
        results = _cached_run_results(rid)
        label = f"Run #{rid}"
        if label_map:
            for tag, rid2 in label_map.items():
                if rid2 == rid:
                    label = tag
                    break
        for r in results:
            scatter_data.append({
                "运行": label,
                "股票": r["code"],
                "net_pnl": r.get("net_pnl", 0),
                "win_rate": r.get("win_rate", 0),
                "profit_factor": r.get("profit_factor", 0),
                "payoff_ratio": r.get("payoff_ratio", 0),
                "avg_ce": r.get("avg_ce", 0),
            })

    if scatter_data:
        df_scatter = pd.DataFrame(scatter_data)
        fig3 = px.scatter(
            df_scatter,
            x=x_metric, y=y_metric,
            color="运行",
            hover_data=["股票"],
            title=f"{x_metric} vs {y_metric}",
            opacity=0.7,
        )
        fig3.add_hline(y=0, line_color="gray", line_width=1, line_dash="dash")
        fig3.add_vline(x=0, line_color="gray", line_width=1, line_dash="dash")
        st.plotly_chart(fig3, use_container_width=True)

    # ── 参数对比 ──
    st.subheader("参数配置对比")

    param_rows = []
    for rid in selected_ids:
        run = next(r for r in completed_runs if r["id"] == rid)
        label = f"Run #{rid}"
        if label_map:
            for tag, rid2 in label_map.items():
                if rid2 == rid:
                    label = tag
                    break
        params_raw = run.get("params_override")
        if params_raw:
            try:
                params = json.loads(params_raw)
                sp = params.get("sp", {})
                bp = params.get("bp", {})
                all_params = {**sp, **bp}
                for k, v in all_params.items():
                    param_rows.append({"运行": label, "参数": k, "值": v})
            except json.JSONDecodeError:
                param_rows.append({"运行": label, "参数": "params_override", "值": params_raw})
        else:
            param_rows.append({"运行": label, "参数": "全部", "值": "使用 yaml 默认值"})

    if param_rows:
        df_params = pd.DataFrame(param_rows)
        # 透视表
        try:
            pivot = df_params.pivot_table(index="参数", columns="运行", values="值", aggfunc="first")
            st.dataframe(pivot, use_container_width=True)
        except Exception:
            st.dataframe(df_params, use_container_width=True, hide_index=True)