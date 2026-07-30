#!/usr/bin/env python3
"""
均值回归(mean_reversion) vs 趋势跟随(trend_following) A/B对比测试

样本: 10只股票（seed=42随机抽样，与K_mr_fix_100对齐）
区间: 3年（2023-07-25 ~ 2026-07-22，与项目标准口径一致）
对比指标: net_pnl / win_rate / payoff / paired_trades / CE(捕获效率)

说明：
  - baseline: trend_following（yaml默认 sl=0.002 tr=0.2 mh=24 cd=24）
  - mr:       mean_reversion（yaml默认 z=1.0 rev=0.3 min_rev=0.001
                              sl=0.0015 mh=10 cd=8 仓位=0.12）
  - CE定义: (sell_price - buy_price) / (day_high - day_low)
  - 输出: 控制台对比表 + outputs/oos_validation/mr_vs_tf_10stocks.json
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "archive" / "diag"))

from at0.paths import ZZ500_5MIN_DIR
from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock
from at0.config import load_signal_params, load_risk_params, load_backtest_params
from at0.cli import adapt_params_by_frequency

# 复用 diag_stepJ1 的 CE 计算工具
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
SAMPLE_N = 100
SEED = 42
BASE_SHARES = 3000

OUTPUT_DIR = PROJECT_ROOT / "outputs" / "oos_validation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUTPUT_DIR / f"mr_vs_tf_{SAMPLE_N}stocks.json"


def select_stocks() -> list[str]:
    """seed=42从500只中随机抽样10只（与K_mr_fix_100样本对齐）。"""
    all_files = sorted(ZZ500_5MIN_DIR.glob("*.json"))
    all_codes = [f.stem for f in all_files if not f.stem.startswith("_")]
    random.seed(SEED)
    return sorted(random.sample(all_codes, SAMPLE_N))


def build_params(strategy_mode: str) -> BacktestParams:
    """构建回测参数。

    strategy_mode="trend_following": yaml默认（sl=0.002 tr=0.2 mh=24 cd=24）
    strategy_mode="mean_reversion":  yaml默认MR覆盖（sl=0.0015 mh=10 cd=8 仓位=0.12）
    """
    from dataclasses import replace as _replace
    sp = load_signal_params()
    rp = load_risk_params()
    bp = load_backtest_params()

    # 切换模式
    sp = _replace(sp, strategy_mode=strategy_mode)

    params = BacktestParams(
        base_shares=BASE_SHARES,
        avg_cost=0.0,  # 由 run_single_stock 按首日prev_close填充
        signal_params=sp,
        risk_params=rp,
        stop_loss_ratio=bp.stop_loss_ratio,
        trailing_ratio=bp.trailing_ratio,
        trailing_activation_pct=bp.trailing_activation_pct,
        max_holding_bars=bp.max_holding_bars,
        cooldown_bars=bp.cooldown_bars,
        # MR模式专用风控（yaml默认值：sl=0.0015 mh=10 cd=8 仓位=0.12）
        mr_stop_loss_ratio=bp.mr_stop_loss_ratio,
        mr_max_holding_bars=bp.mr_max_holding_bars,
        mr_cooldown_bars=bp.mr_cooldown_bars,
    )
    if bp.mr_max_t_size_ratio is not None:
        from dataclasses import replace as _r
        params = _r(params, risk_params=_r(rp, max_t_size_ratio=bp.mr_max_t_size_ratio))
    return params


def run_single(code: str, params: BacktestParams) -> dict | None:
    """单股回测，返回 summary + CE 统计。"""
    data = load_zz500_data(code, START_DATE, END_DATE)
    if not data:
        return None
    daily_bars, daily_prev_closes = data
    if not daily_bars:
        return None

    # avg_cost 取首日 prev_close
    first_date = min(daily_prev_closes.keys())
    avg_cost = daily_prev_closes[first_date]
    if avg_cost <= 0:
        return None

    from dataclasses import replace as _replace
    params = _replace(params, avg_cost=avg_cost)

    # 频率适配（5min, 48根/天）
    params = adapt_params_by_frequency(params, "5min", 48)
    # 适配后重新应用 max_holding_bars（避免被 adapt 覆盖）
    # MR模式下 effective_max_holding_bars 会从 mr_max_holding_bars 取，这里不强制覆盖
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

    return {
        "code": code,
        "summary": summary,
        "ce_stats": ce_stats,
    }


def run_group(codes: list[str], strategy_mode: str, tag: str) -> dict:
    """跑一组回测（所有股票用同一模式）。"""
    print(f"\n{'=' * 70}")
    print(f"  [{tag}] 模式={strategy_mode}  股票={len(codes)}只  区间={START_DATE}~{END_DATE}")
    print(f"{'=' * 70}")

    params = build_params(strategy_mode)
    print(f"  参数: stop_loss={params.effective_stop_loss_ratio} "
          f"max_hold={params.effective_max_holding_bars} "
          f"cooldown={params.effective_cooldown_bars} "
          f"mode={params.signal_params.strategy_mode}")
    if params.is_mean_reversion:
        sp = params.signal_params
        print(f"  MR参数: z={sp.mr_z_threshold} rev_ratio={sp.mr_take_profit_reversion_ratio} "
              f"min_rev={sp.mr_min_reversion} lookback={sp.mr_lookback_bars} "
              f"vol_max={sp.mr_vol_ratio_max}")

    per_stock = []
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        try:
            res = run_single(code, params)
            if not res:
                print(f"  [{i}/{len(codes)}] {code}: 无数据")
                continue
            s = res["summary"]
            ce = res["ce_stats"]
            per_stock.append({
                "code": code,
                "paired_trades": s.get("paired_trades", 0),
                "win_rate": s.get("win_rate", 0),
                "payoff_ratio": s.get("payoff_ratio", 0),
                "net_pnl": s.get("net_pnl", 0),
                "net_pnl_with_unrealized": s.get("net_pnl_with_unrealized", 0),
                "avg_win": s.get("avg_win", 0),
                "avg_loss": s.get("avg_loss", 0),
                "ce_mean": ce["all_stats"]["mean"],
                "ce_p50": ce["all_stats"]["p50"],
                "ce_total_pairs": ce["total_pairs"],
            })
            print(f"  [{i}/{len(codes)}] {code}: 配对={s.get('paired_trades',0):3d} "
                  f"胜率={s.get('win_rate',0)*100:5.1f}% "
                  f"payoff={s.get('payoff_ratio',0):.2f} "
                  f"净={s.get('net_pnl',0):+10.0f} "
                  f"CE均值={ce['all_stats']['mean']*100:5.2f}%")
        except Exception as e:
            print(f"  [{i}/{len(codes)}] {code}: ERROR - {e}")
            import traceback
            traceback.print_exc()

    elapsed = time.time() - t0
    # 汇总
    total_paired = sum(s["paired_trades"] for s in per_stock)
    total_net = sum(s["net_pnl"] for s in per_stock)
    total_net_with_unreal = sum(s["net_pnl_with_unrealized"] for s in per_stock)
    avg_win = sum(s["avg_win"] * s["paired_trades"] for s in per_stock if s["paired_trades"]) / max(total_paired, 1)
    avg_loss = sum(s["avg_loss"] * s["paired_trades"] for s in per_stock if s["paired_trades"]) / max(total_paired, 1)
    # 加权胜率
    wins = sum(int(s["win_rate"] * s["paired_trades"]) for s in per_stock if s["paired_trades"])
    win_rate = wins / total_paired if total_paired else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    # CE 加权均值
    ce_weighted = sum(s["ce_mean"] * s["ce_total_pairs"] for s in per_stock) / max(
        sum(s["ce_total_pairs"] for s in per_stock), 1)

    overall = {
        "strategy_mode": strategy_mode,
        "tag": tag,
        "stocks": len(per_stock),
        "paired_trades": total_paired,
        "win_rate": round(win_rate, 4),
        "payoff_ratio": round(payoff, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "net_pnl": round(total_net, 2),
        "net_pnl_with_unrealized": round(total_net_with_unreal, 2),
        "ce_mean": round(ce_weighted, 4),
        "ce_p50_median": round(
            sorted([s["ce_p50"] for s in per_stock])[len(per_stock) // 2] if per_stock else 0, 4),
        "profitable_stocks": sum(1 for s in per_stock if s["net_pnl"] > 0),
        "elapsed_seconds": round(elapsed, 1),
    }

    print(f"\n  [{tag}] 汇总: 配对{total_paired} 胜率{win_rate*100:.1f}% "
          f"payoff={payoff:.2f} 净盈亏{total_net:+.0f} "
          f"CE均值={ce_weighted*100:.2f}% 盈利{overall['profitable_stocks']}/{len(per_stock)} "
          f"({elapsed:.0f}s)")
    return {"overall": overall, "per_stock": per_stock}


def print_comparison(tf: dict, mr: dict):
    """打印A/B对比表。"""
    tfo, mro = tf["overall"], mr["overall"]
    print(f"\n{'=' * 82}")
    print(f"  A/B 对比（10只股票 × 3年）")
    print(f"{'=' * 82}")
    print(f"{'指标':<20} {'趋势跟随(TF)':<20} {'均值回归(MR)':<20} {'差异':<20}")
    print(f"{'-' * 82}")
    # (名称, TF值, MR值, 差异字符串)
    mr_paired, tf_paired = mro["paired_trades"], tfo["paired_trades"]
    paired_diff = f"{mr_paired-tf_paired:+d} ({mr_paired/tf_paired:.1f}×)" if tf_paired else f"{mr_paired-tf_paired:+d}"
    rows = [
        ("配对笔数", tfo["paired_trades"], mro["paired_trades"], paired_diff),
        ("胜率", f"{tfo['win_rate']*100:.1f}%", f"{mro['win_rate']*100:.1f}%",
         f"{(mro['win_rate']-tfo['win_rate'])*100:+.1f}pp"),
        ("payoff比", f"{tfo['payoff_ratio']:.4f}", f"{mro['payoff_ratio']:.4f}",
         f"{mro['payoff_ratio']-tfo['payoff_ratio']:+.4f}"),
        ("单笔均盈", f"{tfo['avg_win']:+.2f}", f"{mro['avg_win']:+.2f}",
         f"{mro['avg_win']-tfo['avg_win']:+.2f}"),
        ("单笔均亏", f"{tfo['avg_loss']:+.2f}", f"{mro['avg_loss']:+.2f}",
         f"{mro['avg_loss']-tfo['avg_loss']:+.2f}"),
        ("净盈亏(已实现)", f"{tfo['net_pnl']:+.0f}", f"{mro['net_pnl']:+.0f}",
         f"{mro['net_pnl']-tfo['net_pnl']:+.0f}"),
        ("净盈亏(含浮盈)", f"{tfo['net_pnl_with_unrealized']:+.0f}", f"{mro['net_pnl_with_unrealized']:+.0f}",
         f"{mro['net_pnl_with_unrealized']-tfo['net_pnl_with_unrealized']:+.0f}"),
        ("CE均值", f"{tfo['ce_mean']*100:.2f}%", f"{mro['ce_mean']*100:.2f}%",
         f"{(mro['ce_mean']-tfo['ce_mean'])*100:+.2f}pp"),
        ("CE中位", f"{tfo['ce_p50_median']*100:.2f}%", f"{mro['ce_p50_median']*100:.2f}%",
         f"{(mro['ce_p50_median']-tfo['ce_p50_median'])*100:+.2f}pp"),
        ("盈利股票", f"{tfo['profitable_stocks']}/10", f"{mro['profitable_stocks']}/10",
         f"{mro['profitable_stocks']-tfo['profitable_stocks']:+d}"),
    ]
    for name, a, b, diff in rows:
        print(f"  {name:<18} {str(a):<20} {str(b):<20} {diff}")
    print(f"{'-' * 82}")

    # 按股票拆分对比
    print(f"\n  按股票拆分：")
    print(f"  {'code':<10} {'TF配对':>8} {'MR配对':>8} {'TF净':>10} {'MR净':>10} {'TF_CE%':>8} {'MR_CE%':>8}")
    tf_map = {s["code"]: s for s in tf["per_stock"]}
    mr_map = {s["code"]: s for s in mr["per_stock"]}
    for code in sorted(set(tf_map) | set(mr_map)):
        t = tf_map.get(code, {})
        m = mr_map.get(code, {})
        tp = t.get("paired_trades", 0)
        mp = m.get("paired_trades", 0)
        tn = t.get("net_pnl", 0)
        mn = m.get("net_pnl", 0)
        tc = t.get("ce_mean", 0) * 100
        mc = m.get("ce_mean", 0) * 100
        print(f"  {code:<10} {tp:>8} {mp:>8} {tn:>+10.0f} {mn:>+10.0f} {tc:>8.2f} {mc:>8.2f}")


def main():
    print("=" * 78)
    print("均值回归(MR) vs 趋势跟随(TF) A/B对比测试")
    print("=" * 78)
    print(f"样本: {SAMPLE_N}只股票（seed={SEED}随机抽样）")
    print(f"区间: {START_DATE} ~ {END_DATE}（3年）")
    print(f"指标: net_pnl / win_rate / payoff / CE")

    codes = select_stocks()
    print(f"\n抽样股票: {codes}")

    # A: 趋势跟随基线
    tf = run_group(codes, "trend_following", "TF_baseline")
    # B: 均值回归新模式
    mr = run_group(codes, "mean_reversion", "MR_new")

    # 对比
    print_comparison(tf, mr)

    # 保存
    out = {
        "step": "mr_vs_tf_10stocks_ab",
        "config": {
            "sample_n": SAMPLE_N,
            "seed": SEED,
            "start_date": START_DATE,
            "end_date": END_DATE,
            "base_shares": BASE_SHARES,
        },
        "stocks": codes,
        "tf_baseline": tf,
        "mr_new": mr,
    }
    out_path = OUT_FILE
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
