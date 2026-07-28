#!/usr/bin/env python3
"""
步骤3验证：用合并后的统一入口（backtest_zz500.py）重跑步骤2基线样本，
对比 win_rate/net_pnl/paired_trades 是否一致（浮点误差<0.01）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def run_unified(codes: list[str], start: str, end: str, data_dir: Path, tag: str) -> list[dict]:
    """用 backtest_zz500.py 的 run_zz500_single 跑基线样本。"""
    from backtest_zz500 import run_zz500_single
    import io
    import contextlib

    results = []
    for code in codes:
        print(f"\n[unified] {code} {start}~{end} data_dir={data_dir}")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = run_zz500_single(
                    code=code,
                    start_date=start,
                    end_date=end,
                    data_dir=data_dir,
                    base_shares=3000,
                    avg_cost=None,
                    tag=tag,
                )
        except Exception as e:
            print(f"  异常: {e}")
            results.append({"source": tag, "code": code, "error": str(e)})
            continue

        if not result or not result.get("daily_results"):
            results.append({"source": tag, "code": code, "total_trades": 0,
                            "paired_trades": 0, "win_rate": 0.0,
                            "net_pnl": 0.0, "net_pnl_with_unrealized": 0.0})
            continue

        # 直接用 backtest_multi_day 返回的汇总字段（与步骤2基线脚本一致）
        results.append({
            "source": tag,
            "code": code,
            "total_trades": result.get("total_trades", 0),
            "paired_trades": result.get("total_trades", 0),
            "win_rate": round(result.get("win_rate", 0.0), 4),
            "net_pnl": round(result.get("net_pnl", 0.0), 4),
            "net_pnl_with_unrealized": round(result.get("net_pnl_with_unrealized", 0.0), 4),
        })
    return results


def main() -> int:
    print("=" * 78)
    print("步骤3验证：合并后统一入口 vs 步骤2基线")
    print("=" * 78)

    baseline_path = PROJECT_ROOT / "outputs" / "backtest" / "baseline_before_merge.json"
    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    after = {"description": "合并后统一入口结果", "items": []}

    # 1. minute_local_merged（对应基线的 local_minute_1min）
    try:
        local_codes = ["600029"]
        local_results = run_unified(
            local_codes, "2025-07-21", "2025-08-01",
            Path("d:/project/data/minute_local_merged"),
            "minute_local_merged",
        )
        after["items"].extend(local_results)
    except Exception as e:
        print(f"[WARN] minute_local_merged 失败: {e}")
        after["items"].append({"source": "minute_local_merged", "error": str(e)})

    # 2. zz500_5min（对应基线的 zz500_5min）
    try:
        zz500_codes = ["000060", "600298", "600312", "600879", "300972"]
        zz500_results = run_unified(
            zz500_codes, "2026-04-27", "2026-07-23",
            Path("d:/project/data/zz500_5min"),
            "zz500_5min",
        )
        after["items"].extend(zz500_results)
    except Exception as e:
        print(f"[WARN] zz500_5min 失败: {e}")
        after["items"].append({"source": "zz500_5min", "error": str(e)})

    # 保存合并后结果
    out_path = PROJECT_ROOT / "outputs" / "backtest" / "baseline_after_merge.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(after, f, ensure_ascii=False, indent=2, default=str)

    # 对比
    print("\n" + "=" * 78)
    print("对比表（基线 vs 合并后）")
    print("=" * 78)
    print(f"{'source':<22} {'code':<10} {'base_trades':>12} {'after_trades':>13} {'base_pnl':>12} {'after_pnl':>12} {'match':>8}")
    print("-" * 100)

    all_match = True
    for base_item in baseline["items"]:
        if "error" in base_item:
            print(f"{base_item.get('source','?'):<22} BASE ERROR: {base_item['error']}")
            continue
        # 找对应 after 项
        after_item = None
        for a in after["items"]:
            if a.get("code") == base_item.get("code") and "error" not in a:
                after_item = a
                break
        if not after_item:
            print(f"{base_item.get('source','?'):<22} {base_item.get('code','?'):<10} AFTER NOT FOUND")
            all_match = False
            continue

        base_trades = base_item.get("total_trades", 0)
        after_trades = after_item.get("total_trades", 0)
        base_pnl = base_item.get("net_pnl", 0.0)
        after_pnl = after_item.get("net_pnl", 0.0)
        trades_match = base_trades == after_trades
        pnl_match = abs(base_pnl - after_pnl) < 0.01
        match = "OK" if (trades_match and pnl_match) else "FAIL"
        if not (trades_match and pnl_match):
            all_match = False

        print(f"{base_item.get('source','?'):<22} {base_item.get('code','?'):<10} "
              f"{base_trades:>12} {after_trades:>13} "
              f"{base_pnl:>+12.2f} {after_pnl:>+12.2f} {match:>8}")

    print("\n" + "=" * 78)
    if all_match:
        print("结论：PASS — 合并后行为与基线完全一致（浮点误差<0.01）")
    else:
        print("结论：FAIL — 存在行为差异，需排查")
    print(f"合并后结果: {out_path}")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
