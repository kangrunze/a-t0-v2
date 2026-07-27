"""
步骤3 — dualrun 对比：振幅过滤开启前后
====================================
对比"全集"vs"振幅>=阈值子集"的 net_pnl/win_rate/盈利股票数。

数据源：
  - batch_summary_zz100_3m.json（样本1，100只）
  - batch_summary_zz50_sample2.json（样本2，50只）
  - stepD_attribution.json（样本1的每股振幅）
  - stepD2_oos_validation.json（样本2的每股振幅）

不重跑回测，直接用已有结果按振幅阈值过滤，模拟 dualrun 对比。
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtest" / "stepD3_dualrun_comparison.json"

# 步骤1 Youden 阈值
AMP_THRESHOLD = 0.0494  # 4.94%


def load_batch_with_amplitude(batch_path, amplitude_map):
    """加载 batch_summary，附加每股振幅。"""
    with open(batch_path, "r", encoding="utf-8") as f:
        batch = json.load(f)
    stocks = []
    for s in batch["per_stock"]:
        code = s["code"]
        net = s.get("net_pnl_with_unrealized", s["net_pnl"])
        amp = amplitude_map.get(code)
        if amp is None:
            continue
        stocks.append({
            "code": code,
            "net_pnl": net,
            "amplitude": amp,
            "win_rate": s["win_rate"],
            "paired_trades": s["paired_trades"],
            "total_trades": s["total_trades"],
        })
    return stocks


def summarize_group(stocks, name):
    """汇总一组股票的指标。"""
    if not stocks:
        return {"name": name, "n": 0}
    nets = [s["net_pnl"] for s in stocks]
    wrs = [s["win_rate"] for s in stocks if s["win_rate"] > 0]
    profitable = [s for s in stocks if s["net_pnl"] > 0]
    total_pnl = sum(nets)
    total_trades = sum(s["total_trades"] for s in stocks)
    total_paired = sum(s["paired_trades"] for s in stocks)
    total_wins = sum(int(s["paired_trades"] * s["win_rate"]) for s in stocks if s["win_rate"] > 0)
    amps = [s["amplitude"] for s in stocks]
    return {
        "name": name,
        "n": len(stocks),
        "total_trades": total_trades,
        "paired_trades": total_paired,
        "win_rate": round(total_wins / total_paired, 4) if total_paired else 0,
        "total_net_pnl": round(total_pnl, 2),
        "avg_net_pnl": round(mean(nets), 2),
        "median_net_pnl": round(median(nets), 2),
        "profitable_count": len(profitable),
        "profitable_ratio": round(len(profitable) / len(stocks), 4),
        "amplitude_mean": round(mean(amps), 2),
        "amplitude_median": round(median(amps), 2),
    }


def main():
    print("=" * 78)
    print(f"步骤3 — dualrun 对比: 振幅过滤开启前后 (阈值 {AMP_THRESHOLD*100:.2f}%)")
    print("=" * 78)

    # 加载样本1的振幅数据
    with open(PROJECT_ROOT / "outputs" / "backtest" / "stepD_attribution.json", "r", encoding="utf-8") as f:
        stepD = json.load(f)
    amp_map1 = {}
    for s in stepD.get("profitable_per_stock", []) + stepD.get("losing_per_stock", []):
        amp_map1[s["code"]] = s["amplitude_mean"]

    # 加载样本2的振幅数据
    with open(PROJECT_ROOT / "outputs" / "backtest" / "stepD2_oos_validation.json", "r", encoding="utf-8") as f:
        stepD2 = json.load(f)
    # 样本2的每股振幅不在 stepD2 里，需要从 batch_summary 重新算
    # 但 stepD2 里有 high/low amplitude group 的统计，没有 per_stock
    # 重新从 batch_summary_zz50_sample2 + 本地数据算
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "diag"))
    from diag_stepD2_oos_validation import load_stock_amplitude

    with open(PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz50_sample2.json", "r", encoding="utf-8") as f:
        batch2 = json.load(f)
    amp_map2 = {}
    for s in batch2["per_stock"]:
        amp = load_stock_amplitude(s["code"])
        if amp is not None:
            amp_map2[s["code"]] = amp

    # 加载两个样本的回测结果
    stocks1 = load_batch_with_amplitude(
        PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz100_3m.json",
        amp_map1,
    )
    stocks2 = load_batch_with_amplitude(
        PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz50_sample2.json",
        amp_map2,
    )

    print(f"\n样本1: {len(stocks1)} 只（ZZ500 随机抽样100只）")
    print(f"样本2: {len(stocks2)} 只（ZZ500 随机抽样50只，与样本1不重叠）")

    # ── 对比1: 样本1（100只）──
    print("\n" + "=" * 78)
    print("对比1: 样本1 (100只)")
    print("=" * 78)

    s1_all = stocks1
    s1_filtered = [s for s in stocks1 if s["amplitude"] >= AMP_THRESHOLD * 100]

    all_stats = summarize_group(s1_all, "全集（过滤关闭）")
    filtered_stats = summarize_group(s1_filtered, f"振幅>={AMP_THRESHOLD*100:.2f}%（过滤开启）")

    print(f"\n{'指标':<20} {'全集(关闭)':>18} {'过滤(开启)':>18} {'差异':>14}")
    print("-" * 76)
    for key in ["n", "total_trades", "paired_trades", "win_rate", "total_net_pnl",
                "avg_net_pnl", "median_net_pnl", "profitable_count", "profitable_ratio",
                "amplitude_mean"]:
        v1 = all_stats.get(key, 0)
        v2 = filtered_stats.get(key, 0)
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            if key in ("win_rate", "profitable_ratio"):
                diff = f"{(v2-v1)*100:+.1f}pp"
                v1_str = f"{v1*100:.1f}%"
                v2_str = f"{v2*100:.1f}%"
            elif key == "total_net_pnl":
                diff = f"{v2-v1:+,.0f}"
                v1_str = f"{v1:+,.0f}"
                v2_str = f"{v2:+,.0f}"
            else:
                v1_str = f"{v1:,.2f}" if isinstance(v1, float) else f"{v1:,}"
                v2_str = f"{v2:,.2f}" if isinstance(v2, float) else f"{v2:,}"
                diff = f"{v2-v1:+.2f}" if isinstance(v1, float) else f"{v2-v1:+d}"
        else:
            v1_str = str(v1)
            v2_str = str(v2)
            diff = ""
        print(f"{key:<20} {v1_str:>18} {v2_str:>18} {diff:>14}")

    # ── 对比2: 样本2（50只，样本外）──
    print("\n" + "=" * 78)
    print("对比2: 样本2 (50只, 样本外)")
    print("=" * 78)

    s2_all = stocks2
    s2_filtered = [s for s in stocks2 if s["amplitude"] >= AMP_THRESHOLD * 100]

    all_stats2 = summarize_group(s2_all, "全集（过滤关闭）")
    filtered_stats2 = summarize_group(s2_filtered, f"振幅>={AMP_THRESHOLD*100:.2f}%（过滤开启）")

    print(f"\n{'指标':<20} {'全集(关闭)':>18} {'过滤(开启)':>18} {'差异':>14}")
    print("-" * 76)
    for key in ["n", "total_trades", "paired_trades", "win_rate", "total_net_pnl",
                "avg_net_pnl", "median_net_pnl", "profitable_count", "profitable_ratio",
                "amplitude_mean"]:
        v1 = all_stats2.get(key, 0)
        v2 = filtered_stats2.get(key, 0)
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            if key in ("win_rate", "profitable_ratio"):
                diff = f"{(v2-v1)*100:+.1f}pp"
                v1_str = f"{v1*100:.1f}%"
                v2_str = f"{v2*100:.1f}%"
            elif key == "total_net_pnl":
                diff = f"{v2-v1:+,.0f}"
                v1_str = f"{v1:+,.0f}"
                v2_str = f"{v2:+,.0f}"
            else:
                v1_str = f"{v1:,.2f}" if isinstance(v1, float) else f"{v1:,}"
                v2_str = f"{v2:,.2f}" if isinstance(v2, float) else f"{v2:,}"
                diff = f"{v2-v1:+.2f}" if isinstance(v1, float) else f"{v2-v1:+d}"
        else:
            v1_str = str(v1)
            v2_str = str(v2)
            diff = ""
        print(f"{key:<20} {v1_str:>18} {v2_str:>18} {diff:>14}")

    # ── 合并样本（150只）──
    print("\n" + "=" * 78)
    print("对比3: 合并样本 (150只 = 样本1 + 样本2)")
    print("=" * 78)

    all_combined = stocks1 + stocks2
    filtered_combined = [s for s in all_combined if s["amplitude"] >= AMP_THRESHOLD * 100]

    all_stats_c = summarize_group(all_combined, "全集（过滤关闭）")
    filtered_stats_c = summarize_group(filtered_combined, f"振幅>={AMP_THRESHOLD*100:.2f}%（过滤开启）")

    print(f"\n{'指标':<20} {'全集(关闭)':>18} {'过滤(开启)':>18} {'差异':>14}")
    print("-" * 76)
    for key in ["n", "total_trades", "paired_trades", "win_rate", "total_net_pnl",
                "avg_net_pnl", "median_net_pnl", "profitable_count", "profitable_ratio",
                "amplitude_mean"]:
        v1 = all_stats_c.get(key, 0)
        v2 = filtered_stats_c.get(key, 0)
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            if key in ("win_rate", "profitable_ratio"):
                diff = f"{(v2-v1)*100:+.1f}pp"
                v1_str = f"{v1*100:.1f}%"
                v2_str = f"{v2*100:.1f}%"
            elif key == "total_net_pnl":
                diff = f"{v2-v1:+,.0f}"
                v1_str = f"{v1:+,.0f}"
                v2_str = f"{v2:+,.0f}"
            else:
                v1_str = f"{v1:,.2f}" if isinstance(v1, float) else f"{v1:,}"
                v2_str = f"{v2:,.2f}" if isinstance(v2, float) else f"{v2:,}"
                diff = f"{v2-v1:+.2f}" if isinstance(v1, float) else f"{v2-v1:+d}"
        else:
            v1_str = str(v1)
            v2_str = str(v2)
            diff = ""
        print(f"{key:<20} {v1_str:>18} {v2_str:>18} {diff:>14}")

    # ── 综合判断 ──
    print("\n" + "=" * 78)
    print("综合判断")
    print("=" * 78)
    print(f"\n过滤阈值: 60日日均振幅 >= {AMP_THRESHOLD*100:.2f}% (Youden 最优切点)")
    print(f"\n样本1 (100只):")
    print(f"  入选股票: {all_stats['n']} → {filtered_stats['n']} ({filtered_stats['n']/all_stats['n']*100:.0f}%)")
    print(f"  净盈亏: {all_stats['total_net_pnl']:+,.0f} → {filtered_stats['total_net_pnl']:+,.0f} ({filtered_stats['total_net_pnl']-all_stats['total_net_pnl']:+,.0f})")
    print(f"  盈利占比: {all_stats['profitable_ratio']*100:.1f}% → {filtered_stats['profitable_ratio']*100:.1f}% ({(filtered_stats['profitable_ratio']-all_stats['profitable_ratio'])*100:+.1f}pp)")
    print(f"\n样本2 (50只, 样本外):")
    print(f"  入选股票: {all_stats2['n']} → {filtered_stats2['n']} ({filtered_stats2['n']/all_stats2['n']*100:.0f}%)")
    print(f"  净盈亏: {all_stats2['total_net_pnl']:+,.0f} → {filtered_stats2['total_net_pnl']:+,.0f} ({filtered_stats2['total_net_pnl']-all_stats2['total_net_pnl']:+,.0f})")
    print(f"  盈利占比: {all_stats2['profitable_ratio']*100:.1f}% → {filtered_stats2['profitable_ratio']*100:.1f}% ({(filtered_stats2['profitable_ratio']-all_stats2['profitable_ratio'])*100:+.1f}pp)")
    print(f"\n合并样本 (150只):")
    print(f"  入选股票: {all_stats_c['n']} → {filtered_stats_c['n']} ({filtered_stats_c['n']/all_stats_c['n']*100:.0f}%)")
    print(f"  净盈亏: {all_stats_c['total_net_pnl']:+,.0f} → {filtered_stats_c['total_net_pnl']:+,.0f} ({filtered_stats_c['total_net_pnl']-all_stats_c['total_net_pnl']:+,.0f})")
    print(f"  盈利占比: {all_stats_c['profitable_ratio']*100:.1f}% → {filtered_stats_c['profitable_ratio']*100:.1f}% ({(filtered_stats_c['profitable_ratio']-all_stats_c['profitable_ratio'])*100:+.1f}pp)")

    output = {
        "threshold": AMP_THRESHOLD,
        "sample1": {"all": all_stats, "filtered": filtered_stats},
        "sample2": {"all": all_stats2, "filtered": filtered_stats2},
        "combined": {"all": all_stats_c, "filtered": filtered_stats_c},
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结构化结果 -> {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
