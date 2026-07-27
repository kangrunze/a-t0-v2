"""
zz500 参数寻优（基于本地 5min 数据 + 用户改进方向）

改进方向（用户指示）
====================
1. 放宽止损：stop_loss_ratio 0.8% → 1.5%/2.0%
2. 延长持仓上限：max_holding_bars 12 → 24（120min）
3. 加趋势过滤：MA60向上才允许反T买入（用 ADX-based regime 实现）
4. 降频：cooldown_bars 12 → 24
5. 板块择优：科创板/创业板优先（用宽止损）
6. 参数寻优：在子样本上网格搜索

网格设计（精简，避免组合爆炸）
================================
- stop_loss_ratio:       [0.015, 0.020]
- max_holding_bars:      [24]              (用户指定，固定)
- cooldown_bars:         [24]              (用户指定，固定)
- hard_trend_filter_add: [False, True]     (新增趋势门控开关)
- tf_adx_threshold:      [30.0, 35.0]      (趋势确认门槛)
- min_capture_spread:    [0.012, 0.015]    (更宽捕获空间，降频)

合计 2 × 1 × 1 × 2 × 2 × 2 = 16 组合

子样本：30 只（科创板+创业板+主板各10只，固定 seed=42 抽样）
回测窗口：2023-07-25 ~ 2026-07-22（完整3年）

评分函数
========
score = net_pnl + (win_rate - 0.5) × 5000 - losers × 100 - max(0, avg_pnl × 10)
       - 5000 if paired < stocks × 0.5   (避免不开仓退化解)

用法
====
    python -u scripts/optimize_zz500_params.py
    python -u scripts/optimize_zz500_params.py --stage1-codes 20 --limit-combos 8
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
import time
from dataclasses import replace
from itertools import product
from pathlib import Path

# 强制无缓冲
print = functools.partial(print, flush=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 复用 backtest_zz500 的加载器
from at0.backtest import BacktestParams, backtest_multi_day, summarize_one_stock, aggregate_batch
from at0.data import normalize_code
from at0.strategy import SignalParams
from at0.risk import RiskParams

# 直接导入本地数据加载器
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from backtest_zz500 import load_multi_day_zz500, DEFAULT_ZZ500_DIR


# ═══════════════════════════════════════════════════════════════
# 参数搜索空间（用户改进方向）
# ═══════════════════════════════════════════════════════════════
PARAM_GRID = {
    "stop_loss_ratio":         [0.015, 0.020],     # 2（放宽止损）
    "max_holding_bars":        [24],               # 1（延长持仓上限，固定）
    "cooldown_bars":           [24],               # 1（降频，固定）
    "hard_trend_filter_add":   [False, True],      # 2（趋势门控开关）
    "tf_adx_threshold":        [30.0, 35.0],       # 2（趋势确认门槛）
    "min_capture_spread":      [0.012, 0.015],     # 2（更宽捕获空间）
}
# 16 组合


START_DATE = "2023-07-25"
END_DATE = "2026-07-22"


# ═══════════════════════════════════════════════════════════════
# 子样本选取：按板块分层抽样
# ═══════════════════════════════════════════════════════════════
def pick_sample_by_board(data_dir: Path, total: int = 30, seed: int = 42) -> list[str]:
    """按板块分层抽样：科创板/创业板/主板各取 total//3。"""
    import random
    all_codes = []
    for f in sorted(data_dir.glob("*.json")):
        c = f.stem
        if len(c) == 6 and c.isdigit():
            all_codes.append(c)

    kechuang = [c for c in all_codes if c.startswith("68")]   # 科创板
    chuangye = [c for c in all_codes if c.startswith("30")]   # 创业板
    zhuban = [c for c in all_codes if c.startswith("60") or c.startswith("00")]  # 主板

    rng = random.Random(seed)
    rng.shuffle(kechuang)
    rng.shuffle(chuangye)
    rng.shuffle(zhuban)

    per_board = total // 3
    picked = kechuang[:per_board] + chuangye[:per_board] + zhuban[:per_board]
    return picked


# ═══════════════════════════════════════════════════════════════
# 根据参数组合构造 BacktestParams
# ═══════════════════════════════════════════════════════════════
def build_params(combo: dict, base_shares: int = 3000) -> BacktestParams:
    """根据 combo 构造 BacktestParams，应用用户改进方向。"""
    sp_fields = {}
    bp_fields = {}
    rp_fields = {}

    if "tf_adx_threshold" in combo:
        sp_fields["tf_adx_threshold"] = combo["tf_adx_threshold"]
    if "min_capture_spread" in combo:
        rp_fields["min_capture_spread"] = combo["min_capture_spread"]
    if "stop_loss_ratio" in combo:
        bp_fields["stop_loss_ratio"] = combo["stop_loss_ratio"]
    if "max_holding_bars" in combo:
        bp_fields["max_holding_bars"] = combo["max_holding_bars"]
    if "cooldown_bars" in combo:
        bp_fields["cooldown_bars"] = combo["cooldown_bars"]
    if "hard_trend_filter_add" in combo:
        bp_fields["hard_trend_filter_add"] = combo["hard_trend_filter_add"]

    sp = replace(SignalParams(), **sp_fields) if sp_fields else SignalParams()
    rp = replace(RiskParams(), **rp_fields) if rp_fields else RiskParams()
    bp = replace(
        BacktestParams(),
        base_shares=base_shares,
        signal_params=sp,
        risk_params=rp,
        **bp_fields,
    )
    return bp


# ═══════════════════════════════════════════════════════════════
# 评分函数
# ═══════════════════════════════════════════════════════════════
def score_combo(per_stock: list[dict]) -> float:
    """
    综合评分：
      - net_pnl（净盈亏，含浮盈）为主指标
      - 胜率偏离 50% 给奖惩
      - 亏损股票数惩罚
      - 平均单笔盈亏为负时额外惩罚（避免"开多亏多"退化解）
      - 配对数过少惩罚（避免"完全不开仓"退化解）
    """
    if not per_stock:
        return -1e9
    valid = [s for s in per_stock if "error" not in s]
    if not valid:
        return -1e9

    net = sum(s.get("net_pnl_with_unrealized", s.get("net_pnl", 0)) for s in valid)
    paired = sum(s.get("paired_trades", 0) for s in valid)
    wins = sum(s.get("win_trades", 0) for s in valid)
    win_rate = wins / paired if paired > 0 else 0.5
    losers = sum(1 for s in valid if s.get("net_pnl_with_unrealized", s.get("net_pnl", 0)) < 0)

    score = net + (win_rate - 0.5) * 5000 - losers * 100
    if paired > 0:
        avg_pnl = net / paired
        if avg_pnl < 0:
            score += avg_pnl * 10  # 负值再放大惩罚
    # 不开仓退化解惩罚
    if paired < len(valid) * 0.5:
        score -= 5000
    return round(score, 2)


# ═══════════════════════════════════════════════════════════════
# 单组参数回测
# ═══════════════════════════════════════════════════════════════
def run_one_combo(combo: dict, code_list: list[str], data_dir: Path,
                  base_shares: int = 3000) -> tuple[float, dict]:
    """对一组参数在指定股票集合上跑回测。"""
    params = build_params(combo, base_shares)
    per_stock = []
    total_trades = 0

    for code in code_list:
        daily_bars, daily_prev, daily_meta = load_multi_day_zz500(
            code, START_DATE, END_DATE, data_dir,
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

    score = score_combo(per_stock)
    overall = aggregate_batch(per_stock, total_trades) if per_stock else {}
    return score, {
        "combo": combo,
        "score": score,
        "overall": overall,
        "per_stock_count": len(per_stock),
    }


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="zz500 参数寻优（本地 5min 数据）")
    parser.add_argument("--stage1-codes", type=int, default=30,
                        help="阶段1代表股票数（按板块分层抽样）")
    parser.add_argument("--base-shares", type=int, default=3000)
    parser.add_argument("--out", default="outputs/backtest/zz500_param_optimization.json")
    parser.add_argument("--limit-combos", type=int, default=0,
                        help="限制组合数（调试用，0=全部）")
    parser.add_argument("--seed", type=int, default=42, help="抽样随机种子")
    parser.add_argument("--data-dir", default=str(DEFAULT_ZZ500_DIR),
                        help="zz500 5min 数据目录")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    if not data_dir.exists():
        print(f"[ERROR] 数据目录不存在: {data_dir}", file=sys.stderr)
        return 2

    print(f"=== zz500 参数寻优 ===")
    print(f"窗口: {START_DATE} ~ {END_DATE}")
    print(f"数据目录: {data_dir}")
    print(f"参数网格: {PARAM_GRID}")

    # 生成所有参数组合
    keys = list(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combos = [dict(zip(keys, vals)) for vals in product(*values)]
    if args.limit_combos > 0:
        combos = combos[:args.limit_combos]
    print(f"参数组合数: {len(combos)}")

    # 抽样代表股票
    stage1_codes = pick_sample_by_board(data_dir, args.stage1_codes, args.seed)
    print(f"\n阶段1代表股票（{len(stage1_codes)} 只，按板块分层抽样 seed={args.seed}）:")
    print(f"  科创板: {[c for c in stage1_codes if c.startswith('68')]}")
    print(f"  创业板: {[c for c in stage1_codes if c.startswith('30')]}")
    print(f"  主板:   {[c for c in stage1_codes if c.startswith('60') or c.startswith('00')]}")

    print(f"\n--- 开始网格搜索（{len(combos)} 组合 × {len(stage1_codes)} 只股票）---")
    print(f"预估单组合耗时 ≈ {len(stage1_codes) * 12}s（500只×725天×12s/只）")
    print(f"预估总耗时 ≈ {len(combos) * len(stage1_codes) * 12 / 60:.1f} 分钟")

    results = []
    t0 = time.time()
    for i, combo in enumerate(combos, 1):
        t1 = time.time()
        score, detail = run_one_combo(combo, stage1_codes, data_dir, args.base_shares)
        results.append(detail)
        elapsed = time.time() - t1
        total_elapsed = time.time() - t0
        ov = detail.get("overall", {})
        combo_short = json.dumps(combo, ensure_ascii=False)
        print(f"[{i}/{len(combos)}] score={score:>10.1f}  "
              f"net={ov.get('net_pnl_with_unrealized', 0):>+10.1f}  "
              f"wr={ov.get('win_rate', 0):.3f}  "
              f"paired={ov.get('paired_trades', 0):>5}  "
              f"profit={ov.get('profitable_stocks', 0):>2}/"
              f"loss={ov.get('losing_stocks', 0):>2}  "
              f"({elapsed:.0f}s, 累计{total_elapsed:.0f}s)")
        print(f"        combo: {combo_short}")

    # 排序
    results.sort(key=lambda x: x["score"], reverse=True)

    print("\n" + "=" * 80)
    print("最终排名（按综合评分降序）")
    print("=" * 80)
    for i, r in enumerate(results, 1):
        ov = r["overall"]
        print(f"#{i:<2} score={r['score']:>10.1f}  "
              f"net={ov.get('net_pnl_with_unrealized', 0):>+10.1f}  "
              f"wr={ov.get('win_rate', 0):.3f}  "
              f"paired={ov.get('paired_trades', 0):>5}  "
              f"profit/loss={ov.get('profitable_stocks', 0):>2}/{ov.get('losing_stocks', 0):>2}  "
              f"combo={json.dumps(r['combo'], ensure_ascii=False)}")

    best = results[0] if results else None
    if best:
        print("\n" + "=" * 80)
        print("=== 最佳参数组合 ===")
        print("=" * 80)
        print(f"  score: {best['score']:.1f}")
        ov = best["overall"]
        print(f"  净盈亏（含浮盈）: {ov.get('net_pnl_with_unrealized', 0):.2f}")
        print(f"  胜率:   {ov.get('win_rate', 0):.4f}")
        print(f"  配对数: {ov.get('paired_trades', 0)}")
        print(f"  盈利股: {ov.get('profitable_stocks', 0)}  亏损股: {ov.get('losing_stocks', 0)}")
        print(f"  参数: {json.dumps(best['combo'], ensure_ascii=False, indent=2)}")

    # 保存结果
    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "start_date": START_DATE,
            "end_date": END_DATE,
            "data_dir": str(data_dir),
            "param_grid": PARAM_GRID,
            "stage1_codes": stage1_codes,
            "results_all": results,
            "best": best,
            "total_elapsed_sec": time.time() - t0,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
