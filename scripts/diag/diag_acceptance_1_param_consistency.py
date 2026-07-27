#!/usr/bin/env python3
"""
验收1：参数溯源一致性断言
==========================
核对 config/thresholds.yaml 里每个 section 的每个字段，
load_*_params() 返回值必须与 yaml 完全一致。

通过标准：所有 yaml 字段加载值 == yaml 值，0 mismatch。
（yaml 未覆盖的字段保留 dataclass 默认值，属设计意图，不算 drift。）
"""
from __future__ import annotations

import sys
from dataclasses import fields as dc_fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import yaml
from at0.config import (
    CONFIG_FILE,
    load_signal_params,
    load_risk_params,
    load_backtest_params,
    load_cost_model,
    load_exposure_policy,
    load_screener_params,
)


def load_yaml_raw() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_section(yaml_section: dict, loaded_obj, section_name: str) -> tuple[int, int, list[str]]:
    """核对 yaml section 的每个字段是否与加载对象一致。返回 (checked, mismatch, lines)。"""
    checked = 0
    mismatch = 0
    lines = []
    for k, yaml_val in yaml_section.items():
        if not hasattr(loaded_obj, k):
            # yaml 字段在 dataclass 里不存在，加载器会跳过——记录但不算 mismatch
            lines.append(f"  [{section_name}] {k}: yaml={yaml_val!r} → dataclass 无此字段（跳过）")
            continue
        actual = getattr(loaded_obj, k)
        checked += 1
        if actual == yaml_val:
            lines.append(f"  [{section_name}] {k}: yaml={yaml_val!r} == loaded={actual!r}  OK")
        else:
            mismatch += 1
            lines.append(f"  [{section_name}] {k}: yaml={yaml_val!r} != loaded={actual!r}  MISMATCH")
    return checked, mismatch, lines


def main() -> int:
    print("=" * 78)
    print("验收1：参数溯源一致性断言")
    print("=" * 78)
    print(f"yaml: {CONFIG_FILE}")
    print()

    data = load_yaml_raw()
    total_checked = 0
    total_mismatch = 0

    # signal → SignalParams
    if "signal" in data:
        print("── section: signal → SignalParams ──")
        c, m, lines = check_section(data["signal"], load_signal_params(), "signal")
        print("\n".join(lines))
        total_checked += c
        total_mismatch += m

    # risk → RiskParams
    if "risk" in data:
        print("\n── section: risk → RiskParams ──")
        c, m, lines = check_section(data["risk"], load_risk_params(), "risk")
        print("\n".join(lines))
        total_checked += c
        total_mismatch += m

    # backtest → BacktestParams
    if "backtest" in data:
        print("\n── section: backtest → BacktestParams ──")
        c, m, lines = check_section(data["backtest"], load_backtest_params(), "backtest")
        print("\n".join(lines))
        total_checked += c
        total_mismatch += m

    # cost → CostModel
    if "cost" in data:
        print("\n── section: cost → CostModel ──")
        c, m, lines = check_section(data["cost"], load_cost_model(), "cost")
        print("\n".join(lines))
        total_checked += c
        total_mismatch += m

    # exposure_policy → ExposurePolicy
    if "exposure_policy" in data:
        print("\n── section: exposure_policy → ExposurePolicy ──")
        c, m, lines = check_section(data["exposure_policy"], load_exposure_policy(), "exposure_policy")
        print("\n".join(lines))
        total_checked += c
        total_mismatch += m

    # screener → ScreenerParams
    if "screener" in data:
        print("\n── section: screener → ScreenerParams ──")
        c, m, lines = check_section(data["screener"], load_screener_params(), "screener")
        print("\n".join(lines))
        total_checked += c
        total_mismatch += m

    print("\n" + "=" * 78)
    print(f"总计：核对 {total_checked} 个字段，mismatch {total_mismatch} 个")
    print("=" * 78)
    if total_mismatch == 0:
        print("结论：PASS — 所有 yaml 字段加载值与 yaml 完全一致")
        return 0
    else:
        print("结论：FAIL — 存在 mismatch，需排查 config.py 加载逻辑")
        return 1


if __name__ == "__main__":
    sys.exit(main())
