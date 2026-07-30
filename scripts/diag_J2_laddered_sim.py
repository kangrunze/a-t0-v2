#!/usr/bin/env python3
"""
J2 分批建仓模拟（不依赖预测，靠仓位结构降低平均成本）
=====================================================
K0（日内+跨日）已证明方向预测无可覆盖成本的优势。本脚本验证"不依赖预测、
靠仓位分批结构自然降低平均成本"的机制是否有价值。

方法：在36只池子×3年样本上，对J2已确认触发的每一次买入信号，把计划仓位
拆成4等份，分别挂在信号触发时VWAP下方0.5/1.0/1.5/2.0倍ATR的位置，用后续
bar的最低价判断哪些档位被触及（保守假设：只有最低价明确低于挂单价才算成交）。

对比：
  1. 实际成交档位数分布
  2. 分批加权平均买入价 vs J2单点入场价的CE对比
  3. 净盈亏对比（按相同总资金归一化）

数据来源：从已生成的J2回测trades.json提取触发点，回放5min K线判断档位成交。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.paths import ZZ500_5MIN_DIR
from at0.features import cumulative_vwap, intraday_atr, atr_relative

# 振幅筛选后的36只
POOL_36 = [
    "001221", "001309", "001389", "002261", "300339", "300475", "300548",
    "300570", "300620", "300735", "300757", "300972", "301526", "301536",
    "301606", "301611", "600602", "601099", "603000", "603119", "603175",
    "603728", "688166", "688213", "688318", "688322", "688331", "688361",
    "688411", "688498", "688582", "688615", "688629", "688692", "688702",
    "688709",
]

START = "2023-07-25"
END = "2026-07-22"

# 分批档位：VWAP下方 0.5/1.0/1.5/2.0 倍 ATR
LADDER_MULTS = [0.5, 1.0, 1.5, 2.0]
# 每档等额资金（1/4 总仓位）
N_LADDERS = len(LADDER_MULTS)
# 后续bar窗口：检查未来多少根bar内是否有档位被触及
# J2的max_holding_bars=12（1小时），用同样的窗口判断分批成交
LOOKFORWARD_BARS = 12
# 来回交易成本率（佣金万1×2 + 印花税0.05% + 滑点0.1%×2）
ROUND_TRIP_COST = 0.0033

BACKTEST_DIR = PROJECT_ROOT / "outputs" / "backtest"


def load_stock_bars(code: str) -> dict:
    path = ZZ500_5MIN_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("daily_bars", {})


def find_j2_trigger_trades(code: str) -> list[dict]:
    """从已生成的trades.json中提取J2回踩触发的买入腿。
    每条记录需包含: date, time, fill_price, vwap, signal_price。
    """
    # 查找该股票的trades文件（tag包含J2或默认TF回测）
    triggers = []
    # 匹配该股票的所有trades文件（g2_A/g2b_A/compare/TF等），后面再按内容筛选J2触发
    patterns = [f"{code}_*_trades.json"]
    for pattern in patterns:
        for trades_path in BACKTEST_DIR.glob(pattern):
            try:
                with open(trades_path, encoding="utf-8") as f:
                    trades = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
            for t in trades:
                if t.get("direction") != "buy":
                    continue
                rules_fired = t.get("rules_fired", [])
                if any("回踩入场-触发" in r for r in rules_fired):
                    # 需要vwap和fill_price
                    vwap = t.get("vwap")
                    fill_price = t.get("fill_price")
                    if vwap and vwap > 0 and fill_price and fill_price > 0:
                        triggers.append({
                            "date": t.get("date", ""),
                            "time": t.get("time", ""),
                            "fill_price": float(fill_price),
                            "vwap": float(vwap),
                            "signal_price": float(t.get("signal_price", 0)),
                            "source_file": trades_path.name,
                        })
    # 去重（同一时间点可能出现在多个文件中）
    seen = set()
    unique = []
    for t in triggers:
        key = (t["date"], t["time"])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def simulate_laddered_entry(
    daily_bars: dict,
    trigger: dict,
) -> dict | None:
    """对单次J2触发，模拟分批建仓。
    返回: {n_filled, avg_buy_price, j2_price, future_close, ...}
    """
    date = trigger["date"]
    time_str = trigger["time"]
    vwap = trigger["vwap"]
    j2_price = trigger["fill_price"]

    bars = daily_bars.get(date, [])
    if not bars:
        return None

    # 找到触发bar的索引
    trigger_idx = None
    for i, b in enumerate(bars):
        if b.get("time", "").startswith(time_str[-5:]):  # 匹配 HH:MM
            trigger_idx = i
            break
    if trigger_idx is None:
        # 尝试用完整时间匹配
        for i, b in enumerate(bars):
            if b.get("time", "") == time_str:
                trigger_idx = i
                break
    if trigger_idx is None:
        return None

    # 计算触发时的ATR（用截至触发bar的数据）
    bars_up_to_trigger = bars[: trigger_idx + 1]
    atr = intraday_atr(bars_up_to_trigger, period=14)
    if atr is None or atr <= 0:
        return None
    atr_rel = atr_relative(atr, vwap)
    if atr_rel is None or atr_rel <= 0:
        return None

    # 计算各档位挂单价（VWAP下方 N倍ATR）
    ladder_prices = [vwap * (1 - m * atr_rel) for m in LADDER_MULTS]

    # 检查后续 LOOKFORWARD_BARS 根bar的最低价
    future_bars = bars[trigger_idx + 1: trigger_idx + 1 + LOOKFORWARD_BARS]
    if not future_bars:
        return None

    # 各档位是否成交（最低价明确低于挂单价）
    filled = [False] * N_LADDERS
    for fb in future_bars:
        low = float(fb["low"])
        for i, lp in enumerate(ladder_prices):
            if not filled[i] and low < lp:
                filled[i] = True

    n_filled = sum(filled)
    if n_filled == 0:
        # 没有档位成交，用触发价作为fallback（模拟"未成交则不建仓"的保守假设）
        # 这里按用户要求"不强行用市价补齐"，返回0档成交
        return {
            "date": date,
            "time": time_str,
            "j2_price": j2_price,
            "vwap": vwap,
            "atr_rel": atr_rel,
            "ladder_prices": ladder_prices,
            "filled": filled,
            "n_filled": 0,
            "avg_buy_price": None,  # 未成交
            "future_close": float(future_bars[-1]["close"]),
        }

    # 加权平均买入价（等额资金，即等股数，简单算术平均）
    filled_prices = [ladder_prices[i] for i in range(N_LADDERS) if filled[i]]
    avg_buy_price = sum(filled_prices) / n_filled

    # 未来的收盘价（用于计算CE）
    future_close = float(future_bars[-1]["close"])

    return {
        "date": date,
        "time": time_str,
        "j2_price": j2_price,
        "vwap": vwap,
        "atr_rel": atr_rel,
        "ladder_prices": ladder_prices,
        "filled": filled,
        "n_filled": n_filled,
        "avg_buy_price": avg_buy_price,
        "future_close": future_close,
    }


def main():
    print("=" * 110)
    print("J2 分批建仓模拟（不依赖预测，靠仓位结构降低平均成本）")
    print("=" * 110)
    print(f"数据: 振幅筛选36只 × 3年（{START} ~ {END}）")
    print(f"分批档位: VWAP下方 {LADDER_MULTS} 倍ATR，每档1/{N_LADDERS}仓位")
    print(f"成交判断: 后续{LOOKFORWARD_BARS}根bar最低价 < 挂单价")
    print(f"保守假设: 未成交档位不补齐")
    print(f"交易成本: 来回 {ROUND_TRIP_COST*100:.2f}%")

    all_results = []
    by_stock = {}

    for code in POOL_36:
        daily_bars = load_stock_bars(code)
        if not daily_bars:
            print(f"  {code}: 无数据")
            continue
        triggers = find_j2_trigger_trades(code)
        if not triggers:
            print(f"  {code}: 无J2触发记录")
            continue

        stock_results = []
        for trig in triggers:
            r = simulate_laddered_entry(daily_bars, trig)
            if r:
                stock_results.append(r)
                all_results.append(r)

        by_stock[code] = stock_results
        print(f"  {code}: J2触发{len(triggers)}次, 有效模拟{len(stock_results)}次")

    n_total = len(all_results)
    print(f"\n总J2触发模拟数: {n_total}")
    if n_total == 0:
        print("无数据，退出")
        return

    # 1. 成交档位数分布
    print(f"\n{'=' * 110}")
    print("1. 实际成交档位数分布")
    print(f"{'=' * 110}")
    n_filled_dist = [r["n_filled"] for r in all_results]
    for k in range(N_LADDERS + 1):
        cnt = n_filled_dist.count(k)
        pct = cnt / n_total * 100
        print(f"  成交{k}档: {cnt:>4} 次 ({pct:>5.1f}%)")

    # 2. CE对比（只看成交了的）
    valid = [r for r in all_results if r["n_filled"] > 0]
    n_valid = len(valid)
    print(f"\n{'=' * 110}")
    print(f"2. CE对比（仅成交样本，n={n_valid}/{n_total}）")
    print(f"{'=' * 110}")

    if n_valid == 0:
        print("  无成交样本")
    else:
        # J2单点入场CE = (future_close - j2_price) / j2_price
        j2_ces = [(r["future_close"] - r["j2_price"]) / r["j2_price"] for r in valid]
        # 分批入场CE = (future_close - avg_buy_price) / avg_buy_price
        ladder_ces = [(r["future_close"] - r["avg_buy_price"]) / r["avg_buy_price"] for r in valid]

        j2_ce_mean = mean(j2_ces)
        ladder_ce_mean = mean(ladder_ces)
        j2_ce_median = median(j2_ces)
        ladder_ce_median = median(ladder_ces)

        # CE改善 = ladder_ce - j2_ce
        ce_improvements = [lc - jc for lc, jc in zip(ladder_ces, j2_ces)]
        ce_imp_mean = mean(ce_improvements)
        ce_imp_median = median(ce_improvements)
        n_improved = sum(1 for x in ce_improvements if x > 0)

        print(f"\n  {'指标':<24} {'J2单点入场':>14} {'分批建仓':>14} {'差异':>14}")
        print(f"  {'-'*24} {'-'*14} {'-'*14} {'-'*14}")
        print(f"  {'CE均值':<24} {j2_ce_mean*100:>13.3f}% {ladder_ce_mean*100:>13.3f}% {(ladder_ce_mean-j2_ce_mean)*100:>+13.3f}%")
        print(f"  {'CE中位数':<24} {j2_ce_median*100:>13.3f}% {ladder_ce_median*100:>13.3f}% {(ladder_ce_median-j2_ce_median)*100:>+13.3f}%")
        print(f"\n  CE改善（分批-J2）: 均值={ce_imp_mean*100:+.3f}%, 中位={ce_imp_median*100:+.3f}%")
        print(f"  改善样本占比: {n_improved}/{n_valid} ({n_improved/n_valid*100:.1f}%)")

        # 3. 净盈亏对比（按相同总资金归一化）
        print(f"\n{'=' * 110}")
        print(f"3. 净盈亏对比（按相同总资金归一化）")
        print(f"{'=' * 110}")

        # 假设总资金=1，J2全仓买入，分批按成交档数比例买入
        # J2净收益 = CE - 成本（来回）
        # 分批净收益 = (n_filled/N) × (CE - 成本)（未成交部分资金不投入，收益0）
        # 但这样分批会因为仓位低而收益规模小，不公平
        # 正确归一化：假设总资金相同，J2全仓，分批每档用1/N资金
        # 分批实际投入资金 = (n_filled/N) × 总资金
        # 为了按相同总资金对比，把分批的收益放大到全仓规模：
        #   归一化分批收益 = (分批实际收益) / (n_filled/N) = 等价全仓收益
        # 但如果n_filled=0，无法归一化，这部分样本J2有收益而分批为0

        # 更直接的对比方式：
        # 假设总资金W，J2全仓买入 shares = W/j2_price
        # 分批：每档用 W/N 资金，成交档买入 shares_i = (W/N) / ladder_price_i
        # 分批实际买入总股数 = sum(shares_i for filled)
        # 分批实际投入 = n_filled × W/N
        # 期末价值 = sum(shares_i × future_close)
        # 分批收益率（按实际投入） = (期末价值 - 实际投入) / 实际投入
        # 归一化到总资金W：分批未投入资金收益为0
        #   分批总资金收益率 = (期末价值 - 实际投入) / W = (n_filled/N) × 分批收益率

        j2_returns = []  # J2总资金收益率
        ladder_returns = []  # 分批总资金收益率（未投入部分收益0）
        ladder_returns_reinvested = []  # 分批按实际投入计算收益率（放大到全仓等价）

        for r in valid:
            j2_ce = (r["future_close"] - r["j2_price"]) / r["j2_price"]
            j2_net = j2_ce - ROUND_TRIP_COST
            j2_returns.append(j2_net)

            n_f = r["n_filled"]
            # 分批：每档W/N资金，成交档买入
            # 实际投入 = n_f/N × W
            # 每档股数 = (W/N) / ladder_price_i
            # 期末价值 = sum((W/N)/lp × fc) = (W/N) × fc × sum(1/lp)
            # 分批毛收益 = 期末价值 - 实际投入 = (W/N) × fc × sum(1/lp) - n_f×W/N
            # 分批收益率（按实际投入） = (fc × sum(1/lp) - n_f) / n_f
            filled_prices = [r["ladder_prices"][i] for i in range(N_LADDERS) if r["filled"][i]]
            sum_inv_lp = sum(1.0 / lp for lp in filled_prices)
            ladder_ce_actual = (r["future_close"] * sum_inv_lp - n_f) / n_f
            ladder_net_actual = ladder_ce_actual - ROUND_TRIP_COST

            # 归一化到总资金W：只投入了 n_f/N 的资金
            ladder_return_total = (n_f / N_LADDERS) * ladder_net_actual
            ladder_returns.append(ladder_return_total)
            ladder_returns_reinvested.append(ladder_net_actual)

        # 未成交样本：J2有收益，分批为0
        n_unfilled = n_total - n_valid
        unfilled_j2 = []
        for r in all_results:
            if r["n_filled"] == 0:
                j2_ce = (r["future_close"] - r["j2_price"]) / r["j2_price"]
                unfilled_j2.append(j2_ce - ROUND_TRIP_COST)

        # 全样本对比（含未成交）
        all_j2 = j2_returns + unfilled_j2
        all_ladder = ladder_returns + [0.0] * n_unfilled  # 未成交收益0

        j2_mean = mean(all_j2)
        ladder_mean = mean(all_ladder)
        j2_total = sum(all_j2)
        ladder_total = sum(all_ladder)

        print(f"\n  全样本（n={n_total}, 含{n_unfilled}次未成交）:")
        print(f"  {'指标':<28} {'J2单点入场':>16} {'分批建仓':>16} {'差异':>16}")
        print(f"  {'-'*28} {'-'*16} {'-'*16} {'-'*16}")
        print(f"  {'总资金收益率均值':<28} {j2_mean*100:>15.3f}% {ladder_mean*100:>15.3f}% {(ladder_mean-j2_mean)*100:>+15.3f}%")
        print(f"  {'总资金收益率合计':<28} {j2_total*100:>15.3f}% {ladder_total*100:>15.3f}% {(ladder_total-j2_total)*100:>+15.3f}%")

        # 仅成交样本（等价全仓对比）
        j2_valid_mean = mean(j2_returns)
        ladder_reinv_mean = mean(ladder_returns_reinvested)
        print(f"\n  仅成交样本（n={n_valid}, 等价全仓收益率）:")
        print(f"  {'指标':<28} {'J2单点入场':>16} {'分批(等价全仓)':>16} {'差异':>16}")
        print(f"  {'-'*28} {'-'*16} {'-'*16} {'-'*16}")
        print(f"  {'净收益率均值':<28} {j2_valid_mean*100:>15.3f}% {ladder_reinv_mean*100:>15.3f}% {(ladder_reinv_mean-j2_valid_mean)*100:>+15.3f}%")

        # 按成交档数分层看CE改善
        print(f"\n  按成交档数分层:")
        print(f"  {'档数':<8} {'样本':>8} {'J2-CE':>12} {'分批-CE':>12} {'CE改善':>12} {'J2净收益':>12} {'分批净收益':>12}")
        print(f"  {'-'*8} {'-'*8} {'-'*12} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")
        for k in range(1, N_LADDERS + 1):
            layer = [r for r in valid if r["n_filled"] == k]
            if not layer:
                continue
            j2_ces_k = [(r["future_close"] - r["j2_price"]) / r["j2_price"] for r in layer]
            lad_ces_k = [(r["future_close"] - r["avg_buy_price"]) / r["avg_buy_price"] for r in layer]
            j2_net_k = [jc - ROUND_TRIP_COST for jc in j2_ces_k]
            lad_net_k = [lc - ROUND_TRIP_COST for lc in lad_ces_k]
            print(f"  {k}档{'':<4} {len(layer):>8} {mean(j2_ces_k)*100:>11.3f}% {mean(lad_ces_k)*100:>11.3f}% "
                  f"{(mean(lad_ces_k)-mean(j2_ces_k))*100:>+11.3f}% {mean(j2_net_k)*100:>+11.3f}% {mean(lad_net_k)*100:>+11.3f}%")

    # 保存
    out = {
        "step": "J2_laddered_entry_sim",
        "data": "振幅筛选36只 × 3年",
        "ladder_mults": LADDER_MULTS,
        "lookforward_bars": LOOKFORWARD_BARS,
        "round_trip_cost": ROUND_TRIP_COST,
        "n_total": n_total,
        "n_valid": n_valid,
        "n_unfilled": n_total - n_valid if n_valid else n_total,
        "fill_dist": {str(k): n_filled_dist.count(k) for k in range(N_LADDERS + 1)},
        "summary": {
            "j2_ce_mean": j2_ce_mean * 100 if n_valid else None,
            "ladder_ce_mean": ladder_ce_mean * 100 if n_valid else None,
            "ce_improvement_mean": ce_imp_mean * 100 if n_valid else None,
            "ce_improvement_median": ce_imp_median * 100 if n_valid else None,
            "j2_total_return": j2_total * 100 if n_valid else None,
            "ladder_total_return": ladder_total * 100 if n_valid else None,
        },
    }
    out_path = PROJECT_ROOT / "outputs" / "oos_validation" / "J2_laddered_entry_sim.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
