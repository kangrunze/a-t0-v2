"""检查短历史股票的实际首日交易日期，确认是上市晚而非下载缺失"""
import json
from pathlib import Path

DATA_DIR = Path(r"d:\project\a-t0-v2\data\zz500_5min")
short_codes = ["001221", "001386", "001389", "301526", "301536", "301606", "301611",
               "601112", "603049", "603092", "603175", "603341", "688411", "688615",
               "688692", "688702", "688708", "688709"]

print(f"{'code':<8}{'name':<12}{'first_day':<14}{'last_day':<14}{'days':<8}{'bars':<10}")
print("-" * 60)
for code in short_codes:
    f = DATA_DIR / f"{code}.json"
    with open(f, "r", encoding="utf-8") as fp:
        d = json.load(fp)
    days = sorted(d["daily_bars"].keys())
    first = days[0] if days else "N/A"
    last = days[-1] if days else "N/A"
    bars = d["meta"]["total_bars"]
    name = d.get("name", "")
    print(f"{code:<8}{name:<12}{first:<14}{last:<14}{len(days):<8}{bars:<10}")
