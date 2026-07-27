"""
最终验证：用最佳参数组合在 72 只缓存股票上跑完整回测，输出 per_stock 明细。
对比基准（默认参数）和最佳组合（trail_act=0.003 + trail_ratio=0.3 + sl=0.003）。
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


def run_batch(params: BacktestParams, codes: list[str], label: str):
    print(f"\n=== {label} ===")
    per_stock = []
    total_trades = 0
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        daily_bars, daily_prev = load_multi_day_from_cache(code)
        if not daily_bars:
            continue
        try:
            result = backtest_multi_day(code, daily_bars, daily_prev, params=params)
            s = summarize_one_stock(code, result)
            per_stock.append(s)
            total_trades += s["total_trades"]
            net = s.get("net_pnl_with_unrealized", s.get("net_pnl", 0))
            print(f"  [{i:>2}/{len(codes)}] {code}: net={net:>8.1f} wr={s['win_rate']:.3f} paired={s['paired_trades']:>3}")
        except Exception as e:
            print(f"  [{i:>2}/{len(codes)}] {code}: ERROR {e}")
    overall = aggregate_batch(per_stock, total_trades)
    elapsed = time.time() - t0
    print(f"\n  汇总: net={overall['net_pnl_with_unrealized']:.2f} wr={overall['win_rate']:.4f} "
          f"paired={overall['paired_trades']} profit={overall['profitable_stocks']} loss={overall['losing_stocks']} "
          f"({elapsed:.1f}s)")
    return {"per_stock": per_stock, "overall": overall, "elapsed": elapsed}


def main():
    codes = list_cached_codes()
    print(f"=== 最终验证（{len(codes)} 只股票，{START_DATE}~{END_DATE}）===")

    # 基准：默认参数
    base_params = BacktestParams()
    base_result = run_batch(base_params, codes, "基准（默认参数）")

    # 最佳组合
    best_params = replace(
        BacktestParams(),
        trailing_activation_pct=0.003,
        trailing_ratio=0.3,
        stop_loss_ratio=0.003,
    )
    best_result = run_batch(best_params, codes, "最佳组合（trail_act=0.003 + trail_ratio=0.3 + sl=0.003）")

    # 稳健组合（胜率更高）
    robust_params = replace(
        BacktestParams(),
        trailing_activation_pct=0.003,
        trailing_ratio=0.3,
    )
    robust_result = run_batch(robust_params, codes, "稳健组合（trail_act=0.003 + trail_ratio=0.3）")

    # 对比汇总
    print("\n" + "=" * 80)
    print("=== 最终对比 ===")
    print(f"{'组合':<40} {'净盈亏':>10} {'胜率':>8} {'配对':>6} {'盈利股':>8} {'亏损股':>8}")
    print("-" * 80)
    for label, r in [("基准（默认参数）", base_result),
                     ("最佳组合（紧止损）", best_result),
                     ("稳健组合（不紧止损）", robust_result)]:
        ov = r["overall"]
        print(f"{label:<40} {ov['net_pnl_with_unrealized']:>10.1f} {ov['win_rate']:>8.4f} "
              f"{ov['paired_trades']:>6} {ov['profitable_stocks']:>8} {ov['losing_stocks']:>8}")

    # 改善
    base_net = base_result["overall"]["net_pnl_with_unrealized"]
    best_net = best_result["overall"]["net_pnl_with_unrealized"]
    robust_net = robust_result["overall"]["net_pnl_with_unrealized"]
    print(f"\n改善（vs 基准）:")
    print(f"  最佳组合: {best_net - base_net:+.2f}  ({(best_net - base_net)/abs(base_net)*100:+.1f}%)")
    print(f"  稳健组合: {robust_net - base_net:+.2f}  ({(robust_net - base_net)/abs(base_net)*100:+.1f}%)")

    # 保存
    out_path = PROJECT_ROOT / "outputs/backtest/final_validation.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "start_date": START_DATE,
            "end_date": END_DATE,
            "stocks_count": len(codes),
            "base": base_result,
            "best": best_result,
            "robust": robust_result,
            "best_params": {
                "trailing_activation_pct": 0.003,
                "trailing_ratio": 0.3,
                "stop_loss_ratio": 0.003,
            },
            "robust_params": {
                "trailing_activation_pct": 0.003,
                "trailing_ratio": 0.3,
            },
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
