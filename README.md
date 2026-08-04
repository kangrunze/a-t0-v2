# A-T0 — L5 T+0 日内做T策略

> 趋势跟随型 T+0 日内做T策略，基于中证 500 成分股 5min K线回测验证。
> 默认开启 60 日振幅筛选（阈值 0.0494），过滤低波动股票，显著提升净盈亏与成本/毛利比。

---

## 项目结构

```
a-t0-v2/
├── config/
│   ├── thresholds.yaml          # 集中配置（信号/风控/成本/筛选/回测/测量）
│   ├── thresholds.yaml.bak      # 上一版本备份
│   └── overlays/
│       ├── paper.yaml           # 模拟盘 overlay（悲观成本 + 仓位护栏 + 尾盘强平）
│       └── research.yaml        # 研究阶段 overlay（base 成本 + 仓位）
├── docs/
│   ├── TECHNICAL_GUIDE.md       # 技术指南（运行手册 + 模块架构）
│   ├── QUANTWEB_PLAN.md         # Web 平台规划与路线图
│   └── thin4_zombie_params_audit.md  # 僵尸参数审计记录
├── scripts/
│   ├── backtest_zz500.py        # 主回测入口（本地 5min 数据，单股/批量/全集/抽样）
│   ├── optimize_zz500_params.py # 传统网格寻优
│   └── v3/                      # V3/V4 实验与 Optuna 优化
│       └── optuna_stage_o.py    # Stage O 全局调参（Optuna 多 Trial 搜索）
├── src/
│   └── at0/                     # 核心代码包
│       ├── cli.py               # 统一 CLI（backtest/optimize/paper_monitor）
│       ├── strategy.py          # 信号引擎（规则投票，趋势跟随）
│       ├── strategy_alpha.py    # Alpha 评分入口（compute_alpha_score_v3 多引擎融合）
│       ├── backtest.py          # 回测引擎
│       ├── risk.py              # 风控 + 成本模型 + 敞口策略
│       ├── execution.py         # 交易生命周期 + 持仓追踪
│       ├── screener.py          # 候选筛选（振幅/成交额/一字板/捕获空间）
│       ├── features.py          # 指标计算（VWAP/ADX/KDJ/布林带）
│       ├── regime.py            # regime 分类（@deprecated，已被 engines/regime_engine 取代）
│       ├── config.py            # 参数加载（yaml → dataclass）
│       ├── paths.py             # 路径常量（支持 AT0_DATA_DIR 环境变量）
│       ├── data.py              # 数据源适配（mootdx/westock/baostock/eastmoney）
│       ├── reports.py           # HTML 报告生成
│       ├── logging_utils.py     # 日志工具
│       ├── score/               # Alpha 评分（compute_alpha_score_v3，融合 Wave/Support/EM/Regime/Risk）
│       ├── engines/             # V4 引擎层（见「引擎层」章节）
│       └── measurement/         # 测量层（.pyc 形式，TSD 加密源码）
├── web/                         # QuantWeb — Streamlit 回测平台
│   ├── app.py                   # 入口（streamlit run web/app.py --server.port 8501）
│   ├── results_db.py            # SQLite 结果持久化（data/quantweb.db）
│   └── pages/                   # 仪表盘 / 配置 / 结果查看 / 策略对比 / Optuna / 参数配置
├── archive/                     # 已归档的阶段性诊断/实验脚本
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
| M | Measurement | 先能量化"买早了/卖晚了/还是运气"，再开发 Alpha |
| W | Wave | 波浪位置引擎评分 |
| S | Support | 动态支撑/阻力引擎评分 |
| E | Expected Move | 未来收益预测引擎 |
| H | Hold Confidence | 持仓信心评分（是否该继续持有） |
| X | Predictive Exit | 趋势衰竭预测退出 |
| D | Decision | 多引擎融合的统一决策层（DecisionEngineV4） |
| O | Optuna | 全局调参（Stage O，多 Trial 搜索） |

> 统计口径铁律：一律用 **median + IQR** 判定，不用均值（均值会被少数极端交易带偏）；样本量 < 30 对配对交易判 **INCONCLUSIVE**，不允许"看起来变好了"。

---

## 环境准备

- **Python 3.10**（`measurement/` 子包的 .pyc 为 cpython-310 编译）
- 依赖：`mootdx`、`pandas`、`pyyaml`、`baostock`、`streamlit`（Web 平台，无 requirements.txt，按需 `pip install streamlit`）
- 数据：需提前下载 zz500 5min 数据到 `D:\project\data\zz500_5min\`（可用 `scripts/download_zz500_5min.py`）

---

## 快速开始

### 1. 单股回测（从本地 5min 数据）

```powershell
$env:PYTHONPATH = "src"
& "C:\Users\kangrunze\AppData\Local\Programs\Python\Python310\python.exe" `
    scripts/backtest_zz500.py --code 600000 --start 2023-07-25 --end 2026-07-22 --tag demo
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

> 更系统的全局调参见 **Stage O（Optuna）**：`scripts/v3/optuna_stage_o.py`，或在 Web 平台「🎯 Optuna 优化」页发起；结果写入 `outputs/optuna/`。

### 4. 实时监控（research_only）

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
- **关键约定**：`_bars` 未注入时五引擎（Wave/Support/ExpectedMove/Regime/Risk）会静默退化成常数；`compute_alpha_score_v3` 在 `v3_engines_in_backtest=True` 且缺 `_bars` 时显式 `raise`（fail-fast），`False` 时优雅复现旧基线。开关 `SignalParams.v3_engines_in_backtest`（默认 True）。
- 废弃但未清理：`OpportunityEngine` / `ExecutionEngine`（被 DecisionEngineV4 / RiskEngine 取代，文件保留不再导出）。

---

## 参数加载机制

```
thresholds.yaml (base)  ──┐
                          ├─→ config_loader ─→ dataclass 参数对象
overlay (paper/research) ─┘
```

- **基础参数**：`config/thresholds.yaml`
- **overlay 覆盖**：通过 `AT0_OVERLAY=paper|research` 环境变量激活，深度合并覆盖 base
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
| `gen_amplitude_pool.py` | 按 60 日振幅筛选股票池 |
| `gen_candidate_pool.py` | 生成 T-eligible 候选池（baostock） |
| `download_zz500_5min.py` | 下载中证 500 成分股 5min K线 |
| `download_minute_data.py` | 批量下载分钟级 K线（多源） |
| `migrate_minute_local_to_merged.py` | 数据迁移（每股每日 → 每股一文件） |
| `verify_zz500_data.py` | 验证 zz500 数据完整性 |
| `run_baseline_measurement.py` | P1 基线测量（5 类报告） |
| `final_validation.py` | 最终验证（72 只缓存股票） |
| `v3/optuna_stage_o.py` | Stage O 全局调参（Optuna 多 Trial 搜索，写入 outputs/optuna） |

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

---

## Web 平台（QuantWeb）

基于 Streamlit 的回测平台，在浏览器中配置回测、查看运行结果与图表、对比策略、发起 Optuna 优化。

### 运行

```powershell
# 必须从项目根目录启动，且 PYTHONPATH 含项目根（web 包依赖 `from web.results_db import`）
$env:PYTHONPATH = "D:\project\a-t0-v2"
& "C:\Users\kangrunze\AppData\Local\Programs\Python\Python310\python.exe" `
    -m streamlit run web/app.py --server.port 8501 --server.headless true
```

> 注意：必须带 `PYTHONPATH=<项目根>`（或 `cd` 到项目根）。若仅设 `src`，`from web.results_db import` 会因 `web` 包找不到而失败。

启动后访问 http://localhost:8501/ 。兼容 Streamlit 1.60（部分新 API 如 `text_area(font=)` 需 ≥1.62，当前未使用）。

### 页面

| 页面 | 说明 |
|---|---|
| 📊 综合仪表盘 | 运行概览与关键指标 |
| ⚙️ 回测配置 | 选股票池/区间/参数，启动回测（后台线程，不阻塞 UI） |
| 📈 结果查看 | 历史运行、个股净盈亏排行（以排名展示）、胜率/CE 分析、资金曲线、K 线图、明细 |
| 🔬 策略对比 | A/B 对比（base vs candidate，Stage Gate 判定） |
| 🎯 Optuna 优化 | 启动 Stage O 优化 + 查看历史 Trial 收敛 / 参数重要性 |
| 🔧 参数配置 | 七维度权重 / 信号参数 / 风险参数 / 原始 YAML 编辑 |

### 结果持久化（web/results_db.py）

结果存入 `data/quantweb.db`（SQLite，首次运行 `init_db` 自动建表；**不纳入版本控制**）。表：`runs`（运行记录，含 `cancelled` 标记）、`results`（个股摘要）、`daily_results`（逐日明细）、`config_snapshots`（参数快照）。

- **停止任务**：回测任务列表 / 进度轮询区均有「🛑 停止」按钮，写入 `cancelled=1`；后台循环每处理完一只股票检查该标记，命中则优雅终止（粒度 = 每股票）。
- **懒加载**：结果查看页的图表视图用 `st.radio` + 条件执行（非 `st.tabs`），仅选中视图计算，避免切页时一次性加载多年 K 线数据导致卡顿。

---

## measurement/ 子包说明

`src/at0/measurement/` 的源码 `.py` 已被 Trae 沙箱 TSD 加密为 `.py.bak`，运行依赖 `__pycache__/*.cpython-310.pyc`。`__init__.py` 用 `SourcelessFileLoader` 加载 .pyc，包含 6 个子模块：

- `time_split` — 时间分割
- `trade_quality` — 交易质量报告（WinRate/PF/MAE/MFE）
- `cashflow_audit` — 现金流对账
- `ic_analysis` — IC/RankIC/Alpha Decay
- `param_landscape` — 参数景观扫描
- `stratified` — 分层回测（regime × time_slot）

**重要**：`.pyc` 文件已纳入版本控制（.gitignore 例外），不可删除。

---

## 归档说明

`archive/` 目录存放已完成的阶段性诊断和实验脚本，不影响项目运行：

- `archive/diag/` — 24 个阶段性诊断脚本（step1-4 / Stage D/E/F / thin 系列）
- `archive/scripts/` — 9 个 deprecated 或一次性脚本（batch_backtest / optimize_params 等）
- `archive/config/overlays/` — 5 个已完成实验的 overlay（amp0040/amp0045/amp0050/cooldown24/kdj_rev1）
- `archive/schemas/` — 过时的 params_schema.json（未被代码引用）

---

## 技术文档

- [TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) — 运行手册、参数体系、止损安全网、模块架构
- [QUANTWEB_PLAN.md](docs/QUANTWEB_PLAN.md) — Web 平台规划与路线图
- [thin4_zombie_params_audit.md](docs/thin4_zombie_params_audit.md) — 僵尸参数审计记录
