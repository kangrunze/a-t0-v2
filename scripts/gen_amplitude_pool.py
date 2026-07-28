#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按 60 日振幅筛选股票池（gen_amplitude_pool）
============================================
模拟 screener.py 的 min_amplitude_long 检查，从 5min 数据合成日K，计算每只股票
在回测开始前的 60 日日均振幅，按阈值 0.0494 分成两组：
  - passed：60日日均振幅 >= 0.0494（通过筛选）
  - filtered：60日日均振幅 < 0.0494（被筛掉）

振幅口径与 screener.py L80 一致：(high - low) / prev_close
窗口：回测开始日（2023-07-25）之前的最近 60 个交易日

输出：
  - passed_codes.txt / filtered_codes.txt（股票代码列表，每行一个）
  - 控制台打印两组股票数和分布
"""
import json
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────────
DATA_DIR = Path(r"D:\project\data\zz500_5min")
OUT_DIR = Path(r"d:\project\a-t0-v2\outputs\amplitude_pool")
BACKTEST_START = "2023-07-25"  # 回测开始日
BACKTEST_END = "2026-07-22"    # 回测结束日
WINDOW = 60                    # 60 个交易日
THRESHOLD = 0.0494             # Stage D Youden 最优切点


def synth_daily_klines(daily_bars: dict):
    """
    从 5min bars 合成日K，返回 [{date, high, low, close, prev_close}] 按日期升序。
    prev_close 取前一日的合成 close。
    """
    sorted_dates = sorted(daily_bars.keys())
    result = []
    prev_close = None
    for date in sorted_dates:
        bars = daily_bars[date]
        if not bars:
            continue
        high = max(b["high"] for b in bars)
        low = min(b["low"] for b in bars)
        close = bars[-1]["close"]
        result.append({
            "date": date,
            "high": high,
            "low": low,
            "close": close,
            "prev_close": prev_close,
        })
        prev_close = close
    return result


def compute_60d_amplitude(daily_klines, start_date, end_date, window=60):
    """
    计算回测期前 window 个交易日的日均振幅。
    本地数据从 start_date 开始，没有更早数据，所以用回测期前 60 日
    （start_date 之后的 60 个交易日）。这有轻微前视偏差，但本地数据限制下
    唯一可行，且与 diag_stop_ratio_vs_amplitude.py 口径一致（全回测期均值）。

    振幅口径：(high - low) / prev_close（与 screener.py L80 一致）
    """
    in_range = [k for k in daily_klines if start_date <= k["date"] <= end_date]
    if len(in_range) < window:
        use = in_range
    else:
        use = in_range[:window]
    amps = []
    for k in use:
        if k["prev_close"] and k["prev_close"] > 0:
            amps.append((k["high"] - k["low"]) / k["prev_close"])
    if not amps:
        return None, 0, 0
    return sum(amps) / len(amps), len(amps), len(use)


def main():
    print("=" * 70)
    print(f"按 60 日振幅筛选股票池")
    print(f"回测开始日: {BACKTEST_START}")
    print(f"振幅窗口: 回测期前 {WINDOW} 个交易日（2023-07-25起）")
    print(f"振幅阈值: {THRESHOLD*100:.2f}%（Stage D Youden 最优切点）")
    print(f"振幅口径: (high - low) / prev_close（与 screener.py 一致）")
    print("=" * 70)

    # 读取 100 股 seed=42 的股票列表（从已有的 baseline report 提取）
    report_dir = Path(r"d:\project\a-t0-v2\outputs\backtest")
    reports = sorted(report_dir.glob("*_sample100_3y_2023-07-25_2026-07-22_report.json"))
    codes = [rp.name.split("_")[0] for rp in reports]
    print(f"样本股票数: {len(codes)}")

    passed = []
    filtered = []
    no_data = []

    for i, code in enumerate(codes):
        path = DATA_DIR / f"{code}.json"
        if not path.exists():
            no_data.append(code)
            continue
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        daily_bars = d.get("daily_bars", {})
        if not daily_bars:
            no_data.append(code)
            continue

        klines = synth_daily_klines(daily_bars)
        amp, n_used, n_total = compute_60d_amplitude(klines, BACKTEST_START, BACKTEST_END, WINDOW)

        if amp is None:
            no_data.append(code)
            continue

        rec = {"code": code, "amp": amp, "n_used": n_used}
        if amp >= THRESHOLD:
            passed.append(rec)
        else:
            filtered.append(rec)

        if (i + 1) % 20 == 0:
            print(f"  已处理 {i+1}/{len(codes)}")

    print(f"\n结果:")
    print(f"  通过筛选 (amp >= {THRESHOLD*100:.2f}%): {len(passed)} 只")
    print(f"  被筛掉   (amp <  {THRESHOLD*100:.2f}%): {len(filtered)} 只")
    print(f"  无数据/数据不足: {len(no_data)} 只")

    # 振幅分布
    all_amps = [r["amp"] for r in passed + filtered]
    if all_amps:
        all_amps.sort()
        n = len(all_amps)
        print(f"\n  振幅分布: min={all_amps[0]*100:.2f}%  p25={all_amps[n//4]*100:.2f}%  "
              f"p50={all_amps[n//2]*100:.2f}%  p75={all_amps[3*n//4]*100:.2f}%  max={all_amps[-1]*100:.2f}%")

    # 被筛掉的股票详情
    if filtered:
        print(f"\n  被筛掉的 {len(filtered)} 只股票:")
        for r in sorted(filtered, key=lambda x: x["amp"]):
            print(f"    {r['code']}  amp={r['amp']*100:.2f}%  (用了{r['n_used']}日数据)")

    # 输出股票列表文件
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    passed_file = OUT_DIR / "passed_codes.txt"
    filtered_file = OUT_DIR / "filtered_codes.txt"

    with open(passed_file, "w", encoding="utf-8") as f:
        for r in passed:
            f.write(r["code"] + "\n")
    with open(filtered_file, "w", encoding="utf-8") as f:
        for r in filtered:
            f.write(r["code"] + "\n")

    # 同时输出 JSON（含振幅数值，便于后续分析）
    detail_file = OUT_DIR / "amplitude_pool_detail.json"
    with open(detail_file, "w", encoding="utf-8") as f:
        json.dump({
            "backtest_start": BACKTEST_START,
            "window": WINDOW,
            "threshold": THRESHOLD,
            "passed": passed,
            "filtered": filtered,
            "no_data": no_data,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n产物文件:")
    print(f"  {passed_file} ({len(passed)} 只)")
    print(f"  {filtered_file} ({len(filtered)} 只)")
    print(f"  {detail_file}")
    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    main()
