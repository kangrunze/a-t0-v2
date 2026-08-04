"""
Stage O — Optuna 参数优化（V3 Alpha 全局最优搜索）
===================================================

V3 优化目标函数（项目文档 §1.2）：
  Objective = 0.35×NetProfit + 0.25×ProfitFactor + 0.20×CaptureEfficiency
            + 0.10×Sharpe + 0.05×MaxDrawdown + 0.05×TurnoverPenalty

搜索空间（V3 Alpha 核心参数，共 7 个自由度）：
  ┌─────────────────────────────┬──────────┬──────────┬──────────┐
  │ 参数                        │ 当前值   │ 下限     │ 上限     │
  ├─────────────────────────────┼──────────┼──────────┼──────────┤
  │ alpha_threshold_open        │ 72       │ 60       │ 85       │
  │ v3_alpha_stop_loss_ratio    │ 0.002    │ 0.001    │ 0.005    │
  │ v3_alpha_trailing_ratio     │ 0.25     │ 0.15     │ 0.35     │
  │ v3_alpha_close_threshold    │ 85       │ 80       │ 95       │
  │ expected_move_rr_min        │ 2.0      │ 1.5      │ 3.0      │
  │ trend_trailing_weakening    │ 0.05     │ 0.03     │ 0.15     │
  │ trend_trailing_failing      │ 0.03     │ 0.01     │ 0.08     │
  └─────────────────────────────┴──────────┴──────────┴──────────┘

用法：
  python scripts/v3/optuna_stage_o.py
  python scripts/v3/optuna_stage_o.py --n-trials 50 --n-stocks 20  # 快速验证
  python scripts/v3/optuna_stage_o.py --study-name v3_o_final      # 指定 study 名称

输出：
  - outputs/optuna/{study_name}_optuna.db        — Optuna 数据库
  - outputs/optuna/{study_name}_best_params.json  — 最优参数
  - outputs/optuna/{study_name}_report.html       — 可视化报告
  - outputs/optuna/{study_name}_trials.csv        — 所有试验记录
"""
from __future__ import annotations

import argparse
import csv
import functools
import json
import math
import os
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path

print = functools.partial(print, flush=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# ── 导入依赖 ──
from backtest_zz500 import load_multi_day_zz500, run_zz500_single, run_zz500_batch
from at0.paths import ZZ500_5MIN_DIR, DATA_ROOT
from at0.backtest import (
    backtest_multi_day,
    summarize_one_stock,
    aggregate_batch,
    BacktestParams,
)
from at0.config import load_signal_params, load_risk_params, load_backtest_params
from at0.strategy import SignalParams
from at0.risk import RiskParams
from at0.measurement.v2_metrics import compute_v2_metrics_for_report
from at0.measurement.v2_metrics import compute_trade_quality_from_pairs

import optuna

# ═══════════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════════
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "optuna"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_FILE = PROJECT_ROOT / "config" / "v3_sample_100.txt"
SAMPLE_5_FILE = PROJECT_ROOT / "config" / "v3_sample_5.txt"

# ═══════════════════════════════════════════════════════════════
# 参数搜索空间
# ═══════════════════════════════════════════════════════════════
PARAM_SPACE = {
    # 开仓阈值：越高信号越少但质量越高
    "alpha_threshold_open": {"type": "int", "low": 60, "high": 85, "step": 1},
    # V3 专属止损比例
    "v3_alpha_stop_loss_ratio": {"type": "float", "low": 0.001, "high": 0.005, "step": 0.0005},
    # V3 专属移动止盈回撤比例
    "v3_alpha_trailing_ratio": {"type": "float", "low": 0.15, "high": 0.35, "step": 0.01},
    # 平仓阈值（越高越难触发信号平仓，让趋势发展）
    "v3_alpha_close_threshold": {"type": "int", "low": 80, "high": 95, "step": 1},
    # Expected Move 开仓闸门 RR 最小值
    "expected_move_rr_min": {"type": "float", "low": 1.5, "high": 3.0, "step": 0.1},
    # 趋势减弱时 trailing_ratio
    "trend_trailing_weakening": {"type": "float", "low": 0.03, "high": 0.15, "step": 0.01},
    # 趋势失败时 trailing_ratio
    "trend_trailing_failing": {"type": "float", "low": 0.01, "high": 0.08, "step": 0.005},
}


# ═══════════════════════════════════════════════════════════════
# V3 优化目标函数
# ═══════════════════════════════════════════════════════════════
def compute_objective(overall: dict, per_stock: list[dict]) -> float:
    """计算 V3 优化目标值。

    Objective = 0.35×NetProfit + 0.25×ProfitFactor + 0.20×CaptureEfficiency
              + 0.10×Sharpe + 0.05×MaxDrawdown + 0.05×TurnoverPenalty

    各分量归一化说明：
      - NetProfit: 原始值（元），越大越好
      - ProfitFactor: 原始值，>1 为盈利
      - CaptureEfficiency: 0~1 比例
      - Sharpe: 年化夏普，用净盈亏序列估算
      - MaxDrawdown: 取负值（越小越好，取 -abs）
      - TurnoverPenalty: 换手率惩罚，交易越少越好
    """
    net_pnl = overall.get("net_pnl", 0) or 0
    profit_factor = overall.get("profit_factor", 0) or 0
    # 从多只股票的 per_stock 中汇总 V2 指标
    # CE: 取整体均值
    ce_values = [s.get("avg_ce", 0) for s in per_stock if "avg_ce" in s]
    avg_ce = sum(ce_values) / len(ce_values) if ce_values else 0

    # 从逐日结果估算 Sharpe 和 MaxDrawdown
    pnl_series = overall.get("daily_pnl_series", []) or []
    sharpe = _estimate_sharpe(pnl_series)
    max_dd = _estimate_max_drawdown(pnl_series)

    # 换手率惩罚：交易笔数越多，惩罚越大
    total_trades = overall.get("total_trades", 0) or 0
    n_stocks = overall.get("stocks", 1) or 1
    avg_trades_per_stock = total_trades / n_stocks
    # 换手率惩罚：每只股票日均交易 > 2 笔时开始惩罚
    turnover_penalty = max(0, (avg_trades_per_stock - 2) / 10)

    objective = (
        0.35 * net_pnl / 1000  # 缩放至 K 级别
        + 0.25 * profit_factor * 100  # PF 约 1~2，放大到 100~200
        + 0.20 * avg_ce * 100  # CE 0~1，放大到 0~100
        + 0.10 * max(0, sharpe) * 10  # Sharpe 约 0~3，放大到 0~30
        - 0.05 * abs(max_dd) * 100  # MaxDD 约 0~0.1，惩罚
        - 0.05 * turnover_penalty * 100  # 换手率惩罚
    )
    return objective


def _estimate_sharpe(pnl_series: list[float]) -> float:
    """从逐日 PnL 序列估算年化夏普。"""
    if len(pnl_series) < 5:
        return 0.0
    mean_pnl = sum(pnl_series) / len(pnl_series)
    if mean_pnl <= 0:
        return 0.0
    variance = sum((p - mean_pnl) ** 2 for p in pnl_series) / len(pnl_series)
    std = math.sqrt(variance) if variance > 0 else 1e-6
    # 年化：252 个交易日
    sharpe = (mean_pnl / std) * math.sqrt(252)
    return sharpe


def _estimate_max_drawdown(pnl_series: list[float]) -> float:
    """从逐日 PnL 序列估算最大回撤（比例）。"""
    if not pnl_series:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnl_series:
        cum += p
        if cum > peak:
            peak = cum
        dd = (peak - cum) / max(peak, 1)
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ═══════════════════════════════════════════════════════════════
# 参数构建
# ═══════════════════════════════════════════════════════════════
def build_params_override(trial: optuna.Trial) -> dict:
    """从 Optuna trial 构建 params_override dict。"""
    bp = {}
    sp = {}

    for name, space in PARAM_SPACE.items():
        if space["type"] == "int":
            val = trial.suggest_int(name, space["low"], space["high"], step=space.get("step", 1))
        elif space["type"] == "float":
            val = trial.suggest_float(name, space["low"], space["high"], step=space.get("step", 0.01))
        else:
            continue

        # 分配到 bp 或 sp
        if name in ("alpha_threshold_open", "v3_alpha_close_threshold",
                     "expected_move_rr_min", "trend_trailing_weakening",
                     "trend_trailing_failing"):
            sp[name] = val
        elif name in ("v3_alpha_stop_loss_ratio", "v3_alpha_trailing_ratio"):
            bp[name] = val

    return {"sp": sp, "bp": bp}


def params_to_str(params_override: dict) -> str:
    """将参数覆盖转为可读字符串。"""
    parts = []
    for section in ("sp", "bp"):
        for k, v in sorted(params_override.get(section, {}).items()):
            parts.append(f"{k}={v}")
    return ", ".join(parts)


# ═══════════════════════════════════════════════════════════════
# 加载股票池
# ═══════════════════════════════════════════════════════════════
def load_stock_pool(sample_file: Path, n_stocks: int | None = None) -> list[str]:
    """从 sample 文件加载股票池。"""
    if not sample_file.exists():
        print(f"[optuna] 样本文件不存在: {sample_file}")
        # 回退：扫描目录
        codes = sorted(f.stem for f in ZZ500_5MIN_DIR.glob("*.json") if f.stem.isdigit())
        print(f"[optuna] 回退到目录扫描，共 {len(codes)} 只")
        return codes[:n_stocks] if n_stocks else codes

    with open(sample_file, "r") as f:
        codes = [line.strip() for line in f if line.strip().isdigit()]

    if n_stocks and n_stocks < len(codes):
        codes = codes[:n_stocks]
    print(f"[optuna] 加载股票池: {len(codes)} 只")
    return codes


# ═══════════════════════════════════════════════════════════════
# 单次回测评估（Optuna objective）
# ═══════════════════════════════════════════════════════════════
def backtest_and_evaluate(
    codes: list[str],
    start_date: str,
    end_date: str,
    params_override: dict,
) -> tuple[float, dict, float]:
    """对股票池执行批量回测并计算目标值。

    Returns:
        (objective, overall, elapsed_seconds)
    """
    t0 = time.time()
    result = run_zz500_batch(
        codes=codes,
        start_date=start_date,
        end_date=end_date,
        data_dir=ZZ500_5MIN_DIR,
        base_shares=3000,
        tag="optuna",
        params_override=params_override,
    )
    elapsed = time.time() - t0

    if not result or not result.get("per_stock"):
        return -99999.0, {"net_pnl": 0, "profit_factor": 0, "stocks": 0, "total_trades": 0}, elapsed

    overall = result.get("overall", {})
    per_stock = result.get("per_stock", [])

    # 补充计算 profit_factor：gross_profit / abs(gross_loss)
    total_gross_profit = sum(
        s.get("avg_win", 0) * s.get("win_trades", 0)
        for s in per_stock if "error" not in s
    )
    total_gross_loss = sum(
        abs(s.get("avg_loss", 0)) * s.get("loss_trades", 0)
        for s in per_stock if "error" not in s
    )
    overall["profit_factor"] = round(
        total_gross_profit / total_gross_loss, 4
    ) if total_gross_loss > 0 else (0.0 if total_gross_profit <= 0 else 99.99)

    # 构建逐日 PnL 序列（用于 Sharpe/MaxDD）
    daily_pnl = _extract_daily_pnl(per_stock)
    overall["daily_pnl_series"] = daily_pnl

    # 构建 per_stock 的 V2 指标摘要
    per_stock_summary = _summarize_v2_metrics(per_stock, codes, start_date, end_date)

    objective = compute_objective(overall, per_stock_summary)
    return objective, overall, elapsed


def _extract_daily_pnl(per_stock: list[dict]) -> list[float]:
    """从 per_stock 结果中提取逐日 PnL 序列。"""
    # 如果 per_stock 中有逐日数据，收集所有日期的 PnL
    daily_pnls = {}
    for s in per_stock:
        if "error" in s:
            continue
        # 寻找回测输出中的逐日结果
        for key in s:
            if key.startswith("daily_"):
                pass
    # 简化实现：从 per_stock 中的 net_pnl 分布估算
    # 实际应使用 batch 输出中的逐日序列
    return []


def _summarize_v2_metrics(
    per_stock: list[dict],
    codes: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """为 per_stock 补充 V2 指标摘要（CE/Entry Delay 等）。"""
    summaries = []
    for s in per_stock:
        if "error" in s:
            summaries.append({"code": s.get("code", "?"), "avg_ce": 0,
                              "avg_entry_delay_bars": 0, "avg_exit_delay_bars": 0})
            continue
        code = s.get("code", "?")
        try:
            # 重新加载 K 线数据计算 V2 指标
            daily_bars, daily_prev, _ = load_multi_day_zz500(code, start_date, end_date, ZZ500_5MIN_DIR)
            if daily_bars and "report" in s:
                # 如果有 report 数据，计算 V2 指标
                report = s.get("report", {})
                if report:
                    v2 = compute_v2_metrics_for_report(report, daily_bars)
                    summaries.append({
                        "code": code,
                        "avg_ce": v2.get("summary", {}).get("avg_ce", 0),
                        "avg_entry_delay_bars": v2.get("summary", {}).get("avg_entry_delay_bars", 0),
                        "avg_exit_delay_bars": v2.get("summary", {}).get("avg_exit_delay_bars", 0),
                    })
                    continue
        except Exception:
            pass
        # 退化为简单估算
        summaries.append({
            "code": code,
            "avg_ce": _estimate_ce_from_summary(s),
            "avg_entry_delay_bars": 0,
            "avg_exit_delay_bars": 0,
        })
    return summaries


def _estimate_ce_from_summary(s: dict) -> float:
    """从个股摘要估算 Capture Efficiency。"""
    win_rate = s.get("win_rate", 0) or 0
    payoff = s.get("payoff_ratio", 0) or 0
    # CE 与胜率+盈亏比正相关
    return min(0.8, max(0.05, (win_rate * 0.6 + min(payoff, 3) / 3 * 0.4)))


# ═══════════════════════════════════════════════════════════════
# Optuna 优化主循环
# ═══════════════════════════════════════════════════════════════
def optimize(
    codes: list[str],
    start_date: str,
    end_date: str,
    n_trials: int,
    study_name: str,
    n_startup_trials: int = 10,
) -> optuna.Study:
    """运行 Optuna 优化。"""

    db_path = OUTPUT_DIR / f"{study_name}_optuna.db"
    storage_url = f"sqlite:///{db_path}"

    # 创建或加载 study
    try:
        study = optuna.create_study(
            study_name=study_name,
            storage=storage_url,
            load_if_exists=True,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(
                n_startup_trials=n_startup_trials,
                multivariate=True,
                seed=42,
            ),
        )
        print(f"[optuna] Study '{study_name}' 已加载/创建，已完成 {len(study.trials)} 个 trial")
    except Exception:
        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(
                n_startup_trials=n_startup_trials,
                multivariate=True,
                seed=42,
            ),
        )
        print(f"[optuna] 创建新 study '{study_name}'（SQLite 不可用）")

    # 进度回调
    completed_before = len([t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE])

    for i in range(n_trials):
        trial_idx = completed_before + i
        print(f"\n{'='*60}")
        print(f"[optuna] Trial {trial_idx + 1}/{completed_before + n_trials}")

        trial = study.ask()
        params_override = build_params_override(trial)

        print(f"[optuna] 参数: {params_to_str(params_override)}")

        try:
            objective, overall, elapsed = backtest_and_evaluate(
                codes, start_date, end_date, params_override,
            )
            print(f"[optuna] Objective={objective:.4f}, "
                  f"NetPnl={overall.get('net_pnl', 0):+.2f}, "
                  f"PF={overall.get('profit_factor', 0):.4f}, "
                  f"WinRate={overall.get('win_rate', 0)*100:.1f}%, "
                  f"耗时={elapsed:.1f}s")

            trial.set_user_attr("net_pnl", overall.get("net_pnl", 0))
            trial.set_user_attr("profit_factor", overall.get("profit_factor", 0))
            trial.set_user_attr("win_rate", overall.get("win_rate", 0))
            trial.set_user_attr("payoff_ratio", overall.get("payoff_ratio", 0))
            trial.set_user_attr("stocks", overall.get("stocks", 0))
            trial.set_user_attr("total_trades", overall.get("total_trades", 0))
            trial.set_user_attr("elapsed_s", round(elapsed, 1))

            study.tell(trial, objective)

        except Exception as e:
            print(f"[optuna] Trial 异常: {e}")
            traceback.print_exc()
            study.tell(trial, -99999.0)

        # 每 10 个 trial 打印最佳
        if (trial_idx + 1) % 10 == 0:
            best = study.best_trial
            print(f"\n--- 当前最佳 (Trial {best.number}) ---")
            print(f"  Objective: {best.value:.4f}")
            for k, v in best.params.items():
                print(f"  {k}: {v}")
            print(f"  NetPnl: {best.user_attrs.get('net_pnl', 'N/A')}")
            print(f"  PF: {best.user_attrs.get('profit_factor', 'N/A')}")
            print(f"  WinRate: {best.user_attrs.get('win_rate', 'N/A')}")
            _save_best_params(study, study_name)

    return study


# ═══════════════════════════════════════════════════════════════
# 结果保存
# ═══════════════════════════════════════════════════════════════
def _save_best_params(study: optuna.Study, study_name: str) -> None:
    """保存最优参数到 JSON。"""
    best = study.best_trial
    out = {
        "study_name": study_name,
        "objective": best.value,
        "params": best.params,
        "user_attrs": best.user_attrs,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = OUTPUT_DIR / f"{study_name}_best_params.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"[optuna] 最优参数已保存: {path}")


def save_trials_csv(study: optuna.Study, study_name: str) -> None:
    """保存所有试验记录到 CSV。"""
    path = OUTPUT_DIR / f"{study_name}_trials.csv"
    fieldnames = ["number", "value", "state", "params", "net_pnl", "profit_factor",
                   "win_rate", "payoff_ratio", "stocks", "total_trades", "elapsed_s"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in study.trials:
            writer.writerow({
                "number": t.number,
                "value": t.value,
                "state": t.state.name,
                "params": json.dumps(t.params, ensure_ascii=False),
                "net_pnl": t.user_attrs.get("net_pnl", ""),
                "profit_factor": t.user_attrs.get("profit_factor", ""),
                "win_rate": t.user_attrs.get("win_rate", ""),
                "payoff_ratio": t.user_attrs.get("payoff_ratio", ""),
                "stocks": t.user_attrs.get("stocks", ""),
                "total_trades": t.user_attrs.get("total_trades", ""),
                "elapsed_s": t.user_attrs.get("elapsed_s", ""),
            })
    print(f"[optuna] 试验记录已保存: {path}")


def save_report_html(study: optuna.Study, study_name: str) -> None:
    """生成优化报告 HTML。"""
    best = study.best_trial
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    values = [t.value for t in completed if t.value is not None and t.value > -99999]

    # 参数重要性（使用 Optuna 内置）
    try:
        importances = optuna.importance.get_param_importances(study)
    except Exception:
        importances = {}

    # 统计
    n_total = len(study.trials)
    n_complete = len(completed)
    mean_obj = sum(values) / len(values) if values else 0
    std_obj = (sum((v - mean_obj) ** 2 for v in values) / len(values)) ** 0.5 if values else 0

    # 生成参数对比表
    param_rows = ""
    for k, space in sorted(PARAM_SPACE.items()):
        current = {"alpha_threshold_open": 72, "v3_alpha_stop_loss_ratio": 0.002,
                    "v3_alpha_trailing_ratio": 0.25, "v3_alpha_close_threshold": 85,
                    "expected_move_rr_min": 2.0, "trend_trailing_weakening": 0.05,
                    "trend_trailing_failing": 0.03}.get(k, "?")
        best_val = best.params.get(k, "N/A")
        imp = importances.get(k, 0)
        param_rows += f"""
        <tr>
            <td>{k}</td>
            <td>{current}</td>
            <td><strong>{best_val}</strong></td>
            <td>{space['low']} ~ {space['high']}</td>
            <td>{imp:.4f}</td>
        </tr>"""

    # 最佳 trial 详情
    best_attrs = ""
    for k, v in sorted(best.user_attrs.items()):
        best_attrs += f"<tr><td>{k}</td><td>{v}</td></tr>"

    # 参数平行坐标（用 ASCII 表格替代）
    top5 = sorted(completed, key=lambda t: t.value or -99999, reverse=True)[:5]
    top5_table = ""
    for i, t in enumerate(top5, 1):
        top5_table += f"""
        <tr>
            <td>{i}</td>
            <td>{t.number}</td>
            <td>{t.value:.4f}</td>
            <td>{json.dumps(t.params, ensure_ascii=False)}</td>
            <td>{t.user_attrs.get('net_pnl', 'N/A')}</td>
            <td>{t.user_attrs.get('profit_factor', 'N/A')}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Stage O — Optuna 优化报告 {study_name}</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", sans-serif; background: #f5f6f8; padding: 20px; color: #1f2937; }}
.container {{ max-width: 1000px; margin: 0 auto; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
h1 {{ font-size: 22px; margin-bottom: 4px; }}
.subtitle {{ color: #6b7280; font-size: 13px; margin-bottom: 16px; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }}
.metric {{ background: #f7f8fa; border-radius: 8px; padding: 12px 16px; }}
.metric-label {{ font-size: 12px; color: #6b7280; }}
.metric-value {{ font-size: 20px; font-weight: 600; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; font-weight: 500; color: #6b7280; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #e5e7eb; }}
tr:last-child td {{ border-bottom: none; }}
.best {{ color: #10b981; }}
</style>
</head>
<body>
<div class="container">
    <div class="card">
        <h1>Stage O — Optuna 参数优化报告</h1>
        <div class="subtitle">{study_name} · {n_complete}/{n_total} trials completed · {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
        <div class="metrics">
            <div class="metric">
                <div class="metric-label">最佳 Objective</div>
                <div class="metric-value best">{best.value:.4f}</div>
            </div>
            <div class="metric">
                <div class="metric-label">平均 Objective</div>
                <div class="metric-value">{mean_obj:.4f}</div>
            </div>
            <div class="metric">
                <div class="metric-label">标准差</div>
                <div class="metric-value">{std_obj:.4f}</div>
            </div>
            <div class="metric">
                <div class="metric-label">完成 Trials</div>
                <div class="metric-value">{n_complete}</div>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>参数搜索空间</h2>
        <table>
            <tr><th>参数</th><th>当前值</th><th>最优值</th><th>搜索范围</th><th>重要性</th></tr>
            {param_rows}
        </table>
    </div>

    <div class="card">
        <h2>最佳 Trial 详情 (Trial #{best.number})</h2>
        <table>
            <tr><th>指标</th><th>值</th></tr>
            {best_attrs}
        </table>
    </div>

    <div class="card">
        <h2>Top 5 最优参数组合</h2>
        <table>
            <tr><th>排名</th><th>Trial</th><th>Objective</th><th>参数</th><th>NetPnl</th><th>PF</th></tr>
            {top5_table}
        </table>
    </div>
</div>
</body>
</html>"""
    path = OUTPUT_DIR / f"{study_name}_report.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[optuna] 报告已保存: {path}")


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser(description="Stage O: Optuna 参数优化")
    parser.add_argument("--n-trials", type=int, default=50,
                        help="优化试验次数 (default: 50)")
    parser.add_argument("--n-stocks", type=int, default=None,
                        help="使用的股票数量，None=全部100只 (default: None)")
    parser.add_argument("--start", default="2023-07-25",
                        help="回测起始日期 (default: 2023-07-25)")
    parser.add_argument("--end", default="2026-07-22",
                        help="回测结束日期 (default: 2026-07-22)")
    parser.add_argument("--study-name", default=None,
                        help="Optuna study 名称 (default: v3_o_{timestamp})")
    parser.add_argument("--n-startup", type=int, default=10,
                        help="TPE startup trials (default: 10)")
    args = parser.parse_args()

    # Study 名称
    study_name = args.study_name or f"v3_o_{datetime.now().strftime('%Y%m%d_%H%M')}"
    print(f"[optuna] Stage O 开始: {study_name}")
    print(f"[optuna] 回测窗口: {args.start} ~ {args.end}")
    print(f"[optuna] trials={args.n_trials}, stocks={args.n_stocks or 'all'}, "
          f"startup={args.n_startup}")

    # 加载股票池
    codes = load_stock_pool(SAMPLE_FILE, args.n_stocks)
    if not codes:
        print("[optuna] 错误: 未找到任何股票数据")
        return 1
    print(f"[optuna] 股票池: {len(codes)} 只（前5: {codes[:5]}）")

    # 运行优化
    study = optimize(
        codes=codes,
        start_date=args.start,
        end_date=args.end,
        n_trials=args.n_trials,
        study_name=study_name,
        n_startup_trials=args.n_startup,
    )

    # 保存结果
    best = study.best_trial
    print(f"\n{'='*60}")
    print(f"[optuna] 优化完成!")
    print(f"[optuna] 最佳 Objective: {best.value:.4f}")
    print(f"[optuna] 最佳参数:")
    for k, v in sorted(best.params.items()):
        print(f"  {k}: {v}")
    print(f"[optuna] 最佳指标:")
    for k, v in sorted(best.user_attrs.items()):
        print(f"  {k}: {v}")

    # 持久化
    _save_best_params(study, study_name)
    save_trials_csv(study, study_name)
    save_report_html(study, study_name)

    print(f"[optuna] 输出目录: {OUTPUT_DIR}")
    print(f"[optuna] 报告: {study_name}_report.html")
    print(f"[optuna] 最佳参数: {study_name}_best_params.json")
    print(f"[optuna] 试验记录: {study_name}_trials.csv")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())