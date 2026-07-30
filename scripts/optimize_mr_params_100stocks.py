#!/usr/bin/env python3
"""
均值回归(MR)参数调优 — 100股 train50/valid50 网格搜索

样本: 与 K_mr_fix_100 对齐（seed=42 抽样100只，前50训练 + 后50验证）
区间: 3年（2023-07-25 ~ 2026-07-22）
网格: z_threshold × rev_ratio × sl
  - z:        [0.5, 1.0, 1.5, 2.0]   （触发阈值，越小越敏感）
  - rev_ratio:[0.3, 0.5, 0.7]        （回归目标比例，越小越紧）
  - sl:       [0.001, 0.0015, 0.002] （止损比例）

流程:
  1. 训练集50只跑全网格(4×3×3=36组)，按 net_pnl 选最优
  2. 验证集50只用最优参数复核（OOS稳定性检查）
  3. 输出: outputs/oos_validation/mr_optimize_100stocks.json

指标: net_pnl / win_rate / payoff / paired_trades / CE
"""
from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import replace as _replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "archive" / "diag"))

from at0.paths import ZZ500_5MIN_DIR
from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock
from at0.config import load_signal_params, load_risk_params, load_backtest_params
from at0.cli import adapt_params_by_frequency
from diag_stepJ1_capture_efficiency import (
    load_zz500_data,
    compute_daily_hl,
    compute_capture_efficiency,
)

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════
START_DATE = "2023-07-25"
END_DATE = "2026-07-22"
SEED = 42
SAMPLE_N = 100
TRAIN_N = 50
BASE_SHARES = 3000

# 粗筛阶段用少量股票快速过滤（避免36组×50只×3年≈6小时）
COARSE_SAMPLE_N = 10  # 粗筛用10只（seed=42从train50中取前10）
COARSE_TOP_K = 6      # 粗筛后取top6进入精验

OUTPUT_DIR = PROJECT_ROOT / "outputs" / "oos_validation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUTPUT_DIR / "mr_optimize_100stocks.json"

# 网格
Z_GRID = [0.5, 1.0, 1.5, 2.0]
REV_GRID = [0.3, 0.5, 0.7]
SL_GRID = [0.001, 0.0015, 0.002]


def select_stocks() -> tuple[list[str], list[str]]:
    """seed=42抽样100只，前50训练 + 后50验证。"""
    all_files = sorted(ZZ500_5MIN_DIR.glob("*.json"))
    all_codes = [f.stem for f in all_files if not f.stem.startswith("_")]
    random.seed(SEED)
    sample = sorted(random.sample(all_codes, SAMPLE_N))
    return sample[:TRAIN_N], sample[TRAIN_N:]


def build_params(z: float, rev_ratio: float, sl: float) -> BacktestParams:
    """构建MR模式回测参数（覆盖z/rev_ratio/sl，其他用yaml默认）。"""
    sp_base = load_signal_params()
    rp_base = load_risk_params()
    bp_base = load_backtest_params()

    sp = _replace(sp_base,
                  strategy_mode="mean_reversion",
                  mr_z_threshold=z,
                  mr_take_profit_reversion_ratio=rev_ratio)
    # MR止损用覆盖值，其他风控用yaml默认
    params = BacktestParams(
        base_shares=BASE_SHARES,
        avg_cost=0.0,
        signal_params=sp,
        risk_params=rp_base,
        stop_loss_ratio=bp_base.stop_loss_ratio,
        trailing_ratio=bp_base.trailing_ratio,
        trailing_activation_pct=bp_base.trailing_activation_pct,
        max_holding_bars=bp_base.max_holding_bars,
        cooldown_bars=bp_base.cooldown_bars,
        mr_stop_loss_ratio=sl,
        mr_max_holding_bars=bp_base.mr_max_holding_bars,
        mr_cooldown_bars=bp_base.mr_cooldown_bars,
    )
    if bp_base.mr_max_t_size_ratio is not None:
        params = _replace(params, risk_params=_replace(rp_base, max_t_size_ratio=bp_base.mr_max_t_size_ratio))
    return params


def run_single(code: str, params: BacktestParams) -> dict | None:
    """单股回测，返回 summary + CE。"""
    data = load_zz500_data(code, START_DATE, END_DATE)
    if not data:
        return None
    daily_bars, daily_prev_closes = data
    if not daily_bars:
        return None

    first_date = min(daily_prev_closes.keys())
    avg_cost = daily_prev_closes[first_date]
    if avg_cost <= 0:
        return None

    params = _replace(params, avg_cost=avg_cost)
    params = adapt_params_by_frequency(params, "5min", 48)
    if params.exposure_policy is not None and params.is_mean_reversion:
        params.exposure_policy.max_holding_bars = params.effective_max_holding_bars

    result = backtest_multi_day(
        code=code,
        daily_bars=daily_bars,
        daily_prev_closes=daily_prev_closes,
        params=params,
    )

    daily_hl = compute_daily_hl(daily_bars)
    ce_stats = compute_capture_efficiency(result, daily_hl)
    summary = summarize_one_stock(code, result)
    return {"code": code, "summary": summary, "ce_stats": ce_stats}


def run_group(codes: list[str], params: BacktestParams, tag: str, verbose: bool = True) -> dict:
    """跑一组回测，返回汇总。"""
    per_stock = []
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        try:
            res = run_single(code, params)
            if not res:
                continue
            s = res["summary"]
            ce = res["ce_stats"]
            per_stock.append({
                "code": code,
                "paired_trades": s.get("paired_trades", 0),
                "win_rate": s.get("win_rate", 0),
                "payoff_ratio": s.get("payoff_ratio", 0),
                "net_pnl": s.get("net_pnl", 0),
                "avg_win": s.get("avg_win", 0),
                "avg_loss": s.get("avg_loss", 0),
                "ce_mean": ce["all_stats"]["mean"],
                "ce_total_pairs": ce["total_pairs"],
            })
            if verbose:
                print(f"    [{i}/{len(codes)}] {code}: 配对={s.get('paired_trades',0):3d} "
                      f"净={s.get('net_pnl',0):+10.0f} CE={ce['all_stats']['mean']*100:5.2f}%")
        except Exception as e:
            if verbose:
                print(f"    [{i}/{len(codes)}] {code}: ERROR - {e}")

    elapsed = time.time() - t0
    total_paired = sum(s["paired_trades"] for s in per_stock)
    total_net = sum(s["net_pnl"] for s in per_stock)
    avg_win = sum(s["avg_win"] * s["paired_trades"] for s in per_stock if s["paired_trades"]) / max(total_paired, 1)
    avg_loss = sum(s["avg_loss"] * s["paired_trades"] for s in per_stock if s["paired_trades"]) / max(total_paired, 1)
    wins = sum(int(s["win_rate"] * s["paired_trades"]) for s in per_stock if s["paired_trades"])
    win_rate = wins / total_paired if total_paired else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    ce_weighted = sum(s["ce_mean"] * s["ce_total_pairs"] for s in per_stock) / max(
        sum(s["ce_total_pairs"] for s in per_stock), 1)
    profitable = sum(1 for s in per_stock if s["net_pnl"] > 0)

    return {
        "tag": tag,
        "stocks": len(per_stock),
        "paired_trades": total_paired,
        "win_rate": round(win_rate, 4),
        "payoff_ratio": round(payoff, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "net_pnl": round(total_net, 2),
        "ce_mean": round(ce_weighted, 4),
        "profitable_stocks": profitable,
        "elapsed_seconds": round(elapsed, 1),
        "per_stock": per_stock,
    }


def main():
    print("=" * 82)
    print("均值回归(MR)参数调优 — 100股 train50/valid50 网格搜索")
    print("=" * 82)
    print(f"区间: {START_DATE} ~ {END_DATE}（3年）")
    print(f"网格: z{Z_GRID} × rev{REV_GRID} × sl{SL_GRID} = {len(Z_GRID)*len(REV_GRID)*len(SL_GRID)}组")

    train_codes, valid_codes = select_stocks()
    print(f"训练集: {len(train_codes)}只")
    print(f"验证集: {len(valid_codes)}只")

    # ── 阶段1: 粗筛（10只股票×36组，快速过滤） ──
    print(f"\n{'=' * 82}")
    print(f"阶段1: 粗筛（{COARSE_SAMPLE_N}只股票×36组参数）")
    print(f"{'=' * 82}")
    coarse_codes = train_codes[:COARSE_SAMPLE_N]
    print(f"粗筛股票: {coarse_codes}")

    coarse_results = []
    t_start = time.time()
    total_combos = len(Z_GRID) * len(REV_GRID) * len(SL_GRID)
    combo_idx = 0

    for z in Z_GRID:
        for rev in REV_GRID:
            for sl in SL_GRID:
                combo_idx += 1
                t0 = time.time()
                params = build_params(z, rev, sl)
                result = run_group(coarse_codes, params, f"coarse_z{z}_r{rev}_sl{sl}", verbose=False)
                result["z"] = z
                result["rev_ratio"] = rev
                result["sl"] = sl
                coarse_results.append(result)
                elapsed = time.time() - t0
                print(f"  [{combo_idx}/{total_combos}] z={z} rev={rev} sl={sl}: "
                      f"配对{result['paired_trades']:5d} 胜率{result['win_rate']*100:5.1f}% "
                      f"payoff={result['payoff_ratio']:.2f} 净{result['net_pnl']:+10.0f} "
                      f"CE={result['ce_mean']*100:5.2f}% ({elapsed:.0f}s)")

    # 按 net_pnl 排序取 top K
    coarse_results.sort(key=lambda x: x["net_pnl"], reverse=True)
    top_candidates = coarse_results[:COARSE_TOP_K]
    coarse_elapsed = time.time() - t_start

    print(f"\n  粗筛完成（{coarse_elapsed/60:.1f}分钟）")
    print(f"  Top{COARSE_TOP_K} by net_pnl（进入精验）:")
    for i, r in enumerate(top_candidates, 1):
        print(f"    {i}. z={r['z']} rev={r['rev_ratio']} sl={r['sl']}: "
              f"净{r['net_pnl']:+.0f} payoff={r['payoff_ratio']:.2f} "
              f"胜率{r['win_rate']*100:.1f}% CE={r['ce_mean']*100:.2f}%")

    # ── 阶段2: 精验（train50只×top候选参数） ──
    print(f"\n{'=' * 82}")
    print(f"阶段2: 精验（训练集50只×{len(top_candidates)}组候选参数）")
    print(f"{'=' * 82}")

    fine_results = []
    t_fine = time.time()
    for i, cand in enumerate(top_candidates, 1):
        t0 = time.time()
        params = build_params(cand["z"], cand["rev_ratio"], cand["sl"])
        result = run_group(train_codes, params, f"fine_z{cand['z']}_r{cand['rev_ratio']}_sl{cand['sl']}", verbose=False)
        result["z"] = cand["z"]
        result["rev_ratio"] = cand["rev_ratio"]
        result["sl"] = cand["sl"]
        fine_results.append(result)
        elapsed = time.time() - t0
        print(f"  [{i}/{len(top_candidates)}] z={cand['z']} rev={cand['rev_ratio']} sl={cand['sl']}: "
              f"配对{result['paired_trades']:5d} 胜率{result['win_rate']*100:5.1f}% "
              f"payoff={result['payoff_ratio']:.2f} 净{result['net_pnl']:+10.0f} "
              f"CE={result['ce_mean']*100:5.2f}% 盈{result['profitable_stocks']}/50 ({elapsed:.0f}s)")

    fine_results.sort(key=lambda x: x["net_pnl"], reverse=True)
    best = fine_results[0]
    fine_elapsed = time.time() - t_fine

    print(f"\n  精验完成（{fine_elapsed/60:.1f}分钟）")
    print(f"  训练集排名:")
    for i, r in enumerate(fine_results, 1):
        marker = " ←最优" if i == 1 else ""
        print(f"    {i}. z={r['z']} rev={r['rev_ratio']} sl={r['sl']}: "
              f"净{r['net_pnl']:+.0f} payoff={r['payoff_ratio']:.2f} CE={r['ce_mean']*100:.2f}%{marker}")

    # ── 阶段3: 验证集复核（最优参数） ──
    print(f"\n{'=' * 82}")
    print(f"阶段3: 验证集50只复核（最优参数 z={best['z']} rev={best['rev_ratio']} sl={best['sl']}）")
    print(f"{'=' * 82}")

    params = build_params(best["z"], best["rev_ratio"], best["sl"])
    valid_result = run_group(valid_codes, params, "valid_best", verbose=True)
    print(f"\n  验证集: 配对{valid_result['paired_trades']} 胜率{valid_result['win_rate']*100:.1f}% "
          f"payoff={valid_result['payoff_ratio']:.2f} 净{valid_result['net_pnl']:+.0f} "
          f"CE={valid_result['ce_mean']*100:.2f}% 盈{valid_result['profitable_stocks']}/50")

    # ── 阶段4: 对比基线（yaml默认 z=1.0 rev=0.3 sl=0.0015） ──
    print(f"\n{'=' * 82}")
    print("阶段4: 对比基线（yaml默认 z=1.0 rev=0.3 sl=0.0015）")
    print(f"{'=' * 82}")
    baseline_valid_params = build_params(1.0, 0.3, 0.0015)
    baseline_valid = run_group(valid_codes, baseline_valid_params, "valid_baseline", verbose=False)
    print(f"  基线(验证集): 净{baseline_valid['net_pnl']:+.0f} "
          f"payoff={baseline_valid['payoff_ratio']:.2f} CE={baseline_valid['ce_mean']*100:.2f}% "
          f"盈{baseline_valid['profitable_stocks']}/50")

    print(f"\n  最优 vs 基线（验证集）:")
    print(f"    net_pnl:  最优{valid_result['net_pnl']:+.0f} vs 基线{baseline_valid['net_pnl']:+.0f} "
          f"(差{valid_result['net_pnl']-baseline_valid['net_pnl']:+.0f})")
    print(f"    payoff:   最优{valid_result['payoff_ratio']:.4f} vs 基线{baseline_valid['payoff_ratio']:.4f} "
          f"(差{valid_result['payoff_ratio']-baseline_valid['payoff_ratio']:+.4f})")
    print(f"    CE:       最优{valid_result['ce_mean']*100:.2f}% vs 基线{baseline_valid['ce_mean']*100:.2f}% "
          f"(差{(valid_result['ce_mean']-baseline_valid['ce_mean'])*100:+.2f}pp)")

    # ── 保存 ──
    total_elapsed = time.time() - t_start
    out = {
        "step": "mr_optimize_100stocks",
        "config": {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "seed": SEED,
            "sample_n": SAMPLE_N,
            "train_n": TRAIN_N,
            "valid_n": SAMPLE_N - TRAIN_N,
            "grid": {"z": Z_GRID, "rev_ratio": REV_GRID, "sl": SL_GRID},
            "coarse_sample_n": COARSE_SAMPLE_N,
            "coarse_top_k": COARSE_TOP_K,
        },
        "train_codes": train_codes,
        "valid_codes": valid_codes,
        "coarse_codes": coarse_codes,
        "coarse_results": coarse_results,
        "fine_results_train": fine_results,
        "best_params": {
            "z": best["z"],
            "rev_ratio": best["rev_ratio"],
            "sl": best["sl"],
            "train_net_pnl": best["net_pnl"],
            "train_payoff": best["payoff_ratio"],
            "train_ce": best["ce_mean"],
            "train_profitable": best["profitable_stocks"],
        },
        "validation_best": valid_result,
        "validation_baseline": baseline_valid,
        "total_elapsed_minutes": round(total_elapsed / 60, 1),
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {OUT_FILE}")


if __name__ == "__main__":
    main()
