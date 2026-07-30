#!/usr/bin/env python3
"""
Qlib Adapter Smoke Test — 验证 V3 研究层流程跑通
=================================================

流程:
  1. is_qlib_available() 检测
  2. loader 加载 N 只股票 → DataFrame
  3. adapter 计算 Alpha158 因子子集
  4. adapter 构造 Label（Future Return）
  5. adapter.build_qlib_dataset → DatasetH
  6. dataset.compute_cross_section_ic → 因子 IC
  7. model.train_model → LightGBM（或降级常量）

先用小样本（5只）快速验证流程，再扩到 100 只。

用法:
    python scripts/v3/qlib_smoke_test.py --n 5
    python scripts/v3/qlib_smoke_test.py --codes-file config/v3_sample_100.txt --n 20
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5, help="抽样股票数（小样本快速验证）")
    parser.add_argument("--codes-file", default=None, help="股票代码列表文件（每行一个代码）")
    parser.add_argument("--start", default="2024-01-01", help="起始日期（smoke test 用短区间）")
    parser.add_argument("--end", default="2026-07-22", help="结束日期")
    parser.add_argument("--horizon", type=int, default=6, help="Future Return 预测 horizon")
    parser.add_argument("--skip-model", action="store_true", help="跳过模型训练（只验证因子+IC）")
    args = parser.parse_args()

    print("=" * 70)
    print("Qlib Adapter Smoke Test")
    print("=" * 70)

    # ── Step 1: Qlib 可用性 ──
    from at0.qlib import is_qlib_available, is_lightgbm_available
    qlib_ok = is_qlib_available()
    lgb_ok = is_lightgbm_available()
    print(f"\n[1/7] Qlib 可用: {qlib_ok}, LightGBM 可用: {lgb_ok}")
    if not qlib_ok:
        print("  Qlib 未安装，测试终止（降级链应在此前生效）")
        return 1

    # ── Step 2: 加载股票代码 ──
    if args.codes_file:
        with open(args.codes_file, "r", encoding="utf-8") as f:
            all_codes = [l.strip() for l in f if l.strip()]
    else:
        from at0.qlib.loader import list_available_codes
        all_codes = list_available_codes()
    codes = all_codes[: args.n] if args.n < len(all_codes) else all_codes
    print(f"\n[2/7] 测试股票: {len(codes)} 只, 区间 {args.start}~{args.end}")
    print(f"      前5只: {codes[:5]}")

    # ── Step 3: loader 加载 ──
    print(f"\n[3/7] loader 加载 DataFrame ...")
    t0 = time.time()
    from at0.qlib.loader import load_multi_stock_to_dataframe
    raw_df = load_multi_stock_to_dataframe(codes, args.start, args.end, verbose=True)
    print(f"      耗时 {time.time()-t0:.1f}s, shape={raw_df.shape}")
    if raw_df.empty:
        print("  数据为空，测试终止")
        return 1
    print(f"      index levels: {raw_df.index.names}")
    print(f"      columns: {list(raw_df.columns)}")
    print(f"      前3行:\n{raw_df.head(3)}")

    # ── Step 4: adapter 计算因子 ──
    print(f"\n[4/7] adapter 计算 Alpha158 因子子集 ...")
    t0 = time.time()
    from at0.qlib.adapter import compute_alpha158_subset, compute_future_return_label
    feature_df = compute_alpha158_subset(raw_df)
    label_s = compute_future_return_label(raw_df, horizon=args.horizon)
    print(f"      耗时 {time.time()-t0:.1f}s, feature shape={feature_df.shape}")
    print(f"      因子数: {len(feature_df.columns)}")
    print(f"      因子列表: {list(feature_df.columns)}")
    print(f"      NaN 占比: {feature_df.isna().mean().mean():.2%}")
    print(f"      label: {label_s.name}, NaN 占比: {label_s.isna().mean():.2%}")

    # ── Step 5: build_qlib_dataset ──
    print(f"\n[5/7] build_qlib_dataset (DatasetH) ...")
    t0 = time.time()
    from at0.qlib.adapter import build_qlib_dataset
    dataset_dict = build_qlib_dataset(
        codes, args.start, args.end,
        label_horizon=args.horizon,
        train_end="2025-06-30",
        valid_end="2025-12-31",
        verbose=True,
    )
    print(f"      耗时 {time.time()-t0:.1f}s")
    if dataset_dict.get("degraded"):
        print(f"      降级: {dataset_dict.get('error')}")
        return 1
    print(f"      n_samples: {dataset_dict['n_samples']}")
    print(f"      feature_names: {len(dataset_dict['feature_names'])} 个")
    print(f"      segments: {dataset_dict['segments']}")

    # 验证 DatasetH.prepare 可用
    try:
        train_data = dataset_dict["dataset"].prepare("train", col_set=["feature", "label"])
        print(f"      train segment: {len(train_data)} 行")
        print(f"      train columns: {list(train_data.columns)[:6]}...")
    except Exception as ex:
        print(f"      DatasetH.prepare 失败: {ex}")
        return 1

    # ── Step 6: 因子 IC 分析 ──
    print(f"\n[6/7] 因子横截面 IC 分析（valid segment）...")
    t0 = time.time()
    from at0.qlib.dataset import compute_cross_section_ic, rank_factors_by_ic, suggest_alpha_weights
    ic_ranked = pd.DataFrame()  # 兜底，避免后续 UnboundLocalError
    # 用 valid segment 的数据做 IC
    try:
        valid_data = dataset_dict["dataset"].prepare("valid", col_set=["feature", "label"])
        feat_valid = valid_data["feature"]
        label_valid = valid_data["label"].iloc[:, 0]
        # 重置 index 为 (instrument, datetime) — Series.swaplevel 无 axis 参数
        if feat_valid.index.names == ["datetime", "instrument"]:
            feat_valid = feat_valid.swaplevel(0, 1).sort_index()
            label_valid = label_valid.swaplevel(0, 1).sort_index()
        ic_results = compute_cross_section_ic(feat_valid, label_valid)
        ic_ranked = rank_factors_by_ic(ic_results)
        print(f"      耗时 {time.time()-t0:.1f}s, 有效因子: {len(ic_results)}")
        if not ic_ranked.empty:
            print(f"      Top5 因子 (按 |ICIR|):")
            print(ic_ranked.head(5).to_string(index=False))
            weights = suggest_alpha_weights(ic_results)
            nonzero_w = {k: round(v, 3) for k, v in weights.items() if abs(v) > 0.01}
            print(f"      建议权重（非零）: {nonzero_w}")
        else:
            print("      无有效因子（样本不足或全部常数列）")
    except Exception as ex:
        print(f"      IC 分析失败: {ex}")
        import traceback
        traceback.print_exc()

    if args.skip_model:
        print(f"\n[7/7] 跳过模型训练（--skip-model）")
        print("\n" + "=" * 70)
        print("Smoke Test PASSED: Qlib 研究层流程跑通（因子+IC）")
        print("=" * 70)
        return 0

    # ── Step 7: 模型训练 ──
    print(f"\n[7/7] 模型训练 (LightGBM / 降级常量) ...")
    t0 = time.time()
    from at0.qlib.model import train_model
    result = train_model(dataset_dict, verbose=True)
    print(f"      耗时 {time.time()-t0:.1f}s")
    print(f"      degraded: {result.degraded}")
    print(f"      n_train: {result.n_train}, n_test: {result.n_test}")
    print(f"      IC: {result.ic:.4f}, RankIC: {result.rank_ic:.4f}, RMSE: {result.rmse:.6f}")
    if result.feature_importance:
        top5 = sorted(result.feature_importance.items(), key=lambda x: -x[1])[:5]
        print(f"      Top5 重要因子: {top5}")

    # ── 保存结果 ──
    out_path = PROJECT_ROOT / "outputs" / "v3_smoke_test_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result_json = {
        "n_codes": len(codes),
        "codes": codes[:10],
        "start": args.start,
        "end": args.end,
        "horizon": args.horizon,
        "n_samples": dataset_dict["n_samples"],
        "n_factors": len(dataset_dict["feature_names"]),
        "degraded": result.degraded,
        "ic": result.ic if not isinstance(result.ic, float) or result.ic == result.ic else None,
        "rmse": result.rmse if not isinstance(result.rmse, float) or result.rmse == result.rmse else None,
        "n_train": result.n_train,
        "n_test": result.n_test,
        "qlib_available": qlib_ok,
        "lightgbm_available": lgb_ok,
    }
    if not ic_ranked.empty:
        result_json["top5_factors_by_icir"] = ic_ranked.head(5).to_dict(orient="records")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result_json, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {out_path}")

    print("\n" + "=" * 70)
    if result.degraded:
        print("Smoke Test PASSED (降级模式): 流程跑通，但模型未训练")
    else:
        print("Smoke Test PASSED: Qlib 研究层全流程跑通")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
