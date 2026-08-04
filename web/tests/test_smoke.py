"""Sprint 5 端到端冒烟测试 — 验证 Optuna 新功能"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from web.app import create_app

app = create_app()


def test_params_page():
    """参数页应包含 Optuna 相关元素。"""
    with app.test_client() as c:
        rv = c.get("/params/")
        assert rv.status_code == 200
        html = rv.data.decode("utf-8")
        assert "Optuna" in html
        assert "btn-start-optuna" in html  # 发起优化按钮
        assert "optuna-progress-area" in html  # 进度区域
        assert "btn-apply-optuna" in html  # 一键应用按钮


def test_optuna_info_api():
    """Optuna info API 应返回完整的参数空间和权重。"""
    with app.test_client() as c:
        rv = c.get("/api/params/optuna/info")
        assert rv.status_code == 200
        data = rv.get_json()
        assert len(data["param_space"]) >= 7
        assert len(data["objective_weights"]) == 6


def test_optuna_apply_empty():
    """Optuna apply API 无 study_name 应返回 400。"""
    with app.test_client() as c:
        rv = c.post("/api/params/optuna/apply", json={})
        assert rv.status_code == 400


def test_optuna_apply_invalid():
    """Optuna apply API 不存在的 study 应返回 404。"""
    with app.test_client() as c:
        rv = c.post("/api/params/optuna/apply", json={"study_name": "nonexistent"})
        assert rv.status_code == 404


def test_optuna_start_bad_pool():
    """Optuna start API 不存在的股票池应返回 400。"""
    with app.test_client() as c:
        rv = c.post("/api/params/optuna/start", json={
            "study_name": "test_smoke",
            "n_trials": 3,
            "stock_pool": "nonexistent_pool.txt",
        })
        assert rv.status_code == 400


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))