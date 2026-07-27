#!/usr/bin/env python3
"""
步骤2基线（修正版）：用 run_zz500_single 跑 zz500 样本。
合并前后都用 run_zz500_single，确保对比的是同一函数路径。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def run_sample(codes: list[str], start: str, end: str, data_dir: Path, tag: str) -> list[dict]:
    from backtest_zz500 import run_zz500_single
    results = []
    for code in codes:
        print(f"\n[baseline] {tag} {code} {start}~{end}")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = run_zz500_single(
                    code=code, start_date=start, end_date=end,
                    data_dir=data_dir, base_shares=3000, avg_cost=None, tag=tag,
                )
        except Exception as e:
            results.append({"source": tag, "code": code, "error": str(e)})
            continue
        if not result or not result.get("daily_results"):
            results.append({"source": tag, "code": code, "total_trades": 0,
                            "paired_trades": 0, "win_rate": 0.0,
                            "net_pnl": 0.0, "net_pnl_with_unrealized": 0.0})
            continue
        results.append({
            "source": tag, "code": code,
            "total_trades": result.get("total_trades", 0),
            "paired_trades": result.get("total_trades", 0),
            "win_rate": round(result.get("win_rate", 0.0), 4),
            "net_pnl": round(result.get("net_pnl", 0.0), 4),
            "net_pnl_with_unrealized": round(result.get("net_pnl_with_unrealized", 0.0), 4),
        })
    return results


def main() -> int:
    print("=" * 78)
    print("步骤2基线（修正版）：用 run_zz500_single 统一路径")
    print("=" * 78)

    baseline = {"description": "合并前基线（run_zz500_single 路径）", "items": []}

    # zz500_5min 样本
    zz500_codes = ["000060", "600298", "600312", "600879", "300972"]
    baseline["items"].extend(run_sample(
        zz500_codes, "2026-04-27", "2026-07-23",
        Path("d:/project/data/zz500_5min"), "zz500_5min",
    ))

    # minute_local_merged 样本（已是每股一文件格式）
    baseline["items"].extend(run_sample(
        ["600029"], "2025-07-21", "2025-08-01",
        Path("d:/project/data/minute_local_merged"), "minute_local_merged",
    ))

    out_path = PROJECT_ROOT / "outputs" / "backtest" / "baseline_before_merge.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 78)
    print(f"{'source':<22} {'code':<10} {'trades':>8} {'win_rate':>10} {'net_pnl':>12}")
    print("-" * 70)
    for item in baseline["items"]:
        if "error" in item:
            print(f"{item['source']:<22} {item.get('code',''):<10} ERROR: {item['error']}")
            continue
        print(f"{item['source']:<22} {item['code']:<10} {item['total_trades']:>8} "
              f"{item['win_rate']:>10.4f} {item['net_pnl']:>+12.2f}")
    print(f"\n基线已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
