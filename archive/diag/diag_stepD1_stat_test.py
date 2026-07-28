"""
步骤1 — 盈亏分组的统计检验
=========================
对 stepD_attribution.json 中 100 只股票的 ADX 均值、日均振幅做：
  1. Mann-Whitney U 检验（非参数，不假设正态分布）
  2. Cliff's delta 效应量（方向 + 强度）
  3. AUC（用 Mann-Whitney U 直接换算）
  4. ROC 曲线最优切点（Youden's J 统计量）

输出：
  - 控制台报告
  - outputs/backtest/stepD1_stat_test.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUT_JSON = PROJECT_ROOT / "outputs" / "backtest" / "stepD_attribution.json"
OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtest" / "stepD1_stat_test.json"


def mann_whitney_u(x: list[float], y: list[float]) -> tuple[float, float]:
    """Mann-Whitney U 检验，返回 (U, p_value)。

    手工实现（避免 scipy 依赖问题），正态近似 + 连续性修正。
    """
    nx, ny = len(x), len(y)
    if nx == 0 or ny == 0:
        return 0.0, 1.0
    # 合并排序，计算 rank（平均秩处理 ties）
    combined = [(v, 0) for v in x] + [(v, 1) for v in y]
    combined.sort(key=lambda t: t[0])
    # 分配秩（ties 用平均秩）
    ranks = [0.0] * (nx + ny)
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2  # 1-indexed, 平均
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    # R_x = x 组的秩和
    R_x = sum(ranks[i] for i in range(nx + ny) if combined[i][1] == 0)
    U_x = R_x - nx * (nx + 1) / 2
    U_y = nx * ny - U_x
    U = min(U_x, U_y)
    # 正态近似（nx, ny >= 10 时）
    mu = nx * ny / 2
    # 修正 ties 的方差
    n = nx + ny
    # tie correction
    from collections import Counter
    values = [c[0] for c in combined]
    tie_counts = Counter(values)
    tie_term = sum(t * (t * t - 1) for t in tie_counts.values()) / 12
    sigma = math.sqrt((nx * ny / 12) * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 1)
    if sigma == 0:
        return U, 1.0
    # 连续性修正
    z = (U - mu + 0.5) / sigma if U < mu else (U - mu - 0.5) / sigma
    # 双侧 p-value（标准正态 CDF）
    p = 2 * (1 - _norm_cdf(abs(z)))
    return U, p


def _norm_cdf(z: float) -> float:
    """标准正态 CDF（用 erf 近似）。"""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def cliffs_delta(x: list[float], y: list[float]) -> float:
    """Cliff's delta 效应量。

    delta = (#(x_i > y_j) - #(x_i < y_j)) / (nx * ny)
    范围 [-1, 1]，0 表示无差异，|delta|>0.474 为大效应。
    """
    nx, ny = len(x), len(y)
    if nx == 0 or ny == 0:
        return 0.0
    gt = sum(1 for xi in x for yj in y if xi > yj)
    lt = sum(1 for xi in x for yj in y if xi < yj)
    return (gt - lt) / (nx * ny)


def auc_from_u(x: list[float], y: list[float], U: float) -> float:
    """AUC = U_x / (nx * ny)，U_x 是 x 组的 U 统计量。

    注意：mann_whitney_u 返回的是 min(U_x, U_y)，需重新算 U_x。
    """
    nx, ny = len(x), len(y)
    if nx == 0 or ny == 0:
        return 0.5
    # 重新计算 U_x（x 组的秩和 - nx*(nx+1)/2）
    combined = [(v, 0) for v in x] + [(v, 1) for v in y]
    combined.sort(key=lambda t: t[0])
    ranks = [0.0] * (nx + ny)
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    R_x = sum(ranks[i] for i in range(nx + ny) if combined[i][1] == 0)
    U_x = R_x - nx * (nx + 1) / 2
    return U_x / (nx * ny)


def youden_optimal_threshold(profitable_vals: list[float], losing_vals: list[float]) -> tuple[float, float, float]:
    """用 Youden's J 统计量找 ROC 最优切点。

    J = sensitivity + specificity - 1
    遍历所有候选阈值（取所有唯一值的中点），找 J 最大的。

    返回 (best_threshold, best_j, auc)
    """
    # 所有候选阈值
    all_vals = sorted(set(profitable_vals + losing_vals))
    if len(all_vals) < 2:
        return all_vals[0] if all_vals else 0, 0, 0.5
    thresholds = [(all_vals[i] + all_vals[i + 1]) / 2 for i in range(len(all_vals) - 1)]

    n_pos = len(profitable_vals)
    n_neg = len(losing_vals)
    best_j = -1
    best_threshold = thresholds[0]
    for t in thresholds:
        # 假设 val > t 预测为"盈利"
        tp = sum(1 for v in profitable_vals if v > t)
        fn = n_pos - tp
        tn = sum(1 for v in losing_vals if v <= t)
        fp = n_neg - tn
        sensitivity = tp / n_pos if n_pos else 0
        specificity = tn / n_neg if n_neg else 0
        j = sensitivity + specificity - 1
        if j > best_j:
            best_j = j
            best_threshold = t
    # AUC
    U, _ = mann_whitney_u(profitable_vals, losing_vals)
    auc = auc_from_u(profitable_vals, losing_vals, U)
    return best_threshold, best_j, auc


def interpret_cliffs_delta(d: float) -> str:
    """Cliff's delta 解读。"""
    ad = abs(d)
    if ad < 0.147:
        level = "可忽略 (negligible)"
    elif ad < 0.33:
        level = "小 (small)"
    elif ad < 0.474:
        level = "中 (medium)"
    else:
        level = "大 (large)"
    direction = "盈利组更高" if d > 0 else "盈利组更低" if d < 0 else "无差异"
    return f"{level}, {direction}"


def analyze_feature(name: str, profitable_vals: list[float], losing_vals: list[float]) -> dict:
    """对一个特征做完整分析。"""
    U, p = mann_whitney_u(profitable_vals, losing_vals)
    delta = cliffs_delta(profitable_vals, losing_vals)
    auc = auc_from_u(profitable_vals, losing_vals, U)
    best_threshold, best_j, _ = youden_optimal_threshold(profitable_vals, losing_vals)

    print(f"\n{'=' * 78}")
    print(f"特征: {name}")
    print(f"{'=' * 78}")
    print(f"  盈利组 n={len(profitable_vals)}  亏损组 n={len(losing_vals)}")
    if profitable_vals:
        from statistics import mean, median
        print(f"  盈利组: mean={mean(profitable_vals):.3f}  median={median(profitable_vals):.3f}  min={min(profitable_vals):.3f}  max={max(profitable_vals):.3f}")
    if losing_vals:
        print(f"  亏损组: mean={mean(losing_vals):.3f}  median={median(losing_vals):.3f}  min={min(losing_vals):.3f}  max={max(losing_vals):.3f}")
    print(f"\n  Mann-Whitney U 检验:")
    print(f"    U 统计量 = {U:.1f}")
    print(f"    p-value  = {p:.6f}  {'(显著 p<0.05)' if p < 0.05 else '(不显著 p>=0.05)'}")
    print(f"\n  效应量:")
    print(f"    Cliff's delta = {delta:+.4f}  → {interpret_cliffs_delta(delta)}")
    print(f"    AUC           = {auc:.4f}  → {'有区分力 (AUC>0.56)' if auc > 0.56 else '区分力弱 (AUC<=0.56)'}")
    print(f"\n  ROC 最优切点 (Youden's J):")
    print(f"    阈值 = {best_threshold:.4f}")
    print(f"    J    = {best_j:.4f}  (J=0 表示无区分力，J=1 表示完美区分)")
    print(f"    → 若 val > {best_threshold:.4f} 视为'盈利'预测")

    return {
        "feature": name,
        "n_profitable": len(profitable_vals),
        "n_losing": len(losing_vals),
        "mann_whitney_u": U,
        "p_value": p,
        "significant": p < 0.05,
        "cliffs_delta": delta,
        "delta_interpretation": interpret_cliffs_delta(delta),
        "auc": auc,
        "youden_threshold": best_threshold,
        "youden_j": best_j,
    }


def main():
    print("=" * 78)
    print("步骤1 — 盈亏分组统计检验 (Mann-Whitney U + Cliff's delta + AUC + Youden)")
    print("=" * 78)

    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    profitable = data.get("profitable_per_stock", [])
    losing = data.get("losing_per_stock", [])

    print(f"\n盈利组: {len(profitable)} 只 | 亏损组: {len(losing)} 只")

    # 提取特征值（过滤 None）
    def extract(feats, key):
        return [f[key] for f in feats if f.get(key) is not None]

    p_adx = extract(profitable, "adx_mean")
    l_adx = extract(losing, "adx_mean")
    p_amp = extract(profitable, "amplitude_mean")
    l_amp = extract(losing, "amplitude_mean")
    p_vol = extract(profitable, "volume_mean")
    l_vol = extract(losing, "volume_mean")
    p_trend = extract(profitable, "adx_trend_ratio")
    l_trend = extract(losing, "adx_trend_ratio")

    results = {}
    results["adx_mean"] = analyze_feature("ADX均值", p_adx, l_adx)
    results["amplitude_mean"] = analyze_feature("日均振幅 (%)", p_amp, l_amp)
    results["volume_mean"] = analyze_feature("日均成交量", p_vol, l_vol)
    results["adx_trend_ratio"] = analyze_feature("趋势日占比 (ADX>25)", p_trend, l_trend)

    # ── 综合结论 ──
    print("\n" + "=" * 78)
    print("综合结论")
    print("=" * 78)
    for name, r in results.items():
        sig = "显著" if r["significant"] else "不显著"
        print(f"  {name:<24}  p={r['p_value']:.4f}  {sig:<5}  delta={r['cliffs_delta']:+.3f}  AUC={r['auc']:.3f}  Youden阈值={r['youden_threshold']:.3f} (J={r['youden_j']:.3f})")

    print("\n判断:")
    amp_r = results["amplitude_mean"]
    if amp_r["significant"] and abs(amp_r["cliffs_delta"]) > 0.33 and amp_r["auc"] > 0.65:
        print(f"  → 振幅差异统计显著且效应量达中/大，AUC={amp_r['auc']:.3f} 有区分力")
        print(f"  → Youden 最优切点 = {amp_r['youden_threshold']:.2f}% (J={amp_r['youden_j']:.3f})")
        print(f"  → 可进入步骤2样本外验证")
    else:
        print(f"  → 振幅差异未达显著+效应量+AUC 三重门槛，步骤2验证前需重新评估")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结构化结果 -> {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
