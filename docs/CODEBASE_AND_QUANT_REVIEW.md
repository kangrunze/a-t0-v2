# A-T0 v2 代码架构与回测数据综合技术审查

> 文档性质：研究/工程综合审查（非代码改动）
> 生成日期：2026-07-29
> 审查范围：`src/at0/` 全量代码 + `outputs/` 回测历史数据 + `config/thresholds.yaml`
> 方法：静态代码分析 + 回测产物采样验证（所有数字均标注可追溯来源）
> 配套文档：`TECHNICAL_GUIDE.md`（运行手册）、`OPTIMIZATION_ANALYSIS.md`（代码层审计 2026-07-28）、`BACKTEST_DATA_AND_QUANT_REVIEW.md`（回测数据审查 2026-07-29）

---

## 0. 执行摘要

**一句话结论**：项目具备**工程化程度高、A/B 验证文化扎实、版本可追溯完整**三大优势，但当前存在一组"配置写了不生效 / 样本外不纯 / 组合层缺失"的系统性问题，导致回测净盈亏（1.10M）为**乐观上界**而非可兑现期望。上线前需优先修复 P0 级方法论缺陷。

### 核心发现速览

| # | 发现 | 严重度 | 实证依据 |
|---|------|--------|----------|
| 1 | OOS 集合与调参样本重叠 8/36，训练组 CE 是干净 OOS 的 **2.06 倍** | **高** | `oos_validation/step3_group_comparison.json`：train CE_pooled=0.058 vs oos CE_pooled=0.0281 |
| 2 | 无振幅筛选 500 只仍有 95.2% 盈利 → 正期望主要来自 T+0 配对机制本身 | **高** | `K_mr_no_amp_full500` 系列报告（476/500 盈利） |
| 3 | `cost_model=null` / `exposure_policy=null` → 三成本场景未加载、敞口策略未生效 | **高** | `runs/20260729_094130_*/params.json` |
| 4 | `max_holding_bars` yaml=24 vs 运行=12（两处不一致定义） | 中 | yaml `backtest.max_holding_bars=24` vs `exposure_policy.max_holding_bars=12`，params.json 运行值=12 |
| 5 | 胜率双口径：batch 配对口径 73.8% vs report 全口径 36.9%（同一标的 688498） | 中 | `batch_summary_oos_step2_full.json` vs `688498_..._report.json` |
| 6 | 净盈亏为 36 只独立求和，无组合层资本约束/相关性/回撤聚合 | 高 | batch_summary 仅 per_stock 求和 |
| 7 | 跨日腿出现负 CE（隔夜风险侵蚀） | 中 | `step3.per_stock_ce` 多项 `cross_day_ce_mean` 为负 |
| 8 | 7 处 PROJECT_ROOT 重复定义、approve_signal/check_risk 两套实现、4 处命名不一致 | 中 | 源码静态分析 |

**策略可部署性判定**：`重新评估 / 等待证据` —— 在 P0 方法论修复落地并重跑干净 OOS 之前，不建议进入实盘。

---

## 1. 代码架构深度分析

### 1.1 模块职责矩阵

| 模块 | 行数级 | 职责 | 关键类/函数 | 依赖方向 |
|------|--------|------|-------------|----------|
| `data.py` | 大 | 数据源适配（eastmoney→mootdx→westock→baostock 四源回退） | `fetch_minute_bars`/`fetch_multi_day`/`normalize_code`/`run_westock` | 底层 |
| `paths.py` | 小 | 路径常量（`AT0_DATA_DIR` > yaml > 默认） | `DATA_ROOT`/`PROJECT_ROOT`/各文件路径 | 底层 |
| `config.py` | 中 | yaml+overlay 配置加载 | `load_signal_params`/`load_risk_params`/`load_backtest_params`/`load_cost_model`/`load_exposure_policy` | paths |
| `features.py` | 大 | 指标计算层（reference/quote/market 三合一） | `compute_reference_snapshot`/`detect_market_regime`/`cumulative_vwap`/`kdj`/`dmi`/`macd` | data |
| `strategy.py` | **最大** | L5 信号决策层（三层结构 + J2/J4） | `SignalParams`(170+参数)、`TSignal`、`evaluate_reduce_signal`/`evaluate_add_signal`/`evaluate_all_signals` | features |
| `strategy_alpha.py` | 小 | P2 连续评分映射（默认关闭） | `compute_alpha_score(snap, params) -> (float, dict)` | features |
| `risk.py` | 大 | 成本模型 + 敞口策略 + pre-trade 风控 | `CostModel`(frozen)、`ExposurePolicy`、`RiskDecision`、`approve_signal`/`check_risk` | config |
| `execution.py` | 大 | FIFO 配对 + 持仓追踪 + open_legs 持久化 | `TradeLeg`/`LegStatus`/`TradeLifecycle`(`add_fill`/`update_holding`/`check_stop_loss`/`check_expiry`) | config |
| `backtest.py` | **最大** | 回测引擎主循环 + 统计 + artifacts + 调优 | `BacktestParams`/`BacktestState`/`backtest_single_day`/`backtest_multi_day`/`summarize_one_stock`/`aggregate_batch`/`rolling_out_of_sample` | all above |
| `screener.py` | 中 | T-eligible 候选筛选 | `ScreenerParams`/`screen_candidate`/`screen_candidate_baostock` | data |
| `regime.py` | 中 | 日线 Regime（**@deprecated**，Stage E 验证未通过） | `DailyRegime`/`classify_daily_regime` | data |
| `reports.py` | 大 | HTML 报告生成 | `save_html_report`/`save_batch_html_report` | backtest |
| `cli.py` | 大 | 三入口统一分发 | `run`/`run_backtest_cli`/`batch_main`/`monitor_main`/`l5_monitor_cli` | all |
| `logging_utils.py` | 中 | CSV/日志记录 | `log_signal`/`log_trade`/`log_monitor`/`compute_trade_stats` | execution |
| `measurement/` | 加密 | 测量层（.pyc TSD 加密，6 子模块） | `time_split`/`trade_quality`/`cashflow_audit`/`ic_analysis`/`param_landscape`/`stratified` | backtest |

### 1.2 完整数据流链路

```
data.fetch_multi_day (auto: eastmoney→mootdx→westock→baostock)
  ↓ daily_bars + daily_prev_closes + daily_meta
cli.run → BacktestParams → adapt_params_by_frequency
  ↓
backtest.backtest_multi_day (按日循环,跨日传递 carry_open_legs/carry_locked)
  ↓ 每日 backtest_single_day
  ↓ 逐 bar 循环 [warmup, len(bars)):
     ├─ lifecycle.update_holding          # 持仓时长+max_favorable/adverse (bar.low/high)
     ├─ lifecycle.check_stop_loss         # 移动止盈优先,盘中穿透即触发
     ├─ lifecycle.check_expiry            # 超时→expired,记录真实盈亏
     ├─ 涨跌停封板检测
     ├─ strategy.evaluate_reduce_signal / evaluate_add_signal
     │    ↓ compute_reference_snapshot    # features 层广算指标
     │    ↓ merge_with_reference_snapshot  # 合并盘口特征
     │    ↓ _judge_trend_context → detect_market_regime
     │    ↓ 三层计分 / J2回踩 / Stage K 均值回归
     │    ↓ TSignal.triggered 判定
     ├─ hard_trend_filter (可选,默认关)
     ├─ cooldown_bars / require_opposite_direction
     ├─ _execute_trade: approve_signal(风控) → CostModel.fill_price/calc_cost → lifecycle.add_fill(FIFO配对)
     └─ 优先级: reduce > add (保守)
  ↓ 日终 eod_risk_disposal (标记expired+浮盈浮亏)
  ↓ 跨日: export_open_legs / import_open_legs (FIFO队列不重置)
  ↓ 回测结束: compute_unrealized_pnl (含成本)
  ↓ summarize_one_stock / aggregate_batch
  ↓ save_trades_json / save_report_json / save_html_report
  ↓ save_run_artifacts (run_id版本化: params_hash + data_fingerprint + git_commit)
```

### 1.3 架构亮点（保持）

1. **严格分层依赖**：data → features → strategy → risk → execution → backtest，单向依赖无环
2. **特征计算严格因果、无前视偏差**：`cumulative_vwap` 用 `bars[-period:]`、`kdj` 连续递推修复、`volume_ratio` 窗口不重叠 —— 策略护城河
3. **FIFO 跨日连续配对不变量**：`TradeLifecycle` 队列跨日不 reset，`import_open_legs` 设 `fill_bar_idx = -prev_holding` 使持仓时长连续
4. **CostModel 三场景统一收敛**：`optimistic`/`base`/`pessimistic` 工厂模式，`fill_price`(买上浮卖下浮) + `round_trip_cost_rate()` 统一计算
5. **run_id 版本化追溯**：每次运行落盘 `params.json` + `data_fingerprint.json` + `manifest.json`（含 `git_commit`/`param_hash`/`data_hash`），可复现
6. **详尽的 P0-P3 整改注释链**：每个修复有回放验证依据，迭代痕迹清晰
7. **原子读写防护**：`_atomic_update`(文件锁内读-改-写) 防并发覆盖，`live_open_legs.json` 独立锁避免与 `positions.json` 竞争

### 1.4 代码质量问题清单

#### A 区 · 死代码 / 配置失效（影响正确性）

| ID | 问题 | 证据（源码已验证） | 影响 |
|----|------|-------------------|------|
| **B** | `open_max_vwap_dev`(0.012) 全 `src/` 仅定义处 1 次出现，从不读取 | `strategy.py:108`；evaluate 函数体零引用 | 文档声称"防超时腿"的控制从未生效 |
| **C** | `min_net_expected_return`(0.0045) 定义但 `src/` 零引用 | yaml 有定义；`risk.check_risk` 只查 `min_capture_spread` | 预期收益门槛形同虚设 |
| **K** | `open_vwap_dev` 字段 + `_compute_pairing_threshold` 死代码 | `execution.py:75` 存字段；`strategy.py:253` `@deprecated` | 历史遗留 |
| **D** | 参数寻优网格含"不生效参数" | `backtest.py:1189` `PARAM_GRID` 含 `vwap_dev_atr_multiplier`/`rsi_*`；活跃趋势信号只用 VWAP符号+ADX+KDJ+量比 | optimize 结果对当前策略指导有限 |

#### B 区 · 配置加载缺口（回测 vs 实盘不一致）

| ID | 问题 | 证据 | 影响 |
|----|------|------|------|
| **E** | `BacktestParams` dataclass 默认值与 yaml 分歧 | dataclass：`stop_loss=0.008`,`trailing=0.005`,`cooldown=12`；yaml：`0.002/0.2/24` | PyYAML 缺失时落错值 |
| **F** | 成本模型加载缺口 | `config.py:106` `load_backtest_params` 只读 `backtest` 段；`backtest.py:489` 用兼容字段重建 base 0.27%，**不读 `cost` 段 scenario**；params.json 实测 `cost_model: null` | **pessimistic/optimistic 成本场景在回测中不生效** |
| **G** | overlay 字段名不匹配被静默忽略 | `paper.yaml` 用 `max_position_pct/max_t_size/eod_force_flat`；`RiskParams` 字段为 `max_t_size_ratio`；`load_risk_params` 用 `hasattr` 过滤丢弃 | 模拟盘 15% 仓位上限 / 尾盘强平不生效 |
| **H** | 市场门控仅 live 路径，回测缺失 | `backtest_single_day` 直调 `evaluate_reduce/add_signal`；`market_gate_for_add` 仅在 `evaluate_all_signals` 应用 | 回测与实盘行为不一致，回测未反映 COLD 禁加仓等情绪风控 |
| **H2** | `max_holding_bars` 两处不一致 | yaml `backtest.max_holding_bars=24` vs `exposure_policy.max_holding_bars=12`；params.json 运行值=12 | 运行实际取 12，与文档/yaml 声称的 24 不符 |

#### C 区 · 文档与代码不一致

| ID | 问题 | 证据 |
|----|------|------|
| **A** | "4 项规则投票" vs 三层信号结构 | README/TECHNICAL_GUIDE 描述为 4 项投票；代码实为 极值层(VWAP符号+ADX+KDJ) + 确认层(量比) + 环境层(涨跌停)，`triggered` 要求 `extreme≥2 AND confirm≥1 AND rules≥3` |
| **L** | `hard_trend_filter` 文档与行为差异 | `strategy.py:411-414` 注释称"仅信息记录不再加严"；`backtest.py:734` 开启后否决逆趋势信号 |
| **M** | README 引用 `docs/thin4_zombie_params_audit.md` 但实际已并入 `OPTIMIZATION_ANALYSIS.md` | glob 验证 `docs/` 下无此文件 |

#### D 区 · 命名不一致

| 现象 | 位置 |
|------|------|
| `MINUTE_DATA_DIR`(data.py) vs `MINUTE_BARS_DIR`(paths.py) | 数据目录别名 |
| `LOCK_FILE` vs `POSITIONS_LOCK_FILE` | 锁文件常量 |
| `run_backtest_cli` 未遵循 `batch_main`/`monitor_main` 同构命名 | cli.py |
| `signal_source`(trend_following/mean_reversion) vs `strategy_mode` 语义重叠 | strategy.py |

#### E 区 · 潜在运行风险

| ID | 问题 | 证据 | 影响 |
|----|------|------|------|
| **N** | measurement `.pyc` 加载依赖 Python 3.10 特定版本 | `__init__.py` 用 `SourcelessFileLoader` + monkey-patch `dataclasses._is_type` | 跨版本运行风险，无降级路径 |
| **O** | `_fetch_eastmoney` 用 `lmt=min(n_days*240, 5000)` 估算 K 线数 | `data.py` | 历史远期数据可能拉不全 |
| **P** | `PROJECT_ROOT` 在 7 处重复定义 | backtest.py/risk.py/execution.py/cli.py(×2)/config.py/logging_utils.py/data.py | 虽有 paths.py 统一，仍有 fallback 重复 |

---

## 2. 回测数据特征实证

### 2.1 实验清单与核心结果

| 实验簇 | 标的数 | 区间 | 模式 | 净盈亏 | 胜率 | payoff | 成本/毛利 | 盈利占比 | 数据来源 |
|--------|--------|------|------|--------|------|--------|-----------|----------|----------|
| **TF OOS（主）** | 36 | 2023-07-25~2026-07-22 (3年) | 趋势跟随 | **1,104,931** | 69.09% | 2.015 | 9.67% | 36/36 (100%) | `batch_summary_oos_step2_full.json` |
| └ 训练组(重叠8只) | 8 | 同上 | TF | 218,192 | 68.22% | 1.639 | — | 8/8 (100%) | `step3_group_comparison.json` |
| └ **干净 OOS(28只)** | 28 | 同上 | TF | **888,548** | 69.33% | 2.168 | — | 28/28 (100%) | 同上 |
| MR k41 | 20 | 2025-07-28~2026-07-22 (1年) | 均值回归 | 912,766 | 71.68% | 2.698 | 16.0% | 20/20 (100%) | `batch_summary_K4_mr_k41.json` |
| MR 无筛选 | 500 | 1年 | 均值回归 | 4,924,173 | 68.53% | 3.340 | 19.0% | 476/500 (**95.2%**) | `K_mr_no_amp_full500` 系列 |

**关键观察**：三种设置下胜率均 69%-72%、盈亏比均 >2、盈利标的占比极高（100% / 95.2%）。关掉振幅筛选后仍有 95.2% 盈利 → 正期望主要源自 T+0 配对机制本身，而非振幅筛选或择股能力。

### 2.2 捕获效率（CE）实证

| 分组 | n | 总配对 | CE_pooled_mean | CE_mean_of_means | CE_median_of_medians | 数据来源 |
|------|---|--------|----------------|------------------|---------------------|----------|
| 训练组(8只) | 8 | 2,341 | **5.80%** | 4.55% | 3.33% | `step3.ce.train` |
| 干净 OOS(28只) | 28 | 8,527 | **2.81%** | 2.81% | — | `step3.ce.oos` |

**训练组 CE 是干净 OOS 的 2.06 倍**（5.80% vs 2.81%）。这 8 只重叠标的贡献净盈亏 218,192，占报告总额的 **19.7%**。诚实干净 OOS 净盈亏应约为 **0.89M（28 只）**，且仍建立在其他乐观假设之上。

### 2.3 数据质量与方法论问题（按严重度）

**【高】① OOS 不纯（训练/测试污染）**
- `step1_sample_overlap.json` 确认 `overlap_count: 8`，`consistent_with_F5: false`
- 重叠标的：001309, 300475, 300620, 301526, 603728, 688582, 688615, 688692
- 振幅阈值 0.0494 由 training_100 经 Youden 最优化得出，但 passed_36 中 8 只同时出现在 training_100
- 后果：报告的 1.10M 净盈亏中约 20% 来自被污染标的，且这些标的 CE 被高估约 2 倍

**【高】② 近乎普涨 = 结构性正偏信号**
- 100%（TF/MR 精选集）与 95.2%（MR 无筛选 500 只）盈利占比，在真实 T+0 中极不寻常
- 结合固定止损 0.2% + 移动止盈 20% 的非对称损益结构，以及填充价贴近信号价/无显式市场冲击，判断净盈亏为乐观上界

**【高】③ 无组合层聚合 / 资本约束**
- batch_summary 仅 per_stock 独立求和（base_shares=3000 × 36 只 = 假设每日可同时满仓）
- 真实资本无法在 36 只标的上同时满仓；未做相关性、集中度、组合回撤聚合
- 标题 1.10M 不是可实现的组合收益

**【中】④ 胜率双口径不自洽**
- 同一标的 688498：batch 报 `win_rate=0.7382`（配对口径 282/382）；report 顶层 `win_rate=0.3691`（全口径 282/764）
- 同名指标两种分母，易误导且无法横向对比

**【中】⑤ 成本/敞口场景未加载**
- params.json 实测 `cost_model: null`、`exposure_policy: null`
- yaml 中 optimistic(0.2%)/base(0.27%)/pessimistic(0.5%) 三套成本场景从未在回测生效，仅用 base
- 完全没有悲观情景下的净盈亏 → 尾部风险未知
- `max_holding_bars` 运行值=12（exposure_policy 默认），与 yaml `backtest.max_holding_bars=24` 不一致

**【中】⑥ 数据覆盖不均**
- 001221 仅 237 交易日，688498 达 725 日，区间同为 3 年
- 中证 500 成分股调入调出导致有效数据长度差异巨大
- 直接对净盈亏求和混同了"1 年盈利者"与"3 年盈利者"

**【中】⑦ 跨日腿负 CE（隔夜风险）**
- `step3.per_stock_ce` 多个标的 `cross_day_ce_mean` 为负（如 -0.21, -0.13, -0.02）
- `max_holding_bars=12`（运行值）允许约跨 2 日持有，隔夜暴露侵蚀捕获效率
- T+0 策略 thesis 不应包含隔夜暴露

### 2.4 客观优势（保持公允）

- **A/B 验证文化扎实**：step1-3 OOS 协议、16 组 MR 网格、500 只无筛选对照、J2/J4 四组 A/B，实验设计成熟
- **版本可追溯**：每次运行带 `git_commit`/`param_hash`/`data_hash`，可复现
- **J0 VWAP 修复**（`typical_price×volume` 替代 `amount/volume`）修正了 baostock 未复权导致的数据失真
- **K0 门控诊断**实证支撑了"低偏离走趋势、高偏离走回归"的双模式逻辑
- **配对生命周期与原子写**工程实现稳健

---

## 3. 量化投资视角改进建议（五维度）

> 每项均含"具体动作 + 预期效果"。所有改动仅在配置/脚本/文档层，不触碰已验证的核心信号算法。

### 3.1 代码架构优化

| # | 建议 | 具体动作 | 预期效果 | 优先级 |
|---|------|---------|----------|--------|
| 3.1.1 | **配置单一可信源** | 统一 `CostModel`/`ExposurePolicy` 加载入口；消除 `max_holding_bars`(12 vs 24) 两处定义；yaml `cost.scenario` 真正注入 `BacktestParams`（对应审计 E/F/H2） | 消除"配置了不生效"缺陷，回测可复现性↑ | P0 |
| 3.1.2 | **指标定义层集中化** | 新建 `metrics` 模块统一定义 `win_rate`(主口径=配对)、`CE`、`net_pnl`(gross vs net)，供 batch/report 复用 | 消除 73.8% vs 36.9% 同名歧义 | P1 |
| 3.1.3 | **新增 `PortfolioAggregator`** | 输入各股 `daily_results`，施加资本约束（最大并发标的数、单标的上限），输出组合逐日 PnL、Sharpe、最大回撤 | 把"独立求和"变为可兑现收益序列 | P1 |
| 3.1.4 | **合并风控接口** | 将 `approve_signal`(回测) 与 `check_risk`(实盘) 合并为统一接口，参数化回测/实盘差异 | 消除两套实现，降低维护成本 | P2 |
| 3.1.5 | **清理死代码** | 删除 `_compute_pairing_threshold`/`grid_search`/`regime.py` 死代码；对 `open_max_vwap_dev`/`min_net_expected_return` 加 `# DEPRECATED` 注释 | 降低新成员维护困惑 | P2 |
| 3.1.6 | **PROJECT_ROOT 收口** | 删除 7 处 fallback 重复定义，统一收口到 `paths.py` | 消除路径歧义 | P3 |
| 3.1.7 | **measurement 层可验证化** | 对加密 `.pyc`(TSD) 提供校验 harness 或开源计算函数；补 Python 版本检测和降级路径 | 允许独立验证 IC/alpha-decay，破除黑箱；避免跨版本运行风险 | P2 |

### 3.2 策略逻辑改进

| # | 建议 | 具体动作 | 预期效果 | 优先级 |
|---|------|---------|----------|--------|
| 3.2.1 | **门控真正接入信号** | 将 `K0_gate`(3% VWAP 偏离分界) 写入 `strategy.py`，<3% 仅触发趋势、>3% 仅触发回归 | 避免模式错配交易；稳健性↑（可能降交易笔数） | P1 |
| 3.2.2 | **收紧跨日** | 核心模式 `max_holding_bars` 限为日内（如 12 bar=同 session），或强制次日开盘平；隔夜收取风险溢价 | 消除负 CE 隔夜拖累（step3 多项为负） | P1 |
| 3.2.3 | **校验非对称损益** | 审视固定 0.2% 止损 + 20% 移动止盈是否过于机械正向；加入"下一根 VWAP 成交"而非同根成交、显式买卖价差/冲击成本 | 期望更贴近实盘（预计净盈亏下调但更可信） | P2 |
| 3.2.4 | **MR 提为主力之一** | MR(Stage K) payoff 2.7-3.3 高于 TF，对高偏离区间应考虑作为主模式而非纯备选 | 高偏离区间收益质量↑ | P2 |
| 3.2.5 | **hard_trend_filter 增强** | 当前仅在无反向腿时拦截；改为双向拦截或阈值化（如 ADX<20 且无量能时禁开） | 拦截率↑，回撤改善（可能牺牲部分收益） | P3 |

### 3.3 风险控制机制完善

| # | 建议 | 具体动作 | 预期效果 | 优先级 |
|---|------|---------|----------|--------|
| 3.3.1 | **默认加载悲观成本情景** | 报告同时输出 opt/base/pess 三套净盈亏，而非仅 base | 暴露"净盈亏可能转负"的尾部 | P0 |
| 3.3.2 | **组合层风控** | 集中度上限（单标的最大 NAV%）、相关性感知仓位、日内组合回撤止损 | 防范"系统性下跌时 36 只同跌"——个股回测掩盖的相关性风险 | P1 |
| 3.3.3 | **启用未生效检查项** | `check_risk` 当前仅强制 `min_capture_spread`；接入 `min_net_expected_return`(当前零引用) 与 L1/L2 软熔断 | 拦截边际/负 EV 交易 | P1 |
| 3.3.4 | **T+0 严守日内** | 强制 EOD 扁平，跨日暴露计为违规 | 使风险与策略 thesis 一致 | P1 |
| 3.3.5 | **修复 overlay 字段名** | `RiskParams` 增加 `max_position_pct`/`max_t_size`/`eod_force_flat` 字段，或 `load_risk_params` 做字段别名映射 | 模拟盘 15% 仓位上限 / 尾盘强平真实生效 | P1 |

### 3.4 回测准确性提升（**最高优先级**）

| # | 建议 | 具体动作 | 预期效果 | 优先级 |
|---|------|---------|----------|--------|
| 3.4.1 | **P0：重建纯净 OOS** | 36 只集合中剔除与 training_100 重叠的 8 只；阈值 0.0494 仅在 training_100 上最优化；干净 OOS 仅报 28 只 | 消除 2 倍 CE 虚高；诚实期望≈0.89M；决定能否进实盘 | **P0** |
| 3.4.2 | **P0：消除振幅筛选数据泄漏** | `backtest_zz500.py:581` `filter_codes_by_amplitude` 增加 `pre_start_date` 参数，从 `start_date - 60` 之前的数据计算振幅；无更早数据时显式抛出警告 | 恢复样本外属性，避免选股窗口泄漏 | **P0** |
| 3.4.3 | **P1：资本受限组合模拟** | 逐日归一 + 资本上限聚合（最大并发标的数、单标的 NAV 上限） | 得到可兑现收益，而非 1.10M 独立求和 | P1 |
| 3.4.4 | **P1：口径标准化与文档化** | 统一 `win_rate`(主口径=配对) 并在标题暴露 CE、Sharpe、最大回撤 | 报告可信、可横向对比 | P1 |
| 3.4.5 | **P1：执行 realism** | 加入参与度(market-impact)、下一根 VWAP 成交、买卖价差、配对成功率(非 100% 可配对) | 收窄与实盘 gap（预计净盈亏显著下调但更真实） | P1 |
| 3.4.6 | **P2：数据覆盖归一** | 用 `data_fingerprint.trading_days` 做 `净盈亏/日` 与年化；剔除或标注 <X 日标的 | 苹果对苹果比较 | P2 |
| 3.4.7 | **P2：滚动 Walk-forward OOS** | 当前为单 3 年窗口，增加滚动多段以测稳定性 | 检验策略是否过拟合单一区间 | P2 |

### 3.5 策略参数调优方向

| # | 建议 | 具体动作 | 预期效果 | 优先级 |
|---|------|---------|----------|--------|
| 3.5.1 | **网格系统化** | `stop_loss_ratio`∈[0.001,0.003]、`retracement_vwap_band`(z)∈[1.0,2.5] 已网格；每格同时报三成本情景净盈亏，找"成本前净盈亏峰值"拐点 | 定位稳健止损/回撤带，而非单点最优 | P1 |
| 3.5.2 | **释放被钉死参数** | `vwap_dev_atr_multiplier` 已钉死 [0.8]；要么给出理论依据，要么纳入网格 | 避免错过更优区 | P2 |
| 3.5.3 | **剔除僵尸参数出网格** | `open_max_vwap_dev`、`min_net_expected_return` 当前从不读取，不应占用网格维度 | 调优算力聚焦有效参数 | P2 |
| 3.5.4 | **多目标调优** | 目标从单一 `net_pnl` 改为 (净盈亏, Sharpe, 最大回撤, 盈利标的比例, CE 稳定性) | 参数更鲁棒，降低过拟合 | P1 |
| 3.5.5 | **分区间调参** | 借 K0 门控，<3% 桶专调趋势参数、>3% 桶专调回归参数，而非全局一套 | 各区间拟合更优 | P2 |
| 3.5.6 | **修正寻优网格** | `PARAM_GRID` 移除 `vwap_dev_atr_multiplier`/`rsi_*`；加入活跃参数 `tf_adx_threshold`/`tf_vol_ratio_min`/`tf_kdj_reverse_bars`/`min_capture_spread` | optimize 结果对当前策略有指导意义 | P2 |

---

## 4. 优先级路线图

| 优先级 | 动作 | 预期影响 | 对应建议 |
|--------|------|---------|----------|
| **P0** | 重建纯净 OOS（剔除 8 只污染标的） | 诚实期望≈0.89M；消除 2× CE 虚高；决定能否进实盘 | 3.4.1 |
| **P0** | 消除振幅筛选数据泄漏 | 恢复样本外属性 | 3.4.2 |
| **P0** | 修复成本场景加载 + 默认悲观情景 | 暴露真实风险边界 | 3.3.1, 3.1.1 |
| **P1** | 资本受限组合模拟 + 口径统一 | 可信、可兑现的收益与尾部视图 | 3.4.3, 3.4.4, 3.1.3 |
| **P1** | 市场门控接入信号 + 跨日收紧 | 去除模式错配与负 CE 隔夜拖累 | 3.2.1, 3.2.2 |
| **P1** | overlay 字段修复 + 组合层风控 | 模拟盘风控生效；防范相关性风险 | 3.3.5, 3.3.2 |
| **P1** | 多目标调优 + 分区间调参 | 参数更鲁棒 | 3.5.4, 3.5.5 |
| **P2** | 执行 realism（冲击/价差/下一根成交/配对成功率） | 收窄与实盘 gap | 3.4.5 |
| **P2** | 滚动 OOS + 数据归一 + 网格修正 | 鲁棒性与稳定性↑ | 3.4.7, 3.4.6, 3.5.6 |
| **P3** | 文档对齐 + 死代码清理 + 命名统一 | 可维护性↑ | 3.1.5, 3.1.6, 3.2.5 |

---

## 5. 结论与行动映射

- **对策略本身的判定**：`重新评估 / 等待证据`。当前 1.10M 净盈亏夸大了可兑现性（污染 20% + 普涨 + 无组合约束 + 乐观执行）。修复 P0 后若干净 OOS 仍稳健（≈0.89M 且 CE 稳定、盈利标的比例合理），再进入 paper/live 小资金验证。
- **对工程/研究的判定**：`持有`（信号框架与验证文化值得保留），但需补齐"配置生效、指标口径、组合层、执行 realism"四块短板。
- **不可逾越的边界**：本审查未改动任何 `.py`/`.yaml` 代码；所有建议落地需另行评审与回归测试。

---

## 附录：数据出处（可追溯）

- 主 OOS：`outputs/backtest/batch_summary_oos_step2_full.json`（36 只 × 3 年，generated 2026-07-29）
- OOS 污染：`outputs/oos_validation/step1_sample_overlap.json`（overlap_count=8, consistent_with_F5=false）
- CE 对比：`outputs/oos_validation/step3_group_comparison.json`（train CE_pooled=0.058 vs oos CE_pooled=0.0281）
- 运行参数：`outputs/runs/20260729_094130_1e58027d_0eec34db/params.json`（cost_model=null, exposure_policy=null, max_holding_bars=12）
- MR 对照：`outputs/backtest/batch_summary_K4_mr_k41.json`、`K_mr_no_amp_full500` 系列（500 只）
- 配置文件：`config/thresholds.yaml`（J2 enabled=true, J4 enabled=false, sl=0.002, tr=0.2, tap=0, cd=24, mh=24）
- 代码层既有审计：`docs/OPTIMIZATION_ANALYSIS.md`（2026-07-28，A/B/C/D 四区 12 项问题 + 8 个实验）
- 回测数据既有审查：`docs/BACKTEST_DATA_AND_QUANT_REVIEW.md`（2026-07-29，8 项核心发现）
- 运行手册：`docs/TECHNICAL_GUIDE.md`（§11 回测口径与数据质量须知）

> 本报告仅供参考，不构成任何投资建议或实盘操作建议。
