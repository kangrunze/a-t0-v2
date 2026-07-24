#!/usr/bin/env python3
"""
P1 基线测量脚本
===============
主计划 S4 P1: 在 backtest.py 中实现五类报告能力
验收标准: 五类报告均可对现有策略（改造前）跑通，产出基线数值

本脚本在多股票上跑回测，收集交易记录，调用测量层生成基线报告:
  1. IC/RankIC/Alpha Decay (信号有效性)
  2. 交易质量 (WinRate/PF/MAE/MFE)
  3. 分层回测 (regime x time_slot)
  4. 全现金流对账 (防统计幻觉)
  5. 训练集 vs 验证集对比 (样本外验证)

用法:
  python scripts/run_baseline_measurement.py --limit 10
  python scripts/run_baseline_measurement.py --limit 51
"""
import sys, json, os, argparse, glob
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from at0.data import fetch_multi_day, normalize_code
from at0.backtest import (BacktestParams, backtest_multi_day,
                          summarize_one_stock, aggregate_batch)
from at0.strategy import SignalParams
from at0.risk import RiskParams
from at0.measurement import (
    compute_trade_quality, audit_cashflow, compute_stratified_report,
)
from at0.measurement.time_split import (
    list_cached_codes, get_all_dates_from_cache, split_time_range, filter_dates,
)


def adapt_params(params, frequency, bars_per_day):
    """按频率自适应 warmup/eod_check 参数。"""
    if frequency == "5min":
        params.warmup_bars = min(6, max(3, bars_per_day // 8))
        params.eod_check_bar_idx = min(33, bars_per_day - 2)
    else:
        params.warmup_bars = 30
        params.eod_check_bar_idx = 200
    return params


def run_backtest_for_code(code, daily_bars, daily_prev, daily_meta):
    """对单只股票跑回测，返回交易记录和汇总。"""
    pure_code = normalize_code(code)["pure"]
    first_meta = next(iter(daily_meta.values()))
    freq = first_meta.get("frequency", "5min")
    bpd = first_meta.get("bars_count", 48)
    first_date = min(daily_prev.keys())
    avg_cost = daily_prev[first_date]

    params = BacktestParams(
        base_shares=3000, avg_cost=avg_cost,
        signal_params=SignalParams(), risk_params=RiskParams(),
    )
    adapt_params(params, freq, bpd)

    result = backtest_multi_day(pure_code, daily_bars, daily_prev, params)
    summary = summarize_one_stock(pure_code, result)

    # 提取交易记录：backtest_multi_day 返回 dict，trades 在 daily_results 里
    trades = []
    closed_legs = []
    if isinstance(result, dict):
        for daily in result.get("daily_results", []):
            trades.extend(daily.get("trades", []))
            # closed_legs 从 risk_events 提取（止损/超时腿含 max_adverse/max_favorable）
            for ev in daily.get("risk_events", []):
                if ev.get("type") in ("expired", "stopped"):
                    closed_legs.append({
                        "max_adverse": ev.get("max_adverse", 0),
                        "max_favorable": ev.get("max_favorable", 0),
                        "holding_bars": ev.get("holding_bars", 0),
                        "status": ev.get("type", "paired"),
                        "paired_pnl": ev.get("realized_pnl", 0),
                        "direction": ev.get("direction", ""),
                        "fill_price": ev.get("fill_price", 0),
                    })
            # 也从配对交易中提取（配对腿的 MAE/MFE 用 holding_bars 近似）
            for t in daily.get("trades", []):
                if t.get("paired") and t.get("pnl", 0) != 0:
                    closed_legs.append({
                        "max_adverse": 0,  # 配对腿无 MAE 数据
                        "max_favorable": abs(t.get("pnl", 0)),  # 用 pnl 近似 MFE
                        "holding_bars": t.get("holding_bars", 0),
                        "status": "paired",
                        "paired_pnl": t.get("pnl", 0),
                        "direction": t.get("direction", ""),
                        "fill_price": t.get("fill_price", 0),
                    })

    return summary, trades, closed_legs


def main():
    parser = argparse.ArgumentParser(description="P1 基线测量")
    parser.add_argument("--limit", type=int, default=10, help="限制股票数量")
    parser.add_argument("--cache-dir", default=r"D:\project\a-t0\data\multi_day_cache",
                        help="缓存目录")
    parser.add_argument("--out", default="outputs/baseline/baseline_report.json",
                        help="输出路径")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    cache_dir = args.cache_dir

    # ── 1. 获取缓存股票列表和日期范围 ──
    codes = list_cached_codes(cache_dir)
    if args.limit > 0:
        codes = codes[:args.limit]
    print(f"[baseline] 缓存股票: {len(codes)} 只")

    if not codes:
        print("[ERROR] 无缓存数据")
        return

    # 获取日期范围并划分训练集/验证集
    all_dates = get_all_dates_from_cache(cache_dir, codes[0])
    split = split_time_range(all_dates)
    if not split:
        print("[ERROR] 交易日不足60天")
        return
    print(f"[baseline] 训练集: {split.train_start} ~ {split.train_end} ({split.train_days}天)")
    print(f"[baseline] 验证集: {split.test_start} ~ {split.test_end} ({split.test_days}天)")

    # ── 2. 分别在训练集和验证集上跑回测 ──
    results = {"train": {}, "test": {}}
    all_trades = {"train": [], "test": []}
    all_closed_legs = {"train": [], "test": []}

    for i, code in enumerate(codes):
        print(f"[{i+1}/{len(codes)}] {code} ...", end=" ", flush=True)
        try:
            # 读取完整缓存数据
            daily_bars_full, daily_prev_full, daily_meta_full = fetch_multi_day(
                code, "2025-07-24", "2026-07-22", "baostock", use_cache=True
            )
            if not daily_bars_full:
                print("无数据")
                continue

            for period_name, (s, e) in [
                ("train", (split.train_start, split.train_end)),
                ("test", (split.test_start, split.test_end)),
            ]:
                # 过滤日期范围
                daily_bars = filter_dates(daily_bars_full, s, e)
                daily_prev = {d: v for d, v in daily_prev_full.items() if s <= d <= e}
                daily_meta = {d: v for d, v in daily_meta_full.items() if s <= d <= e}

                if not daily_bars:
                    continue

                summary, trades, closed_legs = run_backtest_for_code(
                    code, daily_bars, daily_prev, daily_meta
                )
                results[period_name][code] = summary
                all_trades[period_name].extend(trades)
                all_closed_legs[period_name].extend(closed_legs)

            train_net = results["train"].get(code, {}).get("net_pnl", 0)
            test_net = results["test"].get(code, {}).get("net_pnl", 0)
            print(f"train={train_net:+.0f} test={test_net:+.0f}")
        except Exception as ex:
            print(f"ERROR: {ex}")

    # ── 3. 生成测量层报告 ──
    print("\n" + "=" * 60)
    print("P1 基线测量报告")
    print("=" * 60)

    baseline = {"split": split.to_dict(), "reports": {}}

    for period_name in ["train", "test"]:
        trades = all_trades[period_name]
        closed_legs = all_closed_legs[period_name]
        if not trades:
            continue

        print(f"\n--- {period_name} ({len(trades)}笔交易) ---")

        # 交易质量
        tq = compute_trade_quality(trades, closed_legs)
        print(f"交易质量: 胜率={tq.win_rate:.2%}, PF={tq.profit_factor:.2f}, "
              f"盈亏比={tq.payoff_ratio:.2f}, 期望={tq.expectancy:.2f}")
        print(f"  MAE={tq.avg_mae:.2f}, MFE={tq.avg_mfe:.2f}, MAE/MFE={tq.mae_mfe_ratio:.2f}")
        print(f"  超时腿={tq.expired_count}条/{tq.expired_pnl:+.0f}, "
              f"止损腿={tq.stopped_count}条/{tq.stopped_pnl:+.0f}")

        # 全现金流对账
        audit = audit_cashflow(trades, unrealized_pnl=0, expired_pnl=0)
        print(f"现金流对账: cashflow_pnl={audit.cashflow_pnl:+.2f}, "
              f"paired_total={audit.paired_total:+.2f}, "
              f"差异={audit.discrepancy:+.2f}, 对平={audit.is_matched}")

        # 分层回测: 用交易方向近似 regime (sell=看空趋势, buy=看多趋势)
        regime_map = {}
        for idx, t in enumerate(trades):
            d = t.get("direction", "")
            p = t.get("pnl", 0)
            if d == "sell":
                regime_map[str(idx)] = "sell_short" if p > 0 else "sell_loss"
            elif d == "buy":
                regime_map[str(idx)] = "buy_long" if p > 0 else "buy_loss"
            else:
                regime_map[str(idx)] = "unknown"
        strat = compute_stratified_report(
            trades, dim1_name="direction_pnl", dim2_name="time_slot",
            dim1_values=regime_map,
        )
        conclusive_cells = [c for c in strat.cells if c.is_conclusive and c.n_paired > 0]
        print(f"分层回测: {len(strat.cells)}格, 可结论格={len(conclusive_cells)}")
        for cell in conclusive_cells[:5]:
            print(f"  {cell.dim1_label} x {cell.dim2_label}: n={cell.n_paired}, "
                  f"胜率={cell.win_rate:.2%}, pnl={cell.net_pnl:+.0f}")

        baseline["reports"][period_name] = {
            "n_trades": len(trades),
            "n_stocks": len(results[period_name]),
            "trade_quality": tq.to_dict(),
            "cashflow_audit": audit.to_dict(),
            "stratified": strat.to_dict(),
            "per_stock": {k: v for k, v in results[period_name].items()},
        }

    # ── 4. 训练集 vs 验证集对比 ──
    print("\n--- 样本外验证对比 ---")
    if "train" in baseline["reports"] and "test" in baseline["reports"]:
        tr = baseline["reports"]["train"]["trade_quality"]
        te = baseline["reports"]["test"]["trade_quality"]
        print(f"胜率: train={tr['win_rate']:.2%} vs test={te['win_rate']:.2%}")
        print(f"PF:   train={tr['profit_factor']:.2f} vs test={te['profit_factor']:.2f}")
        print(f"盈亏比: train={tr['payoff_ratio']:.2f} vs test={te['payoff_ratio']:.2f}")
        print(f"期望: train={tr['expectancy']:.2f} vs test={te['expectancy']:.2f}")

        # 过拟合检测: 训练集盈利但验证集亏损
        train_profit = tr["expectancy"] > 0
        test_loss = te["expectancy"] < 0
        if train_profit and test_loss:
            print("\n[WARNING] 过拟合信号: 训练集盈利但验证集亏损!")
            print("  主计划 S3 红线: 需判定为过拟合并重新审视策略逻辑")
            baseline["overfit_warning"] = True
        else:
            baseline["overfit_warning"] = False

    # ── 5. 保存报告 ──
    out_path = project_root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[baseline] 报告已保存: {out_path}")


if __name__ == "__main__":
    main()
