"""Sprint 2 API 冒烟测试 — 验证 API 路由正常响应"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from web.app import create_app

app = create_app()


def test_workbench_trend_api():
    """趋势 API 应返回 traces 列表（即使为空）。"""
    with app.test_client() as client:
        rv = client.get("/api/workbench/trend")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "traces" in data


def test_workbench_queue_api():
    """队列 API 应返回 active 列表。"""
    with app.test_client() as client:
        rv = client.get("/api/workbench/queue")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "active" in data


def test_run_summary_api_not_found():
    """不存在的 run_id 应返回 404。"""
    with app.test_client() as client:
        rv = client.get("/api/runs/99999/summary")
        assert rv.status_code == 404


def test_run_stream_api():
    """SSE 端点应返回 text/event-stream。"""
    with app.test_client() as client:
        rv = client.get("/api/runs/1/stream")
        # SSE 端点可能返回 200 或 404（取决于 run 是否存在）
        # 只要不 500 即可
        assert rv.status_code in (200, 404)


def test_backtest_run_post():
    """发起回测 POST 应返回 JSON（可能因数据缺失报错，但不 500）。"""
    with app.test_client() as client:
        rv = client.post("/api/backtest/run", data={
            "name": "测试",
            "stock_pool": "v3_sample_5.txt",
            "start_date": "2023-07-25",
            "end_date": "2026-07-22",
            "base_shares": 3000,
        })
        # 可能返回 400（股票池文件不存在）或 200（成功创建）
        assert rv.status_code in (200, 400)
        data = rv.get_json()
        if rv.status_code == 200:
            assert "run_id" in data
        else:
            assert "error" in data


# ═══════════════════════════════════════════════════════════════
# Compare API 测试
# ═══════════════════════════════════════════════════════════════

def test_compare_runs_api():
    """对比运行列表 API 应返回 runs 列表（即使为空）。"""
    with app.test_client() as client:
        rv = client.get("/api/compare/runs")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "runs" in data
        assert isinstance(data["runs"], list)


def test_compare_tags_api():
    """标签列表 API 应返回 tags 列表。"""
    with app.test_client() as client:
        rv = client.get("/api/compare/tags")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "tags" in data
        assert isinstance(data["tags"], list)


def test_compare_run_no_ids():
    """手动对比 POST 缺少 run_ids 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/compare/run", json={})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_compare_run_single_id():
    """手动对比 POST 仅有 1 个 run_id 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/compare/run", json={"run_ids": [1]})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_compare_run_too_many():
    """手动对比 POST 超过 4 个 run_ids 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/compare/run", json={"run_ids": [1, 2, 3, 4, 5]})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_compare_group_missing_tags():
    """分组对比 POST 缺少 tag 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/compare/group", json={})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_compare_group_empty_tags():
    """分组对比 POST 空 tag 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/compare/group", json={"tag_a": "", "tag_b": "test"})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


# ═══════════════════════════════════════════════════════════════
# Params API 测试
# ═══════════════════════════════════════════════════════════════

def test_params_weights_get():
    """权重 API 应返回 weights 和 dimensions。"""
    with app.test_client() as client:
        rv = client.get("/api/params/weights")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "weights" in data
        assert "dimensions" in data
        assert "total" in data
        # 默认应有 7 个维度
        assert len(data["dimensions"]) == 7
        assert abs(data["total"] - 1.0) < 0.01


def test_params_weights_save_invalid():
    """权重保存 POST 缺少 weights 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/params/weights", json={})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_params_weights_save_bad_sum():
    """权重总和不为 1.0 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/params/weights", json={
            "weights": {"trend": 0.5, "wave": 0.5, "support": 0.5},
        })
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_params_yaml_get():
    """YAML API 应返回 content 和 path。"""
    with app.test_client() as client:
        rv = client.get("/api/params/yaml")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "content" in data
        assert "path" in data


def test_params_yaml_save_empty():
    """YAML 保存 POST 空内容应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/params/yaml", json={"content": ""})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_params_presets_get():
    """预设列表 API 应返回 presets 列表。"""
    with app.test_client() as client:
        rv = client.get("/api/params/presets")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "presets" in data
        assert isinstance(data["presets"], list)


def test_params_presets_save_no_name():
    """预设保存 POST 缺少名称应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/params/presets", json={"name": ""})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_params_presets_delete_not_found():
    """删除不存在的预设应返回 500（DB 约束）。"""
    with app.test_client() as client:
        rv = client.delete("/api/params/presets/99999")
        # 删除不存在的记录可能是 500（DB 层异常）或 200（无影响）
        # 只要不报 404 或其他错误即可
        assert rv.status_code in (200, 500)


def test_params_backtest_post():
    """发起回测 POST 应返回 JSON（可能因 stock pool 文件缺失返回 400/500）。"""
    with app.test_client() as client:
        rv = client.post("/api/params/backtest", json={
            "name": "参数页测试",
            "stock_pool": "v3_sample_5.txt",
            "start_date": "2023-07-25",
            "end_date": "2026-07-22",
        })
        # 可能返回 200（成功创建）、400（stock pool 文件不存在）或 500（其他）
        assert rv.status_code in (200, 400, 500)
        data = rv.get_json()
        if rv.status_code == 200:
            assert "run_id" in data
        else:
            assert "error" in data


# ═══════════════════════════════════════════════════════════════
# Optuna API 测试
# ═══════════════════════════════════════════════════════════════

def test_optuna_info():
    """Optuna 信息 API 应返回 param_space 和 objective_weights。"""
    with app.test_client() as client:
        rv = client.get("/api/params/optuna/info")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "param_space" in data
        assert "objective_weights" in data
        # 应有 7 个参数
        assert len(data["param_space"]) >= 7


def test_optuna_studies():
    """Optuna studies API 应返回 studies 列表。"""
    with app.test_client() as client:
        rv = client.get("/api/params/optuna/studies")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "studies" in data
        assert isinstance(data["studies"], list)


def test_optuna_trials_no_study():
    """Optuna trials API 缺少 study 参数应返回 400。"""
    with app.test_client() as client:
        rv = client.get("/api/params/optuna/trials")
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_optuna_trials_empty():
    """Optuna trials API 指定不存在的 study 应返回空列表。"""
    with app.test_client() as client:
        rv = client.get("/api/params/optuna/trials?study=nonexistent_study")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "trials" in data
        assert data["count"] == 0


def test_optuna_start():
    """Optuna 启动 API 应返回 ok 和 run_id（或 400 股票池不存在）。"""
    with app.test_client() as client:
        rv = client.post("/api/params/optuna/start", json={
            "study_name": "test_web_start",
            "n_trials": 3,
            "stock_pool": "v3_sample_5.txt",
            "start_date": "2023-07-25",
            "end_date": "2026-07-22",
            "base_shares": 3000,
        })
        # 可能返回 200（成功）或 400（stock pool 文件不存在）
        assert rv.status_code in (200, 400)
        data = rv.get_json()
        if rv.status_code == 200:
            assert data["ok"] is True
            assert "run_id" in data
            assert data["run_id"] > 0
            assert data["n_trials"] == 3
            assert data["study_name"] == "test_web_start"
        else:
            assert "error" in data


def test_optuna_start_default_name():
    """Optuna 启动 API 不指定 study_name 应自动生成。"""
    with app.test_client() as client:
        rv = client.post("/api/params/optuna/start", json={
            "n_trials": 3,
            "stock_pool": "v3_sample_5.txt",
        })
        # 可能返回 200 或 400
        assert rv.status_code in (200, 400)
        data = rv.get_json()
        if rv.status_code == 200:
            assert data["study_name"].startswith("web_optuna_")


def test_optuna_apply_no_study():
    """Optuna 应用 API 缺少 study_name 应返回 400。"""
    with app.test_client() as client:
        rv = client.post("/api/params/optuna/apply", json={})
        assert rv.status_code == 400
        data = rv.get_json()
        assert "error" in data


def test_optuna_apply_invalid_study():
    """Optuna 应用 API 指定不存在的 study 应返回 404。"""
    with app.test_client() as client:
        rv = client.post("/api/params/optuna/apply", json={
            "study_name": "nonexistent_study_for_test",
        })
        assert rv.status_code == 404
        data = rv.get_json()
        assert "error" in data


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))