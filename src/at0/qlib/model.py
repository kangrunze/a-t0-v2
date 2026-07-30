"""
Qlib Model 层 — LightGBM FutureReturn 预测 + 降级链
====================================================

降级链（V3 Part 5）：
  1. LightGBM 可用 → 训练 LGBMRanker/Regressor
  2. LightGBM 不可用 → 退化为 ATR×常数 的简单 ExpectedMove
  3. Qlib 不可用 → 调用方不应到达此模块（adapter 已降级）

严格无泄漏：训练只用 train segment，验证用 valid，测试用 test。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import is_lightgbm_available, is_qlib_available


@dataclass
class ModelResult:
    """模型训练 + 预测结果。"""
    model: object          # 训练好的模型（或 None 如果降级）
    predictions: pd.Series  # test segment 的 FutureReturn 预测
    ic: float              # test segment 的 Spearman IC
    rank_ic: float         # test segment 的 RankIC
    rmse: float            # test segment 的 RMSE
    degraded: bool         # True=降级模式
    feature_importance: Optional[dict] = None
    n_train: int = 0
    n_test: int = 0


def _spearman_ic(pred: np.ndarray, true: np.ndarray) -> float:
    """计算 Spearman IC（秩相关）。"""
    if len(pred) < 5:
        return float("nan")
    from scipy.stats import spearmanr  # noqa
    r, _ = spearmanr(pred, true)
    return float(r) if not np.isnan(r) else float("nan")


def _rmse(pred: np.ndarray, true: np.ndarray) -> float:
    if len(pred) == 0:
        return float("nan")
    return float(np.sqrt(np.mean((pred - true) ** 2)))


def train_lightgbm_model(
    dataset_dict: dict,
    params: Optional[dict] = None,
    verbose: bool = False,
) -> ModelResult:
    """用 LightGBM 训练 FutureReturn 预测模型。

    参数:
        dataset_dict: adapter.build_qlib_dataset 的返回值
        params: LightGBM 参数（None 用默认）
    """
    if not is_lightgbm_available():
        if verbose:
            print("[model] LightGBM 未安装，降级到常量模型")
        return _degraded_constant_model(dataset_dict, verbose=verbose)

    if dataset_dict.get("degraded"):
        return _degraded_constant_model(dataset_dict, verbose=verbose)

    import lightgbm as lgb

    dataset = dataset_dict["dataset"]
    feature_names = dataset_dict["feature_names"]
    label_name = dataset_dict["label_name"]

    # 准备 train/valid/test 数据
    train_data = dataset.prepare("train", col_set=["feature", "label"])
    valid_data = dataset.prepare("valid", col_set=["feature", "label"])
    test_data = dataset.prepare("test", col_set=["feature", "label"])

    if train_data.empty or test_data.empty:
        if verbose:
            print("[model] 数据为空，降级到常量模型")
        return _degraded_constant_model(dataset_dict, verbose=verbose)

    # 展平 MultiIndex 列（feature/label 双层）
    X_train = train_data["feature"].values
    y_train = train_data["label"].iloc[:, 0].values
    X_valid = valid_data["feature"].values
    y_valid = valid_data["label"].iloc[:, 0].values
    X_test = test_data["feature"].values
    y_test = test_data["label"].iloc[:, 0].values

    # 剔除 NaN（滚动因子初始段 + label 末尾段会有 NaN）
    train_mask = ~(np.isnan(X_train).any(axis=1) | np.isnan(y_train))
    valid_mask = ~(np.isnan(X_valid).any(axis=1) | np.isnan(y_valid))
    test_mask = ~(np.isnan(X_test).any(axis=1) | np.isnan(y_test))

    X_train, y_train = X_train[train_mask], y_train[train_mask]
    X_valid, y_valid = X_valid[valid_mask], y_valid[valid_mask]
    X_test, y_test = X_test[test_mask], y_test[test_mask]

    if len(X_train) < 100 or len(X_test) < 10:
        if verbose:
            print(f"[model] 样本不足 train={len(X_train)} test={len(X_test)}，降级")
        return _degraded_constant_model(dataset_dict, verbose=verbose)

    default_params = {
        "objective": "regression",
        "metric": "rmse",
        "max_depth": 6,
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
        "n_estimators": 200,
    }
    if params:
        default_params.update(params)

    if verbose:
        print(f"[model] 训练 LightGBM: train={len(X_train)}, valid={len(X_valid)}, test={len(X_test)}")

    model = lgb.LGBMRegressor(**default_params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_valid, y_valid)],
        callbacks=[lgb.log_evaluation(0)] if not verbose else [lgb.log_evaluation(50)],
    )

    pred_test = model.predict(X_test)
    ic = _spearman_ic(pred_test, y_test)
    rmse = _rmse(pred_test, y_test)

    # 特征重要性
    importance = dict(zip(feature_names, model.feature_importances_.tolist()))

    pred_series = pd.Series(
        pred_test,
        index=test_data["feature"].index[test_mask],
        name="pred_future_return",
    )

    if verbose:
        print(f"[model] 训练完成: IC={ic:.4f}, RMSE={rmse:.6f}")
        top5 = sorted(importance.items(), key=lambda x: -x[1])[:5]
        print(f"[model] Top5 重要因子: {top5}")

    return ModelResult(
        model=model,
        predictions=pred_series,
        ic=ic,
        rank_ic=ic,  # Spearman = RankIC
        rmse=rmse,
        degraded=False,
        feature_importance=importance,
        n_train=len(X_train),
        n_test=len(X_test),
    )


def _degraded_constant_model(
    dataset_dict: dict,
    verbose: bool = False,
) -> ModelResult:
    """降级模型：预测值为 0（无预测力），用于流程跑通验证。"""
    if verbose:
        print("[model] 使用降级常量模型（预测=0），仅用于流程验证")

    dataset = dataset_dict.get("dataset")
    if dataset is None:
        return ModelResult(
            model=None, predictions=pd.Series(dtype=float),
            ic=float("nan"), rank_ic=float("nan"), rmse=float("nan"),
            degraded=True, n_train=0, n_test=0,
        )

    try:
        test_data = dataset.prepare("test", col_set=["feature", "label"])
        y_test = test_data["label"].iloc[:, 0].values
        pred = np.zeros_like(y_test)
        pred_series = pd.Series(
            pred, index=test_data["feature"].index, name="pred_future_return"
        )
        return ModelResult(
            model=None, predictions=pred_series,
            ic=0.0, rank_ic=0.0, rmse=_rmse(pred, y_test),
            degraded=True, n_train=0, n_test=len(y_test),
        )
    except Exception as ex:
        if verbose:
            print(f"[model] 降级模型构造失败: {ex}")
        return ModelResult(
            model=None, predictions=pd.Series(dtype=float),
            ic=float("nan"), rank_ic=float("nan"), rmse=float("nan"),
            degraded=True, n_train=0, n_test=0,
        )


def train_model(
    dataset_dict: dict,
    params: Optional[dict] = None,
    verbose: bool = False,
) -> ModelResult:
    """统一入口：优先 LightGBM，自动降级。"""
    if not is_qlib_available():
        return _degraded_constant_model(dataset_dict, verbose=verbose)
    return train_lightgbm_model(dataset_dict, params=params, verbose=verbose)


__all__ = ["ModelResult", "train_lightgbm_model", "train_model"]
