#!/usr/bin/env python3
"""
数据迁移：minute_local 每股每日一文件 → 每股一文件（zz500 格式）
================================================================
将 data/minute_local/{code}/{date}.json 合并为
data/minute_local_merged/{code}.json

格式与 zz500_5min 完全一致：
  {
    "meta": {"source": "minute_local", "frequency": "1min", ...},
    "daily_bars": {"2025-07-21": [...bars...], ...}
  }

合并后可直接用 load_multi_day_zz500 加载（data_dir=minute_local_merged）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# data 目录在项目外（d:/project/data）
DATA_ROOT = Path("d:/project/data")
SRC_DIR = DATA_ROOT / "minute_local"
DST_DIR = DATA_ROOT / "minute_local_merged"


def merge_one_code(code_dir: Path) -> dict | None:
    """把 {code_dir}/{date}.json 合并为 {code}.json 内容。"""
    date_files = sorted(code_dir.glob("*.json"))
    if not date_files:
        return None

    daily_bars: dict[str, list[dict]] = {}
    frequency = "1min"
    source = "minute_local"

    for df in date_files:
        date_str = df.stem
        try:
            with open(df, "r", encoding="utf-8") as fp:
                d = json.load(fp)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] 跳过损坏文件 {df.name}: {e}")
            continue

        # 兼容两种格式：直接是 bars 列表，或 {"bars": [...], "meta": {...}}
        if isinstance(d, list):
            bars = d
        elif isinstance(d, dict):
            bars = d.get("bars") or d.get("daily_bars") or []
            meta = d.get("meta", {}) or {}
            if meta.get("frequency"):
                frequency = meta["frequency"]
            if meta.get("source"):
                source = meta["source"]
        else:
            continue

        if bars:
            daily_bars[date_str] = bars

    if not daily_bars:
        return None

    return {
        "meta": {
            "source": source,
            "frequency": frequency,
            "code": code_dir.name,
            "merged_from": "minute_local_per_date",
            "dates_count": len(daily_bars),
        },
        "daily_bars": daily_bars,
    }


def main() -> int:
    if not SRC_DIR.exists():
        print(f"[ERROR] 源目录不存在: {SRC_DIR}", file=sys.stderr)
        return 2

    DST_DIR.mkdir(parents=True, exist_ok=True)

    code_dirs = sorted(d for d in SRC_DIR.iterdir() if d.is_dir())
    print(f"迁移 {len(code_dirs)} 只股票: {SRC_DIR} → {DST_DIR}")

    migrated = 0
    skipped = 0
    for code_dir in code_dirs:
        merged = merge_one_code(code_dir)
        if merged is None:
            print(f"  [SKIP] {code_dir.name}: 无有效数据")
            skipped += 1
            continue

        out_path = DST_DIR / f"{code_dir.name}.json"
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(merged, fp, ensure_ascii=False)
        dates_count = merged["meta"]["dates_count"]
        print(f"  [OK] {code_dir.name}: {dates_count} 交易日 → {out_path.name}")
        migrated += 1

    print(f"\n完成: 迁移 {migrated} 只，跳过 {skipped} 只")
    print(f"输出目录: {DST_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
