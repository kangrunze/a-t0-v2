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
AMP_WINDOW_START = "2023-05-04"  # 回测前约 60 个交易日（2023-07-25 往前推）
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
    保留用于兼容性，但 M12 修复后 main 不再使用该函数。
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


def compute_amp_from_baostock(code: str) -> float | None:
    """
    用 baostock 获取回测期前 60 个交易日的日均振幅。

    M12 修复（2026-08-04）：本地数据从回测起始日开始，无更早数据，
    使用 baostock 获取历史数据消除前视偏差。

    振幅口径：(high - low) / prev_close（与 screener.py L80 一致）
    返回日均振幅，失败返回 None。
    """
    import baostock as bs

    # 归一化代码格式
    s = code.strip().lower()
    if len(s) == 6 and s.isdigit():
        head = s[0]
        if head == "6":
            bs_code = f"sh.{s}"
        elif head in ("0", "3"):
            bs_code = f"sz.{s}"
        else:
            return None
    else:
        return None

    lg = bs.login()
    if lg.error_code != "0":
        return None
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,high,low,close,preclose",
            start_date=AMP_WINDOW_START,
            end_date=BACKTEST_START,
            frequency="d",
            adjustflag="2",
        )
        if rs.error_code != "0":
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if len(rows) < WINDOW // 2:
            return None

        use_rows = rows[-WINDOW:]
        amps = []
        for row in use_rows:
            try:
                high = float(row[1])
                low = float(row[2])
                preclose = float(row[4])
                if preclose > 0:
                    amps.append((high - low) / preclose)
            except (ValueError, IndexError):
                continue

        if not amps:
            return None
        return sum(amps) / len(amps)
    finally:
        bs.logout()


def main():
    print("=" * 70)
    print(f"按 60 日振幅筛选股票池")
    print(f"回测开始日: {BACKTEST_START}")
    print(f"振幅窗口: 回测期前 {WINDOW} 个交易日（{AMP_WINDOW_START} ~ {BACKTEST_START}）")
    print(f"振幅阈值: {THRESHOLD*100:.2f}%（Stage D Youden 最优切点）")
    print(f"振幅口径: (high - low) / prev_close（与 screener.py 一致）")
    print(f"数据源: baostock（M12 修复：消除前视偏差）")
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
        # M12 修复：使用 baostock 获取回测期前数据，消除前视偏差
        amp = compute_amp_from_baostock(code)

        if amp is None:
            no_data.append(code)
            continue

        rec = {"code": code, "amp": amp, "n_used": WINDOW}
        if amp >= THRESHOLD:
            passed.append(rec)
        else:
            filtered.append(rec)

        if (i + 1) % 20 == 0:
            print(f"  已处理 {i+1}/{len(codes)} 通过={len(passed)} 筛掉={len(filtered)}")

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
