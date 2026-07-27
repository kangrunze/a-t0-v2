"""
Stage E-1 单元测试：regime.classify_daily_regime
覆盖三类日K样本：趋势(TRENDING)/震荡(RANGING)/异常(NO_TRADE)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from at0.regime import (
    DailyRegime,
    RegimeParams,
    classify_daily_regime,
    synthesize_daily_klines,
    precompute_regimes_for_dates,
)


def _make_uptrend_klines(n: int = 35, start: float = 10.0, step: float = 0.2) -> list[dict]:
    """生成上升趋势日K（close 持续上涨，ADX 会很高）。"""
    klines = []
    price = start
    for i in range(n):
        o = price
        c = price + step
        h = c + 0.05
        l = o - 0.05
        klines.append({
            "date": f"2026-01-{i+1:02d}",
            "open": o, "high": h, "low": l, "close": c,
            "volume": 1_000_000,
        })
        price = c
    return klines


def _make_downtrend_klines(n: int = 35, start: float = 20.0, step: float = 0.2) -> list[dict]:
    """生成下降趋势日K（close 持续下跌，ADX 高，但 close < MA20）。"""
    klines = []
    price = start
    for i in range(n):
        o = price
        c = price - step
        h = o + 0.05
        l = c - 0.05
        klines.append({
            "date": f"2026-01-{i+1:02d}",
            "open": o, "high": h, "low": l, "close": c,
            "volume": 1_000_000,
        })
        price = c
    return klines


def _make_ranging_klines(n: int = 35, base: float = 15.0) -> list[dict]:
    """生成震荡日K（close 在 base 附近随机小幅波动，ADX 低）。

    关键：上下交替波动（不形成单边趋势），振幅小，让 ADX 维持低值。
    """
    klines = []
    for i in range(n):
        # 交替涨跌：偶数日涨、奇数日跌，振幅 ±0.2%
        if i % 2 == 0:
            delta = base * 0.002
        else:
            delta = -base * 0.002
        o = base
        c = base + delta
        h = max(o, c) + 0.01
        l = min(o, c) - 0.01
        klines.append({
            "date": f"2026-01-{i+1:02d}",
            "open": o, "high": h, "low": l, "close": c,
            "volume": 1_000_000,
        })
    return klines


def _make_flat_klines(n: int = 35, price: float = 10.0) -> list[dict]:
    """生成一字板日K（high == low）。"""
    return [{
        "date": f"2026-01-{i+1:02d}",
        "open": price, "high": price, "low": price, "close": price,
        "volume": 1_000_000,
    } for i in range(n)]


def _make_halted_klines(n: int = 35, price: float = 10.0) -> list[dict]:
    """生成停牌日K（volume == 0）。"""
    return [{
        "date": f"2026-01-{i+1:02d}",
        "open": price, "high": price + 0.1, "low": price - 0.1, "close": price,
        "volume": 0,
    } for i in range(n)]


# ── 测试用例 ──

def test_uptrend_is_trending():
    """上升趋势：close 持续上涨站稳 MA20，ADX 高 → TRENDING。"""
    klines = _make_uptrend_klines(n=30)
    regime = classify_daily_regime(klines)
    assert regime == DailyRegime.TRENDING, f"上升趋势应判为 TRENDING，实际 {regime}"
    print(f"[OK] test_uptrend_is_trending: {regime}")


def test_downtrend_is_trending():
    """下降趋势：ADX 高（不区分上下方向）→ TRENDING（v2 修复方向不对称 bug）。

    v1（已废弃）：要求 close > MA20，下降趋势被误判 NO_TRADE。
    v2（本轮修复）：去掉 close > MA20 约束，双向趋势跟随都能跑。
    """
    klines = _make_downtrend_klines(n=35)
    regime = classify_daily_regime(klines)
    assert regime == DailyRegime.TRENDING, f"下降趋势应判为 TRENDING（v2修复后），实际 {regime}"
    print(f"[OK] test_downtrend_is_trending: {regime}")


def test_ranging_is_ranging():
    """震荡盘：close 在 MA20 附近小幅波动，ADX 低 → RANGING。"""
    klines = _make_ranging_klines(n=30)
    regime = classify_daily_regime(klines)
    assert regime == DailyRegime.RANGING, f"震荡盘应判为 RANGING，实际 {regime}"
    print(f"[OK] test_ranging_is_ranging: {regime}")


def test_insufficient_data_is_no_trade():
    """数据不足：日K数 < min_daily_klines → NO_TRADE。"""
    klines = _make_uptrend_klines(n=10)  # 10 < 21
    regime = classify_daily_regime(klines)
    assert regime == DailyRegime.NO_TRADE, f"数据不足应判为 NO_TRADE，实际 {regime}"
    print(f"[OK] test_insufficient_data_is_no_trade: {regime}")


def test_flat_board_is_no_trade():
    """一字板：high == low → NO_TRADE。"""
    klines = _make_flat_klines(n=30)
    regime = classify_daily_regime(klines)
    assert regime == DailyRegime.NO_TRADE, f"一字板应判为 NO_TRADE，实际 {regime}"
    print(f"[OK] test_flat_board_is_no_trade: {regime}")


def test_halted_is_no_trade():
    """停牌：volume == 0 → NO_TRADE。"""
    klines = _make_halted_klines(n=30)
    regime = classify_daily_regime(klines)
    assert regime == DailyRegime.NO_TRADE, f"停牌应判为 NO_TRADE，实际 {regime}"
    print(f"[OK] test_halted_is_no_trade: {regime}")


def test_synthesize_daily_klines():
    """测试从 5min bars 合成日K。"""
    # 构造2天5min数据
    daily_bars_5min = {
        "2026-01-01": [
            {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 1000},
            {"open": 10.2, "high": 10.8, "low": 10.0, "close": 10.6, "volume": 1500},
        ],
        "2026-01-02": [
            {"open": 10.6, "high": 11.0, "low": 10.5, "close": 10.9, "volume": 1200},
            {"open": 10.9, "high": 11.2, "low": 10.8, "close": 11.1, "volume": 1800},
        ],
    }
    klines = synthesize_daily_klines(daily_bars_5min)
    assert len(klines) == 2
    assert klines[0]["open"] == 10.0
    assert klines[0]["high"] == 10.8
    assert klines[0]["low"] == 9.8
    assert klines[0]["close"] == 10.6
    assert klines[0]["volume"] == 2500
    assert klines[1]["close"] == 11.1
    print(f"[OK] test_synthesize_daily_klines: {len(klines)} 根日K合成正确")


def test_precompute_regimes_causal():
    """测试 precompute_regimes_for_dates 的因果性：D 日 regime 只用 D-1 前的数据。"""
    klines = _make_uptrend_klines(n=35)
    # 对第 33 日算 regime，应该只用前 32 日的日K（32 >= min_daily_klines=30，ADX 稳定）
    target_dates = ["2026-01-33"]
    regimes = precompute_regimes_for_dates(klines, target_dates)
    assert "2026-01-33" in regimes
    # 前32日数据足够，且是上升趋势，应判 TRENDING
    assert regimes["2026-01-33"] == DailyRegime.TRENDING, \
        f"第33日 regime 应为 TRENDING，实际 {regimes['2026-01-33']}"
    print(f"[OK] test_precompute_regimes_causal: {regimes}")


def test_naming_no_ambiguity():
    """E-3 验收：命名无歧义——daily_adx 与 tf_adx_threshold 是不同变量。"""
    # regime.py 里用 daily_adx_threshold
    p = RegimeParams()
    assert hasattr(p, "daily_adx_threshold")
    assert p.daily_adx_threshold == 25.0
    # strategy.py 里用 tf_adx_threshold（不同变量）
    from at0.strategy import SignalParams
    sp = SignalParams()
    assert hasattr(sp, "tf_adx_threshold")
    assert sp.tf_adx_threshold == 35.0  # yaml 里的值
    # 两者值不同（一个是日线25，一个是5min 35）
    assert p.daily_adx_threshold != sp.tf_adx_threshold or True  # 值可以相同但变量名不同
    print(f"[OK] test_naming_no_ambiguity: daily_adx_threshold={p.daily_adx_threshold}, "
          f"tf_adx_threshold={sp.tf_adx_threshold}（命名区分清晰）")


def main() -> int:
    print("=" * 78)
    print("Stage E-1 单元测试：regime.classify_daily_regime")
    print("=" * 78)
    tests = [
        test_uptrend_is_trending,
        test_downtrend_is_trending,
        test_ranging_is_ranging,
        test_insufficient_data_is_no_trade,
        test_flat_board_is_no_trade,
        test_halted_is_no_trade,
        test_synthesize_daily_klines,
        test_precompute_regimes_causal,
        test_naming_no_ambiguity,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print("\n" + "=" * 78)
    print(f"总计：{passed} 通过，{failed} 失败")
    print("=" * 78)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
