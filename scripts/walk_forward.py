"""
Stage I: Walk Forward 规范化（滚动窗口验证）

目的
====
补充静态 train/test 切分做不到的能力：按时间窗口看策略表现是否稳定，
而不是只看3年汇总数字。核心产出是"每个测试窗口的表现"这条时间序列，
用来回答"策略的正期望是稳定存在的，还是集中在少数几个窗口"。

方案
====
- 训练窗口 180 个交易日（约9个月）→ 本轮不调参，训练窗口仅用于定义滚动起点
- 测试窗口 30 个交易日（约6周）
- 滚动步长 30 天
- 覆盖 2023-07-25 ~ 2026-07-22（约725交易日，预计 18+ 个测试窗口）
- 每个窗口用当前 thresholds.yaml 的锁定配置跑测试期回测，不重新调参

输出
====
- 每个测试窗口的 win_rate / net_pnl / payoff_ratio / avg_win / avg_loss
- 按时间顺序列出表格
- 正盈亏窗口占比 + 集中度分析

用法
====
    # 默认 100 股抽样 × 3年 × 180/30/30
    python -u scripts/walk_forward.py

    # 自定义样本量
    python -u scripts/walk_forward.py --sample 50 --seed 42

    # 全集
    python -u scripts/walk_forward.py --all

    # 自定义窗口参数
    python -u scripts/walk_forward.py --train-days 180 --test-days 30 --step-days 30

注意
====
- 不落盘单股 trades/report/html（避免产出几千个文件）
- 只收集汇总统计（summarize_one_stock + aggregate_batch）
- 参数从 thresholds.yaml 加载（Stage G 定案值），不重新调参
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

# 强制无缓冲
print = functools.partial(print, flush=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# 复用 backtest_zz500 的加载器和依赖导入
from backtest_zz500 import load_multi_day_zz500, DEFAULT_ZZ500_DIR, filter_codes_by_amplitude
from at0.data import normalize_code


# ═══════════════════════════════════════════════════════════════
# 参数加载（从 thresholds.yaml，不重新调参）
# ═══════════════════════════════════════════════════════════════
def load_locked_params(base_shares: int = 3000):
    """加载当前锁定的回测参数（Stage G 定案值，不调参）。

    从 thresholds.yaml 加载，不接收任何 override。
    """
    # 延迟导入（沙箱兼容）
    try:
        from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock, aggregate_batch
        from at0.strategy import SignalParams
        from at0.risk import RiskParams
        from at0.config import load_signal_params, load_risk_params, load_backtest_params, load_screener_params
        from at0.cli import adapt_params_by_frequency
    except (ValueError, ImportError) as e:
        print(f"[walk_forward] 无法导入回测模块: {e}", file=sys.stderr)
        raise

    sp = load_signal_params()
    rp = load_risk_params()
    screener_sp = load_screener_params()
    params = replace(
        load_backtest_params(),
        base_shares=base_shares,
        signal_params=sp,
        risk_params=rp,
    )
    return params, {
        "backtest_multi_day": backtest_multi_day,
        "summarize_one_stock": summarize_one_stock,
        "aggregate_batch": aggregate_batch,
        "adapt_params_by_frequency": adapt_params_by_frequency,
        "screener_params": screener_sp,
    }


# ═══════════════════════════════════════════════════════════════
# 交易日列表 & 窗口切片
# ═══════════════════════════════════════════════════════════════
def get_all_trading_days(data_dir: Path) -> list[str]:
    """从任一数据文件提取全部交易日列表（升序）。

    用第一个可读的 zz500 json 文件的 daily_bars 键作为交易日日历。
    """
    for f in sorted(data_dir.glob("*.json")):
        if f.stem == "zz500_constituents":
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.load(fp)
            days = sorted(d.get("daily_bars", {}).keys())
            if days:
                return days
        except (json.JSONDecodeError, OSError):
            continue
    return []


def slice_windows(trading_days: list[str], train_days: int, test_days: int,
                  step_days: int) -> list[dict]:
    """按 [180训练][30测试] 滚动切片。

    返回每个窗口的 {window_id, train_start, train_end, test_start, test_end}。
    训练窗口仅用于定义滚动起点（本轮不调参），实际只跑测试窗口回测。
    """
    windows = []
    total = len(trading_days)
    if total < train_days + test_days:
        return windows

    start_idx = 0
    win_id = 1
    while start_idx + train_days + test_days <= total:
        train_start = trading_days[start_idx]
        train_end = trading_days[start_idx + train_days - 1]
        test_start = trading_days[start_idx + train_days]
        test_end = trading_days[start_idx + train_days + test_days - 1]
        windows.append({
            "window_id": win_id,
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
        })
        start_idx += step_days
        win_id += 1
    return windows


# ═══════════════════════════════════════════════════════════════
# 单窗口回测
# ═══════════════════════════════════════════════════════════════
def run_one_window(window: dict, codes: list[str], data_dir: Path,
                   params, dep, base_shares: int = 3000,
                   trading_days: list[str] = None) -> dict:
    """对单个测试窗口跑批量回测，返回该窗口的汇总指标。

    振幅筛选：用测试期前60个交易日的数据计算60日日均振幅，仅对通过的股票回测。
    每个窗口独立筛选（滚动筛选），符合 Walk Forward 的时序严谨性。
    """
    backtest_multi_day = dep["backtest_multi_day"]
    summarize_one_stock = dep["summarize_one_stock"]
    aggregate_batch = dep["aggregate_batch"]
    screener_sp = dep["screener_params"]

    test_start = window["test_start"]
    test_end = window["test_end"]

    # 振幅筛选：取测试期前60个交易日作为计算窗口
    amp_threshold = screener_sp.min_amplitude_long
    amp_passed_codes = codes
    amp_filtered_count = 0
    amp_window_start = None
    amp_window_end = None
    if amp_threshold is not None and trading_days is not None:
        # 定位测试期在交易日列表中的位置
        try:
            test_idx = trading_days.index(test_start)
            amp_start_idx = max(0, test_idx - 60)
            amp_end_idx = test_idx  # 不含测试期
            amp_window_start = trading_days[amp_start_idx]
            amp_window_end = trading_days[amp_end_idx - 1] if amp_end_idx > amp_start_idx else amp_window_start
            amp_passed_codes, amp_filtered = filter_codes_by_amplitude(
                codes, data_dir, amp_window_start, amp_window_end, amp_threshold, window=60,
            )
            amp_filtered_count = len(amp_filtered)
        except (ValueError, IndexError):
            # 测试期不在交易日列表中（可能首窗口前60日不足），退化为不筛选
            pass

    per_stock = []
    total_trades = 0

    for code in amp_passed_codes:
        daily_bars, daily_prev, daily_meta = load_multi_day_zz500(
            code, test_start, test_end, data_dir,
        )
        if not daily_bars:
            continue
        try:
            # 首日 prev_close 作为 avg_cost
            first_date = min(daily_prev.keys())
            avg_cost = daily_prev[first_date]
            params_with_cost = replace(params, avg_cost=avg_cost)

            result = backtest_multi_day(
                code=normalize_code(code)["pure"],
                daily_bars=daily_bars,
                daily_prev_closes=daily_prev,
                params=params_with_cost,
            )
            s = summarize_one_stock(code, result)
            per_stock.append(s)
            total_trades += s["total_trades"]
        except Exception as e:
            per_stock.append({"code": code, "error": str(e)})

    overall = aggregate_batch(per_stock, total_trades) if per_stock else {}
    return {
        "window": window,
        "overall": overall,
        "stocks_run": len([s for s in per_stock if "error" not in s]),
        "amp_filter": {
            "threshold": amp_threshold,
            "amp_window_start": amp_window_start,
            "amp_window_end": amp_window_end,
            "input_count": len(codes),
            "passed_count": len(amp_passed_codes),
            "filtered_count": amp_filtered_count,
        },
    }


# ═══════════════════════════════════════════════════════════════
# 样本选取
# ═══════════════════════════════════════════════════════════════
def pick_sample_codes(data_dir: Path, sample: int, seed: int) -> list[str]:
    """随机抽样 N 只股票代码。"""
    import random
    all_codes = []
    for f in sorted(data_dir.glob("*.json")):
        c = f.stem
        if len(c) == 6 and c.isdigit():
            all_codes.append(c)
    if sample <= 0 or sample >= len(all_codes):
        return all_codes
    rng = random.Random(seed)
    rng.shuffle(all_codes)
    return all_codes[:sample]


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Stage I: Walk Forward 滚动窗口验证")
    parser.add_argument("--sample", type=int, default=100,
                        help="随机抽样股票数（默认100，0=全集）")
    parser.add_argument("--seed", type=int, default=42, help="抽样随机种子")
    parser.add_argument("--all", action="store_true", help="全集（忽略 --sample）")
    parser.add_argument("--base-shares", type=int, default=3000)
    parser.add_argument("--train-days", type=int, default=180, help="训练窗口交易日数")
    parser.add_argument("--test-days", type=int, default=30, help="测试窗口交易日数")
    parser.add_argument("--step-days", type=int, default=30, help="滚动步长（交易日）")
    parser.add_argument("--start", default="2023-07-25", help="样本起始日期")
    parser.add_argument("--end", default="2026-07-22", help="样本结束日期")
    parser.add_argument("--data-dir", default=str(DEFAULT_ZZ500_DIR),
                        help="zz500 5min 数据目录")
    parser.add_argument("--out", default="outputs/backtest/walk_forward_result.json")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    if not data_dir.exists():
        print(f"[ERROR] 数据目录不存在: {data_dir}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("Stage I: Walk Forward 滚动窗口验证")
    print("=" * 70)
    print(f"样本区间: {args.start} ~ {args.end}")
    print(f"数据目录: {data_dir}")
    print(f"窗口参数: 训练{args.train_days}天 + 测试{args.test_days}天, 步长{args.step_days}天")

    # 加载锁定参数（不调参）
    params, dep = load_locked_params(args.base_shares)
    screener_sp = dep["screener_params"]
    print(f"锁定参数（来自 thresholds.yaml，不调参）:")
    print(f"  stop_loss_ratio = {params.stop_loss_ratio}")
    print(f"  trailing_ratio  = {params.trailing_ratio}")
    print(f"  max_holding_bars = {params.max_holding_bars}")
    print(f"  cooldown_bars   = {params.cooldown_bars}")
    print(f"  min_amplitude_long = {screener_sp.min_amplitude_long}  (来自 ScreenerParams)")

    # 获取交易日列表
    all_trading_days = get_all_trading_days(data_dir)
    # 按样本区间过滤
    trading_days = [d for d in all_trading_days if args.start <= d <= args.end]
    print(f"\n交易日总数: {len(trading_days)}（区间 {args.start}~{args.end}）")

    if len(trading_days) < args.train_days + args.test_days:
        print(f"[ERROR] 交易日不足：需要至少 {args.train_days + args.test_days} 天，"
              f"实际 {len(trading_days)} 天", file=sys.stderr)
        return 2

    # 切片窗口
    windows = slice_windows(trading_days, args.train_days, args.test_days, args.step_days)
    print(f"滚动窗口数: {len(windows)}")
    if not windows:
        print("[ERROR] 无法切出任何窗口", file=sys.stderr)
        return 2
    print(f"首窗口测试期: {windows[0]['test_start']} ~ {windows[0]['test_end']}")
    print(f"末窗口测试期: {windows[-1]['test_start']} ~ {windows[-1]['test_end']}")

    # 抽样股票
    if args.all:
        codes = pick_sample_codes(data_dir, 0, args.seed)
    else:
        codes = pick_sample_codes(data_dir, args.sample, args.seed)
    print(f"样本股票数: {len(codes)}（seed={args.seed}）")

    # 预估耗时
    est_per_window = len(codes) * 0.4  # 约0.4s/股（30天数据）
    est_total = est_per_window * len(windows)
    print(f"预估单窗口耗时 ≈ {est_per_window:.0f}s")
    print(f"预估总耗时 ≈ {est_total/60:.1f} 分钟")
    print("=" * 70)

    # 滚动回测
    results = []
    t0 = time.time()
    for i, win in enumerate(windows, 1):
        t1 = time.time()
        print(f"\n--- 窗口 {i}/{len(windows)} ---")
        print(f"  训练期: {win['train_start']} ~ {win['train_end']}")
        print(f"  测试期: {win['test_start']} ~ {win['test_end']}")

        r = run_one_window(win, codes, data_dir, params, dep, args.base_shares,
                           trading_days=trading_days)
        results.append(r)

        ov = r["overall"]
        elapsed = time.time() - t1
        total_elapsed = time.time() - t0
        af = r.get("amp_filter", {})
        amp_info = ""
        if af.get("threshold") is not None:
            amp_info = (f", 振幅筛选: {af['passed_count']}/{af['input_count']}"
                        f"（筛掉{af['filtered_count']}只，窗口{af['amp_window_start']}~{af['amp_window_end']}）")
        print(f"  结果: stocks={r['stocks_run']}, "
              f"paired={ov.get('paired_trades', 0)}, "
              f"win_rate={ov.get('win_rate', 0):.4f}, "
              f"net_pnl={ov.get('net_pnl_with_unrealized', ov.get('net_pnl', 0)):+.2f}, "
              f"payoff={ov.get('payoff_ratio', 0):.4f}, "
              f"avg_win={ov.get('avg_win', 0):+.2f}, "
              f"avg_loss={ov.get('avg_loss', 0):+.2f}"
              f"{amp_info} "
              f"({elapsed:.0f}s, 累计{total_elapsed:.0f}s)")

    total_elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print(f"滚动回测完成，总耗时 {total_elapsed/60:.1f} 分钟")
    print("=" * 70)

    # 生成时间序列报告
    generate_report(results, params, screener_sp, args, total_elapsed)

    # 保存原始结果
    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "config": {
                "train_days": args.train_days,
                "test_days": args.test_days,
                "step_days": args.step_days,
                "sample": len(codes),
                "seed": args.seed,
                "start": args.start,
                "end": args.end,
            },
            "locked_params": {
                "stop_loss_ratio": params.stop_loss_ratio,
                "trailing_ratio": params.trailing_ratio,
                "max_holding_bars": params.max_holding_bars,
                "cooldown_bars": params.cooldown_bars,
            },
            "results": results,
            "total_elapsed_sec": total_elapsed,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n原始结果已保存: {out_path}")

    return 0


# ═══════════════════════════════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════════════════════════════
def generate_report(results: list[dict], params, screener_sp, args, total_elapsed: float):
    """生成时间序列报告 + 集中度分析。"""
    report_path = PROJECT_ROOT / "outputs/backtest/REPORT_walk_forward.md"

    lines = []
    lines.append("# Stage I: Walk Forward 滚动窗口验证报告\n")
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 总耗时：{total_elapsed/60:.1f} 分钟\n")

    lines.append("## 1. 实验配置\n")
    lines.append(f"- **样本区间**：{args.start} ~ {args.end}")
    lines.append(f"- **样本股票**：{args.sample} 只（seed={args.seed}）" if not args.all
                 else f"- **样本股票**：全集")
    lines.append(f"- **窗口参数**：训练 {args.train_days} 天 + 测试 {args.test_days} 天，步长 {args.step_days} 天")
    lines.append(f"- **测试窗口数**：{len(results)}")
    lines.append(f"- **锁定参数**（来自 thresholds.yaml，不调参）：")
    lines.append(f"  - stop_loss_ratio = {params.stop_loss_ratio}")
    lines.append(f"  - trailing_ratio = {params.trailing_ratio}")
    lines.append(f"  - max_holding_bars = {params.max_holding_bars}")
    lines.append(f"  - cooldown_bars = {params.cooldown_bars}")
    lines.append(f"  - min_amplitude_long = {screener_sp.min_amplitude_long}（来自 ScreenerParams）\n")

    # 时间序列表
    lines.append("## 2. 测试窗口时间序列（按时间顺序）\n")
    lines.append("| # | 测试期 | stocks | paired | win_rate | net_pnl | avg_win | avg_loss | payoff | 振幅筛选 |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")

    for r in results:
        win = r["window"]
        ov = r["overall"]
        net = ov.get("net_pnl_with_unrealized", ov.get("net_pnl", 0))
        af = r.get("amp_filter", {})
        amp_str = "关闭"
        if af.get("threshold") is not None:
            amp_str = f"{af['passed_count']}/{af['input_count']}"
        lines.append(
            f"| {win['window_id']} "
            f"| {win['test_start']} ~ {win['test_end']} "
            f"| {r['stocks_run']} "
            f"| {ov.get('paired_trades', 0)} "
            f"| {ov.get('win_rate', 0):.4f} "
            f"| {net:+.2f} "
            f"| {ov.get('avg_win', 0):+.2f} "
            f"| {ov.get('avg_loss', 0):+.2f} "
            f"| {ov.get('payoff_ratio', 0):.4f} "
            f"| {amp_str} |"
        )

    # 汇总统计
    lines.append("\n## 3. 汇总统计（所有窗口平均，供参考）\n")
    nets = [r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) for r in results]
    wrs = [r["overall"].get("win_rate", 0) for r in results]
    payoffs = [r["overall"].get("payoff_ratio", 0) for r in results]
    paired_total = sum(r["overall"].get("paired_trades", 0) for r in results)

    lines.append(f"- **总测试窗口数**：{len(results)}")
    lines.append(f"- **总配对笔数**：{paired_total}")
    lines.append(f"- **平均胜率**：{sum(wrs)/len(wrs):.4f}")
    lines.append(f"- **平均盈亏比**：{sum(payoffs)/len(payoffs):.4f}")
    lines.append(f"- **平均净盈亏**：{sum(nets)/len(nets):+.2f}")
    lines.append(f"- **总净盈亏**：{sum(nets):+.2f}")
    lines.append(f"- **最大单窗口盈利**：{max(nets):+.2f}")
    lines.append(f"- **最大单窗口亏损**：{min(nets):+.2f}\n")

    # 正盈亏窗口占比
    lines.append("## 4. 稳定性分析：正盈亏窗口占比\n")
    pos_windows = [r for r in results if r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) > 0]
    neg_windows = [r for r in results if r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) <= 0]

    pos_pct = len(pos_windows) / len(results) * 100 if results else 0
    lines.append(f"- **净盈亏为正的窗口数**：{len(pos_windows)} / {len(results)}（{pos_pct:.1f}%）")
    lines.append(f"- **净盈亏为负的窗口数**：{len(neg_windows)} / {len(results)}（{100-pos_pct:.1f}%）\n")

    if pos_windows:
        pos_nets = [r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) for r in pos_windows]
        total_pos = sum(pos_nets)
        lines.append(f"- **正收益窗口总贡献**：{total_pos:+.2f}")
        lines.append(f"- **正收益窗口平均**：{total_pos/len(pos_windows):+.2f}")
        lines.append(f"- **正收益窗口最大**：{max(pos_nets):+.2f}")
        lines.append(f"- **正收益窗口最小**：{min(pos_nets):+.2f}\n")

    if neg_windows:
        neg_nets = [r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) for r in neg_windows]
        total_neg = sum(neg_nets)
        lines.append(f"- **负收益窗口总贡献**：{total_neg:+.2f}")
        lines.append(f"- **负收益窗口平均**：{total_neg/len(neg_windows):+.2f}")
        lines.append(f"- **负收益窗口最大**：{max(neg_nets):+.2f}")
        lines.append(f"- **负收益窗口最小**：{min(neg_nets):+.2f}\n")

    # 集中度分析：Top3 正收益窗口贡献占比
    lines.append("## 5. 集中度分析：正收益是否集中在少数窗口\n")
    if pos_windows:
        sorted_pos = sorted(pos_windows, key=lambda r: r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)), reverse=True)
        total_pos = sum(r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) for r in pos_windows)
        lines.append("> 占比 = 该窗口净盈亏 / 所有正收益窗口总贡献（衡量正收益在哪些窗口产生）\n")
        lines.append("| 排名 | 窗口 # | 测试期 | net_pnl | 占正收益总额比 |")
        lines.append("|---:|---:|---|---:|---:|")
        for rank, r in enumerate(sorted_pos[:5], 1):
            net = r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0))
            pct = (net / total_pos * 100) if total_pos > 0 else 0
            lines.append(f"| {rank} | {r['window']['window_id']} | {r['window']['test_start']} ~ {r['window']['test_end']} | {net:+.2f} | {pct:.1f}% |")

        top3_sum = sum(r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) for r in sorted_pos[:3])
        top3_pct = (top3_sum / total_pos * 100) if total_pos > 0 else 0
        lines.append(f"\n- **Top3 正收益窗口合计**：{top3_sum:+.2f}（占正收益总额 {top3_pct:.1f}%）")
        lines.append(f"- **正收益总额**：{total_pos:+.2f}")
        lines.append(f"- **总净盈亏**：{sum(nets):+.2f}（= 正收益 {total_pos:+.2f} + 负收益 {sum(nets)-total_pos:+.2f}）")

        if top3_pct > 80:
            lines.append(f"\n> ⚠️ **集中度警告**：Top3 窗口贡献了正收益总额的 {top3_pct:.1f}%，正收益高度集中在少数窗口。")
        elif top3_pct > 60:
            lines.append(f"\n> ⚠️ **集中度提示**：Top3 窗口贡献了正收益总额的 {top3_pct:.1f}%，存在一定集中度。")
        else:
            lines.append(f"\n> ✅ **集中度正常**：Top3 窗口仅贡献正收益总额的 {top3_pct:.1f}%，正收益分布较均匀。")

    lines.append("\n## 6. 结论（数据呈现，不代用户判断）\n")
    lines.append(f"- {len(pos_windows)}/{len(results)} 个窗口（{pos_pct:.1f}%）净盈亏为正")
    lines.append(f"- 总净盈亏 {sum(nets):+.2f}，平均每窗口 {sum(nets)/len(nets):+.2f}")
    if pos_windows:
        total_pos = sum(r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) for r in pos_windows)
        top3_pct = (sum(r["overall"].get("net_pnl_with_unrealized", r["overall"].get("net_pnl", 0)) for r in sorted_pos[:3]) / total_pos * 100) if total_pos > 0 else 0
        lines.append(f"- Top3 正收益窗口占正收益总额的 {top3_pct:.1f}%")
    lines.append("\n> 策略是否稳定、是否过度依赖特定市场环境，由用户基于上述数据判断。")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告已生成: {report_path}")


if __name__ == "__main__":
    sys.exit(main())
