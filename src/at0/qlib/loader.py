"""
zz500_5min JSON → Qlib MultiIndex DataFrame 加载器
=================================================

不替换 AT0 数据层，仅做格式转换：
  zz500_5min/{code}.json (daily_bars: {date: [bars]})
    ↓
  Qlib MultiIndex DataFrame (instrument, datetime) × [$open,$close,$high,$low,$volume,$amount]

复用 at0.paths.ZZ500_5MIN_DIR，不新建数据源。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from ..paths import ZZ500_5MIN_DIR


def _normalize_code_to_instrument(code: str) -> str:
    """6位代码 → Qlib instrument 格式。

    Qlib A股惯例：sh600000 / sz000009 / sz300001 / sh688001
    """
    pure = code.strip()
    if len(pure) != 6:
        return pure
    if pure[0] == "6":
        return f"sh{pure}"
    if pure[0] in ("0", "3"):
        return f"sz{pure}"
    return pure


def load_single_stock_to_dataframe(
    code: str,
    start_date: str = "2023-01-01",
    end_date: str = "2026-12-31",
    data_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """加载单只股票 5min bar 为 Qlib DataFrame（单 instrument）。

    返回：MultiIndex(datetime, instrument) × [open,high,low,close,volume,amount]
    （Qlib 字段惯例用不带 $ 的纯名称，Expression Engine 会自动加 $）
    """
    d_dir = data_dir or ZZ500_5MIN_DIR
    pure = code.strip()
    if len(pure) == 8 and pure[:2] in ("sh", "sz"):
        pure = pure[2:]
    f = d_dir / f"{pure}.json"
    if not f.exists():
        raise FileNotFoundError(f"数据文件不存在: {f}")

    with open(f, "r", encoding="utf-8") as fp:
        d = json.load(fp)

    raw_daily_bars: dict[str, list[dict]] = d.get("daily_bars", {})
    instrument = _normalize_code_to_instrument(pure)

    records: list[dict] = []
    for d_str in sorted(raw_daily_bars.keys()):
        if not (start_date <= d_str <= end_date):
            continue
        for bar in raw_daily_bars[d_str]:
            records.append({
                "instrument": instrument,
                "datetime": pd.Timestamp(bar["time"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
                "amount": float(bar.get("amount", 0.0)),
            })

    if not records:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "amount"])

    df = pd.DataFrame.from_records(records)
    df = df.set_index(["datetime", "instrument"]).sort_index()
    return df


def load_multi_stock_to_dataframe(
    codes: list[str],
    start_date: str = "2023-01-01",
    end_date: str = "2026-12-31",
    data_dir: Optional[Path] = None,
    verbose: bool = False,
) -> pd.DataFrame:
    """加载多只股票，concat 为统一 MultiIndex DataFrame。

    返回：MultiIndex(instrument, datetime) × [open,high,low,close,volume,amount]
    （Qlib 标准 MultiIndex 顺序：instrument 在外，datetime 在内）
    """
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for i, code in enumerate(codes):
        try:
            df = load_single_stock_to_dataframe(code, start_date, end_date, data_dir)
            if df.empty:
                failed.append(code)
                continue
            frames.append(df)
            if verbose and (i + 1) % 10 == 0:
                print(f"  [loader] 已加载 {i+1}/{len(codes)} 只")
        except (FileNotFoundError, json.JSONDecodeError, OSError) as ex:
            failed.append(code)
            if verbose:
                print(f"  [loader] 跳过 {code}: {ex}")

    if not frames:
        return pd.DataFrame()

    # 各股 df 是 (datetime, instrument) index，concat 后 reset+重排为 (instrument, datetime)
    big = pd.concat(frames, axis=0)
    # 重排为 Qlib 惯例：instrument 在外
    big = big.swaplevel(0, 1, axis=0).sort_index()
    if verbose and failed:
        print(f"  [loader] 失败 {len(failed)} 只: {failed[:5]}{'...' if len(failed)>5 else ''}")
    return big


def list_available_codes(data_dir: Optional[Path] = None) -> list[str]:
    """列出 zz500_5min 目录下所有可用股票代码（6位纯代码）。"""
    d_dir = data_dir or ZZ500_5MIN_DIR
    return sorted(
        f.stem for f in d_dir.glob("*.json") if f.stem.isdigit()
    )


__all__ = [
    "load_single_stock_to_dataframe",
    "load_multi_stock_to_dataframe",
    "list_available_codes",
    "_normalize_code_to_instrument",
]
