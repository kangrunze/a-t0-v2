"""
QuantWeb — 研究工作台
====================
首页。替代原"仪表盘"+"回测配置"。
显示研发进度趋势、运行队列、发起新回测表单。
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify

bp = Blueprint("workbench", __name__, url_prefix="/")


@bp.route("/")
def index():
    """研究工作台首页。"""
    return render_template("workbench.html")


# ═══════════════════════════════════════════════════════════════
# API: 研发进度趋势
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/workbench/trend")
def trend():
    """返回最近 N 次 completed 运行的 median net_pnl + median CE 趋势。

    前端渲染为折线图（双 Y 轴 + IQR 阴影带）。
    """
    from web.results_db import get_conn

    limit = request.args.get("limit", 30, type=int)
    limit = min(max(limit, 10), 100)

    conn = get_conn()
    rows = conn.execute(
        """SELECT id, name, created_at, params_override
           FROM runs WHERE status='completed'
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        return jsonify({"traces": []})

    # 按时间升序
    rows = list(reversed(rows))

    points = []
    for r in rows:
        run_id = r["id"]
        # 读取个股结果计算 median
        conn2 = get_conn()
        results = conn2.execute(
            "SELECT net_pnl, avg_ce FROM results WHERE run_id=?",
            (run_id,),
        ).fetchall()
        conn2.close()

        net_pnls = [x["net_pnl"] for x in results if x["net_pnl"] is not None]
        ces = [x["avg_ce"] for x in results if x["avg_ce"] is not None]

        if not net_pnls:
            continue

        median_net = statistics.median(net_pnls)
        median_ce = statistics.median(ces) if ces else 0.0

        # IQR
        sorted_net = sorted(net_pnls)
        n = len(sorted_net)
        q1_net = sorted_net[n // 4] if n > 1 else median_net
        q3_net = sorted_net[3 * n // 4] if n > 1 else median_net

        sorted_ce = sorted(ces) if ces else [0]
        m = len(sorted_ce)
        q1_ce = sorted_ce[m // 4] if m > 1 else median_ce
        q3_ce = sorted_ce[3 * m // 4] if m > 1 else median_ce

        points.append({
            "run_id": run_id,
            "label": r["name"] or f"#{run_id}",
            "created_at": r["created_at"][:19] if r["created_at"] else "",
            "median_net": round(median_net, 2),
            "q1_net": round(q1_net, 2),
            "q3_net": round(q3_net, 2),
            "median_ce": round(median_ce, 4),
            "q1_ce": round(q1_ce, 4),
            "q3_ce": round(q3_ce, 4),
        })

    if not points:
        return jsonify({"traces": []})

    labels = [p["label"] for p in points]
    created = [p["created_at"] for p in points]

    # net_pnl 迹线
    trace_net = {
        "x": created,
        "y": [p["median_net"] for p in points],
        "type": "scatter",
        "mode": "lines+markers",
        "name": "median net_pnl",
        "line": {"color": "#10b981", "width": 2},
        "marker": {"size": 5, "color": "#10b981"},
        "yaxis": "y",
    }
    # net_pnl IQR 阴影
    trace_net_iqr = {
        "x": created + list(reversed(created)),
        "y": [p["q3_net"] for p in points] + list(reversed([p["q1_net"] for p in points])),
        "type": "scatter",
        "fill": "toself",
        "fillcolor": "rgba(16, 185, 129, 0.15)",
        "line": {"color": "rgba(0,0,0,0)", "width": 0},
        "showlegend": True,
        "name": "net_pnl IQR",
        "yaxis": "y",
    }
    # CE 迹线（右轴）
    trace_ce = {
        "x": created,
        "y": [p["median_ce"] for p in points],
        "type": "scatter",
        "mode": "lines+markers",
        "name": "median CE (右轴)",
        "line": {"color": "#6366f1", "width": 2, "dash": "dot"},
        "marker": {"size": 5, "color": "#6366f1"},
        "yaxis": "y2",
    }
    # CE IQR 阴影
    trace_ce_iqr = {
        "x": created + list(reversed(created)),
        "y": [p["q3_ce"] for p in points] + list(reversed([p["q1_ce"] for p in points])),
        "type": "scatter",
        "fill": "toself",
        "fillcolor": "rgba(99, 102, 241, 0.12)",
        "line": {"color": "rgba(0,0,0,0)", "width": 0},
        "showlegend": True,
        "name": "CE IQR",
        "yaxis": "y2",
    }

    return jsonify({
        "traces": [trace_net_iqr, trace_net, trace_ce_iqr, trace_ce],
    })


# ═══════════════════════════════════════════════════════════════
# API: 发起回测
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/backtest/run", methods=["POST"])
def backtest_run():
    """发起一次回测任务。

    从表单接收参数，写入 runs 表，提交到后台线程池。
    返回 JSON: {"run_id": int} 或 {"error": str}
    """
    from web.results_db import save_run
    from web.runner import submit_backtest

    name = request.form.get("name", "").strip()
    if not name:
        name = f"回测_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}"

    stock_pool = request.form.get("stock_pool", "v3_sample_5.txt").strip()
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    base_shares = request.form.get("base_shares", 3000, type=int)
    preset = request.form.get("preset", "").strip()

    # 参数覆盖
    params_override = {}
    if preset:
        from web.results_db import get_presets
        for p in get_presets():
            if p["name"] == preset:
                params_override = p.get("params_override", {})
                break

    # 解析股票列表
    codes = _resolve_stock_pool(stock_pool)
    if not codes:
        return jsonify({"error": f"股票池 '{stock_pool}' 为空或不存在"}), 400

    # 创建运行记录
    run_id = save_run(
        name=name,
        stock_pool=stock_pool,
        start_date=start_date,
        end_date=end_date,
        params_override=params_override or None,
        tag="web",
    )

    # 提交后台线程
    submit_backtest(
        run_id=run_id,
        codes=codes,
        start_date=start_date,
        end_date=end_date,
        base_shares=base_shares,
        params_override=params_override or None,
    )

    return jsonify({"run_id": run_id})


# ═══════════════════════════════════════════════════════════════
# API: 运行队列状态
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/workbench/queue")
def queue_status():
    """返回当前进行中/排队中的任务列表。"""
    from web.results_db import get_runs

    all_runs = get_runs(limit=50)
    active = [r for r in all_runs if r.get("status") in ("pending", "running")]
    for r in active:
        prog = r.get("progress", "") or ""
        if "/" in prog:
            parts = prog.split("/")
            r["done"] = int(parts[0]) if parts[0].isdigit() else 0
            r["total"] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        else:
            r["done"] = 0
            r["total"] = 0
    return jsonify({"active": active})


def _resolve_stock_pool(pool_name: str) -> list[str]:
    """解析股票池名称，返回股票代码列表。"""
    from at0.paths import PROJECT_ROOT, DATA_ROOT

    if pool_name == "all":
        from scripts.backtest_zz500 import list_all_zz500_codes
        from at0.paths import ZZ500_5MIN_DIR
        return list_all_zz500_codes(ZZ500_5MIN_DIR)

    # 语义化别名
    pool_map = {
        "v3_sample_100.txt": "v3_sample_100.txt",
        "v3_sample_20.txt": "v3_sample_20.txt",
        "v3_sample_5.txt": "v3_sample_5.txt",
        "100只": "v3_sample_100.txt",
        "20只": "v3_sample_20.txt",
        "5只": "v3_sample_5.txt",
    }
    resolved = pool_map.get(pool_name, pool_name)

    # 尝试从项目根读取
    candidates = [
        Path(resolved),
        PROJECT_ROOT / resolved,
        PROJECT_ROOT / "scripts" / resolved,
        DATA_ROOT / resolved,
    ]
    for path in candidates:
        if path.exists():
            codes = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    c = line.strip()
                    if c and not c.startswith("#"):
                        codes.append(c)
            return codes

    return []