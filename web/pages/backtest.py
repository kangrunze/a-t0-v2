"""
QuantWeb — 回测配置页面
=======================
配置并执行回测任务。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import streamlit as st
import pandas as pd

from web.results_db import (save_run, update_run_status, save_result, get_run,
                             save_preset, get_presets, delete_preset,
                             cancel_run, is_run_cancelled, get_runs)


def show() -> None:
    st.title("⚙️ 回测配置")
    st.caption("配置回测参数并启动执行")

    # 基础配置
    col1, col2 = st.columns(2)
    with col1:
        run_name = st.text_input("运行名称", value=f"回测_{time.strftime('%Y%m%d_%H%M')}")
        stock_pool_option = st.selectbox(
            "股票池",
            ["v3_sample_100.txt (100只)", "v3_sample_20.txt (20只)", "v3_sample_5.txt (5只)", "全部500只"],
            index=0,
        )
        stock_pool_map = {
            "v3_sample_100.txt (100只)": "v3_sample_100.txt",
            "v3_sample_20.txt (20只)": "v3_sample_20.txt",
            "v3_sample_5.txt (5只)": "v3_sample_5.txt",
            "全部500只": "all",
        }
        stock_pool = stock_pool_map[stock_pool_option]

    with col2:
        start_date = st.date_input("起始日期", value=pd.to_datetime("2023-07-25"))
        end_date = st.date_input("结束日期", value=pd.to_datetime("2026-07-22"))
        base_shares = st.number_input("底仓股数", value=3000, step=500)

    # 参数预设
    presets = get_presets()
    preset_options = {"无（使用 yaml 默认值）": None}
    for p in presets:
        label = p["name"]
        if p.get("description"):
            p_desc = p['description']
            label += f" — {p_desc}"
        preset_options[label] = p

    selected_preset_label = st.selectbox("参数预设", list(preset_options.keys()), index=0)
    selected_preset = preset_options[selected_preset_label]

    # 确定默认参数值
    default_params = {
        "alpha_th": 72, "stop_loss": 0.002, "trailing": 0.25,
        "close_th": 85, "rr_min": 2.0,
        "tr_weak": 0.05, "tr_fail": 0.03,
    }
    if selected_preset is not None:
        po = selected_preset.get("params_override", {})
        sp = po.get("sp", {})
        bp = po.get("bp", {})
        default_params["alpha_th"] = sp.get("alpha_threshold_open", 72)
        default_params["close_th"] = sp.get("v3_alpha_close_threshold", 85)
        default_params["rr_min"] = sp.get("expected_move_rr_min", 2.0)
        default_params["tr_weak"] = sp.get("trend_trailing_weakening", 0.05)
        default_params["tr_fail"] = sp.get("trend_trailing_failing", 0.03)
        default_params["stop_loss"] = bp.get("v3_alpha_stop_loss_ratio", 0.002)
        default_params["trailing"] = bp.get("v3_alpha_trailing_ratio", 0.25)

    # 参数覆盖
    st.subheader("参数覆盖（可选，留空使用 yaml 默认值）")

    with st.expander("打开参数覆盖", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            alpha_th = st.slider("alpha_threshold_open", 60, 85, default_params["alpha_th"], 1)
            stop_loss = st.slider("stop_loss_ratio", 0.001, 0.005, default_params["stop_loss"], 0.0005,
                                  format="%.3f")
            trailing = st.slider("trailing_ratio", 0.15, 0.35, default_params["trailing"], 0.01)
        with col2:
            close_th = st.slider("close_threshold", 80, 95, default_params["close_th"], 1)
            rr_min = st.slider("expected_move_rr_min", 1.5, 3.0, default_params["rr_min"], 0.1)
        with col3:
            tr_weak = st.slider("trend_trailing_weakening", 0.03, 0.15, default_params["tr_weak"], 0.01,
                                format="%.2f")
            tr_fail = st.slider("trend_trailing_failing", 0.01, 0.08, default_params["tr_fail"], 0.005,
                                format="%.3f")

        use_override = st.checkbox("启用参数覆盖", value=selected_preset is not None)

        # 保存为预设
        save_col1, save_col2 = st.columns([3, 1])
        with save_col1:
            preset_name = st.text_input("预设名称（可选，留空不保存）", value="",
                                        placeholder="例如: Baseline, Optuna最优, 激进, 保守")
        with save_col2:
            if st.button("💾 保存预设") and preset_name.strip():
                params_override = {
                    "sp": {
                        "alpha_threshold_open": alpha_th,
                        "v3_alpha_close_threshold": close_th,
                        "expected_move_rr_min": rr_min,
                        "trend_trailing_weakening": tr_weak,
                        "trend_trailing_failing": tr_fail,
                    },
                    "bp": {
                        "v3_alpha_stop_loss_ratio": stop_loss,
                        "v3_alpha_trailing_ratio": trailing,
                    },
                }
                save_preset(preset_name.strip(), params_override,
                            description=f"alpha_th={alpha_th}, sl={stop_loss}, tr={trailing}")
                st.success(f"✅ 预设「{preset_name}」已保存！")
                st.rerun()

    # 运行按钮
    st.markdown("---")
    if "bt_running" not in st.session_state:
        st.session_state.bt_running = False
        st.session_state.bt_run_id = None

    if st.button("🚀 运行回测", type="primary", use_container_width=True):
        if use_override:
            params_override = {
                "sp": {
                    "alpha_threshold_open": alpha_th,
                    "v3_alpha_close_threshold": close_th,
                    "expected_move_rr_min": rr_min,
                    "trend_trailing_weakening": tr_weak,
                    "trend_trailing_failing": tr_fail,
                },
                "bp": {
                    "v3_alpha_stop_loss_ratio": stop_loss,
                    "v3_alpha_trailing_ratio": trailing,
                },
            }
        else:
            params_override = None

        # 保存运行记录
        run_id = save_run(
            name=run_name,
            stock_pool=stock_pool,
            start_date=str(start_date),
            end_date=str(end_date),
            params_override=params_override,
            tag="web",
        )

        st.success(f"✅ 任务已创建 (Run #{run_id})，正在后台执行...")

        # 后台线程执行（不阻塞界面）
        def _run():
            try:
                _execute_backtest(run_id, stock_pool, str(start_date),
                                  str(end_date), base_shares, params_override)
            except Exception as e:
                import traceback
                traceback.print_exc()
                update_run_status(run_id, "failed", error=str(e))

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # 记录会话状态后立即结束本次脚本，避免 sleep 阻塞整个页面
        st.session_state.bt_running = True
        st.session_state.bt_run_id = run_id
        st.rerun()

    # 回测任务列表（运行中任务可一键停止）
    _show_task_list()

    # 后台进度轮询（非阻塞：每次仅短暂等待后自动刷新，不卡住界面）
    if st.session_state.get("bt_running"):
        _poll_backtest_progress(st.session_state.bt_run_id)


def _execute_backtest(
    run_id: int,
    stock_pool: str,
    start_date: str,
    end_date: str,
    base_shares: int,
    params_override: dict | None,
) -> None:
    """后台执行回测任务，实时更新进度。"""
    import pandas as pd
    from backtest_zz500 import load_multi_day_zz500, run_zz500_single, run_zz500_batch
    from at0.paths import ZZ500_5MIN_DIR
    from at0.backtest import summarize_one_stock

    # 加载股票池
    if stock_pool == "all":
        codes = sorted(f.stem for f in ZZ500_5MIN_DIR.glob("*.json") if f.stem.isdigit())
    else:
        pool_path = PROJECT_ROOT / "config" / stock_pool
        if pool_path.exists():
            with open(pool_path) as f:
                codes = [line.strip() for line in f if line.strip().isdigit()]
        else:
            codes = []

    if not codes:
        update_run_status(run_id, "failed", error="股票池为空")
        return

    update_run_status(run_id, "running", progress=f"0/{len(codes)}")

    t0 = time.time()

    # 逐只执行并更新进度
    per_stock: list[dict] = []
    all_trades_count = 0
    for i, code in enumerate(codes, 1):
        # 检查是否已被用户停止（每处理完一只股票后检查一次）
        if is_run_cancelled(run_id):
            update_run_status(
                run_id, "cancelled",
                progress=f"{i - 1}/{len(codes)}",
            )
            break

        try:
            result = run_zz500_single(
                code=code,
                start_date=start_date,
                end_date=end_date,
                data_dir=ZZ500_5MIN_DIR,
                base_shares=base_shares,
                avg_cost=None,
                tag=f"web_run_{run_id}",
                params_override=params_override,
            )
        except Exception as e:
            per_stock.append({"code": code, "error": str(e),
                              "total_trades": 0, "paired_trades": 0,
                              "win_trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                              "unrealized_pnl": 0.0, "net_pnl_with_unrealized": 0.0,
                              "final_open_legs_count": 0})
            continue

        if not result or not result.get("daily_results"):
            per_stock.append({"code": code, "error": "no_data",
                              "total_trades": 0, "paired_trades": 0,
                              "win_trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                              "unrealized_pnl": 0.0, "net_pnl_with_unrealized": 0.0,
                              "final_open_legs_count": 0})
            continue

        trades = _extract_trades(result)
        summary = summarize_one_stock(code, result)
        per_stock.append(summary)
        all_trades_count += len(trades)

        # 保存个股结果
        if "error" in summary:
            save_result(run_id, code, summary)
        else:
            try:
                daily_bars, daily_prev, _ = load_multi_day_zz500(
                    code, start_date, end_date, ZZ500_5MIN_DIR,
                )
                if daily_bars:
                    from at0.measurement.v2_metrics import compute_v2_metrics_for_report
                    v2 = compute_v2_metrics_for_report(result, daily_bars)
                    summary["avg_ce"] = v2.get("summary", {}).get("avg_ce", 0)
                    summary["avg_entry_delay_bars"] = v2.get("summary", {}).get("avg_entry_delay_bars", 0)
                    summary["avg_exit_delay_bars"] = v2.get("summary", {}).get("avg_exit_delay_bars", 0)
                    summary["avg_remaining_move_pct"] = v2.get("summary", {}).get("avg_remaining_move_pct", 0)
                    summary["avg_wave_number"] = v2.get("summary", {}).get("avg_wave_number", 0)
            except Exception:
                pass
            save_result(run_id, code, summary)

        # 更新进度
        update_run_status(run_id, "running", progress=f"{i}/{len(codes)}")

    # 生成整体汇总
    from at0.backtest import aggregate_batch
    overall = aggregate_batch(per_stock, all_trades_count)
    elapsed = time.time() - t0
    update_run_status(run_id, "completed", duration_s=elapsed, progress=f"{len(codes)}/{len(codes)}")


def _extract_trades(result: dict) -> list[dict]:
    """从回测结果中提取交易列表。"""
    trades = []
    for day_result in result.get("daily_results", []):
        for t in day_result.get("trades", []):
            t["date"] = day_result.get("date", "")
            trades.append(t)
    return trades


def _show_task_list() -> None:
    """显示回测任务列表，运行中任务可一键停止。"""
    st.markdown("---")
    st.subheader("📋 回测任务列表")

    runs = get_runs(limit=20)
    if not runs:
        st.caption("暂无任务记录。前往上方运行一次回测。")
        return

    for r in runs:
        status = r.get("status")
        cols = st.columns([0.5, 3.2, 2.3, 2, 1.2])
        with cols[0]:
            st.write(f"**#{r['id']}**")
        with cols[1]:
            st.write(r.get("name") or f"Run #{r['id']}")
        with cols[2]:
            if status == "running":
                st.write(f"🟡 运行中 {r.get('progress', '')}")
            elif status == "completed":
                st.write("🟢 完成")
            elif status == "failed":
                st.write("🔴 失败")
            elif status == "cancelled":
                st.write("⚪ 已停止")
            else:
                st.write(status)
        with cols[3]:
            st.write(f"{r.get('start_date', '?')[:10]}~{r.get('end_date', '?')[:10]}")
        with cols[4]:
            if status == "running":
                if st.button("🛑 停止", key=f"stop_{r['id']}",
                             use_container_width=True):
                    cancel_run(r["id"])
                    st.toast(f"已向 Run #{r['id']} 发送停止信号")
                    st.rerun()


def _poll_backtest_progress(run_id: int | None) -> None:
    """非阻塞进度轮询：每次脚本运行仅短暂等待后自动刷新，不卡住界面。"""
    placeholder = st.empty()
    if run_id is None:
        st.session_state.bt_running = False
        return

    run = get_run(run_id)
    if run is None:
        st.session_state.bt_running = False
        placeholder.error("❌ 运行记录丢失")
        return

    status = run.get("status", "")
    prog = run.get("progress", "")
    done = total = 0
    if prog and "/" in prog:
        try:
            a, b = prog.split("/")
            done, total = int(a), int(b)
        except ValueError:
            pass
    pct = done / total if total > 0 else 0

    if status == "running":
        placeholder.progress(pct, text=f"正在执行: {prog} ({pct*100:.0f}%)")
        st.info(f"⏳ 进度: {prog} 只股票 — 页面每 1.5 秒自动刷新（后台线程不阻塞界面）")
        if st.button("🛑 停止任务", key="stop_poll", use_container_width=True):
            cancel_run(run_id)
            st.toast(f"已向 Run #{run_id} 发送停止信号")
            st.rerun()
        time.sleep(1.5)
        st.rerun()
    elif status == "completed":
        st.session_state.bt_running = False
        placeholder.success(f"✅ Run #{run_id} 已完成！")
        st.page_link("pages/results.py", label="👉 查看结果")
    elif status == "failed":
        st.session_state.bt_running = False
        placeholder.error(f"❌ 回测失败: {run.get('error', '未知错误')}")
    elif status == "cancelled":
        st.session_state.bt_running = False
        placeholder.warning(f"⚪ Run #{run_id} 已被手动停止（进度 {prog}）")
        st.page_link("pages/results.py", label="👉 查看结果")
    else:
        st.session_state.bt_running = False