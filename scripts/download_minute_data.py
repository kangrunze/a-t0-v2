#!/usr/bin/env python3
"""
批量下载分钟级K线数据到本地指定目录（供 backtest_zz500.py --data-dir 离线回测）。

存储约定
========
按"每股每日一文件"组织，便于子范围查询与断点续传：

    {out_dir}/{code}/{date}.json

每个文件内容：
    {
      "code":   "600000",
      "date":   "2026-07-23",
      "bars":   [{"time":"...","open":...,"high":...,"low":...,"close":...,"volume":...,"amount":...}],
      "prev_close": 10.18,
      "meta":  {"source":"baostock","frequency":"5min","trading_date":"2026-07-23","bars_count":48}
    }

频率与数据源映射
================
项目内 data.py 的多源适配器决定了各源能给的频率：

    1min  ← mootdx / eastmoney / westock  （1 分钟线）
    5min  ← baostock                       （BaoStock 最细 5 分钟）
    auto  ← eastmoney→mootdx→westock→baostock 回退，优先 1min

因此 --freq 用来约束数据源选择：
    --freq 1min --source auto  → 实际用 mootdx（或 eastmoney）
    --freq 5min --source auto  → 实际用 baostock
    --freq auto                → 不约束，完全按 --source 走

用法示例
========
    # 下载 3 只股票 2026-07-01 ~ 2026-07-22 的 5 分钟线
    python scripts/download_minute_data.py --codes 600000,600026,600519 \
        --start 2026-07-01 --end 2026-07-22 --freq 5min \
        --out-dir data/minute_local

    # 1 分钟线（mootdx 优先）
    python scripts/download_minute_data.py --codes 600000 \
        --start 2026-07-21 --end 2026-07-22 --freq 1min

    # 从候选池下载
    python scripts/download_minute_data.py --pool \
        --start 2026-07-15 --end 2026-07-22 --freq 5min

    # 从文本文件读代码列表（每行一个代码）
    python scripts/download_minute_data.py --codes @codes.txt \
        --start 2026-07-01 --end 2026-07-22

    # 强制重新下载（默认会跳过已存在的文件）
    python scripts/download_minute_data.py --codes 600000 --start ... --end ... --overwrite
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 让 `from at0.data import ...` 在直接运行脚本时生效
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# ── at0.paths 加载（带 importlib 兜底）────────────────────────────
# 数据根目录由 src/at0/paths.py 集中管理，支持环境变量 AT0_DATA_DIR 覆盖。
def _load_at0_paths():
    try:
        from at0.paths import DATA_ROOT, MINUTE_LOCAL_DIR
        return DATA_ROOT, MINUTE_LOCAL_DIR
    except (ValueError, ImportError):
        paths_path = PROJECT_ROOT / "src" / "at0" / "paths.py"
        spec = importlib.util.spec_from_file_location("_at0_paths", str(paths_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.DATA_ROOT, mod.MINUTE_LOCAL_DIR

DATA_ROOT, DEFAULT_OUT_DIR = _load_at0_paths()

# Trae 沙箱会加密部分 .py 文件（at0/__init__.py / backtest.py 等含 %TSD-Header%）。
# 直接 `from at0.data import ...` 在裸 python 终端会因 __init__.py 加密失败。
# data.py 本身是明文，故先尝试常规 import；失败则用 importlib 直接按文件路径加载。
def _load_at0_data():
    try:
        from at0.data import fetch_minute_bars, normalize_code
        return fetch_minute_bars, normalize_code
    except (ValueError, ImportError):
        data_path = PROJECT_ROOT / "src" / "at0" / "data.py"
        spec = importlib.util.spec_from_file_location("_at0_data", str(data_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.fetch_minute_bars, mod.normalize_code

fetch_minute_bars, normalize_code = _load_at0_data()  # noqa: E402

# ── 频率 → 数据源映射 ──────────────────────────────────────────
# 1min 源：mootdx / eastmoney / westock
# 5min 源：baostock
FREQ_1MIN_SOURCES = ("mootdx", "eastmoney", "westock")
FREQ_5MIN_SOURCES = ("baostock",)

POOL_PATH = PROJECT_ROOT / "outputs" / "backtest" / "candidate_pool.json"


# ═══════════════════════════════════════════════════════════════
# 代码列表解析
# ═══════════════════════════════════════════════════════════════
def parse_codes(args) -> list[str]:
    """
    按优先级解析代码列表：--codes > --pool。
    --codes 支持 "600000,600026" 或 "@path/to/file.txt"。
    """
    if args.codes:
        s = args.codes.strip()
        if s.startswith("@"):
            # 从文件读，每行一个代码（# 开头视为注释）
            file_path = Path(s[1:])
            if not file_path.is_absolute():
                file_path = PROJECT_ROOT / file_path
            if not file_path.exists():
                raise FileNotFoundError(f"代码列表文件不存在: {file_path}")
            codes = []
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    c = line.strip()
                    if c and not c.startswith("#"):
                        codes.append(c)
            return codes
        # 逗号分隔
        return [c.strip() for c in s.split(",") if c.strip()]

    if args.pool:
        if not POOL_PATH.exists():
            raise FileNotFoundError(
                f"候选池不存在: {POOL_PATH}，请先跑 gen_candidate_pool.py 或用 --codes 指定"
            )
        with open(POOL_PATH, "r", encoding="utf-8") as f:
            pool = json.load(f)
        return [c["code"] for c in pool.get("candidates", [])]

    raise ValueError("必须指定 --codes 或 --pool 之一")


def resolve_sources(freq: str, source: str) -> list[str]:
    """
    根据 --freq 与 --source 解析数据源列表（按优先级，前者失败自动回退后者）。

    - --source 非 auto：单源列表（并校验与 --freq 兼容）
    - --source auto + --freq 1min：[eastmoney, mootdx, westock]
    - --source auto + --freq 5min：[baostock]
    - --source auto + --freq auto：['auto']（让 data.py 按其内部回退链处理，
      含 baostock，可能跨频率）
    """
    if freq == "auto" and source == "auto":
        return ["auto"]

    if freq == "1min":
        allowed = FREQ_1MIN_SOURCES
    elif freq == "5min":
        allowed = FREQ_5MIN_SOURCES
    else:
        raise ValueError(f"未知 --freq: {freq}（应为 1min/5min/auto）")

    if source == "auto":
        # auto + freq 限定：返回该频率下的全部源（依次回退）
        return list(allowed)

    if source not in allowed:
        raise ValueError(
            f"--freq {freq} 与 --source {source} 不兼容："
            f"{freq} 仅支持 {allowed}，baostock 只能出 5min，mootdx/eastmoney/westock 只能出 1min"
        )
    return [source]


def probe_available_sources(sources: list[str]) -> list[str]:
    """
    启动时探测可用数据源，过滤掉依赖缺失的源（避免每日重复打印初始化失败日志）。

    探测策略：检查各源的第三方依赖是否可 import（不发网络请求）。
      - mootdx  → import mootdx.quotes
      - baostock → import baostock
      - eastmoney/westock → import requests
      - 'auto'  → 原样保留（由 data.py 内部回退）
    """
    if not sources:
        return sources

    # 'auto' 不探测，原样返回
    if sources == ["auto"]:
        return sources

    dep_map = {
        "mootdx":   "mootdx.quotes",
        "baostock": "baostock",
        "eastmoney": "requests",
        "westock":  "requests",
    }
    available: list[str] = []
    skipped: list[str] = []
    for src in sources:
        mod_name = dep_map.get(src)
        if mod_name is None:
            available.append(src)
            continue
        try:
            __import__(mod_name)
            available.append(src)
        except ImportError:
            skipped.append(f"{src}（缺 {mod_name}）")

    if skipped:
        print(f"[probe] 跳过不可用源: {', '.join(skipped)}")
    return available


# 运行期被禁用的源（probe 后若结果为空，会回退到 'auto'）
_probed_sources: list[str] | None = None


# ═══════════════════════════════════════════════════════════════
# 单日下载 + 落盘
# ═══════════════════════════════════════════════════════════════
def download_one_day(
    code: str,
    trading_date: str,
    sources: list[str],
    out_dir: Path,
    overwrite: bool,
) -> tuple[str, int, str]:
    """
    下载一只股票一天的数据并落盘。按 sources 列表依次尝试，首个成功即用。

    返回: (status, bars_count, message)
      status ∈ {"ok", "skip", "empty", "error"}
    """
    nc = normalize_code(code)
    pure_code = nc["pure"]
    code_dir = out_dir / pure_code
    code_dir.mkdir(parents=True, exist_ok=True)
    out_path = code_dir / f"{trading_date}.json"

    if out_path.exists() and not overwrite:
        # 已存在则跳过（读已有文件的 bars_count 用于统计）
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            n = len(existing.get("bars", []))
            return ("skip", n, f"已存在({n} bars)")
        except (json.JSONDecodeError, OSError):
            # 文件损坏，重新下载
            pass

    last_err = "无可用源"
    for src in sources:
        try:
            bars, prev_close, meta = fetch_minute_bars(code, trading_date, src)
        except Exception as e:
            last_err = f"{src}: {e}"
            continue

        if not bars or prev_close <= 0:
            # 当前源无数据，记录错误并尝试下一个源
            errs = meta.get("errors") if meta else None
            last_err = f"{src}: {errs or '无数据'}"
            continue

        # 成功：落盘
        payload = {
            "code": pure_code,
            "date": trading_date,
            "bars": bars,
            "prev_close": prev_close,
            "meta": meta,
        }
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except OSError as e:
            return ("error", len(bars), f"写入失败: {e}")

        return ("ok", len(bars), f"{meta.get('source')}/{meta.get('frequency')}")

    # 所有源都失败
    return ("empty", 0, last_err)


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def enumerate_dates(start: str, end: str) -> list[str]:
    """枚举 [start, end] 区间内的所有自然日（含端点）。"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    if e < s:
        raise ValueError(f"--end ({end}) 早于 --start ({start})")
    days: list[str] = []
    cur = s
    while cur <= e:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量下载分钟级K线到本地（供 backtest_zz500.py --data-dir 离线回测）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--codes", default=None,
                        help="股票代码，逗号分隔；或 @file.txt 从文件读取（每行一个）")
    parser.add_argument("--pool", action="store_true",
                        help=f"从候选池读取：{POOL_PATH.relative_to(PROJECT_ROOT)}")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--freq", default="auto",
                        choices=["1min", "5min", "auto"],
                        help="频率约束：1min→mootdx/eastmoney, 5min→baostock, auto→按 --source")
    parser.add_argument("--source", default="auto",
                        choices=["auto", "mootdx", "westock", "baostock", "eastmoney"],
                        help="数据源（与 --freq 联动校验）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                        help=f"输出目录，默认 {DEFAULT_OUT_DIR.relative_to(PROJECT_ROOT)}")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已存在的文件（默认跳过）")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="每次请求间延迟秒数（防限流），默认 0.3")
    args = parser.parse_args()

    # 解析代码列表
    try:
        codes = parse_codes(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    if not codes:
        print("[ERROR] 代码列表为空", file=sys.stderr)
        return 2

    # 解析数据源列表
    try:
        sources = resolve_sources(args.freq, args.source)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    # 探测可用源（过滤掉依赖缺失的，如未安装 mootdx）
    sources = probe_available_sources(sources)
    if not sources:
        print("[ERROR] 没有可用的数据源。请安装依赖：", file=sys.stderr)
        print("        pip install mootdx   (1min 源)", file=sys.stderr)
        print("        pip install baostock (5min 源)", file=sys.stderr)
        print("        pip install requests  (eastmoney/westock 1min 源)", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = DATA_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    dates = enumerate_dates(args.start, args.end)

    print("=" * 70)
    print(f"批量下载分钟数据")
    print(f"  股票数:    {len(codes)} 只")
    print(f"  日期范围:  {args.start} ~ {args.end}（{len(dates)} 个自然日）")
    print(f"  频率约束:  {args.freq}    数据源回退链: {' → '.join(sources)}")
    print(f"  输出目录:  {out_dir}")
    print(f"  覆盖模式:  {'是' if args.overwrite else '否（跳过已存在）'}")
    print(f"  请求延迟:  {args.delay}s")
    print("=" * 70)

    # 统计
    total = len(codes) * len(dates)
    done_ok = done_skip = done_empty = done_err = 0
    t0 = time.time()

    for ci, code in enumerate(codes, 1):
        try:
            pure = normalize_code(code)["pure"]
        except ValueError as e:
            print(f"[{ci}/{len(codes)}] {code}: 代码格式错误 ({e})")
            done_err += len(dates)
            continue

        code_ok = code_skip = code_empty = code_err = 0
        for di, d in enumerate(dates, 1):
            status, n, msg = download_one_day(
                code, d, sources, out_dir, args.overwrite,
            )
            if status == "ok":
                code_ok += 1
                done_ok += 1
                tag = f"✓ {n} bars ({msg})"
            elif status == "skip":
                code_skip += 1
                done_skip += 1
                tag = f"· skip ({msg})"
            elif status == "empty":
                code_empty += 1
                done_empty += 1
                tag = f"- 无数据"
            else:
                code_err += 1
                done_err += 1
                tag = f"✗ {msg}"

            # 仅打印有变化或最后一天，避免刷屏
            if di == len(dates) or status == "error":
                progress = (ci - 1) * len(dates) + di
                elapsed = time.time() - t0
                print(f"[{ci}/{len(codes)}] {pure} {d}  {tag}  "
                      f"({progress}/{total}, {elapsed:.0f}s)")

            if args.delay > 0 and status != "skip":
                time.sleep(args.delay)

        # 单股小结
        print(f"  └ {pure}: ok={code_ok} skip={code_skip} empty={code_empty} err={code_err}")

    # 总览
    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("下载完成")
    print(f"  成功:        {done_ok}")
    print(f"  跳过(已存在): {done_skip}")
    print(f"  无数据(休市): {done_empty}")
    print(f"  错误:        {done_err}")
    print(f"  总计:        {total}")
    print(f"  耗时:        {elapsed:.1f}s")
    print(f"  输出目录:    {out_dir}")
    print("=" * 70)
    return 0 if done_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
