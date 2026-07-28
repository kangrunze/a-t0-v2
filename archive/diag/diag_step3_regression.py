#!/usr/bin/env python3
"""
步骤3 回归测试：修复配置管理bug后，验证默认调用（不传 override）的参数
确实来自 thresholds.yaml，并与"直接读 yaml"路径一致。

同时跑一次30股批量，对比步骤1（显式 override 仅 stop_loss/trailing_act）
以暴露其他 drift 字段（如 cooldown_bars: yaml=3 vs dataclass=12）的影响。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from at0.config import load_signal_params, load_risk_params, load_backtest_params
from backtest_zz500 import run_zz500_batch, run_zz500_single

BASELINE_CODES = [
    "300140", "603119", "000878", "300024", "000951",
    "002056", "600879", "300957", "002065", "601019",
    "603568", "603766", "002261", "301498", "688561",
    "000997", "601865", "300136", "300972", "600312",
    "688180", "600909", "600602", "603225", "000060",
    "688331", "600298", "600098", "600256", "301200",
]

START_DATE = "2026-04-27"
END_DATE = "2026-07-23"
DATA_DIR = Path(r"D:\project\data\zz500_5min")
TAG = "diag_step3_regression_default"

# 关键 drift 字段（yaml 值 vs dataclass 默认值）
DRIFT_FIELDS = {
    "bp": {
        "stop_loss_ratio": 0.004,           # yaml=0.004, dataclass=0.008
        "trailing_activation_pct": 0.0,     # yaml=0.0,   dataclass=0.005
        "cooldown_bars": 3,                 # yaml=3,     dataclass=12
    },
    "sp": {
        "use_continuous_alpha": False,      # yaml=false
        "vwap_dev_atr_multiplier": 0.8,     # yaml=0.8
    },
    "rp": {
        "min_capture_spread": 0.0072,       # yaml=0.0072
        "max_t_size_ratio": 0.25,           # yaml=0.25
    },
}


def verify_param_loading() -> bool:
    """验证 config 加载器返回的参数与 yaml 期望值一致。"""
    print("=" * 78)
    print("回归测试 1：验证 config 加载器读取 yaml 值")
    print("=" * 78)
    ok = True
    sp = load_signal_params()
    rp = load_risk_params()
    bp = load_backtest_params()

    for field, expected in DRIFT_FIELDS["bp"].items():
        actual = getattr(bp, field)
        status = "OK" if actual == expected else "MISMATCH"
        print(f"  bp.{field:<28} expected={expected:<8} actual={actual:<8} [{status}]")
        if actual != expected:
            ok = False
    for field, expected in DRIFT_FIELDS["sp"].items():
        actual = getattr(sp, field)
        status = "OK" if actual == expected else "MISMATCH"
        print(f"  sp.{field:<28} expected={expected:<8} actual={actual:<8} [{status}]")
        if actual != expected:
            ok = False
    for field, expected in DRIFT_FIELDS["rp"].items():
        actual = getattr(rp, field)
        status = "OK" if actual == expected else "MISMATCH"
        print(f"  rp.{field:<28} expected={expected:<8} actual={actual:<8} [{status}]")
        if actual != expected:
            ok = False
    print(f"\n  结果: {'全部通过' if ok else '存在 MISMATCH'}")
    return ok


def verify_default_call_uses_yaml() -> bool:
    """验证 run_zz500_single 不传 override 时，构造的 params 来自 yaml。"""
    print("\n" + "=" * 78)
    print("回归测试 2：验证 run_zz500_single 不传 override 时走 yaml 路径")
    print("=" * 78)
    # 捕获 run_zz500_single 内部打印的参数行
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_zz500_single(
            code="300140",
            start_date=START_DATE,
            end_date=END_DATE,
            data_dir=DATA_DIR,
            base_shares=3000,
            avg_cost=None,
            tag="regression_probe",
        )
    log = buf.getvalue()
    # 提取 stop_loss= 行
    import re
    m = re.search(r"stop_loss=([\d.]+).*?cooldown=(\d+)", log)
    if not m:
        print(f"  [WARN] 未在日志中找到参数行")
        return False
    stop_loss = float(m.group(1))
    cooldown = int(m.group(2))
    print(f"  日志参数: stop_loss={stop_loss}, cooldown={cooldown}")
    ok_sl = stop_loss == 0.004
    ok_cd = cooldown == 3
    print(f"  stop_loss==0.004 (yaml): {'OK' if ok_sl else 'FAIL'}")
    print(f"  cooldown==3 (yaml):      {'OK' if ok_cd else 'FAIL'}")
    return ok_sl and ok_cd


def run_default_batch_and_compare() -> dict:
    """跑30股默认批量，对比步骤1（显式 override）以暴露 cooldown_bars drift 影响。"""
    print("\n" + "=" * 78)
    print("回归测试 3：默认批量（不传 override）vs 步骤1（显式 override）")
    print("=" * 78)
    print("目的：步骤1只 override 了 stop_loss/trailing_act，其余用 dataclass 默认；")
    print("      修复后默认路径全部从 yaml 加载，cooldown_bars 从 dataclass=12 变为 yaml=3")
    print("      此对比暴露 cooldown_bars drift 的影响。")
    print()

    result = run_zz500_batch(
        codes=BASELINE_CODES,
        start_date=START_DATE,
        end_date=END_DATE,
        data_dir=DATA_DIR,
        base_shares=3000,
        tag=TAG,
        params_override=None,  # 不传 override，走 yaml 默认
    )

    # 加载步骤1结果对比
    step1_path = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_diag_step1_yaml_params.json"
    with open(step1_path, "r", encoding="utf-8") as f:
        step1 = json.load(f)

    s1 = step1["overall"]
    s3 = result["overall"]
    print("\n" + "=" * 88)
    print("步骤3 默认(yaml全量) vs 步骤1(仅override stop_loss/trailing_act)")
    print("=" * 88)
    print(f"{'指标':<22} {'步骤1(partial)':>18} {'步骤3(yaml全量)':>18} {'变化':>14}")
    print("-" * 88)
    rows = [
        ("total_trades",      s1["total_trades"],      s3["total_trades"],      "{:d}"),
        ("paired_trades",     s1["paired_trades"],     s3["paired_trades"],     "{:d}"),
        ("win_rate",          s1["win_rate"],          s3["win_rate"],          "{:.4f}"),
        ("gross_pnl",         s1["gross_pnl"],         s3["gross_pnl"],         "{:+.2f}"),
        ("total_cost",        s1["total_cost"],        s3["total_cost"],        "{:.2f}"),
        ("net_pnl",           s1["net_pnl"],           s3["net_pnl"],           "{:+.2f}"),
        ("net_pnl_w_unreal",  s1["net_pnl_with_unrealized"], s3["net_pnl_with_unrealized"], "{:+.2f}"),
        ("profitable_stocks", s1["profitable_stocks"], s3["profitable_stocks"], "{:d}"),
        ("losing_stocks",     s1["losing_stocks"],     s3["losing_stocks"],     "{:d}"),
    ]
    for name, s1v, s3v, fmt in rows:
        delta = s3v - s1v
        delta_str = f"{delta:+.2f}" if isinstance(s3v, float) else f"{delta:+d}"
        print(f"{name:<22} {fmt.format(s1v):>18} {fmt.format(s3v):>18} {delta_str:>14}")
    print("=" * 88)
    print("注：差异主要来自 cooldown_bars (步骤1=12 dataclass默认, 步骤3=3 yaml)")
    return result


def main() -> int:
    ok1 = verify_param_loading()
    ok2 = verify_default_call_uses_yaml()
    if not (ok1 and ok2):
        print("\n[FAIL] 参数加载验证未通过，不继续批量回测", file=sys.stderr)
        return 1
    result = run_default_batch_and_compare()

    out_path = PROJECT_ROOT / "outputs" / "backtest" / f"{TAG}_comparison.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "step": "step3_regression",
            "description": "default call (no override) now reads yaml; compares with step1 partial override",
            "step3_default": result["overall"],
            "drift_fields_corrected": DRIFT_FIELDS,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n回归结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
