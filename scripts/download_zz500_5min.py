#!/usr/bin/env python3
"""
下载中证500全部成分股近3年5分钟K线数据（baostock 源）。

存储格式
========
每只股票一个 JSON 文件（符合用户要求）：

    {out_dir}/{pure_code}.json

文件内容：
    {
      "code": "600004",
      "name": "白云机场",
      "baostock_code": "sh.600004",
      "start_date": "2023-07-25",
      "end_date": "2026-07-22",
      "meta": {"source": "baostock", "frequency": "5min", "total_bars": 34800, "trading_days": 725},
      "daily_bars": {
        "2023-07-25": [
          {"time":"2023-07-25 09:35:00","open":...,"high":...,"low":...,"close":...,"volume":...,"amount":...},
          ...
        ],
        "2023-07-26": [...],
        ...
      },
      "daily_prev_closes": {"2023-07-25": 7.95, "2023-07-26": 7.88, ...}
    }

防封策略（关键）
================
baostock 是免费公开接口，但高频请求会被临时封 IP。实测安全策略：

1. **按月分批请求**：3年 = 36个月，每股每月一次请求（约2s），而非每股一次3年请求（154s易超时）
2. **请求间延迟**：每股每月请求后 sleep 0.5s（可调），避免连续高频
3. **每完成一只股票暂停**：每股36月请求完后 sleep 2s，给服务器喘息
4. **断线自动重连**：检测到 baostock error_code != "0" 时，logout + login 后重试该月
5. **失败重试上限**：单月最多重试3次，仍失败则记录跳过，不阻塞整体
6. **断点续传**：已完成的股票文件跳过（除非 --overwrite）

用法示例
========
    # 默认下载近3年中证500全部成分股
    python scripts/download_zz500_5min.py

    # 指定起止日期
    python scripts/download_zz500_5min.py --start 2023-07-25 --end 2026-07-22

    # 自定义输出目录和延迟
    python scripts/download_zz500_5min.py --out-dir data/zz500_5min --delay 0.8

    # 覆盖已下载文件
    python scripts/download_zz500_5min.py --overwrite

    # 只下载前 N 只（测试用）
    python scripts/download_zz500_5min.py --limit 5

    # 指定成分股日期（默认用 end_date 当天）
    python scripts/download_zz500_5min.py --constituent-date 2026-06-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 直接 import baostock（不走 at0.data 的加密 __init__.py）
import baostock as bs  # noqa: E402

# 复用 at0.data 的代码归一化（data.py 是明文，用 importlib 兜底）
def _load_normalize_code():
    try:
        from at0.data import normalize_code
        return normalize_code
    except (ValueError, ImportError):
        import importlib.util
        data_path = PROJECT_ROOT / "src" / "at0" / "data.py"
        spec = importlib.util.spec_from_file_location("_at0_data", str(data_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.normalize_code

normalize_code = _load_normalize_code()  # noqa: E402

DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "zz500_5min"
DEFAULT_START = "2023-07-25"  # 近3年
DEFAULT_END = "2026-07-22"


# ═══════════════════════════════════════════════════════════════
# baostock 登录管理（带重连）
# ═══════════════════════════════════════════════════════════════
_bs_logged_in = False


def bs_login() -> bool:
    """登录 baostock（进程级单例）。"""
    global _bs_logged_in
    if _bs_logged_in:
        return True
    lg = bs.login()
    if lg.error_code != "0":
        print(f"[bs] 登录失败: {lg.error_msg}", file=sys.stderr)
        return False
    _bs_logged_in = True
    return True


def bs_reconnect() -> bool:
    """断线重连：先 logout 再 login。"""
    global _bs_logged_in
    try:
        bs.logout()
    except Exception:
        pass
    _bs_logged_in = False
    time.sleep(2)  # 重连前等2秒
    return bs_login()


# ═══════════════════════════════════════════════════════════════
# 中证500成分股获取
# ═══════════════════════════════════════════════════════════════
def fetch_zz500_constituents(date: str) -> list[dict]:
    """
    获取中证500成分股列表。

    :param date: YYYY-MM-DD，取该日期的最新成分股
    :return: [{"code": "sh.600004", "name": "白云机场", "pure": "600004"}, ...]
    """
    if not bs_login():
        return []
    rs = bs.query_zz500_stocks(date=date)
    if rs.error_code != "0":
        print(f"[bs] query_zz500_stocks 失败: {rs.error_msg}", file=sys.stderr)
        return []

    stocks = []
    while rs.next():
        row = rs.get_row_data()  # [updateDate, code, code_name]
        bs_code = row[1]
        name = row[2]
        pure = bs_code.split(".")[-1]
        stocks.append({"code": bs_code, "name": name, "pure": pure})
    return stocks


# ═══════════════════════════════════════════════════════════════
# 按月枚举
# ═══════════════════════════════════════════════════════════════
def enumerate_months(start: str, end: str) -> list[tuple[str, str]]:
    """
    枚举 [start, end] 区间内的每个月，返回 [(month_start, month_end), ...]。
    month_start 为该月第一天（或 start），month_end 为该月最后一天（或 end）。
    """
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    if e < s:
        raise ValueError(f"--end ({end}) 早于 --start ({start})")

    months = []
    cur = datetime(s.year, s.month, 1)
    while cur <= e:
        # 该月最后一天
        if cur.month == 12:
            next_month = datetime(cur.year + 1, 1, 1)
        else:
            next_month = datetime(cur.year, cur.month + 1, 1)
        month_end = next_month - timedelta(days=1)

        # 限定到 [start, end]
        ms = max(cur, s)
        me = min(month_end, e)
        months.append((ms.strftime("%Y-%m-%d"), me.strftime("%Y-%m-%d")))
        cur = next_month
    return months


# ═══════════════════════════════════════════════════════════════
# 单月5min数据拉取（带重试）
# ═══════════════════════════════════════════════════════════════
def parse_bs_time(t: str) -> str:
    """'20260715093500000' -> '2026-07-15 09:35:00'。"""
    if not t or len(t) < 14:
        return t
    return f"{t[0:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:14]}"


def fetch_month_5min(
    bs_code: str,
    month_start: str,
    month_end: str,
    max_retries: int = 3,
) -> tuple[list[dict], str]:
    """
    拉取一只股票一个月的 5min K线。

    :return: (bars, error_msg)  bars 为空且 error_msg 非空时表示失败
    """
    fields = "date,time,open,high,low,close,volume,amount"
    last_err = ""
    for attempt in range(max_retries):
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, fields,
                start_date=month_start, end_date=month_end,
                frequency="5", adjustflag="2",  # 前复权
            )
            if rs.error_code != "0":
                last_err = f"bs error: {rs.error_msg}"
                # 重连
                if not bs_reconnect():
                    continue
                continue

            bars = []
            bs_fields = rs.fields
            while rs.next():
                row = dict(zip(bs_fields, rs.get_row_data()))
                try:
                    close = float(row.get("close", 0))
                    if close <= 0:
                        continue
                    bars.append({
                        "date": row["date"],
                        "time": parse_bs_time(row.get("time", "")),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": close,
                        "volume": int(float(row["volume"])),
                        "amount": float(row["amount"]),
                    })
                except (ValueError, KeyError):
                    continue
            return bars, ""  # 成功
        except Exception as e:
            last_err = f"exception: {e}"
            if not bs_reconnect():
                continue
            time.sleep(1)

    return [], last_err


def fetch_prev_closes(
    bs_code: str,
    start_date: str,
    end_date: str,
    max_retries: int = 3,
) -> dict[str, float]:
    """
    拉取日期区间内每天的昨收（日线 preclose 字段）。
    一次请求拿整个区间，避免逐日请求。
    """
    last_err = ""
    for attempt in range(max_retries):
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, "date,close,preclose",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2",
            )
            if rs.error_code != "0":
                last_err = rs.error_msg
                if not bs_reconnect():
                    continue
                continue

            prev_closes = {}
            while rs.next():
                row = rs.get_row_data()  # [date, close, preclose]
                try:
                    pc = float(row[2])
                    if pc > 0:
                        prev_closes[row[0]] = pc
                except (IndexError, ValueError):
                    continue
            return prev_closes
        except Exception as e:
            last_err = str(e)
            if not bs_reconnect():
                continue
            time.sleep(1)
    print(f"  [warn] {bs_code} prev_close 获取失败: {last_err}", file=sys.stderr)
    return {}


# ═══════════════════════════════════════════════════════════════
# 单股下载（按月分批 + 落盘）
# ═══════════════════════════════════════════════════════════════
def download_one_stock(
    stock: dict,
    months: list[tuple[str, str]],
    start_date: str,
    end_date: str,
    out_dir: Path,
    overwrite: bool,
    delay: float,
) -> tuple[str, int, int, str]:
    """
    下载一只股票全部月份的 5min 数据并合并落盘。

    :return: (status, total_bars, trading_days, message)
      status ∈ {"ok", "skip", "partial", "error"}
    """
    pure = stock["pure"]
    out_path = out_dir / f"{pure}.json"

    if out_path.exists() and not overwrite:
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            n = existing.get("meta", {}).get("total_bars", 0)
            return ("skip", n, len(existing.get("daily_bars", {})), "已存在")
        except (json.JSONDecodeError, OSError):
            pass  # 文件损坏，重新下载

    bs_code = stock["code"]
    daily_bars: dict[str, list[dict]] = {}
    failed_months: list[str] = []
    total_bars = 0

    for mi, (ms, me) in enumerate(months, 1):
        bars, err = fetch_month_5min(bs_code, ms, me)
        if err:
            failed_months.append(f"{ms}~{me}: {err}")
            continue

        # 按日期分组
        for b in bars:
            d = b.pop("date")
            daily_bars.setdefault(d, []).append(b)
            total_bars += 1

        if delay > 0:
            time.sleep(delay)

    if not daily_bars:
        return ("error", 0, 0, f"全部月份失败: {failed_months[:2]}")

    # 排序每天 bars
    for d in daily_bars:
        daily_bars[d].sort(key=lambda b: b["time"])

    # 拉昨收（一次请求整个区间）
    prev_closes = fetch_prev_closes(bs_code, start_date, end_date)
    if delay > 0:
        time.sleep(delay)

    trading_days = len(daily_bars)
    payload = {
        "code": pure,
        "name": stock["name"],
        "baostock_code": bs_code,
        "start_date": start_date,
        "end_date": end_date,
        "meta": {
            "source": "baostock",
            "frequency": "5min",
            "total_bars": total_bars,
            "trading_days": trading_days,
            "failed_months": failed_months,
        },
        "daily_bars": dict(sorted(daily_bars.items())),
        "daily_prev_closes": prev_closes,
    }

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError as e:
        return ("error", total_bars, trading_days, f"写入失败: {e}")

    if failed_months:
        return ("partial", total_bars, trading_days,
                f"{len(failed_months)}月失败: {failed_months[0][:50]}")
    return ("ok", total_bars, trading_days, f"{trading_days}天/{total_bars}bars")


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser(
        description="下载中证500全部成分股近3年5min K线（baostock，每只股票一个文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--start", default=DEFAULT_START, help=f"起始日期，默认 {DEFAULT_START}")
    parser.add_argument("--end", default=DEFAULT_END, help=f"结束日期，默认 {DEFAULT_END}")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                        help=f"输出目录，默认 {DEFAULT_OUT_DIR.relative_to(PROJECT_ROOT)}")
    parser.add_argument("--constituent-date", default=None,
                        help="成分股快照日期，默认取 --end 当天")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="每次月请求后延迟秒数（防封），默认 0.5")
    parser.add_argument("--stock-delay", type=float, default=2.0,
                        help="每只股票完成后延迟秒数（防封），默认 2.0")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")
    parser.add_argument("--limit", type=int, default=None,
                        help="只下载前 N 只（测试用）")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 成分股日期
    const_date = args.constituent_date or args.end

    # 登录
    if not bs_login():
        return 2

    # 获取成分股
    print(f"[INFO] 获取中证500成分股（快照日期 {const_date}）...")
    stocks = fetch_zz500_constituents(const_date)
    if not stocks:
        print("[ERROR] 获取成分股失败", file=sys.stderr)
        return 2
    if args.limit:
        stocks = stocks[:args.limit]
    print(f"[INFO] 共 {len(stocks)} 只成分股")

    # 按月分批
    months = enumerate_months(args.start, args.end)
    print(f"[INFO] 日期范围 {args.start} ~ {args.end}，分 {len(months)} 个月批次")
    print(f"[INFO] 防封策略: 月请求延迟={args.delay}s, 股票间延迟={args.stock_delay}s")
    print(f"[INFO] 输出目录: {out_dir}")
    print("=" * 70)

    # 统计
    total = len(stocks)
    done_ok = done_skip = done_partial = done_err = 0
    total_bars_all = 0
    t0 = time.time()

    for i, stock in enumerate(stocks, 1):
        elapsed = time.time() - t0
        if i > 1:
            eta = elapsed / (i - 1) * (total - i + 1)
        else:
            eta = 0

        print(f"[{i}/{total}] {stock['pure']} {stock['name']}  "
              f"(已用 {elapsed:.0f}s, 预估剩余 {eta:.0f}s)")

        status, bars, days, msg = download_one_stock(
            stock, months, args.start, args.end, out_dir,
            args.overwrite, args.delay,
        )

        if status == "ok":
            done_ok += 1
            total_bars_all += bars
            print(f"  ✓ {days}天/{bars}bars")
        elif status == "skip":
            done_skip += 1
            total_bars_all += bars
            print(f"  · skip ({msg})")
        elif status == "partial":
            done_partial += 1
            total_bars_all += bars
            print(f"  △ partial: {msg}")
        else:
            done_err += 1
            print(f"  ✗ error: {msg}")

        # 每只股票完成后暂停（防封）
        if args.stock_delay > 0 and i < total:
            time.sleep(args.stock_delay)

    bs.logout()

    # 总览
    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("下载完成")
    print(f"  成功:     {done_ok}")
    print(f"  跳过:     {done_skip}")
    print(f"  部分成功: {done_partial}")
    print(f"  错误:     {done_err}")
    print(f"  总计:     {total}")
    print(f"  总bars:   {total_bars_all:,}")
    print(f"  耗时:     {elapsed:.0f}s ({elapsed/3600:.1f}h)")
    print(f"  输出目录: {out_dir}")
    print("=" * 70)
    return 0 if done_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
