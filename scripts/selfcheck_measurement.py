#!/usr/bin/env python3
"""
Measurement V2 自检 —— 合成数据，不依赖任何回测产物 / K线文件
================================================================
用途：改完 measurement 或 backtest 通路后，一条命令确认没写坏。

    python scripts/selfcheck_measurement.py

覆盖：
  1. 哨兵语义：不可用返回 None，而 -1 / 0 必须作为合法值参与统计
  2. describe() 的 median / IQR 正确性
  3. spearman_ic 的方向与并列秩处理
  4. compute_v2_metrics_for_report 端到端跑通（含 excursion / trend_quality）
  5. Stage Gate 判定：PASS / FAIL / NO_EFFECT / INCONCLUSIVE 四类结论
  6. backtest 的 _bars 注入开关确实存在（Stage M0.5 通路）

退出码 0 = 全过；非 0 = 有失败项。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from at0.measurement.v2_metrics import (  # noqa: E402
    describe, spearman_ic, collect_distributions, compute_trade_quality_from_pairs,
    compute_attribution, compute_v2_metrics_for_report,
    _metric_entry_quality, _metric_exit_delay, _metric_excursion,
    _metric_trend_quality, _metric_wave_capture,
)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        FAILURES.append(name)


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


# ═══════════════════════════════════════════════════════════════
# 合成 K 线：一段先跌后涨的行情
#   idx:   0     1     2     3     4     5     6
#   low:  10.0  9.6   9.2   9.5   9.9  10.3  10.1
#  high:  10.2  9.9   9.6  10.0  10.4  10.8  10.6
# 当日最低点在 idx=2（9.2），最高点在 idx=5（10.8）
# ═══════════════════════════════════════════════════════════════
def make_bars() -> list[dict]:
    rows = [
        (10.0, 10.2, 10.1),
        (9.6, 9.9, 9.7),
        (9.2, 9.6, 9.4),
        (9.5, 10.0, 9.9),
        (9.9, 10.4, 10.3),
        (10.3, 10.8, 10.7),
        (10.1, 10.6, 10.2),
    ]
    return [
        {"time": f"2026-06-01 09:{35 + i * 5:02d}:00",
         "low": lo, "high": hi, "close": cl, "open": cl}
        for i, (lo, hi, cl) in enumerate(rows)
    ]


def t_sentinels() -> None:
    print("\n[1] 哨兵语义：不可用=None，-1/0 是合法值")
    bars = make_bars()

    # (a) 无 day_bars → entry 指标必须是 None，不能是 -1
    r = _metric_entry_quality({"direction": "buy", "fill_price": 10.0, "time": "x"},
                              {}, {}, [], [])
    check("entry 不可用返回 None（非 -1）", r["entry_delay_bars"] is None, repr(r))

    # (b) 在 idx=1 买入，当日最低点在 idx=2 → delay = 1-2 = -1（合法：早买1根）
    open_t = {"direction": "buy", "fill_price": 9.7,
              "time": bars[1]["time"], "date": "2026-06-01"}
    r = _metric_entry_quality(open_t, {}, {}, bars[1:], bars)
    check("Entry Delay = -1 是合法值（提前1根入场）",
          r["entry_delay_bars"] == -1, repr(r))

    # (c) -1 必须进入分布统计，不能被过滤
    per_trade = [{"entry_quality": {"entry_delay_bars": -1, "execution_gain_pct": -0.5},
                  "capture_efficiency": {"ce": 0.5}},
                 {"entry_quality": {"entry_delay_bars": 3, "execution_gain_pct": 1.0},
                  "capture_efficiency": {"ce": 0.3}},
                 {"entry_quality": {"entry_delay_bars": None, "execution_gain_pct": None},
                  "capture_efficiency": {"ce": 0.4}}]
    d = collect_distributions(per_trade)
    check("Entry Delay 分布 n=2（None 缺席，-1 在场）",
          d["entry_delay_bars"]["n"] == 2, repr(d["entry_delay_bars"]))
    check("Entry Delay median = 1.0（(-1+3)/2）",
          approx(d["entry_delay_bars"]["median"], 1.0), repr(d["entry_delay_bars"]))
    check("Execution Gain 负值未被丢弃（n=2）",
          d["execution_gain_pct"]["n"] == 2, repr(d["execution_gain_pct"]))

    # (d) exit_delay 不可用 → None（不是 0，0 表示完美退出）
    r = _metric_exit_delay({"direction": "buy"}, {}, {}, [], [])
    check("Exit Delay 不可用返回 None（非 0）", r["exit_delay_bars"] is None, repr(r))

    # 持仓 idx1..idx5，最高点在切片内 idx=4（对应全局 idx5），平仓在切片末尾
    r = _metric_exit_delay({"direction": "buy"}, {}, {}, bars[1:6], bars)
    check("Exit Delay = 0 表示恰好在极值K线平仓",
          r["exit_delay_bars"] == 0, repr(r))

    # (e) wave_number 不可用 → None（波数从 1 起算，0 会污染分布）
    r = _metric_wave_capture({"time": "not-in-bars"}, {}, {}, [], bars)
    check("Wave Number 不可用返回 None（非 0）", r["wave_number"] is None, repr(r))


def t_excursion_and_quality() -> None:
    print("\n[2] MAE / MFE / Trend Quality")
    bars = make_bars()
    # 在 9.7 买入，持仓 idx1..idx5：最低 9.2，最高 10.8
    open_t = {"direction": "buy", "fill_price": 9.7}
    r = _metric_excursion(open_t, {}, {}, bars[1:6], bars)
    check("MAE ≈ (9.7-9.2)/9.7 = 5.1546%", approx(r["mae_pct"], 5.1546, 1e-3), repr(r))
    check("MFE ≈ (10.8-9.7)/9.7 = 11.3402%", approx(r["mfe_pct"], 11.3402, 1e-3), repr(r))
    check("MAE/MFE ratio < 1（有利偏移更大）", r["mae_mfe_ratio"] < 1, repr(r))

    # closes: 9.7, 9.4, 9.9, 10.3, 10.7 → 上涨 3 次 / 4 次转移
    r = _metric_trend_quality(open_t, {}, {}, bars[1:6], bars)
    check("Trend Quality = 0.75（4次转移中3次有利）",
          approx(r["trend_quality"], 0.75), repr(r))

    # 卖出方向应当对称
    r_sell = _metric_trend_quality({"direction": "sell"}, {}, {}, bars[1:6], bars)
    check("卖出方向 Trend Quality = 0.25（对称）",
          approx(r_sell["trend_quality"], 0.25), repr(r_sell))


def t_stats() -> None:
    print("\n[3] describe / spearman_ic")
    d = describe([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    check("median = 5.5", approx(d["median"], 5.5), repr(d))
    check("p25 = 3.25", approx(d["p25"], 3.25), repr(d))
    check("p75 = 7.75", approx(d["p75"], 7.75), repr(d))
    check("空输入不抛异常且 n=0", describe([])["n"] == 0)

    check("完全同序 IC = 1", approx(spearman_ic([1, 2, 3, 4], [10, 20, 30, 40]), 1.0))
    check("完全反序 IC = -1", approx(spearman_ic([1, 2, 3, 4], [40, 30, 20, 10]), -1.0))
    check("样本 < 3 返回 None（不可用，非 0）", spearman_ic([1, 2], [1, 2]) is None)
    check("并列值不抛异常", isinstance(spearman_ic([1, 1, 1, 2], [3, 3, 2, 1]), float))


def t_end_to_end() -> None:
    print("\n[4] 端到端：compute_v2_metrics_for_report")
    bars = make_bars()
    # 结构须与 _reconstruct_pairs 一致：report -> daily_results[] -> trades[]
    # 开仓腿 status="open" 且 paired=False；平仓腿 paired=True 且 status 在允许集合内
    report = {
        "code": "000001",
        "daily_results": [{
            "date": "2026-06-01",
            "trades": [
                {"direction": "buy", "fill_price": 9.7, "shares": 100,
                 "time": bars[1]["time"], "date": "2026-06-01", "pnl": 0.0,
                 "status": "open", "paired": False,
                 "engine_ctx": {"alpha": 88.0, "expected_rr": 1.6,
                                "sub_scores": {"wave": 90.0, "support": 70.0},
                                "engines_active": True}},
                {"direction": "sell", "fill_price": 10.7, "shares": 100,
                 "time": bars[5]["time"], "date": "2026-06-01", "pnl": 100.0,
                 "status": "paired", "paired": True, "holding_bars": 4},
            ],
        }],
    }
    out = compute_v2_metrics_for_report(report, {"2026-06-01": bars})
    check("成功配对 1 笔", out.get("pair_count", 0) >= 1, repr(out.get("pair_count")))
    check("返回 distributions", "distributions" in out)
    check("返回 trade_quality", "trade_quality" in out)
    if out.get("per_trade"):
        tr = out["per_trade"][0]
        check("per_trade 含 excursion", "excursion" in tr)
        check("per_trade 含 trend_quality", "trend_quality" in tr)
        check("per_trade 透传 engine_ctx", (tr.get("engine_ctx") or {}).get("alpha") == 88.0)

    tq = compute_trade_quality_from_pairs(out.get("per_trade", []))
    check("trade_quality 有 net_pnl 字段", "net_pnl" in tq, repr(tq))

    attr = compute_attribution(out.get("per_trade", []))
    check("attribution 识别到 engines_active=True", attr.get("engines_active") is True, repr(attr))


def t_gate() -> None:
    print("\n[5] Stage Gate 判定")
    from compare_v2_ab import evaluate_gate, MIN_PAIRS

    def mk(n, ce, ed, pnl):
        return {
            "distributions": {
                "ce": {"median": ce, "mean": ce, "n": n},
                "entry_delay_bars": {"median": ed, "mean": ed, "n": n},
            },
            "trade_quality": {"n": n, "net_pnl": pnl},
            "attribution": {"engines_active": True},
        }

    # 样本不足
    v = evaluate_gate(mk(5, 0.30, 20, 100), mk(5, 0.40, 10, 120), "W")
    check("样本不足 → INCONCLUSIVE", v["verdict"] == "INCONCLUSIVE", v.get("reason", ""))

    # 逐笔全等（改动没生效）
    base = mk(50, 0.32, 19.6, 152.14)
    v = evaluate_gate(base, mk(50, 0.32, 19.6, 152.14), "W")
    check("逐笔全等 → NO_EFFECT", v["verdict"] == "NO_EFFECT", repr(v["verdict"]))
    check("标记 identical_to_base", v.get("identical_to_base") is True)

    # 达标：ED 20→10（-50% ≥ 20%），CE 0.30→0.36（+20% ≥ 5%）
    v = evaluate_gate(mk(50, 0.30, 20, 100), mk(50, 0.36, 10, 105), "W")
    check("过程指标达标 + 护栏OK → PASS", v["verdict"] == "PASS", repr(v))

    # 过程达标但净利润腰斩
    v = evaluate_gate(mk(50, 0.30, 20, 100), mk(50, 0.36, 10, 50), "W")
    check("net_pnl 跌破护栏 → PASS_WITH_WARNING",
          v["verdict"] == "PASS_WITH_WARNING", repr(v["verdict"]))

    # 改善不够
    v = evaluate_gate(mk(50, 0.30, 20, 100), mk(50, 0.305, 19, 100), "W")
    check("改善幅度不足 → FAIL", v["verdict"] == "FAIL", repr(v["verdict"]))

    check(f"MIN_PAIRS 护栏已设置（={MIN_PAIRS}）", MIN_PAIRS >= 30)


def t_backtest_wiring() -> None:
    print("\n[6] Stage M0.5：回测 Engine 通路")
    from at0.strategy import SignalParams
    sp = SignalParams()
    check("SignalParams 有 v3_engines_in_backtest", hasattr(sp, "v3_engines_in_backtest"))
    check("默认开启（对齐实盘口径）", getattr(sp, "v3_engines_in_backtest", False) is True)

    src = (PROJECT_ROOT / "src" / "at0" / "backtest.py").read_text(encoding="utf-8")
    check("回测 alpha 分支注入 _bars", "_bars=bars_up_to_now" in src)
    check("回测写入 alpha_ctx", "state.alpha_ctx = {" in src)
    check("成交记录带 engine_ctx", 'trade_record["engine_ctx"]' in src)

    # 关键回归：_bars 缺失且开启引擎时 fail-fast（不再静默退化为常数）
    asrc = (PROJECT_ROOT / "src" / "at0" / "score" / "alpha_score.py").read_text(encoding="utf-8")
    check("alpha_score 缺失 _bars 时 fail-fast（不再静默退化）",
          'snap.get("_bars")' in asrc and "bars is None" in asrc
          and "v3_engines_in_backtest" in asrc)


def main() -> int:
    print("=" * 66)
    print("  Measurement V2 自检（合成数据，无需回测产物）")
    print("=" * 66)
    for fn in (t_sentinels, t_excursion_and_quality, t_stats,
               t_end_to_end, t_gate, t_backtest_wiring):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  [ERROR] {fn.__name__} 抛出异常: {e}")
            traceback.print_exc()
            FAILURES.append(f"{fn.__name__} 异常")

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"  失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print(f"    - {f}")
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
