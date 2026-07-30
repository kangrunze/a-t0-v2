#!/usr/bin/env python3
"""
Phase 1: Passive J2 验证
========================
范式转向：从「预测型」到「位置型」。
J2触发后不在当前价成交，挂限价单(VWAP-0.5×ATR)，12根bar内触达才成交。
对比当前J2(即时成交) vs Passive J2(被动成交)，用完整出场逻辑(trailing/stop_loss/超时)。

验证指标：CE均值、买点位置、成交率、net_pnl/笔
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.features import intraday_atr, atr_relative

POOL_36 = [
    "001221", "001309", "001389", "002261", "300339", "300475", "300548",
    "300570", "300620", "300735", "300757", "300972", "301526", "301536",
    "301606", "301611", "600602", "601099", "603000", "603119", "603175",
    "603728", "688166", "688213", "688318", "688322", "688331", "688361",
    "688411", "688498", "688582", "688615", "688629", "688692", "688702",
    "688709",
]

# 出场参数（与生产yaml一致）
STOP_LOSS_RATIO = 0.002
TRAILING_RATIO = 0.2
TRAILING_ACTIVATION_PCT = 0.005
MAX_HOLDING_BARS = 12
# Passive J2 限价挂单参数
LIMIT_ATR_MULT = 0.5      # VWAP下方0.5倍ATR
LIMIT_WINDOW = 12         # 12根bar内触达才成交

# 成本模型
COMMISSION = 0.0001       # 佣金万1（单边）
STAMP_TAX = 0.0005        # 印花税（卖出）
SLIPPAGE = 0.001          # 滑点（主动单）
# A组（即时成交）：买入+滑点，卖出+滑点+印花税
# B组（被动成交）：买入无滑点（限价单），卖出+滑点+印花税
COST_A_BUY = COMMISSION + SLIPPAGE
COST_A_SELL = COMMISSION + STAMP_TAX + SLIPPAGE
COST_B_BUY = COMMISSION                  # 被动单无滑点
COST_B_SELL = COMMISSION + STAMP_TAX + SLIPPAGE

BACKTEST_DIR = PROJECT_ROOT / "outputs" / "backtest"


def load_stock_bars(code: str) -> dict:
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def find_j2_triggers(code: str) -> list[dict]:
    """从已生成的trades.json提取J2回踩触发记录。"""
    triggers = []
    for trades_path in BACKTEST_DIR.glob(f"{code}_*_trades.json"):
        try:
            with open(trades_path, encoding="utf-8") as f:
                trades = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        for t in trades:
            if t.get("direction") != "buy":
                continue
            if any("回踩入场-触发" in r for r in t.get("rules_fired", [])):
                vwap = t.get("vwap")
                fill_price = t.get("fill_price")
                if vwap and vwap > 0 and fill_price and fill_price > 0:
                    triggers.append({
                        "date": t.get("date", ""),
                        "time": t.get("time", ""),
                        "fill_price": float(fill_price),
                        "vwap": float(vwap),
                    })
    seen = set()
    unique = []
    for t in triggers:
        key = (t["date"], t["time"])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def find_bar_idx(bars: list[dict], time_str: str) -> int | None:
    """找到时间对应的bar索引。"""
    hhmm = time_str[-5:] if len(time_str) >= 5 else time_str
    for i, b in enumerate(bars):
        bt = b.get("time", "")
        if bt.startswith(hhmm) or bt == time_str:
            return i
    return None


def simulate_exit(bars: list[dict], fill_idx: int, fill_price: float) -> dict:
    """从买入成交bar开始，用trailing/stop_loss/超时逻辑找卖出点。
    返回 {exit_price, exit_reason, exit_idx, max_favorable, holding_bars}
    """
    max_favorable = 0.0
    max_adverse = 0.0
    end_idx = min(fill_idx + 1 + MAX_HOLDING_BARS, len(bars))

    for j in range(fill_idx + 1, end_idx):
        bar = bars[j]
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        # 更新max_favorable/max_adverse（先更新再检查，与execution.py一致）
        max_favorable = max(max_favorable, high - fill_price)
        max_adverse = max(max_adverse, fill_price - low)
        holding_bars = j - fill_idx

        # 检查移动止盈（trailing激活后）
        trailing_armed = max_favorable >= fill_price * TRAILING_ACTIVATION_PCT
        if trailing_armed:
            stop_line = fill_price + max_favorable * (1 - TRAILING_RATIO)
            if low <= stop_line:
                return {"exit_price": stop_line, "exit_reason": "trailing",
                        "exit_idx": j, "max_favorable": max_favorable,
                        "holding_bars": holding_bars}
        else:
            # 固定止损（trailing未激活时）
            threshold = fill_price * STOP_LOSS_RATIO
            if max_adverse >= threshold:
                return {"exit_price": fill_price - threshold,
                        "exit_reason": "stop_loss", "exit_idx": j,
                        "max_favorable": max_favorable,
                        "holding_bars": holding_bars}

        # 超时
        if holding_bars >= MAX_HOLDING_BARS:
            return {"exit_price": close, "exit_reason": "timeout",
                    "exit_idx": j, "max_favorable": max_favorable,
                    "holding_bars": holding_bars}

    # 循环结束未平仓（到收盘）
    last_close = float(bars[end_idx - 1]["close"]) if end_idx > fill_idx + 1 else fill_price
    return {"exit_price": last_close, "exit_reason": "eod",
            "exit_idx": end_idx - 1, "max_favorable": max_favorable,
            "holding_bars": end_idx - 1 - fill_idx}


def compute_position(fill_price: float, bars: list[dict], date: str) -> float | None:
    """计算买点位置 = (fill_price - day_low) / (day_high - day_low)。"""
    day_bars = bars
    if not day_bars:
        return None
    day_high = max(float(b["high"]) for b in day_bars)
    day_low = min(float(b["low"]) for b in day_bars)
    if day_high <= day_low:
        return 0.5
    return (fill_price - day_low) / (day_high - day_low)


def simulate_group(bars: list[dict], trigger: dict, is_passive: bool) -> dict | None:
    """模拟单次J2触发的完整交易（A组即时 / B组被动）。
    返回 {fill_price, exit_price, exit_reason, ce, net_pnl, position, holding_bars, filled}
    """
    date = trigger["date"]
    time_str = trigger["time"]
    vwap = trigger["vwap"]
    j2_price = trigger["fill_price"]  # 当前J2的成交价（含滑点）

    trigger_idx = find_bar_idx(bars, time_str)
    if trigger_idx is None:
        return None

    # 计算ATR
    bars_up_to = bars[: trigger_idx + 1]
    atr = intraday_atr(bars_up_to, period=14)
    if atr is None or atr <= 0:
        return None
    atr_rel = atr_relative(atr, vwap)
    if atr_rel is None or atr_rel <= 0:
        return None

    if is_passive:
        # B组：挂限价单 VWAP - 0.5×ATR_rel×VWAP
        limit_price = vwap * (1 - LIMIT_ATR_MULT * atr_rel)
        # 后续12根bar内触达才成交
        fill_idx = None
        fill_price = None
        end_check = min(trigger_idx + 1 + LIMIT_WINDOW, len(bars))
        for j in range(trigger_idx + 1, end_check):
            if float(bars[j]["low"]) <= limit_price:
                fill_idx = j
                fill_price = limit_price  # 被动成交，不加滑点
                break
        if fill_idx is None:
            # 未成交
            return {"filled": False, "fill_price": None, "exit_price": None,
                    "exit_reason": "unfilled", "ce": None, "net_pnl": None,
                    "position": None, "holding_bars": None}
    else:
        # A组：即时成交，fill_price = trigger的close + 滑点
        fill_idx = trigger_idx
        fill_price = j2_price  # 已含滑点

    # 出场
    exit_info = simulate_exit(bars, fill_idx, fill_price)

    # 计算CE和净PnL
    exit_price = exit_info["exit_price"]
    # 卖出加滑点+印花税
    if is_passive:
        exit_fill = exit_price * (1 - SLIPPAGE)  # 卖出滑点
        cost_rate = COST_B_BUY + COST_B_SELL
    else:
        exit_fill = exit_price * (1 - SLIPPAGE)
        cost_rate = COST_A_BUY + COST_A_SELL

    ce = (exit_fill - fill_price) / fill_price  # 毛收益率
    net_pnl = ce - cost_rate                    # 净收益率

    # 买点位置
    position = compute_position(fill_price, bars, date)

    return {
        "filled": True,
        "fill_price": fill_price,
        "exit_price": exit_fill,
        "exit_reason": exit_info["exit_reason"],
        "ce": ce,
        "net_pnl": net_pnl,
        "position": position,
        "holding_bars": exit_info["holding_bars"],
        "max_favorable_pct": exit_info["max_favorable"] / fill_price,
    }


def main():
    print("=" * 120)
    print("Phase 1: Passive J2 验证（位置型 vs 预测型）")
    print("=" * 120)
    print(f"数据: 36只池子 × 3年")
    print(f"出场: stop_loss={STOP_LOSS_RATIO} trailing={TRAILING_RATIO} max_holding={MAX_HOLDING_BARS}")
    print(f"Passive: 限价=VWAP-{LIMIT_ATR_MULT}×ATR 窗口={LIMIT_WINDOW}bar")
    print(f"成本: A组(即时)来回={COST_A_BUY+COST_A_SELL:.4f}  B组(被动)来回={COST_B_BUY+COST_B_SELL:.4f}")

    a_results = []
    b_results = []
    b_unfilled = 0
    total_triggers = 0

    for code in POOL_36:
        daily_bars = load_stock_bars(code)
        if not daily_bars:
            continue
        triggers = find_j2_triggers(code)
        if not triggers:
            continue

        code_a = []
        code_b = []
        code_unfilled = 0
        for trig in triggers:
            date = trig["date"]
            bars = daily_bars.get(date, [])
            if not bars:
                continue
            total_triggers += 1

            # A组：即时成交
            a = simulate_group(bars, trig, is_passive=False)
            if a:
                a_results.append(a)
                code_a.append(a)

            # B组：被动成交
            b = simulate_group(bars, trig, is_passive=True)
            if b:
                if b["filled"]:
                    b_results.append(b)
                    code_b.append(b)
                else:
                    b_unfilled += 1
                    code_unfilled += 1

        if code_a or code_b:
            a_ce = mean([x["ce"] for x in code_a]) if code_a else 0
            b_ce = mean([x["ce"] for x in code_b]) if code_b else 0
            print(f"  {code}: 触发{len(triggers)}, A成交{len(code_a)}(CE={a_ce*100:+.2f}%), "
                  f"B成交{len(code_b)}/未成交{code_unfilled}(CE={b_ce*100:+.2f}%)")

    n_a = len(a_results)
    n_b = len(b_results)
    n_total = total_triggers

    print(f"\n总J2触发: {n_total}")
    print(f"A组(即时)成交: {n_a}")
    print(f"B组(被动)成交: {n_b} (成交率={n_b/n_total*100:.1f}%)")
    print(f"B组未成交: {b_unfilled} ({b_unfilled/n_total*100:.1f}%)")

    if n_a == 0 or n_b == 0:
        print("样本不足")
        return

    # 核心对比
    print(f"\n{'=' * 120}")
    print("核心对比：A组(即时J2) vs B组(Passive J2)")
    print(f"{'=' * 120}")

    a_ces = [r["ce"] for r in a_results]
    b_ces = [r["ce"] for r in b_results]
    a_nets = [r["net_pnl"] for r in a_results]
    b_nets = [r["net_pnl"] for r in b_results]
    a_pos = [r["position"] for r in a_results if r["position"] is not None]
    b_pos = [r["position"] for r in b_results if r["position"] is not None]

    print(f"\n{'指标':<24} {'A组(即时J2)':>16} {'B组(Passive)':>16} {'差异':>16} {'目标':>16}")
    print(f"{'-'*24} {'-'*16} {'-'*16} {'-'*16} {'-'*16}")
    print(f"{'样本数':<24} {n_a:>16} {n_b:>16} {'':>16} {'':>16}")
    print(f"{'CE均值':<24} {mean(a_ces)*100:>15.3f}% {mean(b_ces)*100:>15.3f}% {(mean(b_ces)-mean(a_ces))*100:>+15.3f}% {'≥8%':>16}")
    print(f"{'CE中位数':<24} {median(a_ces)*100:>15.3f}% {median(b_ces)*100:>15.3f}% {(median(b_ces)-median(a_ces))*100:>+15.3f}% {'':>16}")
    print(f"{'净收益率均值':<24} {mean(a_nets)*100:>15.3f}% {mean(b_nets)*100:>15.3f}% {(mean(b_nets)-mean(a_nets))*100:>+15.3f}% {'≥0.35%':>16}")
    print(f"{'净收益率中位数':<24} {median(a_nets)*100:>15.3f}% {median(b_nets)*100:>15.3f}% {(median(b_nets)-median(a_nets))*100:>+15.3f}% {'':>16}")
    if a_pos and b_pos:
        print(f"{'买点位置均值':<24} {mean(a_pos):>16.3f} {mean(b_pos):>16.3f} {mean(b_pos)-mean(a_pos):>+16.3f} {'≤0.35':>16}")
        print(f"{'买点位置中位数':<24} {median(a_pos):>16.3f} {median(b_pos):>16.3f} {median(b_pos)-median(a_pos):>+16.3f} {'':>16}")

    # 净收益正负占比
    a_pos_net = sum(1 for x in a_nets if x > 0)
    b_pos_net = sum(1 for x in b_nets if x > 0)
    print(f"{'净收益为正占比':<24} {a_pos_net/n_a*100:>15.1f}% {b_pos_net/n_b*100:>15.1f}% {(b_pos_net/n_b-a_pos_net/n_a)*100:>+15.1f}pp {'':>16}")

    # 出场原因分布
    print(f"\n{'=' * 120}")
    print("出场原因分布")
    print(f"{'=' * 120}")
    print(f"{'原因':<16} {'A组(即时)':>12} {'占比':>8} {'B组(Passive)':>16} {'占比':>8}")
    print(f"{'-'*16} {'-'*12} {'-'*8} {'-'*16} {'-'*8}")
    all_reasons = set(r["exit_reason"] for r in a_results) | set(r["exit_reason"] for r in b_results)
    for reason in sorted(all_reasons):
        a_cnt = sum(1 for r in a_results if r["exit_reason"] == reason)
        b_cnt = sum(1 for r in b_results if r["exit_reason"] == reason)
        a_pct = a_cnt / n_a * 100 if n_a else 0
        b_pct = b_cnt / n_b * 100 if n_b else 0
        print(f"  {reason:<14} {a_cnt:>12} {a_pct:>7.1f}% {b_cnt:>16} {b_pct:>7.1f}%")

    # B组含未成交的归一化对比（总资金口径）
    print(f"\n{'=' * 120}")
    print("总资金归一化对比（含B组未成交=收益0）")
    print(f"{'=' * 120}")
    # A组每次触发都成交，B组只有n_b/n_total次成交
    a_total_net = sum(a_nets)
    b_total_net = sum(b_nets)  # 未成交部分收益0
    a_avg_per_trigger = a_total_net / n_total
    b_avg_per_trigger = b_total_net / n_total
    print(f"  {'指标':<28} {'A组(即时J2)':>16} {'B组(Passive)':>16} {'差异':>16}")
    print(f"  {'-'*28} {'-'*16} {'-'*16} {'-'*16}")
    print(f"  {'每次触发净收益(总资金口径)':<28} {a_avg_per_trigger*100:>15.4f}% {b_avg_per_trigger*100:>15.4f}% {(b_avg_per_trigger-a_avg_per_trigger)*100:>+15.4f}%")
    print(f"  {'总净收益率合计':<28} {a_total_net*100:>15.2f}% {b_total_net*100:>15.2f}% {(b_total_net-a_total_net)*100:>+15.2f}%")

    # 保存
    out = {
        "step": "phase1_passive_j2",
        "n_total_triggers": n_total,
        "n_a": n_a,
        "n_b": n_b,
        "n_unfilled": b_unfilled,
        "fill_rate": n_b / n_total,
        "a_ce_mean": mean(a_ces),
        "b_ce_mean": mean(b_ces),
        "a_net_mean": mean(a_nets),
        "b_net_mean": mean(b_nets),
        "a_position_mean": mean(a_pos) if a_pos else None,
        "b_position_mean": mean(b_pos) if b_pos else None,
        "a_total_net": a_total_net,
        "b_total_net": b_total_net,
        "exit_dist_a": {r: sum(1 for x in a_results if x["exit_reason"] == r) for r in all_reasons},
        "exit_dist_b": {r: sum(1 for x in b_results if x["exit_reason"] == r) for r in all_reasons},
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "phase1_passive_j2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
