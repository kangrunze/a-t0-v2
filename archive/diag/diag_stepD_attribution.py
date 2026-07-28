"""
Stage D — 按盈亏分组做归因分析
=============================
诊断性质，不涉及改代码。

对 batch_summary_zz100_3m.json 中的 100 只股票按 net_pnl_with_unrealized 盈亏分组：
  - profitable 组（net > 0）
  - losing 组（net < 0）

对比两组在样本期（2026-04-27~2026-07-23）内的：
  1. 日线 ADX 均值分布（趋势强度）
  2. 日均成交量（流动性）
  3. 日均振幅（波动性）
  4. 趋势日占比（ADX > 25 的天数比例）
  5. 上涨/下跌/震荡日占比

数据源：D:\\project\\data\\zz500_5min\\{code}.json 中的 daily_bars（5min K线）
       日线 ADX 用 features.dmi() 从合成日K计算，或直接从 5min 合成日K算

输出：
  - 控制台对比表
  - outputs/backtest/stepD_attribution.json（结构化结果）
"""
from __future__ import annotations

import json
import sys
import importlib.util
from pathlib import Path
from statistics import mean, median, stdev

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 加载 at0.paths（带 importlib 兜底）
def _load_at0_paths():
    try:
        from at0.paths import DATA_ROOT, ZZ500_5MIN_DIR
        return DATA_ROOT, ZZ500_5MIN_DIR
    except (ValueError, ImportError):
        paths_path = PROJECT_ROOT / "src" / "at0" / "paths.py"
        spec = importlib.util.spec_from_file_location("_at0_paths", str(paths_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.DATA_ROOT, mod.ZZ500_5MIN_DIR

DATA_ROOT, ZZ500_5MIN_DIR = _load_at0_paths()

# 加载 at0.features.dmi（带 importlib 兜底）
def _load_dmi():
    try:
        from at0.features import dmi
        return dmi
    except (ValueError, ImportError):
        feats_path = PROJECT_ROOT / "src" / "at0" / "features.py"
        spec = importlib.util.spec_from_file_location("_at0_feats", str(feats_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.dmi

dmi = _load_dmi()


BATCH_SUMMARY = PROJECT_ROOT / "outputs" / "backtest" / "batch_summary_zz100_3m.json"
START_DATE = "2026-04-27"
END_DATE = "2026-07-23"
ADX_TREND_THRESHOLD = 25.0  # ADX > 25 视为趋势日（与 strategy.py adx_trend_threshold 对齐）
OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtest" / "stepD_attribution.json"


def load_stock_daily_bars(code: str) -> dict:
    """加载 {code}.json，返回 {date_str: [bars]} 字典（仅样本区间内）。"""
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    daily_bars = data.get("daily_bars", {})
    return {d: bars for d, bars in daily_bars.items() if START_DATE <= d <= END_DATE}


def compute_daily_features(daily_bars: dict) -> list[dict]:
    """从 5min bars 合成日K，计算每日 ADX/成交量/振幅。

    返回 [{"date", "adx", "volume", "amplitude", "close", "prev_close"}]
    """
    if not daily_bars:
        return []
    sorted_dates = sorted(daily_bars.keys())
    # 合成日K
    daily_klines = []
    prev_close = None
    for date in sorted_dates:
        bars = daily_bars[date]
        if not bars:
            continue
        high = max(b["high"] for b in bars)
        low = min(b["low"] for b in bars)
        opn = bars[0]["open"]
        close = bars[-1]["close"]
        volume = sum(b.get("volume", 0) for b in bars)
        daily_klines.append({
            "date": date,
            "high": high, "low": low, "open": opn, "close": close,
            "volume": volume,
            "prev_close": prev_close,
        })
        prev_close = close

    # 用 features.dmi 计算 ADX 序列（需至少 period+1 根日K）
    # dmi 接收 [{"high","low","close"}] 格式的 bars
    dmi_bars = [{"high": k["high"], "low": k["low"], "close": k["close"]} for k in daily_klines]
    # dmi 返回最后一个 bar 的 (pdi, mdi, adx)，需逐日计算
    # 为效率，直接调 dmi 一次取最后一日 ADX 不够，需逐日递推
    # 但 features.dmi 已是递推实现，传全部 bars 取最后值即可
    # 改为逐日计算：对每个 i 传 dmi_bars[:i+1]
    results = []
    for i, k in enumerate(daily_klines):
        adx = None
        if i >= 14:  # dmi 默认 period=14
            pdi, mdi, adx_val = dmi(dmi_bars[: i + 1], period=14)
            if adx_val is not None:
                adx = adx_val
        amplitude = (k["high"] - k["low"]) / k["open"] if k["open"] > 0 else 0
        results.append({
            "date": k["date"],
            "adx": adx,
            "volume": k["volume"],
            "amplitude": amplitude,
            "close": k["close"],
            "prev_close": k["prev_close"],
        })
    return results


def summarize_stock_features(daily_feats: list[dict]) -> dict:
    """聚合单只股票的样本期特征。"""
    if not daily_feats:
        return {}
    adxs = [d["adx"] for d in daily_feats if d["adx"] is not None]
    vols = [d["volume"] for d in daily_feats]
    amps = [d["amplitude"] for d in daily_feats]
    # 趋势日占比
    trend_days = sum(1 for a in adxs if a is not None and a > ADX_TREND_THRESHOLD)
    trend_ratio = trend_days / len(adxs) if adxs else 0
    # 上涨/下跌日占比（用 close vs prev_close）
    up_days = sum(1 for d in daily_feats if d["prev_close"] and d["close"] > d["prev_close"])
    down_days = sum(1 for d in daily_feats if d["prev_close"] and d["close"] < d["prev_close"])
    range_days = sum(1 for d in daily_feats if d["prev_close"] and abs(d["close"] - d["prev_close"]) / d["prev_close"] < 0.003)
    total_dir = up_days + down_days
    return {
        "days": len(daily_feats),
        "adx_mean": round(mean(adxs), 2) if adxs else None,
        "adx_median": round(median(adxs), 2) if adxs else None,
        "adx_std": round(stdev(adxs), 2) if len(adxs) > 1 else 0,
        "adx_trend_ratio": round(trend_ratio, 4),
        "volume_mean": round(mean(vols), 0) if vols else 0,
        "volume_median": round(median(vols), 0) if vols else 0,
        "amplitude_mean": round(mean(amps) * 100, 2) if amps else 0,
        "amplitude_median": round(median(amps) * 100, 2) if amps else 0,
        "up_days_ratio": round(up_days / total_dir, 4) if total_dir else 0,
        "down_days_ratio": round(down_days / total_dir, 4) if total_dir else 0,
        "range_days_ratio": round(range_days / len(daily_feats), 4) if daily_feats else 0,
    }


def group_stats(features_list: list[dict]) -> dict:
    """聚合组内统计。"""
    if not features_list:
        return {}
    adx_means = [f["adx_mean"] for f in features_list if f.get("adx_mean") is not None]
    adx_trend_ratios = [f["adx_trend_ratio"] for f in features_list if "adx_trend_ratio" in f]
    vol_means = [f["volume_mean"] for f in features_list if f.get("volume_mean")]
    amp_means = [f["amplitude_mean"] for f in features_list if "amplitude_mean" in f]
    up_ratios = [f["up_days_ratio"] for f in features_list if "up_days_ratio" in f]
    down_ratios = [f["down_days_ratio"] for f in features_list if "down_days_ratio" in f]
    range_ratios = [f["range_days_ratio"] for f in features_list if "range_days_ratio" in f]
    return {
        "stocks": len(features_list),
        "adx_mean_avg": round(mean(adx_means), 2) if adx_means else None,
        "adx_mean_median": round(median(adx_means), 2) if adx_means else None,
        "adx_mean_std": round(stdev(adx_means), 2) if len(adx_means) > 1 else 0,
        "adx_trend_ratio_avg": round(mean(adx_trend_ratios), 4) if adx_trend_ratios else 0,
        "volume_mean_avg": round(mean(vol_means), 0) if vol_means else 0,
        "volume_mean_median": round(median(vol_means), 0) if vol_means else 0,
        "amplitude_mean_avg": round(mean(amp_means), 2) if amp_means else 0,
        "amplitude_mean_median": round(median(amp_means), 2) if amp_means else 0,
        "up_days_ratio_avg": round(mean(up_ratios), 4) if up_ratios else 0,
        "down_days_ratio_avg": round(mean(down_ratios), 4) if down_ratios else 0,
        "range_days_ratio_avg": round(mean(range_ratios), 4) if range_ratios else 0,
    }


def main():
    print("=" * 78)
    print("Stage D — 按盈亏分组做归因分析")
    print(f"样本: 100 只 ZZ500 股票, {START_DATE} ~ {END_DATE}")
    print("=" * 78)

    with open(BATCH_SUMMARY, "r", encoding="utf-8") as f:
        batch = json.load(f)

    per_stock = batch["per_stock"]
    profitable = [s for s in per_stock if s.get("net_pnl_with_unrealized", s["net_pnl"]) > 0]
    losing = [s for s in per_stock if s.get("net_pnl_with_unrealized", s["net_pnl"]) < 0]

    print(f"\n盈利组: {len(profitable)} 只 | 亏损组: {len(losing)} 只")
    print(f"计算每只股票的日线 ADX/成交量/振幅...")

    profitable_feats = []
    losing_feats = []
    for i, s in enumerate(per_stock):
        code = s["code"]
        net = s.get("net_pnl_with_unrealized", s["net_pnl"])
        daily_bars = load_stock_daily_bars(code)
        if not daily_bars:
            print(f"  [{i+1}/100] {code} 无数据，跳过")
            continue
        daily_feats = compute_daily_features(daily_bars)
        feats = summarize_stock_features(daily_feats)
        feats["code"] = code
        feats["net_pnl"] = net
        feats["win_rate"] = s["win_rate"]
        feats["paired_trades"] = s["paired_trades"]
        if net > 0:
            profitable_feats.append(feats)
        elif net < 0:
            losing_feats.append(feats)
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/100] 已处理")

    print(f"\n盈利组特征聚合: {len(profitable_feats)} 只")
    print(f"亏损组特征聚合: {len(losing_feats)} 只")

    profitable_stats = group_stats(profitable_feats)
    losing_stats = group_stats(losing_feats)

    # ── 对比表 ──
    print("\n" + "=" * 78)
    print("盈亏组特征对比")
    print("=" * 78)
    print(f"{'指标':<28} {'盈利组':>18} {'亏损组':>18} {'差异':>14}")
    print("-" * 78)

    def fmt(v, suffix=""):
        if v is None:
            return "N/A"
        if isinstance(v, float):
            return f"{v:,.2f}{suffix}"
        return f"{v:,}{suffix}"

    rows = [
        ("股票数", "stocks", ""),
        ("ADX均值 (平均)", "adx_mean_avg", ""),
        ("ADX均值 (中位)", "adx_mean_median", ""),
        ("ADX均值 (标准差)", "adx_mean_std", ""),
        ("趋势日占比 ADX>25", "adx_trend_ratio_avg", " %"),
        ("日均成交量 (平均)", "volume_mean_avg", ""),
        ("日均成交量 (中位)", "volume_mean_median", ""),
        ("日均振幅 (平均) %", "amplitude_mean_avg", ""),
        ("日均振幅 (中位) %", "amplitude_mean_median", ""),
        ("上涨日占比", "up_days_ratio_avg", " %"),
        ("下跌日占比", "down_days_ratio_avg", " %"),
        ("震荡日占比 (日涨跌<0.3%)", "range_days_ratio_avg", " %"),
    ]
    for label, key, suffix in rows:
        p = profitable_stats.get(key)
        l = losing_stats.get(key)
        diff = ""
        if p is not None and l is not None and isinstance(p, (int, float)):
            if p != 0:
                diff_pct = (p - l) / p * 100 if p != 0 else 0
                diff = f"{diff_pct:+.1f}%"
        print(f"{label:<28} {fmt(p, suffix):>18} {fmt(l, suffix):>18} {diff:>14}")

    # ── 分位数分布（ADX均值） ──
    print("\n" + "=" * 78)
    print("ADX均值分位数分布")
    print("=" * 78)
    p_adx = sorted([f["adx_mean"] for f in profitable_feats if f.get("adx_mean") is not None])
    l_adx = sorted([f["adx_mean"] for f in losing_feats if f.get("adx_mean") is not None])

    def quantiles(arr, name):
        if not arr:
            print(f"  {name}: 无数据")
            return
        n = len(arr)
        print(f"  {name} (n={n}):")
        print(f"    min={arr[0]:.2f}  p25={arr[n//4]:.2f}  median={arr[n//2]:.2f}  p75={arr[3*n//4]:.2f}  max={arr[-1]:.2f}")

    quantiles(p_adx, "盈利组")
    quantiles(l_adx, "亏损组")

    # ── 振幅分位数 ──
    print("\n" + "=" * 78)
    print("日均振幅分位数分布 (%)")
    print("=" * 78)
    p_amp = sorted([f["amplitude_mean"] for f in profitable_feats])
    l_amp = sorted([f["amplitude_mean"] for f in losing_feats])
    quantiles(p_amp, "盈利组")
    quantiles(l_amp, "亏损组")

    # ── 结论判断 ──
    print("\n" + "=" * 78)
    print("归因结论")
    print("=" * 78)
    if profitable_stats.get("adx_mean_avg") and losing_stats.get("adx_mean_avg"):
        adx_diff = profitable_stats["adx_mean_avg"] - losing_stats["adx_mean_avg"]
        trend_diff = profitable_stats["adx_trend_ratio_avg"] - losing_stats["adx_trend_ratio_avg"]
        vol_diff_pct = ((profitable_stats["volume_mean_avg"] - losing_stats["volume_mean_avg"])
                        / profitable_stats["volume_mean_avg"] * 100) if profitable_stats["volume_mean_avg"] else 0
        amp_diff = profitable_stats["amplitude_mean_avg"] - losing_stats["amplitude_mean_avg"]

        print(f"  ADX均值差异: 盈利组 {profitable_stats['adx_mean_avg']:.2f} vs 亏损组 {losing_stats['adx_mean_avg']:.2f} (差 {adx_diff:+.2f})")
        print(f"  趋势日占比差异: 盈利组 {profitable_stats['adx_trend_ratio_avg']*100:.1f}% vs 亏损组 {losing_stats['adx_trend_ratio_avg']*100:.1f}% (差 {trend_diff*100:+.1f}pp)")
        print(f"  日均成交量差异: {vol_diff_pct:+.1f}%")
        print(f"  日均振幅差异: 盈利组 {profitable_stats['amplitude_mean_avg']:.2f}% vs 亏损组 {losing_stats['amplitude_mean_avg']:.2f}% (差 {amp_diff:+.2f}pp)")

        print("\n  判断:")
        if abs(adx_diff) < 2 and abs(trend_diff) < 0.1:
            print("  → ADX/趋势度差异不显著（<2 ADX单位 / <10pp 趋势日占比）")
            print("  → 策略在不同 ADX 环境下表现一致，问题不在 regime 过滤，")
            print("  → 更可能在选股池（成交量/振幅/个股特性）或策略本身。")
        elif adx_diff > 2:
            print("  → 盈利组 ADX 显著更高，策略在趋势市表现更好。")
            print("  → 应加强 regime 过滤（Stage E 方向），在低 ADX 股票/时段禁交易。")
        elif adx_diff < -2:
            print("  → 盈利组 ADX 反而更低，策略在震荡市表现更好（与趋势跟随假设矛盾）。")
            print("  → 需重新审视策略方向假设，或检查 ADX 计算口径。")

        if abs(amp_diff) > 0.5:
            print(f"  → 振幅差异显著（{amp_diff:+.2f}pp），{'盈利组振幅更大，策略依赖波动' if amp_diff > 0 else '盈利组振幅更小，策略偏好低波动'}")

    # ── 落盘 ──
    output = {
        "sample": {
            "start": START_DATE,
            "end": END_DATE,
            "total_stocks": len(per_stock),
            "profitable_count": len(profitable),
            "losing_count": len(losing),
        },
        "profitable_stats": profitable_stats,
        "losing_stats": losing_stats,
        "profitable_per_stock": profitable_feats,
        "losing_per_stock": losing_feats,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结构化结果 -> {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
