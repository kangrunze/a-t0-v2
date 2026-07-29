#!/usr/bin/env python3
"""
Stage K — 均值回归无振幅筛选全池测试
===================================
500股全池，关闭振幅筛选，均值回归模式，近1年。
对比：均值回归是否依赖振幅筛选（高波动股票池）？
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest_zz500 import run_zz500_batch
from at0.paths import ZZ500_5MIN_DIR

# 全池：ZZ500_5MIN_DIR 下所有 .json
all_files = sorted(ZZ500_5MIN_DIR.glob("*.json"))
ALL_CODES = [f.stem for f in all_files if not f.stem.startswith("_")]
print(f"全池股票数: {len(ALL_CODES)}")

START_DATE = "2025-07-28"
END_DATE = "2026-07-22"

# 均值回归最优参数（K4定案 z=1.0/sl=0.001）
MR_OVERRIDE = {
    "sp": {
        "strategy_mode": "mean_reversion",
        "mr_z_threshold": 1.0,
        "mr_take_profit_z": 0.3,
        "mr_lookback_bars": 12,
        "mr_vol_ratio_max": 1.5,
    },
    "bp": {
        "stop_loss_ratio": 0.001,
        "max_holding_bars": 10,
        "cooldown_bars": 8,
    },
    "rp": {
        "max_t_size_ratio": 0.12,
    },
}


def main():
    print("=" * 80)
    print("Stage K — 均值回归无振幅筛选全池测试（断点续跑）")
    print("=" * 80)

    # 断点续跑：检查哪些股票已有report
    report_pattern = f"_K_mr_no_amp_full500_{START_DATE}_{END_DATE}_report.json"
    done_codes = set()
    for f in (PROJECT_ROOT / "outputs" / "backtest").glob(f"*{report_pattern}"):
        done_codes.add(f.name.split("_")[0])
    pending_codes = [c for c in ALL_CODES if c not in done_codes]
    print(f"全池: {len(ALL_CODES)}只, 已完成: {len(done_codes)}只, 待跑: {len(pending_codes)}只")
    print(f"期间: {START_DATE} ~ {END_DATE}")
    print(f"模式: mean_reversion (z=1.0 sl=0.001), 振幅筛选: 关闭")

    t_start = time.time()

    # 跑待处理的股票
    if pending_codes:
        result = run_zz500_batch(
            codes=pending_codes,
            start_date=START_DATE,
            end_date=END_DATE,
            data_dir=ZZ500_5MIN_DIR,
            base_shares=3000,
            tag="K_mr_no_amp_full500",
            params_override=MR_OVERRIDE,
        )
    else:
        result = {"per_stock": []}

    elapsed = time.time() - t_start
    print(f"\n本轮回测耗时: {elapsed/60:.1f}分钟")

    # 汇总全部500只（从report.json读取，含断点前完成的）
    print(f"\n{'=' * 80}")
    print(f"汇总全部500只（从report.json读取）")
    print(f"{'=' * 80}")

    from backtest_zz500 import _import_backtest_deps
    dep = _import_backtest_deps()
    extract_trades = dep["extract_trades"]
    summarize_one_stock = dep["summarize_one_stock"]
    import io, contextlib

    all_per_stock = []
    for code in ALL_CODES:
        report_path = PROJECT_ROOT / "outputs" / "backtest" / f"{code}_K_mr_no_amp_full500_{START_DATE}_{END_DATE}_report.json"
        if not report_path.exists():
            continue
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                summary = summarize_one_stock(code, report)
            all_per_stock.append(summary)
        except Exception as e:
            print(f"  {code} 汇总失败: {e}")

    # 计算overall
    total_trades = sum(s.get("total_trades", 0) for s in all_per_stock)
    total_paired = sum(s.get("paired_trades", 0) for s in all_per_stock)
    total_win = sum(s.get("win_trades", 0) for s in all_per_stock)
    total_loss = sum(s.get("loss_trades", 0) for s in all_per_stock)
    total_gross = sum(s.get("gross_pnl", 0) for s in all_per_stock)
    total_cost = sum(s.get("total_cost", 0) for s in all_per_stock)
    total_net = sum(s.get("net_pnl", 0) for s in all_per_stock)
    total_unrealized = sum(s.get("unrealized_pnl", 0) for s in all_per_stock)
    profitable = sum(1 for s in all_per_stock if s.get("net_pnl", 0) > 0)
    losing = sum(1 for s in all_per_stock if s.get("net_pnl", 0) < 0)
    avg_win = sum(s.get("avg_win", 0) * s.get("win_trades", 0) for s in all_per_stock) / max(total_win, 1)
    avg_loss = sum(s.get("avg_loss", 0) * s.get("loss_trades", 0) for s in all_per_stock) / max(total_loss, 1)

    overall = {
        "stocks": len(all_per_stock),
        "total_trades": total_trades,
        "paired_trades": total_paired,
        "win_trades": total_win,
        "loss_trades": total_loss,
        "win_rate": total_win / total_paired if total_paired else 0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": abs(avg_win / avg_loss) if avg_loss != 0 else 0,
        "gross_pnl": round(total_gross, 2),
        "total_cost": round(total_cost, 2),
        "net_pnl": round(total_net, 2),
        "unrealized_pnl": round(total_unrealized, 2),
        "net_pnl_with_unrealized": round(total_net + total_unrealized, 2),
        "profitable_stocks": profitable,
        "losing_stocks": losing,
    }
    print(f"  股票数: {overall.get('stocks', 0)}")
    print(f"  总配对: {overall.get('paired_trades', 0)}笔")
    print(f"  净盈亏: {overall.get('net_pnl', 0):+.0f}")
    print(f"  胜率: {overall.get('win_rate', 0)*100:.1f}%")
    print(f"  payoff: {overall.get('payoff_ratio', 0):.4f}")
    print(f"  盈利股票: {overall.get('profitable_stocks', 0)}/{overall.get('stocks', 0)}")
    print(f"  成本/毛利: {overall.get('total_cost', 0)/max(abs(overall.get('gross_pnl', 1)),1)*100:.1f}%")
    print(f"  耗时: {elapsed/60:.1f}分钟")

    # 保存
    out = {
        "step": "K_mean_reversion_no_amplitude_full500",
        "stocks_count": len(ALL_CODES),
        "period": f"{START_DATE}~{END_DATE}",
        "amplitude_filter": False,
        "overall": overall,
        "per_stock": all_per_stock,
        "batch_elapsed_minutes": round(elapsed / 60, 1),
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "K_mr_no_amp_full500.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
