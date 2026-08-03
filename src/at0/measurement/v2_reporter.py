"""
Measurement V2 — 报告生成器
==========================
生成 JSON + CSV + HTML 三种格式的 V2 测量报告。

JSON: 完整数据（供后续脚本消费）
CSV:  每笔交易一行（供 Excel 分析）
HTML: 可视化报告（供人工审阅）

用法:
    from at0.measurement.v2_reporter import generate_v2_report
    generate_v2_report(metrics, output_dir)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# CSV 报告（每笔交易一行）
# ═══════════════════════════════════════════════════════════════

def _flatten_trade(trade: dict) -> dict:
    """把嵌套的 per_trade 记录展平为单行（CSV 友好）。"""
    row = {
        "date": trade.get("date", ""),
        "direction": trade.get("direction", ""),
        "open_price": trade.get("open_price", 0.0),
        "close_price": trade.get("close_price", 0.0),
        "pnl": trade.get("pnl", 0.0),
        "holding_bars": trade.get("holding_bars", 0),
    }
    # 展平各指标的子字段
    for metric_name in ("capture_efficiency", "entry_quality", "exit_delay",
                         "remaining_move", "wave_capture", "excursion",
                         "trend_quality"):
        m = trade.get(metric_name, {})
        if isinstance(m, dict):
            for k, v in m.items():
                row[f"{metric_name}.{k}"] = v
    # Stage M0 引擎上下文（用于离线归因）
    ctx = trade.get("engine_ctx") or {}
    if isinstance(ctx, dict) and ctx:
        row["engine.alpha"] = ctx.get("alpha")
        row["engine.expected_rr"] = ctx.get("expected_rr")
        row["engine.active"] = ctx.get("engines_active")
        for k, v in (ctx.get("sub_scores") or {}).items():
            row[f"engine.sub.{k}"] = v
    return row


def write_csv_report(metrics: dict, output_path: Path) -> None:
    """生成 CSV 报告（所有股票的所有交易合并到一个 CSV）。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    for stock in metrics.get("per_stock", []):
        code = stock.get("code", "?")
        for trade in stock.get("per_trade", []):
            row = _flatten_trade(trade)
            row["code"] = code
            all_rows.append(row)

    if not all_rows:
        output_path.write_text("", encoding="utf-8")
        return

    # 收集所有字段（保持顺序）
    fieldnames = ["code", "date", "direction", "open_price", "close_price",
                  "pnl", "holding_bars"]
    for row in all_rows:
        for k in row.keys():
            if k not in fieldnames:
                fieldnames.append(k)

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)


# ═══════════════════════════════════════════════════════════════
# HTML 报告（可视化）
# ═══════════════════════════════════════════════════════════════

def _num(v, default: float = 0.0) -> float:
    """把可能为 None / 非数值的指标安全转成 float（仅用于展示层）。

    注意：这个 default 只影响渲染，不参与任何统计口径 ——
    统计侧必须让 None 缺席，绝不能补 0，否则会把"没数据"算成"表现为0"。
    """
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _fmt_pct(v: float, decimals: int = 2) -> str:
    """格式化百分比。"""
    return f"{v * 100:.{decimals}f}%" if v else "0.00%"


def _fmt_num(v: float, decimals: int = 2) -> str:
    """格式化数值。"""
    return f"{v:.{decimals}f}" if v else "0.00"


def _generate_overall_html(overall: dict) -> str:
    """生成总体指标 HTML 表格。"""
    pairs = overall.get("total_pairs", 0)
    ce = overall.get("avg_ce", 0)
    ed = overall.get("avg_entry_delay_bars", 0)
    xd = overall.get("avg_exit_delay_bars", 0)
    eg = overall.get("avg_execution_gain_pct", 0)
    rm = overall.get("avg_remaining_move_pct", 0)
    wn = overall.get("avg_wave_number", 0)
    wc = overall.get("avg_wave_capture_pct", 0)
    ol = overall.get("total_opportunity_lost", 0)
    ol_surge = overall.get("avg_opportunity_lost_surge_pct", 0)

    return f"""
    <div class="card">
      <h2>Overall Metrics</h2>
      <table class="metric-table">
        <tr><th>指标</th><th>值</th><th>说明</th></tr>
        <tr><td>配对交易数</td><td class="value">{pairs}</td><td>已配对的交易对数</td></tr>
        <tr><td>平均 CE</td><td class="value {'good' if ce > 0.4 else 'bad' if ce < 0.2 else ''}">{_fmt_pct(ce)}</td><td>捕获效率，目标 55%+</td></tr>
        <tr><td>平均 Entry Delay</td><td class="value {'good' if ed < 5 else 'bad' if ed > 15 else ''}">{_fmt_num(ed, 1)} K</td><td>入场延迟K线数，目标 < 3</td></tr>
        <tr><td>平均 Exit Delay</td><td class="value">{_fmt_num(xd, 1)} K</td><td>退出延迟K线数（正=卖晚，负=卖早）</td></tr>
        <tr><td>平均 Exec Gain</td><td class="value {'bad' if eg > 1 else ''}">{_fmt_num(eg, 2)}%</td><td>偏离理想价格%，正值=买晚了</td></tr>
        <tr><td>平均 Remaining Move</td><td class="value">{_fmt_num(rm, 2)}%</td><td>开仓后最大可获利幅度</td></tr>
        <tr><td>平均 Wave Number</td><td class="value {'good' if wn < 2 else 'bad' if wn >= 3 else ''}">{_fmt_num(wn, 1)}</td><td>开仓波数，1=最优（第一波）</td></tr>
        <tr><td>平均 Wave Capture</td><td class="value {'good' if wc > 0.6 else 'bad' if wc < 0.3 else ''}">{_fmt_pct(wc)}</td><td>波浪捕获率，越高越好</td></tr>
        <tr><td>Opportunity Lost</td><td class="value {'bad' if ol > 10 else ''}">{ol} 次</td><td>未交易的上涨机会数</td></tr>
        <tr><td>OL 平均涨幅</td><td class="value">{_fmt_num(ol_surge, 2)}%</td><td>错过机会的平均涨幅</td></tr>
      </table>
    </div>"""


_DIST_LABELS = [
    ("ce", "Capture Efficiency", "ratio"),
    ("entry_delay_bars", "Entry Delay (K)", "num"),
    ("exit_delay_bars", "Exit Delay (K)", "num"),
    ("execution_gain_pct", "Execution Gain (%)", "num"),
    ("remaining_move_pct", "Remaining Move (%)", "num"),
    ("mae_pct", "MAE (%)", "num"),
    ("mfe_pct", "MFE (%)", "num"),
    ("trend_quality", "Trend Quality", "ratio"),
    ("wave_number", "Wave Number", "num"),
    ("wave_capture_pct", "Wave Capture", "ratio"),
]


def _generate_distribution_html(distributions: dict) -> str:
    """分布统计表 —— 均值 + 中位数 + IQR，避免被极端交易带偏。"""
    if not distributions:
        return ""
    rows = []
    for key, label, kind in _DIST_LABELS:
        d = distributions.get(key)
        if not d or not d.get("n"):
            continue
        fm = (lambda v: _fmt_pct(v)) if kind == "ratio" else (lambda v: _fmt_num(v, 2))
        rows.append(f"""
        <tr>
          <td style="text-align:left">{label}</td>
          <td>{d['n']}</td>
          <td class="value">{fm(d['mean'])}</td>
          <td class="value">{fm(d['median'])}</td>
          <td>{fm(d['p25'])}</td>
          <td>{fm(d['p75'])}</td>
          <td>{_fmt_num(d['std'], 2)}</td>
        </tr>""")
    if not rows:
        return ""
    return f"""
    <div class="card">
      <h2>Distribution (全池配对交易)</h2>
      <p class="note">均值易被极端交易带偏，Stage 验收以 median + IQR 为准。</p>
      <table>
        <tr><th>指标</th><th>N</th><th>Mean</th><th>Median</th>
            <th>P25</th><th>P75</th><th>Std</th></tr>
        {''.join(rows)}
      </table>
    </div>"""


def _generate_quality_html(tq: dict) -> str:
    """结果指标区块（与过程指标同源同口径）。"""
    if not tq or not tq.get("n"):
        return ""
    pf = tq.get("profit_factor", 0)
    wr = tq.get("win_rate", 0)
    net = tq.get("net_pnl", 0)
    return f"""
    <div class="card">
      <h2>Trade Quality (结果指标)</h2>
      <table class="metric-table">
        <tr><th>指标</th><th>值</th><th>说明</th></tr>
        <tr><td>配对交易数</td><td class="value">{tq['n']}</td><td>参与统计的样本量</td></tr>
        <tr><td>胜率</td><td class="value {'good' if wr > 0.55 else ''}">{_fmt_pct(wr)}</td><td>盈利交易占比</td></tr>
        <tr><td>Profit Factor</td><td class="value {'good' if pf > 1.3 else 'bad' if pf < 1 else ''}">{_fmt_num(pf, 2)}</td><td>总盈利 / 总亏损</td></tr>
        <tr><td>盈亏比</td><td class="value">{_fmt_num(tq.get('payoff_ratio', 0), 2)}</td><td>平均盈利 / 平均亏损</td></tr>
        <tr><td>期望</td><td class="value {'good' if tq.get('expectancy', 0) > 0 else 'bad'}">{_fmt_num(tq.get('expectancy', 0), 2)}</td><td>每笔平均盈亏（元）</td></tr>
        <tr><td>净盈亏</td><td class="value {'good' if net > 0 else 'bad'}">{net:+.2f}</td><td>配对口径净盈亏（元）</td></tr>
      </table>
    </div>"""


def _generate_attribution_html(attr: dict) -> str:
    """引擎归因区块 —— 回答"引擎的分到底有没有预测力"。"""
    if not attr or not attr.get("available"):
        return """
    <div class="card">
      <h2>Engine Attribution</h2>
      <p class="note">本次回测未记录 engine_ctx（Trade Event Logger 未启用或 report 为旧版本），
      无法做引擎归因。请用带 Stage M0 日志的回测重跑。</p>
    </div>"""

    active = attr.get("engines_active")
    conf = attr.get("confidence_accuracy", {})
    exp = attr.get("expected_accuracy", {})
    sup = attr.get("support_accuracy", {})
    wav = attr.get("wave_accuracy", {})

    def _ic_row(label: str, block: dict, key: str, expect: str) -> str:
        ic = block.get(key, 0)
        n = block.get("n", 0)
        ok = (ic > 0.05) if expect == "正" else (ic < -0.05)
        cls = "good" if ok else ("bad" if abs(ic) < 0.05 else "")
        return (f"<tr><td style='text-align:left'>{label}</td><td>{n}</td>"
                f"<td class='value {cls}'>{ic:+.4f}</td><td>期望方向：{expect}</td></tr>")

    buckets = conf.get("buckets") or []
    bucket_rows = "".join(
        f"<tr><td>Q{b['bucket']}</td><td>{b['n']}</td>"
        f"<td>{b['score_lo']:.1f} ~ {b['score_hi']:.1f}</td>"
        f"<td>{_fmt_pct(b['win_rate'])}</td><td>{b['avg_pnl']:+.2f}</td>"
        f"<td>{_fmt_pct(b['avg_ce'])}</td></tr>"
        for b in buckets
    )
    bucket_html = f"""
      <h3 style="margin-top:18px;font-size:14px;color:#16213e">Alpha 分档单调性</h3>
      <table>
        <tr><th>分档</th><th>N</th><th>Alpha 区间</th><th>胜率</th><th>平均盈亏</th><th>平均 CE</th></tr>
        {bucket_rows}
      </table>
      <p class="note">若 Q1→Q4 胜率 / 平均盈亏不单调递增，说明 Alpha 评分没有区分度。</p>""" if bucket_rows else ""

    warn = "" if active else """
      <p class="note" style="color:#e74c3c">警告：engines_active=False —— 本次回测未启用
      V3 Engine 通路（Wave/Support/ExpectedMove/Regime/Risk 均为占位打分），
      归因结果不代表 Engine 的真实能力。</p>"""

    return f"""
    <div class="card">
      <h2>Engine Attribution</h2>{warn}
      <table>
        <tr><th>归因项</th><th>N</th><th>RankIC</th><th>判读</th></tr>
        {_ic_row("Confidence Accuracy (Alpha → PnL)", conf, "rank_ic_alpha_vs_pnl", "正")}
        {_ic_row("Expected Accuracy (RR → Remaining Move)", exp, "rank_ic_rr_vs_remaining_move", "正")}
        {_ic_row("Support Accuracy (Support 分 → MAE)", sup, "rank_ic_support_vs_mae", "负")}
        {_ic_row("Wave Accuracy (Wave 分 → CE)", wav, "rank_ic_wave_vs_ce", "正")}
      </table>
      {bucket_html}
    </div>"""


def _generate_per_stock_html(per_stock: list[dict]) -> str:
    """生成每股指标 HTML 表格。"""
    rows = []
    for s in per_stock:
        code = s.get("code", "?")
        summary = s.get("summary", {})
        ol = s.get("opportunity_lost", {})
        pairs = s.get("pair_count", 0)
        ce = summary.get("avg_ce", 0)
        ed = summary.get("avg_entry_delay_bars", 0)
        xd = summary.get("avg_exit_delay_bars", 0)
        rm = summary.get("avg_remaining_move_pct", 0)
        wn = summary.get("avg_wave_number", 0)
        ol_count = ol.get("total_lost", 0)

        rows.append(f"""
        <tr>
          <td>{code}</td>
          <td>{pairs}</td>
          <td class="{'good' if ce > 0.4 else 'bad' if ce < 0.2 else ''}">{_fmt_pct(ce)}</td>
          <td class="{'good' if ed < 5 else 'bad' if ed > 15 else ''}">{_fmt_num(ed, 1)}</td>
          <td>{_fmt_num(xd, 1)}</td>
          <td>{_fmt_num(rm, 2)}%</td>
          <td class="{'good' if wn < 2 else 'bad' if wn >= 3 else ''}">{_fmt_num(wn, 1)}</td>
          <td class="{'bad' if ol_count > 5 else ''}">{ol_count}</td>
        </tr>""")

    return f"""
    <div class="card">
      <h2>Per-Stock Breakdown</h2>
      <table class="stock-table">
        <tr><th>Code</th><th>配对数</th><th>CE</th><th>Entry Delay</th>
            <th>Exit Delay</th><th>Remaining Move</th><th>Wave #</th>
            <th>Opp. Lost</th></tr>
        {''.join(rows)}
      </table>
    </div>"""


def _generate_trade_detail_html(per_stock: list[dict], max_trades: int = 50) -> str:
    """生成每笔交易明细 HTML（限制行数避免过大）。"""
    all_trades = []
    for s in per_stock:
        code = s.get("code", "?")
        for t in s.get("per_trade", []):
            t["code"] = code
            all_trades.append(t)

    # 按日期排序，取前 max_trades 笔
    all_trades.sort(key=lambda t: (t.get("date", ""), t.get("code", "")))
    all_trades = all_trades[:max_trades]

    rows = []
    for t in all_trades:
        # 指标不可用时为 None（哨兵），显示 "—" 而非伪造成 0 / -1
        ce = _num(t.get("capture_efficiency", {}).get("ce"))
        ed = t.get("entry_quality", {}).get("entry_delay_bars")
        eg = _num(t.get("entry_quality", {}).get("execution_gain_pct"))
        xd = t.get("exit_delay", {}).get("exit_delay_bars")
        rm = _num(t.get("remaining_move", {}).get("remaining_move_pct"))
        wn = t.get("wave_capture", {}).get("wave_number")
        wt = t.get("wave_capture", {}).get("wave_total")
        wave_txt = "—" if wn is None or wt is None else f"{wn}/{wt}"
        pnl = _num(t.get("pnl"))
        ed_cls = "" if ed is None else ("good" if 0 <= ed < 5 else "bad" if ed > 15 else "")
        ed_txt = "—" if ed is None else str(ed)
        xd_txt = "—" if xd is None else str(xd)

        rows.append(f"""
        <tr>
          <td>{t.get('code', '')}</td>
          <td>{t.get('date', '')}</td>
          <td>{t.get('direction', '')}</td>
          <td>{t.get('open_price', 0):.4f}</td>
          <td>{t.get('close_price', 0):.4f}</td>
          <td class="{'good' if pnl > 0 else 'bad'}">{pnl:+.2f}</td>
          <td>{t.get('holding_bars', 0)}</td>
          <td class="{'good' if ce > 0.4 else 'bad' if ce < 0.2 else ''}">{_fmt_pct(ce)}</td>
          <td class="{ed_cls}">{ed_txt}</td>
          <td>{eg:+.2f}%</td>
          <td>{xd_txt}</td>
          <td>{rm:.2f}%</td>
          <td>{wave_txt}</td>
        </tr>""")

    note = f"<p class='note'>显示前 {len(all_trades)} 笔交易（按日期排序）</p>" if len(all_trades) == max_trades else ""

    return f"""
    <div class="card">
      <h2>Trade Details</h2>
      {note}
      <table class="trade-table">
        <tr><th>Code</th><th>Date</th><th>Dir</th><th>Open</th><th>Close</th>
            <th>PnL</th><th>Bars</th><th>CE</th><th>ED</th><th>EG</th>
            <th>XD</th><th>RM</th><th>Wave</th></tr>
        {''.join(rows)}
      </table>
    </div>"""


def write_html_report(metrics: dict, output_path: Path) -> None:
    """生成 HTML 可视化报告。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tag = metrics.get("tag", "?")
    start = metrics.get("start", "")
    end = metrics.get("end", "")
    overall = metrics.get("overall", {})
    per_stock = metrics.get("per_stock", [])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>V2 Measurement Report — {tag}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 20px; background: #f5f5f5; color: #333;
    }}
    h1 {{ color: #1a1a2e; border-bottom: 2px solid #16213e; padding-bottom: 10px; }}
    h2 {{ color: #16213e; margin-top: 0; }}
    .header {{ margin-bottom: 20px; }}
    .header span {{ background: #16213e; color: white; padding: 4px 12px;
                    border-radius: 4px; margin-right: 10px; font-size: 14px; }}
    .card {{ background: white; border-radius: 8px; padding: 20px;
             margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: center; }}
    th {{ background: #16213e; color: white; font-weight: 600; }}
    tr:nth-child(even) {{ background: #f9f9f9; }}
    .value {{ font-weight: 600; font-size: 14px; }}
    .good {{ color: #27ae60; }}
    .bad {{ color: #e74c3c; }}
    .note {{ color: #7f8c8d; font-size: 12px; font-style: italic; }}
    .metric-table td:nth-child(3) {{ text-align: left; color: #666; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>V2 Measurement Report</h1>
    <span>Tag: {tag}</span>
    <span>区间: {start} ~ {end}</span>
    <span>股票数: {overall.get('stocks', 0)}</span>
  </div>

  {_generate_overall_html(overall)}
  {_generate_quality_html(metrics.get("trade_quality", {}))}
  {_generate_distribution_html(metrics.get("distributions", {}))}
  {_generate_attribution_html(metrics.get("attribution", {}))}
  {_generate_per_stock_html(per_stock)}
  {_generate_trade_detail_html(per_stock)}

</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


# ═══════════════════════════════════════════════════════════════
# 统一报告生成入口
# ═══════════════════════════════════════════════════════════════

def generate_v2_report(
    metrics: dict,
    output_dir: Path | str,
    prefix: str = "v2_report",
) -> dict:
    """生成完整的 V2 报告（JSON + CSV + HTML）。

    :param metrics: compute_v2_metrics_batch 的返回值
    :param output_dir: 输出目录
    :param prefix: 文件名前缀
    :return: {"json": path, "csv": path, "html": path}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tag = metrics.get("tag", "unknown")

    json_path = output_dir / f"{prefix}_{tag}.json"
    csv_path = output_dir / f"{prefix}_{tag}.csv"
    html_path = output_dir / f"{prefix}_{tag}.html"

    # JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # CSV
    write_csv_report(metrics, csv_path)

    # HTML
    write_html_report(metrics, html_path)

    return {"json": str(json_path), "csv": str(csv_path), "html": str(html_path)}
