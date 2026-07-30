#!/usr/bin/env python3
"""
Phase 2: Range-Position T0 全规则验证（v3 A/B对比）
====================================================
范式转向：去掉方向预测，只用位置门槛 + 被动限价 + 固定sl/tr。
反T: position ≤ 0.40 挂买限价(VWAP-0.5ATR)，成交后位置止盈/trailing/止损/超时出场
正T: position ≥ 0.60 挂卖限价(VWAP+0.5ATR)，成交后位置止盈/trailing/止损/超时出场

A/B对比：
  A: sl=0.002(用户原设计，TF验证值) + cooldown=5
  B: sl=0.010(适配区间逢低买入) + cooldown=5
"""
from __future__ import annotations
import json, sys
import importlib.util
from pathlib import Path
from statistics import mean, median
from dataclasses import dataclass, field

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 直接加载 paths.py（绕过 at0/__init__.py，避免 measurement TSD 加密模块）
_spec = importlib.util.spec_from_file_location(
    "_at0_paths", str(PROJECT_ROOT / "src" / "at0" / "paths.py")
)
_paths_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_paths_mod)
ZZ500_5MIN_DIR = _paths_mod.ZZ500_5MIN_DIR


# ── 内联 features.py 的 4 个纯计算函数 ──
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

# ── 固定参数 ──
BUY_POS_MAX = 0.40
SELL_POS_MIN = 0.60
VOL_RATIO_MAX = 2.0
LIMIT_ATR_MULT = 0.5
LIMIT_WINDOW = 12
BUY_EXIT_POS = 0.65
SELL_EXIT_POS = 0.35
TRAILING_RATIO = 0.2
TRAILING_ACT_PCT = 0.005
MAX_HOLDING = 12
WARMUP = 25
POS_WINDOW = 20
COOLDOWN_BARS = 5  # 出场后冷却N根bar不再挂单
COMMISSION = 0.0001
STAMP_TAX = 0.0005
SLIPPAGE = 0.001
COST_BUY_PASSIVE = COMMISSION
COST_SELL_ACTIVE = COMMISSION + STAMP_TAX + SLIPPAGE
COST_SELL_PASSIVE = COMMISSION + STAMP_TAX
COST_BUY_ACTIVE = COMMISSION + SLIPPAGE
START = "2023-07-25"
END = "2026-07-22"


@dataclass
class Trade:
    direction: str
    fill_price: float
    fill_idx: int
    exit_price: float
    exit_reason: str
    exit_idx: int
    holding_bars: int
    ce: float
    net_pnl: float
    position: float


def load_bars(code: str) -> dict:
    p = ZZ500_5MIN_DIR / f"{code}.json"
    if not p.exists(): return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def run_stock(code: str, stop_loss_ratio: float) -> list[Trade]:
    """对单只股票运行Range-Position回测。"""
    daily = load_bars(code)
    trades: list[Trade] = []

    for date in sorted(daily.keys()):
        if date < START or date > END: continue
        bars = daily[date]
        if len(bars) <= WARMUP: continue

        buy_state = "IDLE"
        sell_state = "IDLE"
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

            window = bars[max(0, i - POS_WINDOW + 1) : i + 1]
            win_high = max(float(b["high"]) for b in window)
            win_low = min(float(b["low"]) for b in window)

            bars_so_far = bars[:i+1]
            vwap = cumulative_vwap(bars_so_far)
            atr = intraday_atr(bars_so_far, 14)
            vr = volume_ratio(bars_so_far, 5, 20)
            if not vwap or not atr or atr <= 0: continue
            atr_rel = atr_relative(atr, vwap)
            if not atr_rel or atr_rel <= 0: continue
            pos = (c - win_low) / (win_high - win_low) if win_high > win_low else 0.5

            # 冷却递减
            if buy_cooldown > 0: buy_cooldown -= 1
            if sell_cooldown > 0: sell_cooldown -= 1

            # ── 1. 检查挂单成交 ──
            if buy_state == "PENDING":
                if l <= buy_limit:
                    buy_state = "FILLED"
                    buy_fill_idx = i; buy_fill_price = buy_limit
                    buy_max_fav = 0.0; buy_max_adv = 0.0
                elif i - buy_pending_bar >= LIMIT_WINDOW:
                    buy_state = "IDLE"

            if sell_state == "PENDING":
                if h >= sell_limit:
                    sell_state = "FILLED"
                    sell_fill_idx = i; sell_fill_price = sell_limit
                    sell_max_fav = 0.0; sell_max_adv = 0.0
                elif i - sell_pending_bar >= LIMIT_WINDOW:
                    sell_state = "IDLE"

            # ── 2. 检查出场 ──
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
                    if buy_max_adv >= buy_fill_price * stop_loss_ratio:
                        exit_price = buy_fill_price - buy_fill_price * stop_loss_ratio
                        exit_reason = "stop_loss"
                if exit_price is None and hb >= MAX_HOLDING:
                    exit_price = c; exit_reason = "timeout"

                if exit_price is not None:
                    exit_fill = exit_price * (1 - SLIPPAGE)
                    ce = (exit_fill - buy_fill_price) / buy_fill_price
                    cost = COST_BUY_PASSIVE + COST_SELL_ACTIVE
                    trades.append(Trade("buy", buy_fill_price, buy_fill_idx,
                        exit_fill, exit_reason, i, hb, ce, ce - cost, pos))
                    buy_state = "IDLE"
                    buy_cooldown = COOLDOWN_BARS

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
                    if sell_max_adv >= sell_fill_price * stop_loss_ratio:
                        exit_price = sell_fill_price + sell_fill_price * stop_loss_ratio
                        exit_reason = "stop_loss"
                if exit_price is None and hb >= MAX_HOLDING:
                    exit_price = c; exit_reason = "timeout"

                if exit_price is not None:
                    buyback = exit_price * (1 + SLIPPAGE)
                    ce = (sell_fill_price - buyback) / sell_fill_price
                    cost = COST_SELL_PASSIVE + COST_BUY_ACTIVE
                    trades.append(Trade("sell", sell_fill_price, sell_fill_idx,
                        buyback, exit_reason, i, hb, ce, ce - cost, pos))
                    sell_state = "IDLE"
                    sell_cooldown = COOLDOWN_BARS

            # ── 3. 生成新信号（IDLE + 冷却结束）──
            if (buy_state == "IDLE" and buy_cooldown == 0
                    and pos <= BUY_POS_MAX and vr is not None and vr <= VOL_RATIO_MAX):
                buy_state = "PENDING"
                buy_pending_bar = i
                buy_limit = vwap * (1 - LIMIT_ATR_MULT * atr_rel)

            if (sell_state == "IDLE" and sell_cooldown == 0
                    and pos >= SELL_POS_MIN and vr is not None and vr <= VOL_RATIO_MAX):
                sell_state = "PENDING"
                sell_pending_bar = i
                sell_limit = vwap * (1 + LIMIT_ATR_MULT * atr_rel)

    return trades


def analyze(trades: list[Trade], label: str, sl: float):
    """分析并打印一组交易的结果。"""
    n = len(trades)
    buy_ts = [t for t in trades if t.direction == "buy"]
    sell_ts = [t for t in trades if t.direction == "sell"]
    n_b = len(buy_ts); n_s = len(sell_ts)

    print(f"\n{'=' * 100}")
    print(f"  {label}  (sl={sl}, cooldown={COOLDOWN_BARS})")
    print(f"{'=' * 100}")

    if n == 0:
        print("  无交易"); return {}

    all_ce = [t.ce for t in trades]
    all_net = [t.net_pnl for t in trades]
    buy_pos = [t.position for t in buy_ts]
    sell_pos = [t.position for t in sell_ts]

    b_ce = mean([t.ce for t in buy_ts]) if buy_ts else 0
    s_ce = mean([t.ce for t in sell_ts]) if sell_ts else 0
    b_net = mean([t.net_pnl for t in buy_ts]) if buy_ts else 0
    s_net = mean([t.net_pnl for t in sell_ts]) if sell_ts else 0
    b_pos_m = mean(buy_pos) if buy_pos else 0
    s_pos_m = mean(sell_pos) if sell_pos else 0

    print(f"\n  {'指标':<22} {'反T(买入腿)':>14} {'正T(卖出腿)':>14} {'合计':>14} {'目标':>14}")
    print(f"  {'-'*22} {'-'*14} {'-'*14} {'-'*14} {'-'*14}")
    print(f"  {'样本数':<22} {n_b:>14} {n_s:>14} {n:>14}")
    print(f"  {'CE均值':<22} {b_ce*100:>13.3f}% {s_ce*100:>13.3f}% {mean(all_ce)*100:>13.3f}% {'≥8%':>14}")
    print(f"  {'净收益率均值':<22} {b_net*100:>13.3f}% {s_net*100:>13.3f}% {mean(all_net)*100:>13.3f}% {'≥0.35%':>14}")
    print(f"  {'入场位置均值':<22} {b_pos_m:>14.3f} {s_pos_m:>14.3f} {'—':>14} {'买≤0.35/卖≥0.65':>14}")
    pos_net = sum(1 for x in all_net if x > 0)
    print(f"  {'净收益为正占比':<22} {sum(1 for t in buy_ts if t.net_pnl>0)/max(n_b,1)*100:>13.1f}% {sum(1 for t in sell_ts if t.net_pnl>0)/max(n_s,1)*100:>13.1f}% {pos_net/n*100:>13.1f}%")
    print(f"  {'总净收益率':<22} {sum(t.net_pnl for t in buy_ts)*100:>13.2f}% {sum(t.net_pnl for t in sell_ts)*100:>13.2f}% {sum(all_net)*100:>13.2f}%")

    # 出场原因
    print(f"\n  出场原因:")
    reasons = set(t.exit_reason for t in trades)
    for r in sorted(reasons):
        bc = sum(1 for t in buy_ts if t.exit_reason == r)
        sc = sum(1 for t in sell_ts if t.exit_reason == r)
        tc = bc + sc
        print(f"    {r:<14} {tc:>6} ({tc/n*100:.1f}%)")

    return {
        "label": label, "sl": sl, "cooldown": COOLDOWN_BARS,
        "n_total": n, "n_buy": n_b, "n_sell": n_s,
        "ce_mean": mean(all_ce), "net_mean": mean(all_net),
        "ce_buy": b_ce, "ce_sell": s_ce,
        "pos_buy": b_pos_m, "pos_sell": s_pos_m,
        "total_net": sum(all_net),
        "exit_dist": {r: sum(1 for t in trades if t.exit_reason == r) for r in reasons},
    }


def main():
    print("=" * 100)
    print("Phase 2 v3: Range-Position T0 — A/B对比 (cooldown=5)")
    print("=" * 100)
    print(f"位置门槛: 买≤{BUY_POS_MAX} 卖≥{SELL_POS_MIN} 量比≤{VOL_RATIO_MAX}")
    print(f"限价: VWAP±{LIMIT_ATR_MULT}×ATR 窗口={LIMIT_WINDOW}bar")
    print(f"出场: 位置止盈(买≥{BUY_EXIT_POS}/卖≤{SELL_EXIT_POS}) trailing={TRAILING_RATIO} max_holding={MAX_HOLDING}")
    print(f"冷却: {COOLDOWN_BARS}bar")

    configs = [
        ("A: sl=0.002(TF原值)", 0.002),
        ("B: sl=0.010(区间适配)", 0.010),
    ]

    results = []
    for label, sl in configs:
        print(f"\n运行 {label} ...")
        all_trades = []
        for code in POOL_36:
            ts = run_stock(code, sl)
            all_trades.extend(ts)
        r = analyze(all_trades, label, sl)
        if r: results.append(r)

    # 对比汇总
    if len(results) >= 2:
        print(f"\n{'=' * 100}")
        print("A/B 对比汇总")
        print(f"{'=' * 100}")
        print(f"  {'指标':<20} {'A(sl=0.002)':>20} {'B(sl=0.010)':>20} {'差异':>20}")
        print(f"  {'-'*20} {'-'*20} {'-'*20} {'-'*20}")
        a, b = results[0], results[1]
        print(f"  {'总交易数':<20} {a['n_total']:>20} {b['n_total']:>20} {b['n_total']-a['n_total']:>20}")
        print(f"  {'CE均值':<20} {a['ce_mean']*100:>19.3f}% {b['ce_mean']*100:>19.3f}% {(b['ce_mean']-a['ce_mean'])*100:>19.3f}%")
        print(f"  {'净收益率均值':<20} {a['net_mean']*100:>19.3f}% {b['net_mean']*100:>19.3f}% {(b['net_mean']-a['net_mean'])*100:>19.3f}%")
        print(f"  {'总净收益率':<20} {a['total_net']*100:>19.2f}% {b['total_net']*100:>19.2f}% {(b['total_net']-a['total_net'])*100:>19.2f}%")
        print(f"  {'反T入场位置':<20} {a['pos_buy']:>20.3f} {b['pos_buy']:>20.3f} {b['pos_buy']-a['pos_buy']:>20.3f}")
        print(f"  {'正T入场位置':<20} {a['pos_sell']:>20.3f} {b['pos_sell']:>20.3f} {b['pos_sell']-a['pos_sell']:>20.3f}")

    # 保存
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "phase2_range_position_v3.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
