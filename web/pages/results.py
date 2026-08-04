"""
QuantWeb — 结果查看页面
=======================
浏览历史回测结果，查看细节和图表。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from web.results_db import get_runs, get_run_summary, get_run_results, get_run


@st.cache_data(ttl=30)
def _cached_runs(limit: int = 50):
    return get_runs(limit=limit)


@st.cache_data(ttl=30)
def _cached_run_summary(run_id: int):
    return get_run_summary(run_id)


@st.cache_data(ttl=30)
def _cached_run_results(run_id: int):
    return get_run_results(run_id)


@st.cache_data(ttl=120)
def _cached_load_kline(code: str, start_date: str, end_date: str):
    from backtest_zz500 import load_multi_day_zz500
    from at0.paths import ZZ500_5MIN_DIR
    return load_multi_day_zz500(code, start_date, end_date, ZZ500_5MIN_DIR)


@st.fragment(run_every=2)
def _running_status_fragment(run_id: int) -> None:
    """运行中状态：fragment 局部每 2 秒刷新进度。

    直接查库读取最新状态（绕过 _cached_runs 的 30s ttl 缓存），确保进度实时。
    当检测到状态离开 running（completed/failed/cancelled）时触发全页 rerun，
    让上方汇总指标和图表加载最新结果。
    """
    from web.results_db import get_run
    run = get_run(run_id)
    if run is None:
        st.warning("运行记录已消失")
        return
    if run["status"] != "running":
        # 状态已变更，触发整页刷新以加载结果
        st.toast("✅ 回测状态已变更，正在刷新页面...")
        st.rerun()
        return
    prog = run.get("progress", "")
    st.warning(f"⏳ 回测正在执行中... 进度: {prog}")
    if prog:
        parts = prog.split("/")
        if len(parts) == 2:
            try:
                done, total = int(parts[0]), int(parts[1])
                st.progress(done / total if total > 0 else 0,
                            text=f"已处理 {done}/{total} 只股票")
            except ValueError:
                pass
    st.caption("⏱️ 每 2 秒自动刷新进度（fragment 局部刷新，不阻塞其他页面交互）")


def show() -> None:
    st.title("📈 结果查看")
    st.caption("浏览历史回测运行结果")

    if st.button("🔄 刷新数据"):
        _cached_runs.clear()
        _cached_run_summary.clear()
        _cached_run_results.clear()
        st.rerun()

    runs = _cached_runs(50)
    if not runs:
        st.info("还没有回测运行记录。前往「⚙️ 回测配置」运行第一次回测。")
        return

    # 运行选择器
    run_options = {}
    for r in runs:
        rid = r['id']
        rname = r.get('name', '')
        rcreated = r['created_at'][:19]
        rstatus = r['status']
        run_options[f"#{rid} {rname} ({rcreated}) — {rstatus}"] = rid
    selected_label = st.selectbox("选择回测运行", list(run_options.keys()), index=0)
    selected_run_id = run_options[selected_label]

    # 获取选中运行的信息
    selected_run = next(r for r in runs if r["id"] == selected_run_id)
    summary = _cached_run_summary(selected_run_id)
    results = _cached_run_results(selected_run_id)

    # 运行状态卡片
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        status_color = "🟢" if selected_run["status"] == "completed" else \
                       "🟡" if selected_run["status"] == "running" else "🔴"
        s_status = selected_run['status']
        st.metric("状态", f"{status_color} {s_status}")
    with col2:
        s_start = selected_run.get('start_date', '?')[:10]
        s_end = selected_run.get('end_date', '?')[:10]
        st.metric("股票范围", f"{s_start} ~ {s_end}")
    with col3:
        st.metric("股票数", summary["stocks"] if summary else 0)
    with col4:
        st.metric("总配对交易", summary["total_paired"] if summary else 0)
    with col5:
        if selected_run.get("duration_s"):
            s_duration = selected_run['duration_s']
            st.metric("耗时", f"{s_duration:.1f}s")

    if selected_run["status"] == "running":
        # 用 fragment 做局部自动刷新，替代原来的 time.sleep(2)+st.rerun()。
        # 原写法会同步阻塞主线程 2 秒，期间用户点击得不到响应；且整页 rerun
        # 会重跑所有上方逻辑。fragment(run_every=2) 只重跑这个小函数，
        # 主线程不阻塞，其他页面交互即时响应。
        _running_status_fragment(selected_run_id)
        return

    if selected_run["status"] == "failed":
        st.error(f"❌ 回测执行失败: {selected_run.get('error', '未知错误')}")
        return

    if selected_run["status"] == "cancelled":
        st.warning(f"⚪ 该运行已被手动停止（进度 {selected_run.get('progress', '')}），以下为已完成的个股结果。")

    if not results:
        st.warning("该运行没有结果数据。")
        return

    # ── 汇总指标 ──
    st.subheader("整体汇总")

    df = pd.DataFrame(results)
    total_net = df["net_pnl"].sum()
    profitable_count = len(df[df["net_pnl"] > 0])
    avg_wr = df["win_rate"].mean()
    avg_ce = df["avg_ce"].mean()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总净盈亏", f"{total_net:+.2f}",
                  delta=f"{profitable_count}/{len(df)} 盈利股票")
    with col2:
        st.metric("平均胜率", f"{avg_wr*100:.1f}%")
    with col3:
        st.metric("平均 CE", f"{avg_ce*100:.1f}%")
    with col4:
        total_pf = df["profit_factor"].mean()
        st.metric("平均 Profit Factor", f"{total_pf:.2f}")

    # ── 图表（懒加载：只渲染当前选中的视图，避免切页时一次性加载全部重图）──
    # Streamlit 的 st.tabs 会无条件执行所有 with 块；本页 K 线图需从磁盘加载
    # 多年 5 分钟数据并构建多个 Plotly 图，若每次切页都全跑会严重卡顿。
    # 改用 radio 选择 + 条件执行，仅选中项才计算，导航/切 tab 即时响应。
    view_options = ["个股净盈亏", "胜率分布", "CE 分析", "📊 资金曲线", "📈 K线图", "个股明细"]
    active_view = st.radio(
        "图表视图", view_options, index=0, horizontal=True, key="result_view_tab",
    )

    if active_view == "个股净盈亏":
        _show_net_pnl_ranking(df)

    elif active_view == "胜率分布":
        fig2 = px.scatter(
            df,
            x="win_rate", y="net_pnl",
            title="胜率 vs 净盈亏",
            hover_data=["code", "paired_trades", "profit_factor"],
            labels={"win_rate": "胜率", "net_pnl": "净盈亏"},
            color="paired_trades",
            color_continuous_scale="Viridis",
        )
        fig2.add_hline(y=0, line_color="gray", line_width=1)
        fig2.add_vline(x=0.5, line_color="gray", line_width=1, line_dash="dash")
        st.plotly_chart(fig2, use_container_width=True)

    elif active_view == "CE 分析":
        df_ce = df[df["avg_ce"] > 0].copy()
        if len(df_ce) > 0:
            fig3 = px.scatter(
                df_ce,
                x="avg_ce", y="net_pnl",
                title="Capture Efficiency vs 净盈亏",
                hover_data=["code", "win_rate", "avg_entry_delay"],
                labels={"avg_ce": "CE", "net_pnl": "净盈亏"},
                color="avg_entry_delay",
                color_continuous_scale="RdYlGn_r",
            )
            fig3.add_hline(y=0, line_color="gray", line_width=1)
            st.plotly_chart(fig3, use_container_width=True)

            # CE 分布直方图
            fig4 = px.histogram(
                df_ce, x="avg_ce", nbins=20,
                title="CE 分布",
                labels={"avg_ce": "CE"},
            )
            fig4.add_vline(x=0.55, line_color="green", line_width=2, line_dash="dash",
                           annotation_text="目标 55%")
            st.plotly_chart(fig4, use_container_width=True)
        else:
            st.info("暂无 CE 数据")

    elif active_view == "📊 资金曲线":
        _show_equity_curve(selected_run, results, selected_run_id)

    elif active_view == "📈 K线图":
        _show_kline_tab(selected_run, results, selected_run_id)

    elif active_view == "个股明细":
        display_cols = ["code", "paired_trades", "win_rate", "net_pnl",
                        "profit_factor", "payoff_ratio", "avg_ce",
                        "avg_entry_delay", "avg_exit_delay"]
        df_display = df[display_cols].copy()
        df_display["win_rate"] = df_display["win_rate"].apply(lambda x: f"{x*100:.1f}%")
        df_display["avg_ce"] = df_display["avg_ce"].apply(
            lambda x: f"{x*100:.1f}%" if x > 0 else "N/A")
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "code": "股票代码",
                "paired_trades": "配对交易",
                "win_rate": "胜率",
                "net_pnl": st.column_config.NumberColumn("净盈亏", format="%+.2f"),
                "profit_factor": "PF",
                "payoff_ratio": "盈亏比",
                "avg_ce": "CE",
                "avg_entry_delay": "入场延迟",
                "avg_exit_delay": "退出延迟",
            },
        )

        # 下载 CSV
        csv = df_display.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 下载 CSV",
            csv,
            f"results_run_{selected_run_id}.csv",
            "text/csv",
        )


# ═══════════════════════════════════════════════════════════════
# 个股净盈亏排行（以排名展示，不以数值轴为主）
# ═══════════════════════════════════════════════════════════════
def _show_net_pnl_ranking(df: pd.DataFrame) -> None:
    """个股净盈亏排行：以排名为核心展示。

    横向条按收益排序、绿涨红跌着色；数值仅作为文本标签/悬停信息，
    不再以原始数值轴为主（优化「不要按数值展示」的诉求）。
    """
    df_sorted = df.sort_values("net_pnl", ascending=False).reset_index(drop=True)
    df_sorted.insert(0, "排名", range(1, len(df_sorted) + 1))
    colors = ["#10b981" if v >= 0 else "#ef4444" for v in df_sorted["net_pnl"]]

    # 横向条（第 1 名在顶部）
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_sorted["net_pnl"],
        y=df_sorted["排名"].astype(str) + ". " + df_sorted["code"],
        orientation="h",
        marker_color=colors,
        text=df_sorted["net_pnl"].apply(lambda v: f"{v:+.2f}"),
        textposition="outside",
        hovertemplate="%{y}<br>净盈亏 %{x:+.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="个股净盈亏排行（绿涨 / 红跌）",
        height=min(max(420, 30 * len(df_sorted)), 1400),
        yaxis=dict(autorange="reversed"),
        xaxis_title="净盈亏",
        margin=dict(l=10, r=40, t=40, b=30),
    )
    fig.add_vline(x=0, line_color="gray", line_width=1)
    st.plotly_chart(fig, use_container_width=True)

    # 排行榜单（带排名号）
    rank_df = df_sorted[["排名", "code", "net_pnl", "win_rate",
                         "paired_trades", "profit_factor"]].copy()
    rank_df["win_rate"] = rank_df["win_rate"].apply(lambda x: f"{x*100:.1f}%")
    st.dataframe(
        rank_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "排名": st.column_config.NumberColumn("排名", format="%d"),
            "code": "股票代码",
            "net_pnl": st.column_config.NumberColumn("净盈亏", format="%+.2f"),
            "win_rate": "胜率",
            "paired_trades": "配对交易",
            "profit_factor": "PF",
        },
    )


# ═══════════════════════════════════════════════════════════════
# 资金曲线
# ═══════════════════════════════════════════════════════════════
def _show_equity_curve(selected_run: dict, results: list[dict], run_id: int) -> None:
    """显示累计 PnL 曲线和回撤曲线。"""
    st.subheader("累计净盈亏曲线")

    if not results:
        st.info("暂无数据")
        return

    df = pd.DataFrame(results)
    df_sorted = df.sort_values("net_pnl", ascending=False).reset_index(drop=True)

    # 累计 PnL 曲线（按股票逐个累加，模拟资金曲线）
    cum_pnl = df_sorted["net_pnl"].cumsum()

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("累计净盈亏", "回撤"),
    )

    # 资金曲线
    fig.add_trace(
        go.Scatter(
            x=list(range(len(cum_pnl))),
            y=cum_pnl,
            mode="lines",
            name="累计 PnL",
            line=dict(color="#10b981", width=2),
            fill="tozeroy",
            fillcolor="rgba(16, 185, 129, 0.1)",
        ),
        row=1, col=1,
    )
    fig.add_hline(y=0, line_color="gray", line_width=1, row=1, col=1)

    # 回撤曲线
    peak = cum_pnl.cummax()
    drawdown = (cum_pnl - peak) / peak.replace(0, 1) * 100
    fig.add_trace(
        go.Scatter(
            x=list(range(len(drawdown))),
            y=drawdown,
            mode="lines",
            name="回撤 %",
            line=dict(color="#ef4444", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(239, 68, 68, 0.1)",
        ),
        row=2, col=1,
    )
    fig.add_hline(y=0, line_color="gray", line_width=1, row=2, col=1)

    fig.update_layout(
        height=500,
        showlegend=False,
        yaxis_title="累计 PnL",
    )
    # 副轴标题按网格位置设置，避免 xaxis2_title / yaxis2_title 在旧版 Plotly 报错
    fig.update_xaxes(title_text="股票 (按净盈亏排序)", row=2, col=1)
    fig.update_yaxes(title_text="回撤 %", row=2, col=1)
    fig.update_xaxes(tickvals=list(range(0, len(df_sorted), max(1, len(df_sorted) // 10))),
                     ticktext=df_sorted["code"].iloc[::max(1, len(df_sorted) // 10)].tolist())
    st.plotly_chart(fig, use_container_width=True)

    # 关键指标
    total_net = cum_pnl.iloc[-1] if len(cum_pnl) > 0 else 0
    max_dd = drawdown.min() if len(drawdown) > 0 else 0
    profitable = len(df[df["net_pnl"] > 0])
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总净盈亏", f"{total_net:+.2f}")
    with col2:
        st.metric("最大回撤", f"{max_dd:.2f}%")
    with col3:
        st.metric("盈利股票", f"{profitable}/{len(df)}")
    with col4:
        st.metric("盈利比", f"{profitable/len(df)*100:.1f}%" if len(df) > 0 else "N/A")

    # 简要说明
    st.caption("💡 资金曲线按个股净盈亏排序累加，X 轴为股票代码，非时间序列。")


# ═══════════════════════════════════════════════════════════════
# K线图 + 交易标记
# ═══════════════════════════════════════════════════════════════
@st.fragment
def _show_kline_tab(selected_run: dict, results: list[dict], run_id: int) -> None:
    """显示个股 K 线图与交易标记。

    用 @st.fragment 包裹：切股票 / 切均线开关 / 拖日期范围时只局部重跑本函数，
    不触发整页 rerun，避免上方汇总指标、汇总表格跟着重算。
    """
    st.subheader("个股 K 线图")

    if not results:
        st.info("暂无数据")
        return

    # 股票选择器
    # 注意：save_result 会把 error=None 写入列，读回时 "error" key 恒存在，
    # 故不能用 `if "error" not in r`（会把全部股票过滤掉），应判断值是否为真。
    codes = [r["code"] for r in results if not r.get("error")]
    if not codes:
        st.info("该运行没有有效的股票数据")
        return

    selected_code = st.selectbox("选择股票", codes, index=0, key="kline_code")

    # 获取该股票的回测结果
    stock_result = next((r for r in results if r["code"] == selected_code), None)
    if stock_result is None:
        st.warning(f"未找到股票 {selected_code} 的数据")
        return

    start_date = selected_run.get("start_date", "2023-07-25")[:10]
    end_date = selected_run.get("end_date", "2026-07-22")[:10]

    # 用 st.status 替代 st.spinner：显示带步骤的"正在读数据"反馈，
    # 让用户明确感知是"在读 3 年 5min JSON"而非"卡住了"。
    with st.status(f"正在加载 {selected_code} 的 K 线数据（{start_date} ~ {end_date}）...",
                   expanded=True) as status:
        st.caption("从磁盘读取 5 分钟 K 线 JSON 并聚合为日线 OHLCV...")
        try:
            daily_bars, daily_prev, _ = _cached_load_kline(
                selected_code, start_date, end_date,
            )
        except Exception as e:
            st.error(f"加载 K 线数据失败: {e}")
            return
        status.update(label=f"K 线数据加载完成（{len(daily_bars)} 个交易日）",
                      state="complete", expanded=False)

    if not daily_bars:
        st.warning(f"未找到 {selected_code} 在 {start_date} ~ {end_date} 范围内的 K 线数据")
        return

    # 构建日线 OHLCV
    ohlc_data = _build_daily_ohlc(daily_bars)
    if not ohlc_data:
        st.warning("OHLC 数据为空")
        return

    df_ohlc = pd.DataFrame(ohlc_data)
    df_ohlc["date"] = pd.to_datetime(df_ohlc["date"])

    # 选择时间范围
    date_range = st.slider(
        "选择日期范围",
        min_value=df_ohlc["date"].min().to_pydatetime(),
        max_value=df_ohlc["date"].max().to_pydatetime(),
        value=(df_ohlc["date"].min().to_pydatetime(), df_ohlc["date"].max().to_pydatetime()),
        format="YYYY-MM-DD",
        key="kline_date_range",
    )
    df_ohlc = df_ohlc[(df_ohlc["date"] >= pd.Timestamp(date_range[0])) &
                       (df_ohlc["date"] <= pd.Timestamp(date_range[1]))]

    if len(df_ohlc) == 0:
        st.info("所选日期范围内无数据")
        return

    # 计算均线
    show_ma = st.checkbox("显示均线 (MA5/MA20)", value=True, key="kline_ma")
    show_volume = st.checkbox("显示成交量", value=True, key="kline_vol")

    # 构建 K 线图
    fig = _build_candlestick_chart(df_ohlc, selected_code, show_ma, show_volume)
    st.plotly_chart(fig, use_container_width=True)

    # 显示股票信息
    col1, col2, col3 = st.columns(3)
    with col1:
        close_last = df_ohlc['close'].iloc[-1]
        st.metric("最新收盘", f"{close_last:.2f}")
    with col2:
        close_first = df_ohlc['close'].iloc[0]
        st.metric("区间涨幅", f"{(close_last / close_first - 1)*100:.2f}%")
    with col3:
        st.metric("区间天数", len(df_ohlc))

    # 股票指标
    st.subheader(f"{selected_code} 回测指标")
    metric_cols = ["paired_trades", "win_rate", "net_pnl", "profit_factor", "payoff_ratio", "avg_ce"]
    metric_labels = ["配对交易", "胜率", "净盈亏", "PF", "盈亏比", "CE"]
    cols = st.columns(len(metric_cols))
    for i, (key, label) in enumerate(zip(metric_cols, metric_labels)):
        val = stock_result.get(key, "N/A")
        if key == "win_rate" and isinstance(val, (int, float)):
            val = f"{val*100:.1f}%"
        elif key == "avg_ce" and isinstance(val, (int, float)):
            val = f"{val*100:.1f}%" if val > 0 else "N/A"
        elif isinstance(val, float):
            val = f"{val:+.2f}" if key == "net_pnl" else f"{val:.2f}"
        with cols[i]:
            st.metric(label, val)


def _build_daily_ohlc(daily_bars: dict) -> list[dict]:
    """从5分钟 K 线数据构建日线 OHLCV。"""
    ohlc = []
    for date, bars in sorted(daily_bars.items()):
        if not bars:
            continue
        opens = [b.get("open", 0) for b in bars if "open" in b]
        highs = [b.get("high", 0) for b in bars if "high" in b]
        lows = [b.get("low", 0) for b in bars if "low" in b]
        closes = [b.get("close", 0) for b in bars if "close" in b]
        volumes = [b.get("volume", 0) for b in bars if "volume" in b]
        if not opens:
            continue
        ohlc.append({
            "date": date,
            "open": opens[0],
            "high": max(highs),
            "low": min(lows),
            "close": closes[-1],
            "volume": sum(volumes),
        })
    return ohlc


def _build_candlestick_chart(
    df: pd.DataFrame, code: str,
    show_ma: bool = True, show_volume: bool = True,
) -> go.Figure:
    """构建 Plotly 蜡烛图。"""
    if show_volume:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.7, 0.3],
        )
    else:
        fig = make_subplots(rows=1, cols=1)

    # K 线
    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name=code,
            increasing_line_color="#10b981",
            decreasing_line_color="#ef4444",
        ),
        row=1, col=1,
    )

    # 均线
    if show_ma and len(df) >= 5:
        df["ma5"] = df["close"].rolling(5).mean()
        if len(df) >= 20:
            df["ma20"] = df["close"].rolling(20).mean()
        fig.add_trace(
            go.Scatter(x=df["date"], y=df["ma5"],
                       mode="lines", name="MA5",
                       line=dict(color="#f59e0b", width=1.5)),
            row=1, col=1,
        )
        if "ma20" in df.columns:
            fig.add_trace(
                go.Scatter(x=df["date"], y=df["ma20"],
                           mode="lines", name="MA20",
                           line=dict(color="#8b5cf6", width=1.5)),
                row=1, col=1,
            )

    # 成交量
    if show_volume and "volume" in df.columns:
        colors = ["#10b981" if df["close"].iloc[i] >= df["open"].iloc[i] else "#ef4444"
                  for i in range(len(df))]
        fig.add_trace(
            go.Bar(x=df["date"], y=df["volume"],
                   name="成交量", marker_color=colors,
                   opacity=0.5),
            row=2, col=1,
        )

    fig.update_layout(
        title=f"{code} 日线 K 线图",
        height=600 if show_volume else 450,
        hovermode="x unified",
        yaxis_title="价格",
    )
    # 成交量副轴标题按网格位置设置，避免直接用 yaxis2 / yaxis2_title
    # （部分 Plotly 版本不把 yaxis2 当作 Layout 的合法属性，会抛 ValueError）
    if show_volume:
        fig.update_yaxes(title_text="成交量", row=2, col=1)
    # 关闭价格主图（row=1）的 rangeslider
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    return fig