#!/usr/bin/env python3
"""
从中证500 5min本地数据跑回测（不联网）。

读取 scripts/download_zz500_5min.py 产出的 data/zz500_5min/{code}.json
按指定股票范围 + 日期区间构建 daily_bars / daily_prev_closes / daily_meta 后
调用 at0.backtest.backtest_multi_day，复用 at0.cli 的输出格式。

数据加载策略
============
1. 直接读 {data_dir}/{code}.json 中的 daily_bars 字段（已是按日期组织的 5min K线）；
2. 按日期升序遍历，本日 prev_close = 前一交易日最后一根 bar 的 close；
   首日 prev_close 用首根 bar 的 open 近似（baostock 未单独存日K preclose）；
3. 区间过滤：仅保留 [start, end] 区间内的交易日；
4. 调用 backtest_multi_day 跑回测；
5. 输出 trades.json / report.json / report.html（路径同 cli backtest）。

用法示例
========
    # 单股回测
    python scripts/backtest_zz500.py --code 000009 \
        --start 2023-07-25 --end 2026-07-22

    # 批量回测（多只）
    python scripts/backtest_zz500.py --codes 000009,000021,000027 \
        --start 2023-07-25 --end 2026-07-22

    # 全集（500只）
    python scripts/backtest_zz500.py --all \
        --start 2023-07-25 --end 2026-07-22

    # 抽样 50 只
    python scripts/backtest_zz500.py --sample 50 --seed 42 \
        --start 2023-07-25 --end 2026-07-22
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ═══════════════════════════════════════════════════════════════
# at0.paths 加载（带 importlib 兜底）
# 数据根目录由 src/at0/paths.py 集中管理，支持环境变量 AT0_DATA_DIR 覆盖。
# ═══════════════════════════════════════════════════════════════
def _load_at0_paths():
    try:
        from at0.paths import DATA_ROOT, ZZ500_5MIN_DIR
        return DATA_ROOT, ZZ500_5MIN_DIR
    except (ValueError, ImportError):
        paths_path = PROJECT_ROOT / "src" / "at0" / "paths.py"
        spec = importlib.util.spec_from_file_location("_at0_paths", str(paths_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.DATA_ROOT, mod.ZZ500_5MIN_DIR

DATA_ROOT, DEFAULT_ZZ500_DIR = _load_at0_paths()


# ═══════════════════════════════════════════════════════════════
# at0.data 加载（带 importlib 兜底）
# ═══════════════════════════════════════════════════════════════
def _load_at0_data():
    try:
        from at0.data import normalize_code
        return normalize_code
    except (ValueError, ImportError):
        data_path = PROJECT_ROOT / "src" / "at0" / "data.py"
        spec = importlib.util.spec_from_file_location("_at0_data", str(data_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.normalize_code


normalize_code = _load_at0_data()


# ═══════════════════════════════════════════════════════════════
# 延迟导入回测依赖（at0.backtest / at0.cli / at0.strategy / at0.risk / at0.reports）
# ═══════════════════════════════════════════════════════════════
def _import_backtest_deps():
    """延迟导入回测所需的所有 at0 子模块。"""
    from at0.backtest import (
        BacktestParams,
        backtest_multi_day,
        print_backtest_summary,
        compute_data_fingerprint,
        generate_run_id,
        save_run_artifacts,
        extract_trades,
        summarize_one_stock,
        aggregate_batch,
    )
    from at0.strategy import SignalParams
    from at0.risk import RiskParams
    from at0.reports import save_html_report, save_batch_html_report
    from at0.cli import (
        adapt_params_by_frequency,
        save_trades_json,
        save_report_json,
        BACKTEST_OUTPUT_DIR,
    )
    # P0 配置整改：统一从 thresholds.yaml 加载默认参数（消除 dataclass 默认值 drift）
    from at0.config import (
        load_signal_params,
        load_risk_params,
        load_backtest_params,
    )
    from dataclasses import asdict
    return {
        "BacktestParams": BacktestParams,
        "backtest_multi_day": backtest_multi_day,
        "print_backtest_summary": print_backtest_summary,
        "compute_data_fingerprint": compute_data_fingerprint,
        "generate_run_id": generate_run_id,
        "save_run_artifacts": save_run_artifacts,
        "extract_trades": extract_trades,
        "summarize_one_stock": summarize_one_stock,
        "aggregate_batch": aggregate_batch,
        "SignalParams": SignalParams,
        "RiskParams": RiskParams,
        "save_html_report": save_html_report,
        "save_batch_html_report": save_batch_html_report,
        "adapt_params_by_frequency": adapt_params_by_frequency,
        "save_trades_json": save_trades_json,
        "save_report_json": save_report_json,
        "BACKTEST_OUTPUT_DIR": BACKTEST_OUTPUT_DIR,
        "asdict": asdict,
        "load_signal_params": load_signal_params,
        "load_risk_params": load_risk_params,
        "load_backtest_params": load_backtest_params,
    }


# DEFAULT_ZZ500_DIR 来自 _load_at0_paths()（见文件顶部）


# ═══════════════════════════════════════════════════════════════
# zz500 数据加载（核心适配器）
# ═══════════════════════════════════════════════════════════════
def load_multi_day_zz500(
    code: str,
    start_date: str,
    end_date: str,
    data_dir: Path,
) -> tuple[dict, dict, dict]:
    """
    从 {data_dir}/{code}.json 加载多日 5min 数据。

    返回与 at0.data.fetch_multi_day 完全一致的三元组：
        (daily_bars, daily_prev_closes, daily_meta)

    prev_close 推导：
      - 按日期升序，本日 prev_close = 前一交易日最后一根 bar 的 close
      - 首个交易日 prev_close = 当日首根 bar 的 open（近似，误差≤一个5min跳空）
    """
    nc = normalize_code(code)
    pure_code = nc["pure"]
    f = data_dir / f"{pure_code}.json"
    if not f.exists():
        print(f"[load_zz500] 文件不存在: {f}")
        return {}, {}, {}

    try:
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
    except (json.JSONDecodeError, OSError) as ex:
        print(f"[load_zz500] 跳过损坏文件 {f.name}: {ex}")
        return {}, {}, {}

    raw_daily_bars: dict[str, list[dict]] = d.get("daily_bars", {})
    file_meta = d.get("meta", {}) or {}

    # 区间过滤 + 升序
    sorted_dates = sorted(
        d for d in raw_daily_bars.keys()
        if start_date <= d <= end_date
    )

    daily_bars: dict[str, list[dict]] = {}
    daily_prev_closes: dict[str, float] = {}
    daily_meta: dict[str, dict] = {}

    prev_day_close: float | None = None
    for d in sorted_dates:
        bars = raw_daily_bars[d]
        if not bars:
            continue
        # prev_close：前一日收盘；首日退化为首根 open
        if prev_day_close is not None and prev_day_close > 0:
            pc = prev_day_close
        else:
            pc = float(bars[0].get("open", 0))
        if pc <= 0:
            prev_day_close = float(bars[-1].get("close", 0)) or None
            continue

        daily_bars[d] = bars
        daily_prev_closes[d] = pc
        daily_meta[d] = {
            "source": file_meta.get("source", "baostock"),
            "frequency": file_meta.get("frequency", "5min"),
            "trading_date": d,
            "bars_count": len(bars),
        }
        prev_day_close = float(bars[-1].get("close", 0)) or None

    print(f"[load_zz500] {pure_code} {start_date}~{end_date} "
          f"← 本地({len(daily_bars)}交易日, {f.name})")
    return daily_bars, daily_prev_closes, daily_meta


# ═══════════════════════════════════════════════════════════════
# 单股回测
# ═══════════════════════════════════════════════════════════════
def run_zz500_single(
    code: str,
    start_date: str,
    end_date: str,
    data_dir: Path,
    base_shares: int = 3000,
    avg_cost: float | None = None,
    tag: str = "zz500",
    params_override: dict | None = None,
) -> dict:
    """从 zz500 本地数据跑单股回测，返回结果 dict（同时落盘 trades/report/html）。

    params_override: 可选 dict，覆盖 BacktestParams/SignalParams/RiskParams 字段。
        格式: {"bp": {...}, "sp": {...}, "rp": {...}}
        bp 字段: stop_loss_ratio/max_holding_bars/cooldown_bars/hard_trend_filter_add/...
        sp 字段: tf_adx_threshold/tf_vol_ratio_min/...
        rp 字段: min_capture_spread/...
    """
    print(f"[run_zz500] {code} {start_date}~{end_date} data_dir={data_dir}")

    try:
        dep = _import_backtest_deps()
    except (ValueError, ImportError) as e:
        print(
            "[run_zz500] 无法导入回测模块（at0.backtest/at0.cli 等）。\n"
            "  原因: Trae 沙箱对部分 .py 文件做了 TSD 加密，裸 python 终端无法直接编译。\n"
            "  解决: 在 Trae IDE 中通过运行按钮/调试器执行本脚本（沙箱会自动解密），\n"
            f"  原始异常: {e}",
            file=sys.stderr,
        )
        return {}

    BacktestParams = dep["BacktestParams"]
    backtest_multi_day = dep["backtest_multi_day"]
    print_backtest_summary = dep["print_backtest_summary"]
    compute_data_fingerprint = dep["compute_data_fingerprint"]
    generate_run_id = dep["generate_run_id"]
    save_run_artifacts = dep["save_run_artifacts"]
    SignalParams = dep["SignalParams"]
    RiskParams = dep["RiskParams"]
    save_html_report = dep["save_html_report"]
    adapt_params_by_frequency = dep["adapt_params_by_frequency"]
    save_trades_json = dep["save_trades_json"]
    save_report_json = dep["save_report_json"]
    BACKTEST_OUTPUT_DIR = dep["BACKTEST_OUTPUT_DIR"]
    asdict = dep["asdict"]
    load_signal_params = dep["load_signal_params"]
    load_risk_params = dep["load_risk_params"]
    load_backtest_params = dep["load_backtest_params"]

    daily_bars, daily_prev_closes, daily_meta = load_multi_day_zz500(
        code, start_date, end_date, data_dir,
    )
    if not daily_bars:
        print("[run_zz500] 未找到任何本地数据，退出")
        return {}

    first_meta = next(iter(daily_meta.values()))
    frequency = first_meta.get("frequency", "5min")
    bars_per_day = first_meta.get("bars_count", 48)
    print(f"[run_zz500] 数据频率={frequency}, 约 {bars_per_day} 根/天")

    if avg_cost is None:
        first_date = min(daily_prev_closes.keys())
        avg_cost = daily_prev_closes[first_date]
        print(f"[run_zz500] avg_cost 未指定，取首日 prev_close={avg_cost:.4f}")

    # P0 配置整改：默认从 thresholds.yaml 加载参数（消除 dataclass 默认值 drift）。
    # params_override 仅用于显式实验覆盖，叠加在 yaml 默认值之上。
    from dataclasses import replace as _replace
    sp_kwargs = {}
    rp_kwargs = {}
    bp_kwargs = {}
    if params_override:
        sp_kwargs = dict(params_override.get("sp", {}))
        rp_kwargs = dict(params_override.get("rp", {}))
        bp_kwargs = dict(params_override.get("bp", {}))

    sp = _replace(load_signal_params(), **sp_kwargs) if sp_kwargs else load_signal_params()
    rp = _replace(load_risk_params(), **rp_kwargs) if rp_kwargs else load_risk_params()
    params = _replace(
        load_backtest_params(),
        base_shares=base_shares,
        avg_cost=avg_cost,
        signal_params=sp,
        risk_params=rp,
        **bp_kwargs,
    )
    # 先按频率适配 warmup/eod（这两个不参与 override）
    params = adapt_params_by_frequency(params, frequency, bars_per_day)
    # 适配后再应用 bp override（避免被 adapt 覆盖 max_holding_bars 等）
    if bp_kwargs:
        params = _replace(params, **bp_kwargs)
        # 同步 exposure_policy 的 max_holding_bars（approve_signal 用它判 expired）
        if params.exposure_policy is not None and "max_holding_bars" in bp_kwargs:
            params.exposure_policy.max_holding_bars = bp_kwargs["max_holding_bars"]
    print(f"[run_zz500] warmup_bars={params.warmup_bars}, "
          f"eod_check_bar_idx={params.eod_check_bar_idx}, "
          f"stop_loss={params.stop_loss_ratio}, "
          f"max_hold={params.max_holding_bars}, "
          f"cooldown={params.cooldown_bars}, "
          f"hard_trend_add={params.hard_trend_filter_add}, "
          f"tf_adx={params.signal_params.tf_adx_threshold}, "
          f"min_cap={params.risk_params.min_capture_spread}")

    result = backtest_multi_day(
        code=normalize_code(code)["pure"],
        daily_bars=daily_bars,
        daily_prev_closes=daily_prev_closes,
        params=params,
    )

    code_tag = normalize_code(code)["pure"]
    suffix = f"{tag}_{start_date}_{end_date}"
    trades_json = BACKTEST_OUTPUT_DIR / f"{code_tag}_{suffix}_trades.json"
    report_json = BACKTEST_OUTPUT_DIR / f"{code_tag}_{suffix}_report.json"
    report_html = BACKTEST_OUTPUT_DIR / f"{code_tag}_{suffix}_report.html"
    n = save_trades_json(result, daily_meta, trades_json)
    save_report_json(result, daily_meta, report_json)
    save_html_report(result, daily_bars, report_html,
                     code=code_tag, start_date=start_date, end_date=end_date)

    print(f"\n[run_zz500] 买卖触发记录 -> {trades_json}  ({n} 笔)")
    print(f"[run_zz500] 完整报告     -> {report_json}")
    print(f"[run_zz500] HTML 可视化  -> {report_html}")
    print_backtest_summary(result)

    data_fp = compute_data_fingerprint(
        code=normalize_code(code)["pure"],
        start_date=start_date,
        end_date=end_date,
        daily_meta=daily_meta,
        frequency=frequency,
    )
    params_dict = asdict(params)
    run_id = generate_run_id(params_dict, data_fp)
    run_dir = save_run_artifacts(
        run_id=run_id,
        params_dict=params_dict,
        data_fingerprint=data_fp,
        output_files={
            "trades": trades_json,
            "report": report_json,
            "html": report_html,
        },
        run_type=f"single_{tag}",
    )
    print(f"[run_zz500] run_id       -> {run_id}")
    print(f"[run_zz500] artifacts    -> {run_dir}")
    return result


# ═══════════════════════════════════════════════════════════════
# 批量回测
# ═══════════════════════════════════════════════════════════════
def run_zz500_batch(
    codes: list[str],
    start_date: str,
    end_date: str,
    data_dir: Path,
    base_shares: int,
    tag: str = "zz500",
    params_override: dict | None = None,
) -> dict:
    """多股票批量回测 + 汇总（沿用 batch_main 的输出约定）。返回汇总 dict。"""
    try:
        dep = _import_backtest_deps()
    except (ValueError, ImportError) as e:
        print(
            "[batch_zz500] 无法导入回测模块（at0.backtest/at0.cli 等）。\n"
            f"  原始异常: {e}",
            file=sys.stderr,
        )
        return {}

    extract_trades = dep["extract_trades"]
    summarize_one_stock = dep["summarize_one_stock"]
    aggregate_batch = dep["aggregate_batch"]
    save_batch_html_report = dep["save_batch_html_report"]
    BACKTEST_OUTPUT_DIR = dep["BACKTEST_OUTPUT_DIR"]

    summary_path = BACKTEST_OUTPUT_DIR / f"batch_summary_{tag}.json"
    html_path = BACKTEST_OUTPUT_DIR / f"batch_summary_{tag}.html"

    print(f"[batch_zz500] 回测窗口 {start_date} ~ {end_date}, "
          f"data_dir={data_dir}, 股票数={len(codes)}")
    print(f"[batch_zz500] base_shares={base_shares}, tag={tag}")
    print("=" * 70)

    per_stock: list[dict] = []
    all_trades_count = 0
    t0 = time.time()

    for i, code in enumerate(codes, 1):
        prefix = f"[{i}/{len(codes)}] {code}"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = run_zz500_single(
                    code=code,
                    start_date=start_date,
                    end_date=end_date,
                    data_dir=data_dir,
                    base_shares=base_shares,
                    avg_cost=None,
                    tag=tag,
                    params_override=params_override,
                )
        except Exception as e:
            print(f"{prefix} 异常: {e}")
            per_stock.append({"code": code, "error": str(e),
                              "total_trades": 0, "paired_trades": 0,
                              "win_trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                              "unrealized_pnl": 0.0,
                              "net_pnl_with_unrealized": 0.0,
                              "final_open_legs_count": 0})
            continue

        if not result or not result.get("daily_results"):
            print(f"{prefix} 无数据")
            per_stock.append({"code": code, "error": "no_data",
                              "total_trades": 0, "paired_trades": 0,
                              "win_trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                              "unrealized_pnl": 0.0,
                              "net_pnl_with_unrealized": 0.0,
                              "final_open_legs_count": 0})
            continue

        trades = extract_trades(result)
        summary = summarize_one_stock(code, result)
        per_stock.append(summary)
        all_trades_count += len(trades)

        wr = f"{summary['win_rate']*100:.1f}%" if summary['paired_trades'] else "N/A"
        elapsed = time.time() - t0
        avg_per_stock = elapsed / i
        remaining = avg_per_stock * (len(codes) - i)
        print(f"{prefix}  交易={summary['total_trades']:3d} "
              f"配对={summary['paired_trades']:3d} "
              f"胜率={wr:>5s} "
              f"净盈亏={summary['net_pnl']:+10.2f} "
              f"浮盈={summary['unrealized_pnl']:+10.2f} "
              f"未配对={summary['final_open_legs_count']:2d} "
              f"[已用{elapsed:.0f}s, 剩余~{remaining:.0f}s]")

    # 整体汇总
    overall = aggregate_batch(per_stock, all_trades_count)
    print("\n" + "=" * 70)
    print("整体汇总")
    print("=" * 70)
    print(f"  股票数:          {overall['stocks']}")
    print(f"  总交易笔数:      {overall['total_trades']}")
    if overall['total_trades']:
        paired_pct = f" ({overall['paired_trades']/overall['total_trades']*100:.1f}%)"
        print(f"  配对笔数:        {overall['paired_trades']}{paired_pct}")
    else:
        print(f"  配对笔数: 0")
    print(f"  盈利笔数:        {overall['win_trades']}")
    print(f"  整体胜率:        {overall['win_rate']*100:.1f}%")
    print(f"  毛利润:          {overall['gross_pnl']:+.2f}")
    print(f"  总成本:          {overall['total_cost']:.2f}")
    print(f"  净盈亏(已实现):  {overall['net_pnl']:+.2f}")
    print(f"  未配对浮盈浮亏:  {overall['unrealized_pnl']:+.2f}")
    print(f"  净盈亏(含浮盈):  {overall['net_pnl_with_unrealized']:+.2f}")
    print(f"  回测结束未配对腿: {overall['final_open_legs_count']}")

    # 按股票拆分
    print("\n" + "-" * 70)
    print("按股票拆分（按含浮盈净盈亏降序，前30）")
    print("-" * 70)
    sorted_by_pnl = sorted(
        per_stock,
        key=lambda x: x.get("net_pnl_with_unrealized", x.get("net_pnl", 0)),
        reverse=True,
    )
    for s in sorted_by_pnl[:30]:
        if "error" in s:
            print(f"  {s['code']:12s}  ERROR: {s['error']}")
            continue
        wr = f"{s['win_rate']*100:.1f}%" if s['paired_trades'] else "  N/A"
        print(f"  {s['code']:12s}  交易={s['total_trades']:3d}  "
              f"配对={s['paired_trades']:3d}  胜率={wr:>5s}  "
              f"已实现={s['net_pnl']:+10.2f}  浮盈={s['unrealized_pnl']:+10.2f}  "
              f"含浮盈={s['net_pnl_with_unrealized']:+10.2f}  未配对={s['final_open_legs_count']}")

    # 落盘
    BACKTEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "tag": tag,
        "start": start_date, "end": end_date,
        "data_dir": str(data_dir), "base_shares": base_shares,
        "codes_count": len(codes),
        "overall": overall, "per_stock": per_stock,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2, default=str)
    try:
        save_batch_html_report(summary_data, html_path)
    except Exception as e:
        print(f"[batch_zz500] HTML 生成失败: {e}")
    print(f"\n[batch_zz500] 汇总报告 -> {summary_path}")
    print(f"[batch_zz500] HTML报告 -> {html_path}")
    return summary_data


# ═══════════════════════════════════════════════════════════════
# 代码列表解析
# ═══════════════════════════════════════════════════════════════
def list_all_zz500_codes(data_dir: Path) -> list[str]:
    """枚举 data_dir 下所有 {code}.json，返回代码升序列表。"""
    files = sorted(data_dir.glob("*.json"))
    codes = []
    for f in files:
        c = f.stem
        if len(c) == 6 and c.isdigit():
            codes.append(c)
    return codes


def parse_codes(args, data_dir: Path) -> list[str]:
    if args.code:
        return [args.code.strip()]
    if args.codes:
        s = args.codes.strip()
        if s.startswith("@"):
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
        return [c.strip() for c in s.split(",") if c.strip()]
    if args.all:
        return list_all_zz500_codes(data_dir)
    if args.sample:
        all_codes = list_all_zz500_codes(data_dir)
        if args.seed is not None:
            random.Random(args.seed).shuffle(all_codes)
        return all_codes[:args.sample]
    raise ValueError("必须指定 --code / --codes / --all / --sample 之一")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 zz500 5min 本地数据跑回测（不联网）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--code", default=None, help="单只股票代码")
    g.add_argument("--codes", default=None,
                   help="多只股票代码，逗号分隔；或 @file.txt 从文件读取")
    g.add_argument("--all", action="store_true", help="枚举 data-dir 下全部股票")
    g.add_argument("--sample", type=int, default=None,
                   help="从全集随机抽样 N 只（配合 --seed 可复现）")
    parser.add_argument("--seed", type=int, default=None, help="抽样随机种子")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--data-dir", default=str(DEFAULT_ZZ500_DIR),
                        help=f"zz500 5min 数据目录，默认 {DEFAULT_ZZ500_DIR}")
    parser.add_argument("--base-shares", type=int, default=3000, help="底仓股数")
    parser.add_argument("--avg-cost", type=float, default=None,
                        help="底仓成本（仅单股 --code 时生效；空=取首日 prev_close）")
    parser.add_argument("--tag", default="zz500",
                        help="输出文件后缀 tag，默认 zz500")
    parser.add_argument("--params-json", default=None,
                        help='参数覆盖 JSON 文件路径，格式 {"bp":{...},"sp":{...},"rp":{...}}')
    args = parser.parse_args()

    params_override = None
    if args.params_json:
        p = Path(args.params_json)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        with open(p, "r", encoding="utf-8") as f:
            params_override = json.load(f)
        print(f"[main] 已加载参数覆盖: {p}")
        print(f"[main] override = {json.dumps(params_override, ensure_ascii=False)}")

    try:
        codes = parse_codes(args, Path(args.data_dir))
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    if not codes:
        print("[ERROR] 代码列表为空", file=sys.stderr)
        return 2

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = DATA_ROOT / data_dir
    if not data_dir.exists():
        print(f"[ERROR] 数据目录不存在: {data_dir}", file=sys.stderr)
        return 2

    if len(codes) == 1:
        run_zz500_single(
            code=codes[0],
            start_date=args.start,
            end_date=args.end,
            data_dir=data_dir,
            base_shares=args.base_shares,
            avg_cost=args.avg_cost,
            tag=args.tag,
            params_override=params_override,
        )
    else:
        run_zz500_batch(
            codes=codes,
            start_date=args.start,
            end_date=args.end,
            data_dir=data_dir,
            base_shares=args.base_shares,
            tag=args.tag,
            params_override=params_override,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
