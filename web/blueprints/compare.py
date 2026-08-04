"""
QuantWeb — 策略对比
====================
A/B 对比 + Stage Gate 判定 + 显著性检验（Wilcoxon signed-rank）。
"""
from __future__ import annotations

import json
import statistics

from flask import Blueprint, render_template, request, jsonify

bp = Blueprint("compare", __name__, url_prefix="")


@bp.route("/compare/")
def index():
    """策略对比首页。"""
    return render_template("compare.html")


# ═══════════════════════════════════════════════════════════════
# API: 可对比的运行列表
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/compare/runs")
def compare_runs():
    """返回已完成运行的列表（供选择器使用）。"""
    from web.results_db import get_runs
    runs = get_runs(limit=100)
    items = []
    for r in runs:
        if r.get("status") != "completed":
            continue
        items.append({
            "id": r["id"],
            "name": r.get("name", "") or f"#{r['id']}",
            "created_at": (r.get("created_at") or "")[:19],
            "stock_pool": r.get("stock_pool", ""),
            "tag": r.get("tag", ""),
        })
    return jsonify({"runs": items})


# ═══════════════════════════════════════════════════════════════
# API: 标签列表
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/compare/tags")
def compare_tags():
    """返回所有可用的 tag 列表。"""
    from web.results_db import get_run_tags
    return jsonify({"tags": get_run_tags()})


# ═══════════════════════════════════════════════════════════════
# API: 按标签分组对比
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/compare/group", methods=["POST"])
def compare_group():
    """按标签分组，对比两个标签下的所有已完成运行。"""
    data = request.get_json(force=True) or {}
    tag_a = (data.get("tag_a") or "").strip()
    tag_b = (data.get("tag_b") or "").strip()
    if not tag_a or not tag_b:
        return jsonify({"error": "需要指定 tag_a 和 tag_b"}), 400

    from web.results_db import get_runs_by_tag
    runs_a = get_runs_by_tag(tag_a, limit=50)
    runs_b = get_runs_by_tag(tag_b, limit=50)
    runs_a = [r for r in runs_a if r.get("status") == "completed"]
    runs_b = [r for r in runs_b if r.get("status") == "completed"]

    if not runs_a or not runs_b:
        return jsonify({"error": "某个标签下无已完成运行"}), 400

    # 聚合各标签下的所有个股结果
    results_a = _aggregate_tag_results(runs_a)
    results_b = _aggregate_tag_results(runs_b)

    comparison = _compute_comparison(
        {"name": tag_a, "results": results_a, "n_runs": len(runs_a)},
        {"name": tag_b, "results": results_b, "n_runs": len(runs_b)},
    )
    return jsonify(comparison)


# ═══════════════════════════════════════════════════════════════
# API: 手动选择运行对比
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/compare/run", methods=["POST"])
def compare_run():
    """对比 2-4 次手动选择的运行。"""
    data = request.get_json(force=True) or {}
    run_ids = data.get("run_ids", [])
    if not isinstance(run_ids, list) or len(run_ids) < 2:
        return jsonify({"error": "至少选择 2 次运行"}), 400
    if len(run_ids) > 4:
        return jsonify({"error": "最多对比 4 次运行"}), 400

    from web.results_db import get_runs_by_ids, get_run_results
    runs = get_runs_by_ids(run_ids)
    runs = [r for r in runs if r.get("status") == "completed"]
    if len(runs) < 2:
        return jsonify({"error": "选中的运行中有未完成的"}), 400

    # 构建每个运行的数据
    run_data_list = []
    for r in runs:
        results = get_run_results(r["id"])
        run_data_list.append({
            "name": r.get("name", "") or f"#{r['id']}",
            "run_id": r["id"],
            "results": results,
            "n_runs": 1,
        })

    # 两两对比（以第一个为基准）
    response = {
        "runs": [],
        "comparisons": [],
    }

    for rd in run_data_list:
        run_info = _compute_run_summary(rd["results"])
        run_info["name"] = rd["name"]
        run_info["run_id"] = rd["run_id"]
        response["runs"].append(run_info)

    # 基准 vs 其他
    base = run_data_list[0]
    for other in run_data_list[1:]:
        comparison = _compute_comparison(base, other)
        response["comparisons"].append(comparison)

    return jsonify(response)


# ═══════════════════════════════════════════════════════════════
# 内部：单次运行汇总 + Stage Gate 判定
# ═══════════════════════════════════════════════════════════════
def _compute_run_summary(results: list[dict]) -> dict:
    """计算单次运行的汇总指标 + Stage Gate 判定。"""
    net_pnls = [r["net_pnl"] for r in results if r.get("net_pnl") is not None]
    ces = [r["avg_ce"] for r in results if r.get("avg_ce") is not None]
    win_rates = [r["win_rate"] for r in results if r.get("win_rate") is not None]
    pfs = [r["profit_factor"] for r in results if r.get("profit_factor") is not None]
    payoffs = [r["payoff_ratio"] for r in results if r.get("payoff_ratio") is not None]

    def _median(arr):
        return round(statistics.median(arr), 4) if arr else 0.0

    def _mean(arr):
        return round(sum(arr) / len(arr), 4) if arr else 0.0

    n = len(results)
    median_net = _median(net_pnls)
    mean_net = _mean(net_pnls)

    # Stage Gate
    if n < 30:
        gate = "INCONCLUSIVE"
        gate_reason = f"样本量 {n} < 30"
    elif median_net > 0:
        gate = "PASS"
        gate_reason = f"median net_pnl = {median_net:+,.2f} > 0"
    else:
        gate = "FAIL"
        gate_reason = f"median net_pnl = {median_net:+,.2f} ≤ 0"

    # IQR
    sorted_net = sorted(net_pnls)
    q1 = sorted_net[len(sorted_net) // 4] if len(sorted_net) > 1 else median_net
    q3 = sorted_net[3 * len(sorted_net) // 4] if len(sorted_net) > 1 else median_net

    return {
        "n_samples": n,
        "total_net": round(sum(net_pnls), 2) if net_pnls else 0.0,
        "median_net": median_net,
        "mean_net": mean_net,
        "q1_net": round(q1, 2),
        "q3_net": round(q3, 2),
        "median_ce": _median(ces),
        "mean_ce": _mean(ces),
        "median_wr": _median(win_rates),
        "mean_wr": _mean(win_rates),
        "median_pf": _median(pfs),
        "median_payoff": _median(payoffs),
        "gate": gate,
        "gate_reason": gate_reason,
    }


# ═══════════════════════════════════════════════════════════════
# 内部：两两对比 + Wilcoxon 显著性检验
# ═══════════════════════════════════════════════════════════════
def _compute_comparison(base: dict, other: dict) -> dict:
    """对比两个运行/分组，计算差值 + 显著性检验。

    Args:
        base: 基准运行数据（含 results 列表）
        other: 对比运行数据（含 results 列表）

    Returns:
        包含各项指标对比和显著性检验结果的 dict
    """
    base_results = base["results"]
    other_results = other["results"]

    base_summary = _compute_run_summary(base_results)
    other_summary = _compute_run_summary(other_results)

    # 按股票代码配对
    base_by_code = {r["code"]: r for r in base_results}
    other_by_code = {r["code"]: r for r in other_results}
    common_codes = set(base_by_code.keys()) & set(other_by_code.keys())

    # 配对 net_pnl 差值
    paired_net = []
    for code in common_codes:
        b = base_by_code[code].get("net_pnl", 0) or 0
        o = other_by_code[code].get("net_pnl", 0) or 0
        paired_net.append({"code": code, "base": b, "other": o, "diff": o - b})

    # Wilcoxon signed-rank test
    wilcoxon_result = _wilcoxon_test(paired_net)

    # 指标对比
    metrics = ["total_net", "median_net", "mean_net", "median_ce", "mean_ce",
               "median_wr", "mean_wr", "median_pf", "median_payoff"]
    metric_diffs = []
    for m in metrics:
        bv = base_summary.get(m, 0)
        ov = other_summary.get(m, 0)
        metric_diffs.append({
            "metric": m,
            "base": bv,
            "other": ov,
            "diff": round(ov - bv, 4),
        })

    # 找到共同股票中 improvement/worsening 最大的
    sorted_by_diff = sorted(paired_net, key=lambda x: x["diff"], reverse=True)

    return {
        "base_name": base["name"],
        "other_name": other["name"],
        "base": base_summary,
        "other": other_summary,
        "common_stocks": len(common_codes),
        "paired_net_pnl": paired_net,
        "top_improvements": sorted_by_diff[:5],
        "top_worsenings": sorted_by_diff[-5:][::-1],
        "metric_diffs": metric_diffs,
        "wilcoxon": wilcoxon_result,
    }


def _wilcoxon_test(paired: list[dict]) -> dict:
    """对配对 net_pnl 执行 Wilcoxon signed-rank 检验。

    Returns:
        dict with p_value, statistic, conclusion
    """
    if len(paired) < 6:
        return {
            "p_value": None,
            "statistic": None,
            "n_pairs": len(paired),
            "conclusion": "样本量不足（需 ≥6 个配对）",
            "significant": None,
        }

    diffs = [p["diff"] for p in paired]
    # 去除差值为 0 的配对（Wilcoxon 要求非零差）
    nonzero = [d for d in diffs if d != 0]
    if len(nonzero) < 6:
        return {
            "p_value": None,
            "statistic": None,
            "n_pairs": len(paired),
            "nonzero_pairs": len(nonzero),
            "conclusion": "非零差异样本量不足（需 ≥6）",
            "significant": None,
        }

    try:
        from scipy.stats import wilcoxon
        stat, p = wilcoxon(nonzero, alternative="two-sided")
        p = round(p, 4)
        if p < 0.05:
            conclusion = f"统计显著 (p={p}, 拒绝 H₀，两组存在差异)"
            significant = True
        else:
            conclusion = f"统计不显著 (p={p}, 无法拒绝 H₀)"
            significant = False
        return {
            "p_value": p,
            "statistic": round(stat, 2),
            "n_pairs": len(paired),
            "nonzero_pairs": len(nonzero),
            "conclusion": conclusion,
            "significant": significant,
        }
    except Exception as e:
        return {
            "p_value": None,
            "statistic": None,
            "n_pairs": len(paired),
            "conclusion": f"检验执行失败: {e}",
            "significant": None,
        }


def _aggregate_tag_results(runs: list[dict]) -> list[dict]:
    """聚合同一标签下所有运行的个股结果（按 code 取 mean）。"""
    from web.results_db import get_run_results
    by_code: dict[str, list[dict]] = {}
    for r in runs:
        results = get_run_results(r["id"])
        for res in results:
            code = res["code"]
            if code not in by_code:
                by_code[code] = []
            by_code[code].append(res)

    # 聚合：对每个 code 取均值
    aggregated = []
    for code, res_list in by_code.items():
        n = len(res_list)
        avg = {
            "code": code,
            "paired_trades": round(sum(x.get("paired_trades", 0) for x in res_list) / n),
            "win_rate": round(sum(x.get("win_rate", 0) for x in res_list) / n, 4),
            "net_pnl": round(sum(x.get("net_pnl", 0) for x in res_list) / n, 2),
            "profit_factor": round(sum(x.get("profit_factor", 0) for x in res_list) / n, 4),
            "payoff_ratio": round(sum(x.get("payoff_ratio", 0) for x in res_list) / n, 4),
            "avg_ce": round(sum(x.get("avg_ce", 0) for x in res_list) / n, 4),
        }
        aggregated.append(avg)
    return aggregated