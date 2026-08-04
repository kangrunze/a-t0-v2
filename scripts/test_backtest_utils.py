"""
backtest.py 工具函数测试（NEW-1 修复验证）
==============================================
测试 _to_minutes 和 _infer_frequency 修复后的正确性。

运行: python scripts/test_backtest_utils.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.at0.backtest import _to_minutes, _infer_frequency


def test_to_minutes_yyyy_mm_dd_hh_mm_ss():
    """NEW-1 修复: "YYYY-MM-DD HH:MM:SS" 格式应正确解析为分钟数。"""
    result = _to_minutes("2026-07-23 09:35:00")
    assert result == 575, f"Expected 575, got {result}"
    print(f"  PASS: _to_minutes('2026-07-23 09:35:00') = {result}")


def test_to_minutes_hh_mm_ss():
    """"HH:MM:SS" 格式应正确解析。"""
    result = _to_minutes("14:30:00")
    assert result == 870, f"Expected 870, got {result}"
    print(f"  PASS: _to_minutes('14:30:00') = {result}")


def test_to_minutes_hh_mm():
    """HH:MM 格式应正确解析。"""
    result = _to_minutes("09:35")
    assert result == 575, f"Expected 575, got {result}"
    print(f"  PASS: _to_minutes('09:35') = {result}")


def test_to_minutes_empty():
    """空字符串应返回 0。"""
    result = _to_minutes("")
    assert result == 0, f"Expected 0, got {result}"
    print(f"  PASS: _to_minutes('') = {result}")


def test_to_minutes_date_only():
    """仅有日期无时间应返回 0。"""
    result = _to_minutes("2026-07-23")
    assert result == 0, f"Expected 0, got {result}"
    print(f"  PASS: _to_minutes('2026-07-23') = {result}")


def test_to_minutes_risk_engine_840():
    """14:00 后 minute_of_day >= 840（risk_engine 时间维风控分支）。"""
    result = _to_minutes("2026-07-23 14:00:00")
    assert result >= 840, f"Expected >= 840, got {result}"
    print(f"  PASS: _to_minutes('2026-07-23 14:00:00') = {result} (>= 840)")


def test_to_minutes_risk_engine_870():
    """14:30 后 minute_of_day >= 870（risk_engine 尾盘强平分支）。"""
    result = _to_minutes("2026-07-23 14:35:00")
    assert result >= 870, f"Expected >= 870, got {result}"
    print(f"  PASS: _to_minutes('2026-07-23 14:35:00') = {result} (>= 870)")


def test_infer_frequency_5min():
    """5min 数据应推断为 '5min'。"""
    bars = [
        {"time": "2026-07-23 09:35:00"},
        {"time": "2026-07-23 09:40:00"},
    ]
    result = _infer_frequency(bars)
    assert result == "5min", f"Expected '5min', got '{result}'"
    print(f"  PASS: _infer_frequency(5min bars) = '{result}'")


def test_infer_frequency_1min():
    """1min 数据应推断为 '1min'。"""
    bars = [
        {"time": "2026-07-23 09:35:00"},
        {"time": "2026-07-23 09:36:00"},
    ]
    result = _infer_frequency(bars)
    assert result == "1min", f"Expected '1min', got '{result}'"
    print(f"  PASS: _infer_frequency(1min bars) = '{result}'")


def test_infer_frequency_single_bar():
    """单根 bar 应返回 '1min'（默认值）。"""
    bars = [{"time": "2026-07-23 09:35:00"}]
    result = _infer_frequency(bars)
    assert result == "1min", f"Expected '1min', got '{result}'"
    print(f"  PASS: _infer_frequency(single bar) = '{result}'")


def test_infer_frequency_empty():
    """空列表应返回 '1min'（默认值）。"""
    result = _infer_frequency([])
    assert result == "1min", f"Expected '1min', got '{result}'"
    print(f"  PASS: _infer_frequency([]) = '{result}'")


if __name__ == "__main__":
    print("=" * 60)
    print("backtest.py 工具函数测试（NEW-1 修复验证）")
    print("=" * 60)
    print()

    tests = [
        test_to_minutes_yyyy_mm_dd_hh_mm_ss,
        test_to_minutes_hh_mm_ss,
        test_to_minutes_hh_mm,
        test_to_minutes_empty,
        test_to_minutes_date_only,
        test_to_minutes_risk_engine_840,
        test_to_minutes_risk_engine_870,
        test_infer_frequency_5min,
        test_infer_frequency_1min,
        test_infer_frequency_single_bar,
        test_infer_frequency_empty,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1

    print()
    print(f"结果: {passed}/{len(tests)} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)