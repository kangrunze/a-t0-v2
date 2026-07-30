#!/usr/bin/env python3
"""
Stage G2-2b: 动态止损 A/B 测试（单向收紧版）
============================================
G2-2 双向缩放（max_scale=2.0）失败：高振幅股止损被放宽后单笔亏损放大，
avg_loss 差距从 67.34 扩大到 167.80。K0 已证明高 ATR 分桶无更强延续优势
去补偿放宽的止损，"放宽"方向本身缺乏依据。

本轮只改一个参数：max_scale 从 2.0 → 1.0（振幅≥中位数的股票维持 0.002 不变，
只对振幅<中位数的股票按比例收紧），min_scale=0.5 不变。

在36只振幅筛选股票池上对比：
  A组（基准）：固定止损 stop_loss_ratio=0.002（现状）
  B组（单向收紧）：dynamic_stop_enabled=True，max_scale=1.0，min_scale=0.5

其余参数（trailing_ratio=0.2/cd=24/mh=24/J2开启）不变。
MR模式不参与（确保 is_mean_reversion=False）。
"""
from __future__ import annotations

import json
import sys
import time
import statistics
from pathlib import Path
from collections import Counter, defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import run_zz500_single, filter_codes_by_amplitude
from at0.paths import ZZ500_5MIN_DIR

# 振幅筛选后的36只
POOL_36 = [
    "001221", "001309", "001389", "002261", "300339", "300475", "300548",
    "300570", "300620", "300735", "300757", "300972", "301526", "301536",
    "301606", "301611", "600602", "601099", "603000", "603119", "603175",
    "603728", "688166", "688213", "688318", "688322", "688331", "688361",
    "688411", "688498", "688582", "688615", "688629", "688692", "688702",
    "688709",
]

START = "2025-07-28"
END = "2026-07-22"
AMP_THRESHOLD = 0.0494  # 60日日均振幅下限（与生产配置一致）
AMP_WINDOW = 60

OUTPUT_DIR = Path(r"d:\project\a-t0-v2\outputs\backtest")


def compute_amplitude_table():
    """预计算36只股票的60日日均振幅 + 池子中位数。"""
    print("[amp] 计算36只股票的60日日均振幅...")
    passed, filtered = filter_codes_by_amplitude(
        codes=POOL_36, data_dir=ZZ500_5MIN_DIR,
        start_date=START, end_date=END,
        threshold=0.0,  # 设为0，让所有股票都"通过"，我们拿全部振幅
        window=AMP_WINDOW,
    )
    # filter_codes_by_amplitude 返回的 filtered 只包含未通过的（带振幅）
    # 我们需要所有股票的振幅，重新计算
    amp_table = {}
    all_amps = []
    for code in POOL_36:
        path = ZZ500_5MIN_DIR / f"{code}.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        daily_bars = d.get("daily_bars", {})
        if not daily_bars:
            continue
        sorted_dates = sorted(daily_bars.keys())
        in_range = [d for d in sorted_dates if START <= d <= END]
        use_dates = in_range[:AMP_WINDOW] if len(in_range) >= AMP_WINDOW else in_range
        amps = []
        prev_close = None
        for date in use_dates:
            bars = daily_bars[date]
            if not bars:
                continue
            high = max(b["high"] for b in bars)
            low = min(b["low"] for b in bars)
            if prev_close and prev_close > 0:
                amps.append((high - low) / prev_close)
            prev_close = bars[-1]["close"]
        if amps:
            avg_amp = sum(amps) / len(amps)
            amp_table[code] = avg_amp
            all_amps.append(avg_amp)

    median_amp = statistics.median(all_amps) if all_amps else 0.06
    print(f"[amp] 成功计算 {len(amp_table)} 只股票振幅")
    print(f"[amp] 池子振幅中位数 = {median_amp:.4f} ({median_amp*100:.2f}%)")
    print(f"[amp] 振幅范围: {min(all_amps)*100:.2f}% ~ {max(all_amps)*100:.2f}%")
    return amp_table, median_amp


def run_group(group_name, amp_table, median_amp, use_dynamic):
    """跑一组（A或B），返回每只股票的结果。"""
    print(f"\n{'='*70}")
    print(f"[{group_name}] dynamic_stop={'ON' if use_dynamic else 'OFF'}")
    print(f"{'='*70}")

    bp = {
        "stop_loss_ratio": 0.002,  # 基准止损（与yaml一致）
    }
    if use_dynamic:
        bp["dynamic_stop_enabled"] = True
        bp["amplitude_table"] = amp_table
        bp["pool_median_amplitude"] = median_amp
        bp["dynamic_stop_min_scale"] = 0.5
        bp["dynamic_stop_max_scale"] = 1.0

    # 强制 TF 模式（yaml 当前是 mean_reversion，G2 只在 TF 模式下验证动态止损）
    sp = {"strategy_mode": "trend_following"}

    results = []
    for code in POOL_36:
        t0 = time.time()
        tag = f"g2b_{group_name}_{code}"
        override = {"bp": bp, "sp": sp}
        r = run_zz500_single(
            code=code, start_date=START, end_date=END,
            data_dir=ZZ500_5MIN_DIR, base_shares=3000,
            tag=tag, params_override=override,
        )
        elapsed = time.time() - t0
        if not r:
            print(f"  {code}: ERROR")
            continue
        r["code"] = code
        r["amplitude"] = amp_table.get(code, 0)
        r["elapsed"] = elapsed
        print(f"  {code}: T={r.get('total_trades',0):>3} 净={r.get('net_pnl',0):>+8.0f} "
              f"amp={r['amplitude']*100:.2f}% ({elapsed:.0f}s)")
        results.append(r)
    return results


def analyze_trades_from_report(report_path):
    """
    从 report.json 提取交易级统计：
    - 按 add(买入开仓)/reduce(卖出开仓) 方向分别统计
    - avg_loss（亏损单的平均亏损额）
    - payoff_ratio（平均盈利/平均亏损绝对值）
    - win_rate
    """
    if not report_path.exists():
        return None
    with open(report_path, encoding="utf-8") as f:
        r = json.load(f)

    # 收集所有配对交易（status=stopped/paired 的腿有 paired_pnl）
    # 按 direction 分类：开仓 direction="buy"=add（反T买入），"sell"=reduce（正T卖出）
    add_pnls = []   # 买入开仓的配对盈亏
    reduce_pnls = [] # 卖出开仓的配对盈亏
    all_pnls = []

    for dr in r.get("daily_results", []):
        for t in dr.get("trades", []):
            # 只看有 rules_fired 的开仓腿，其 pnl 字段记录配对后的盈亏
            # 但实际上开仓腿 pnl=0，平仓腿才有 pnl
            # status=stopped/paired 的腿是平仓腿，其 direction 是平仓方向（与开仓相反）
            # 开仓方向需要从 rules_fired 或配对关系推断
            # 简化：平仓腿 direction="buy" 表示买回平仓（开仓是 sell=reduce）
            #       平仓腿 direction="sell" 表示卖出平仓（开仓是 buy=add）
            if t.get("status") in ("stopped", "paired"):
                pnl = t.get("pnl", 0)
                if t.get("direction") == "buy":
                    # 买回平仓 → 开仓是 sell → reduce（正T卖出）
                    reduce_pnls.append(pnl)
                else:
                    # 卖出平仓 → 开仓是 buy → add（反T买入）
                    add_pnls.append(pnl)
                all_pnls.append(pnl)

    def stats(pnls):
        if not pnls:
            return {"n": 0, "win_rate": 0, "avg_loss": 0, "payoff_ratio": 0}
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        avg_win = statistics.mean(wins) if wins else 0
        avg_loss = statistics.mean(losses) if losses else 0
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf") if avg_win > 0 else 0
        return {
            "n": len(pnls),
            "win_rate": len(wins) / len(pnls),
            "avg_loss": avg_loss,
            "payoff_ratio": payoff,
        }

    return {
        "all": stats(all_pnls),
        "add": stats(add_pnls),      # 反T买入开仓
        "reduce": stats(reduce_pnls), # 正T卖出开仓
    }


def find_report_path(tag, code):
    """查找某次回测的 report.json。"""
    pattern = f"{code}_{tag}_*_report.json"
    matches = list(OUTPUT_DIR.glob(pattern))
    # 取最新的
    return matches[0] if matches else None


def compare_groups(a_results, b_results, amp_table, median_amp):
    """对比A/B两组结果。"""
    print("\n" + "=" * 100)
    print("A/B 对比汇总")
    print("=" * 100)

    # 总体对比
    def group_summary(results):
        total_trades = sum(r.get("total_trades", 0) for r in results)
        total_net = sum(r.get("net_pnl", 0) for r in results)
        total_cost_red = sum(r.get("total_cost_reduction", 0) for r in results)
        total_cost_paid = sum(r.get("total_cost_paid", 0) for r in results)
        gross_profit = total_cost_red  # 降成本=毛利
        cost_ratio = total_cost_paid / gross_profit if gross_profit > 0 else 0
        return {
            "n_stocks": len(results),
            "total_trades": total_trades,
            "total_net": total_net,
            "gross_profit": gross_profit,
            "total_cost": total_cost_paid,
            "cost_ratio": cost_ratio,
        }

    a_sum = group_summary(a_results)
    b_sum = group_summary(b_results)

    print(f"\n{'指标':<20} {'A组(固定0.002)':>18} {'B组(动态缩放)':>18} {'差异':>14}")
    print("-" * 75)
    print(f"{'股票数':<20} {a_sum['n_stocks']:>18} {b_sum['n_stocks']:>18}")
    print(f"{'总T次数':<20} {a_sum['total_trades']:>18} {b_sum['total_trades']:>18} {b_sum['total_trades']-a_sum['total_trades']:>+14}")
    print(f"{'总净盈亏':<20} {a_sum['total_net']:>+18.0f} {b_sum['total_net']:>+18.0f} {b_sum['total_net']-a_sum['total_net']:>+14.0f}")
    print(f"{'总毛利(降成本)':<20} {a_sum['gross_profit']:>+18.0f} {b_sum['gross_profit']:>+18.0f} {b_sum['gross_profit']-a_sum['gross_profit']:>+14.0f}")
    print(f"{'总交易成本':<20} {a_sum['total_cost']:>18.0f} {b_sum['total_cost']:>18.0f} {b_sum['total_cost']-a_sum['total_cost']:>+14.0f}")
    print(f"{'成本/毛利比':<20} {a_sum['cost_ratio']:>18.3f} {b_sum['cost_ratio']:>18.3f} {b_sum['cost_ratio']-a_sum['cost_ratio']:>+14.3f}")

    # 按振幅分组：高振幅(前50%) vs 低振幅(后50%)
    print("\n" + "=" * 100)
    print("核心验证：高振幅组 vs 低振幅组的 avg_loss 差距是否缩小")
    print("=" * 100)

    # 按振幅排序，中位数作为分界
    sorted_codes = sorted(amp_table.keys(), key=lambda c: amp_table[c])
    n = len(sorted_codes)
    split = n // 2
    low_amp_codes = set(sorted_codes[:split])   # 低振幅组
    high_amp_codes = set(sorted_codes[split:])  # 高振幅组

    print(f"\n振幅中位数: {median_amp*100:.2f}%")
    print(f"低振幅组({len(low_amp_codes)}只): 振幅 < {median_amp*100:.2f}%")
    print(f"高振幅组({len(high_amp_codes)}只): 振幅 ≥ {median_amp*100:.2f}%")

    # 从 report.json 提取交易级统计
    def group_trade_stats(results, group_codes, group_tag):
        stats_add = {"n": 0, "wins": 0, "losses": [], "payoffs": []}
        stats_reduce = {"n": 0, "wins": 0, "losses": [], "payoffs": []}
        stats_all = {"n": 0, "wins": 0, "losses": [], "payoffs": []}
        for r in results:
            if r["code"] not in group_codes:
                continue
            tag = f"g2b_{'A' if results == a_results else 'B'}_{r['code']}"
            report_path = find_report_path(tag, r["code"])
            if not report_path:
                continue
            ts = analyze_trades_from_report(report_path)
            if not ts:
                continue
            for key, stats in [("add", stats_add), ("reduce", stats_reduce), ("all", stats_all)]:
                s = ts[key]
                stats["n"] += s["n"]
                stats["wins"] += int(s["win_rate"] * s["n"])
                # 收集亏损额（用 avg_loss × 亏损单数近似）
                n_losses = s["n"] - int(s["win_rate"] * s["n"])
                if n_losses > 0 and s["avg_loss"] < 0:
                    stats["losses"].extend([s["avg_loss"]] * n_losses)
                if s["payoff_ratio"] < float("inf"):
                    stats["payoffs"].append(s["payoff_ratio"])

        def finalize(stats):
            n = stats["n"]
            wins = stats["wins"]
            losses_list = stats["losses"]
            avg_loss = statistics.mean(losses_list) if losses_list else 0
            avg_payoff = statistics.mean(stats["payoffs"]) if stats["payoffs"] else 0
            return {
                "n": n,
                "win_rate": wins / n if n else 0,
                "avg_loss": avg_loss,
                "payoff_ratio": avg_payoff,
            }

        return {
            "all": finalize(stats_all),
            "add": finalize(stats_add),
            "reduce": finalize(stats_reduce),
        }

    # 4 个组合：A高、A低、B高、B低
    combos = [
        ("A组 固定止损", "低振幅", a_results, low_amp_codes),
        ("A组 固定止损", "高振幅", a_results, high_amp_codes),
        ("B组 动态止损", "低振幅", b_results, low_amp_codes),
        ("B组 动态止损", "高振幅", b_results, high_amp_codes),
    ]

    print(f"\n{'组别':<16} {'振幅组':<10} {'样本T':>8} {'胜率':>8} {'avg_loss':>12} {'payoff':>10}")
    print("-" * 70)
    results_map = {}
    for grp, amp_grp, results, codes in combos:
        stats = group_trade_stats(results, codes, amp_grp)
        s = stats["all"]
        print(f"{grp:<16} {amp_grp:<10} {s['n']:>8} {s['win_rate']*100:>7.1f}% "
              f"{s['avg_loss']:>+12.2f} {s['payoff_ratio']:>10.2f}")
        results_map[(grp, amp_grp)] = stats

    # 核心对比：高/低振幅组 avg_loss 差距
    print("\n" + "-" * 70)
    print("核心指标：高/低振幅组 avg_loss 差距（动态化本该缩小的）")
    print("-" * 70)
    a_low_loss = results_map[("A组 固定止损", "低振幅")]["all"]["avg_loss"]
    a_high_loss = results_map[("A组 固定止损", "高振幅")]["all"]["avg_loss"]
    b_low_loss = results_map[("B组 动态止损", "低振幅")]["all"]["avg_loss"]
    b_high_loss = results_map[("B组 动态止损", "高振幅")]["all"]["avg_loss"]
    a_gap = abs(a_high_loss - a_low_loss)
    b_gap = abs(b_high_loss - b_low_loss)
    print(f"  A组 低振幅 avg_loss: {a_low_loss:+.2f}")
    print(f"  A组 高振幅 avg_loss: {a_high_loss:+.2f}")
    print(f"  A组 差距: {a_gap:.2f}")
    print(f"  B组 低振幅 avg_loss: {b_low_loss:+.2f}")
    print(f"  B组 高振幅 avg_loss: {b_high_loss:+.2f}")
    print(f"  B组 差距: {b_gap:.2f}")
    print(f"\n  差距变化: {a_gap:.2f} → {b_gap:.2f} ({'缩小✓' if b_gap < a_gap else '未缩小✗'})")

    # 留痕：按 add/reduce 方向分别统计
    print("\n" + "=" * 100)
    print("留痕：按 add(买入)/reduce(卖出) 方向分别统计（不用于本轮决策）")
    print("=" * 100)
    print(f"\n{'组别':<16} {'方向':<10} {'样本T':>8} {'胜率':>8} {'avg_loss':>12} {'payoff':>10}")
    print("-" * 70)
    for grp, amp_grp, _, _ in combos:
        stats = results_map[(grp, amp_grp)]
        for direction in ["add", "reduce"]:
            s = stats[direction]
            if s["n"] == 0:
                continue
            print(f"{grp:<16} {direction+' '+amp_grp:<10} {s['n']:>8} {s['win_rate']*100:>7.1f}% "
                  f"{s['avg_loss']:>+12.2f} {s['payoff_ratio']:>10.2f}")

    # 保存结果
    out = {
        "stage": "G2-2b_unidirectional_tighten_ab_test",
        "config": {
            "base_stop_loss": 0.002,
            "dynamic_min_scale": 0.5,
            "dynamic_max_scale": 1.0,
            "pool_median_amplitude": median_amp,
            "amplitude_table": amp_table,
        },
        "summary": {
            "A_fixed": a_sum,
            "B_dynamic": b_sum,
        },
        "by_amplitude_group": {
            "A_low": results_map[("A组 固定止损", "低振幅")],
            "A_high": results_map[("A组 固定止损", "高振幅")],
            "B_low": results_map[("B组 动态止损", "低振幅")],
            "B_high": results_map[("B组 动态止损", "高振幅")],
        },
        "avg_loss_gap": {
            "A": a_gap,
            "B": b_gap,
            "improved": b_gap < a_gap,
        },
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "G2-2b_unidirectional_tighten_ab.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


def main():
    print("=" * 100)
    print(f"Stage G2-2b: 动态止损 A/B 测试 — 单向收紧 ({START} ~ {END})")
    print("=" * 100)

    # 步骤1：预计算振幅表
    amp_table, median_amp = compute_amplitude_table()

    # 步骤2：跑 A 组（固定止损）
    a_results = run_group("A", amp_table, median_amp, use_dynamic=False)

    # 步骤3：跑 B 组（动态止损）
    b_results = run_group("B", amp_table, median_amp, use_dynamic=True)

    # 步骤4：对比分析
    compare_groups(a_results, b_results, amp_table, median_amp)


if __name__ == "__main__":
    main()
