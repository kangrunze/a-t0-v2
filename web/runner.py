"""
QuantWeb — 后台回测执行引擎
============================
ThreadPoolExecutor + 数据库任务表，不引入 Celery/Redis。

执行流程：
  1. /api/backtest/run 写入跑表并提交到线程池
  2. 后台线程逐只股票调用 run_zz500_single()
  3. 每完成一只股票 UPDATE runs SET progress + 写入 results 表
  4. 全部完成后 UPDATE runs SET status='completed'
  5. 前端通过 SSE 消费 runs.progress 的变化

约束：
  - 单机单进程，max_workers=1（回测本身是 CPU 密集，多线程无益）
  - 支持取消（后台线程每处理完一只股票检查 cancelled 标志）
"""
from __future__ import annotations

import concurrent.futures
import io
import contextlib
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

# 项目路径（与 app.py 保持一致）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# 全局线程池
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bt")

# 活跃任务映射：run_id -> future
_active_runs: dict[int, concurrent.futures.Future] = {}


def submit_backtest(
    run_id: int,
    codes: list[str],
    start_date: str,
    end_date: str,
    base_shares: int,
    params_override: dict | None = None,
    on_progress: Callable | None = None,
) -> None:
    """提交回测到后台线程池。

    Args:
        run_id: runs 表中的主键
        codes: 股票代码列表
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        base_shares: 底仓股数
        params_override: 参数覆盖 dict
        on_progress: 进度回调 (done, total, status, error) -> None
    """
    from web.results_db import update_run_status

    def _run():
        update_run_status(run_id, "running", progress=f"0/{len(codes)}")
        if on_progress:
            on_progress(done=0, total=len(codes), status="running")
        try:
            _execute_backtest(
                run_id, codes, start_date, end_date,
                base_shares, params_override, on_progress,
            )
        except Exception as ex:
            tb = traceback.format_exc()
            update_run_status(run_id, "failed", error=f"{ex}\n{tb}")
            if on_progress:
                on_progress(done=0, total=len(codes), status="failed", error=str(ex))

    fut = _executor.submit(_run)
    _active_runs[run_id] = fut


def _execute_backtest(
    run_id: int,
    codes: list[str],
    start_date: str,
    end_date: str,
    base_shares: int,
    params_override: dict | None,
    on_progress: Callable | None,
) -> None:
    """实际的回测执行循环。"""
    from web.results_db import (
        update_run_status, save_result, is_run_cancelled, get_conn,
    )
    from at0.paths import ZZ500_5MIN_DIR

    # 延迟导入 backtest_zz500 模块
    from scripts.backtest_zz500 import (
        run_zz500_single,
        load_multi_day_zz500,
    )

    # 延迟导入 at0.backtest 的汇总函数
    try:
        from at0.backtest import summarize_one_stock, aggregate_batch, extract_trades
    except (ValueError, ImportError):
        # 回退到 scripts 中的导入
        from scripts.backtest_zz500 import _import_backtest_deps
        dep = _import_backtest_deps()
        summarize_one_stock = dep["summarize_one_stock"]
        aggregate_batch = dep["aggregate_batch"]
        extract_trades = dep["extract_trades"]

    data_dir = ZZ500_5MIN_DIR
    t0 = time.time()
    per_stock: list[dict] = []
    all_trades_count = 0

    for i, code in enumerate(codes, 1):
        # 检查取消
        if is_run_cancelled(run_id):
            update_run_status(run_id, "cancelled", progress=f"{i-1}/{len(codes)}")
            if on_progress:
                on_progress(done=i - 1, total=len(codes), status="cancelled")
            return

        # 执行单股回测
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = run_zz500_single(
                    code=code,
                    start_date=start_date,
                    end_date=end_date,
                    data_dir=data_dir,
                    base_shares=base_shares,
                    avg_cost=None,
                    tag=f"web_{run_id}",
                    params_override=params_override,
                )
        except Exception as e:
            per_stock.append({"code": code, "error": str(e),
                              "paired_trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                              "profit_factor": 0.0, "payoff_ratio": 0.0,
                              "avg_ce": 0.0, "avg_entry_delay_bars": 0,
                              "avg_exit_delay_bars": 0, "avg_remaining_move_pct": 0.0,
                              "avg_wave_number": 0})
            save_result(run_id, code, {"error": str(e)})
            progress = f"{i}/{len(codes)}"
            update_run_status(run_id, "running", progress=progress)
            if on_progress:
                on_progress(done=i, total=len(codes), status="running")
            continue

        if not result or not result.get("daily_results"):
            per_stock.append({"code": code, "error": "no_data",
                              "paired_trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                              "profit_factor": 0.0, "payoff_ratio": 0.0,
                              "avg_ce": 0.0, "avg_entry_delay_bars": 0,
                              "avg_exit_delay_bars": 0, "avg_remaining_move_pct": 0.0,
                              "avg_wave_number": 0})
            save_result(run_id, code, {"error": "no_data"})
            progress = f"{i}/{len(codes)}"
            update_run_status(run_id, "running", progress=progress)
            if on_progress:
                on_progress(done=i, total=len(codes), status="running")
            continue

        # 汇总个股
        summary = summarize_one_stock(code, result)
        trades = extract_trades(result)
        all_trades_count += len(trades)

        # 尝试计算 v2 指标（CE / entry_delay 等）
        avg_ce = _compute_avg_ce(result, trades)
        avg_entry = _compute_avg_entry_delay(result)
        avg_exit = _compute_avg_exit_delay(result)
        avg_remain = _compute_avg_remaining_move(result)
        avg_wave = _compute_avg_wave_number(result)

        # 计算 profit_factor（基于已配对交易）
        paired = [t for t in trades if t.get("paired")]
        gross_profit = sum(t.get("pnl", 0) for t in paired if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t.get("pnl", 0) for t in paired if t.get("pnl", 0) < 0))
        profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else 0.0

        result_summary = {
            "paired_trades": summary["paired_trades"],
            "win_rate": summary["win_rate"],
            "net_pnl": summary["net_pnl"],
            "profit_factor": profit_factor,
            "payoff_ratio": summary["payoff_ratio"],
            "avg_ce": avg_ce,
            "avg_entry_delay_bars": avg_entry,
            "avg_exit_delay_bars": avg_exit,
            "avg_remaining_move_pct": avg_remain,
            "avg_wave_number": avg_wave,
        }
        save_result(run_id, code, result_summary)
        per_stock.append({**summary, "profit_factor": profit_factor,
                          "avg_ce": avg_ce, "avg_entry_delay_bars": avg_entry,
                          "avg_exit_delay_bars": avg_exit,
                          "avg_remaining_move_pct": avg_remain,
                          "avg_wave_number": avg_wave})

        # 更新进度
        progress = f"{i}/{len(codes)}"
        update_run_status(run_id, "running", progress=progress)
        if on_progress:
            on_progress(done=i, total=len(codes), status="running")

    # 全部完成
    duration = time.time() - t0
    overall = aggregate_batch(per_stock, all_trades_count)
    # 将整体汇总写入 runs 表（存到 params_override 字段临时存放）
    from web.results_db import get_conn
    conn = get_conn()
    conn.execute(
        "UPDATE runs SET status='completed', progress=?, duration_s=?, "
        "params_override=? WHERE id=?",
        (f"{len(codes)}/{len(codes)}", duration,
         json.dumps({"overall": overall}, ensure_ascii=False), run_id),
    )
    conn.commit()
    conn.close()
    if on_progress:
        on_progress(done=len(codes), total=len(codes), status="completed")


def _compute_avg_ce(result: dict, trades: list[dict]) -> float:
    """计算平均捕获效率 (CE)。"""
    ces = []
    for t in trades:
        if not t.get("paired"):
            continue
        entry = t.get("entry_price", 0)
        exit_p = t.get("exit_price", 0)
        high = t.get("high_of_day", 0)
        low = t.get("low_of_day", 0)
        if high and low and (high - low) > 0:
            ce = (exit_p - entry) / (high - low)
            ces.append(ce)
    return round(sum(ces) / len(ces), 4) if ces else 0.0


def _compute_avg_entry_delay(result: dict) -> float:
    bars = result.get("avg_entry_delay_bars", 0)
    return float(bars) if bars else 0.0


def _compute_avg_exit_delay(result: dict) -> float:
    bars = result.get("avg_exit_delay_bars", 0)
    return float(bars) if bars else 0.0


def _compute_avg_remaining_move(result: dict) -> float:
    val = result.get("avg_remaining_move_pct", 0)
    return float(val) if val else 0.0


def _compute_avg_wave_number(result: dict) -> float:
    val = result.get("avg_wave_number", 0)
    return float(val) if val else 0.0


def cancel_backtest(run_id: int) -> None:
    """取消正在运行的回测。"""
    from web.results_db import cancel_run
    cancel_run(run_id)


def get_active_runs() -> list[dict]:
    """获取当前活跃的运行列表。"""
    from web.results_db import get_runs
    all_runs = get_runs(limit=100)
    return [r for r in all_runs if r.get("status") in ("pending", "running")]