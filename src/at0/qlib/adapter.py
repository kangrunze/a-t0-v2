"""
Qlib Adapter — AT0 DataFrame → Qlib DatasetH（核心入口）
========================================================

定位（V3 Part 5）：
  zz500_5min JSON → loader.DataFrame → adapter 算因子 → StaticDataLoader → DatasetH

设计决策：
  采用 StaticDataLoader + pandas 自算因子（Alpha158 核心子集），不走 dump_bin。
  原因：
    1. 保留 AT0 JSON 数据层不动（硬约束）
    2. 避免 dump_bin 一次性成本（500股×3年×5min=17M行）
    3. 够 G1 全因子 IC 筛选用（30+ 连续因子）
    4. 后续 G5 可升级到 dump_bin + 完整 Alpha158 Expression

Alpha158 因子子集（pandas 实现，分组）：
  - 价格形态（KMID/KLEN/KUP/KLOW/KSFT）
  - 动量（ROC5/ROC10/ROC20/MA5/MA10/MA20）
  - 波动（STD5/STD10/STD20/ATR14）
  - 量价（VSTD5/VRATIO/AMOUNT_STD）
  - 横截面位置（CLOSE2HIGH/CLOSE2LOW）
  - 时间（MIN_OF_DAY）
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import is_qlib_available


# ═══════════════════════════════════════════════════════════════
# Alpha158 核心因子子集（pandas 实现）
# ═══════════════════════════════════════════════════════════════

def _rolling_by_instrument(
    df: pd.DataFrame,
    col: str,
    window: int,
    func,
) -> pd.Series:
    """按 instrument 分组滚动计算。"""
    return df.groupby(level="instrument")[col].transform(
        lambda s: s.rolling(window, min_periods=max(1, window // 2)).apply(func, raw=True)
    )


def compute_alpha158_subset(features_df: pd.DataFrame) -> pd.DataFrame:
    """在 OHLCV DataFrame 上计算 Alpha158 核心因子子集。

    输入：MultiIndex(instrument, datetime) × [open,high,low,close,volume,amount]
    输出：同 index × [30+ 因子列]
    """
    g = features_df.copy()
    # 确保按 instrument 内时间升序
    g = g.sort_index(level=["instrument", "datetime"])

    open_ = g["open"]
    high = g["high"]
    low = g["low"]
    close = g["close"]
    volume = g["volume"]
    amount = g.get("amount", volume * close)

    out = pd.DataFrame(index=g.index)
    eps = 1e-12

    # ── 价格形态（K线形态，归一化到 open）──
    out["KMID"] = (close - open_) / (open_ + eps)
    out["KLEN"] = (high - low) / (open_ + eps)
    out["KMID2"] = (close - open_) / (high - low + eps)
    out["KUP"] = (high - np.maximum(open_, close)) / (open_ + eps)
    out["KLOW"] = (np.minimum(open_, close) - low) / (open_ + eps)
    out["KSFT"] = (2 * close - high - low) / (open_ + eps)
    out["OPEN0"] = open_ / (close + eps)
    out["HIGH0"] = high / (close + eps)
    out["LOW0"] = low / (close + eps)

    # ── 动量（ROC + 均线偏离）──
    out["ROC5"] = close.groupby(level="instrument").pct_change(5)
    out["ROC10"] = close.groupby(level="instrument").pct_change(10)
    out["ROC20"] = close.groupby(level="instrument").pct_change(20)

    ma5 = close.groupby(level="instrument").rolling(5, min_periods=3).mean().reset_index(level=0, drop=True)
    ma10 = close.groupby(level="instrument").rolling(10, min_periods=5).mean().reset_index(level=0, drop=True)
    ma20 = close.groupby(level="instrument").rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    out["MA5"] = ma5 / (close + eps)
    out["MA10"] = ma10 / (close + eps)
    out["MA20"] = ma20 / (close + eps)
    out["MA5_MA20"] = ma5 / (ma20 + eps)

    # ── 波动（STD + ATR）──
    std5 = close.groupby(level="instrument").rolling(5, min_periods=3).std().reset_index(level=0, drop=True)
    std10 = close.groupby(level="instrument").rolling(10, min_periods=5).std().reset_index(level=0, drop=True)
    std20 = close.groupby(level="instrument").rolling(20, min_periods=10).std().reset_index(level=0, drop=True)
    out["STD5"] = std5 / (close + eps)
    out["STD10"] = std10 / (close + eps)
    out["STD20"] = std20 / (close + eps)

    # ATR14（True Range 14周期均值，归一化到 close）
    tr = pd.concat([
        high - low,
        (high - close.groupby(level="instrument").shift(1)).abs(),
        (low - close.groupby(level="instrument").shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.groupby(level="instrument").rolling(14, min_periods=7).mean().reset_index(level=0, drop=True)
    out["ATR14"] = atr14 / (close + eps)

    # ── 量价 ──
    vstd5 = volume.groupby(level="instrument").rolling(5, min_periods=3).std().reset_index(level=0, drop=True)
    vma5 = volume.groupby(level="instrument").rolling(5, min_periods=3).mean().reset_index(level=0, drop=True)
    vma20 = volume.groupby(level="instrument").rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    out["VSTD5"] = vstd5 / (vma5 + eps)
    out["VRATIO"] = vma5 / (vma20 + eps)
    out["VWAP_DEV"] = (close * volume).rolling(20, min_periods=10).sum() / (
        volume.rolling(20, min_periods=10).sum() + eps
    )
    out["VWAP_DEV"] = out["VWAP_DEV"].groupby(level="instrument").transform(
        lambda s: (close - s) / (s + eps)
    )

    # ── 横截面位置（日内相对位置，0=最低 1=最高）──
    # 按日聚合（每48根5min = 1天，这里用 date 做 groupby）
    day_key = g.index.get_level_values("datetime").normalize()
    day_high = high.groupby([g.index.get_level_values("instrument"), day_key]).transform("max")
    day_low = low.groupby([g.index.get_level_values("instrument"), day_key]).transform("min")
    out["CLOSE2HIGH"] = (close - day_low) / (day_high - day_low + eps)
    out["CLOSE2LOW"] = (day_high - close) / (day_high - day_low + eps)

    # ── 时间（分钟级日内周期）──
    dt = g.index.get_level_values("datetime")
    minutes_of_day = pd.Series(dt.hour * 60 + dt.minute, index=g.index)
    out["MIN_OF_DAY"] = ((minutes_of_day - 570) / (900 - 570 + eps)).clip(-1, 2)  # 9:30=570, 15:00=900

    # 替换 inf
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


# ═══════════════════════════════════════════════════════════════
# Label 构造（Future Return，严格无泄漏）
# ═══════════════════════════════════════════════════════════════

def compute_future_return_label(
    features_df: pd.DataFrame,
    horizon: int = 6,
) -> pd.Series:
    """计算未来 N 根 bar 的收益率（Future Return）。

    严格无泄漏：用 shift(-horizon)，当前 bar 的 label = close[t+horizon]/close[t] - 1。
    训练时用过去数据训练，预测时用当前特征预测未来，时间切分在 dataset.py 做。
    """
    close = features_df["close"]
    future_close = close.groupby(level="instrument").shift(-horizon)
    label = future_close / close - 1.0
    label.name = f"LABEL_RET_{horizon}"
    return label


# ═══════════════════════════════════════════════════════════════
# Qlib Dataset 构造（StaticDataLoader + DatasetH）
# ═══════════════════════════════════════════════════════════════

def build_qlib_dataset(
    codes: list[str],
    start_date: str = "2023-01-01",
    end_date: str = "2026-12-31",
    label_horizon: int = 6,
    train_end: str = "2025-06-30",
    valid_end: str = "2025-12-31",
    verbose: bool = False,
) -> dict:
    """端到端：JSON → DataFrame → 因子 → Label → Qlib DatasetH。

    返回:
        {
            "dataset": DatasetH（可传给 model.fit），
            "feature_names": list[str],
            "label_name": str,
            "n_samples": int,
            "segments": dict,
            "degraded": bool,  # True=降级模式
        }
    """
    if not is_qlib_available():
        return {"degraded": True, "error": "qlib 未安装"}

    from .loader import load_multi_stock_to_dataframe
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.data.dataset.loader import StaticDataLoader

    if verbose:
        print(f"[adapter] 加载 {len(codes)} 只股票 {start_date}~{end_date} ...")
    raw_df = load_multi_stock_to_dataframe(codes, start_date, end_date, verbose=verbose)
    if raw_df.empty:
        return {"degraded": True, "error": "无数据"}

    if verbose:
        print(f"[adapter] 原始 DataFrame: {raw_df.shape}, 计算 Alpha158 因子子集 ...")
    feature_df = compute_alpha158_subset(raw_df)
    label_s = compute_future_return_label(raw_df, horizon=label_horizon)

    # 合并为 Qlib 要求的 fields_group 结构
    # StaticDataLoader config: {"feature": df_feature, "label": df_label}
    # 要求 index 是 (datetime, instrument) — Qlib 内部 load 用 df.loc[:, instruments]
    feature_df_swapped = feature_df.swaplevel(0, 1, axis=0).sort_index()
    label_df = label_s.to_frame()
    label_df_swapped = label_df.swaplevel(0, 1, axis=0).sort_index()

    feature_names = list(feature_df_swapped.columns)
    label_name = label_df_swapped.columns[0]

    # 构造 DataHandlerLP（内部用 StaticDataLoader）
    # DataHandlerLP 的 init 支持 infer_processors / learn_processors
    handler_config = {
        "feature": feature_df_swapped,
        "label": label_df_swapped,
    }
    data_loader = StaticDataLoader(config=handler_config, join="inner")

    # 时间切分（严格按日期，杜绝泄漏）
    segments = {
        "train": (pd.Timestamp(start_date), pd.Timestamp(train_end)),
        "valid": (pd.Timestamp(train_end), pd.Timestamp(valid_end)),
        "test": (pd.Timestamp(valid_end), pd.Timestamp(end_date)),
    }

    handler = DataHandlerLP(
        data_loader=data_loader,
        infer_processors=[],
        learn_processors=[],
    )
    dataset = DatasetH(handler=handler, segments=segments)

    n_samples = len(feature_df_swapped)
    if verbose:
        print(f"[adapter] Dataset 构建完成: {n_samples} 样本, {len(feature_names)} 因子")
        print(f"[adapter] segments: train={segments['train']}, valid={segments['valid']}, test={segments['test']}")

    return {
        "dataset": dataset,
        "feature_names": feature_names,
        "label_name": label_name,
        "n_samples": n_samples,
        "segments": segments,
        "degraded": False,
    }


__all__ = [
    "compute_alpha158_subset",
    "compute_future_return_label",
    "build_qlib_dataset",
]
