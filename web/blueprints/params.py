"""
QuantWeb — 参数与预设
======================
合并原"参数配置"+ Optuna 页。
七维度权重 / YAML 编辑 / Optuna 优化 / 预设管理。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from flask import Blueprint, render_template, request, jsonify, current_app

bp = Blueprint("params", __name__, url_prefix="")


# ── YAML 文件路径 ──
def _get_config_path() -> Path:
    """获取 thresholds.yaml 的绝对路径。"""
    root = current_app.config.get("PROJECT_ROOT", Path.cwd())
    return Path(root) / "config" / "thresholds.yaml"


# ═══════════════════════════════════════════════════════════════
# 页面
# ═══════════════════════════════════════════════════════════════
@bp.route("/params/")
def index():
    """参数与预设页。"""
    return render_template("params.html")


# ═══════════════════════════════════════════════════════════════
# API: 七维度权重
# ═══════════════════════════════════════════════════════════════
DEFAULT_WEIGHTS = {
    "trend": 0.25,
    "wave": 0.20,
    "support": 0.15,
    "momentum": 0.15,
    "liquidity": 0.10,
    "expected_move": 0.10,
    "risk": 0.05,
}


@bp.route("/api/params/weights")
def get_weights():
    """获取当前七维度权重。"""
    yaml_path = _get_config_path()
    weights = dict(DEFAULT_WEIGHTS)
    total = sum(DEFAULT_WEIGHTS.values())
    try:
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "signal" in data:
                w = data["signal"].get("v3_alpha_weights")
                if isinstance(w, dict) and w:
                    weights.update(w)
                    total = sum(weights.values())
    except Exception:
        pass
    return jsonify({
        "weights": weights,
        "total": round(total, 4),
        "dimensions": list(weights.keys()),
    })


@bp.route("/api/params/weights", methods=["POST"])
def save_weights():
    """保存七维度权重到 thresholds.yaml。"""
    data = request.get_json(force=True) or {}
    new_weights = data.get("weights")
    if not isinstance(new_weights, dict) or not new_weights:
        return jsonify({"error": "无效的权重数据"}), 400

    total = sum(new_weights.values())
    if abs(total - 1.0) > 0.01:
        return jsonify({"error": f"权重总和需为 1.0，当前 {total:.4f}"}), 400

    yaml_path = _get_config_path()
    try:
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                full_data = yaml.safe_load(f) or {}
        else:
            full_data = {}
    except Exception as e:
        return jsonify({"error": f"读取 YAML 失败: {e}"}), 500

    # 确保 signal 段存在，写入 v3_alpha_weights
    if "signal" not in full_data:
        full_data["signal"] = {}
    full_data["signal"]["v3_alpha_weights"] = {k: float(v) for k, v in new_weights.items()}

    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(full_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({"ok": True, "weights": new_weights, "total": round(total, 4)})
    except Exception as e:
        return jsonify({"error": f"写入 YAML 失败: {e}"}), 500


# ═══════════════════════════════════════════════════════════════
# API: 原始 YAML
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/params/yaml")
def get_yaml():
    """获取完整 YAML 内容。"""
    yaml_path = _get_config_path()
    try:
        if yaml_path.exists():
            content = yaml_path.read_text(encoding="utf-8")
        else:
            content = ""
        return jsonify({"content": content, "path": str(yaml_path)})
    except Exception as e:
        return jsonify({"error": f"读取 YAML 失败: {e}"}), 500


@bp.route("/api/params/yaml", methods=["POST"])
def save_yaml():
    """保存完整 YAML 内容。"""
    data = request.get_json(force=True) or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "内容不能为空"}), 400

    # 验证 YAML 可解析
    try:
        yaml.safe_load(content)
    except Exception as e:
        return jsonify({"error": f"YAML 格式错误: {e}"}), 400

    yaml_path = _get_config_path()
    try:
        yaml_path.write_text(content, encoding="utf-8")
        return jsonify({"ok": True, "path": str(yaml_path)})
    except Exception as e:
        return jsonify({"error": f"写入 YAML 失败: {e}"}), 500


# ═══════════════════════════════════════════════════════════════
# API: 预设管理
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/params/presets")
def get_presets():
    """获取所有参数预设。"""
    from web.results_db import get_presets as _get_presets
    return jsonify({"presets": _get_presets()})


@bp.route("/api/params/presets", methods=["POST"])
def save_preset():
    """创建或更新参数预设。"""
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "预设名称不能为空"}), 400
    description = (data.get("description") or "").strip()
    params_override = data.get("params_override", {})

    from web.results_db import save_preset as _save_preset
    preset_id = _save_preset(name, params_override, description)
    return jsonify({"ok": True, "preset_id": preset_id})


@bp.route("/api/params/presets/<int:preset_id>", methods=["DELETE"])
def delete_preset(preset_id: int):
    """删除参数预设。"""
    from web.results_db import delete_preset as _delete_preset
    try:
        _delete_preset(preset_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": f"删除失败: {e}"}), 500


# ═══════════════════════════════════════════════════════════════
# API: 用当前配置发起回测
# ═══════════════════════════════════════════════════════════════
@bp.route("/api/params/backtest", methods=["POST"])
def backtest_from_params():
    """用当前配置发起回测（代理到 workbench 的 backtest_run）。"""
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip() or "参数页回测"
    stock_pool = (data.get("stock_pool") or "").strip() or "v3_sample_5.txt"
    start_date = (data.get("start_date") or "").strip() or "2023-07-25"
    end_date = (data.get("end_date") or "").strip() or "2026-07-22"
    base_shares = data.get("base_shares", 3000)
    tag = (data.get("tag") or "").strip() or "params_page"

    from web.blueprints.workbench import _resolve_stock_pool
    codes = _resolve_stock_pool(stock_pool)
    if not codes:
        return jsonify({"error": f"股票池 '{stock_pool}' 为空或不存在"}), 400

    from web.results_db import save_run
    run_id = save_run(
        name=name,
        stock_pool=stock_pool,
        start_date=start_date,
        end_date=end_date,
        params_override={},
        tag=tag,
    )

    from web.runner import submit_backtest
    submit_backtest(
        run_id=run_id,
        codes=codes,
        start_date=start_date,
        end_date=end_date,
        base_shares=base_shares,
        params_override={},
    )

    return jsonify({"run_id": run_id, "status": "pending"})


# ═══════════════════════════════════════════════════════════════
# API: Optuna
# ═══════════════════════════════════════════════════════════════

# Optuna 参数搜索空间（与 scripts/v3/optuna_stage_o.py 对齐）
OPTUNA_PARAM_SPACE = {
    "alpha_threshold_open": {"type": "int", "low": 60, "high": 85, "step": 1, "current": 72, "label": "开仓阈值"},
    "v3_alpha_stop_loss_ratio": {"type": "float", "low": 0.001, "high": 0.005, "step": 0.0005, "current": 0.002, "label": "止损比例"},
    "v3_alpha_trailing_ratio": {"type": "float", "low": 0.15, "high": 0.35, "step": 0.01, "current": 0.25, "label": "移动止盈回撤"},
    "v3_alpha_close_threshold": {"type": "int", "low": 80, "high": 95, "step": 1, "current": 85, "label": "平仓阈值"},
    "expected_move_rr_min": {"type": "float", "low": 1.5, "high": 3.0, "step": 0.1, "current": 2.0, "label": "Expected Move RR"},
    "trend_trailing_weakening": {"type": "float", "low": 0.03, "high": 0.15, "step": 0.01, "current": 0.05, "label": "趋势减弱 trailing"},
    "trend_trailing_failing": {"type": "float", "low": 0.01, "high": 0.08, "step": 0.005, "current": 0.03, "label": "趋势失败 trailing"},
}

# V3 优化目标函数权重
OPTUNA_OBJECTIVE_WEIGHTS = {
    "net_profit": 0.35,
    "profit_factor": 0.25,
    "capture_efficiency": 0.20,
    "sharpe": 0.10,
    "max_drawdown": 0.05,
    "turnover_penalty": 0.05,
}


@bp.route("/api/params/optuna/info")
def optuna_info():
    """返回 Optuna 参数空间和目标函数权重。"""
    return jsonify({
        "param_space": OPTUNA_PARAM_SPACE,
        "objective_weights": OPTUNA_OBJECTIVE_WEIGHTS,
    })


@bp.route("/api/params/optuna/studies")
def optuna_studies():
    """返回所有 Optuna study 列表。"""
    from web.results_db import get_optuna_studies
    return jsonify({"studies": get_optuna_studies()})


@bp.route("/api/params/optuna/trials")
def optuna_trials():
    """返回某 study 的 trial 列表。"""
    study_name = request.args.get("study", "").strip()
    if not study_name:
        return jsonify({"error": "需要指定 study 名称"}), 400
    from web.results_db import get_optuna_trials
    trials = get_optuna_trials(study_name)
    return jsonify({"study_name": study_name, "trials": trials, "count": len(trials)})


@bp.route("/api/params/optuna/apply", methods=["POST"])
def optuna_apply():
    """将 Optuna 最优参数应用到 thresholds.yaml。"""
    data = request.get_json(force=True) or {}
    study_name = (data.get("study_name") or "").strip()
    if not study_name:
        return jsonify({"error": "需要指定 study 名称"}), 400

    from web.results_db import get_optuna_best
    best = get_optuna_best(study_name)
    if not best:
        return jsonify({"error": f"study '{study_name}' 中无 trial 记录"}), 404

    params = best.get("params", {})
    if not isinstance(params, dict) or not params:
        return jsonify({"error": "最优 trial 参数为空"}), 400

    # 写入 thresholds.yaml
    yaml_path = _get_config_path()
    try:
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                full_data = yaml.safe_load(f) or {}
        else:
            full_data = {}
    except Exception as e:
        return jsonify({"error": f"读取 YAML 失败: {e}"}), 500

    # 确保 signal 段存在
    if "signal" not in full_data:
        full_data["signal"] = {}

    # 将 Optuna 参数写入 signal 段的 sp/bp 覆盖
    sp = full_data["signal"].setdefault("sp", {})
    bp = full_data["signal"].setdefault("bp", {})

    param_map = {
        "alpha_threshold_open": ("sp", "alpha_threshold_open"),
        "v3_alpha_close_threshold": ("sp", "v3_alpha_close_threshold"),
        "expected_move_rr_min": ("sp", "expected_move_rr_min"),
        "trend_trailing_weakening": ("sp", "trend_trailing_weakening"),
        "trend_trailing_failing": ("sp", "trend_trailing_failing"),
        "v3_alpha_stop_loss_ratio": ("bp", "v3_alpha_stop_loss_ratio"),
        "v3_alpha_trailing_ratio": ("bp", "v3_alpha_trailing_ratio"),
    }

    for param_key, (section, yaml_key) in param_map.items():
        if param_key in params:
            if section == "sp":
                sp[yaml_key] = params[param_key]
            else:
                bp[yaml_key] = params[param_key]

    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(full_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({
            "ok": True,
            "study_name": study_name,
            "objective": best.get("objective"),
            "params": params,
            "path": str(yaml_path),
        })
    except Exception as e:
        return jsonify({"error": f"写入 YAML 失败: {e}"}), 500


@bp.route("/api/params/optuna/start", methods=["POST"])
def optuna_start():
    """异步启动 Optuna 优化（创建 runs 记录用于进度追踪）。"""
    data = request.get_json(force=True) or {}
    study_name = (data.get("study_name") or "").strip()
    if not study_name:
        study_name = f"web_optuna_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}"
    n_trials = int(data.get("n_trials", 10))
    stock_pool = (data.get("stock_pool") or "").strip() or "v3_sample_5.txt"
    start_date = (data.get("start_date") or "").strip() or "2023-07-25"
    end_date = (data.get("end_date") or "").strip() or "2026-07-22"
    base_shares = int(data.get("base_shares", 3000))

    from web.blueprints.workbench import _resolve_stock_pool
    codes = _resolve_stock_pool(stock_pool)
    if not codes:
        return jsonify({"error": f"股票池 '{stock_pool}' 为空或不存在"}), 400

    # 创建 runs 记录用于进度追踪
    from web.results_db import save_run
    run_id = save_run(
        name=f"Optuna: {study_name}",
        stock_pool=stock_pool,
        start_date=start_date,
        end_date=end_date,
        params_override={"study_name": study_name, "n_trials": n_trials},
        tag="optuna",
    )

    from web.optuna_runner import start_optuna
    start_optuna(
        run_id=run_id,
        study_name=study_name,
        codes=codes,
        start_date=start_date,
        end_date=end_date,
        n_trials=n_trials,
        base_shares=base_shares,
    )

    return jsonify({
        "ok": True,
        "run_id": run_id,
        "study_name": study_name,
        "n_trials": n_trials,
        "codes": len(codes),
    })