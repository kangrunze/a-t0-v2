"""
单参数敏感性分析：以默认参数为基准，每次只改一个参数，看对 72 只股票的影响。
目标：找到能改善默认参数 (-10842) 的微调方向。
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


def main():
    codes = list_cached_codes()
    print(f"=== 单参数敏感性分析（{len(codes)} 只股票，{START_DATE}~{END_DATE}）===")
    print()

    # 基准：默认参数
    base_params = BacktestParams()
    t0 = time.time()
    base_ov = run_eval(base_params, codes)
    print(f"基准（默认参数）: net={base_ov['net_pnl_with_unrealized']:.1f} wr={base_ov['win_rate']:.4f} "
          f"paired={base_ov['paired_trades']} profit={base_ov['profitable_stocks']} loss={base_ov['losing_stocks']} "
          f"({time.time()-t0:.1f}s)")
    print()

    # 单参数变化
    variations = [
        # ("vwap_dev_atr_multiplier", [0.6, 0.8, 1.0, 1.2]),
        ("tf_adx_threshold", [25.0, 30.0, 35.0, 40.0, 45.0]),
        ("tf_vol_ratio_min", [1.5, 2.0, 2.5, 3.0]),
        ("stop_loss_ratio", [0.005, 0.008, 0.012, 0.015]),
        ("cooldown_bars", [6, 12, 18, 24]),
        ("trailing_activation_pct", [0.003, 0.005, 0.008, 0.012]),
        ("trailing_ratio", [0.3, 0.5, 0.7]),
        ("max_holding_bars", [6, 12, 18, 24]),
        ("min_capture_spread", [0.006, 0.0072, 0.009, 0.012]),
    ]

    results = {"base": {"net": base_ov['net_pnl_with_unrealized'], "wr": base_ov['win_rate'],
                        "paired": base_ov['paired_trades'], "overall": base_ov}}
    best_net = base_ov['net_pnl_with_unrealized']
    best_combo = {"name": "base", "value": None}

    for param_name, values in variations:
        print(f"--- {param_name} ---")
        results[param_name] = {}
        for v in values:
            # 构造参数
            if param_name in {"tf_adx_threshold", "tf_vol_ratio_min", "vwap_dev_atr_multiplier"}:
                sp = replace(base_params.signal_params, **{param_name: v})
                params = replace(base_params, signal_params=sp)
            elif param_name == "min_capture_spread":
                rp = replace(base_params.risk_params, **{param_name: v})
                params = replace(base_params, risk_params=rp)
            else:
                params = replace(base_params, **{param_name: v})
            t0 = time.time()
            ov = run_eval(params, codes)
            delta = ov['net_pnl_with_unrealized'] - base_ov['net_pnl_with_unrealized']
            marker = " ★" if ov['net_pnl_with_unrealized'] > best_net else ""
            if ov['net_pnl_with_unrealized'] > best_net:
                best_net = ov['net_pnl_with_unrealized']
                best_combo = {"name": param_name, "value": v}
            print(f"  {param_name}={v}: net={ov['net_pnl_with_unrealized']:>9.1f} ({delta:>+9.1f}) wr={ov['win_rate']:.4f} "
                  f"paired={ov['paired_trades']:>4} profit={ov['profitable_stocks']:>2} loss={ov['losing_stocks']:>2} "
                  f"({time.time()-t0:.1f}s){marker}")
            results[param_name][str(v)] = {"net": ov['net_pnl_with_unrealized'], "wr": ov['win_rate'],
                                            "paired": ov['paired_trades'], "overall": ov}
        print()

    print("=== 总结 ===")
    print(f"基准 net: {base_ov['net_pnl_with_unrealized']:.1f}")
    print(f"最佳 net: {best_net:.1f}  (由 {best_combo['name']}={best_combo['value']} 改善)")

    out_path = PROJECT_ROOT / "outputs/backtest/param_sensitivity.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"结果已保存: {out_path}")


if __name__ == "__main__":
    main()
