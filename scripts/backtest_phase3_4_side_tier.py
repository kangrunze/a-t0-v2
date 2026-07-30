#!/usr/bin/env python3
"""
Phase 3+4: 单边vs双边 + 波动分档联合验证
==========================================
Phase 3: 仅反T / 仅正T / 双边，看方向不对称性
Phase 4: 按日均振幅分高/中/低三档，看不同波动率下的表现

基础参数沿用 Phase 2 v3: sl=0.002, cooldown=5, 位置门槛0.4/0.6
"""
from __future__ import annotations
import json, sys
import importlib.util
from pathlib import Path
from statistics import mean, median
from dataclasses import dataclass

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "_at0_paths", str(PROJECT_ROOT / "src" / "at0" / "paths.py")
)
_paths_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_paths_mod)
ZZ500_5MIN_DIR = _paths_mod.ZZ500_5MIN_DIR


def cumulative_vwap(bars):
    if not bars: return None
    total_pv = 0.0; total_vol = 0.0
    for b in bars:
        vol = b.get("volume", 0)
        if vol <= 0: continue
        h = b.get("high", 0); l = b.get("low", 0); c = b.get("close", 0)
        if h <= 0 and l <= 0 and c <= 0: continue
        tp = (h + l + c) / 3.0
        if tp <= 0: continue
        total_pv += tp * vol; total_vol += vol
    if total_vol <= 0:
        closes = [b.get("close", 0) for b in bars if b.get("close", 0) > 0]
        return sum(closes) / len(closes) if closes else None
    return total_pv / total_vol


def intraday_atr(bars, period=14):
    if len(bars) < period + 1: return None
    trs = []
    for i in range(-period, 0):
        cur = bars[i]; prev = bars[i - 1]
        prev_close = prev.get("close", 0)
        h = cur.get("high", 0); l = cur.get("low", 0)
        if prev_close <= 0: tr = h - l
        else: tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def atr_relative(atr, vwap):
    if atr is None or vwap is None or vwap <= 0 or atr <= 0: return None
    return atr / vwap


def volume_ratio(bars, lookback=5, baseline=20):
    if len(bars) < lookback + baseline: return None
    recent = bars[-lookback:]
    prior = bars[-(lookback + baseline): -lookback]
    recent_avg = sum(b["volume"] for b in recent) / lookback
    prior_avg = sum(b["volume"] for b in prior) / baseline
    if prior_avg <= 0: return None
    return recent_avg / prior_avg


POOL_36 = [
    "001221","001309","001389","002261","300339","300475","300548","300570",
    "300620","300735","300757","300972","301526","301536","301606","301611",
    "600602","601099","603000","603119","603175","603728","688166","688213",
    "688318","688322","688331","688361","688411","688498","688582","688615",
    "688629","688692","688702","688709",
]

# 参数
BUY_POS_MAX = 0.40; SELL_POS_MIN = 0.60; VOL_RATIO_MAX = 2.0
LIMIT_ATR_MULT = 0.5; LIMIT_WINDOW = 12
BUY_EXIT_POS = 0.65; SELL_EXIT_POS = 0.35
TRAILING_RATIO = 0.2; TRAILING_ACT_PCT = 0.005
MAX_HOLDING = 12; WARMUP = 25; POS_WINDOW = 20; COOLDOWN_BARS = 5
STOP_LOSS_RATIO = 0.002
COMMISSION = 0.0001; STAMP_TAX = 0.0005; SLIPPAGE = 0.001
COST_BUY_PASSIVE = COMMISSION
COST_SELL_ACTIVE = COMMISSION + STAMP_TAX + SLIPPAGE
COST_SELL_PASSIVE = COMMISSION + STAMP_TAX
COST_BUY_ACTIVE = COMMISSION + SLIPPAGE
START = "2023-07-25"; END = "2026-07-22"


@dataclass
class Trade:
    direction: str; fill_price: float; fill_idx: int
    exit_price: float; exit_reason: str; exit_idx: int
    holding_bars: int; ce: float; net_pnl: float; position: float


def load_bars(code: str) -> dict:
    p = ZZ500_5MIN_DIR / f"{code}.json"
    if not p.exists(): return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def calc_stock_amplitude(code: str) -> float:
    """计算股票在整个区间的平均日内振幅 (high-low)/close。"""
    daily = load_bars(code)
    amps = []
    for date in sorted(daily.keys()):
        if date < START or date > END: continue
        bars = daily[date]
        if not bars: continue
        day_high = max(float(b["high"]) for b in bars)
        day_low = min(float(b["low"]) for b in bars)
        day_close = float(bars[-1]["close"])
        if day_close > 0:
            amps.append((day_high - day_low) / day_close)
    return mean(amps) if amps else 0.0


def run_stock(code: str, mode: str = "both") -> list[Trade]:
    """
    mode: "both"=双边, "reverse"=仅反T(买入腿), "forward"=仅正T(卖出腿)
    """
    daily = load_bars(code)
    trades: list[Trade] = []
    enable_buy = mode in ("both", "reverse")
    enable_sell = mode in ("both", "forward")

    for date in sorted(daily.keys()):
        if date < START or date > END: continue
        bars = daily[date]
        if len(bars) <= WARMUP: continue

        buy_state = "IDLE"; sell_state = "IDLE"
        buy_pending_bar = 0; sell_pending_bar = 0
        buy_limit = 0.0; sell_limit = 0.0
        buy_fill_idx = 0; sell_fill_idx = 0
        buy_fill_price = 0.0; sell_fill_price = 0.0
        buy_max_fav = 0.0; buy_max_adv = 0.0
        sell_max_fav = 0.0; sell_max_adv = 0.0
        buy_cooldown = 0; sell_cooldown = 0

        for i in range(WARMUP, len(bars)):
            bar = bars[i]
            h = float(bar["high"]); l = float(bar["low"]); c = float(bar["close"])

            window = bars[max(0, i - POS_WINDOW + 1): i + 1]
            win_high = max(float(b["high"]) for b in window)
            win_low = min(float(b["low"]) for b in window)

            bars_so_far = bars[:i + 1]
            vwap = cumulative_vwap(bars_so_far)
            atr = intraday_atr(bars_so_far, 14)
            vr = volume_ratio(bars_so_far, 5, 20)
            if not vwap or not atr or atr <= 0: continue
            atr_rel = atr_relative(atr, vwap)
            if not atr_rel or atr_rel <= 0: continue
            pos = (c - win_low) / (win_high - win_low) if win_high > win_low else 0.5

            if buy_cooldown > 0: buy_cooldown -= 1
            if sell_cooldown > 0: sell_cooldown -= 1

            # 挂单成交检查
            if enable_buy and buy_state == "PENDING":
                if l <= buy_limit:
                    buy_state = "FILLED"; buy_fill_idx = i; buy_fill_price = buy_limit
                    buy_max_fav = 0.0; buy_max_adv = 0.0
                elif i - buy_pending_bar >= LIMIT_WINDOW:
                    buy_state = "IDLE"

            if enable_sell and sell_state == "PENDING":
                if h >= sell_limit:
                    sell_state = "FILLED"; sell_fill_idx = i; sell_fill_price = sell_limit
                    sell_max_fav = 0.0; sell_max_adv = 0.0
                elif i - sell_pending_bar >= LIMIT_WINDOW:
                    sell_state = "IDLE"

            # 出场检查
            if buy_state == "FILLED":
                buy_max_fav = max(buy_max_fav, h - buy_fill_price)
                buy_max_adv = max(buy_max_adv, buy_fill_price - l)
                hb = i - buy_fill_idx
                exit_price = None; exit_reason = None
                if pos >= BUY_EXIT_POS:
                    exit_price = c; exit_reason = "pos_tp"
                elif buy_max_fav >= buy_fill_price * TRAILING_ACT_PCT:
                    stop_line = buy_fill_price + buy_max_fav * (1 - TRAILING_RATIO)
                    if l <= stop_line:
                        exit_price = stop_line; exit_reason = "trailing"
                else:
                    if buy_max_adv >= buy_fill_price * STOP_LOSS_RATIO:
                        exit_price = buy_fill_price - buy_fill_price * STOP_LOSS_RATIO
                        exit_reason = "stop_loss"
                if exit_price is None and hb >= MAX_HOLDING:
                    exit_price = c; exit_reason = "timeout"
                if exit_price is not None:
                    exit_fill = exit_price * (1 - SLIPPAGE)
                    ce = (exit_fill - buy_fill_price) / buy_fill_price
                    cost = COST_BUY_PASSIVE + COST_SELL_ACTIVE
                    trades.append(Trade("buy", buy_fill_price, buy_fill_idx,
                        exit_fill, exit_reason, i, hb, ce, ce - cost, pos))
                    buy_state = "IDLE"; buy_cooldown = COOLDOWN_BARS

            if sell_state == "FILLED":
                sell_max_fav = max(sell_max_fav, sell_fill_price - l)
                sell_max_adv = max(sell_max_adv, h - sell_fill_price)
                hb = i - sell_fill_idx
                exit_price = None; exit_reason = None
                if pos <= SELL_EXIT_POS:
                    exit_price = c; exit_reason = "pos_tp"
                elif sell_max_fav >= sell_fill_price * TRAILING_ACT_PCT:
                    stop_line = sell_fill_price - sell_max_fav * (1 - TRAILING_RATIO)
                    if h >= stop_line:
                        exit_price = stop_line; exit_reason = "trailing"
                else:
                    if sell_max_adv >= sell_fill_price * STOP_LOSS_RATIO:
                        exit_price = sell_fill_price + sell_fill_price * STOP_LOSS_RATIO
                        exit_reason = "stop_loss"
                if exit_price is None and hb >= MAX_HOLDING:
                    exit_price = c; exit_reason = "timeout"
                if exit_price is not None:
                    buyback = exit_price * (1 + SLIPPAGE)
                    ce = (sell_fill_price - buyback) / sell_fill_price
                    cost = COST_SELL_PASSIVE + COST_BUY_ACTIVE
                    trades.append(Trade("sell", sell_fill_price, sell_fill_idx,
                        buyback, exit_reason, i, hb, ce, ce - cost, pos))
                    sell_state = "IDLE"; sell_cooldown = COOLDOWN_BARS

            # 新信号
            if (enable_buy and buy_state == "IDLE" and buy_cooldown == 0
                    and pos <= BUY_POS_MAX and vr is not None and vr <= VOL_RATIO_MAX):
                buy_state = "PENDING"; buy_pending_bar = i
                buy_limit = vwap * (1 - LIMIT_ATR_MULT * atr_rel)

            if (enable_sell and sell_state == "IDLE" and sell_cooldown == 0
                    and pos >= SELL_POS_MIN and vr is not None and vr <= VOL_RATIO_MAX):
                sell_state = "PENDING"; sell_pending_bar = i
                sell_limit = vwap * (1 + LIMIT_ATR_MULT * atr_rel)

    return trades


def stats(trades: list[Trade]) -> dict:
    if not trades: return {"n": 0}
    ces = [t.ce for t in trades]
    nets = [t.net_pnl for t in trades]
    buy_ts = [t for t in trades if t.direction == "buy"]
    sell_ts = [t for t in trades if t.direction == "sell"]
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    return {
        "n": len(trades),
        "n_buy": len(buy_ts), "n_sell": len(sell_ts),
        "ce_mean": mean(ces), "net_mean": mean(nets),
        "total_net": sum(nets),
        "ce_buy": mean([t.ce for t in buy_ts]) if buy_ts else 0,
        "ce_sell": mean([t.ce for t in sell_ts]) if sell_ts else 0,
        "pos_buy": mean([t.position for t in buy_ts]) if buy_ts else 0,
        "pos_sell": mean([t.position for t in sell_ts]) if sell_ts else 0,
        "win_rate": sum(1 for x in nets if x > 0) / len(nets),
        "exit_dist": reasons,
    }


def print_stats(s: dict, label: str):
    if s["n"] == 0:
        print(f"  {label}: 无交易")
        return
    print(f"  {label}:")
    print(f"    交易数={s['n']} (反T={s['n_buy']} 正T={s['n_sell']})  "
          f"CE={s['ce_mean']*100:+.3f}%  净收益={s['net_mean']*100:+.3f}%  "
          f"胜率={s['win_rate']*100:.1f}%  总净={s['total_net']*100:+.1f}%")
    ed = s["exit_dist"]
    tot = s["n"]
    print(f"    出场: " + "  ".join(f"{k}={v}({v/tot*100:.0f}%)" for k, v in sorted(ed.items())))


def main():
    print("=" * 100)
    print("Phase 3+4: 单边vs双边 + 波动分档")
    print("=" * 100)
    print(f"sl={STOP_LOSS_RATIO} cooldown={COOLDOWN_BARS} 位置门槛买≤{BUY_POS_MAX}/卖≥{SELL_POS_MIN}")

    # ── Phase 3: 单边 vs 双边 ──
    print(f"\n{'=' * 100}")
    print("Phase 3: 单边 vs 双边")
    print(f"{'=' * 100}")

    phase3_results = {}
    for mode, label in [("both", "双边"), ("reverse", "仅反T(买入腿)"), ("forward", "仅正T(卖出腿)")]:
        all_t = []
        for code in POOL_36:
            all_t.extend(run_stock(code, mode))
        s = stats(all_t)
        print_stats(s, label)
        phase3_results[mode] = s

    # ── Phase 4: 波动分档 ──
    print(f"\n{'=' * 100}")
    print("Phase 4: 波动分档")
    print(f"{'=' * 100}")

    # 计算每只股票的平均日内振幅
    amp_map = {}
    for code in POOL_36:
        amp_map[code] = calc_stock_amplitude(code)
    sorted_by_amp = sorted(amp_map.items(), key=lambda x: x[1])
    n = len(sorted_by_amp)
    tier_size = n // 3
    low_tier = [c for c, _ in sorted_by_amp[:tier_size]]
    mid_tier = [c for c, _ in sorted_by_amp[tier_size:2 * tier_size]]
    high_tier = [c for c, _ in sorted_by_amp[2 * tier_size:]]

    print(f"\n  低波动档({len(low_tier)}只): 振幅 {mean([amp_map[c] for c in low_tier])*100:.2f}%")
    print(f"    {low_tier}")
    print(f"  中波动档({len(mid_tier)}只): 振幅 {mean([amp_map[c] for c in mid_tier])*100:.2f}%")
    print(f"    {mid_tier}")
    print(f"  高波动档({len(high_tier)}只): 振幅 {mean([amp_map[c] for c in high_tier])*100:.2f}%")
    print(f"    {high_tier}")

    phase4_results = {}
    for tier, codes in [("低波动", low_tier), ("中波动", mid_tier), ("高波动", high_tier)]:
        all_t = []
        for code in codes:
            all_t.extend(run_stock(code, "both"))
        s = stats(all_t)
        print_stats(s, f"{tier}档({len(codes)}只)")
        phase4_results[tier] = s

    # ── 汇总 ──
    print(f"\n{'=' * 100}")
    print("汇总")
    print(f"{'=' * 100}")
    print("\nPhase 3 单边vs双边:")
    print(f"  {'模式':<16} {'交易数':>8} {'CE均值':>10} {'净收益率':>10} {'胜率':>8} {'总净':>10}")
    for mode, label in [("both", "双边"), ("reverse", "仅反T"), ("forward", "仅正T")]:
        s = phase3_results[mode]
        if s["n"] > 0:
            print(f"  {label:<16} {s['n']:>8} {s['ce_mean']*100:>9.3f}% {s['net_mean']*100:>9.3f}% {s['win_rate']*100:>7.1f}% {s['total_net']*100:>9.1f}%")

    print("\nPhase 4 波动分档:")
    print(f"  {'档位':<16} {'交易数':>8} {'CE均值':>10} {'净收益率':>10} {'胜率':>8} {'总净':>10}")
    for tier in ["低波动", "中波动", "高波动"]:
        s = phase4_results[tier]
        if s["n"] > 0:
            print(f"  {tier:<16} {s['n']:>8} {s['ce_mean']*100:>9.3f}% {s['net_mean']*100:>9.3f}% {s['win_rate']*100:>7.1f}% {s['total_net']*100:>9.1f}%")

    # 保存
    out = {
        "phase3": {k: v for k, v in phase3_results.items()},
        "phase4": {k: v for k, v in phase4_results.items()},
        "amplitude_map": amp_map,
        "tier_split": {"low": low_tier, "mid": mid_tier, "high": high_tier},
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "phase3_4_side_tier.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
