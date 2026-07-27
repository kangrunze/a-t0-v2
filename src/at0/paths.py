"""
A-T0 数据路径集中配置
====================
所有 data/ 目录下的路径统一从此模块获取，禁止在各脚本/模块里硬编码。

数据根目录解析优先级（高 → 低）：
  1. 环境变量 ``AT0_DATA_DIR``
  2. ``config/thresholds.yaml`` 中的 ``data.root`` 字段
  3. 默认 ``<项目根>/data``

用法:
  from at0.paths import MINUTE_LOCAL_DIR, ZZ500_5MIN_DIR
  或
  from at0.paths import DATA_ROOT  # 自行拼接子路径

设计要点：
  - PROJECT_ROOT 仍指向代码仓库根（src/at0/paths.py → src/at0/ → src/ → 项目根），
    供需要查找 config/ 等代码相关路径的场景使用。
  - DATA_ROOT 默认 = PROJECT_ROOT / "data"，但可通过上述任意方式改为外部目录
    （例如数据已迁出仓库到 D:\\project\\data）。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（代码仓库根），src/at0/paths.py → src/at0/ → src/ → 项目根
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent


def _load_data_root_from_yaml() -> Path | None:
    """从 config/thresholds.yaml 读取 data.root 配置（如果存在）。

    保持轻量：PyYAML 未安装或文件不存在时返回 None，不影响后续 fallback。
    """
    yaml_path = PROJECT_ROOT / "config" / "thresholds.yaml"
    if not yaml_path.exists():
        return None
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            data_section = data.get("data")
            if isinstance(data_section, dict):
                root = data_section.get("root")
                if root:
                    return Path(str(root)).expanduser().resolve()
    except Exception:
        pass
    return None


# ── 数据根目录：环境变量 > yaml 配置 > 默认值 ──
_data_dir_env = os.environ.get("AT0_DATA_DIR")
if _data_dir_env:
    DATA_ROOT: Path = Path(_data_dir_env).expanduser().resolve()
else:
    _yaml_root = _load_data_root_from_yaml()
    if _yaml_root is not None:
        DATA_ROOT = _yaml_root
    else:
        DATA_ROOT = PROJECT_ROOT / "data"


# ── 子目录 / 文件路径常量 ──
# 分钟线数据（按股票分目录存放每日 JSON）
MINUTE_LOCAL_DIR: Path = DATA_ROOT / "minute_local"

# ZZ500 5min K线（每只股票一个 JSON 文件）
ZZ500_5MIN_DIR: Path = DATA_ROOT / "zz500_5min"

# 旧版分钟线目录（westock fetch_minute_bars 落盘）
MINUTE_BARS_DIR: Path = DATA_ROOT / "minute_bars"

# 多日数据缓存
MULTI_DAY_CACHE_DIR: Path = DATA_ROOT / "multi_day_cache"

# 题材快照（L2 输入）
THEMES_FILE: Path = DATA_ROOT / "themes_v17.json"

# 实盘运行时状态文件
POSITIONS_FILE: Path = DATA_ROOT / "positions.json"
POSITIONS_LOCK_FILE: Path = POSITIONS_FILE.with_suffix(".json.lock")
LIVE_OPEN_LEGS_FILE: Path = DATA_ROOT / "live_open_legs.json"
LIVE_OPEN_LEGS_LOCK_FILE: Path = LIVE_OPEN_LEGS_FILE.with_suffix(".json.lock")

# 风控 / 市场层门控落盘文件
L1_GATE_FILE: Path = DATA_ROOT / "l1_gate.json"
MARKET_GATE_FILE: Path = DATA_ROOT / "market_gate.json"


def get_data_root() -> Path:
    """返回当前数据根目录（供需要动态读取的场景使用）。"""
    return DATA_ROOT


def resolve_data_path(*parts: str | Path) -> Path:
    """基于 DATA_ROOT 拼接子路径。

    例: resolve_data_path("minute_local", "600029", "2026-07-23.json")
    """
    p = DATA_ROOT
    for part in parts:
        p = p / part
    return p


def __repr__() -> str:  # pragma: no cover
    return (
        f"at0.paths(DATA_ROOT={DATA_ROOT}, PROJECT_ROOT={PROJECT_ROOT}, "
        f"AT0_DATA_DIR_env={os.environ.get('AT0_DATA_DIR')!r})"
    )
