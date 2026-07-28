"""
参数优化脚本 — 用本地 multi_day_cache 离线搜索最佳趋势跟随参数
================================================================
样本：data/multi_day_cache/ 下 2026-06-25~2026-07-24 的 74 只股票
方法：两阶段
  阶段1 粗筛：选 10 只代表股票 × 全网格（4×4×3×3×3×3=1296组），按评分排前 20
  阶段2 精调：74 只股票 × 前 20 组，按综合评分选最佳
目标函数：
  score = net_pnl + 0.5*(win_rate-0.5)*scale - 0.3*max_drawdown
  （净盈亏为主，胜率>50%奖励、<50%惩罚，回撤惩罚）

@deprecated 本脚本用 SignalParams(**sp_fields) 直接构造参数，未走
config/thresholds.yaml，基准参数与 yaml 不一致会导致寻优结果不可比。
新代码请使用 scripts/optimize_zz500_params.py（已通过 load_signal_params()
统一走 yaml）。保留本脚本仅为历史参考，不应在新流程中调用。
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import time
import traceback
from dataclasses import replace
from itertools import product
from pathlib import Path

# 强制 print 立即刷新（避免后台运行时输出缓冲）
print = functools.partial(print, flush=True)

# 保证 src/ 在 path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock, aggregate_batch
from at0.data import fetch_multi_day, normalize_code
from at0.strategy import SignalParams
from at0.risk import RiskParams

CACHE_DIR = PROJECT_ROOT / "data" / "multi_day_cache"
START_DATE = "2026-06-25"
END_DATE = "2026-07-24"


# ──────────────────────────────────────────────────────────────
# 参数搜索空间
# ──────────────────────────────────────────────────────────────
PARAM_GRID = {
    "vwap_dev_atr_multiplier": [0.6, 0.8, 1.0],      # 3（开仓深度）
    "tf_adx_threshold":        [25.0, 30.0, 35.0],    # 3（趋势确认）
    "tf_vol_ratio_min":        [1.5, 2.0],            # 2（量能门槛）
    "stop_loss_ratio":         [0.005, 0.008, 0.012], # 3（止损）
    "cooldown_bars":           [6, 12],               # 2（降频）
}
# 合计 3*3*2*3*2 = 108 组合（去掉 trailing_activation_pct，影响较小；trailing 默认 0.005）


def list_cached_codes() -> list[str]:
    """枚举缓存目录里覆盖 2026-06-25~2026-07-24 的股票代码。"""
    if not CACHE_DIR.exists():
        return []
    suffix = f"_{START_DATE}_{END_DATE}.json"
    codes = []
    for f in sorted(CACHE_DIR.iterdir()):
        if f.name.endswith(suffix):
            codes.append(f.name.replace(suffix, ""))
    return codes


def load_multi_day_from_cache(code: str):
    """直接从缓存文件读取（不走网络）。"""
    nc = normalize_code(code)
    cache_path = CACHE_DIR / f"{nc['pure']}_{START_DATE}_{END_DATE}.json"
    if not cache_path.exists():
        return None, None, None
    with open(cache_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d["daily_bars"], d["daily_prev_closes"], d.get("daily_meta", {})


def build_params(combo: dict, base_shares: int = 3000) -> BacktestParams:
    """根据参数组合构造 BacktestParams。"""
    sp_fields = {
        k: v for k, v in combo.items()
        if k in {"vwap_dev_atr_multiplier", "tf_adx_threshold", "tf_vol_ratio_min"}
    }
    bp_fields = {
        k: v for k, v in combo.items()
        if k in {"stop_loss_ratio", "trailing_activation_pct", "cooldown_bars"}
    }
    sp = SignalParams(**sp_fields)
    return BacktestParams(
        base_shares=base_shares,
        signal_params=sp,
        **bp_fields,
    )


def score_combo(per_stock: list[dict]) -> float:
    """
    综合评分（v2：避免过度开仓）：
      - net_pnl（净盈亏）为主指标
      - 胜率偏离 50% 给奖惩（鼓励高胜率）
      - 亏损股票数惩罚（鼓励一致性）
      - 平均单笔盈亏惩罚（避免"开多亏多"的退化解）
    """
    if not per_stock:
        return -1e9
    valid = [s for s in per_stock if "error" not in s]
    if not valid:
        return -1e9
    net = sum(s.get("net_pnl_with_unrealized", s.get("net_pnl", 0)) for s in valid)
    paired = sum(s.get("paired_trades", 0) for s in valid)
    wins = sum(s.get("win_trades", 0) for s in valid)
    win_rate = wins / paired if paired > 0 else 0.5
    losers = sum(1 for s in valid if s.get("net_pnl_with_unrealized", s.get("net_pnl", 0)) < 0)
    # 评分：净盈亏 + 胜率奖励 - 亏损股惩罚
    score = net + (win_rate - 0.5) * 5000 - losers * 100
    # 平均单笔盈亏：如果 paired>0 且 avg_pnl 为负，额外惩罚（避免靠开多仓拉低净盈亏）
    if paired > 0:
        avg_pnl = net / paired
        if avg_pnl < 0:
            score += avg_pnl * 10  # 负值再放大惩罚
    # 完全不开仓的退化解惩罚（保持少量下限）
    if paired < len(valid) * 0.5:
        score -= 5000
    return round(score, 2)


def run_one_combo(combo: dict, code_list: list[str], base_shares: int = 3000) -> tuple[float, dict]:
    """对一组参数在指定股票集合上跑回测，返回 (score, detail)。"""
    params = build_params(combo, base_shares)
    per_stock = []
    total_trades = 0
    for code in code_list:
        daily_bars, daily_prev, _ = load_multi_day_from_cache(code)
        if not daily_bars:
            continue
        try:
            result = backtest_multi_day(code, daily_bars, daily_prev, params=params)
            s = summarize_one_stock(code, result)
            per_stock.append(s)
            total_trades += s["total_trades"]
        except Exception as e:
            per_stock.append({"code": code, "error": str(e)})
    score = score_combo(per_stock)
    overall = aggregate_batch(per_stock, total_trades) if per_stock else {}
    return score, {"combo": combo, "score": score, "overall": overall, "per_stock_count": len(per_stock)}


def pick_representative(codes: list[str], n: int = 10) -> list[str]:
    """从代码列表里挑选 n 只代表股票（按振幅/成交额分散）。这里简化：均匀抽样。"""
    if len(codes) <= n:
        return codes
    step = len(codes) // n
    return codes[::step][:n]


def main():
    parser = argparse.ArgumentParser(description="参数优化（本地缓存）")
    parser.add_argument("--stage1-codes", type=int, default=10, help="阶段1代表股票数")
    parser.add_argument("--stage1-top", type=int, default=20, help="阶段1保留前N组")
    parser.add_argument("--stage2-codes", type=int, default=0, help="阶段2股票数（0=全部缓存）")
    parser.add_argument("--base-shares", type=int, default=3000)
    parser.add_argument("--out", default="outputs/backtest/param_optimization.json")
    parser.add_argument("--limit-combos", type=int, default=0, help="限制组合数（调试用，0=全部）")
    args = parser.parse_args()

    print(f"=== 参数优化（本地缓存 {START_DATE}~{END_DATE}） ===")
    all_codes = list_cached_codes()
    print(f"缓存股票数: {len(all_codes)}")
    if not all_codes:
        print("错误：缓存目录无数据")
        return

    # 生成所有参数组合
    keys = list(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combos = [dict(zip(keys, vals)) for vals in product(*values)]
    if args.limit_combos > 0:
        combos = combos[:args.limit_combos]
    print(f"参数组合数: {len(combos)}")

    # ── 阶段1：粗筛 ──
    stage1_codes = pick_representative(all_codes, args.stage1_codes)
    print(f"\n--- 阶段1：粗筛（{len(stage1_codes)} 只代表股 × {len(combos)} 组合）---")
    print(f"代表股: {stage1_codes}")
    t0 = time.time()
    results_stage1 = []
    for i, combo in enumerate(combos, 1):
        t1 = time.time()
        score, detail = run_one_combo(combo, stage1_codes, args.base_shares)
        results_stage1.append(detail)
        elapsed = time.time() - t1
        total_elapsed = time.time() - t0
        ov = detail.get("overall", {})
        print(f"[{i}/{len(combos)}] score={score:>10.1f}  net={ov.get('net_pnl_with_unrealized',0):>9.1f}  "
              f"wr={ov.get('win_rate',0):.3f}  paired={ov.get('paired_trades',0):>4}  "
              f"({elapsed:.1f}s, 累计{total_elapsed:.0f}s)")

    # 按评分排序，保留前 N
    results_stage1.sort(key=lambda x: x["score"], reverse=True)
    top_combos = [r["combo"] for r in results_stage1[:args.stage1_top]]
    print(f"\n阶段1完成（{time.time()-t0:.0f}s），保留前 {len(top_combos)} 组：")
    for i, r in enumerate(results_stage1[:args.stage1_top], 1):
        print(f"  #{i}  score={r['score']:.1f}  net={r['overall'].get('net_pnl_with_unrealized',0):.1f}  "
              f"wr={r['overall'].get('win_rate',0):.3f}  combo={r['combo']}")

    # ── 阶段2：精调 ──
    stage2_codes = all_codes if args.stage2_codes == 0 else pick_representative(all_codes, args.stage2_codes)
    print(f"\n--- 阶段2：精调（{len(stage2_codes)} 只股票 × {len(top_combos)} 组合）---")
    t0 = time.time()
    results_stage2 = []
    for i, combo in enumerate(top_combos, 1):
        t1 = time.time()
        score, detail = run_one_combo(combo, stage2_codes, args.base_shares)
        results_stage2.append(detail)
        elapsed = time.time() - t1
        total_elapsed = time.time() - t0
        ov = detail.get("overall", {})
        print(f"[{i}/{len(top_combos)}] score={score:>10.1f}  net={ov.get('net_pnl_with_unrealized',0):>9.1f}  "
              f"wr={ov.get('win_rate',0):.3f}  paired={ov.get('paired_trades',0):>4}  "
              f"({elapsed:.1f}s, 累计{total_elapsed:.0f}s)")

    results_stage2.sort(key=lambda x: x["score"], reverse=True)
    best = results_stage2[0] if results_stage2 else None
    print(f"\n=== 阶段2完成（{time.time()-t0:.0f}s） ===")
    if best:
        print("\n=== 最佳参数组合 ===")
        print(f"  score: {best['score']:.1f}")
        ov = best["overall"]
        print(f"  净盈亏: {ov.get('net_pnl_with_unrealized', 0):.2f}")
        print(f"  胜率:   {ov.get('win_rate', 0):.4f}")
        print(f"  配对数: {ov.get('paired_trades', 0)}")
        print(f"  盈利股: {ov.get('profitable_stocks', 0)}  亏损股: {ov.get('losing_stocks', 0)}")
        print(f"  参数: {json.dumps(best['combo'], ensure_ascii=False, indent=2)}")

    # 保存结果
    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "start_date": START_DATE,
            "end_date": END_DATE,
            "cache_dir": str(CACHE_DIR),
            "param_grid": PARAM_GRID,
            "stage1_codes": stage1_codes,
            "stage2_codes_count": len(stage2_codes),
            "stage1_top": results_stage1[:args.stage1_top],
            "stage2_all": results_stage2,
            "best": best,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
