#!/usr/bin/env python3
"""
从 zz500_5min 的500只股票中排除原36只池子，按60日日均振幅筛选50只。
振幅口径与 filter_codes_by_amplitude 一致：(high-low)/prev_close。
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from at0.paths import ZZ500_5MIN_DIR

# 原36只池子
EXCLUDE_36 = {
    "001221", "001309", "001389", "002261", "300339", "300475", "300548",
    "300570", "300620", "300735", "300757", "300972", "301526", "301536",
    "301606", "301611", "600602", "601099", "603000", "603119", "603175",
    "603728", "688166", "688213", "688318", "688322", "688331", "688361",
    "688411", "688498", "688582", "688615", "688629", "688692", "688702",
    "688709",
}

START = "2023-07-25"
END = "2026-07-22"
AMP_THRESHOLD = 0.0494  # 与生产一致
WINDOW = 60


def compute_amplitude(code: str) -> float | None:
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    daily_bars = d.get("daily_bars", {})
    if not daily_bars:
        return None
    sorted_dates = sorted(daily_bars.keys())
    in_range = [d for d in sorted_dates if START <= d <= END]
    use_dates = in_range[:WINDOW] if len(in_range) >= WINDOW else in_range
    amps = []
    prev_close = None
    for date in use_dates:
        bars = daily_bars[date]
        if not bars:
            continue
        high = max(b["high"] for b in bars)
        low = min(b["low"] for b in bars)
        if prev_close and prev_close > 0:
            amps.append((high - low) / prev_close)
        prev_close = bars[-1]["close"]
    if not amps:
        return None
    return sum(amps) / len(amps)


def main():
    all_files = sorted(ZZ500_5MIN_DIR.glob("*.json"))
    all_codes = [f.stem for f in all_files]
    candidate_codes = [c for c in all_codes if c not in EXCLUDE_36]
    print(f"总文件: {len(all_codes)}, 排除原36只后候选: {len(candidate_codes)}")

    amp_list = []
    for code in candidate_codes:
        amp = compute_amplitude(code)
        if amp is not None:
            amp_list.append((code, amp))

    # 按振幅筛选
    passed = [(c, a) for c, a in amp_list if a >= AMP_THRESHOLD]
    passed.sort(key=lambda x: -x[1])  # 振幅从高到低

    print(f"\n振幅≥{AMP_THRESHOLD*100:.2f}%的股票: {len(passed)} 只")
    print(f"\n取前50只（按振幅降序）:")
    print("-" * 50)
    pool_50 = [c for c, a in passed[:50]]
    for i, (code, amp) in enumerate(passed[:50], 1):
        print(f"  {i:>2}. {code}  amp={amp*100:.2f}%")

    # 输出为 Python 列表格式，方便复制
    print(f"\nPOOL_50 = [")
    for i in range(0, len(pool_50), 7):
        chunk = pool_50[i:i+7]
        print(f"    {','.join(repr(c) for c in chunk)}{',' if i+7 < len(pool_50) else ''}")
    print(f"]")

    # 保存到json
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "pool_50_zz500.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "source": "zz500_5min",
            "exclude": sorted(EXCLUDE_36),
            "amp_threshold": AMP_THRESHOLD,
            "amp_window": WINDOW,
            "start": START,
            "end": END,
            "pool_50": pool_50,
            "amplitudes": {c: a for c, a in passed[:50]},
        }, f, ensure_ascii=False, indent=2)
    print(f"\n保存: {out_path}")


if __name__ == "__main__":
    main()
