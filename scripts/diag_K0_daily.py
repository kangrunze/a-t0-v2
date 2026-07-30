#!/usr/bin/env python3
"""
K0 跨日闸门诊断（日线粒度）
==========================
K0 原版只验证了日内5分钟粒度的延续/回归，跨日粒度的均值回归是完全不同
的市场机制，尚未验证过。本脚本用和 K0 完全一致的方法论，把"5分钟后"
换成"1-3个交易日后"。

数据：zz500随机50只(排除原36只)，2023-07-25 ~ 2026-07-22（3年）
偏离基准：5日SMA / 20日SMA（两个基准分别测）
标准化：日线 ATR（14日）相对收盘价的比例
前瞻窗口：1/2/3 个交易日
分桶：|dev| / (ATR/close) 的 ATR 倍数（与 K0 一致）

口径对齐 K0：
  - dev > 0 且 future_return > 0 → 延续（继续涨）
  - dev > 0 且 future_return ≤ 0 → 回归（跌回均值）
  - dev < 0 对称处理
  - 期望值 = P(回归)×E(回归幅) - P(延续)×E(延续幅)

交易成本门槛：来回总成本率约 0.33%（佣金万1×2 + 印花税0.05% + 滑点0.1%×2）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR

# 从 zz500_5min 500只中排除原36只后随机选50只（seed=42，忽略振幅筛选）
POOL_50 = [
    "601179", "000983", "000415", "603486", "002945", "002673", "002487",
    "002065", "603379", "000937", "601699", "688676", "600688", "000831",
    "600906", "600126", "000519", "000513", "000887", "002461", "002583",
    "600516", "600977", "000423", "600764", "002407", "603160", "601233",
    "601997", "600105", "002465", "600256", "600901", "002984", "688099",
    "688469", "000032", "603658", "688052", "002155", "601990", "300458",
    "002152", "002444", "603688", "300432", "300857", "000898", "300666",
    "688297",
]

START = "2023-07-25"
END = "2026-07-22"

# ATR 标准化分桶（与 K0 完全一致）
ATR_BUCKETS = [
    (0.5, 1.0, "0.5-1.0x ATR"),
    (1.0, 1.5, "1.0-1.5x ATR"),
    (1.5, 2.0, "1.5-2.0x ATR"),
    (2.0, 2.5, "2.0-2.5x ATR"),
    (2.5, 3.0, "2.5-3.0x ATR"),
    (3.0, 4.0, "3.0-4.0x ATR"),
    (4.0, 999, "4.0x+ ATR"),
]

# 前瞻窗口（交易日）
LOOKFORWARDS = [1, 2, 3]

# 来回交易成本率（佣金万1×2 + 印花税0.05% + 滑点0.1%×2）
ROUND_TRIP_COST = 0.0033

SMA_SHORT = 5
SMA_LONG = 20
ATR_PERIOD = 14


def load_daily_ohlc(code: str) -> list[dict]:
    """从5min数据聚合出日线OHLC，返回按日期排序的 [{date, open, high, low, close}, ...]"""
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        daily_bars = json.load(f).get("daily_bars", {})
    out = []
    for date in sorted(daily_bars.keys()):
        if date < START or date > END:
            continue
        bars = daily_bars[date]
        if not bars:
            continue
        out.append({
            "date": date,
            "open": float(bars[0]["open"]),
            "high": max(float(b["high"]) for b in bars),
            "low": min(float(b["low"]) for b in bars),
            "close": float(bars[-1]["close"]),
        })
    return out


def compute_sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def compute_daily_atr(daily: list[dict], end_idx: int, period: int = ATR_PERIOD) -> float | None:
    """计算日线ATR（简单平均，period=14）。
    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    """
    if end_idx < period:
        return None
    trs = []
    for i in range(end_idx - period + 1, end_idx + 1):
        if i == 0:
            continue
        h = daily[i]["high"]
        l = daily[i]["low"]
        pc = daily[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period - 1:
        return None
    return sum(trs) / len(trs)


def compute_records(daily: list[dict], sma_period: int) -> list[dict]:
    """计算每个交易日的偏离度、z_score、以及1/2/3日前瞻收益。"""
    records = []
    closes = [d["close"] for d in daily]
    for i in range(len(daily)):
        # 需要足够的历史算SMA和ATR，以及足够的前瞻
        if i < max(sma_period, ATR_PERIOD) - 1:
            continue
        if i + max(LOOKFORWARDS) >= len(daily):
            break

        close = closes[i]
        sma = compute_sma(closes[: i + 1], sma_period)
        if sma is None or sma <= 0:
            continue
        dev = (close - sma) / sma

        atr = compute_daily_atr(daily, i, ATR_PERIOD)
        if atr is None or atr <= 0:
            continue
        atr_rel = atr / close  # ATR相对收盘价的比例
        z_score = abs(dev) / atr_rel

        future_returns = {}
        for lf in LOOKFORWARDS:
            fc = closes[i + lf]
            future_returns[lf] = (fc - close) / close

        records.append({
            "date": daily[i]["date"],
            "dev": dev,
            "z_score": z_score,
            "atr_rel": atr_rel,
            "future_returns": future_returns,
        })
    return records


def classify(dev: float, future_return: float) -> str:
    if dev > 0:
        return "continuation" if future_return > 0 else "reversion"
    else:
        return "continuation" if future_return < 0 else "reversion"


def run_bucket_stats(records: list[dict], lookforward: int) -> list[dict]:
    """对指定前瞻窗口做分桶统计。"""
    bucket_results = []
    for lo, hi, label in ATR_BUCKETS:
        in_bucket = [r for r in records if lo <= r["z_score"] < hi]
        n = len(in_bucket)
        if n == 0:
            bucket_results.append({"bucket": label, "n": 0})
            continue
        cont_amps = []
        rev_amps = []
        for r in in_bucket:
            fr = r["future_returns"][lookforward]
            typ = classify(r["dev"], fr)
            amp = abs(fr)
            if typ == "continuation":
                cont_amps.append(amp)
            else:
                rev_amps.append(amp)
        p_cont = len(cont_amps) / n
        p_rev = len(rev_amps) / n
        e_cont = mean(cont_amps) if cont_amps else 0
        e_rev = mean(rev_amps) if rev_amps else 0
        ev = p_rev * e_rev - p_cont * e_cont
        # 判断：回归概率≥50%且期望值能覆盖交易成本
        if p_rev >= 0.50 and ev > ROUND_TRIP_COST:
            judgment = "✓ 回归占优(覆盖成本)"
        elif p_rev >= 0.50 and ev > 0:
            judgment = "~ 回归占优(未覆盖成本)"
        elif p_rev >= 0.45 and ev > 0:
            judgment = "~ 接近"
        else:
            judgment = "✗ 延续占优"
        bucket_results.append({
            "bucket": label, "n": n,
            "p_continuation": round(p_cont, 4),
            "p_reversion": round(p_rev, 4),
            "e_continuation": round(e_cont, 6),
            "e_reversion": round(e_rev, 6),
            "expected_value": round(ev, 6),
            "judgment": judgment,
        })
    return bucket_results


def print_buckets(title: str, bucket_results: list[dict], lookforward: int):
    print(f"\n{'=' * 110}")
    print(f"{title}  前瞻={lookforward}交易日  成本门槛={ROUND_TRIP_COST*100:.2f}%")
    print(f"{'=' * 110}")
    print(f"{'z_score分桶':<16} {'样本':>7} {'P(延续)':>8} {'P(回归)':>8} "
          f"{'E(延续幅)':>10} {'E(回归幅)':>10} {'期望值':>10} {'判断':>24}")
    print(f"{'-'*16} {'-'*7} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*24}")
    for b in bucket_results:
        if b["n"] == 0:
            print(f"{b['bucket']:<16} {0:>7} {'N/A':>8} {'N/A':>8} "
                  f"{'N/A':>10} {'N/A':>10} {'N/A':>10} {'N/A':>24}")
            continue
        print(f"{b['bucket']:<16} {b['n']:>7} {b['p_continuation']*100:>7.1f}% "
              f"{b['p_reversion']*100:>7.1f}% {b['e_continuation']*100:>9.3f}% "
              f"{b['e_reversion']*100:>9.3f}% {b['expected_value']*100:>+9.4f}% "
              f"{b['judgment']:>24}")


def main():
    print("=" * 110)
    print("K0 跨日闸门诊断（日线粒度）")
    print("=" * 110)
    print(f"数据: zz500随机50只(排除原36只) × 3年（{START} ~ {END}）")
    print(f"偏离基准: {SMA_SHORT}日SMA / {SMA_LONG}日SMA")
    print(f"标准化: 日线ATR({ATR_PERIOD}) / close")
    print(f"前瞻窗口: {LOOKFORWARDS} 交易日")
    print(f"分桶: |dev| / (ATR/close) 的 ATR 倍数（与K0一致）")
    print(f"交易成本门槛: 来回 {ROUND_TRIP_COST*100:.2f}%")

    # 两个基准 × 50只股票，收集记录
    short_records = []
    long_records = []
    for code in POOL_50:
        daily = load_daily_ohlc(code)
        if len(daily) < SMA_LONG + max(LOOKFORWARDS):
            continue
        s_recs = compute_records(daily, SMA_SHORT)
        l_recs = compute_records(daily, SMA_LONG)
        short_records.extend(s_recs)
        long_records.extend(l_recs)
        print(f"  {code}: {len(daily)}交易日, short记录={len(s_recs)}, long记录={len(l_recs)}")

    print(f"\n总记录数: {SMA_SHORT}日基准={len(short_records)}, {SMA_LONG}日基准={len(long_records)}")

    out = {
        "step": "K0_daily_gate",
        "data": "zz500随机50只(排除原36只) × 3年",
        "sma_short": SMA_SHORT,
        "sma_long": SMA_LONG,
        "atr_period": ATR_PERIOD,
        "lookforwards": LOOKFORWARDS,
        "round_trip_cost": ROUND_TRIP_COST,
        "bucket_type": "ATR标准化 z-score（与K0一致）",
    }

    for label, records in [(f"{SMA_SHORT}日SMA基准", short_records),
                           (f"{SMA_LONG}日SMA基准", long_records)]:
        if not records:
            print(f"\n[{label}] 无记录，跳过")
            continue

        # z_score 分布
        z_scores = [r["z_score"] for r in records]
        z_sorted = sorted(z_scores)
        n = len(z_scores)
        print(f"\n[{label}] z_score 分布: 均值={mean(z_scores):.2f} "
              f"中位={z_sorted[n//2]:.2f} P25={z_sorted[n//4]:.2f} "
              f"P75={z_sorted[3*n//4]:.2f} 最大={max(z_scores):.2f}")

        out[label] = {"n_total": n, "by_lookforward": {}}

        for lf in LOOKFORWARDS:
            bucket_results = run_bucket_stats(records, lf)
            print_buckets(f"[{label}]", bucket_results, lf)

            # 全局统计
            cont_all = sum(1 for r in records
                           if classify(r["dev"], r["future_returns"][lf]) == "continuation")
            rev_all = n - cont_all
            print(f"\n  全局: 总样本={n}, P(延续)={cont_all/n*100:.1f}%, P(回归)={rev_all/n*100:.1f}%")

            # 按方向拆分
            pos = [r for r in records if r["dev"] > 0]
            neg = [r for r in records if r["dev"] < 0]
            pos_cont = sum(1 for r in pos if classify(r["dev"], r["future_returns"][lf]) == "continuation")
            neg_cont = sum(1 for r in neg if classify(r["dev"], r["future_returns"][lf]) == "continuation")
            print(f"  方向: dev>0({len(pos)}样本) P(延续)={pos_cont/max(len(pos),1)*100:.1f}%  "
                  f"dev<0({len(neg)}样本) P(延续)={neg_cont/max(len(neg),1)*100:.1f}%")

            # 找占优分桶
            dom = [b for b in bucket_results if "✓" in b.get("judgment", "")]
            close = [b for b in bucket_results if "~" in b.get("judgment", "")]
            if dom:
                print(f"  ✓ 有{len(dom)}个分桶'回归占优且覆盖成本':")
                for b in dom:
                    print(f"    - {b['bucket']}: P(回归)={b['p_reversion']*100:.1f}%, "
                          f"期望值={b['expected_value']*100:+.4f}%")
            elif close:
                print(f"  ~ 有{len(close)}个分桶接近临界（回归占优但未覆盖成本）:")
                for b in close:
                    print(f"    - {b['bucket']}: P(回归)={b['p_reversion']*100:.1f}%, "
                          f"期望值={b['expected_value']*100:+.4f}%")
            else:
                print(f"  ✗ 所有分桶都是延续占优，无回归机会")

            out[label]["by_lookforward"][str(lf)] = {
                "buckets": bucket_results,
                "global": {
                    "n_total": n,
                    "p_continuation": round(cont_all / n, 4),
                    "p_reversion": round(rev_all / n, 4),
                },
                "by_direction": {
                    "dev_positive": {
                        "n": len(pos),
                        "p_continuation": round(pos_cont / max(len(pos), 1), 4),
                    },
                    "dev_negative": {
                        "n": len(neg),
                        "p_continuation": round(neg_cont / max(len(neg), 1), 4),
                    },
                },
                "dominant_buckets": dom,
                "close_buckets": close,
            }

    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "K0_daily_gate.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
