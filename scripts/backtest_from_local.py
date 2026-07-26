#!/usr/bin/env python3
"""
从本地下载好的分钟数据跑回测（不联网）。

读取 scripts/download_minute_data.py 产出的 {data_dir}/{code}/{date}.json
按指定股票范围 + 日期范围聚合后调用 backtest_multi_day，复用 cli.run 的输出格式。

数据加载策略
============
1. 枚举 [start, end] 区间内的所有自然日；
2. 对每个日期，检查 {data_dir}/{code}/{date}.json 是否存在；
3. 存在则加载（bars + prev_close + meta），不存在则跳过；
4. 全部加载完成后，调用 backtest_multi_day 跑回测；
5. 输出 trades.json / report.json / report.html（路径同 cli backtest）。

由于数据是按"每股每日一文件"存的，预先下载大区间后再跑任意子区间回测
都不需要再次联网，只需要指定不同的 --start / --end 即可。

用法示例
========
    # 单股回测（用默认 data/minute_local 目录）
    python scripts/backtest_from_local.py --code 600000 \
        --start 2026-07-01 --end 2026-07-22

    # 指定本地数据目录
    python scripts/backtest_from_local.py --code 600000 \
        --start 2026-07-01 --end 2026-07-22 \
        --data-dir D:/stock_data/minute

    # 批量回测（多只股票，输出汇总）
    python scripts/backtest_from_local.py --codes 600000,600026,600519 \
        --start 2026-07-01 --end 2026-07-22

    # 从候选池回测
    python scripts/backtest_from_local.py --pool \
        --start 2026-07-01 --end 2026-07-22

    # 指定底仓股数与成本
    python scripts/backtest_from_local.py --code 600000 \
        --start 2026-07-01 --end 2026-07-22 \
        --base-shares 5000 --avg-cost 10.50
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ── at0.data 加载（带 importlib 兜底）─────────────────────────────
# Trae 沙箱会加密 at0/__init__.py / backtest.py 等文件（%TSD-Header%）。
# 裸 python 终端下 `from at0.data import ...` 会因 __init__.py 加密失败；
# data.py 本身是明文，故先常规 import，失败则用 importlib 直载。
def _load_at0_data():
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

normalize_code = _load_at0_data()  # noqa: E402


# ── 回测依赖（at0.backtest / at0.cli / at0.strategy / at0.risk / at0.reports）──
# 这些模块在 Trae 沙箱外可能因 TSD 加密无法 import。
# 故延迟到 run_from_local() 内部 import：--help 与 load_multi_day_local() 不依赖它们。
def _import_backtest_deps():
    """
    延迟导入回测所需的所有 at0 子模块。

    在 Trae IDE 沙箱内（运行含 %TSD-Header% 加密文件的项目时）正常工作。
    在裸 python 终端下若 import 失败，会抛出 ImportError 并给出明确提示。
    """
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
    }

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "minute_local"
POOL_PATH = PROJECT_ROOT / "outputs" / "backtest" / "candidate_pool.json"


# ═══════════════════════════════════════════════════════════════
# 代码列表解析（与 download_minute_data.py 一致）
# ═══════════════════════════════════════════════════════════════
def parse_codes(args) -> list[str]:
    """--codes / --pool / --code 三选一。"""
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

    if args.pool:
        if not POOL_PATH.exists():
            raise FileNotFoundError(
                f"候选池不存在: {POOL_PATH}，请先跑 gen_candidate_pool.py 或用 --codes 指定"
            )
        with open(POOL_PATH, "r", encoding="utf-8") as f:
            pool = json.load(f)
        return [c["code"] for c in pool.get("candidates", [])]

    raise ValueError("必须指定 --code / --codes / --pool 之一")


# ═══════════════════════════════════════════════════════════════
# 本地数据加载（核心：替代 fetch_multi_day 的网络调用）
# ═══════════════════════════════════════════════════════════════
def load_multi_day_local(
    code: str,
    start_date: str,
    end_date: str,
    data_dir: Path,
) -> tuple[dict, dict, dict]:
    """
    从本地 {data_dir}/{code}/{date}.json 加载多日数据。

    返回与 at0.data.fetch_multi_day 完全一致的三元组：
        (daily_bars, daily_prev_closes, daily_meta)
        daily_bars:        {date: [bars]}
        daily_prev_closes: {date: prev_close}
        daily_meta:        {date: meta}
    """
    nc = normalize_code(code)
    pure_code = nc["pure"]
    code_dir = data_dir / pure_code

    daily_bars: dict[str, list[dict]] = {}
    daily_prev_closes: dict[str, float] = {}
    daily_meta: dict[str, dict] = {}

    if not code_dir.exists():
        print(f"[load_local] 目录不存在: {code_dir}")
        return daily_bars, daily_prev_closes, daily_meta

    # 枚举区间内自然日，逐日加载
    s = datetime.strptime(start_date, "%Y-%m-%d")
    e = datetime.strptime(end_date, "%Y-%m-%d")
    if e < s:
        raise ValueError(f"--end ({end_date}) 早于 --start ({start_date})")

    cur = s
    loaded = 0
    while cur <= e:
        d = cur.strftime("%Y-%m-%d")
        f = code_dir / f"{d}.json"
        if f.exists():
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    payload = json.load(fp)
                bars = payload.get("bars", [])
                pc = float(payload.get("prev_close", 0))
                meta = payload.get("meta", {}) or {}
                if bars and pc > 0:
                    daily_bars[d] = bars
                    daily_prev_closes[d] = pc
                    daily_meta[d] = meta
                    loaded += 1
            except (json.JSONDecodeError, OSError, ValueError) as ex:
                print(f"[load_local] 跳过损坏文件 {f.name}: {ex}")
        cur += timedelta(days=1)

    print(f"[load_local] {pure_code} {start_date}~{end_date} "
          f"← 本地({loaded}交易日, {code_dir})")
    return daily_bars, daily_prev_closes, daily_meta


# ═══════════════════════════════════════════════════════════════
# 单股回测（与 cli.run 同结构，仅把数据来源换成 load_multi_day_local）
# ═══════════════════════════════════════════════════════════════
def run_from_local(
    code: str,
    start_date: str,
    end_date: str,
    data_dir: Path,
    base_shares: int = 3000,
    avg_cost: float | None = None,
) -> dict:
    """
    从本地数据跑单股回测，返回结果 dict（同时落盘 trades/report/html）。
    """
    print(f"[run_from_local] {code} {start_date}~{end_date} "
          f"data_dir={data_dir}")

    # 0. 延迟导入回测依赖（在 Trae 沙箱外会失败）
    try:
        dep = _import_backtest_deps()
    except (ValueError, ImportError) as e:
        print(
            "[run_from_local] 无法导入回测模块（at0.backtest/at0.cli 等）。\n"
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

    # 1. 本地加载（替代 fetch_multi_day）
    daily_bars, daily_prev_closes, daily_meta = load_multi_day_local(
        code, start_date, end_date, data_dir,
    )
    if not daily_bars:
        print("[run_from_local] 未找到任何本地数据，退出")
        return {}

    # 2. 推断频率（取首个交易日 meta）
    first_meta = next(iter(daily_meta.values()))
    frequency = first_meta.get("frequency", "1min")
    bars_per_day = first_meta.get("bars_count", 240)
    print(f"[run_from_local] 数据频率={frequency}, 约 {bars_per_day} 根/天")

    # 3. 底仓成本：未指定则用首日 prev_close
    if avg_cost is None:
        first_date = min(daily_prev_closes.keys())
        avg_cost = daily_prev_closes[first_date]
        print(f"[run_from_local] avg_cost 未指定，取首日 prev_close={avg_cost:.4f}")

    # 4. 构造回测参数 + 频率自适应
    params = BacktestParams(
        base_shares=base_shares,
        avg_cost=avg_cost,
        signal_params=SignalParams(),
        risk_params=RiskParams(),
    )
    params = adapt_params_by_frequency(params, frequency, bars_per_day)
    print(f"[run_from_local] warmup_bars={params.warmup_bars}, "
          f"eod_check_bar_idx={params.eod_check_bar_idx}")

    # 5. 回测
    result = backtest_multi_day(
        code=normalize_code(code)["pure"],
        daily_bars=daily_bars,
        daily_prev_closes=daily_prev_closes,
        params=params,
    )

    # 6. 输出（沿用 cli.run 的输出约定）
    code_tag = normalize_code(code)["pure"]
    trades_json = BACKTEST_OUTPUT_DIR / f"{code_tag}_{start_date}_{end_date}_trades.json"
    report_json = BACKTEST_OUTPUT_DIR / f"{code_tag}_{start_date}_{end_date}_report.json"
    report_html = BACKTEST_OUTPUT_DIR / f"{code_tag}_{start_date}_{end_date}_report.html"
    n = save_trades_json(result, daily_meta, trades_json)
    save_report_json(result, daily_meta, report_json)
    save_html_report(result, daily_bars, report_html,
                     code=code_tag, start_date=start_date, end_date=end_date)

    print(f"\n[run_from_local] 买卖触发记录 -> {trades_json}  ({n} 笔)")
    print(f"[run_from_local] 完整报告     -> {report_json}")
    print(f"[run_from_local] HTML 可视化  -> {report_html}")
    print_backtest_summary(result)

    # 7. 版本化 artifacts（与 cli.run 一致）
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
        run_type="single_local",
    )
    print(f"[run_from_local] run_id       -> {run_id}")
    print(f"[run_from_local] artifacts    -> {run_dir}")
    return result


# ═══════════════════════════════════════════════════════════════
# 批量回测
# ═══════════════════════════════════════════════════════════════
def batch_from_local(
    codes: list[str],
    start_date: str,
    end_date: str,
    data_dir: Path,
    base_shares: int,
) -> None:
    """多股票批量回测 + 汇总（沿用 batch_main 的输出约定）。"""
    # 延迟导入：批量也依赖 at0.backtest 等
    try:
        dep = _import_backtest_deps()
    except (ValueError, ImportError) as e:
        print(
            "[batch_from_local] 无法导入回测模块（at0.backtest/at0.cli 等）。\n"
            "  原因: Trae 沙箱对部分 .py 文件做了 TSD 加密，裸 python 终端无法直接编译。\n"
            "  解决: 在 Trae IDE 中通过运行按钮/调试器执行本脚本（沙箱会自动解密），\n"
            f"  原始异常: {e}",
            file=sys.stderr,
        )
        return

    extract_trades = dep["extract_trades"]
    summarize_one_stock = dep["summarize_one_stock"]
    aggregate_batch = dep["aggregate_batch"]
    save_batch_html_report = dep["save_batch_html_report"]
    BACKTEST_OUTPUT_DIR = dep["BACKTEST_OUTPUT_DIR"]

    summary_path = BACKTEST_OUTPUT_DIR / "batch_summary.json"
    html_path = BACKTEST_OUTPUT_DIR / "batch_summary.html"

    print(f"[batch_local] 回测窗口 {start_date} ~ {end_date}, "
          f"data_dir={data_dir}")
    print(f"[batch_local] base_shares={base_shares}")
    print("=" * 70)

    per_stock: list[dict] = []
    all_trades_count = 0

    for i, code in enumerate(codes, 1):
        prefix = f"[{i}/{len(codes)}] {code}"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = run_from_local(
                    code=code,
                    start_date=start_date,
                    end_date=end_date,
                    data_dir=data_dir,
                    base_shares=base_shares,
                    avg_cost=None,
                )
        except Exception as e:
            print(f"{prefix} 异常: {e}")
            per_stock.append({"code": code, "error": str(e),
                              "total_trades": 0, "paired_trades": 0,
                              "win_trades": 0, "win_rate": 0.0, "net_pnl": 0.0})
            continue

        if not result or not result.get("daily_results"):
            print(f"{prefix} 无数据")
            per_stock.append({"code": code, "error": "no_data",
                              "total_trades": 0, "paired_trades": 0,
                              "win_trades": 0, "win_rate": 0.0, "net_pnl": 0.0})
            continue

        trades = extract_trades(result)
        summary = summarize_one_stock(code, result)
        per_stock.append(summary)
        all_trades_count += len(trades)

        wr = f"{summary['win_rate']*100:.1f}%" if summary['paired_trades'] else "N/A"
        print(f"{prefix}  交易={summary['total_trades']:2d} "
              f"配对={summary['paired_trades']:2d} "
              f"胜率={wr:>5s} "
              f"净盈亏={summary['net_pnl']:+8.2f} "
              f"浮盈={summary['unrealized_pnl']:+8.2f} "
              f"未配对腿={summary['final_open_legs_count']}")

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
    print("按股票拆分（按含浮盈净盈亏降序）")
    print("-" * 70)
    sorted_by_pnl = sorted(
        per_stock,
        key=lambda x: x.get("net_pnl_with_unrealized", x.get("net_pnl", 0)),
        reverse=True,
    )
    for s in sorted_by_pnl:
        if "error" in s:
            print(f"  {s['code']:12s}  ERROR: {s['error']}")
            continue
        wr = f"{s['win_rate']*100:.1f}%" if s['paired_trades'] else "  N/A"
        print(f"  {s['code']:12s}  交易={s['total_trades']:2d}  "
              f"配对={s['paired_trades']:2d}  胜率={wr:>5s}  "
              f"已实现={s['net_pnl']:+8.2f}  浮盈={s['unrealized_pnl']:+8.2f}  "
              f"含浮盈={s['net_pnl_with_unrealized']:+8.2f}  未配对={s['final_open_legs_count']}")

    # 落盘
    BACKTEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "start": start_date, "end": end_date,
        "data_dir": str(data_dir), "base_shares": base_shares,
        "overall": overall, "per_stock": per_stock,
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
    save_batch_html_report(summary_data, html_path)
    print(f"\n[batch_local] 汇总报告 -> {summary_path}")
    print(f"[batch_local] HTML报告 -> {html_path}")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser(
        description="从本地分钟数据跑回测（不联网，配合 download_minute_data.py 使用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--code", default=None, help="单只股票代码")
    g.add_argument("--codes", default=None,
                   help="多只股票代码，逗号分隔；或 @file.txt 从文件读取")
    g.add_argument("--pool", action="store_true",
                   help=f"从候选池读取：{POOL_PATH.relative_to(PROJECT_ROOT)}")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help=f"本地数据目录，默认 {DEFAULT_DATA_DIR.relative_to(PROJECT_ROOT)}")
    parser.add_argument("--base-shares", type=int, default=3000, help="底仓股数")
    parser.add_argument("--avg-cost", type=float, default=None,
                        help="底仓成本（仅单股 --code 时生效；空=取首日 prev_close）")
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

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    if not data_dir.exists():
        print(f"[ERROR] 本地数据目录不存在: {data_dir}", file=sys.stderr)
        print(f"        请先跑: python scripts/download_minute_data.py ...",
              file=sys.stderr)
        return 2

    if len(codes) == 1:
        # 单股
        run_from_local(
            code=codes[0],
            start_date=args.start,
            end_date=args.end,
            data_dir=data_dir,
            base_shares=args.base_shares,
            avg_cost=args.avg_cost,
        )
    else:
        # 批量
        batch_from_local(
            codes=codes,
            start_date=args.start,
            end_date=args.end,
            data_dir=data_dir,
            base_shares=args.base_shares,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
