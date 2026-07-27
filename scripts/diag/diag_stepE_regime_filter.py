#!/usr/bin/env python3
"""
Stage E-3：regime 过滤效果诊断
===============================
1. 跑 baseline（regime_filter_enabled=False），记录每日 trades
2. 跑 with filter（regime_filter_enabled=True），拿 filtered_days 列表
3. 从 baseline 提取被 NO_TRADE 拦掉日期的 trades，算盈亏分布
4. 对比 overall 指标（win_rate/net_pnl/profitable_stocks/filtered_count）

验收 E-2：被 NO_TRADE 拦掉的历史交易中，原本亏损的比例显著高于原本盈利的比例。
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
    """构造回测参数（走 yaml 默认，与 Stage A-D 修复后基线一致）。"""
    sp = load_signal_params()
    rp = load_risk_params()
    return _replace(
        load_backtest_params(),
        base_shares=base_shares,
        avg_cost=10.0,  # 占位，run 时按首日 prev_close 覆盖
        signal_params=sp,
        risk_params=rp,
    )


def run_one(code: str, params, regime_filter: bool) -> dict:
    """单股跑一次回测。返回 backtest_multi_day 结果。"""
    daily_bars, daily_prev_closes, _ = load_multi_day_zz500(
        code, START_DATE, END_DATE, DATA_DIR,
    )
    if not daily_bars:
        return {}

    # avg_cost 用首日 prev_close
    first_date = min(daily_prev_closes.keys())
    params = _replace(params, avg_cost=daily_prev_closes[first_date])

    return backtest_multi_day(
        code=normalize_code(code)["pure"],
        daily_bars=daily_bars,
        daily_prev_closes=daily_prev_closes,
        params=params,
        regime_filter_enabled=regime_filter,
    )


def extract_trades_by_date(result: dict) -> dict[str, list[dict]]:
    """从 backtest_multi_day 结果提取 {date: [trades]}。"""
    by_date: dict[str, list[dict]] = {}
    for daily in result.get("daily_results", []):
        d = daily.get("date", "")
        trades = daily.get("trades", [])
        by_date[d] = trades
    return by_date


def compute_trade_pnl_stats(trades: list[dict]) -> dict:
    """计算交易列表的盈亏统计。"""
    if not trades:
        return {"count": 0, "win": 0, "loss": 0, "total_pnl": 0.0}
    # trades 里每个是开仓/平仓记录，pnl 字段在配对平仓时有值
    paired = [t for t in trades if t.get("pnl") is not None]
    wins = sum(1 for t in paired if t.get("pnl", 0) > 0)
    losses = sum(1 for t in paired if t.get("pnl", 0) < 0)
    total_pnl = sum(t.get("pnl", 0) for t in paired)
    return {
        "count": len(paired),
        "win": wins,
        "loss": losses,
        "total_pnl": round(total_pnl, 2),
    }


def main() -> int:
    print("=" * 78)
    print("Stage E-3: regime 过滤效果诊断")
    print("=" * 78)
    print(f"股票数: {len(BASELINE_CODES)}")
    print(f"日期区间: {START_DATE} ~ {END_DATE}")
    print()

    params = build_params()

    # 累计统计
    baseline_overall = {"total_trades": 0, "paired_trades": 0, "win_trades": 0,
                        "net_pnl": 0.0, "profitable_stocks": 0, "losing_stocks": 0}
    filtered_overall = {"total_trades": 0, "paired_trades": 0, "win_trades": 0,
                        "net_pnl": 0.0, "profitable_stocks": 0, "losing_stocks": 0}
    # 被拦日期的交易盈亏（用 baseline 数据）
    blocked_trades_all = []  # 所有被拦日期的配对交易 pnl 列表
    blocked_dates_count = 0
    total_days = 0

    per_stock_comparison = []

    for i, code in enumerate(BASELINE_CODES, 1):
        print(f"[{i}/{len(BASELINE_CODES)}] {code}", end=" ", flush=True)

        # baseline
        bl = run_one(code, params, regime_filter=False)
        if not bl:
            print("无数据")
            continue
        # filtered
        ft = run_one(code, params, regime_filter=True)

        # baseline 统计
        bl_paired = bl.get("total_trades", 0)
        bl_wins = sum(1 for d in bl.get("daily_results", [])
                      for t in d.get("trades", []) if t.get("pnl", 0) > 0)
        bl_net = bl.get("net_pnl", 0.0)
        bl_profitable = 1 if bl_net > 0 else 0

        baseline_overall["total_trades"] += bl.get("total_trades", 0)
        baseline_overall["paired_trades"] += bl_paired
        baseline_overall["win_trades"] += bl_wins
        baseline_overall["net_pnl"] += bl_net
        baseline_overall["profitable_stocks"] += bl_profitable
        baseline_overall["losing_stocks"] += (1 - bl_profitable)

        # filtered 统计
        ft_paired = ft.get("total_trades", 0)
        ft_wins = sum(1 for d in ft.get("daily_results", [])
                      for t in d.get("trades", []) if t.get("pnl", 0) > 0)
        ft_net = ft.get("net_pnl", 0.0)
        ft_profitable = 1 if ft_net > 0 else 0

        filtered_overall["total_trades"] += ft.get("total_trades", 0)
        filtered_overall["paired_trades"] += ft_paired
        filtered_overall["win_trades"] += ft_wins
        filtered_overall["net_pnl"] += ft_net
        filtered_overall["profitable_stocks"] += ft_profitable
        filtered_overall["losing_stocks"] += (1 - ft_profitable)

        # 被拦日期的交易（从 baseline 提取）
        filtered_days = ft.get("filtered_days", [])
        blocked_dates_count += len(filtered_days)
        total_days += len(bl.get("daily_results", []))
        bl_trades_by_date = extract_trades_by_date(bl)
        for d in filtered_days:
            trades_on_d = bl_trades_by_date.get(d, [])
            for t in trades_on_d:
                if t.get("pnl") is not None:
                    blocked_trades_all.append(t.get("pnl", 0.0))

        bl_wr = bl_wins / bl_paired if bl_paired > 0 else 0.0
        ft_wr = ft_wins / ft_paired if ft_paired > 0 else 0.0
        print(f"baseline(交易={bl_paired},胜率={bl_wr:.2%},净={bl_net:+.1f}) "
              f"filtered(交易={ft_paired},胜率={ft_wr:.2%},净={ft_net:+.1f},拦{len(filtered_days)}日)")

        per_stock_comparison.append({
            "code": code,
            "baseline_paired": bl_paired,
            "baseline_win_rate": round(bl_wr, 4),
            "baseline_net_pnl": round(bl_net, 2),
            "filtered_paired": ft_paired,
            "filtered_win_rate": round(ft_wr, 4),
            "filtered_net_pnl": round(ft_net, 2),
            "filtered_days_count": len(filtered_days),
        })

    # ── 汇总 ──
    bl_wr_overall = (baseline_overall["win_trades"] / baseline_overall["paired_trades"]
                     if baseline_overall["paired_trades"] > 0 else 0.0)
    ft_wr_overall = (filtered_overall["win_trades"] / filtered_overall["paired_trades"]
                     if filtered_overall["paired_trades"] > 0 else 0.0)

    # 被拦交易盈亏统计
    blocked_count = len(blocked_trades_all)
    blocked_wins = sum(1 for p in blocked_trades_all if p > 0)
    blocked_losses = sum(1 for p in blocked_trades_all if p < 0)
    blocked_pnl = sum(blocked_trades_all)
    blocked_loss_ratio = blocked_losses / blocked_count if blocked_count > 0 else 0.0

    print("\n" + "=" * 78)
    print("Stage E-3 汇总：baseline vs regime filter")
    print("=" * 78)
    print(f"{'指标':<24} {'baseline':>16} {'filtered':>16} {'变化':>14}")
    print("-" * 78)
    rows = [
        ("total_trades",      baseline_overall["total_trades"],      filtered_overall["total_trades"],      "{:d}"),
        ("paired_trades",     baseline_overall["paired_trades"],     filtered_overall["paired_trades"],     "{:d}"),
        ("win_rate",          bl_wr_overall,                          ft_wr_overall,                          "{:.4f}"),
        ("net_pnl",           baseline_overall["net_pnl"],            filtered_overall["net_pnl"],            "{:+.2f}"),
        ("profitable_stocks", baseline_overall["profitable_stocks"],  filtered_overall["profitable_stocks"],  "{:d}"),
        ("losing_stocks",     baseline_overall["losing_stocks"],      filtered_overall["losing_stocks"],      "{:d}"),
    ]
    for name, bl_v, ft_v, fmt in rows:
        delta = ft_v - bl_v
        delta_str = f"{delta:+.2f}" if isinstance(ft_v, float) else f"{delta:+d}"
        print(f"{name:<24} {fmt.format(bl_v):>16} {fmt.format(ft_v):>16} {delta_str:>14}")
    print("=" * 78)

    print(f"\n被 NO_TRADE 拦掉的日期数: {blocked_dates_count} / 总交易日 {total_days} "
          f"({blocked_dates_count/total_days*100:.1f}%)" if total_days > 0 else "")

    print(f"\n被拦日期的交易盈亏统计（验收 E-2）:")
    print(f"  被拦交易总数:    {blocked_count}")
    print(f"  原本盈利笔数:    {blocked_wins} ({blocked_wins/blocked_count*100:.1f}%)" if blocked_count > 0 else "  原本盈利笔数: 0")
    print(f"  原本亏损笔数:    {blocked_losses} ({blocked_losses/blocked_count*100:.1f}%)" if blocked_count > 0 else "  原本亏损笔数: 0")
    print(f"  原本合计盈亏:    {blocked_pnl:+.2f}")
    print(f"  亏损比例:        {blocked_loss_ratio:.2%}")
    if blocked_count > 0:
        if blocked_loss_ratio > 0.6:
            verdict = "PASS — 亏损比例显著高于盈利（>60%），regime 过滤有效拦掉坏交易"
        elif blocked_loss_ratio > 0.5:
            verdict = "WEAK — 亏损比例略高于盈利（50-60%），方向对但不显著"
        else:
            verdict = "FAIL — 亏损比例不高于盈利，regime 判据需重新设计"
        print(f"  验收 E-2 判定:   {verdict}")

    # 保存报告
    out_path = PROJECT_ROOT / "outputs" / "backtest" / "stepE_regime_filter_comparison.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "step": "stepE_regime_filter",
            "description": "Stage E: regime NO_TRADE filter vs baseline",
            "stocks": len(BASELINE_CODES),
            "date_range": [START_DATE, END_DATE],
            "baseline_overall": {**baseline_overall, "win_rate": bl_wr_overall},
            "filtered_overall": {**filtered_overall, "win_rate": ft_wr_overall},
            "blocked_trades_stats": {
                "count": blocked_count,
                "wins": blocked_wins,
                "losses": blocked_losses,
                "total_pnl": round(blocked_pnl, 2),
                "loss_ratio": round(blocked_loss_ratio, 4),
            },
            "blocked_dates_count": blocked_dates_count,
            "total_days": total_days,
            "per_stock": per_stock_comparison,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
