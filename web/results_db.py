"""
QuantWeb — 结果持久化层
========================
SQLite 数据库存储回测运行记录、结果摘要、逐日明细。

Schema:
  - runs: 回测运行记录
  - results: 个股结果摘要
  - daily_results: 逐日明细
  - config_snapshots: 参数配置快照
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "quantweb.db"


def get_conn() -> sqlite3.Connection:
    """获取数据库连接。

    并发策略：
      - WAL 模式：读写可并发，后台写线程（回测进度/结果）不会阻塞前台读请求。
        默认 rollback-journal 模式会给整个 db 文件加写锁，导致"回测跑着时
        点哪儿都慢"（读请求撞写锁，默认 5s 超时才返回）。
      - busy_timeout=3000：万一仍撞锁，最多等 3 秒而非默认 5 秒，兜底。
      - timeout=3.0：connect 层面的等锁超时，与 busy_timeout 协同。
    每个连接独立设置 PRAGMA：SQLite 的 journal_mode 是数据库级持久属性
    （第一次设为 WAL 后会持久生效），但 busy_timeout 是连接级，必须每连接设。
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=3.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_db() -> None:
    """初始化数据库表结构。"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            status TEXT NOT NULL DEFAULT 'pending',
            stock_pool TEXT,
            start_date TEXT,
            end_date TEXT,
            params_override TEXT,
            tag TEXT,
            duration_s REAL,
            error TEXT,
            progress TEXT
        );

        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            paired_trades INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            payoff_ratio REAL DEFAULT 0,
            avg_ce REAL DEFAULT 0,
            avg_entry_delay REAL DEFAULT 0,
            avg_exit_delay REAL DEFAULT 0,
            avg_remaining_move REAL DEFAULT 0,
            avg_wave_number REAL DEFAULT 0,
            error TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(id)
        );

        CREATE TABLE IF NOT EXISTS daily_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            net_pnl REAL DEFAULT 0,
            trades INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            FOREIGN KEY (result_id) REFERENCES results(id)
        );

        CREATE TABLE IF NOT EXISTS config_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            yaml_content TEXT,
            run_id INTEGER,
            description TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(id)
        );

        CREATE TABLE IF NOT EXISTS optuna_trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_name TEXT NOT NULL,
            trial_number INTEGER NOT NULL,
            objective REAL,
            params TEXT,
            net_pnl REAL,
            profit_factor REAL,
            win_rate REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            params_override TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
    """)

    # 兼容旧表：添加 progress 列（如果不存在）
    try:
        conn.execute("ALTER TABLE runs ADD COLUMN progress TEXT")
    except Exception:
        pass

    # 兼容旧表：添加 cancelled 列（停止任务用）
    try:
        conn.execute("ALTER TABLE runs ADD COLUMN cancelled INTEGER DEFAULT 0")
    except Exception:
        pass

    # 外键索引：get_run_results / get_run_summary 按 run_id 过滤，
    # 无索引会退化成全表扫描（数据量小暂时无感，运行次数多后变慢）。
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_results_result_id ON daily_results(result_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_optuna_study ON optuna_trials(study_name)")

    conn.commit()
    conn.close()


def save_run(
    name: str,
    stock_pool: str,
    start_date: str,
    end_date: str,
    params_override: dict | None = None,
    tag: str = "web",
) -> int:
    """创建回测运行记录，返回 run_id。"""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO runs (name, stock_pool, start_date, end_date, params_override, tag, status)
           VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
        (name, stock_pool, start_date, end_date,
         json.dumps(params_override or {}, ensure_ascii=False), tag),
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def update_run_status(run_id: int, status: str, duration_s: float | None = None,
                       error: str | None = None, progress: str | None = None) -> None:
    """更新回测运行状态。"""
    conn = get_conn()
    fields = ["status = ?"]
    values = [status]
    if duration_s is not None:
        fields.append("duration_s = ?")
        values.append(duration_s)
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    values.append(run_id)
    conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_run(run_id: int) -> dict | None:
    """获取单次运行记录。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def cancel_run(run_id: int) -> None:
    """请求停止某次回测（后台线程每处理完一只股票后检查此标志）。"""
    conn = get_conn()
    conn.execute(
        "UPDATE runs SET cancelled = 1 WHERE id = ? AND status = 'running'",
        (run_id,),
    )
    conn.commit()
    conn.close()


def is_run_cancelled(run_id: int) -> bool:
    """检查某次回测是否已被请求停止。"""
    conn = get_conn()
    row = conn.execute("SELECT cancelled FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return bool(row["cancelled"]) if row else False


def save_result(run_id: int, code: str, summary: dict) -> int:
    """保存个股结果，返回 result_id。"""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO results (run_id, code, paired_trades, win_rate, net_pnl,
                                profit_factor, payoff_ratio, avg_ce,
                                avg_entry_delay, avg_exit_delay,
                                avg_remaining_move, avg_wave_number, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, code,
         summary.get("paired_trades", 0),
         summary.get("win_rate", 0),
         summary.get("net_pnl", 0),
         summary.get("profit_factor", 0),
         summary.get("payoff_ratio", 0),
         summary.get("avg_ce", 0),
         summary.get("avg_entry_delay_bars", 0),
         summary.get("avg_exit_delay_bars", 0),
         summary.get("avg_remaining_move_pct", 0),
         summary.get("avg_wave_number", 0),
         summary.get("error")),
    )
    result_id = cur.lastrowid
    conn.commit()
    conn.close()
    return result_id


def get_runs(limit: int = 20) -> list[dict]:
    """获取最近的回测运行记录。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_run_results(run_id: int) -> list[dict]:
    """获取某次运行的个股结果。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM results WHERE run_id = ? ORDER BY net_pnl DESC", (run_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_runs_by_ids(run_ids: list[int]) -> list[dict]:
    """批量获取指定 ID 的运行记录。"""
    if not run_ids:
        return []
    conn = get_conn()
    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM runs WHERE id IN ({placeholders}) ORDER BY id DESC",
        run_ids,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_runs_by_tag(tag: str, limit: int = 50) -> list[dict]:
    """按标签获取运行记录。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM runs WHERE tag = ? ORDER BY id DESC LIMIT ?",
        (tag, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_run_tags() -> list[str]:
    """获取所有不同的 tag 值。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT tag FROM runs WHERE tag IS NOT NULL AND tag != '' ORDER BY tag"
    ).fetchall()
    conn.close()
    return [r["tag"] for r in rows]


def get_run_summary(run_id: int) -> dict | None:
    """获取某次运行的整体汇总。"""
    results = get_run_results(run_id)
    if not results:
        return None
    total_paired = sum(r.get("paired_trades", 0) for r in results)
    total_wins = sum(int(r["paired_trades"] * r["win_rate"]) for r in results)
    total_net = sum(r.get("net_pnl", 0) for r in results)
    profitable = sum(1 for r in results if r.get("net_pnl", 0) > 0)
    return {
        "stocks": len(results),
        "total_paired": total_paired,
        "total_net": round(total_net, 2),
        "profitable_stocks": profitable,
        "profitable_ratio": round(profitable / len(results), 4) if results else 0,
        "avg_win_rate": round(sum(r.get("win_rate", 0) for r in results) / len(results), 4),
        "avg_ce": round(sum(r.get("avg_ce", 0) for r in results) / len(results), 4),
    }


def save_optuna_trial(study_name: str, trial_number: int, objective: float,
                       params: dict, net_pnl: float, profit_factor: float,
                       win_rate: float) -> None:
    """保存 Optuna trial 记录。"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO optuna_trials (study_name, trial_number, objective, params,
                                       net_pnl, profit_factor, win_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (study_name, trial_number, objective,
         json.dumps(params, ensure_ascii=False),
         net_pnl, profit_factor, win_rate),
    )
    conn.commit()
    conn.close()


def get_optuna_best(study_name: str) -> dict | None:
    """获取 Optuna 最优 trial。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM optuna_trials WHERE study_name = ? ORDER BY objective DESC LIMIT 1",
        (study_name,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_optuna_studies() -> list[dict]:
    """获取所有 unique study 名称及 trial 数、最佳 objective。"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT study_name,
               COUNT(*) AS trial_count,
               MAX(objective) AS best_objective,
               MAX(created_at) AS last_run
        FROM optuna_trials
        GROUP BY study_name
        ORDER BY last_run DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_optuna_trials(study_name: str, limit: int = 200) -> list[dict]:
    """获取某 study 的所有 trial 记录。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM optuna_trials WHERE study_name = ? ORDER BY trial_number ASC LIMIT ?",
        (study_name, limit),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["params"] = json.loads(d["params"]) if d.get("params") else {}
        except (json.JSONDecodeError, TypeError):
            d["params"] = {}
        result.append(d)
    return result


# ── 参数预设 ──

def save_preset(name: str, params_override: dict, description: str = "") -> int:
    """保存参数预设。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO presets (name, description, params_override) VALUES (?, ?, ?)",
            (name, description, json.dumps(params_override, ensure_ascii=False)),
        )
        preset_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        # 名称已存在，更新
        conn.execute(
            "UPDATE presets SET description = ?, params_override = ? WHERE name = ?",
            (description, json.dumps(params_override, ensure_ascii=False), name),
        )
        conn.commit()
        cur = conn.execute("SELECT id FROM presets WHERE name = ?", (name,))
        preset_id = cur.fetchone()["id"]
    finally:
        conn.close()
    return preset_id


def get_presets() -> list[dict]:
    """获取所有参数预设。"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM presets ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["params_override"] = json.loads(d["params_override"]) if d.get("params_override") else {}
        except (json.JSONDecodeError, TypeError):
            d["params_override"] = {}
        result.append(d)
    return result


def delete_preset(preset_id: int) -> None:
    """删除参数预设。"""
    conn = get_conn()
    conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()


# 初始化
init_db()