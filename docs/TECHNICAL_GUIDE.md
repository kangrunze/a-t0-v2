# A-T0 技术指南

> L5 T+0 日内做T策略 — 运行手册与技术参考

本文档覆盖项目运行方式（回测 / 实时监控 / 批量优化）、参数体系、止损安全网机制及输出产物。

---

## 目录

- [1. 环境准备](#1-环境准备)
- [2. 统一 CLI 入口](#2-统一-cli-入口)
- [3. 单股回测（backtest）](#3-单股回测backtest)
- [4. 批量回测与优化（optimize）](#4-批量回测与优化optimize)
- [5. 实时监控（paper_monitor）](#5-实时监控paper_monitor)
- [6. 运行示例](#6-运行示例)
- [7. 参数体系](#7-参数体系)
- [8. 止损安全网（实盘接入）](#8-止损安全网实盘接入)
- [9. 输出产物](#9-输出产物)
- [10. 模块架构](#10-模块架构)

---

## 1. 环境准备

### 依赖

- Python 3.10+
- 依赖包：见各 import（无独立 requirements.txt，按需安装：`mootdx`、`pandas`、`pyyaml` 等）

### 目录结构

```
a-t0-v2/
├── config/
│   ├── thresholds.yaml          # 集中配置（信号/风控/成本/筛选/回测/测量）
│   ├── thresholds.yaml.bak      # 上一版本备份（gitignore）
│   └── overlays/
│       ├── paper.yaml           # 模拟盘 overlay（悲观成本+保守仓位）
│       └── research.yaml        # 研究阶段 overlay（base 成本）
├── docs/                        # 技术文档
├── scripts/                     # 工具脚本（回测/优化/数据下载/筛选）
├── src/
│   └── at0/
        ├── cli.py               # 统一 CLI 入口
        ├── strategy.py          # 信号引擎（4项规则投票，趋势跟随）
        ├── strategy_alpha.py    # P2 连续评分（5维加权，默认关闭）
        ├── features.py          # 指标计算层
        ├── risk.py              # 风控 + 成本模型 + 敞口策略
        ├── execution.py         # 交易生命周期 + 持仓追踪 + open_legs 持久化
        ├── backtest.py          # 回测引擎
        ├── screener.py          # 候选筛选（振幅/成交额/一字板/捕获空间）
        ├── regime.py            # regime 分类（@deprecated，Stage E 验证未通过）
        ├── config.py            # 参数加载器（yaml → dataclass）
        ├── paths.py             # 路径常量（支持 AT0_DATA_DIR 环境变量）
        ├── data.py              # 数据源适配（mootdx/westock/baostock）
        ├── reports.py           # HTML 报告生成
        ├── logging_utils.py     # 日志工具
        └── measurement/         # 测量层（.pyc 形式，TSD 加密源码）
├── archive/                     # 已归档的阶段性诊断/实验脚本
└── .gitignore
```

**数据目录**（不在代码仓库内，通过 `config/thresholds.yaml` 的 `data.root` 指定）：

```
D:\project\data\
├── zz500_5min/          # 中证 500 成分股 5min K线（每只一个 JSON）
├── minute_local/        # 分钟线（按股票分目录，每日一 JSON）
├── multi_day_cache/     # 多日数据缓存
├── positions.json       # 持仓状态（底仓/T+1锁定/今日T状态）
├── live_open_legs.json  # 实盘 open_legs 状态（止损监控用）
└── market_gate.json     # 市场层门控快照
```

---

## 2. 统一 CLI 入口

所有功能通过 `python -m at0.cli <子命令>` 调用：

```
python -m at0.cli <backtest|optimize|paper_monitor|validate_data> [args...]
```

| 子命令 | 说明 | 原脚本 |
|---|---|---|
| `backtest` | 单股多日回测 | run_backtest.py |
| `optimize` | 批量多股票回测与汇总 | batch_backtest.py |
| `paper_monitor` | L5 实盘 T+0 监控（research_only） | l5_monitor.py |
| `validate_data` | 数据校验（预留，暂未实现） | — |

需 `cd src` 或设置 `PYTHONPATH=src`：

```bash
# Windows PowerShell
$env:PYTHONPATH = "src"
python -m at0.cli --help
```

---

## 3. 单股回测（backtest）

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--code` | 600000 | 股票代码 |
| `--start` | （空） | 起始日期 YYYY-MM-DD |
| `--end` | （空） | 结束日期 YYYY-MM-DD |
| `--days` | 7 | 回溯天数（无 `--start` 时用，从 `--end` 倒推） |
| `--source` | auto | 数据源：auto/mootdx/westock/baostock/eastmoney |
| `--base-shares` | 3000 | 底仓股数 |
| `--avg-cost` | （空） | 底仓成本价（空=取首日 prev_close） |

### 数据源说明

| 源 | 实时 | 历史 | 备注 |
|---|---|---|---|
| `auto` | ✓ | ✓ | 自动回退（mootdx→westock→baostock） |
| `mootdx` | ✓ | ✓ | 通达信，优先用 |
| `westock` | ✓ | 部分 | 需设 `WESTOCK_DIR` 环境变量 |
| `baostock` | ✗ | ✓ | 历史回测主力，免 IP 封锁 |
| `eastmoney` | ✓ | ✗ | 免依赖，仅实时 |

---

## 4. 批量回测与优化（optimize）

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--start` | 2026-06-22 | 起始日期 |
| `--end` | 2026-07-22 | 结束日期 |
| `--source` | baostock | 数据源（批量默认历史源） |
| `--codes` | （空） | 逗号分隔代码（覆盖候选池） |
| `--base-shares` | 3000 | 底仓股数 |

默认从 `outputs/backtest/candidate_pool.json` 读取候选池，输出汇总到 `outputs/backtest/batch_summary.{json,html}`。

---

## 5. 实时监控（paper_monitor）

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--source` | auto | 数据源 |
| `--date` | （空） | YYYY-MM-DD（空=实时今日，传=历史回放） |
| `--demo` | false | 测试模式：忽略交易时段限制 |
| `--eod-check` | false | 仅执行尾盘平衡检查 |

### 运行模式

- **实时模式**（不传 `--date`）：检查交易时段 → 加载持仓 → 逐只监控 → 输出信号
- **历史回放**（传 `--date`）：不受交易时段限制，回放指定交易日
- **尾盘检查**（`--eod-check`）：检查所有持仓的未配对敞口并输出平衡建议

### 输出类型

监控结果 `result["action"]` 有三种：

| action | 含义 | 输出标签 |
|---|---|---|
| `signal` | 策略信号触发（正T卖出/反T买入） | `动作: ...` |
| `stop_loss` | 止损/超时平仓触发（价格兜底） | `[止损] ...` |
| `none` | 无信号/风控拒绝/数据不足 | 不输出 |

---

## 6. 运行示例

### 示例 1：单股 7 天回测（默认参数）

```bash
cd d:\project\a-t0-v2
$env:PYTHONPATH = "src"
python -m at0.cli backtest --code 600000 --days 7
```

输出：
```
[run_backtest] 拉取 600000 2026-07-17~2026-07-24 (source=auto) ...
[run_backtest] 数据频率=1min, 约 240 根/天
[run_backtest] avg_cost 未指定，取首日 prev_close=10.2340
[run_backtest] warmup_bars=30, eod_check_bar_idx=230

[run_backtest] 买卖触发记录 -> outputs/backtest/600000_..._trades.json  (N 笔)
[run_backtest] 完整报告     -> outputs/backtest/600000_..._report.json
[run_backtest] HTML 可视化  -> outputs/backtest/600000_..._report.html
[run_backtest] run_id       -> <hash>
```

### 示例 2：指定日期区间 + 底仓成本

```bash
python -m at0.cli backtest --code 600026 --start 2026-06-22 --end 2026-07-22 --source baostock --base-shares 5000 --avg-cost 15.8
```

### 示例 3：批量回测（候选池）

```bash
python -m at0.cli optimize --start 2026-06-22 --end 2026-07-22 --source baostock
```

输出汇总：
```
[batch] run_id    -> <hash>
→ outputs/backtest/batch_summary.json
→ outputs/backtest/batch_summary.html
```

### 示例 4：实时监控（交易时段内）

```bash
python -m at0.cli paper_monitor --source auto
```

输出（research_only）：
```
L5 日内做T信号提醒｜07-24 14:35 — research_only
说明：这是研究/监控信号，不是自动交易或立即执行指令。

- 600000 (银行)
  动作: 正T-卖出 500 股 @ 10.85 — 参考价 10.72
  信号: reduce (reduce=3/4, add=0/4)
  规则: vwap_dev_atr | bb_breakout | kdj_overbought
- 600026 (航运)
  [止损] 止损平仓 sell 300股 @ 11.20 (开仓11.35 max_fav=0.0450 max_adv=0.0120 hold=3bars)
```

### 示例 5：非交易时段测试（demo 模式）

```bash
python -m at0.cli paper_monitor --demo --source eastmoney
```

### 示例 6：历史回放某交易日

```bash
python -m at0.cli paper_monitor --date 2026-07-23 --source baostock
```

### 示例 7：尾盘平衡检查

```bash
python -m at0.cli paper_monitor --eod-check
```

输出 JSON：
```json
[
  {"code": "600000", "status": "net_reduce", "net_position_delta": -500, "action": "需买回500股平衡"}
]
```

### 示例 8：模拟盘 overlay（悲观成本 + 保守仓位）

```bash
$env:AT0_OVERLAY = "paper"
python -m at0.cli paper_monitor
```

参数合并：`thresholds.yaml`（base）→ `paper.yaml`（overlay 覆盖成本场景为 pessimistic、仓位上限 15%）。

---

## 7. 参数体系

### 配置加载流程

```
thresholds.yaml (base)  ──┐
                          ├─→ config_loader ─→ dataclass 参数对象
overlay (paper/research) ─┘
```

- 基础参数在 [thresholds.yaml](file:///d:/project/a-t0-v2/config/thresholds.yaml)
- overlay 仅覆盖部分字段（`cost_model.scenario` / `risk.max_position_pct` 等）
- dataclass 默认值作为最后兜底（yaml 缺失时回退）

### 关键参数表

#### 信号参数（[thresholds.yaml signal 段](file:///d:/project/a-t0-v2/config/thresholds.yaml)）

| 参数 | 值 | 说明 |
|---|---|---|
| `min_rules_to_trigger` | 3 | 4项规则中至少3项满足才触发 |
| `vwap_dev_atr_multiplier` | 0.8 | VWAP偏离 ≥ 0.8×ATR/VWAP |
| `bb_period` / `bb_std` | 20 / 2.0 | 布林带周期/标准差倍数 |
| `kdj_overbought` / `kdj_oversold` | 80 / 20 | KDJ超买超卖（主触发） |
| `rsi_overbought` / `rsi_oversold` | 70 / 30 | RSI辅助确认 |
| `mfi_overbought` / `mfi_oversold` | 80 / 20 | 资金面超买超卖 |
| `vol_ratio_shrink_threshold` | 0.8 | 量比缩量阈值 |
| `cooldown_bars` | 3 | 信号触发后冷却 K 线数 |

#### 风控参数（[thresholds.yaml risk 段](file:///d:/project/a-t0-v2/config/thresholds.yaml)）

| 参数 | 值 | 说明 |
|---|---|---|
| `max_t_size_ratio` | 0.25 | 单次T仓位 ≤ 底仓25% |
| `max_t_trades_per_day` | 4 | 每日最大T次数 |
| `min_capture_spread` | 0.0072 | 最小预期捕获 0.72% |
| `min_net_expected_return` | 0.0045 | 最小净期望收益 0.45% |
| `eod_check_time` | "14:50" | 尾盘平衡检查时间 |

#### 止损参数（[thresholds.yaml backtest 段](file:///d:/project/a-t0-v2/config/thresholds.yaml)）

| 参数 | 值 | 说明 |
|---|---|---|
| `stop_loss_ratio` | 0.004 | 固定止损 0.4%（51股train/test验证：test PF 1.294→1.510，比旧值0.008显著更优） |
| `trailing_ratio` | 0.5 | 移动止盈回撤 50% |
| `trailing_activation_pct` | 0.0 | 移动止盈激活门槛（0=1tick盈利即激活；提高到0.5%的方案已被否决，胜率81%→42%崩溃） |
| `max_holding_bars` | 12 | 超时平仓（12根5min=1h） |
| `cooldown_bars` | 3 | 信号冷却（3根K线） |

#### 振幅筛选参数（[thresholds.yaml screener 段](file:///d:/project/a-t0-v2/config/thresholds.yaml)）

| 参数 | 值 | 说明 |
|---|---|---|
| `min_20d_amplitude` | 0.035 | 20 日平均振幅 ≥ 3.5% |
| `min_20d_amount` | 1.0e8 | 20 日日均成交额 ≥ 1 亿元 |
| `min_capture_spread` | 0.006 | 单笔预期捕获空间 ≥ 0.6% |
| `min_amplitude_long` | 0.0494 | 60 日日均振幅下限（Youden 最优阈值，样本外验证通过） |

> `min_amplitude_long` 主依据：F5 500 股全池验证，27 只独立股票净盈亏 +128,622，4/4 标准全通过。
> `scripts/backtest_zz500.py` 的 `--no-amplitude-filter` 可禁用此筛选（A/B 对比用）。

#### 成本模型（[thresholds.yaml cost 段](file:///d:/project/a-t0-v2/config/thresholds.yaml)）

| 场景 | 佣金 | 印花税 | 滑点 | 冲击 | 来回总成本 |
|---|---|---|---|---|---|
| optimistic | 万1 | 0.05% | 0.05% | 0 | 0.2% |
| **base** | 万1 | 0.05% | 0.1% | 0 | **0.27%** |
| pessimistic | 万1 | 0.05% | 0.2% | 0.05% | 0.5% |

> 佣金 2026-07-24 调降：万2.5 → 万1

#### 市场层门控（[thresholds.yaml market 段](file:///d:/project/a-t0-v2/config/thresholds.yaml)）

| 情绪 | 判定 | 加仓权重 | 减仓权重 |
|---|---|---|---|
| HOT | 涨停≥80 且 跌停≤10 | 1.2 | 0.8 |
| NEUTRAL | 其余 | 1.0 | 1.0 |
| COOL | 上涨占比≤30 | 0.8 | 1.1 |
| **COLD** | 涨停≤20 或 跌停≥50 | **0.5（禁加仓）** | 1.2 |

---

## 8. 止损安全网（实盘接入）

### 背景

实盘 `paper_monitor` 长期只有策略反转信号平仓，缺价格止损兜底（`check_stop_loss`/`check_expiry` 原本只在 `backtest.py` 调用）。已通过 `live_open_legs.json` 持久化层将止损接入实时监控循环。

### 数据流

```
信号触发(通过风控)
  → add_fill 录入 live_open_legs.json (FIFO配对)
       ↓ 下一轮起
每轮 monitor
  → load_live_open_legs
  → 重建 TradeLifecycle
  → update_holding(盘中 low/high)     # 更新 max_favorable/max_adverse
  → check_stop_loss                   # 移动止盈优先，未激活走固定止损
  → check_expiry                      # 超时腿标记 expired
  → save_live_open_legs               # 回写（极值已更新，止损腿已移出）
  → 触发? 输出 [止损] 提醒并 return : 继续评估新信号
```

### 止损逻辑（[execution.py check_stop_loss](file:///d:/project/a-t0-v2/src/at0/execution.py)）

```
移动止盈激活条件：
  trailing_ratio > 0
  AND max_favorable > 0
  AND max_favorable >= fill_price × trailing_activation_pct(0.5%)

激活后：
  retained = max_favorable × (1 - trailing_ratio)
  stop_line = fill_price ± retained      # 从最高点回撤 50% 触发
  盘中穿透(bar.low/high) → 成交价 = stop_line

未激活时（浮盈不足0.5%）：
  threshold = fill_price × stop_loss_ratio(0.4%)
  max_adverse >= threshold → 固定止损，成交价 = fill_price ± threshold
```

### 持久化文件格式

`{data_root}/live_open_legs.json`（数据目录下，由 `paths.LIVE_OPEN_LEGS_FILE` 指定）：

```json
{
  "600000.SH": [
    {
      "direction": "buy",
      "shares": 500,
      "fill_price": 10.85,
      "time": "10:35",
      "date": "2026-07-24",
      "status": "open",
      "holding_bars": 3,
      "max_favorable": 0.0450,
      "max_adverse": 0.0120,
      "open_vwap_dev": 0.0085,
      "stop_fill_price": null
    }
  ]
}
```

- 与回测 `TradeLifecycle.export_open_legs` 格式一致
- 跨日延续：`import_open_legs` 设 `fill_bar_idx = -prev_holding_bars`，`holding_bars` 跨日连续
- 原子读写：独立文件锁 `live_open_legs.json.lock`，避免与 `positions.json` 锁竞争

### 注意事项

1. **research_only 语义**：开仓录入是"虚拟开仓"（信号触发即记录），用户未实际跟单会产生虚假 open_leg。如实盘执行层上线，需改为真实成交后录入。
2. **跨日清理**：`live_open_legs.json` 不会自动按日清空。若某腿跨多日未配对也未超时，会持续监控（受 `max_holding_bars=12` 超时保护兜底）。如需按交易日清理，可在 `reset_today_state` 旁加清理钩子。

---

## 9. 输出产物

### 单股回测

| 文件 | 路径 | 内容 |
|---|---|---|
| trades.json | `outputs/backtest/{code}_{start}_{end}_trades.json` | 买卖触发记录 |
| report.json | `outputs/backtest/{code}_{start}_{end}_report.json` | 完整回测报告 |
| report.html | `outputs/backtest/{code}_{start}_{end}_report.html` | HTML 可视化 |
| run artifacts | `outputs/runs/{run_id}/` | 版本化参数+数据指纹+产物路径 |

### 批量回测

| 文件 | 路径 | 内容 |
|---|---|---|
| batch_summary.json | `outputs/backtest/batch_summary.json` | 多股票汇总 |
| batch_summary.html | `outputs/backtest/batch_summary.html` | HTML 汇总 |

### 实时监控

| 文件 | 路径 | 内容 |
|---|---|---|
| market_gate.json | 项目根 | 市场情绪快照（每轮落盘） |
| live_open_legs.json | `data/` | 实盘 open_legs 状态 |
| monitor.log / signal.log / trade.log | 见 logging_utils | 运行日志 |

---

## 10. 模块架构

### 调用链（实盘监控）

```
monitor_main                          # cli.py 主入口
  ├─ load_positions                   # execution.py 读 positions.json
  ├─ load_signal_params / load_risk_params / load_backtest_params / load_cost_model
  ├─ compute_market_snapshot          # features.py 市场情绪
  └─ monitor_single_stock (逐只)      # cli.py
       ├─ fetch_minute_bars           # data.py 拉分钟K线
       ├─ fetch_realtime_quote        # data.py 实时报价
       ├─ evaluate_all_signals        # strategy.py 4项规则投票
       │    └─ compute_reference_snapshot  # features.py 指标计算
       ├─ 【止损层】load_live_open_legs → update_holding → check_stop_loss → check_expiry → save
       ├─ check_risk                  # risk.py 7项风控检查
       └─ 【开仓录入】add_fill → save_live_open_legs
```

### 调用链（回测）

```
run (cli.py)
  ├─ fetch_multi_day                  # data.py 多日数据
  ├─ adapt_params_by_frequency        # 频率自适应
  └─ backtest_multi_day               # backtest.py
       └─ 逐 bar 遍历
            ├─ update_holding          # 更新极值
            ├─ check_stop_loss         # 止损/移动止盈
            ├─ check_expiry            # 超时
            ├─ evaluate_all_signals    # 信号
            ├─ check_risk / approve_signal  # 风控
            └─ add_fill / apply_t_trade     # 成交
```

### 信号生成逻辑

`evaluate_all_signals` 同时产出 reduce（减仓）和 add（加仓）信号，各含 `rules_score`（0–4）：

| 规则 | 判据 |
|---|---|
| ① VWAP偏离 | \|vwap_dev\| ≥ 0.8 × ATR/VWAP |
| ② 布林带 | 价触上轨/下轨 |
| ③ KDJ | K>80 超买 / K<20 超卖（主触发） |
| ④ 量比缩量 | 量比异常 + 缩量确认 |

- 触发门槛：`min_rules_to_trigger=3`（4项中至少3项）
- `recommendation ∈ {reduce, add, none, conflict}`

### 买卖方向映射

| recommendation | direction | 语义 |
|---|---|---|
| `reduce` | `sell` | 正T-卖出（卖底仓等回落买回） |
| `add` | `buy` | 反T-买入（买入等反弹卖出） |
