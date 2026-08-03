#!/usr/bin/env python3
"""
Measurement V2 — A/B 比较器 & Stage Gate 判定
=============================================
把「两个 tag 的回测结果」变成「Stage 能不能过闸」的结论。

设计原则：
  - Stage 验收看过程指标（CE / Entry Delay / MAE / Trend Quality），不看净利润。
    净利润只做「不许显著变差」的护栏，不作为通过条件。
  - 一律用 median 判定，mean 仅作参考 —— 均值会被少数极端交易带偏。
  - 样本量不足时直接判 INCONCLUSIVE，不允许"看起来变好了"。

用法:
    python scripts/compare_v2_ab.py --base v3_b5_optimized --cand w_wave_v1 --gate W
    python scripts/compare_v2_ab.py --base A --cand B --gate none --start 2026-04-01
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.measurement.v2_metrics import compute_v2_metrics_batch  # noqa: E402

DEFAULT_REPORT_DIR = PROJECT_ROOT / "outputs" / "backtest"
DEFAULT_DATA_DIR = Path(r"D:\project\data\zz500_5min")
DEFAULT_START = "2026-04-01"
DEFAULT_END = "2026-07-22"

MIN_PAIRS = 30  # 低于此样本量不给结论


# ═══════════════════════════════════════════════════════════════
# Stage Gate 定义
# 每条 = (指标路径, 显示名, 方向, 最小改善幅度)
#   方向 "down" = 越小越好；"up" = 越大越好
#   最小改善幅度为相对变化（0.20 = 至少改善 20%）
# ═══════════════════════════════════════════════════════════════
GATES: dict[str, dict] = {
    "W": {
        "name": "Stage W — Wave Engine",
        "goal": "提前买点：Entry Delay ↓、CE ↑",
        "criteria": [
            ("entry_delay_bars", "Entry Delay (median, K)", "down", 0.20),
            ("ce", "Capture Efficiency (median)", "up", 0.05),
        ],
    },
    "S": {
        "name": "Stage S — Support Engine",
        "goal": "更优回踩位置：MAE ↓、CE ↑",
        "criteria": [
            ("mae_pct", "MAE (median, %)", "down", 0.15),
            ("ce", "Capture Efficiency (median)", "up", 0.05),
        ],
    },
    "E": {
        "name": "Stage E — Expected Move Engine",
        "goal": "提高盈亏比：Remaining Move ↑、低空间交易减少",
        "criteria": [
            ("remaining_move_pct", "Remaining Move (median, %)", "up", 0.15),
            ("mfe_pct", "MFE (median, %)", "up", 0.10),
        ],
    },
    "H": {
        "name": "Stage H — Hold Confidence V2",
        "goal": "延长持仓：Exit Delay 更合理、Trend Quality ↑",
        "criteria": [
            ("trend_quality", "Trend Quality (median)", "up", 0.10),
            ("ce", "Capture Efficiency (median)", "up", 0.05),
        ],
    },
    "X": {
        "name": "Stage X — Predictive Exit",
        "goal": "提前识别趋势结束：Exit Delay ↓、MAE ↓",
        "criteria": [
            ("exit_delay_bars", "Exit Delay (median, K)", "down", 0.20),
            ("mae_pct", "MAE (median, %)", "down", 0.10),
        ],
    },
}

# 护栏：净利润不许比基线差超过 10%
PNL_GUARDRAIL = 0.10


def _median(metrics: dict, key: str) -> float:
    return float(((metrics.get("distributions") or {}).get(key) or {}).get("median", 0.0))


def _mean(metrics: dict, key: str) -> float:
    return float(((metrics.get("distributions") or {}).get(key) or {}).get("mean", 0.0))


def _n(metrics: dict, key: str) -> int:
    return int(((metrics.get("distributions") or {}).get(key) or {}).get("n", 0))


def _rel_change(base: float, cand: float) -> float:
    """相对变化；基线为 0 时退化为绝对差。"""
    if base == 0:
        return cand
    return (cand - base) / abs(base)


def evaluate_gate(base: dict, cand: dict, gate_key: str) -> dict:
    """按 Stage Gate 规则判定候选组是否通过。"""
    gate = GATES.get(gate_key)
    n_base = base.get("trade_quality", {}).get("n", 0)
    n_cand = cand.get("trade_quality", {}).get("n", 0)

    result = {
        "gate": gate_key,
        "gate_name": gate["name"] if gate else "(无闸门，仅对比)",
        "n_base": n_base,
        "n_cand": n_cand,
        "checks": [],
        "guardrail": {},
        "verdict": "INCONCLUSIVE",
    }

    # ── 样本量护栏 ──
    if min(n_base, n_cand) < MIN_PAIRS:
        result["reason"] = (f"样本量不足（base={n_base}, cand={n_cand}，"
                            f"要求 ≥ {MIN_PAIRS}），不给结论")
        return result

    # ── 逐笔全等检测：改动完全没生效的典型信号 ──
    identical = (
        abs(base.get("trade_quality", {}).get("net_pnl", 0)
            - cand.get("trade_quality", {}).get("net_pnl", 0)) < 1e-6
        and n_base == n_cand
        and abs(_median(base, "ce") - _median(cand, "ce")) < 1e-9
    )
    result["identical_to_base"] = identical

    if not gate:
        result["verdict"] = "COMPARE_ONLY"
        return result

    all_pass = True
    for key, label, direction, min_gain in gate["criteria"]:
        b, c = _median(base, key), _median(cand, key)
        rel = _rel_change(b, c)
        gain = -rel if direction == "down" else rel
        passed = gain >= min_gain
        all_pass = all_pass and passed
        result["checks"].append({
            "metric": key, "label": label, "direction": direction,
            "base": round(b, 4), "cand": round(c, 4),
            "rel_change": round(rel, 4), "required_gain": min_gain,
            "actual_gain": round(gain, 4), "pass": passed,
        })

    # ── 净利润护栏 ──
    pnl_b = base.get("trade_quality", {}).get("net_pnl", 0.0)
    pnl_c = cand.get("trade_quality", {}).get("net_pnl", 0.0)
    pnl_rel = _rel_change(pnl_b, pnl_c)
    pnl_ok = pnl_rel >= -PNL_GUARDRAIL
    result["guardrail"] = {
        "net_pnl_base": round(pnl_b, 2), "net_pnl_cand": round(pnl_c, 2),
        "rel_change": round(pnl_rel, 4),
        "max_allowed_drop": PNL_GUARDRAIL, "pass": pnl_ok,
    }

    if identical:
        result["verdict"] = "NO_EFFECT"
        result["reason"] = "候选组与基线逐笔全等 —— 改动未进入决策路径，先查通路再谈效果"
    elif all_pass and pnl_ok:
        result["verdict"] = "PASS"
    elif all_pass and not pnl_ok:
        result["verdict"] = "PASS_WITH_WARNING"
        result["reason"] = "过程指标达标但净利润跌破护栏，需排查退出侧"
    else:
        result["verdict"] = "FAIL"
    return result


# ═══════════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════════

COMPARE_ROWS = [
    ("ce", "Capture Efficiency", "ratio"),
    ("entry_delay_bars", "Entry Delay (K)", "num"),
    ("exit_delay_bars", "Exit Delay (K)", "num"),
    ("execution_gain_pct", "Execution Gain (%)", "num"),
    ("remaining_move_pct", "Remaining Move (%)", "num"),
    ("mae_pct", "MAE (%)", "num"),
    ("mfe_pct", "MFE (%)", "num"),
    ("trend_quality", "Trend Quality", "ratio"),
    ("wave_number", "Wave Number", "num"),
]


def print_comparison(base: dict, cand: dict, base_tag: str, cand_tag: str,
                     verdict: dict) -> None:
    print(f"\n{'=' * 82}")
    print(f"  A/B 对比  base={base_tag}  vs  cand={cand_tag}")
    print(f"{'=' * 82}")
    print(f"{'指标 (median)':<26} | {'Base':>12} | {'Cand':>12} | {'Δ':>12} | {'Δ%':>9}")
    print("-" * 82)
    for key, label, kind in COMPARE_ROWS:
        b, c = _median(base, key), _median(cand, key)
        if _n(base, key) == 0 and _n(cand, key) == 0:
            continue
        d = c - b
        rel = _rel_change(b, c) * 100
        if kind == "ratio":
            print(f"{label:<26} | {b * 100:>11.2f}% | {c * 100:>11.2f}% | "
                  f"{d * 100:>+11.2f}% | {rel:>+8.1f}%")
        else:
            print(f"{label:<26} | {b:>12.2f} | {c:>12.2f} | {d:>+12.2f} | {rel:>+8.1f}%")

    print("-" * 82)
    for label, key in [("配对交易数", "n"), ("胜率", "win_rate"),
                       ("Profit Factor", "profit_factor"), ("净盈亏", "net_pnl")]:
        b = base.get("trade_quality", {}).get(key, 0)
        c = cand.get("trade_quality", {}).get(key, 0)
        if key == "win_rate":
            print(f"{label:<26} | {b * 100:>11.2f}% | {c * 100:>11.2f}% | "
                  f"{(c - b) * 100:>+11.2f}% |")
        else:
            print(f"{label:<26} | {b:>12.2f} | {c:>12.2f} | {c - b:>+12.2f} |")

    # 引擎通路状态
    ba = base.get("attribution", {}).get("engines_active")
    ca = cand.get("attribution", {}).get("engines_active")
    print(f"{'V3 Engine 通路':<26} | {str(ba):>12} | {str(ca):>12} |")

    print(f"\n{'=' * 82}")
    print(f"  {verdict['gate_name']}")
    print(f"{'=' * 82}")
    for chk in verdict["checks"]:
        flag = "PASS" if chk["pass"] else "FAIL"
        arrow = "↓" if chk["direction"] == "down" else "↑"
        print(f"  [{flag}] {chk['label']:<34} {chk['base']:.4f} → {chk['cand']:.4f}  "
              f"({arrow} 需 {chk['required_gain']:.0%}，实际 {chk['actual_gain']:+.1%})")
    if verdict.get("guardrail"):
        g = verdict["guardrail"]
        flag = "PASS" if g["pass"] else "FAIL"
        print(f"  [{flag}] 护栏：净盈亏 {g['net_pnl_base']:+.2f} → {g['net_pnl_cand']:+.2f} "
              f"({g['rel_change']:+.1%}，允许最多 -{g['max_allowed_drop']:.0%})")
    print(f"\n  判定：{verdict['verdict']}")
    if verdict.get("reason"):
        print(f"  说明：{verdict['reason']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Measurement V2 A/B 比较器")
    ap.add_argument("--base", required=True, help="基线 tag")
    ap.add_argument("--cand", required=True, help="候选 tag")
    ap.add_argument("--gate", default="none",
                    help=f"Stage Gate: {'/'.join(GATES)} 或 none")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--out", default="", help="结果 JSON 输出路径（默认自动命名）")
    args = ap.parse_args()

    report_dir = Path(args.report_dir)
    data_dir = Path(args.data_dir)

    print(f"[compare] 计算基线指标: {args.base}")
    base = compute_v2_metrics_batch(report_dir, args.base, args.start, args.end, data_dir)
    print(f"[compare] 计算候选指标: {args.cand}")
    cand = compute_v2_metrics_batch(report_dir, args.cand, args.start, args.end, data_dir)

    if not base or not cand:
        print("[ERROR] 缺少 report 文件，无法对比")
        return 1

    gate_key = args.gate.upper() if args.gate.upper() in GATES else ""
    verdict = evaluate_gate(base, cand, gate_key)
    print_comparison(base, cand, args.base, args.cand, verdict)

    out_path = Path(args.out) if args.out else (
        report_dir / f"ab_{args.base}__vs__{args.cand}.json")
    payload = {
        "base_tag": args.base, "cand_tag": args.cand,
        "start": args.start, "end": args.end,
        "verdict": verdict,
        "base": {"overall": base.get("overall"),
                 "distributions": base.get("distributions"),
                 "trade_quality": base.get("trade_quality"),
                 "attribution": base.get("attribution")},
        "cand": {"overall": cand.get("overall"),
                 "distributions": cand.get("distributions"),
                 "trade_quality": cand.get("trade_quality"),
                 "attribution": cand.get("attribution")},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n结果 -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
