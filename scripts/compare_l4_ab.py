#!/usr/bin/env python3
"""
L4 闸门 A/B 对比分析脚本
========================
读取多组 batch_summary JSON，生成对比表（净盈亏/胜率/Payoff/盈亏比/成本/盈利股数）。

用法:
    python scripts/compare_l4_ab.py baseline.json gate_rr25.json [gate_rr20.json ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_summary(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt(v, sign=False, pct=False, w=12):
    if v is None:
        return f"{'N/A':>{w}}"
    if pct:
        s = f"{v*100:.2f}%"
    elif sign:
        s = f"{v:+.2f}"
    else:
        s = f"{v:.2f}"
    return f"{s:>{w}}"


def compare(summaries: list[tuple[str, dict]]) -> None:
    # 表头
    cols = ["指标"]
    for label, _ in summaries:
        cols.append(label)
    widths = [16] + [14] * len(summaries)

    rows = []

    # 基线引用（第一组）
    base = summaries[0][1]["overall"] if summaries else {}

    def row(label, key, sign=False, pct=False, delta=False):
        r = [label]
        for i, (_, s) in enumerate(summaries):
            v = s["overall"].get(key)
            r.append(fmt(v, sign=sign, pct=pct))
            if delta and i > 0 and v is not None and base.get(key) is not None:
                bv = base[key]
                if pct:
                    d = v - bv
                    r[-1] = f"{fmt(v, pct=True)} ({d*100:+.2f}pp)"
                else:
                    d = v - bv
                    pct_d = (d / abs(bv) * 100) if bv else 0
                    r[-1] = f"{fmt(v, sign=sign)} ({pct_d:+.1f}%)"
        rows.append(r)

    def row_raw(label, fn, sign=False):
        r = [label]
        for i, (_, s) in enumerate(summaries):
            v = fn(s["overall"])
            r.append(fmt(v, sign=sign))
            if i > 0:
                bv = fn(base)
                if bv:
                    pct_d = (v - bv) / abs(bv) * 100
                    r[-1] = f"{fmt(v, sign=sign)} ({pct_d:+.1f}%)"
        rows.append(r)

    row("总交易笔数", "total_trades", delta=True)
    row("配对笔数", "paired_trades", delta=True)
    row("盈利笔数", "win_trades", delta=True)
    row("亏损笔数", "loss_trades", delta=True)
    row("胜率", "win_rate", pct=True, delta=True)
    row("单笔均盈", "avg_win", sign=True, delta=True)
    row("单笔均亏", "avg_loss", sign=True, delta=True)
    row("盈亏比", "payoff_ratio", delta=True)
    row("毛利润", "gross_pnl", sign=True, delta=True)
    row("总成本", "total_cost", delta=True)
    row_raw("成本/毛利%", lambda o: o["total_cost"] / o["gross_pnl"] * 100 if o["gross_pnl"] else 0)
    row("净盈亏(已实现)", "net_pnl", sign=True, delta=True)
    row("净盈亏(含浮盈)", "net_pnl_with_unrealized", sign=True, delta=True)
    row("盈利股票数", "profitable_stocks", delta=True)
    row_raw("每笔期望", lambda o: o["net_pnl"] / o["paired_trades"] if o["paired_trades"] else 0, sign=True)

    # 打印表格
    print()
    header = " | ".join(f"{c:>{w}}" for c, w in zip(cols, widths))
    print(header)
    print("-" * len(header))
    for r in rows:
        print(" | ".join(f"{c:>{w}}" for c, w in zip(r, widths)))

    # 结论
    print("\n" + "=" * 70)
    if len(summaries) >= 2:
        b = summaries[0][1]["overall"]
        g = summaries[1][1]["overall"]
        pnl_d = g["net_pnl"] - b["net_pnl"]
        pay_d = g["payoff_ratio"] - b["payoff_ratio"]
        wr_d = g["win_rate"] - b["win_rate"]
        print(f"vs 基线: 净盈亏 {pnl_d:+,.2f} ({pnl_d/b['net_pnl']*100:+.1f}%), "
              f"Payoff {pay_d:+.4f}, 胜率 {wr_d*100:+.2f}pp")


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: python scripts/compare_l4_ab.py <summary1.json> <summary2.json> [...]")
        print("  第一个文件作为基线")
        return 1
    summaries = []
    for a in args:
        p = Path(a)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if not p.exists():
            # 尝试 outputs/backtest 下找
            p2 = PROJECT_ROOT / "outputs" / "backtest" / a
            if p2.exists():
                p = p2
            else:
                print(f"[ERROR] 文件不存在: {p}")
                return 1
        s = load_summary(p)
        label = Path(a).stem.replace("batch_summary_", "")
        summaries.append((label, s))
    compare(summaries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
