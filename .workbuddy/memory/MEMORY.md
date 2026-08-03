# a-t0-v2 项目长期记忆

## 项目定位
A股 T0（日内回转）量化策略，5min K线，ZZ500 成分股。当前处于 **V4 结构优化**阶段。

## 研发流程约定（机构级，用户明确指定）
- Stage 顺序：**M（Measurement）→ W（Wave）→ S（Support）→ E（Expected Move）→ H（Hold Confidence）→ X（Predictive Exit）→ D（多引擎融合）→ O（全局调参）**，串行推进。
- **Measurement 必须先于 Alpha 开发**。理由：不能只知道"收益提高了"，必须能归因到"买早了/卖晚了/还是运气"。
- **Stage 验收看过程指标（CE / Entry Delay / Exit Delay / MAE / Trend Quality），不看净利润**；净利润只作「不得显著变差」的护栏（当前阈值 -10%）。
- **一律以 median + IQR 判定，不用均值**——均值会被少数极端交易带偏。
- 样本量 < 30 对配对交易时判 INCONCLUSIVE，不允许"看起来变好了"。

## 关键架构约束
- `score/alpha_score.py: compute_alpha_score_v3` 用 `bars = snap.get("_bars", [])` 决定是否实例化 Engine：
  **`_bars` 没注入 = 五个 Engine（Wave/Support/ExpectedMove/Regime/Risk）全部空转**。
  回测与实盘必须都注入，否则两边口径不一致、A/B 结论全部失效。开关：`SignalParams.v3_engines_in_backtest`（默认 True）。
- 调用链：`strategy.compute_alpha_score` → `strategy_alpha.compute_alpha_score`（读 `snap["_direction"]`）→ `alpha_score.compute_alpha_score_v3(snap, params, direction)`。

## 测量口径铁律
- **指标不可用一律返回 `None`，禁止用 -1 / 0 兼作哨兵值。**
  `entry_delay_bars = -1`（早1根入场）、`exit_delay_bars = 0`（极值点平仓）都是**合法且最优**的取值，
  用它们当哨兵会在聚合时把最好的样本过滤掉，造成系统性偏差。
- 聚合过滤条件写 `isinstance(v, (int,float)) and not isinstance(v, bool)`，**不要写 `if v > 0` 或 `if v != -1`**。
- 全池统计要在**所有配对交易**上算，不要"每股均值再平均"（mean of means 会失真）。
- 展示层可以把 None 显示为 "—"，但**统计层绝不能补 0**（"没数据" ≠ "表现为0"）。

## 关键文件
- `src/at0/measurement/v2_metrics.py` — METRIC_REGISTRY 注册式指标框架 + 分布/归因
- `src/at0/measurement/v2_reporter.py` — HTML / CSV 报告
- `scripts/compare_v2_ab.py` — A/B 比较器 + Stage Gate 判定（`--base --cand --gate`）
- `scripts/selfcheck_measurement.py` — 合成数据自检，改完 measurement 先跑这个
- `scripts/diag_v2_measurement.py` — 单组诊断报告

## 已知历史坑
- `batch_summary_w_wave_v1.json` 与 `batch_summary_v3_b5_optimized.json` 曾逐字节相同 —— 这是 Engine 空转的典型症状。
  **A/B 两组结果完全一致时，先怀疑改动没进决策路径，不要急着解读指标。**
  `compare_v2_ab.py` 已内置 `identical_to_base` 检测，会直接判 `NO_EFFECT`。
</content>
