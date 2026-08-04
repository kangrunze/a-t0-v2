"""
QuantWeb — 运行详情
====================
单次回测运行的完整结果页。
替代原"结果查看"页面。
"""
from __future__ import annotations

import json
import statistics
import time
from functools import lru_cache

from flask import Blueprint, render_template, request, jsonify, Response

bp = Blueprint("run", __name__, url_prefix="/runs")


@bp.route("/")
def list():
    """运行列表页。"""
    from web.results_db import get_runs
    runs = get_runs(limit=50)
    return render_template("run_list.html", runs=runs)


@bp.route("/<int:run_id>")
def detail(run_id: int):
    """运行详情页。"""
    return render_template("run.html", run_id=run_id)


# ═══════════════════════════════════════════════════════════════
# API: 运行汇总
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/runs/<int:run_id>/summary")
def run_summary(run_id: int):
    """返回某次运行的整体汇总 + 个股明细。

    Stage Gate 判定逻辑：
      - 从 overall 中提取 median + IQR
      - 样本量 < 30 → INCONCLUSIVE
      - 否则根据 median 正负判定 PASS/FAIL
    """
    from web.results_db import get_run, get_run_results

    run = get_run(run_id)
    if not run:
        return jsonify({"error": "运行记录不存在"}), 404

    results = get_run_results(run_id)
    if not results:
        return jsonify({"run": run, "error": "暂无结果数据"})

    # 基础汇总
    net_pnls = [r["net_pnl"] for r in results if r.get("net_pnl") is not None]
    win_rates = [r["win_rate"] for r in results if r.get("win_rate") is not None]
    ces = [r["avg_ce"] for r in results if r.get("avg_ce") is not None]
    pfs = [r["profit_factor"] for r in results if r.get("profit_factor") is not None]
    payoffs = [r["payoff_ratio"] for r in results if r.get("payoff_ratio") is not None]

    def _median_or_zero(arr):
        return round(statistics.median(arr), 4) if arr else 0.0

    def _mean_or_zero(arr):
        return round(sum(arr) / len(arr), 4) if arr else 0.0

    total_net = sum(net_pnls) if net_pnls else 0.0
    median_net = _median_or_zero(net_pnls)
    mean_net = _mean_or_zero(net_pnls)
    median_wr = _median_or_zero(win_rates)
    mean_wr = _mean_or_zero(win_rates)
    median_ce = _median_or_zero(ces)
    mean_ce = _mean_or_zero(ces)
    median_pf = _median_or_zero(pfs)
    median_payoff = _median_or_zero(payoffs)

    # Stage Gate 判定
    n_samples = len(results)
    if n_samples < 30:
        gate = "INCONCLUSIVE"
        gate_reason = f"样本量 {n_samples} < 30，统计口径不足"
    elif median_net > 0:
        gate = "PASS"
        gate_reason = f"median net_pnl = {median_net:+,.2f} > 0"
    else:
        gate = "FAIL"
        gate_reason = f"median net_pnl = {median_net:+,.2f} ≤ 0"

    # 个股明细
    stock_list = []
    for r in results:
        stock_list.append({
            "code": r["code"],
            "paired_trades": r.get("paired_trades", 0),
            "win_rate": round(r.get("win_rate", 0) * 100, 1),
            "net_pnl": round(r.get("net_pnl", 0), 2),
            "profit_factor": round(r.get("profit_factor", 0), 2),
            "payoff_ratio": round(r.get("payoff_ratio", 0), 4),
            "avg_ce": round(r.get("avg_ce", 0) * 100, 2),
            "error": r.get("error"),
        })

    return jsonify({
        "run": {
            "id": run["id"],
            "name": run.get("name", ""),
            "status": run.get("status", ""),
            "created_at": run.get("created_at", ""),
            "stock_pool": run.get("stock_pool", ""),
            "start_date": run.get("start_date", ""),
            "end_date": run.get("end_date", ""),
            "duration_s": run.get("duration_s"),
            "progress": run.get("progress", ""),
            "error": run.get("error"),
        },
        "gate": gate,
        "gate_reason": gate_reason,
        "n_samples": n_samples,
        "summary": {
            "total_net": round(total_net, 2),
            "median_net": median_net,
            "mean_net": mean_net,
            "median_wr": median_wr,
            "mean_wr": mean_wr,
            "median_ce": median_ce,
            "mean_ce": mean_ce,
            "median_pf": median_pf,
            "median_payoff": median_payoff,
        },
        "stocks": stock_list,
    })


# ═══════════════════════════════════════════════════════════════
# API: SSE 进度推送
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/runs/<int:run_id>/stream")
def run_stream(run_id: int):
    """SSE 端点：后台回测进度实时推送。

    前端用 EventSource 消费，不轮询。
    后端每 1 秒检查一次 runs.progress 是否变化。
    """
    from web.results_db import get_run

    def _generate():
        last_progress = None
        while True:
            run = get_run(run_id)
            if not run:
                yield f"event: error\ndata: {json.dumps({'error': 'run not found'})}\n\n"
                break

            status = run.get("status", "")
            progress = run.get("progress", "") or ""
            error = run.get("error", "") or ""

            # 仅 progress 变化时才推送
            if progress != last_progress:
                done, total = 0, 0
                if "/" in progress:
                    parts = progress.split("/")
                    done = int(parts[0]) if parts[0].isdigit() else 0
                    total = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                data = json.dumps({
                    "status": status,
                    "progress": progress,
                    "done": done,
                    "total": total,
                    "error": error,
                })
                yield f"event: progress\ndata: {data}\n\n"
                last_progress = progress

            if status in ("completed", "failed", "cancelled"):
                yield f"event: progress\ndata: {json.dumps({'status': status, 'done': total, 'total': total, 'progress': progress})}\n\n"
                break

            time.sleep(1)

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════
# API: K 线图
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/runs/<int:run_id>/kline/<code>")
def run_kline(run_id: int, code: str):
    """返回某只股票的 K 线图 HTML（Plotly 渲染）。

    服务端缓存（lru_cache, TTL ≈ 120s），前端按需加载。
    """
    html = _render_kline_chart(run_id, code)
    return jsonify({"html": html})


@lru_cache(maxsize=128)
def _render_kline_chart(run_id: int, code: str) -> str:
    """渲染 K 线图（带缓存）。"""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from at0.paths import ZZ500_5MIN_DIR
    from scripts.backtest_zz500 import load_multi_day_zz500

    # 获取运行记录以确定日期范围
    from web.results_db import get_run
    run = get_run(run_id)
    if not run:
        return "<p class='text-danger'>运行记录不存在</p>"

    start_date = run.get("start_date", "")
    end_date = run.get("end_date", "")

    # 加载数据
    daily_bars, daily_prev_closes, daily_meta = load_multi_day_zz500(
        code, start_date, end_date, ZZ500_5MIN_DIR,
    )
    if not daily_bars:
        return f"<p class='text-secondary'>股票 {code} 在区间内无数据</p>"

    # 构建 OHLC 数据
    dates = sorted(daily_bars.keys())
    ohlc_data = []
    volume_data = []
    for d in dates:
        bars = daily_bars[d]
        if not bars:
            continue
        opens = [b.get("open", 0) for b in bars]
        highs = [b.get("high", 0) for b in bars]
        lows = [b.get("low", 0) for b in bars]
        closes = [b.get("close", 0) for b in bars]
        volumes = [b.get("volume", 0) for b in bars]
        # 日级聚合
        ohlc_data.append({
            "date": d,
            "open": opens[0],
            "high": max(highs),
            "low": min(lows),
            "close": closes[-1],
        })
        volume_data.append({
            "date": d,
            "volume": sum(volumes),
        })

    if not ohlc_data:
        return f"<p class='text-secondary'>股票 {code} 无有效 K 线数据</p>"

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
    )

    # 蜡烛图
    fig.add_trace(go.Candlestick(
        x=[d["date"] for d in ohlc_data],
        open=[d["open"] for d in ohlc_data],
        high=[d["high"] for d in ohlc_data],
        low=[d["low"] for d in ohlc_data],
        close=[d["close"] for d in ohlc_data],
        increasing_line_color="#10b981",
        decreasing_line_color="#ef4444",
        name=code,
    ), row=1, col=1)

    # 成交量
    colors = ["#10b981" if ohlc_data[i]["close"] >= ohlc_data[i]["open"] else "#ef4444"
              for i in range(len(ohlc_data))]
    fig.add_trace(go.Bar(
        x=[d["date"] for d in volume_data],
        y=[d["volume"] for d in volume_data],
        marker_color=colors,
        name="成交量",
    ), row=2, col=1)

    fig.update_layout(
        height=400,
        margin=dict(l=10, r=10, t=20, b=10),
        template="plotly_white",
        showlegend=False,
        xaxis_rangeslider_visible=False,
        font=dict(family="JetBrains Mono, monospace", size=10),
    )
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    return fig.to_html(include_plotlyjs=False, full_html=False, div_id=f"kline-{code}")


# ═══════════════════════════════════════════════════════════════
# API: 取消运行
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/runs/<int:run_id>/cancel", methods=["POST"])
def cancel_run(run_id: int):
    """取消正在运行的回测。"""
    from web.runner import cancel_backtest
    cancel_backtest(run_id)
    return jsonify({"ok": True})