# L5 T+0 日内做T策略 — 代码与策略深度优化分析

> 分析范围：`src/at0/{strategy,backtest,risk,features,config,execution}.py`、`scripts/{backtest_zz500,optimize_zz500_params}.py`、`config/{thresholds,overlays/paper,overlays/research}.yaml`、`README.md`、`docs/TECHNICAL_GUIDE.md`、`docs/thin4_zombie_params_audit.md`
> 方法：只读静态审查 + grep 交叉验证（关键断言均经源码二次确认）。未执行任何代码修改。
> 日期：2026-07-28

---

## 0. 一句话结论

策略**信号逻辑与回测引擎本身质量不差**（指标层严格因果、撮合/锁仓正确），但存在一组**"配置写了却不生效 / 文档说了却没做 / 寻优调了没用的参数"** 的系统性一致性问题。其中 **振幅筛选的数据泄漏** 与 **回测未接入市场门控** 会直接抬高"样本外"收益预期，是上线前必须优先处理的两类风险。

---

## 1. 优化点清单（按严重度分区）

### A 区 · 死代码 / 配置失效（影响正确性与可维护性）

| ID | 问题 | 证据（源码已验证） | 影响 |
|----|------|-------------------|------|
| **B** | `open_max_vwap_dev`（方案A开仓深度上限）是死代码 | `strategy.py:108` 定义默认值 `0.012`；**全 `src/` 仅此一处出现**，evaluate 函数体内从不读取 | 文档声称"防 86.7% 超时腿"的控制**实际从未生效** |
| **C** | `min_net_expected_return`(0.0045) 定义却从未执行 | `thresholds.yaml` 有定义；**`src/` 零引用**；`risk.check_risk` 只查 `min_capture_spread` | 预期收益门槛形同虚设，僵尸配置 |
| **K** | `open_vwap_dev` 字段 + `_compute_pairing_threshold` 死代码 | `execution.py:75` 仍存字段；`strategy.py:253` `@deprecated` 均值回归平仓阈值 | 历史遗留，thin4 审计建议保留但应显式标记 |
| **D** | 参数寻优网格含"不生效参数" | `backtest.py:1189` `PARAM_GRID` 含 `vwap_dev_atr_multiplier`/`rsi_overbought`/`rsi_oversold`；活跃趋势信号只用 VWAP符号+ADX+KDJ+量比 | optimize 结果对当前策略**指导意义有限**（line 1604 已把 `vwap_dev_atr_multiplier` 钉死为 `[0.8]`，属部分修复迹象） |

### B 区 · 配置加载缺口（回测 vs 实盘行为不一致）

| ID | 问题 | 证据 | 影响 |
|----|------|------|------|
| **E** | `BacktestParams` dataclass 默认值与 yaml 分歧 | dataclass：`stop_loss=0.008`,`trailing=0.005`,`cooldown=12`(`backtest.py:351/355/358/360`)；yaml：`0.02/0.0/3` | 入口脚本已调 `load_backtest_params` 覆盖（yaml 生效），但 **PyYAML 缺失/回退时落错值**，潜在隐患 |
| **F** | 成本模型加载缺口 | `config.py:106` `load_backtest_params` 只读 `backtest` 段；`backtest.py:489` 用兼容字段重建 base 0.27%，**不读 `cost` 段 scenario**；`load_cost_model` 仅 `cli.py` 调用 | **pessimistic/optimistic 成本场景在回测中不生效** |
| **G** | overlay 字段名不匹配被静默忽略 | `paper.yaml` 用 `max_position_pct/max_t_size/eod_force_flat`；`RiskParams` 字段为 `max_t_size_ratio/...`；`load_risk_params` 用 `hasattr` 过滤 → 不匹配字段丢弃 | 模拟盘 15% 仓位上限 / `eod_force_flat` **不生效** |
| **H** | 市场门控仅 live 路径，回测缺失 | `backtest_single_day` 直调 `evaluate_reduce/add_signal`(`backtest.py:666/677`)；`market_gate_for_add` 仅在 `evaluate_all_signals`(`strategy.py:748`) 应用 | 回测与实盘**行为不一致**，回测未反映情绪风控（COLD 禁加仓等） |

### C 区 · 文档与代码不一致

| ID | 问题 | 证据 | 影响 |
|----|------|------|------|
| **A** | "4 项规则投票" vs 三层信号结构 | `README.md`/`TECHNICAL_GUIDE.md` 描述为 4 项投票；代码实为 极值层(VWAP符号+ADX+KDJ) + 确认层(量比) + 环境层(涨跌停过滤)，`TSignal.triggered` 要求 `extreme≥2 AND confirm≥1 AND rules≥3` | 维护困惑；功能近似但描述失真，新成员易误改 |

### D 区 · 方法学 / 统计偏差

| ID | 问题 | 证据 | 影响 |
|----|------|------|------|
| **I** | 振幅筛选窗口数据泄漏 | `backtest_zz500.py:608` `use_dates = in_range_dates[:window]`（回测窗口**内**前 60 日）；line 605-607 注释承认"本地数据从 start_date 开始，无更早数据" | **样本内选择偏差**，F5 验证"样本外"性质被打折扣 |
| **J** | `hard_trend_filter` 效果有限 | `backtest.py:694/698` 仅当**无反向 open 腿**时拦截 | 启用后仍能开仓，风控收口弱 |

---

## 2. 优势（无需优化，保持）

- **指标层严格因果、无前视偏差**（`features.py`：cumulative_vwap、intraday_bollinger 用 `bars[-period:]`、kdj 连续递推修复、volume_ratio 窗口不重叠）—— 这是策略的护城河，别动。
- **撮合/锁仓正确**（`execution.py`：FIFO 配对、holding_bars 跟踪、max_favorable/max_adverse 盘中极值、`check_stop_loss` 移动止盈+固定止损兜底、`check_expiry` 超时标记；`apply_t_trade`/`reset_today_state` 原子读-改-写文件锁、T+1 锁定）。
- **振幅筛选方向有统计依据**（`min_amplitude_long=0.0494` 为 Youden 最优阈值，60 日日均振幅下限；F5 500 股全池 -42 万 → +15 万）—— 阈值本身合理，问题仅在*选股窗口*（见 I）。

---

## 3. 可执行实验步骤

> 约定：所有实验先在当前 `base` 成本 + 全池 F5 回测下建立 **baseline**，再叠加单变量改动，避免多变量混淆。回测入口 `scripts/backtest_zz500.py` 已支持 `--no-amplitude-filter` 等开关。

### 实验 1 · 消除振幅筛选数据泄漏【优先级：高】
- **目的**：把选股从"回测窗口内前 60 日"改为"start_date 之前的 pre-window"，恢复样本外属性。
- **改动点**：`backtest_zz500.py:581 filter_codes_by_amplitude` —— 增加 `pre_start_date` 参数，从 `start_date - 60` 个交易日之前的数据计算振幅；若本地无更早数据，显式抛出警告并退化为 `--no-amplitude-filter` 对照。
- **运行**：
  ```bash
  # 修复后（需本地有更早数据）
  python scripts/backtest_zz500.py --start 2024-01-01 --end 2024-06-30
  # 对照：禁用振幅筛选
  python scripts/backtest_zz500.py --start 2024-01-01 --end 2024-06-30 --no-amplitude-filter
  ```
- **验证指标**：F5 净盈亏、亏损股数占比、阈值命中率；重点看修复后收益相对原值**回落幅度**（即被泄漏虚增的部分）。
- **预期**：净盈亏较当前报告值下修；若下修显著，说明原 F5 验证的"样本外"结论需重述。

### 实验 2 · 修复成本场景加载【优先级：高】
- **目的**：让回测尊重 `cost.scenario`，使 pessimistic 成本真实生效。
- **改动点**：`backtest.py:489 get_cost_model` 优先调用 `config.load_cost_model()`（读 `cost` 段 scenario）；回测入口注入 `cost_model` 而非用兼容字段重建。
- **运行**：
  ```bash
  # base
  python scripts/backtest_zz500.py --cost-scenario base
  # pessimistic（滑点0.2%+冲击0.05%）
  python scripts/backtest_zz500.py --cost-scenario pessimistic
  ```
- **验证指标**：两场景下 net_pnl 衰减比例、盈亏平衡所需振幅阈值。
- **预期**：pessimistic 下净盈亏显著低于 base，暴露真实风险边界（当前 pessimistic 在回测中是被忽略的"纸面风控"）。

### 实验 3 · 修复 paper/research overlay 字段名【优先级：中】
- **目的**：让模拟盘 15% 仓位上限与尾盘强平真实生效。
- **改动点**：二选一 —— (a) `RiskParams` 增加 `max_position_pct`/`max_t_size`/`eod_force_flat` 字段；或 (b) `load_risk_params` 做字段别名映射（paper.yaml 旧名 → dataclass 新名）。推荐 (a) 并保留向后兼容。
- **运行**：`paper.yaml` 下跑模拟盘路径，断言单票仓位 ≤15%、14:50 前强制平仓。
- **验证指标**：最大单票仓位占比、EOD 未平腿数。
- **预期**：当前 paper overlay 近乎无效，修复后模拟盘风控与实盘一致。

### 实验 4 · 市场门控接入回测【优先级：高】
- **目的**：消除回测/实盘行为不一致，把情绪风控纳入回测评估。
- **改动点**：`backtest_single_day` 构造 `MarketSnapshot` 并传入，调用 `evaluate_all_signals`（含 `market_gate_for_add` / `adjust_signal_weight` / `use_continuous_alpha` 分支），或显式在开仓前调用 `market_gate_for_add(market)`。
- **运行**：对比"接入门控"vs"原直调"两版在 COLD 行情日的回撤差异。
- **验证指标**：COLD 日胜率、单日最大回撤、全年 net_pnl。
- **预期**：接入后 COLD 日开仓被抑制，回撤下降；原回测高估了情绪极端日的表现。

### 实验 5 · 清理或重新启用 `open_max_vwap_dev`【优先级：中】
- **选项 A（清理）**：删除 `strategy.py:108` 定义及 `execution.py:75` 遗留字段，更新 thin4 审计标记。低风险，消除误导。
- **选项 B（重新启用）**：在 `evaluate_add_signal` 中读取 `open_max_vwap_dev` 作为开仓深度上限（VWAP 偏离超过该值不追单），验证能否降低超时腿（`expired`）比例。
- **运行（B）**：
  ```bash
  python scripts/backtest_zz500.py --open-max-vwap-dev 0.012   # 启用
  python scripts/backtest_zz500.py --open-max-vwap-dev 0.0     # 关闭对照
  ```
- **验证指标**：超时腿占比、超时腿平均亏损、整体胜率。
- **预期**：若启用后超时腿显著下降且净盈亏不降，则 B 优于 A。

### 实验 6 · 参数寻优网格修正【优先级：中】
- **目的**：让 optimizer 调的是当前策略真正使用的参数，避免过拟合到死参数。
- **改动点**：`backtest.py:1189` / `optimize_zz500_params.py:70` 的 `PARAM_GRID` —— 移除 `vwap_dev_atr_multiplier`/`rsi_*`；加入活跃参数：`tf_adx_threshold`、`tf_vol_ratio_min`、`tf_kdj_reverse_bars`、`min_capture_spread`、`stop_loss_ratio`、`max_holding_bars`、`cooldown_bars`。
- **配合**：改用 **walk-forward**（滚动窗口训练/验证）替代单次全样本网格，抑制过拟合。
- **运行**：`python scripts/optimize_zz500_params.py --walk-forward`
- **验证指标**：样本内 vs 样本外 net_pnl 衰减比（>30% 提示过拟合）。
- **预期**：参数重要性重新排序，且样本外衰减收敛。

### 实验 7 · 文档对齐【优先级：低，但建议随代码改动同步】
- 更新 `README.md` / `TECHNICAL_GUIDE.md` 的"4 项规则投票"为**三层信号结构**描述。
- 在 `thresholds.yaml` 对僵尸参数（`min_net_expected_return`、`open_max_vwap_dev`）加 `# DEPRECATED` 注释，避免误用。

### 实验 8 · `hard_trend_filter` 增强【优先级：低】
- **目的**：当前仅在无反向腿时拦截，收口弱。
- **改动点**：改为双向拦截或阈值化（如 ADX<20 且无量能时禁开），在 `backtest.py:694/698` 放宽前置条件。
- **验证指标**：拦截率、被拦截交易的"若执行"亏损、整体回撤。
- **预期**：拦截率上升，回撤改善但可能牺牲部分收益，需权衡。

---

## 4. 建议执行顺序（按 ROI）

1. **实验 1（数据泄漏）** → 立即影响"样本外"结论可信度，先做。
2. **实验 4（市场门控接入回测）** → 回测/实盘一致性，上线前必做。
3. **实验 2（成本场景加载）** → 暴露真实风险边界。
4. **实验 3 / 5（overlay 字段 & open_max_vwap_dev）** → 配置有效性修复。
5. **实验 6（寻优网格修正）** → 在以上修复后重跑，结果才有意义。
6. **实验 7 / 8（文档 & 趋势过滤增强）** → 收尾与增强。

---

## 5. 待确认事项（需你拍板）

- **实验 5** 选 A（清理）还是 B（重新启用）？涉及行为改动，thin4 审计建议"不删但标记"，但 B 可能带来实打实的超时腿改善。
- **实验 1** 本地是否存有 `start_date` 之前的更早行情？若无，泄漏只能靠 walk-forward 间接缓解，无法彻底消除。
- **paper.yaml** 的 `max_position_pct` 等字段，是希望改名对齐 `RiskParams`，还是在 `RiskParams` 中新增字段？影响 (a)/(b) 方案选择。
