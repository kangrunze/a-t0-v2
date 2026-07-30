"""
BaseEngine — V3 Engine 基类
==============================

所有 Engine 的统一接口：
  - score(bars, snap, direction) -> float  (0~100 子评分)
  - name -> str  (Engine 名称)

设计原则：
  1. 各 Engine 独立可插拔，不互相依赖
  2. score 方法纯函数，无副作用
  3. None 安全：缺失数据返回中性 50
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class BaseEngine(ABC):
    """V3 Engine 基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Engine 名称。"""
        ...

    @abstractmethod
    def score(
        self,
        bars: list[dict],
        snap: dict,
        direction: str = "reduce",
    ) -> float:
        """计算子评分 (0~100)。

        :param bars: 完整 K 线序列（含历史），按时间升序
        :param snap: 当前 bar 的特征快照（features.py 产出）
        :param direction: "reduce"（卖出腿）或 "add"（买入腿）
        :return: 0~100 的子评分
        """
        ...
