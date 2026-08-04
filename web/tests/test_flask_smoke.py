"""Flask 冒烟测试 - 验证 4 个页面均可正常渲染"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from web.app import create_app

app = create_app()


def test_workbench_page():
    with app.test_client() as client:
        rv = client.get("/")
        assert rv.status_code == 200, f"工作台页返回 {rv.status_code}: {rv.data[:500]}"


def test_run_list_page():
    with app.test_client() as client:
        rv = client.get("/runs/")
        assert rv.status_code == 200, f"运行列表页返回 {rv.status_code}: {rv.data[:500]}"


def test_compare_page():
    with app.test_client() as client:
        rv = client.get("/compare/")
        assert rv.status_code == 200, f"策略对比页返回 {rv.status_code}: {rv.data[:500]}"


def test_params_page():
    with app.test_client() as client:
        rv = client.get("/params/")
        assert rv.status_code == 200, f"参数与预设页返回 {rv.status_code}: {rv.data[:500]}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=long"]))