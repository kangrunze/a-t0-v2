"""
measurement.time_split
=======================
训练集/验证集时间划分工具。

从缓存目录中列举股票代码、获取完整交易日列表、按比例划分训练集和验证集、
以及按日期范围过滤日线数据。

与 ``run_baseline_measurement.py`` 配合使用，提供样本外验证的时间划分基础。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# TimeSplit
# ═══════════════════════════════════════════════════════════════

@dataclass
class TimeSplit:
    """训练集/验证集时间划分结果。

    :param train_start:  训练集起始日期 (YYYY-MM-DD)
    :param train_end:    训练集结束日期 (YYYY-MM-DD)
    :param test_start:   验证集起始日期 (YYYY-MM-DD)
    :param test_end:     验证集结束日期 (YYYY-MM-DD)
    :param train_days:   训练集交易日数
    :param test_days:    验证集交易日数
    :param all_dates:    完整日期列表（排序后）
    """

    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_days: int
    test_days: int
    all_dates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """将划分结果转为 dict，便于序列化为 JSON。"""
        return {
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "train_days": self.train_days,
            "test_days": self.test_days,
            "all_dates": self.all_dates,
        }


# ═══════════════════════════════════════════════════════════════
# 时间划分
# ═══════════════════════════════════════════════════════════════

def split_time_range(
    all_dates: list[str],
    train_ratio: float = 0.5,
) -> Optional[TimeSplit]:
    """将排序后的日期列表按比例划分为训练集和验证集。

    划分规则：
      - 按 ``train_ratio`` 比例取前 N 个日期为训练集，其余为验证集
      - 训练集至少保留 1 个交易日，否则返回 ``None``
      - 训练集和验证集均至少 1 个交易日，否则返回 ``None``

    :param all_dates:   排序后的日期字符串列表 (YYYY-MM-DD)
    :param train_ratio: 训练集占比，默认 0.5
    :returns:           划分结果，若日期不足则返回 ``None``
    """
    if not all_dates:
        return None

    sorted_dates = sorted(all_dates)
    n = len(sorted_dates)
    split_idx = max(1, int(n * train_ratio))

    # 确保验证集至少 1 天
    if split_idx >= n:
        split_idx = n - 1

    train_dates = sorted_dates[:split_idx]
    test_dates = sorted_dates[split_idx:]

    if not train_dates or not test_dates:
        return None

    return TimeSplit(
        train_start=train_dates[0],
        train_end=train_dates[-1],
        test_start=test_dates[0],
        test_end=test_dates[-1],
        train_days=len(train_dates),
        test_days=len(test_dates),
        all_dates=sorted_dates,
    )


# ═══════════════════════════════════════════════════════════════
# 缓存读取
# ═══════════════════════════════════════════════════════════════

def list_cached_codes(cache_dir: str) -> list[str]:
    """列举缓存目录中所有可用的股票代码。

    缓存文件命名格式为 ``{pure_code}_{start}_{end}.json``，
    函数从文件名中提取 ``pure_code`` 部分并去重返回。

    :param cache_dir: 缓存目录路径
    :returns:         去重排序后的股票代码列表
    """
    cache_path = Path(cache_dir)
    if not cache_path.is_dir():
        return []

    codes: set[str] = set()
    for fpath in cache_path.glob("*.json"):
        # 文件名格式: {code}_{start}_{end}.json
        stem = fpath.stem  # 去掉 .json 后缀
        parts = stem.split("_")
        if len(parts) >= 3:
            # 代码是第一部分，日期部分可能含连字符，但代码是纯数字
            code = parts[0]
            if code.isdigit():
                codes.add(code)

    return sorted(codes)


def get_all_dates_from_cache(cache_dir: str, code: str) -> list[str]:
    """从缓存文件加载指定股票的全部交易日，返回排序后的日期列表。

    查找缓存目录中第一个匹配 ``{code}_*.json`` 的文件，
    加载其 ``daily_bars`` 的键（日期）并排序返回。

    :param cache_dir: 缓存目录路径
    :param code:      股票代码（纯数字 6 位）
    :returns:         排序后的日期列表 (YYYY-MM-DD)，若无缓存则返回空列表
    """
    cache_path = Path(cache_dir)
    if not cache_path.is_dir():
        return []

    # 查找匹配的缓存文件，取最新的一个
    candidates = sorted(cache_path.glob(f"{code}_*.json"))
    if not candidates:
        return []

    try:
        with open(candidates[-1], "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    daily_bars = data.get("daily_bars", {})
    if not daily_bars:
        return []

    return sorted(daily_bars.keys())


# ═══════════════════════════════════════════════════════════════
# 日期过滤
# ═══════════════════════════════════════════════════════════════

def filter_dates(
    daily_bars: dict[str, ...],
    start: str,
    end: str,
) -> dict:
    """按日期范围过滤一个以日期为键的字典（含两端）。

    :param daily_bars: 日期键值字典，如 ``{"2026-01-01": [...], ...}``
    :param start:      起始日期 (YYYY-MM-DD)，包含
    :param end:        结束日期 (YYYY-MM-DD)，包含
    :returns:          过滤后的字典，仅保留 ``start <= date <= end`` 的条目
    """
    return {d: v for d, v in daily_bars.items() if start <= d <= end}