"""
QuantWeb — Optuna 后台优化执行器
=================================
从 Web UI 发起超参数优化，在后台线程中逐 trial 执行回测并评估。
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

# Optuna 参数搜索空间（与 scripts/v3/optuna_stage_o.py 对齐）
PARAM_SPACE = {
    "alpha_threshold_open": {"type": "int", "low": 60, "high": 85, "step": 1},
    "v3_alpha_stop_loss_ratio": {"type": "float", "low": 0.001, "high": 0.005, "step": 0.0005},
    "v3_alpha_trailing_ratio": {"type": "float", "low": 0.15, "high": 0.35, "step": 0.01},
    "v3_alpha_close_threshold": {"type": "int", "low": 80, "high": 95, "step": 1},
    "expected_move_rr_min": {"type": "float", "low": 1.5, "high": 3.0, "step": 0.1},
    "trend_trailing_weakening": {"type": "float", "low": 0.03, "high": 0.15, "step": 0.01},
    "trend_trailing_failing": {"type": "float", "low": 0.01, "high": 0.08, "step": 0.005},
}

# 优化目标函数权重
OBJECTIVE_WEIGHTS = {
    "net_profit": 0.35,
    "profit_factor": 0.25,
    "capture_efficiency": 0.20,
    "sharpe": 0.10,
    "max_drawdown": 0.05,
    "turnover_penalty": 0.05,
}

_executor = ThreadPoolExecutor(max_workers=1)
_active_optuna: dict[int, object] = {}  # run_id -> Future


def _sample_params() -> dict:
    """从搜索空间随机采样一组参数。"""
    params = {}
    for name, space in PARAM_SPACE.items():
        if space["type"] == "int":
            low = space["low"]
            high = space["high"]
            step = space.get("step", 1)
            n_steps = (high - low) // step
            params[name] = low + random.randint(0, n_steps) * step
        elif space["type"] == "float":
            low = space["low"]
            high = space["high"]
            step = space.get("step", 0.001)
            n_steps = int((high - low) / step)
            params[name] = round(low + random.randint(0, n_steps) * step, 6)
    return params


def _build_params_override(params: dict) -> dict:
    """将采样参数拆分为 sp/bp override。"""
    sp = {}
    bp = {}
    for name, val in params.items():
        if name in ("alpha_threshold_open", "v3_alpha_close_threshold",
                     "expected_move_rr_min", "trend_trailing_weakening",
                     "trend_trailing_failing"):
            sp[name] = val
        elif name in ("v3_alpha_stop_loss_ratio", "v3_alpha_trailing_ratio"):
            bp[name] = val
    return {"sp": sp, "bp": bp}


def compute_objective(net_pnl: float, profit_factor: float, avg_ce: float,
                       sharpe: float, max_dd: float, total_trades: int,
                       n_stocks: int) -> float:
    """计算 V3 优化目标值（与 CLI 脚本对齐）。"""
    avg_trades_per_stock = total_trades / max(n_stocks, 1)
    turnover_penalty = max(0, (avg_trades_per_stock - 2) / 10)

    objective = (
        0.35 * net_pnl / 1000
        + 0.25 * profit_factor * 100
        + 0.20 * avg_ce * 100
        + 0.10 * max(0, sharpe) * 10
        - 0.05 * abs(max_dd) * 100
        - 0.05 * turnover_penalty * 100
    )
    return objective


def _run_single_trial(
    codes: list[str],
    start_date: str,
    end_date: str,
    params_override: dict,
    base_shares: int = 3000,
) -> dict:
    """对股票池执行单次批量回测，返回评估结果。"""
    try:
        from scripts.backtest_zz500 import run_zz500_batch, load_multi_day_zz500
        from at0.paths import ZZ500_5MIN_DIR
        from at0.measurement.v2_metrics import compute_v2_metrics_for_report

        result = run_zz500_batch(
            codes=codes,
            start_date=start_date,
            end_date=end_date,
            data_dir=ZZ500_5MIN_DIR,
            base_shares=base_shares,
            tag="optuna_web",
            params_override=params_override,
        )

        if not result or not result.get("per_stock"):
            return {"net_pnl": 0, "profit_factor": 0, "avg_ce": 0,
                    "sharpe": 0, "max_dd": 0, "total_trades": 0, "stocks": 0, "error": "empty result"}

        overall = result.get("overall", {})
        per_stock = result.get("per_stock", [])

        # 计算 profit_factor
        total_gross_profit = sum(
            s.get("avg_win", 0) * s.get("win_trades", 0)
            for s in per_stock if "error" not in s
        )
        total_gross_loss = sum(
            abs(s.get("avg_loss", 0)) * s.get("loss_trades", 0)
            for s in per_stock if "error" not in s
        )
        profit_factor = round(
            total_gross_profit / total_gross_loss, 4
        ) if total_gross_loss > 0 else (0.0 if total_gross_profit <= 0 else 99.99)

        # 计算 CE 均值
        ce_values = [s.get("avg_ce", 0) for s in per_stock if "avg_ce" in s]
        avg_ce = sum(ce_values) / len(ce_values) if ce_values else 0

        # 估算 Sharpe / MaxDD（简化：从 per_stock net_pnl 分布估算）
        net_pnls = [s.get("net_pnl", 0) for s in per_stock if "error" not in s]
        sharpe = 0.0
        max_dd = 0.0
        if len(net_pnls) > 3:
            mean_pnl = sum(net_pnls) / len(net_pnls)
            if mean_pnl > 0:
                variance = sum((p - mean_pnl) ** 2 for p in net_pnls) / len(net_pnls)
                std = math.sqrt(variance) if variance > 0 else 1e-6
                sharpe = (mean_pnl / std) * math.sqrt(252 / len(net_pnls))

        net_pnl = overall.get("net_pnl", 0) or 0
        total_trades = overall.get("total_trades", 0) or 0
        n_stocks = len([s for s in per_stock if "error" not in s])

        return {
            "net_pnl": net_pnl,
            "profit_factor": profit_factor,
            "avg_ce": avg_ce,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "total_trades": total_trades,
            "stocks": n_stocks,
        }
    except Exception as e:
        return {"net_pnl": 0, "profit_factor": 0, "avg_ce": 0,
                "sharpe": 0, "max_dd": 0, "total_trades": 0, "stocks": 0, "error": str(e)}


def _execute_optuna(
    run_id: int,
    study_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    n_trials: int,
    base_shares: int,
    on_progress: Callable | None = None,
) -> None:
    """执行 Optuna 优化，逐 trial 回测并保存结果。"""
    from web.results_db import save_optuna_trial, update_run_status

    update_run_status(run_id, "running", progress=f"0/{n_trials}")

    for trial_num in range(1, n_trials + 1):
        try:
            # 采样参数
            params = _sample_params()
            params_override = _build_params_override(params)

            # 执行回测
            eval_result = _run_single_trial(
                codes, start_date, end_date, params_override, base_shares,
            )

            if eval_result.get("error"):
                objective = -99999.0
            else:
                objective = compute_objective(
                    net_pnl=eval_result["net_pnl"],
                    profit_factor=eval_result["profit_factor"],
                    avg_ce=eval_result["avg_ce"],
                    sharpe=eval_result["sharpe"],
                    max_dd=eval_result["max_dd"],
                    total_trades=eval_result["total_trades"],
                    n_stocks=eval_result["stocks"],
                )

            # 保存到 DB
            save_optuna_trial(
                study_name=study_name,
                trial_number=trial_num,
                objective=round(objective, 4),
                params=params,
                net_pnl=round(eval_result["net_pnl"], 2),
                profit_factor=round(eval_result["profit_factor"], 4),
                win_rate=round(eval_result.get("avg_ce", 0), 4),  # 暂用 CE 占位
            )

            progress = f"{trial_num}/{n_trials}"
            update_run_status(run_id, "running", progress=progress)
            if on_progress:
                on_progress(done=trial_num, total=n_trials, status="running",
                            objective=round(objective, 2))

        except Exception as ex:
            tb = traceback.format_exc()
            print(f"[optuna_web] Trial {trial_num} 失败: {ex}\n{tb}", file=sys.stderr)

    update_run_status(run_id, "completed", progress=f"{n_trials}/{n_trials}")
    if on_progress:
        on_progress(done=n_trials, total=n_trials, status="completed")


def start_optuna(
    run_id: int,
    study_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    n_trials: int = 20,
    base_shares: int = 3000,
    on_progress: Callable | None = None,
) -> None:
    """在后台线程中启动 Optuna 优化。

    Args:
        run_id: runs 表中的主键（用于进度追踪）
        study_name: 优化 study 名称
        codes: 股票代码列表
        start_date: 起始日期
        end_date: 结束日期
        n_trials: trial 数量
        base_shares: 底仓股数
        on_progress: 进度回调
    """
    _executor.submit(
        _execute_optuna,
        run_id, study_name, codes, start_date, end_date,
        n_trials, base_shares, on_progress,
    )


def cancel_optuna() -> None:
    """取消正在运行的优化（尚无实现）。"""
    pass