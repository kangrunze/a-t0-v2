#!/usr/bin/env python3
"""
步骤4：解锁测量层 — IC/RankIC 分析

发现：src/at0/measurement/*.py.bak 是 TSD 加密，但 __pycache__/*.cpython-310.pyc
完好，__init__.py 已用 SourcelessFileLoader 加载。所以测量层函数
(compute_ic / compute_alpha_decay) 在标准 Python 下可直接 import 使用。
→ 测量层已"解锁"，无需重写。

本脚本：
1. 加载30股5min K线数据
2. 逐bar计算 compute_reference_snapshot（含 vwap_dev/rsi/kdj_k/adx/volume_ratio）
3. 计算各 horizon 的前瞻收益
4. 调用 measurement.compute_alpha_decay 计算每个因子的 RankIC / Alpha Decay
5. 输出报告，判断哪些因子有预测力（|IC均值| > 0.02 且 ICIR > 0.5）

输出: outputs/backtest/ic_analysis_step4.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from at0.features import compute_reference_snapshot
from at0.measurement import compute_alpha_decay

# 复用 backtest_zz500 的数据加载器
from backtest_zz500 import load_multi_day_zz500

BASELINE_CODES = [
    "300140", "603119", "000878", "300024", "000951",
    "002056", "600879", "300957", "002065", "601019",
    "603568", "603766", "002261", "301498", "688561",
    "000997", "601865", "300136", "300972", "600312",
    "688180", "600909", "600602", "603225", "000060",
    "688331", "600298", "600098", "600256", "301200",
]

START_DATE = "2026-04-27"
END_DATE = "2026-07-23"
DATA_DIR = Path(r"D:\project\data\zz500_5min")

# 因子名 → snapshot 字段名
FACTORS = {
    "vwap_dev":   "vwap_dev",
    "rsi":        "rsi",
    "kdj_k":      "kdj_k",
    "adx":        "adx",
    "vol_ratio":  "volume_ratio",
}

HORIZONS = [1, 3, 5, 10, 20]
WARMUP = 30          # 与 BacktestParams.warmup_bars 一致
BAR_STRIDE = 2       # 每隔2根bar采样一次，控制数据量


def collect_samples_for_stock(code: str) -> dict:
    """单股收集 (factor_scores, forward_returns_by_horizon) 样本。

    返回 {factor_name: {"scores": [...], "returns_by_horizon": {h: [...]}}}
    """
    daily_bars, daily_prev, _ = load_multi_day_zz500(
        code, START_DATE, END_DATE, DATA_DIR,
    )
    if not daily_bars:
        return {}

    # 按因子聚合样本
    factor_samples = {f: {"scores": [], "returns_by_horizon": {h: [] for h in HORIZONS}}
                      for f in FACTORS}

    for date_str, bars in daily_bars.items():
        n = len(bars)
        if n < WARMUP + max(HORIZONS) + 1:
            continue
        prev_close = daily_prev.get(date_str, 0)
        if prev_close <= 0:
            continue

        # 逐bar采样（带 stride 控制数据量）
        for i in range(WARMUP, n - max(HORIZONS), BAR_STRIDE):
            bars_up_to = bars[: i + 1]
            snap = compute_reference_snapshot(bars_up_to, prev_close=prev_close)
            if not snap:
                continue

            price = bars[i]["close"]
            # 前瞻收益（因果：用未来 h 根的 close）
            fwd_rets = {}
            for h in HORIZONS:
                if i + h < n:
                    fwd_rets[h] = (bars[i + h]["close"] - price) / price
                else:
                    fwd_rets[h] = None

            # 收集每个因子的样本（跳过 None）
            for fname, skey in FACTORS.items():
                val = snap.get(skey)
                if val is None or val != val:  # None or NaN
                    continue
                # 至少有一个 horizon 的前瞻收益有效
                valid_ret = False
                for h in HORIZONS:
                    r = fwd_rets[h]
                    if r is not None:
                        factor_samples[fname]["scores"].append(float(val))
                        factor_samples[fname]["returns_by_horizon"][h].append(float(r))
                        valid_ret = True
                if not valid_ret:
                    pass  # 没有任何前瞻收益，跳过
                # 注意：上面会把 score append 多次（每个 horizon 一次），需要修正
            # 修正：每bar每因子只 append 一次 score
            # （上面逻辑有bug，重写见下）

    # 上面的嵌套逻辑有重复 append 问题，这里重写干净的版本
    return _collect_clean(code, daily_bars, daily_prev)


def _collect_clean(code, daily_bars, daily_prev):
    """跨日拼接采样：5min bar 每天仅 48 根，单日 n=48 < WARMUP(30)+max(HORIZONS)(20)+1=51，
    若按日独立处理则全部被过滤且 range(30, 28) 为空。

    修复：对每个交易日 D，构建 bars_ext = bars[D-1] + bars[D] + bars[D+1]，
    前一天提供 indicator warmup（>=30 根），后一天保证 horizon=20 前瞻可达。
    仅在 today_bars 对应区间内采样，保证样本对应"当日"决策点。
    """
    factor_samples = {f: {"scores": [], "returns_by_horizon": {h: [] for h in HORIZONS}}
                      for f in FACTORS}

    dates_sorted = sorted(daily_bars.keys())
    n_days = len(dates_sorted)
    if n_days == 0:
        return factor_samples

    for idx, date_str in enumerate(dates_sorted):
        today_bars = daily_bars[date_str]
        if not today_bars:
            continue
        prev_close = daily_prev.get(date_str, 0)
        if prev_close <= 0:
            continue

        # 前一天（提供 warmup），后一天（提供前瞻可达性）
        prev_day_bars = daily_bars[dates_sorted[idx - 1]] if idx > 0 else []
        next_day_bars = daily_bars[dates_sorted[idx + 1]] if idx < n_days - 1 else []

        bars_ext = prev_day_bars + today_bars + next_day_bars
        n_ext = len(bars_ext)
        if n_ext < WARMUP + max(HORIZONS) + 1:
            continue

        # today 在 bars_ext 中的区间: [offset_prev, offset_prev + len(today_bars))
        offset_prev = len(prev_day_bars)
        offset_today_end = offset_prev + len(today_bars)

        # 采样范围：必须在 today 内，且 i >= WARMUP（保证 indicator warmup），
        # i + max(HORIZONS) < n_ext（保证所有 horizon 前瞻可达）
        i_start = max(WARMUP, offset_prev)
        i_end = min(offset_today_end, n_ext - max(HORIZONS))
        if i_end <= i_start:
            continue

        for i in range(i_start, i_end, BAR_STRIDE):
            bars_up_to = bars_ext[: i + 1]
            snap = compute_reference_snapshot(bars_up_to, prev_close=prev_close)
            if not snap:
                continue

            price = bars_ext[i]["close"]
            fwd_rets = {}
            for h in HORIZONS:
                if i + h < n_ext:
                    fwd_rets[h] = (bars_ext[i + h]["close"] - price) / price

            for fname, skey in FACTORS.items():
                val = snap.get(skey)
                if val is None or val != val:  # None or NaN
                    continue
                # 所有 horizon 都有效时才记录（保证 scores 和 returns 长度一致）
                if all(h in fwd_rets for h in HORIZONS):
                    factor_samples[fname]["scores"].append(float(val))
                    for h in HORIZONS:
                        factor_samples[fname]["returns_by_horizon"][h].append(fwd_rets[h])

    return factor_samples


def main() -> int:
    print("=" * 78)
    print("步骤4: IC/RankIC 分析（使用已解锁的 measurement 层）")
    print("=" * 78)
    print(f"股票数: {len(BASELINE_CODES)}")
    print(f"日期区间: {START_DATE} ~ {END_DATE}")
    print(f"因子: {list(FACTORS.keys())}")
    print(f"horizons: {HORIZONS}")
    print(f"warmup={WARMUP}, bar_stride={BAR_STRIDE}")
    print()

    if not DATA_DIR.exists():
        print(f"[ERROR] 数据目录不存在: {DATA_DIR}", file=sys.stderr)
        return 2

    # 1. 跨股聚合样本
    aggregated = {f: {"scores": [], "returns_by_horizon": {h: [] for h in HORIZONS}}
                  for f in FACTORS}

    for i, code in enumerate(BASELINE_CODES, 1):
        print(f"[{i}/{len(BASELINE_CODES)}] {code} 采集样本...", end=" ", flush=True)
        stock_samples = _collect_clean(
            code,
            *load_multi_day_zz500(code, START_DATE, END_DATE, DATA_DIR)[:2],
        )
        total = sum(len(v["scores"]) for v in stock_samples.values())
        print(f"{total} 样本")
        for f in FACTORS:
            if f not in stock_samples:
                continue
            aggregated[f]["scores"].extend(stock_samples[f]["scores"])
            for h in HORIZONS:
                aggregated[f]["returns_by_horizon"][h].extend(
                    stock_samples[f]["returns_by_horizon"][h]
                )

    # 2. 调用 compute_alpha_decay 计算每个因子的 IC
    print("\n" + "=" * 78)
    print("IC / Alpha Decay 分析结果")
    print("=" * 78)
    ic_reports = {}
    for fname in FACTORS:
        scores = aggregated[fname]["scores"]
        n = len(scores)
        if n < 50:
            print(f"\n[{fname}] 样本不足 ({n}), 跳过")
            continue
        report = compute_alpha_decay(
            scores=scores,
            returns_by_horizon=aggregated[fname]["returns_by_horizon"],
            signal_name=fname,
            horizons=HORIZONS,
        )
        ic_reports[fname] = report.to_dict() if hasattr(report, "to_dict") else report
        print(f"\n[{fname}] 样本数={n}")
        # 尝试打印关键字段
        rd = report.to_dict() if hasattr(report, "to_dict") else report
        if isinstance(rd, dict):
            for k, v in rd.items():
                if isinstance(v, dict):
                    print(f"  {k}:")
                    for kk, vv in v.items():
                        print(f"    {kk}: {vv}")
                elif isinstance(v, list):
                    print(f"  {k}: {v}")
                else:
                    print(f"  {k}: {v}")

    # 3. 保存报告
    out_path = PROJECT_ROOT / "outputs" / "backtest" / "ic_analysis_step4.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "step": "step4_ic_analysis",
            "description": "RankIC of vwap_dev/rsi/kdj_k/adx/vol_ratio via measurement.compute_alpha_decay",
            "stocks": len(BASELINE_CODES),
            "date_range": [START_DATE, END_DATE],
            "factors": list(FACTORS.keys()),
            "horizons": HORIZONS,
            "sample_counts": {f: len(aggregated[f]["scores"]) for f in FACTORS},
            "ic_reports": ic_reports,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
