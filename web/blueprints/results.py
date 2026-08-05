"""
QuantWeb — 回测结果（HTML 报告查看）
====================================
新增「回测结果」标签：浏览历史回测运行，直接在内嵌 iframe 中查看
run_zz500_single 落盘的逐股 HTML 可视化报告（与 CLI 产物完全一致）。

HTML 报告是自包含完整文档（含 chart.js CDN + 内联样式），因此用 iframe
直接渲染，避免把完整 <html> 文档塞进 <div> 引发样式/脚本冲突。

HTML 路径解析：
  - 优先用 results.html_path 字段（新建回测会在 runner 中写入）
  - 兜底：按 code + 运行起止日期扫描 outputs/backtest 目录
    （历史 web_run_1 / web_run_2 等报告也能被找到，无需重跑）
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, render_template, jsonify, Response, abort

bp = Blueprint("results", __name__, url_prefix="/results")

# 报告落盘根目录（与 scripts/backtest_zz500.py 的 BACKTEST_OUTPUT_DIR 一致）
_REPORT_ROOT = Path(__file__).resolve().parent.parent.parent / "outputs" / "backtest"


def _find_html_on_disk(code: str, start_date: str, end_date: str) -> str | None:
    """按 code + 运行起止日期在 outputs/backtest 中模糊查找 HTML 报告。"""
    if not start_date or not end_date:
        return None
    try:
        from scripts.backtest_zz500 import normalize_code
        code_tag = normalize_code(code)["pure"]
        base = _REPORT_ROOT
        if not base.is_dir():
            return None
        cands = sorted(base.glob(f"{code_tag}_*_{start_date}_{end_date}_report.html"))
        return str(cands[-1]) if cands else None
    except Exception:
        return None


@bp.route("/")
def index():
    """回测结果页（标签主页）。"""
    from web.results_db import get_runs
    runs = get_runs(limit=50)
    return render_template("results.html", runs=runs)


@bp.route("/api/<int:run_id>/stocks")
def run_stocks(run_id: int):
    """返回某次运行的个股列表 + 是否已有 HTML 报告（含磁盘兜底）。"""
    from web.results_db import get_run, get_run_results

    run = get_run(run_id)
    if not run:
        return jsonify({"error": "运行记录不存在"}), 404

    start_date = run.get("start_date", "") or ""
    end_date = run.get("end_date", "") or ""

    results = get_run_results(run_id)
    stocks = []
    for r in results:
        db_path = r.get("html_path")
        has_html = bool(db_path) or bool(_find_html_on_disk(r["code"], start_date, end_date))
        stocks.append({
            "code": r["code"],
            "net_pnl": r.get("net_pnl", 0),
            "paired_trades": r.get("paired_trades", 0),
            "win_rate": r.get("win_rate", 0),
            "has_html": has_html,
        })
    return jsonify({
        "run": {
            "id": run["id"],
            "name": run.get("name", ""),
            "status": run.get("status", ""),
        },
        "stocks": stocks,
    })


@bp.route("/api/<int:run_id>/html/<code>")
def run_html(run_id: int, code: str):
    """读取并返回某只股票的 HTML 报告原文（供 iframe 渲染）。"""
    from web.results_db import get_run, get_run_results

    run = get_run(run_id)
    if not run:
        return abort(404, description="运行记录不存在")

    start_date = run.get("start_date", "") or ""
    end_date = run.get("end_date", "") or ""

    results = get_run_results(run_id)
    match = next((r for r in results if r["code"] == code), None)
    path = match.get("html_path") if match else None
    if not path:
        # 兜底：按 code + 运行起止日期扫描磁盘
        path = _find_html_on_disk(code, start_date, end_date)

    if not path:
        return abort(404, description="该股票无 HTML 报告（仅新建回测会落盘报告；历史运行可能缺失）")

    p = Path(path)
    if not p.is_file():
        return abort(404, description="HTML 报告文件不存在（可能已被清理）")

    # 目录穿越防护：限定在 outputs/backtest 之内
    try:
        p.resolve().relative_to(_REPORT_ROOT.resolve())
    except ValueError:
        return abort(403, description="非法路径")

    try:
        html = p.read_text(encoding="utf-8")
    except Exception as ex:
        return abort(500, description=f"读取 HTML 失败: {ex}")

    return Response(html, mimetype="text/html")
