"""
QuantWeb — Optuna 优化管理页面
===============================
在 Web 界面中启动、监控和管理 Optuna 参数优化。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from web.results_db import get_presets, save_preset


def show() -> None:
    st.title("🎯 Optuna 参数优化")
    st.caption("启动和管理超参数优化任务")

    # ── 检查是否已有完成的优化结果 ──
    optuna_dir = PROJECT_ROOT / "outputs" / "optuna"
    study_files = list(optuna_dir.glob("*_best_params.json")) if optuna_dir.exists() else []
    study_names = sorted(set(f.stem.replace("_best_params", "") for f in study_files))

    tab1, tab2 = st.tabs(["🚀 启动优化", "📊 历史优化结果"])

    with tab1:
        _show_launch_tab(study_names)

    with tab2:
        _show_history_tab(study_names, optuna_dir)


def _show_launch_tab(study_names: list[str]) -> None:
    """启动新优化任务。"""
    st.subheader("配置优化任务")

    col1, col2 = st.columns(2)
    with col1:
        study_name = st.text_input("Study 名称", value=f"v3_o_{__import__('time').strftime('%Y%m%d_%H%M')}")
        n_trials = st.number_input("Trial 数量", min_value=5, max_value=200, value=30, step=5)

    with col2:
        stock_pool = st.selectbox(
            "股票池",
            ["v3_sample_20.txt (20只)", "v3_sample_100.txt (100只)", "v3_sample_5.txt (5只)"],
            index=0,
        )
        stock_pool_map = {
            "v3_sample_20.txt (20只)": "v3_sample_20.txt",
            "v3_sample_100.txt (100只)": "v3_sample_100.txt",
            "v3_sample_5.txt (5只)": "v3_sample_5.txt",
        }
        n_stocks = st.number_input("使用股票数", min_value=5, max_value=100, value=20, step=5)

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("回测起始日期", value=pd.to_datetime("2023-07-25"))
    with col2:
        end_date = st.date_input("回测结束日期", value=pd.to_datetime("2025-01-22"))

    with st.expander("高级设置", expanded=False):
        n_startup = st.number_input("TPE Startup Trials", min_value=5, max_value=50, value=10, step=5)

    if "optuna_running" not in st.session_state:
        st.session_state.optuna_running = False
        st.session_state.optuna_proc = None
        st.session_state.optuna_log = None

    if st.button("🚀 启动优化", type="primary", use_container_width=True):
        import subprocess
        cmd = [
            "python", str(PROJECT_ROOT / "scripts" / "v3" / "optuna_stage_o.py"),
            "--study-name", study_name,
            "--n-trials", str(n_trials),
            "--n-stocks", str(n_stocks),
            "--start", str(start_date),
            "--end", str(end_date),
            "--n-startup", str(n_startup),
        ]
        st.code(" ".join(cmd), language="bash")

        optuna_dir = PROJECT_ROOT / "outputs" / "optuna"
        optuna_dir.mkdir(parents=True, exist_ok=True)
        log_path = optuna_dir / f"{study_name}.log"

        try:
            # 日志重定向到文件（避免 PIPE 缓冲死锁），后台进程不阻塞界面
            with open(log_path, "w", encoding="utf-8") as lf:
                process = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            st.session_state.optuna_proc = process
            st.session_state.optuna_log = str(log_path)
            st.session_state.optuna_running = True
            st.success(f"✅ 优化已启动: {study_name}")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 启动失败: {e}")

    # 后台轮询（非阻塞：每次仅短暂等待后自动刷新）
    if st.session_state.get("optuna_running") and st.session_state.optuna_proc is not None:
        _poll_optuna(st.session_state.optuna_proc, st.session_state.optuna_log, study_name)

    # 已有研究快速加载
    if study_names:
        st.markdown("---")
        st.subheader("已有研究")
        selected_study = st.selectbox("选择已有研究查看", [""] + study_names)
        if selected_study:
            _display_study_results(selected_study, PROJECT_ROOT / "outputs" / "optuna")


def _show_history_tab(study_names: list[str], optuna_dir: Path) -> None:
    """显示历史优化结果。"""
    if not study_names:
        st.info("还没有完成的优化结果。前往「启动优化」标签页启动第一个优化。")
        return

    selected = st.selectbox("选择研究", study_names, index=0)
    if selected:
        _display_study_results(selected, optuna_dir)


def _display_study_results(study_name: str, optuna_dir: Path) -> None:
    """显示单个研究的详细结果。"""
    best_params_path = optuna_dir / f"{study_name}_best_params.json"
    trials_csv_path = optuna_dir / f"{study_name}_trials.csv"
    report_html_path = optuna_dir / f"{study_name}_report.html"

    # 最佳参数
    if best_params_path.exists():
        with open(best_params_path, "r", encoding="utf-8") as f:
            best = json.load(f)

        st.subheader("🏆 最佳参数")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Objective", f"{best.get('objective', 0):.4f}")
        with col2:
            attrs = best.get("user_attrs", {})
            st.metric("NetPnl", f"{attrs.get('net_pnl', 'N/A')}")
        with col3:
            st.metric("Profit Factor", f"{attrs.get('profit_factor', 'N/A')}")
        with col4:
            st.metric("Win Rate", f"{attrs.get('win_rate', 'N/A')}")

        st.subheader("最优参数值")
        params_df = pd.DataFrame([
            {"参数": k, "最优值": v}
            for k, v in best.get("params", {}).items()
        ])
        st.dataframe(params_df, use_container_width=True, hide_index=True)

        # 一键应用为预设
        if st.button("💾 应用为参数预设", type="primary"):
            params = best.get("params", {})
            sp = {
                "alpha_threshold_open": params.get("alpha_threshold_open", 72),
                "v3_alpha_close_threshold": params.get("v3_alpha_close_threshold", 85),
                "expected_move_rr_min": params.get("expected_move_rr_min", 2.0),
                "trend_trailing_weakening": params.get("trend_trailing_weakening", 0.05),
                "trend_trailing_failing": params.get("trend_trailing_failing", 0.03),
            }
            bp = {
                "v3_alpha_stop_loss_ratio": params.get("v3_alpha_stop_loss_ratio", 0.002),
                "v3_alpha_trailing_ratio": params.get("v3_alpha_trailing_ratio", 0.25),
            }
            save_preset(
                f"Optuna_{study_name}",
                {"sp": sp, "bp": bp},
                description=f"Optuna 优化: {study_name} (Objective={best.get('objective', 0):.4f})",
            )
            st.success("✅ 已保存为参数预设！请前往「⚙️ 回测配置」页面加载使用。")

    # Trial 记录
    if trials_csv_path.exists():
        st.subheader("所有 Trial 记录")
        df_trials = pd.read_csv(trials_csv_path)
        st.dataframe(df_trials, use_container_width=True, hide_index=True)

        # 可视化
        if len(df_trials) > 1:
            tab_a, tab_b = st.tabs(["Objective 收敛", "参数重要性"])

            with tab_a:
                df_plot = df_trials[df_trials["value"].notna()].copy()
                if len(df_plot) > 0:
                    df_plot["value"] = pd.to_numeric(df_plot["value"], errors="coerce")
                    df_plot = df_plot.dropna(subset=["value"])
                    df_plot["cummax"] = df_plot["value"].cummax()

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df_plot["number"], y=df_plot["value"],
                        mode="markers", name="Objective",
                        marker=dict(color="#3b82f6", size=6),
                    ))
                    fig.add_trace(go.Scatter(
                        x=df_plot["number"], y=df_plot["cummax"],
                        mode="lines", name="Cumulative Best",
                        line=dict(color="#10b981", width=2),
                    ))
                    fig.update_layout(
                        title="Objective 收敛曲线",
                        xaxis_title="Trial Number",
                        yaxis_title="Objective",
                    )
                    st.plotly_chart(fig, use_container_width=True)

            with tab_b:
                # 参数 vs Objective 散点图
                param_cols = [c for c in df_trials.columns if c not in
                              ("number", "value", "state", "net_pnl", "profit_factor",
                               "win_rate", "payoff_ratio", "stocks", "total_trades", "elapsed_s")]
                selected_param = st.selectbox("选择参数查看", param_cols if param_cols else ["params"])
                if selected_param == "params" and "params" in df_trials.columns:
                    st.info("params 列为 JSON 字符串，请使用具体参数列。")
                elif selected_param in df_trials.columns:
                    df_param = df_trials[df_trials["value"].notna()].copy()
                    df_param["value"] = pd.to_numeric(df_param["value"], errors="coerce")
                    df_param = df_param.dropna(subset=["value", selected_param])
                    df_param[selected_param] = pd.to_numeric(df_param[selected_param], errors="coerce")
                    df_param = df_param.dropna(subset=[selected_param])

                    if len(df_param) > 1:
                        fig2 = px.scatter(
                            df_param,
                            x=selected_param, y="value",
                            title=f"{selected_param} vs Objective",
                            trendline="lowess",
                        )
                        st.plotly_chart(fig2, use_container_width=True)

    # HTML 报告链接
    if report_html_path.exists():
        st.markdown("---")
        st.markdown(f"📄 [查看完整 HTML 报告](file:///{report_html_path})")


def _poll_optuna(proc, log_path: str, study_name: str) -> None:
    """非阻塞轮询 Optuna 子进程状态并展示日志尾部。"""
    placeholder = st.empty()
    if Path(log_path).exists():
        lines = Path(log_path).read_text(encoding="utf-8", errors="ignore").splitlines()[-60:]
        placeholder.code("\n".join(lines), language="text")
    rc = proc.poll()
    if rc is not None:
        st.session_state.optuna_running = False
        placeholder.empty()
        if rc == 0:
            st.success(f"✅ 优化完成！Study: {study_name}")
        else:
            st.error(f"❌ 优化失败，返回码: {rc}")
        return
    st.info("⏳ 优化进行中… 页面每 2 秒自动刷新（后台进程不阻塞界面）")
    time.sleep(2)
    st.rerun()