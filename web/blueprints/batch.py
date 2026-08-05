"""
QuantWeb — 批量回测报告查看 + 导入
==================================
「批量回测」标签：浏览 scripts/backtest_zz500.py / batch_backtest.py 生成的
batch_summary_*.json，展示整体汇总（overall）+ 逐股明细（per_stock），
并支持**一键导入为正式运行记录**（写入 runs/results 表），使 CLI/后台产物
在「运行详情 / 回测结果 / 研究工作台」中与 Web 发起的回测统一展示。

与 Web 单股回测运行（runs 表 / 回测结果标签）正交：
  - 批量回测是离线脚本产物，输出 batch_summary_*.json
  - 本蓝图只负责"查看 + 导入"，不触发回测；磁盘扫描兜底，无需重跑
  - 导入幂等：按 runs.source_file 去重，重复导入返回已有 run_id
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, render_template, jsonify, abort

bp = Blueprint("batch", __name__, url_prefix="/batch")

# 报告落盘根目录（与 scripts/batch_backtest.py 的 --out 默认 outputs/backtest 一致）
_BATCH_ROOT = Path(__file__).resolve().parent.parent.parent / "outputs" / "backtest"


def _list_batch_files():
    """按修改时间倒序列出所有 batch_summary_*.json。"""
    if not _BATCH_ROOT.is_dir():
        return []
    return sorted(
        _BATCH_ROOT.glob("batch_summary_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _safe_report_path(name: str) -> Path | None:
    """校验报告文件名，防目录穿越，返回绝对路径或 None。

    只允许纯文件名（不含路径分隔符、必须以 .json 结尾），
    且解析后必须落在 _BATCH_ROOT 之内并真实存在。
    """
    if not name or ("/" in name) or ("\\" in name) or not name.endswith(".json"):
        return None
    p = (_BATCH_ROOT / name).resolve()
    try:
        p.relative_to(_BATCH_ROOT.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


@bp.route("/")
def index():
    """批量回测报告页（标签主页）。"""
    return render_template("batch_reports.html")


@bp.route("/api/reports")
def api_reports():
    """列出所有批量回测报告（整体汇总摘要 + 导入状态）。"""
    from web.results_db import get_run_id_by_source_file

    reports = []
    for p in _list_batch_files():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        overall = data.get("overall", {}) or {}
        per = data.get("per_stock", []) or []
        n_err = sum(1 for s in per if s.get("error"))
        reports.append({
            "name": p.name,
            "mtime": p.stat().st_mtime,
            "start_date": data.get("start") or data.get("start_date", ""),
            "end_date": data.get("end") or data.get("end_date", ""),
            "source": data.get("source", ""),
            "pool_path": data.get("pool_path", ""),
            "stocks": overall.get("stocks", len(per)),
            "total_trades": overall.get("total_trades"),
            "paired_trades": overall.get("paired_trades"),
            "win_rate": overall.get("win_rate"),
            "net_pnl": overall.get("net_pnl"),
            "net_pnl_with_unrealized": overall.get("net_pnl_with_unrealized"),
            "profitable_stocks": overall.get("profitable_stocks"),
            "losing_stocks": overall.get("losing_stocks"),
            "n_ok": len(per) - n_err,
            "n_err": n_err,
            "imported_run_id": get_run_id_by_source_file(p.name),
        })
    return jsonify({"reports": reports})


@bp.route("/api/report/<name>")
def api_report(name: str):
    """返回某份批量回测报告的完整内容（overall + per_stock）。"""
    p = _safe_report_path(name)
    if not p:
        return abort(404, description="报告不存在或非法路径")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        return abort(500, description=f"读取报告失败: {ex}")
    return jsonify(data)


@bp.route("/api/report/<name>/import", methods=["POST"])
def api_import(name: str):
    """把某份批量回测报告导入为正式运行记录（幂等）。

    导入后可在「运行详情 / 回测结果 / 研究工作台」中查看该次 CLI 回测：
    Stage Gate 判定、个股排行、逐股 HTML 报告（如有落盘）、K 线图。
    """
    from web.results_db import import_batch_summary

    p = _safe_report_path(name)
    if not p:
        return abort(404, description="报告不存在或非法路径")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        return abort(500, description=f"读取报告失败: {ex}")

    if not data.get("per_stock"):
        return jsonify({"error": "报告无 per_stock 明细，无法导入"}), 400

    run_id = import_batch_summary(p.name, data)
    return jsonify({
        "ok": True,
        "run_id": run_id,
        "url": f"/runs/{run_id}",
        "n_stocks": len(data.get("per_stock", [])),
    })
