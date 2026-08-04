# a-t0-v2 代码审查报告

> 审查范围：全部活跃源码（`src/at0/` 的 decision 路径、measurement 层、回测/执行/风控/特征/数据/配置层、脚本入口、配置与 qlib 集成）。
> 审查方式：逐文件精读 + 调用链交叉验证。**本报告仅做审查与建议，未修改任何代码。**
> 严重级别：Critical（静默失效/直接扭曲收益） > High（正确性或口径错误） > Medium > Low。

---

## 0. 执行摘要（最该先看的 7 件事）

| # | 级别 | 问题 | 位置 | 影响 |
|---|---|---|---|---|
| 1 | **Critical** | overlay（paper/research）**段名/字段名写错**，模拟盘悲观成本、仓位护栏、尾盘强平全部静默失效 | `config/overlays/paper.yaml`、`research.yaml` + `config.py:140-142` | 误以为有风控护栏，实盘/模拟盘风险敞口无约束 |
| 2 | **Critical** | **Engine 空转陷阱**：`_bars` 缺失时五个 Engine 静默退化成常数，函数无任何告警 | `src/at0/score/alpha_score.py:443-444` | 回测/实盘口径可在一次配置翻转后彻底不一致，历史"逐字节相同"根因 |
| 3 | **Critical** | **CLI `backtest`/`optimize` 子命令不读 `thresholds.yaml`**，与 `backtest_zz500.py` 口径完全不同 | `src/at0/cli.py:237-242`（`run()` 用 `SignalParams()`/`RiskParams()` 默认） | 同一股票同一区间两条入口结果不同；CLI 路径连振幅筛选都不做 |
| 4 | **Critical** | **默认参数漂移**：`BacktestParams` 兜底默认 `stop_loss=0.008 / max_holding=12 / cooldown=12`，与 yaml 定案 `0.002 / 24 / 24` 不符；yaml 缺键时静默用错值 | `src/at0/backtest.py:359/363/366` | yaml 缺字段 → 止损宽松 4 倍、超时强平早一倍，调参结论失真 |
| 5 | **High** | **止损 / MR 移动止盈平仓漏计平仓侧成本** → 净盈亏系统性高估 | `src/at0/backtest.py:875-887`、`716-728` | 所有含止损/MR-TP 的策略回测收益偏乐观 |
| 6 | **High** | 信号用当根 OHLC 极值判定、却以**收盘价**成交 → 入场价乐观偏差（J2 回踩尤甚） | `src/at0/backtest.py:649`、`990-1016` | 回测成交价优于信号触发真实可成交价 |
| 7 | **High** | 测量层**头牌指标用 mean-of-means**（`overall.avg_ce`），与 Stage Gate 用的全池 median 口径不一致；且 `spearman_ic` 空集返回 `0.0` 伪造"无相关" | `v2_metrics.py:981-997`、`639-640` | 报告中"最显眼数字"与验收门槛算的不是同一个东西，且已污染产物 JSON |

> 共同特征：**大量"静默失效"**——配置写错、参数缺位、注入缺失都不报错，只在收益数字上悄悄偏离预期。这是本项目最高危的一类问题。

---

## 1. 整体逻辑梳理（数据如何从 K 线流到信号 / 再到收益）

```
本地 5min K线 (zz500_5min/)
   │  scripts/backtest_zz500.py (含振幅筛选 + 抽样)  ← 主入口(读yaml)
   │  src/at0/cli.py (backtest/optimize)            ← 次入口(不读yaml, 见摘要#3)
   ▼
backtest.backtest_multi_day
   ├─ 逐日 backtest_single_day 主循环（每根 K 线）：
   │     ① update_holding（盘中极值口径）
   │     ② MR 移动止盈 / check_stop_loss（固定+移动止损）
   │     ③ check_expiry（超时强平）
   │     ④ 一字板早退
   │     ⑤ 信号评估：features.compute_reference_snapshot(bars[:i+1])
   │           └─ 布尔三层触发 或 连续 Alpha 分支（use_continuous_alpha=True 默认）
   │     ⑥ 冷却约束
   │     ⑦ _execute_trade → approve_signal 风控 + 成本模型 + TradeLifecycle FIFO 配对
   ├─ 跨日串联：carry_open_legs / carry_locked（T+1 底仓解锁）
   └─ 残留腿按末日 close 计浮盈 → net_pnl_with_unrealized
   ▼
连续 Alpha 分支（V4 核心）：
   features 快照 → compute_alpha_score → strategy_alpha → alpha_score.compute_alpha_score_v3
       ├─ FeatureVector.from_snapshot（G1 占位因子）
       ├─ bars = snap.get("_bars", []) → engines = {} 或 _get_engines()   ← 空转陷阱(摘要#2)
       ├─ 七维融合：Trend(+Regime 0.4) / Wave / Support / Momentum / Liquidity / ExpectedMove / Risk
       └─ DecisionEngineV4.decide（Stage D/X：开平阈值、RR 闸门、HoldConfidence、PredictiveExit）
   ▼
outputs/backtest/batch_summary_*.json + measurement 层（v2_metrics 注册式指标 → median+IQR → compare_v2_ab Stage Gate）
```

**主干正确性结论**：回测的 FIFO 配对、T+1 底仓、冷却、超时、跨日延续逻辑整体严谨（有多处 P0 整改痕迹）；成本模型（印花税仅卖侧、滑点双侧、`round_trip_cost=0.0027`）与 T+0 不裸卖空约束均正确。问题集中在**配置加载链、成本记账完整性、以及"静默退化"防护缺失**。

---

## 2. 漏洞与安全审查

整体安全面**良好**：

- ✅ 未发现 `eval` / `exec` / `pickle` / `shell=True` / 硬编码 token 密码。
- ⚠️ **HTML 报告未对注入值做转义**（存储型 XSS / 渲染破坏风险）：
  - `src/at0/reports.py:311,358,945,1047`（直接 f-string 拼 `code`/日期/JSON 进 `<script>`，`</script>` 或 U+2028/2029 会提前终结标签）；
  - `src/at0/measurement/v2_reporter.py:380-427`（`tag/code/date/direction` 未 `html.escape`）。
  - 修复：文本 `html.escape`；script 内 JSON 做 `</`.replace 与 `\u2028/2029` 转义；表格用 `textContent`。
- ⚠️ **路径穿越防护缺失**：`src/at0/paths.py:98-106` 直接拼接 `*parts`；正常调用安全，但将来若传入含 `..` 的 `code/date` 可写出任意路径。建议对 `code/date` 加白名单 `^[0-9A-Za-z._-]+$`。
- ⚠️ **硬编码绝对路径散落**（可移植性/静默失效，非注入）：`config/thresholds.yaml:13`、`scripts/compare_v2_ab.py:30`、`gen_amplitude_pool.py:22`、`migrate_minute_local_to_merged.py:24`，以及 **`run_baseline_measurement.py:104` 指向旧项目 `D:\project\a-t0\data`（非本仓库）** → 缓存为空时静默"无数据"退出，给"已跑通基线"假象。统一改用 `at0.paths`（`AT0_DATA_DIR` 环境变量已支持）。
- ⚠️ **HTML 报告依赖外网 CDN（chart.js）** `reports.py:312,906,1023`：离线/内网打开图表全失效，建议内联精简版。

---

## 3. Bug 清单（按严重程度）

### 3.1 Critical

**C1. overlay 段名/字段名错误 → 模拟盘风控静默失效**（已核实）
- `config/overlays/paper.yaml:10-16`、`research.yaml:10-16` 用顶层键 `cost_model:` 与 `risk:`；但 `config.py:140-142` 的 `load_cost_model` 读的是 `data["cost"]`（不是 `cost_model`），合并后 `data["cost"]` 仍是 base → **悲观成本从未生效**。
- `risk` 下的 `max_position_pct` / `max_t_size` / `eod_force_flat` **均不在 `RiskParams` 中**（`RiskParams` 只有 `max_t_size_ratio / max_t_trades_per_day / min_capture_spread / eod_check_time`），被 `hasattr` 过滤静默丢弃；正确字段名应为 `max_t_size_ratio`。
- 根因：overlay 编写时未与 loader 段名、dataclass 字段名对齐；loader 的 `hasattr` 过滤"吃掉"错误键而不告警。
- 修复：overlay 改为 `cost: {scenario: pessimistic}` + `risk: {max_t_size_ratio: 0.15}`；并在 `load_risk_params`/`load_cost_model` 对"yaml 有但 dataclass 无"的键打印 `WARNING: 忽略未知键 xxx`。补单测：设 `AT0_OVERLAY=paper` 后断言 `load_cost_model().scenario == "pessimistic"`。

**C2. Engine 空转陷阱**（已核实）
- `src/at0/score/alpha_score.py:443-444`：`bars = snap.get("_bars", [])`；`engines = _get_engines() if bars else {}`。`_bars` 缺失 → `engines={}`，Wave/Support/ExpectedMove/Regime/Risk 全部回退 G1 占位；expected_move 与 risk 恒返 50，信号退化为近常数（正是 memory 记载"w_wave_v1 与 v3_b5 逐字节相同"根因）。
- 当前 `backtest.py:1045-1046`（默认 `v3_engines_in_backtest=True`）与 `strategy.py:1174` 有注入；但 `backtest.py:1048-1049`（`v3_engines_in_backtest=False`）与任何新调用方/单测缺失 `_bars` 时**静默退化且无任何日志**。
- 修复：在 `compute_alpha_score_v3` 入口加守卫——`assert "_bars" in snap` 或 `logger.warning("Engine 空转：_bars 缺失")`；建议删除 `v3_engines_in_backtest` 开关，让 Engine 始终运行（bars 由调用方保证）。

**C3. CLI 回测入口不读 yaml**（已核实）
- `src/at0/cli.py:237-242`（`run()`）直接 `BacktestParams(signal_params=SignalParams(), risk_params=RiskParams())`，未调任何 `load_*_params()`；`run_backtest_cli`/`batch_main` 都经 `run()`。而 `monitor_main()`（L926）正确加载。结果：CLI 路径用全默认参数且**不做振幅筛选**，与 `backtest_zz500.py` 不可比。
- 修复：`run()` 内改为 `bp = load_backtest_params(); bp.signal_params = load_signal_params(); bp.risk_params = load_risk_params(); ...`。补回归测试证两入口数值一致。

**C4. 默认参数漂移**（已核实）
- `src/at0/backtest.py:359/363/366`：`cooldown_bars=12`、`max_holding_bars=12`、`stop_loss_ratio=0.008`，与 yaml 定案 `24 / 24 / 0.002` 不符；`risk.py` 的 `min_capture_spread=0.0072` 也与 yaml `0.006` 不符。yaml 缺键时静默取这些差异巨大的默认值。
- 修复：把已验证值直接写进 dataclass 默认（单一事实源），或在 loader 对缺失 yaml 抛明确告警/异常。

### 3.2 High

**H1. 强制平仓漏计平仓侧成本 → 净盈亏高估**（回测/成本）
- `backtest.py:875-887`（止损平仓）、`716-728`（MR 移动止盈平仓）手工追加成交记录并硬编码 `"cost": 0.0`，未把反向那一笔的佣金+印花税+滑点计入 `state.total_cost_paid`；注释"止损成本已在开仓时计入"是错的（开仓只付开仓侧）。`total_pnl = cost_reduction - total_cost_paid` 因此被高估。
- 修复：这两处分支改用统一 `_close_leg()` 辅助函数，复用 `_execute_trade` 的 `cost_model.calc_cost(close_dir, shares, close_price)` 记账。

**H2. 收盘价撮合 + 当根极值信号 → 入场乐观偏差**（回测/前瞻）
- `backtest.py:649` 成交价取 `bar["close"]`，而 `990-1016` 信号评估用含当根 high/low 的 `bars_up_to_now`（如 J2 回踩判定"当根最低曾触 VWAP 带"）。盘中触达、收盘拉回时，回测以更高/更低收盘价成交，优于真实可成交价。
- 修复：回踩/突破类信号入场价取触发价位；或显式标注"收盘撮合"并做悲观滑点/冲击校正，在报告中披露对 J2 的影响。

**H3. RiskEngine 永远拿不到止损/时间 → 风险维名存实亡**（决策路径）
- `risk_engine.py:69,82` `stop_loss_ratio = snap.get("_stop_loss_ratio", 0.002)`——全局 grep 确认 `v3_alpha_stop_loss_ratio` 从未注入 snap；`minute_of_day = snap.get("minute_of_day")`——`features.compute_reference_snapshot` 快照字典不含该键，`time_score` 恒为 70。
- 结果：风险分仅随 ATR 波动率变，止损与时间两因子形同虚设。
- 修复：调用 `compute_alpha_score_v3` 前将 `params.effective_stop_loss_ratio` 与 `minute_of_day`（从 bars 的 time 推导）注入 snap。

**H4. 测量头牌指标 mean-of-means，与 Gate 口径不一致**（测量层）
- `v2_metrics.py:981-997` 的 `overall.avg_ce` 是"每只股票 summary 均值"再平均（mean of means，约定明确禁止）；而 Stage Gate 用的 `distributions["ce"]["median"]`（全池）。两者可能严重背离。HTML 头牌直接消费被污染的 `avg_ce`。
- 修复：`overall` 只保留全池口径（以 `median_ce` 为主）；reporter 头牌改读 `median_ce` 并标注"全池 median"。

**H5. `spearman_ic` 空集返回 0.0 而非 None → 伪造"无相关"**（测量层，已污染产物）
- `v2_metrics.py:639-640`：`if len(xs) < 3: return 0.0`。产物 `v2_report_v3_b5_optimized.json` 四个 `rank_ic_*` 全为 0.0，实为样本不足却被标红"bad"。与 `rank_ic.py:33` 的正确 `None` 语义不一致。
- 修复：改返回 `None`；`compute_attribution` 对 `None` 不进 `available`；reporter `_ic_row` 显示"— / INCONCLUSIVE"。

**H6. `_direction` 缺失静默按 "reduce" 评分**（决策路径）
- `strategy_alpha.py:186` `direction = snap.get("_direction", "reduce")`。缺失不抛错，买入腿会被错误按卖出语义打分且无栈痕迹。
- 修复：入口 `assert "_direction" in snap`，缺失即报错。

**H7. `l7_adx_weak_threshold` 配置死参数**（决策路径）
- `hold_confidence_engine.py:204`（默认 15.0）vs `strategy.py:192`（`l7_adx_weak_threshold=20.0`）；`decision_engine.decide` 调 `check_trend_failure` **未传该参数**，yaml 调此值完全无效。
- 修复：显式传入 `adx_weak_threshold=sp.l7_adx_weak_threshold`（trend-adaptive trailing 路径同步）。

**H8. OpportunityEngine / AlphaScoreAggregator 死代码未接线**（决策路径）
- `opportunity_engine.py` 读永不被写入的 `_sub_scores`，且 `compute_alpha_score_v3` 聚合循环根本没调它；`AlphaScoreAggregator` 是并行的"第二套聚合"。概念重叠。
- 修复：短期标注/删除 dead code，避免误用；若启用需明确融合位置并注入 `_sub_scores`。

### 3.3 Medium

- **M1. 回测 O(n²) 每根重算全量特征**：`backtest.py:771/801` 每根 `compute_reference_snapshot(bars[:i+1])` + `dmi(...)`，多日批量放大。VWAP 用增量累加、ATR/KDJ/EMA 用状态递推、或每根算一次后缓存快照。
- **M2. 东方财富适配器历史深度 cap 5000 根（≈21 交易日）**：`data.py:785-787` 老日期静默返回空 → 无数据源静默跳过。改用按日期分页或窗口 slice 拼接，目标日期早于可得范围应显式报错。
- **M3. HTML 交易明细把 None 渲染成 0.0/+0.00%**：`v2_reporter.py:90-96`（`_num` 默认 0.0 用于 `ce/eg/rm`），违背哨兵约定（`ed/xd/wn` 已用"—"）。`_num` 对 `None` 返回"—"。
- **M4. `describe`/`_percentile` 空数组返回 0.0 而非 None**：`v2_metrics.py:578-614`。直接调用者会把"无数据"当"中位数=0"。改返回 `None`。
- **M5. `identical_to_base` 检测过窄**：`compare_v2_ab.py:132-137` 只看 net_pnl + n + CE median 三个汇总值（且精度阈值 1e-6/1e-9 不一致）。建议对 `per_trade` 全量指标排序后哈希比对。
- **M6. measurement `__init__.py` 硬编码 `cpython-310` + 静默吞异常**：`:75` 拼 `.cpython-310.pyc`，`:89-92` `except Exception: return None`。升级 Python 后 magic number 不匹配 → 子模块全为 `None`，调用方才 `AttributeError`，极难排查。改按 `sys.implementation.cache_tag` 动态拼；加载失败显式 `warnings.warn` 并暴露可用清单。
- **M7. `loaders.py` 跨日配对按日内 time 字符串匹配**：`loaders.py:156-180,239-253` 缺日期 → 不同日期同 `09:35:00` 同价会错配；`bar["time"]` 作全局索引键会覆盖。统一用 `f"{date} {time}"`，`fill_price` 加容差。
- **M8. 净利润护栏 near-zero 基线失真**：`compare_v2_ab.py:102-106,159-167` 相对护栏在基线≈0/为负时语义失效 → 改用绝对阈值或标注。
- **M9. `compute_trade_quality_from_pairs` 用 `or 0.0` 把 None 污染为 0**：`v2_metrics.py:710-713,764`。None 应跳过该笔而非补 0。
- **M10. 两条 RankIC 实现口径不一致**：`v2_metrics.spearman_ic`（空→0.0）vs `rank_ic.compute_rank_ic`（空→None）。统一到后者。
- **M11. config loader `hasattr` 过滤 → yaml 拼错字段名静默丢弃**：`config.py` 多处。对 yaml 有但 dataclass 无的 key 打印 warning。
- **M12. 振幅筛选窗口落在回测期内 → 样本选择偏差**：`backtest_zz500.py` 的 `filter_codes_by_amplitude`、`gen_amplitude_pool.py`、`select_50_from_zz500.py` 都用回测区间内前 60 日筛选标的（用回测期表现挑回测标的）。`walk_forward.py` 正确地用测试期前 60 日。建议 `filter_codes_by_amplitude` 增加 `screen_window_end` 默认取回测起点前 N 日。
- **M13. A/B 对比无统计显著性检验**：`compare_l4_ab.py`（纯描述性）、`compare_v2_ab.py`（无 p-value）。memory 记载 F1 样本独立性存疑，纯描述性对比检测不到。建议加配对 t 检验/Wilcoxon，并校验两批股票集合一致。
- **M14. `min_net_expected_return` 未接入开仓门槛**：`risk.py:114` `CostModel.expected_net_return` 写了无人调用；`_execute_trade:1249` 只比毛 `expected_spread`。开仓门槛应扣成本后 ≥ `min_net_expected_return`。
- **M15. 回测走 `approve_signal`、实盘走 `check_risk`，两套阈值易漂移**：`backtest.py:1223` vs `risk.py:647` 且 `_execute_trade` 内又手写一份 `min_capture_spread`。统一收敛到 `check_risk`。
- **M16. migrate 脚本未排序 + 硬编码路径**：`migrate_minute_local_to_merged.py:24,39-76` 合并时未对 bars 按 `time` 排序（download 脚本有排序却丢了），且覆盖写无幂等检查。
- **M17. qlib adapter index 朝向疑点**：`qlib/adapter.py:209` `swaplevel(0,1)` 把 MultiIndex 变 (instrument, datetime)，与多数 qlib 加载器期望 (datetime, instrument) 相反，可能无声错位。需对所用 qlib 版本断言并冒烟测试实际取数。
- **M18. qlib 集成未端到端验证**：`scripts/v3/qlib_backtest_integration.py:138-146` 仅验证"可调用"，未把预测喂给 `backtest_multi_day` 对比净值差异。

### 3.4 Low

- **L1. 缺失数据返回魔法中性值 50.0 而非 None**（决策路径多 Engine）：`wave/support/expected_move/regime/risk/hold_confidence/predictive_exit` 在数据不足时返 50，按权重参与融合，把"缺失"伪装成"中等置信"。建议缺失关键输入返 `None`，聚合层按权重剔除（参考 `hold_confidence` 的 `total_weight` 做法）。
- **L2. SupportEngine 每根 bar 全量重算 EMA30 + 重复 `_extract_today_bars`**：`support_engine.py:156,292` 与 `wave_engine.py:100` 同副本。EMA 可缓存，公共函数抽到 base/features。
- **L3. ExpectedMoveEngine 类级可变单例 `_qlib_predictions`**：`expected_move_engine.py:38,48` 跨股票/跨 run 泄漏。改实例属性或 per-run 上下文。
- **L4. decision_engine 每 bar 新建实例**：`decision_engine.py:149,177` 与 `_get_engines()` 单例不一致，风格割裂。
- **L5. `strategy_v3/__init__.py` 空门面**：注释称"V3 策略层门面"，实为注释空模块。删或补。
- **L6. 布林带用总体标准差 /period**：`features.py:150`，与 TA-Lib 一致可接受，但需与下游确认。
- **L7. 模块导入期全局副作用**：`config.py:185` `load_market_thresholds()` 改类变量；多线程/多配置并发会互污染（非线程安全）。
- **L8. `logging_utils.py:195` 用 `net_pnl_estimate`（预估）当真实 pnl 算胜率**：仅影响日志复盘口径。
- **L9. `eod_check_bar_idx=200` 对 5min 是死配置**：`backtest.py:356` 单日回测从未引用；建议删除或改比例阈值。
- **L10. 下载/候选池脚本默认结束日 2026-07-22（未来日期）**：`download_zz500_5min.py`、`gen_candidate_pool.py` 首次运行大概率无数据。
- **L11. `describe` 未排除 bool / std 用总体方差**：`v2_metrics.py:597,603` 影响极小。
- **L12. `selfcheck_measurement.py` 覆盖缺口**：未测 `compute_v2_metrics_batch`；且 `spearman_ic` 空输入用 `approx(...,0.0)` 把错误当正确（见 H5）；哨兵测试建议补"None 不应补 0"反向断言。

---

## 4. 待优化点（性能 / 可维护性）

1. **特征计算 O(n²) → 增量/递推缓存**（M1）：批量多日回测的主性能瓶颈。
2. **消除重复实现**：两套 RankIC（M10）、`_extract_today_bars` 副本（L2）、`decision_engine` 与 `alpha_score` 并行聚合（H8）。
3. **统一配置单一事实源**：dataclass 默认 = yaml 定案值（C4）；overlay 与 loader 段名/字段名对齐（C1）；未知键告警（M11）。
4. **统一回测/实盘风控入口**（M15）：`approve_signal` 与 `check_risk` 收敛，避免"回测好看实盘亏"。
5. **统一路径来源**：所有脚本改用 `at0.paths`（`AT0_DATA_DIR`），删除硬编码绝对路径（含旧项目 a-t0 路径）。
6. **HTML 报告内联/转义**：去 CDN 依赖 + 转义注入值（安全节）。
7. **measurement 加载健壮性**：动态 cache_tag + 失败告警（M6）。

---

## 5. 建议清理的遗留 / 调试脚本

脚本目录下大量 `_` 前缀临时诊断与单股硬编码实验脚本，建议移入 `archive/scripts/` 或加 `_dev` 前缀集中管理：

- 临时诊断：`_tmp_diag_mr1.py`、`_tmp_wave_debug.py`、`_diag_mr1_entry_exit.py`、`_diag_mr_optimize.py`、`_diag_K0_by_direction.py`、`_check_dynamic_stop.py`、`_debug_g2_single.py`
- MR / 单股实验（硬编码 689009 / K 股 / 单一 code）：`backtest_689009_mr_optimize.py`、`backtest_689009_tf_mr_compare.py`、`backtest_K_mr_no_amp.py`、`backtest_K_mr_fix_100.py`、`diag_K0_gate_atr.py`、`diag_K0_daily.py`、`diag_J2_laddered_sim.py`
- 阶段实验：`backtest_phase1_passive_j2.py`、`backtest_phase2_range_position.py`、`backtest_phase3_4_side_tier.py`、`backtest_G2_dynamic_stop_ab.py`
- V3 诊断（硬编码 `code="000032"` + 未来 `end`）：`v3/diag_g1_single_b.py`、`v3/perf_profile.py`（可作 dev 工具但需去掉未来日期硬编码）
- 可保留为轻量回归测试的：`test_mr_vs_tf_10stocks.py`（命名应规范）

---

## 6. 优先级行动清单（建议顺序）

1. **立即（Critical）**：C1（overlay 对齐 + 未知键告警）、C2（Engine `_bars` 缺失守卫）、C3（CLI 接入 config）、C4（默认参数与 yaml 对齐）。
2. **高（High）**：H1（平仓补成本）、H3（透传 `_stop_loss_ratio`/`minute_of_day`）、H4/H5（测量口径与 IC None）、H6（`_direction` 断言）、H7（传入 `l7_adx_weak_threshold`）、H8（死代码标注）。
3. **中（Medium）**：M1（特征增量）、M11/M12/M13（配置告警、筛选窗口前移、A/B 加显著性）、M6（measurement 加载健壮性）、M7（跨日配对键）、M14/M15（风控门槛与双入口统一）、M17/M18（qlib 验证）。
4. **清理**：按第 5 节归档临时脚本。

> 说明：以上结论基于逐文件精读与调用链交叉验证。其中 C1/C2/C3/C4 我已直接打开源文件核对行号与代码；其余 High/Medium 来自对实际源码的审查，行号均指向可读的 `.py` 文件。所有建议均为"应改什么、为什么、怎么改"，未动任何代码。
