"""验证中证500 5min数据完整性"""
import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 数据根目录由 src/at0/paths.py 集中管理，支持环境变量 AT0_DATA_DIR 覆盖。
try:
    from at0.paths import ZZ500_5MIN_DIR as DATA_DIR
except (ValueError, ImportError):
    paths_path = PROJECT_ROOT / "src" / "at0" / "paths.py"
    spec = importlib.util.spec_from_file_location("_at0_paths", str(paths_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    DATA_DIR = mod.ZZ500_5MIN_DIR
files = sorted(DATA_DIR.glob("*.json"))
print(f"总文件数: {len(files)}")

stats = {
    "total_bars_min": float("inf"),
    "total_bars_max": 0,
    "trading_days_min": float("inf"),
    "trading_days_max": 0,
    "files_with_failed_months": 0,
    "total_failed_months": 0,
    "zero_bar_files": 0,
    "short_history_files": 0,
}

issues = []
bar_sum = 0
day_sum = 0
for f in files:
    try:
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
        meta = d.get("meta", {})
        bars = meta.get("total_bars", 0)
        days = meta.get("trading_days", 0)
        failed = meta.get("failed_months", [])
        bar_sum += bars
        day_sum += days
        if bars < stats["total_bars_min"]:
            stats["total_bars_min"] = bars
        if bars > stats["total_bars_max"]:
            stats["total_bars_max"] = bars
        if days < stats["trading_days_min"]:
            stats["trading_days_min"] = days
        if days > stats["trading_days_max"]:
            stats["trading_days_max"] = days
        if failed:
            stats["files_with_failed_months"] += 1
            stats["total_failed_months"] += len(failed)
            issues.append(f"{d['code']}: failed_months={failed}")
        if bars == 0:
            stats["zero_bar_files"] += 1
            issues.append(f"{d['code']}: 0 bars")
        if days < 700:
            stats["short_history_files"] += 1
            issues.append(f"{d['code']}: only {days} trading days (start={d.get('start_date')}, end={d.get('end_date')})")
    except Exception as e:
        issues.append(f"{f.name}: parse error {e}")

print(f"总K线条数: {bar_sum:,}")
print(f"平均每文件K线: {bar_sum / len(files):,.0f}")
print(f"平均交易日: {day_sum / len(files):.1f}")
print(f"K线数范围: [{stats['total_bars_min']}, {stats['total_bars_max']}]")
print(f"交易日范围: [{stats['trading_days_min']}, {stats['trading_days_max']}]")
print(f"有失败月份的文件数: {stats['files_with_failed_months']}")
print(f"失败月份总数: {stats['total_failed_months']}")
print(f"0K线文件数: {stats['zero_bar_files']}")
print(f"短历史文件数(<700天): {stats['short_history_files']}")
print()
print(f"问题文件数: {len(issues)}")
print("问题清单(前30):")
for i in issues[:30]:
    print(f"  {i}")
