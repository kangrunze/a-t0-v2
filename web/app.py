"""
QuantWeb — A-T0 回测平台 Flask 入口
====================================
替代原 Streamlit 实现。机构终端风格，4 页蓝图路由。

Usage:
    flask --app web.app run --port 8501
    # 或 python -m web.app
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from flask import Flask, g

from web.results_db import get_conn, init_db, DB_PATH


def create_app() -> Flask:
    """应用工厂。"""
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY="dev",  # 仅开发用，生产环境应覆盖
        PROJECT_ROOT=PROJECT_ROOT,
        DB_PATH=str(DB_PATH),
    )

    # ── 模板上下文 ──
    @app.context_processor
    def inject_globals():
        from datetime import datetime
        from at0.paths import get_data_root
        return {
            "data_root": str(get_data_root()),
            "now": lambda: datetime.now(),
        }

    # ── 数据库连接：每请求一个连接（WAL 模式，读写不互锁）──
    def get_db():
        if "db" not in g:
            g.db = get_conn()
        return g.db

    app.get_db = get_db  # 挂到 app 上供蓝图使用

    @app.teardown_appcontext
    def close_db(exception):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    # ── 注册 Blueprint ──
    from web.blueprints.workbench import bp as workbench_bp
    from web.blueprints.run import bp as run_bp
    from web.blueprints.compare import bp as compare_bp
    from web.blueprints.params import bp as params_bp
    from web.blueprints.results import bp as results_bp
    from web.blueprints.batch import bp as batch_bp

    app.register_blueprint(workbench_bp)
    app.register_blueprint(run_bp)
    app.register_blueprint(compare_bp)
    app.register_blueprint(params_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(batch_bp)

    # ── 初始化数据库 ──
    init_db()

    return app


if __name__ == "__main__":
    import os

    app = create_app()
    # 安全默认：仅本机访问 + 关闭调试器。
    # 需要局域网/云主机暴露时显式设置：
    #   FLASK_HOST=0.0.0.0 python -m web.app
    # 需要调试器时显式设置（仅限可信环境，Werkzeug 调试器可执行任意代码）：
    #   FLASK_DEBUG=1 python -m web.app
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host=host, port=8501, threaded=True, debug=debug)