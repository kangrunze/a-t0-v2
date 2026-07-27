#!/usr/bin/env python3
"""
步骤3（独立诊断）：daily_adx 与当天 5min 做T净盈亏的相关性
==========================================================
不依赖 IC 框架，只用最简单的分组对比 + spearman 相关系数。

方法：
  1. 对每只股票每个交易日，算"截至昨日的日线 ADX"（与 regime 用同一个 daily_adx）
  2. 跑 baseline 回测拿到当天该股票的 5min 做T净盈亏（cost_reduction - cost_paid）
  3. 汇总所有 (daily_adx, 当天净盈亏) 样本
  4. 按 daily_adx 五分组对比平均盈亏
  5. 算 spearman 相关系数

输出：相关性方向和强度，不与步骤2结论混在一起。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from dataclasses import replace as _replace
from at0.backtest import backtest_multi_day
from at0.config import load_signal_params, load_risk_params, load_backtest_params
from at0.data import normalize_code
from at0.features import dmi as _dmi
from at0.regime import synthesize_daily_klines, RegimeParams
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


def build_params(base_shares: int = 3000):
    sp = load_signal_params()
    rp = load_risk_params()
    return _replace(
        load_backtest_params(),
        base_shares=base_shares,
        avg_cost=10.0,
        signal_params=sp,
        risk_params=rp,
    )


def compute_daily_adx_series(daily_bars: dict[str, list[dict]]) -> dict[str, float]:
    """对每个交易日 D，用截至 D-1 的日K算 daily_adx（与 regime 完全一致的因果口径）。

    返回 {date_str: daily_adx}（ADX 算不出的日期跳过）。
    """
    p = RegimeParams()
    daily_klines = synthesize_daily_klines(daily_bars)
    sorted_klines = sorted(daily_klines, key=lambda x: x.get("date", ""))
    adx_by_date: dict[str, float] = {}
    for k in sorted_klines:
        d = k["date"]
        up_to_yesterday = [x for x in sorted_klines if x["date"] < d]
        if len(up_to_yesterday) < p.min_daily_klines:
            continue
        # 异常过滤（与 classify_daily_regime 一致）
        last = up_to_yesterday[-1]
        if (last.get("close", 0) <= 0 or last.get("volume", 0) <= 0
                or last.get("high", 0) == last.get("low", 0)):
            continue
        _, _, adx = _dmi(up_to_yesterday, period=p.adx_period)
        if adx is not None:
            adx_by_date[d] = float(adx)
    return adx_by_date


def extract_daily_pnl(result: dict) -> dict[str, float]:
    """从 backtest_multi_day 结果提取 {date: 当天净盈亏}。

    当天净盈亏 = cost_reduction（已实现配对盈亏） - total_cost_paid（成本）
    未配对敞口浮盈浮亏不计入（与步骤2 baseline 口径一致）。
    """
    pnl_by_date: dict[str, float] = {}
    for daily in result.get("daily_results", []):
        d = daily.get("date", "")
        cr = daily.get("cost_reduction", 0.0)
        cp = daily.get("total_cost_paid", 0.0)
        pnl_by_date[d] = round(cr - cp, 4)
    return pnl_by_date


def spearman_corr(xs: list[float], ys: list[float]) -> float:
    """简单 spearman 相关系数（不依赖 scipy）。"""
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    # 排名（平均排名处理并列）
    def rank(vals):
        idx_sorted = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[idx_sorted[j+1]] == vals[idx_sorted[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-based
            for k in range(i, j+1):
                ranks[idx_sorted[k]] = avg_rank
            i = j + 1
        return ranks
    rx = rank(xs)
    ry = rank(ys)
    n = len(xs)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = (sum((rx[i] - mean_rx) ** 2 for i in range(n))) ** 0.5
    den_y = (sum((ry[i] - mean_ry) ** 2 for i in range(n))) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def main() -> int:
    print("=" * 78)
    print("步骤3（独立诊断）：daily_adx 与当天 5min 做T净盈亏的相关性")
    print("=" * 78)
    print(f"股票数: {len(BASELINE_CODES)}")
    print(f"日期区间: {START_DATE} ~ {END_DATE}")
    print(f"方法: spearman 相关系数 + daily_adx 五分组对比平均盈亏")
    print()

    params = build_params()

    # 收集所有 (daily_adx, daily_pnl) 样本
    samples: list[tuple[float, float]] = []
    per_stock_summary: list[dict] = []

    for i, code in enumerate(BASELINE_CODES, 1):
        print(f"[{i}/{len(BASELINE_CODES)}] {code}", end=" ", flush=True)
        daily_bars, daily_prev_closes, _ = load_multi_day_zz500(
            code, START_DATE, END_DATE, DATA_DIR,
        )
        if not daily_bars:
            print("无数据")
            continue

        first_date = min(daily_prev_closes.keys())
        p = _replace(params, avg_cost=daily_prev_closes[first_date])

        # baseline 回测（regime_filter_enabled=False）
        result = backtest_multi_day(
            code=normalize_code(code)["pure"],
            daily_bars=daily_bars,
            daily_prev_closes=daily_prev_closes,
            params=p,
            regime_filter_enabled=False,
        )

        # 算每日 daily_adx
        adx_by_date = compute_daily_adx_series(daily_bars)
        # 提取每日净盈亏
        pnl_by_date = extract_daily_pnl(result)

        # 配对：同一天有 adx 和 pnl
        stock_samples = []
        for d, adx in adx_by_date.items():
            if d in pnl_by_date:
                samples.append((adx, pnl_by_date[d]))
                stock_samples.append({"date": d, "daily_adx": round(adx, 2), "daily_pnl": pnl_by_date[d]})

        # 单股 spearman
        if len(stock_samples) >= 5:
            xs = [s["daily_adx"] for s in stock_samples]
            ys = [s["daily_pnl"] for s in stock_samples]
            rho = spearman_corr(xs, ys)
        else:
            rho = None

        n = len(stock_samples)
        profitable_days = sum(1 for s in stock_samples if s["daily_pnl"] > 0)
        print(f"样本={n} 盈利日={profitable_days} spearman={rho:+.4f}" if rho is not None
              else f"样本={n}（不足5个，跳过spearman）")

        per_stock_summary.append({
            "code": code,
            "sample_count": n,
            "profitable_days": profitable_days,
            "spearman": round(rho, 4) if rho is not None else None,
        })

    # ── 全样本统计 ──
    print("\n" + "=" * 78)
    print("全样本统计")
    print("=" * 78)
    print(f"总样本数: {len(samples)}")

    if not samples:
        print("无样本，退出")
        return 1

    xs_all = [s[0] for s in samples]
    ys_all = [s[1] for s in samples]
    rho_all = spearman_corr(xs_all, ys_all)
    print(f"spearman 相关系数 (daily_adx vs 当天净盈亏): {rho_all:+.4f}")

    # 五分组对比
    print("\n按 daily_adx 五分组对比平均盈亏:")
    sorted_samples = sorted(samples, key=lambda x: x[0])
    n = len(sorted_samples)
    quintile_size = n // 5
    print(f"{'分组':<8} {'daily_adx 范围':<24} {'样本数':>8} {'平均盈亏':>12} {'盈利日占比':>12}")
    print("-" * 72)
    for qi in range(5):
        start = qi * quintile_size
        end = (qi + 1) * quintile_size if qi < 4 else n
        group = sorted_samples[start:end]
        if not group:
            continue
        adx_min = min(g[0] for g in group)
        adx_max = max(g[0] for g in group)
        avg_pnl = sum(g[1] for g in group) / len(group)
        win_days = sum(1 for g in group if g[1] > 0)
        win_ratio = win_days / len(group)
        print(f"Q{qi+1}      [{adx_min:6.2f}, {adx_max:6.2f}]     {len(group):>6}   {avg_pnl:>+10.2f}   {win_ratio:>10.2%}")

    # 单股 spearman 分布
    rhos = [s["spearman"] for s in per_stock_summary if s["spearman"] is not None]
    if rhos:
        pos = sum(1 for r in rhos if r > 0)
        neg = sum(1 for r in rhos if r < 0)
        avg_rho = sum(rhos) / len(rhos)
        print(f"\n单股 spearman 分布:")
        print(f"  正相关股票数: {pos} / {len(rhos)}")
        print(f"  负相关股票数: {neg} / {len(rhos)}")
        print(f"  平均 spearman: {avg_rho:+.4f}")

    # 保存报告
    out_path = PROJECT_ROOT / "outputs" / "backtest" / "stepE3_daily_adx_correlation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "step": "stepE3_daily_adx_correlation",
            "description": "独立诊断：daily_adx 与当天5min做T净盈亏的相关性",
            "stocks": len(BASELINE_CODES),
            "date_range": [START_DATE, END_DATE],
            "total_samples": len(samples),
            "spearman_all": round(rho_all, 4),
            "quintile_breakdown": [
                {
                    "quintile": qi + 1,
                    "adx_range": [min(g[0] for g in sorted_samples[qi*quintile_size:(qi+1)*quintile_size if qi < 4 else n]),
                                  max(g[0] for g in sorted_samples[qi*quintile_size:(qi+1)*quintile_size if qi < 4 else n])],
                    "sample_count": len(sorted_samples[qi*quintile_size:(qi+1)*quintile_size if qi < 4 else n]),
                    "avg_pnl": round(sum(g[1] for g in sorted_samples[qi*quintile_size:(qi+1)*quintile_size if qi < 4 else n]) / max(1, len(sorted_samples[qi*quintile_size:(qi+1)*quintile_size if qi < 4 else n])), 4),
                }
                for qi in range(5)
            ],
            "per_stock": per_stock_summary,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
