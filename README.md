# A-T0 — L5 T+0 日内做T策略

> 趋势跟随型 T+0 日内做T策略，基于中证 500 成分股 5min K线回测验证。
> 当前处于 **V4 结构优化**阶段：Measurement V2 研究测量体系（Stage M0/M1）已落地，
> V4 引擎层（Wave/Support/EM/Regime/Risk）与 Flask 版 QuantWeb 回测平台已上线。
> 默认开启 60 日振幅筛选（阈值 0.0494），过滤低波动股票，显著提升净盈亏与成本/毛利比。

---

## 项目结构

```
a-t0-v2/
├── config/
│   ├── thresholds.yaml          # 集中配置（信号/风控/成本/筛选/回测/测量）
│   ├── thresholds.yaml.bak      # 上一版本备份
│   ├── experiments/             # 样本/实验股票池（v3/mr/v4 等，按文件名递归查找）
│   └── overlays/
│       ├── paper.yaml           # 模拟盘 overlay（悲观成本 + 仓位护栏 + 尾盘强平）
│       ├── research.yaml        # 研究阶段 overlay（base 成本 + 仓位）
│       └── swing.yaml           # 波段交易 overlay（跨天持有，实验性）
├── docs/
│   ├── TECHNICAL_GUIDE.md           # 技术指南（运行手册 + 模块架构）
│   ├── QUANTWEB_PLAN.md             # Web 平台规划与路线图
│   ├── V3_ARCHITECTURE.md           # V3 架构说明
│   ├── CODEBASE_AND_QUANT_REVIEW.md # 代码库与量化逻辑评审
│   ├── BACKTEST_DATA_AND_QUANT_REVIEW.md  # 回测数据与量化逻辑评审
│   └── OPTIMIZATION_ANALYSIS.md     # 参数寻优分析
├── scripts/
│   ├── backtest_zz500.py        # 主回测入口（本地 5min 数据，单股/批量/全集/抽样）
│   ├── optimize_zz500_params.py # 传统网格寻优
│   ├── compare_v2_ab.py         # Measurement V2 A/B 比较器 + Stage Gate 判定
│   ├── selfcheck_measurement.py # 测量层合成数据自检
│   ├── diag_v2_measurement.py   # 单组诊断报告
│   ├── run_baseline_measurement.py  # P1 基线测量（5 类报告）
│   ├── verify_zz500_data.py     # 数据完整性校验
│   └── v3/                      # V3/V4 实验与 Optuna 优化
│       ├── optuna_stage_o.py    # Stage O 全局调参（Optuna 多 Trial 搜索）
│       ├── sample_100_stocks.py # 100 只样本抽取
│       └── qlib_*.py            # qlib 预测集成相关
├── src/
│   └── at0/                     # 核心代码包
│       ├── cli.py               # 统一 CLI（backtest/optimize/paper_monitor/validate_data）
│       ├── strategy.py          # 信号引擎（规则投票，趋势跟随）
│       ├── strategy_alpha.py    # Alpha 评分入口（compute_alpha_score_v3 多引擎融合）
│       ├── backtest.py          # 回测引擎
│       ├── risk.py              # 风控 + 成本模型 + 敞口策略
│       ├── execution.py         # 交易生命周期 + 持仓追踪
│       ├── screener.py          # 候选筛选（振幅/成交额/一字板/捕获空间）
│       ├── features.py          # 指标计算（VWAP/ADX/KDJ/布林带）
│       ├── regime.py            # regime 分类（@deprecated，已被 engines/regime_engine 取代）
│       ├── config.py            # 参数加载（yaml + overlay → dataclass）
│       ├── paths.py             # 路径常量（支持 AT0_DATA_DIR 环境变量）
│       ├── data.py              # 数据源适配（mootdx/westock/baostock/eastmoney）
│       ├── reports.py           # HTML 报告生成
│       ├── logging_utils.py     # 日志工具
│       ├── score/               # Alpha 评分（compute_alpha_score_v3，融合 Wave/Support/EM/Regime/Risk）
│       ├── engines/             # V4 引擎层（见「引擎层」章节）
│       ├── models/              # 特征向量 / 交易候选 dataclass
│       ├── qlib/                # qlib 预测适配（adapter/dataset/loader/model）
│       ├── strategy_v3/         # V3 策略入口
│       └── measurement/         # 测量层（V2 研究体系，可读源码，见「Measurement V2」章节）
├── web/                         # QuantWeb — Flask 回测平台（见「Web 平台」章节）
│   ├── app.py                   # 应用工厂（flask --app web.app run --port 8501）
│   ├── blueprints/              # 5 个蓝图：workbench/run/compare/params/results
│   ├── runner.py                # 后台回测执行引擎（ThreadPoolExecutor + 任务表）
│   ├── optuna_runner.py         # Optuna 后台优化执行器
│   ├── results_db.py            # SQLite 结果持久化（data/quantweb.db）
│   ├── templates/               # Jinja2 模板（base/workbench/run/compare/params/results）
│   ├── static/                  # quantweb.css / quantweb.js
│   ├── tests/                   # Flask API / DB / 冒烟测试
│   ├── streamlit_app.py         # 旧版 Streamlit 入口（Sprint 5 前保留，仅回退验证）
│   └── pages/                   # 旧版 Streamlit 页面（同上）
├── archive/                     # 已归档脚本/实验/死代码（见「归档说明」）
│   ├── diag/                    # 阶段性诊断脚本
│   ├── scripts/                 # deprecated 或一次性脚本
│   ├── config/                  # 已完成实验的 overlay / 股票池
│   └── src_at0_deadcode/        # 死代码归档（domain/pipeline/dualrun）
└── .gitignore
```

**数据目录**（不在代码仓库内，通过 `config/thresholds.yaml` 的 `data.root` 指定）：

```
D:\project\data\
├── zz500_5min/          # 中证 500 成分股 5min K线（每只一个 JSON）
├── minute_local/        # 分钟线（按股票分目录，每日一 JSON）
└── multi_day_cache/     # 多日数据缓存
```

---

## 研发流程（Stage 体系）

策略研发按严格的串行 Stage 推进，每个 Stage **先有测量（Measurement）再谈 Alpha**；Stage 验收看**过程指标**（CE / Entry Delay / Exit Delay / MAE / Trend Quality）的 **median + IQR**，而非净利润：

| Stage | 名称 | 关注点 |
|---|---|---|
| M | Measurement | 先能量化"买早了/卖晚了/还是运气"，再开发 Alpha（V2 体系已落地） |
| W | Wave | 波浪位置引擎评分 |
| S | Support | 动态支撑/阻力引擎评分 |
| E | Expected Move | 未来收益预测引擎 |
| H | Hold Confidence | 持仓信心评分（是否该继续持有） |
| X | Predictive Exit | 趋势衰竭预测退出 |
| D | Decision | 多引擎融合的统一决策层（DecisionEngineV4） |
| O | Optuna | 全局调参（Stage O，多 Trial 搜索） |

> **当前进度**：V4 结构优化阶段，Measurement V2 基建（M0/M1）与 W/S/E 引擎已验证，D 层（DecisionEngineV4）已上线，O 阶段 Optuna 全局调参可用。
>
> 统计口径铁律：一律用 **median + IQR** 判定，不用均值（均值会被少数极端交易带偏）；样本量 < 30 对配对交易判 **INCONCLUSIVE**，不允许"看起来变好了"。

---

## 环境准备

- **Python ≥ 3.10**（`requirements.txt` 提供全部依赖：`flask` / `jinja2` / `plotly` / `pandas` / `pyyaml` / `scipy` / `numpy`）
- 安装：`pip install -r requirements.txt`
- 数据：需提前下载 zz500 5min 数据到 `D:\project\data\zz500_5min\`（可用 `scripts/download_zz500_5min.py`）

---

## 快速开始

### 1. 单股回测（从本地 5min 数据）

```powershell
$env:PYTHONPATH = "src"
python scripts/backtest_zz500.py --code 600000 --start 2023-07-25 --end 2026-07-22 --tag demo
```

输出：`outputs/backtest/batch_summary_*_demo.json`（含 trades/report/汇总）

### 2. 批量回测（全集 / 抽样 / 指定列表）

```powershell
# 全集（500 只，振幅筛选自动开启）
python scripts/backtest_zz500.py --all --start 2023-07-25 --end 2026-07-22 --tag zz500_3y

# 随机抽样 100 只
python scripts/backtest_zz500.py --sample 100 --seed 42 --start 2023-07-25 --end 2026-07-22

# 指定多只
python scripts/backtest_zz500.py --codes 600000,000001 --start 2023-07-25 --end 2026-07-22

# 禁用振幅筛选（A/B 对比用）
python scripts/backtest_zz500.py --all --no-amplitude-filter --start 2023-07-25 --end 2026-07-22
```

### 3. 参数寻优

```powershell
python scripts/optimize_zz500_params.py --stage1-codes 30 --seed 42
```

按板块分层抽样 30 只代表股，16 组合网格搜索，输出到 `outputs/backtest/zz500_param_optimization.json`。

> 更系统的全局调参见 **Stage O（Optuna）**：`scripts/v3/optuna_stage_o.py`，或在 Web 平台「参数与预设」页发起；结果写入 `outputs/optuna/`。

### 4. A/B 对比与 Stage Gate（Measurement V2）

```powershell
python scripts/compare_v2_ab.py --base v3_b5_optimized --cand w_wave_v1 --gate W
```

把两个 tag 的回测结果变成「Stage 能不能过闸」的结论（详见「Measurement V2」章节）。

### 5. Web 平台（QuantWeb，Flask）

```powershell
pip install -r requirements.txt
flask --app web.app run --port 8501
```

启动后访问 http://localhost:8501/ ，在浏览器中配置回测、实时查看进度（SSE）、浏览结果与 K 线、A/B 对比、发起 Optuna 优化。

### 6. 实时监控（research_only）

```powershell
$env:PYTHONPATH = "src"
python -m at0.cli paper_monitor --source auto
```

---

## 核心参数（config/thresholds.yaml）

### 振幅筛选（screener 段）

| 参数 | 值 | 说明 |
|---|---|---|
| `min_20d_amplitude` | 0.035 | 20 日平均振幅 ≥ 3.5% |
| `min_20d_amount` | 1.0e8 | 20 日日均成交额 ≥ 1 亿元 |
| `min_capture_spread` | 0.006 | 单笔预期捕获空间 ≥ 0.6% |
| `min_amplitude_long` | 0.0494 | **60 日日均振幅下限（默认开启，Youden 最优阈值）** |

**`min_amplitude_long` 验证过程与结论**（F1–F5，2026-07-28 确认为默认配置）：

- **F1 样本独立性核实**：Youden 阈值训练样本（100 股）与 100 股 A/B 样本完全重叠，100 股 A/B 结果降级为"训练集一致性复核"而非样本外证据
- **F2 误伤股特征分析**：18 只被筛掉的盈利股在振幅/ADX/趋势占比/成交量四维度上与亏损组无显著差异（p > 0.3），判断为运气而非可识别特征，不设计例外规则
- **F4 阈值敏感性**：对比 0.04 / 0.045 / 0.0494 / 0.05，0.0494 在净盈亏和成本/毛利比上最优，0.05 与 0.0494 完全相同
- **F5 500 股全池 A/B（主依据）**：baseline(无筛选) 净盈亏 -421,766、成本/毛利比 240.3%；开启 0.0494 后 36 只通过，净盈亏 +151,364、成本/毛利比 42.5%。其中 27 只独立样本（与训练集不重叠）净盈亏 +128,622、盈利占比 70.4%，4/4 标准全通过，且样本外均值优于训练集，阈值未过拟合

### 信号参数（signal 段，趋势跟随）

| 参数 | 值 | 说明 |
|---|---|---|
| `min_rules_to_trigger` | 3 | 4 项规则中至少 3 项满足 |
| `tf_adx_threshold` | 35.0 | ADX > 此值确认趋势存在 |
| `tf_vol_ratio_min` | 2.0 | 量比 > 此值确认量能放大 |
| `tf_kdj_reverse_bars` | 2 | KDJ 连续 N 根反向确认 |
| `use_continuous_alpha` | false | P2 Alpha 评分默认关闭 |

### 风控参数（risk 段）

| 参数 | 值 | 说明 |
|---|---|---|
| `max_t_size_ratio` | 0.25 | 单次 T 仓位 ≤ 底仓 25% |
| `round_trip_cost` | 0.0027 | 来回成本 0.27% |
| `min_net_expected_return` | 0.0045 | 最小净期望收益 0.45% |

### 止损参数（backtest 段）

| 参数 | 值 | 说明 |
|---|---|---|
| `stop_loss_ratio` | 0.002 | 固定止损 0.2%（J0+新VWAP 36股×3年×8值网格最优，2026-07-29 定案） |
| `trailing_ratio` | 0.2 | 移动止盈回撤 20%（J0+新VWAP 36股×3年×6值网格最优） |
| `trailing_activation_pct` | 0.0 | 有盈即锁（提高门槛会摧毁胜率） |
| `max_holding_bars` | 24 | 超时平仓（24 根 5min = 2h） |
| `cooldown_bars` | 24 | 信号冷却（24 根 5min = 2h） |

### J2 回踩入场参数（signal 段，2026-07-29 正式启用）

| 参数 | 值 | 说明 |
|---|---|---|
| `retracement_entry_enabled` | true | 买入侧分离模式（趋势确认+入场价格确定分离） |
| `retracement_vwap_band` | 0.02 | VWAP 偏离 ≤ 2% 视为回踩到位 |
| `retracement_kdj_max` | 70.0 | KDJ.K < 70 确认非追高 |
| `retracement_lookback` | 8 | 回看 8 根 K 线检查历史冲高 |
| `retracement_min_surge` | 0.005 | 历史冲高 ≥ 0.5% 确认趋势存在 |

> **验证依据**（36股×3年×4组A/B）：J2 启用后配对+88%、净盈亏+90%（623K→1,185K）、CE均值+39%（2.26%→3.14%）、胜率维持69%。机制：回踩到VWAP附近买入，买入点更贴近日内相对低点。

### J4 冲高确认参数（signal 段，2026-07-29 验证后拒绝启用）

| 参数 | 值 | 说明 |
|---|---|---|
| `surge_exit_enabled` | false | 卖出侧分离模式（结构性无效，保持关闭） |
| `surge_vwap_band` | 0.005 | VWAP 偏离 ≤ 0.5% 视为冲高到位 |
| `surge_kdj_min` | 40.0 | KDJ.K > 40 确认已反弹 |
| `surge_lookback` | 8 | 回看 8 根 K 线检查历史深跌 |
| `surge_min_drop` | 0.005 | 历史深跌 ≥ 0.5% 确认趋势存在 |

> **拒绝原因**：J4设计目标是通过"等价格冲高到VWAP再卖出"提升卖出点CE。但36股×3年×4组A/B + 20股×5参数敏感性检查证明：J4单独使CE均值从2.26%降至1.82%（与目标相反）。根因：趋势跟随策略的卖出逻辑是"卖在强势区"（接近日内高点），已接近CE最优；J4要求"先跌后反弹到VWAP再卖"，VWAP在下跌日位于日内中低位→卖点结构性更低。参数从松到紧，J4要么加量降质（CE降），要么退化为baseline（无效）。

### 成本模型（cost 段）

| 场景 | 佣金 | 印花税 | 滑点 | 来回总成本 |
|---|---|---|---|---|
| optimistic | 万1 | 0.05% | 0.05% | 0.2% |
| **base**（默认） | 万1 | 0.05% | 0.1% | **0.27%** |
| pessimistic | 万1 | 0.05% | 0.2% | 0.5% |

---

## 引擎层（V4 架构）

`src/at0/engines/` 为可插拔引擎层，统一接口 `score(bars, snap, direction) -> float (0~100)`：

| 引擎 | 文件 | 角色 |
|---|---|---|
| SupportEngine (G2) | support_engine.py | 动态支撑/阻力评分 |
| WaveEngine (G4) | wave_engine.py | 波浪位置评分 |
| ExpectedMoveEngine (G5) | expected_move_engine.py | 未来收益预测（qlib 预测） |
| RegimeEngine (G7) | regime_engine.py | 市场状态识别 |
| RiskEngine | risk_engine.py | 风险评分（Kelly sizing 支持） |
| HoldConfidenceEngine | hold_confidence_engine.py | 持仓信心评分（Stage H） |
| PredictiveExitEngine | predictive_exit_engine.py | 趋势衰竭预测退出（Stage X） |
| DecisionEngineV4 | decision_engine.py | 统一决策层（Stage D），融合上述引擎 |

- Alpha 评分入口：`src/at0/score/alpha_score.py` 的 `compute_alpha_score_v3(snap, params, direction)`，按 `v3_alpha_weights`（信号段）加权融合各引擎。
- **关键约定**：`_bars` 未注入时五引擎（Wave/Support/ExpectedMove/Regime/Risk）会静默退化成常数；`compute_alpha_score_v3` 在 `v3_engines_in_backtest=True` 且缺 `_bars` 时显式 `raise`（fail-fast），`False` 时优雅复现旧基线。开关 `SignalParams.v3_engines_in_backtest`（默认 True）。回测与实盘必须都注入 `_bars`，否则 A/B 结论全部失效。

---

## Measurement V2（研究测量体系）

V4 结构优化阶段的研究基础设施（Stage M0 + M1），位于 `src/at0/measurement/`，**全部为可读 .py 源码**（2026-08-04 从黑盒 .pyc 恢复，2026-08-05 移除 loader shim）。

**设计原则**：

- 指标可扩展：`METRIC_REGISTRY` 注册式框架，新增指标不改报告生成器
- 可归因：每笔交易记录所有指标（CE / Entry Delay / Exit Delay / MAE / Trend Quality），支持分档/分组聚合
- 可复现：基于 report.json + K线数据离线计算，不依赖运行时状态

**核心模块**：

| 文件 | 作用 |
|---|---|
| `v2_metrics.py` | METRIC_REGISTRY 注册式指标框架 + 批量计算 |
| `v2_reporter.py` | HTML / CSV 报告生成 |
| `trade_quality.py` | 交易质量报告（WinRate / PF / MAE / MFE） |
| `cashflow_audit.py` | 现金流对账 |
| `ic_analysis.py` | IC / RankIC / Alpha Decay |
| `param_landscape.py` | 参数景观扫描 |
| `stratified.py` | 分层回测（regime × time_slot） |
| `time_split.py` | 时间分割 |

**配套脚本**：

| 脚本 | 作用 |
|---|---|
| `scripts/compare_v2_ab.py` | A/B 比较器 + Stage Gate 判定（`--base --cand --gate`） |
| `scripts/selfcheck_measurement.py` | 合成数据自检（改完 measurement 先跑） |
| `scripts/diag_v2_measurement.py` | 单组诊断报告 |

**测量口径铁律**（改测量代码时必须遵守）：

- 指标不可用一律返回 `None`，禁止用 -1 / 0 兼作哨兵值（`entry_delay_bars = -1`、`exit_delay_bars = 0` 都是合法且最优的取值）
- 聚合过滤写 `isinstance(v, (int, float)) and not isinstance(v, bool)`，不要写 `if v > 0` 或 `if v != -1`
- 全池统计在**所有配对交易**上算，不做"每股均值再平均"（mean of means 会失真）
- 展示层可以把 None 显示为 "—"，但统计层绝不补 0

---

## 参数加载机制

```
thresholds.yaml (base)  ──┐
                          ├─→ config_loader ─→ dataclass 参数对象
overlay (paper/research/swing) ─┘
```

- **基础参数**：`config/thresholds.yaml`
- **overlay 覆盖**：通过 `AT0_OVERLAY=paper|research|swing` 环境变量激活，深度合并覆盖 base
- **dataclass 默认值**：作为 yaml 缺失时的最后兜底

加载函数（`src/at0/config.py`）：
- `load_signal_params()` / `load_risk_params()` / `load_backtest_params()`
- `load_screener_params()` / `load_cost_model()` / `load_exposure_policy()`

---

## 振幅筛选逻辑

`scripts/backtest_zz500.py` 中的 `filter_codes_by_amplitude` 函数：

1. 读取每只股票的 `daily_bars`，取回测期前 60 个交易日
2. 逐日合成日 K，计算振幅 `(high - low) / prev_close`（与 `screener.py` 口径一致）
3. 计算日均振幅，`>= 0.0494` 通过，否则筛掉
4. `--no-amplitude-filter` 可强制禁用（A/B 对比用）

**联动**：`thresholds.yaml` 的 `screener.min_amplitude_long` 控制开关，设为 `null` 则关闭。

---

## 统一 CLI（src/at0/cli.py）

```powershell
$env:PYTHONPATH = "src"
python -m at0.cli <subcommand> [args...]
```

| 子命令 | 说明 |
|---|---|
| `backtest` | 单股多日回测（`--code`/`--start`/`--end`/`--source`/`--base-shares`） |
| `optimize` | 批量多股票回测（`--start`/`--end`/`--codes`） |
| `paper_monitor` | L5 实盘监控（`--source`/`--date`/`--demo`/`--eod-check`） |
| `validate_data` | 数据校验（预留） |

**注意**：zz500 本地数据回测推荐用 `scripts/backtest_zz500.py`（支持振幅筛选和批量模式），CLI 的 `backtest` 子命令主要用于在线数据源回测。

---

## scripts/ 工具脚本

| 脚本 | 作用 |
|---|---|
| `backtest_zz500.py` | **主回测入口**，从本地 5min 数据跑回测（单股/批量/全集/抽样） |
| `optimize_zz500_params.py` | zz500 参数寻优（16 组合网格搜索） |
| `compare_v2_ab.py` | Measurement V2 A/B 比较器 + Stage Gate 判定 |
| `selfcheck_measurement.py` | 测量层合成数据自检 |
| `diag_v2_measurement.py` | 单组测量诊断报告 |
| `run_baseline_measurement.py` | P1 基线测量（5 类报告） |
| `gen_amplitude_pool.py` | 按 60 日振幅筛选股票池 |
| `gen_candidate_pool.py` | 生成 T-eligible 候选池（baostock） |
| `download_zz500_5min.py` | 下载中证 500 成分股 5min K线 |
| `download_minute_data.py` | 批量下载分钟级 K线（多源） |
| `migrate_minute_local_to_merged.py` | 数据迁移（每股每日 → 每股一文件） |
| `verify_zz500_data.py` | 验证 zz500 数据完整性 |
| `final_validation.py` | 最终验证（72 只缓存股票） |
| `walk_forward.py` | Walk-forward 验证 |
| `v3/optuna_stage_o.py` | Stage O 全局调参（Optuna 多 Trial 搜索，写入 outputs/optuna） |
| `v3/sample_100_stocks.py` | 100 只样本抽取（按板块分层） |

---

## 输出产物

所有回测产物默认输出到 `outputs/backtest/`（已被 .gitignore 忽略）：

| 文件 | 内容 |
|---|---|
| `batch_summary_*_<tag>.json` | 多股票汇总（含 per_stock / overall） |
| `batch_summary_*_<tag>.html` | HTML 汇总报告 |
| `{code}_{start}_{end}_trades.json` | 单股买卖触发记录 |
| `{code}_{start}_{end}_report.json` | 单股完整回测报告 |
| `outputs/runs/<run_id>/` | 版本化 artifacts（参数 + 数据指纹） |
| `outputs/optuna/` | Optuna 优化结果（trial 历史 / 参数重要性） |

---

## Web 平台（QuantWeb — Flask）

基于 Flask 的机构终端风格回测平台（Sprint 5 起替代原 Streamlit 实现），在浏览器中发起回测、实时查看进度、浏览结果与 K 线、A/B 对比、发起 Optuna 优化。

### 运行

```powershell
# 依赖（首次）
pip install -r requirements.txt

# 启动（应用工厂 + 蓝图，自动加载 src/ 与 scripts/ 到 sys.path）
flask --app web.app run --port 8501
# 或
python -m web.app

# 局域网/云主机暴露：
FLASK_HOST=0.0.0.0 python -m web.app
# 调试模式（仅限可信环境）：
FLASK_DEBUG=1 python -m web.app
```

启动后访问 http://localhost:8501/ 。

### 蓝图（页面）

| 路由 | 蓝图 | 说明 |
|---|---|---|
| `/` | workbench | 研究工作台：研发进度趋势（median net_pnl + CE 双轴 IQR 图）、运行队列、发起新回测表单 |
| `/runs/`、`/runs/<id>` | run | 运行列表 + 单次运行详情（汇总 / Stage Gate 判定 / 个股排行 / K 线图） |
| `/compare/` | compare | 策略 A/B 对比 + Stage Gate 判定 + Wilcoxon 显著性检验 |
| `/params/` | params | 参数与预设：七维度权重 / YAML 编辑 / Optuna 优化 / 预设管理 |
| `/results/` | results | 回测结果：逐股 HTML 可视化报告（iframe 内嵌，与 CLI 产物一致） |

### 架构要点

- **后台执行**：`web/runner.py` 用 `ThreadPoolExecutor`（max_workers=1，回测为 CPU 密集）+ SQLite 任务表，不引入 Celery/Redis；支持取消（逐股粒度检查 `cancelled` 标志）
- **实时进度**：`/api/runs/<id>/stream` SSE 端点，前端 `EventSource` 消费，进度变化才推送，不做轮询
- **K 线图**：Plotly 服务端渲染（`lru_cache` 缓存，TTL ≈ 120s），前端按需加载
- **持久化**：`web/results_db.py` — SQLite（`data/quantweb.db`，WAL 模式，**不纳入版本控制**）；表：`runs` / `results` / `daily_results` / `config_snapshots` / `presets`
- **测试**：`web/tests/` — API / DB / Flask 冒烟测试
- **旧版 Streamlit**：`web/streamlit_app.py` + `web/pages/` 保留（Sprint 5 前实现，仅回退验证用，新功能不再加）

---

## measurement/ 子包说明

`src/at0/measurement/` 曾为黑盒形态：源码被 Trae 沙箱 TSD 加密为 `.py.bak`，运行依赖 `__pycache__/*.cpython-310.pyc` + `SourcelessFileLoader` shim。2026-08 已完成**源码恢复**：

- **2026-08-04（Sprint 2）**：六个子模块（time_split / trade_quality / cashflow_audit / ic_analysis / param_landscape / stratified）按 .pyc docstring 与现有使用模式重写为可读 .py，git blame 可追溯
- **2026-08-05**：移除 `SourcelessFileLoader` shim，改为标准 `from .xxx import ...` 导入；同时从仓库移除 6 个 `.py.bak` 占位与全部 `__pycache__/*.pyc`，并删除 .gitignore 中"例外保留 .pyc"规则

> 历史坑：shim 不检查 .pyc 是否比 .py 新，会静默加载过时编译产物——失效模式恰是"改了代码但没生效"。这也是曾经 A/B 两组结果逐字节相同（Engine 空转）教训的同类风险。

---

## 归档说明

`archive/` 目录存放已完成的阶段性诊断、实验脚本与死代码，不影响项目运行：

- `archive/diag/` — 24 个阶段性诊断脚本（step1-4 / Stage D/E/F / thin 系列）
- `archive/scripts/` — 9 个 deprecated 或一次性脚本（batch_backtest / optimize_params / verify_stop_scheme 等）
- `archive/config/` — 已完成实验的 overlay 与实验股票池（amp0040/amp0045/amp0050/cooldown24/kdj_rev1 等）
- `archive/schemas/` — 过时的 params_schema.json（未被代码引用）
- `archive/src_at0_deadcode/` — 死代码归档：`domain.py` / `pipeline.py` / `dualrun/`（真黑盒，仅 .py.bak + .pyc，无源码；因不再被引用而归档，**不可删除**）

---

## 技术文档

- [TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) — 运行手册、参数体系、止损安全网、模块架构
- [QUANTWEB_PLAN.md](docs/QUANTWEB_PLAN.md) — Web 平台规划与路线图
- [V3_ARCHITECTURE.md](docs/V3_ARCHITECTURE.md) — V3 架构说明
- [CODEBASE_AND_QUANT_REVIEW.md](docs/CODEBASE_AND_QUANT_REVIEW.md) — 代码库与量化逻辑评审
- [BACKTEST_DATA_AND_QUANT_REVIEW.md](docs/BACKTEST_DATA_AND_QUANT_REVIEW.md) — 回测数据与量化逻辑评审
- [OPTIMIZATION_ANALYSIS.md](docs/OPTIMIZATION_ANALYSIS.md) — 参数寻优分析

---

## 仓库与分支

- 主分支为 **`main`**（非 `master`）。`dev` / `dev-alpha` / `dev-v2` / `dev-v3` 为开发分支，`dev-alpha` 与 `dev-v2` 内容等价。
- `main` 与 dev 分支历史无关联（no common ancestor）：远端 `main` 曾只含旧代码与 752 个市场数据文件（`data/minute_local/`、`data/zz500_5min/`，仅存在于 git 历史）。已通过 `git merge --allow-unrelated-histories` 合并，代码取 dev 线，数据保留。
- `data/` 目录在 dev 分支 .gitignore 中排除，但 main 中保留合并带入的数据文件。
