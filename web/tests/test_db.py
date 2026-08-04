"""
QuantWeb DB 层单元测试 (Sprint 1 验收)
========================================
验证项：
  - WAL 模式开启
  - busy_timeout 设置
  - 索引已创建
  - 基本 CRUD 操作
  - 旧数据兼容性
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))


# ── 辅助：临时数据库 ──
def _temp_db(monkey_module=None):
    """创建临时 DB 用于测试，返回 (db_path, results_db_module 的引用)。"""
    import web.results_db as db
    # 保存原路径并替换
    orig_path = db.DB_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.DB_PATH = Path(tmp.name)
    db.init_db()
    return tmp.name, db, orig_path


def _cleanup(db_path, db_module, orig_path):
    db_module.DB_PATH = orig_path
    if db_path and os.path.exists(db_path):
        os.unlink(db_path)
    # 清理 .wal 和 .shm
    for ext in ("-wal", "-shm"):
        p = str(db_path) + ext
        if os.path.exists(p):
            os.unlink(p)


# ═══════════════════════════════════════════════════════════════
# Test 1: WAL 模式
# ═══════════════════════════════════════════════════════════════
def test_journal_mode_is_wal():
    db_path, db_mod, orig = _temp_db()
    try:
        conn = db_mod.get_conn()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert journal == "wal", f"预期 wal, 实际 {journal}"
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 2: busy_timeout
# ═══════════════════════════════════════════════════════════════
def test_busy_timeout_is_3000():
    db_path, db_mod, orig = _temp_db()
    try:
        conn = db_mod.get_conn()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert timeout == 3000, f"预期 3000, 实际 {timeout}"
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 3: 索引已创建
# ═══════════════════════════════════════════════════════════════
def test_indexes_exist():
    db_path, db_mod, orig = _temp_db()
    try:
        conn = db_mod.get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        names = [r[0] for r in rows]
        conn.close()
        expected = [
            "idx_results_run_id",
            "idx_daily_results_result_id",
            "idx_runs_status",
            "idx_optuna_study",
        ]
        for name in expected:
            assert name in names, f"缺少索引: {name}"
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 4: CRUD — 创建运行
# ═══════════════════════════════════════════════════════════════
def test_save_and_get_run():
    db_path, db_mod, orig = _temp_db()
    try:
        run_id = db_mod.save_run(
            name="测试运行",
            stock_pool="v3_sample_5.txt",
            start_date="2023-07-25",
            end_date="2026-07-22",
            params_override={"test": True},
            tag="unit_test",
        )
        assert run_id > 0, f"预期 run_id > 0, 实际 {run_id}"

        run = db_mod.get_run(run_id)
        assert run is not None
        assert run["name"] == "测试运行"
        assert run["status"] == "pending"
        assert run["tag"] == "unit_test"
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 5: CRUD — 更新状态与进度
# ═══════════════════════════════════════════════════════════════
def test_update_run_status_and_progress():
    db_path, db_mod, orig = _temp_db()
    try:
        run_id = db_mod.save_run("进度测试", "all", "2023-01-01", "2023-12-31")
        db_mod.update_run_status(run_id, "running", progress="5/100")
        run = db_mod.get_run(run_id)
        assert run["status"] == "running"
        assert run["progress"] == "5/100"

        db_mod.update_run_status(run_id, "completed", duration_s=12.5)
        run = db_mod.get_run(run_id)
        assert run["status"] == "completed"
        assert run["duration_s"] == 12.5
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 6: CRUD — 保存结果与汇总
# ═══════════════════════════════════════════════════════════════
def test_save_result_and_summary():
    db_path, db_mod, orig = _temp_db()
    try:
        run_id = db_mod.save_run("结果测试", "v3_sample_5.txt", "2023-01-01", "2023-12-31")
        db_mod.save_result(run_id, "000001", {
            "paired_trades": 10,
            "win_rate": 0.5,
            "net_pnl": 1500.0,
            "profit_factor": 1.8,
            "payoff_ratio": 1.2,
            "avg_ce": 0.35,
            "avg_entry_delay_bars": 2,
            "avg_exit_delay_bars": 1,
            "avg_remaining_move_pct": 0.1,
            "avg_wave_number": 3,
        })
        db_mod.save_result(run_id, "000002", {
            "paired_trades": 8,
            "win_rate": 0.375,
            "net_pnl": -500.0,
            "profit_factor": 0.6,
            "payoff_ratio": 0.8,
            "avg_ce": 0.15,
            "avg_entry_delay_bars": 3,
            "avg_exit_delay_bars": 2,
            "avg_remaining_move_pct": 0.05,
            "avg_wave_number": 2,
        })

        results = db_mod.get_run_results(run_id)
        assert len(results) == 2
        assert results[0]["code"] == "000001"  # net_pnl 降序

        summary = db_mod.get_run_summary(run_id)
        assert summary is not None
        assert summary["stocks"] == 2
        assert summary["total_paired"] == 18
        assert summary["total_net"] == 1000.0
        assert summary["profitable_stocks"] == 1
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 7: 取消运行
# ═══════════════════════════════════════════════════════════════
def test_cancel_run():
    db_path, db_mod, orig = _temp_db()
    try:
        run_id = db_mod.save_run("取消测试", "all", "2023-01-01", "2023-12-31")
        db_mod.update_run_status(run_id, "running")
        assert not db_mod.is_run_cancelled(run_id)
        db_mod.cancel_run(run_id)
        assert db_mod.is_run_cancelled(run_id)
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 8: 预设 CRUD
# ═══════════════════════════════════════════════════════════════
def test_presets():
    db_path, db_mod, orig = _temp_db()
    try:
        pid = db_mod.save_preset("测试预设", {"sp": {"alpha_threshold_open": 72}}, "用于测试")
        assert pid > 0

        presets = db_mod.get_presets()
        assert len(presets) == 1
        assert presets[0]["name"] == "测试预设"

        # 更新已存在的预设
        pid2 = db_mod.save_preset("测试预设", {"sp": {"alpha_threshold_open": 80}}, "更新后")
        assert pid2 == pid or pid2 > 0
        presets = db_mod.get_presets()
        assert len(presets) == 1

        db_mod.delete_preset(pid)
        presets = db_mod.get_presets()
        assert len(presets) == 0
    finally:
        _cleanup(db_path, db_mod, orig)


# ═══════════════════════════════════════════════════════════════
# Test 9: 旧数据兼容性 — 用现有 quantweb.db 验证
# ═══════════════════════════════════════════════════════════════
def test_legacy_db_compatibility():
    """验证已有 quantweb.db 可被新平台正常读取。

    如果当前没有 quantweb.db，此测试跳过。
    如果存在，验证其表结构包含所有必需的列。
    """
    from web.results_db import DB_PATH, get_conn
    if not DB_PATH.exists():
        return  # 跳过
    conn = get_conn()
    # 检查 runs 表有所有必需列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()]
    conn.close()
    for required in ["id", "name", "status", "created_at", "stock_pool",
                      "start_date", "end_date", "params_override", "tag",
                      "duration_s", "error", "progress", "cancelled"]:
        assert required in cols, f"旧数据库缺少列: {required}"


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))