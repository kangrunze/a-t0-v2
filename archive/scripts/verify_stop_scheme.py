#!/usr/bin/env python3
"""
验证脚本：佣金万1 + 止损结构修复（"小赚多次、大亏几次"）
========================================================
对比三组配置（51 只股票，train/test 时间分割，与 baseline_report_51 一致）：
  A 基线      : 佣金万2.5 + 旧止损（固定1.5% / trailing 0.5 / 无激活门槛）
  B 仅降佣金  : 佣金万1   + 旧止损
  C 完整方案  : 佣金万1   + 新止损（固定0.8% / trailing 0.5 / 激活门槛0.5%）

另在训练集上对 C 的 (stop_loss_ratio × trailing_activation_pct) 做小网格，
确认所选参数处于平台区而非孤峰。

用法:
  python scripts/verify_stop_scheme.py [--grid] [--out outputs/verify_stop_scheme.json]

数据：直接读 data/multi_day_cache/{code}_2025-07-24_2026-07-22.json（离线）。
"""
import sys
import json
import argparse
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.backtest import BacktestParams, backtest_multi_day, extract_trades
from at0.strategy import SignalParams
from at0.risk import RiskParams, CostModel

CACHE_DIR = PROJECT_ROOT / "data" / "multi_day_cache"
CACHE_SUFFIX = "_2025-07-24_2026-07-22.json"
TRAIN_END = "2026-01-19"   # 与 baseline_report_51 的 split 一致


def load_cached(code_file: Path):
    d = json.loads(code_file.read_text(encoding="utf-8"))
    return d["code"], d["daily_bars"], d["daily_prev_closes"]


def split_days(daily_bars: dict, daily_prev: dict, mode: str):
    """按 TRAIN_END 分割交易日。mode: train/test/all"""
    if mode == "train":
        days = [d for d in daily_bars if d <= TRAIN_END]
    elif mode == "test":
        days = [d for d in daily_bars if d > TRAIN_END]
    else:
        days = list(daily_bars)
    bars = {d: daily_bars[d] for d in sorted(days)}
    prev = {d: daily_prev[d] for d in sorted(days) if d in daily_prev}
    return bars, prev


def make_params(commission: float, stop_loss: float, trailing: float,
                activation: float) -> BacktestParams:
    return BacktestParams(
        commission_rate=commission,
        cost_model=CostModel(commission_rate=commission),
        base_shares=3000,
        signal_params=SignalParams(),
        risk_params=RiskParams(),
        stop_loss_ratio=stop_loss,
        trailing_ratio=trailing,
        trailing_activation_pct=activation,
    )


def adapt_params(params: BacktestParams, freq: str, bars_per_day: int):
    if freq == "5min":
        params.warmup_bars = min(6, max(3, bars_per_day // 8))
        params.eod_check_bar_idx = min(33, bars_per_day - 2)
    else:
        params.warmup_bars = 30
        params.eod_check_bar_idx = 200
    return params


def run_config(stock_files: list, mode: str, commission: float,
               stop_loss: float, trailing: float, activation: float) -> dict:
    """跑一组配置，返回聚合指标（口径与 trade_quality 对齐）。"""
    all_pnls, net_w_u_sum = [], 0.0
    n_expired, expired_pnl_sum = 0, 0.0
    n_stopped = 0
    holding_bars_all = []
    for f in stock_files:
        code, daily_bars, daily_prev = load_cached(f)
        bars, prev = split_days(daily_bars, daily_prev, mode)
        if not bars:
            continue
        first_day_bars = next(iter(bars.values()))
        # 频率推断：09:35 结尾为 5min
        freq = "5min" if len(first_day_bars) <= 60 else "1min"
        first_date = min(prev.keys())
        params = make_params(commission, stop_loss, trailing, activation)
        params.avg_cost = prev[first_date]
        adapt_params(params, freq, len(first_day_bars))
        result = backtest_multi_day(code, bars, prev, params)
        net_w_u_sum += result.get("net_pnl_with_unrealized", 0.0)
        n_expired += result.get("expired_legs_count", 0)
        expired_pnl_sum += result.get("expired_legs_real_pnl", 0.0)
        for t in extract_trades(result):
            if t.get("paired") and t.get("pnl") is not None:
                all_pnls.append(t["pnl"])
                if t.get("holding_bars") is not None:
                    holding_bars_all.append(t["holding_bars"])
                if t.get("status") == "stopped":
                    n_stopped += 1
    wins = [p for p in all_pnls if p > 0]
    losses = [p for p in all_pnls if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    avg_win = gp / len(wins) if wins else 0.0
    avg_loss = gl / len(losses) if losses else 0.0
    n = len(all_pnls)
    return {
        "paired_trades": n,
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(avg_win / avg_loss, 4) if avg_loss else 0.0,
        "profit_factor": round(gp / gl, 4) if gl else 0.0,
        "expectancy": round((gp - gl) / n, 2) if n else 0.0,
        "max_single_loss": round(min(all_pnls), 2) if all_pnls else 0.0,
        "median_holding_bars": median(holding_bars_all) if holding_bars_all else 0,
        "stopped_count": n_stopped,
        "expired_count": n_expired,
        "expired_pnl": round(expired_pnl_sum, 2),
        "net_pnl_with_unrealized": round(net_w_u_sum, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true", help="在训练集上跑参数小网格")
    ap.add_argument("--out", default="outputs/verify_stop_scheme.json")
    args = ap.parse_args()

    stock_files = sorted(CACHE_DIR.glob(f"*{CACHE_SUFFIX}"))
    print(f"[verify] {len(stock_files)} 只股票（{CACHE_SUFFIX}）")

    configs = {
        "A_baseline_wan2.5_oldstop": dict(commission=0.00025, stop_loss=0.015, trailing=0.5, activation=0.0),
        "B_wan1_oldstop":            dict(commission=0.0001,  stop_loss=0.015, trailing=0.5, activation=0.0),
        "C_wan1_newstop":            dict(commission=0.0001,  stop_loss=0.008, trailing=0.5, activation=0.005),
    }

    report = {"split": {"train_end": TRAIN_END}, "configs": {}}
    for name, cfg in configs.items():
        report["configs"][name] = {"params": cfg}
        for mode in ("train", "test"):
            print(f"[run] {name} / {mode} ...", flush=True)
            report["configs"][name][mode] = run_config(stock_files, mode, **cfg)
            r = report["configs"][name][mode]
            print(f"      paired={r['paired_trades']} win={r['win_rate']*100:.1f}% "
                  f"payoff={r['payoff_ratio']:.2f} PF={r['profit_factor']:.3f} "
                  f"exp={r['expectancy']:+.2f} net_w_u={r['net_pnl_with_unrealized']:+.0f}")

    if args.grid:
        report["grid_train"] = []
        for sl in (0.006, 0.008, 0.010):
            for act in (0.003, 0.005, 0.008):
                print(f"[grid] stop={sl} act={act} ...", flush=True)
                r = run_config(stock_files, "train", commission=0.0001,
                               stop_loss=sl, trailing=0.5, activation=act)
                row = {"stop_loss": sl, "activation": act, **r}
                report["grid_train"].append(row)
                print(f"      PF={r['profit_factor']:.3f} exp={r['expectancy']:+.2f} "
                      f"payoff={r['payoff_ratio']:.2f} net_w_u={r['net_pnl_with_unrealized']:+.0f}")

    out = PROJECT_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] 报告已写入 {out}")


if __name__ == "__main__":
    main()
