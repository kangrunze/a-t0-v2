#!/usr/bin/env python3
"""
OOS步骤3 — 调参样本 vs 样本外 分组对比
=====================================
把步骤2筛出的36只股票分成两类：
  - "调参用过"（训练集重叠，8只）：J0-J4 调参时直接用了
  - "调参没用过"（OOS，28只）：F5 里的独立样本，J0-J4 把它们当训练集用了

分别统计两类的 net_pnl / payoff / CE（捕获效率）。
如果 OOS 组明显弱于训练组，说明过拟合存在。

输入：
  - outputs/oos_validation/step1_sample_overlap.json（步骤1的清单）
  - outputs/backtest/batch_summary_oos_step2_full.json（步骤2的汇总）
  - outputs/backtest/{code}_oos_step2_full_*_report.json（步骤2的per-stock报告，用于CE）
  - D:\\project\\data\\zz500_5min\\{code}.json（原始K线，用于daily_hl）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "archive" / "diag"))

from at0.paths import ZZ500_5MIN_DIR

# 复用 J1 的 CE 计算（已在实战中验证过）
from diag_stepJ1_capture_efficiency import compute_daily_hl, compute_capture_efficiency

STEP1_JSON = PROJECT_ROOT / "outputs" / "oos_validation" / "step1_sample_overlap.json"
STEP2_SUMMARY = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_oos_step2_full.json"
REPORT_GLOB = "*_oos_step2_full_*_report.json"
OUTPUT_JSON = PROJECT_ROOT / "outputs" / "oos_validation" / "step3_group_comparison.json"


def load_step1() -> dict:
    with open(STEP1_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_step2_summary() -> dict:
    with open(STEP2_SUMMARY, "r", encoding="utf-8") as f:
        return json.load(f)


def find_report(code: str) -> Path | None:
    """找步骤2为该股票生成的 report.json"""
    pattern = f"{code}_oos_step2_full_*_report.json"
    matches = list((PROJECT_ROOT / "outputs" / "backtest").glob(pattern))
    return matches[0] if matches else None


def load_stock_bars(code: str) -> dict:
    """加载原始K线用于 daily_hl"""
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def compute_stock_ce(code: str) -> dict:
    """计算单股的 CE 统计（同日 + 跨日）。"""
    report_path = find_report(code)
    if report_path is None:
        return {"available": False, "reason": "report.json not found"}
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    daily_bars = load_stock_bars(code)
    if not daily_bars:
        return {"available": False, "reason": "daily_bars empty"}
    daily_hl = compute_daily_hl(daily_bars)
    # compute_capture_efficiency 期望 result（含 daily_results）
    # report.json 顶层就有 daily_results
    ce_data = compute_capture_efficiency(report, daily_hl)
    # J1 实际返回的 key: same_day_ce_list / cross_day_ce_list / total_pairs
    same_day = ce_data.get("same_day_ce_list", [])
    cross_day = ce_data.get("cross_day_ce_list", [])
    all_ce = same_day + cross_day
    return {
        "available": True,
        "n_pairs": ce_data.get("total_pairs", len(all_ce)),
        "same_day_n": len(same_day),
        "cross_day_n": len(cross_day),
        "ce_mean": round(mean(all_ce), 4) if all_ce else None,
        "ce_median": round(median(all_ce), 4) if all_ce else None,
        "same_day_ce_mean": round(mean(same_day), 4) if same_day else None,
        "cross_day_ce_mean": round(mean(cross_day), 4) if cross_day else None,
    }


def analyze_group(per_stock: list[dict], label: str) -> dict:
    """统计一组股票的 net_pnl/payoff/CE。"""
    if not per_stock:
        return {"label": label, "n": 0}
    pnls = [s.get("net_pnl_with_unrealized", s.get("net_pnl", 0)) for s in per_stock]
    total_paired = sum(s.get("paired_trades", 0) for s in per_stock)
    total_wins = sum(s.get("win_trades", 0) for s in per_stock)
    total_losses = sum(s.get("loss_trades", 0) for s in per_stock)
    total_win_pnl = sum(s.get("avg_win", 0) * s.get("win_trades", 0) for s in per_stock)
    total_loss_pnl = sum(s.get("avg_loss", 0) * s.get("loss_trades", 0) for s in per_stock)
    avg_win = total_win_pnl / total_wins if total_wins else 0
    avg_loss = total_loss_pnl / total_losses if total_losses else 0
    payoff = avg_win / abs(avg_loss) if avg_loss != 0 else 0
    win_rate = total_wins / total_paired if total_paired else 0
    profitable = sum(1 for p in pnls if p > 0)
    return {
        "label": label,
        "n": len(per_stock),
        "total_paired_trades": total_paired,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(payoff, 4),
        "net_pnl_total": round(sum(pnls), 2),
        "net_pnl_mean": round(mean(pnls), 2),
        "net_pnl_median": round(median(pnls), 2),
        "profitable_stocks": profitable,
        "profitable_ratio": round(profitable / len(per_stock), 4),
    }


def main():
    print("=" * 90)
    print("OOS步骤3 — 调参样本 vs 样本外 分组对比")
    print("=" * 90)

    step1 = load_step1()
    step2 = load_step2_summary()

    train_codes = set(step1["train_in_36"])   # 8只：训练集重叠
    oos_codes = set(step1["oos_in_36"])       # 28只：F5的OOS
    print(f"\n分组（来自步骤1）：")
    print(f"  调参用过(训练集重叠): {len(train_codes)} 只 = {sorted(train_codes)}")
    print(f"  调参没用过(OOS):      {len(oos_codes)} 只 = {sorted(oos_codes)}")

    per_stock = step2["per_stock"]
    print(f"\n步骤2回测股票数: {len(per_stock)}")

    # 分组
    train_group = [s for s in per_stock if s["code"] in train_codes]
    oos_group = [s for s in per_stock if s["code"] in oos_codes]
    unmatched = [s for s in per_stock
                 if s["code"] not in train_codes and s["code"] not in oos_codes]
    if unmatched:
        print(f"\n[WARN] {len(unmatched)} 只未匹配到步骤1清单: {[s['code'] for s in unmatched]}")

    # 基础指标对比
    g_train = analyze_group(train_group, f"训练组(调参用过, {len(train_group)}只)")
    g_oos = analyze_group(oos_group, f"OOS组(调参没用过, {len(oos_group)}只)")
    g_all = analyze_group(per_stock, f"全部(36只)")

    print(f"\n{'=' * 90}")
    print(f"基础指标对比")
    print(f"{'=' * 90}")
    print(f"\n{'组别':<30s} {'数量':>5s} {'配对':>6s} {'胜率':>7s} {'payoff':>8s} {'净盈亏':>14s} {'均值':>12s} {'中位':>12s} {'盈利占比':>8s}")
    print(f"{'-'*30} {'-'*5} {'-'*6} {'-'*7} {'-'*8} {'-'*14} {'-'*12} {'-'*12} {'-'*8}")
    for g in [g_all, g_train, g_oos]:
        print(f"{g['label']:<30s} {g['n']:>5d} {g['total_paired_trades']:>6d} "
              f"{g['win_rate']*100:>6.1f}% {g['payoff_ratio']:>8.4f} "
              f"{g['net_pnl_total']:>+14.2f} {g['net_pnl_mean']:>+12.2f} "
              f"{g['net_pnl_median']:>+12.2f} {g['profitable_ratio']*100:>7.1f}%")

    # CE 计算（逐股）
    print(f"\n{'=' * 90}")
    print(f"捕获效率（CE）计算 — 从 report.json + 原始K线现算")
    print(f"{'=' * 90}")

    train_ce = []
    oos_ce = []
    print(f"\n逐股计算 CE（复用 J1 的 compute_capture_efficiency）...")
    for i, code in enumerate(sorted(train_codes | oos_codes), 1):
        ce = compute_stock_ce(code)
        group = "训练" if code in train_codes else "OOS "
        if ce.get("available"):
            n_pairs = ce.get("n_pairs") or 0
            ce_mean = ce.get("ce_mean")
            ce_med = ce.get("ce_median")
            ce_mean_s = f"{ce_mean:.4f}" if ce_mean is not None else "N/A"
            ce_med_s = f"{ce_med:.4f}" if ce_med is not None else "N/A"
            print(f"  [{i:2d}] {code} [{group}] pairs={n_pairs:4d} "
                  f"CE_mean={ce_mean_s} CE_med={ce_med_s} "
                  f"(same_day={ce.get('same_day_n',0)}, cross_day={ce.get('cross_day_n',0)})")
            if code in train_codes:
                train_ce.append(ce)
            else:
                oos_ce.append(ce)
        else:
            print(f"  [{i:2d}] {code} [{group}] CE 不可用: {ce.get('reason')}")

    # CE 汇总
    def ce_summary(ces: list[dict], label: str) -> dict:
        if not ces:
            return {"label": label, "n": 0, "n_stocks": 0, "total_pairs": 0,
                    "ce_mean_of_means": None, "ce_median_of_medians": None,
                    "ce_pooled_mean": None}
        means = [c["ce_mean"] for c in ces if c.get("ce_mean") is not None]
        medians = [c["ce_median"] for c in ces if c.get("ce_median") is not None]
        total_pairs = sum(c.get("n_pairs", 0) or 0 for c in ces)
        pooled_num = sum(c["ce_mean"] * c["n_pairs"] for c in ces
                         if c.get("ce_mean") is not None and c.get("n_pairs"))
        return {
            "label": label,
            "n_stocks": len(ces),
            "total_pairs": total_pairs,
            "ce_mean_of_means": round(mean(means), 4) if means else None,
            "ce_median_of_medians": round(mean(medians), 4) if medians else None,
            "ce_pooled_mean": round(pooled_num / total_pairs, 4) if total_pairs and pooled_num else None,
        }

    ce_train = ce_summary(train_ce, f"训练组CE({len(train_ce)}只)")
    ce_oos = ce_summary(oos_ce, f"OOS组CE({len(oos_ce)}只)")
    ce_all = ce_summary(train_ce + oos_ce, f"全部CE(36只)")

    print(f"\n{'=' * 90}")
    print(f"CE 汇总对比")
    print(f"{'=' * 90}")
    print(f"\n{'组别':<25s} {'股票数':>6s} {'总配对':>8s} {'CE均值(均值)':>14s} {'CE中位(均值)':>14s} {'CE池化均值':>12s}")
    print(f"{'-'*25} {'-'*6} {'-'*8} {'-'*14} {'-'*14} {'-'*12}")
    for c in [ce_all, ce_train, ce_oos]:
        mom = c.get("ce_mean_of_means")
        mpm = c.get("ce_median_of_medians")
        pooled = c.get("ce_pooled_mean")
        mom_s = f"{mom:>14.4f}" if mom is not None else f"{'N/A':>14s}"
        mpm_s = f"{mpm:>14.4f}" if mpm is not None else f"{'N/A':>14s}"
        pooled_s = f"{pooled:>12.4f}" if pooled is not None else f"{'N/A':>12s}"
        print(f"{c['label']:<25s} {c['n_stocks']:>6d} {c['total_pairs']:>8d} {mom_s} {mpm_s} {pooled_s}")

    # 过拟合判断
    print(f"\n{'=' * 90}")
    print(f"过拟合判断")
    print(f"{'=' * 90}")
    print(f"\n  指标对比（训练组 vs OOS组）：")
    if g_train["n"] and g_oos["n"]:
        pnl_diff = (g_oos["net_pnl_mean"] - g_train["net_pnl_mean"]) / abs(g_train["net_pnl_mean"]) * 100 if g_train["net_pnl_mean"] != 0 else 0
        payoff_diff = (g_oos["payoff_ratio"] - g_train["payoff_ratio"]) / g_train["payoff_ratio"] * 100 if g_train["payoff_ratio"] != 0 else 0
        ce_diff_pct = 0
        ce_train_val = ce_train.get("ce_pooled_mean")
        ce_oos_val = ce_oos.get("ce_pooled_mean")
        if ce_train_val and ce_oos_val and ce_train_val != 0:
            ce_diff_pct = (ce_oos_val - ce_train_val) / ce_train_val * 100
        print(f"  净盈亏均值: 训练={g_train['net_pnl_mean']:+.2f}  OOS={g_oos['net_pnl_mean']:+.2f}  差异={pnl_diff:+.1f}%")
        print(f"  payoff:     训练={g_train['payoff_ratio']:.4f}     OOS={g_oos['payoff_ratio']:.4f}     差异={payoff_diff:+.1f}%")
        ce_t_s = f"{ce_train_val:.4f}" if ce_train_val is not None else "N/A"
        ce_o_s = f"{ce_oos_val:.4f}" if ce_oos_val is not None else "N/A"
        ce_d_s = f"{ce_diff_pct:+.1f}%" if ce_train_val and ce_oos_val else "N/A"
        print(f"  CE池化均值: 训练={ce_t_s}     OOS={ce_o_s}     差异={ce_d_s}")
        print(f"  盈利占比:   训练={g_train['profitable_ratio']*100:.1f}%       OOS={g_oos['profitable_ratio']*100:.1f}%")

        print(f"\n  判断标准：")
        criteria = [
            ("OOS净盈亏均值为正", g_oos["net_pnl_mean"] > 0),
            ("OOS盈利占比 ≥ 75%", g_oos["profitable_ratio"] >= 0.75),
            ("OOS payoff ≥ 训练的80%", g_oos["payoff_ratio"] >= g_train["payoff_ratio"] * 0.8),
            ("OOS CE ≥ 训练的80%",
             ce_oos_val >= ce_train_val * 0.8 if (ce_train_val and ce_oos_val) else True),
        ]
        for label, passed in criteria:
            print(f"    [{'✓' if passed else '✗'}] {label}")
        passed_count = sum(1 for _, p in criteria if p)
        print(f"\n  通过 {passed_count}/{len(criteria)} 项")

        if passed_count == len(criteria):
            print(f"\n  结论: OOS组表现与训练组相当，未发现明显过拟合。")
            print(f"        当前配置组合可正式定案。")
        elif passed_count >= 2:
            print(f"\n  结论: OOS组略有衰减但整体可用，轻微过拟合风险。")
            print(f"        建议关注未通过的指标，但配置可保留。")
        else:
            print(f"\n  结论: OOS组明显弱于训练组，过拟合风险显著。")
            print(f"        建议收紧/简化参数组合（尤其J2的0.02/70可能对36只过拟合）。")

    # 保存
    output = {
        "step": "oos_step3_group_comparison",
        "groups": {
            "train": g_train,
            "oos": g_oos,
            "all": g_all,
        },
        "ce": {
            "train": ce_train,
            "oos": ce_oos,
            "all": ce_all,
        },
        "per_stock_ce": {
            "train": train_ce,
            "oos": oos_ce,
        },
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
