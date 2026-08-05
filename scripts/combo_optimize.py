"""
组合优化：基于单参数敏感性分析的最佳值，验证联合效果。
基准 net=-9315，目标：组合后接近 0 或盈利。
"""
from __future__ import annotations
import functools
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

print = functools.partial(print, flush=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock, aggregate_batch
from at0.data import normalize_code
from at0.strategy import SignalParams
from at0.risk import RiskParams

CACHE_DIR = PROJECT_ROOT / "data" / "multi_day_cache"
START_DATE = "2026-06-25"
END_DATE = "2026-07-24"


def list_cached_codes():
    suffix = f"_{START_DATE}_{END_DATE}.json"
    return [f.name.replace(suffix, "") for f in sorted(CACHE_DIR.iterdir()) if f.name.endswith(suffix)]


def load_multi_day_from_cache(code):
    nc = normalize_code(code)
    cache_path = CACHE_DIR / f"{nc['pure']}_{START_DATE}_{END_DATE}.json"
    if not cache_path.exists():
        return None, None
    with open(cache_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d["daily_bars"], d["daily_prev_closes"]


def run_eval(params: BacktestParams, codes: list[str]) -> dict:
    per_stock = []
    total_trades = 0
    for code in codes:
        daily_bars, daily_prev = load_multi_day_from_cache(code)
        if not daily_bars:
            continue
        try:
            result = backtest_multi_day(code, daily_bars, daily_prev, params=params)
            s = summarize_one_stock(code, result)
            per_stock.append(s)
            total_trades += s["total_trades"]
        except Exception as e:
            per_stock.append({"code": code, "error": str(e)})
    return aggregate_batch(per_stock, total_trades)


# 候选组合（基于敏感性分析的 Top 改善方向）
COMBOS = [
    # name, signal_params overrides, backtest_params overrides, risk_params overrides
    ("base", {}, {}, {}),
    # 1. 只改最有效的两个
    ("trail_act=0.003 + trail_ratio=0.3",
     {}, {"trailing_activation_pct": 0.003, "trailing_ratio": 0.3}, {}),
    # 2. 加紧止损
    ("trail + sl=0.005",
     {}, {"trailing_activation_pct": 0.003, "trailing_ratio": 0.3, "stop_loss_ratio": 0.005}, {}),
    # 3. 加量能门槛
    ("trail + sl + vol=2.5",
     {"tf_vol_ratio_min": 2.5}, {"trailing_activation_pct": 0.003, "trailing_ratio": 0.3, "stop_loss_ratio": 0.005}, {}),
    # 4. 加 ADX 门槛
    ("trail + sl + vol=2.5 + adx=40",
     {"tf_vol_ratio_min": 2.5, "tf_adx_threshold": 40.0},
     {"trailing_activation_pct": 0.003, "trailing_ratio": 0.3, "stop_loss_ratio": 0.005}, {}),
    # 5. 加 min_capture_spread
    ("trail + sl + vol=2.5 + adx=40 + spread=0.012",
     {"tf_vol_ratio_min": 2.5, "tf_adx_threshold": 40.0},
     {"trailing_activation_pct": 0.003, "trailing_ratio": 0.3, "stop_loss_ratio": 0.005},
     {"min_capture_spread": 0.012}),
    # 6. 紧止损但保留 vol=2.0
    ("trail + sl=0.005 + spread=0.012",
     {},
     {"trailing_activation_pct": 0.003, "trailing_ratio": 0.3, "stop_loss_ratio": 0.005},
     {"min_capture_spread": 0.012}),
    # 7. 最激进止损
    ("trail_act=0.003 + trail_ratio=0.3 + sl=0.003",
     {}, {"trailing_activation_pct": 0.003, "trailing_ratio": 0.3, "stop_loss_ratio": 0.003}, {}),
]


def main():
    codes = list_cached_codes()
    print(f"=== 组合优化（{len(codes)} 只股票，{START_DATE}~{END_DATE}）===")
    print()

    results = []
    for name, sp_ov, bp_ov, rp_ov in COMBOS:
        base = BacktestParams()
        sp = replace(base.signal_params, **sp_ov) if sp_ov else base.signal_params
        rp = replace(base.risk_params, **rp_ov) if rp_ov else base.risk_params
        params = replace(base, signal_params=sp, risk_params=rp, **bp_ov)
        t0 = time.time()
        ov = run_eval(params, codes)
        elapsed = time.time() - t0
        results.append({"name": name, "overall": ov, "elapsed": elapsed,
                        "params": {"sp_ov": sp_ov, "bp_ov": bp_ov, "rp_ov": rp_ov}})
        print(f"[{name}]")
        print(f"  net={ov['net_pnl_with_unrealized']:>9.1f} wr={ov['win_rate']:.4f} paired={ov['paired_trades']:>4} "
              f"profit={ov['profitable_stocks']:>2} loss={ov['losing_stocks']:>2} ({elapsed:.1f}s)")
        print()

    # 排序找最佳
    results.sort(key=lambda x: x["overall"]["net_pnl_with_unrealized"], reverse=True)
    print("=== 最终排名（按净盈亏）===")
    for i, r in enumerate(results, 1):
        ov = r["overall"]
        print(f"#{i} {r['name']}: net={ov['net_pnl_with_unrealized']:>9.1f} wr={ov['win_rate']:.4f} "
              f"paired={ov['paired_trades']:>4} profit={ov['profitable_stocks']:>2} loss={ov['losing_stocks']:>2}")

    best = results[0]
    print(f"\n=== 最佳组合 ===")
    print(f"  name: {best['name']}")
    print(f"  net:  {best['overall']['net_pnl_with_unrealized']:.2f}")
    print(f"  wr:   {best['overall']['win_rate']:.4f}")
    print(f"  params: {json.dumps(best['params'], ensure_ascii=False, indent=2)}")

    out_path = PROJECT_ROOT / "outputs/backtest/combo_optimization.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
