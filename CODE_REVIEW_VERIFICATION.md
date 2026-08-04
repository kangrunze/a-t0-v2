# a-t0-v2 修复核验 + 继续审查报告（2026-08-04）

> 上一轮审查（`CODE_REVIEW_REPORT.md`）列出 Critical×4、High×8、Medium×18、Low×12。
> 本轮：**逐文件核实用户声称的修复是否真实落地**，并**继续深挖此前未覆盖的区域 + 检查修复是否引入回归**。全程只读，未改动代码。
> 核验方法：直接 `Read` 源码定位行号；对"已修复"项逐一打开实际文件确认，不默认相信。

---

## 0. 一句话结论

**上一轮 Critical/High 修复全部真实落地（已逐行确认），代码质量明显提升。但深挖发现一个被修复"顺带带出"的高危回归：`_to_minutes` 解析 bug 使 H3（时间维风控）在回测路径被静默禁用、并使 5min 回测的频率/regime 推断恒为 1min。** 这是本轮最该先修的 NEW-1。

---

## 1. "已修复"项逐条核验（我直接读源码确认）

| 项 | 结论 | 证据（当前代码） |
|---|---|---|
| **C1** overlay 段名/字段名 | ✅ 真修 | `config/overlays/paper.yaml:10` `cost:`、`research.yaml:10` `cost:`；两文件 `risk.max_t_size_ratio` 已修正 |
| **C2** Engine 空转守卫 | ✅ 真修且干净 | `alpha_score.py:450-457`：`v3_engines_in_backtest=True` 且 `bars is None` → 显式 `raise ValueError`（非 assert，避免 `-O` 剥离）|
| **C3** CLI 读 yaml | ✅ 真修 | `cli.py:237-248`：`load_backtest_params()`/`load_signal_params()`/`load_risk_params()` |
| **C4** 默认参数对齐 yaml | ✅ 真修 | `backtest.py:361/365/368`：`cooldown_bars=24`、`max_holding_bars=24`、`stop_loss_ratio=0.002` |
| **H1** 平仓补成本 | ✅ 真修（两分支） | `backtest.py:879-892`（止损）、`718-731`（MR-TP）均 `calc_cost` 计入 `total_cost_paid` |
| **H3** 注入 `_stop_loss_ratio`/`minute_of_day` | ⚠️ 注入存在但**值恒为 0**（见 NEW-1） | `backtest.py:1056-1060` 注入了，但 `_mod = _to_minutes(_time_str)` 恒返回 0 |
| **H4** 测量头牌口径 | ✅ 真修 | `v2_metrics.py:1005` 暴露 `median_ce`；`v2_reporter.py:112` 优先读 `median_ce` 回退 `avg_ce` |
| **H5** `spearman_ic` 空集返 None | ✅ 真修 | `v2_metrics.py:639-640` |
| **H6** `_direction` 缺失即报错 | ✅ 真修 | `strategy_alpha.py:188-190` 缺失 `raise KeyError` |
| **H7** `l7_adx_weak_threshold` 传入 | ✅ 真修 | `backtest.py:783`、`decision_engine.py:155/167` 显式用 `sp.l7_adx_weak_threshold` |
| **M11** loader 未知键告警 | ✅ 真修 | `config.py` 六处 `load_*` 均调 `_warn_unknown_keys`（`:93/107/122/137/156/176`）|

> 以上 Critical/High 修复干净、无遗漏分支。C2 的 `raise` 在正常路径不会误抛（回测 `backtest.py:1057/1060`、实盘 `strategy.py:1174-1175` 都已注入 `_bars`）。

---

## 2. 新发现的回归 / Bug（本轮深挖）

### 【高】NEW-1 · `_to_minutes` 解析 bug → H3 时间维被静默禁用 + 5min 频率推断恒 1min
- **位置**：`src/at0/backtest.py:111-119`（`_to_minutes`）、`:132`（`_infer_frequency`）、`:1054`（H3 注入处）
- **根因**：数据源 `bar["time"]` 全部是 **`"YYYY-MM-DD HH:MM:SS"`**（已确认：`data.py:567` mootdx、`:724` baostock、`:820` eastmoney 均产出带日期格式）。而 `_to_minutes` 只做 `str(s).split(":")[0]`：
  ```python
  def _to_minutes(s: str) -> int:
      parts = str(s).split(":")
      if len(parts) >= 2:
          try:
              return int(parts[0]) * 60 + int(parts[1])   # parts[0]="2026-07-23 09" → int() 抛 ValueError
          except ValueError:
              return 0                                    # ← 恒返回 0
  ```
- **连锁后果（均为静默，无报错）**：
  1. H3 注入的 `minute_of_day`（`backtest.py:1058/1060`）恒为 `0`（int）。
  2. `risk_engine.py:85-89`：`if minute_of_day >= 840 / >= 870` 两个**尾盘时间维风控分支永不触发**，`time_score` 恒为 70 → **H3 修复意图在回测路径被实际禁用**。
  3. `_infer_frequency`（`:132`）：`diff = _to_minutes(b1) - _to_minutes(b0) = 0` → 恒返回 `"1min"`。5min 数据被当成 1min → `_judge_trend_context` 用 `min_bars_for_trend=60`（应为 12）→ **趋势过滤退化（5min 数据几乎永远判定为 range）**。
- **修复**：`_to_minutes` 先 `s.split(" ")[-1]` 取时间部分再切分，或改用 `datetime.strptime`；`_infer_frequency` 应优先信任 `meta["frequency"]`（data.py 已正确给出 `"5min"`，cli.py:226/249 也持有）。

### 【中】NEW-2 · 实盘路径未注入 `_stop_loss_ratio`/`minute_of_day`
- **位置**：`src/at0/strategy.py:1174-1175`（实盘 `evaluate_all_signals` 的 alpha 分支）
- **问题**：仅注入 `_direction`/`_bars`，未注入 H3 的两个键 → 实盘下 `risk_engine` 取 `minute_of_day=None` → 时间维分支同样跳过。叠加 NEW-1，**回测（值恒0）与实盘（键缺失）两条路时间维风控都不生效**，与 H3 修复意图完全背离。
- **修复**：`strategy.py:1174-1175` 一并注入（需先修 NEW-1 使值正确）；补单测断言快照含两键。

### 【中】NEW-3 · frequency 未贯通到实时路径
- **位置**：`backtest.py:620/1004/1018`、`strategy.py:1117-1121`
- **问题**：实时 `evaluate_all_signals` 调用 `_judge_trend_context` 不传 `frequency`，落到默认 `"1min"`；而 cli.py 已正确持有 `meta["frequency"]="5min"`。两处频率来源不一致。
- **修复**：让 `run_backtest`/`evaluate_all_signals` 接收并透传 `meta["frequency"]`，移除对破损 `_infer_frequency` 的依赖（或修复后仅作校验）。

### 【确认无回归】H1 平仓补成本后配对自洽
- `execution.py` 的 `TradeLifecycle.add_fill` FIFO 配对 `pair_pnl` 与 `earliest.paired_pnl` 同步累加，无 double-count；文件锁 `_file_lock` 包住读写。H1 修复后 trade 记录与 pnl 聚合自洽。

---

## 3. 前次 Medium 项当前真实状态（多数尚未处理，低优先级）

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| M1 | 回测 O(n²) 每根重算特征 | **not-fixed** | `backtest.py:775/806` 仍每根全量重算；仅独立预取缓存（:1617），非特征增量 |
| M2 | 东财 cap 5000 根 | **not-fixed** | `data.py:787` 仍 `min(n_days*240, 5000)`，老日期静默空返回 |
| M6 | measurement 加载硬编码 cpython-310 + 无告警 | **not-fixed** | `__init__.py:75/89-92` 仍硬编码 tag、失败静默 `return None`；无 `warnings.warn`、无 `cache_tag` 动态化 |
| M7 | 跨日配对用日内 time | **not-fixed 但当前良性** | `loaders.py:156-180/239-253` 无日期维度；但当前数据源 time 含日期（`YYYY-MM-DD HH:MM:SS`），故 `time_to_global_idx` 不冲突、`<=` 比较正确 → **当前不触发**，属脆弱依赖（纯 `"HH:MM:SS"` 源会静默错配）|
| M12 | 振幅筛选窗口落在回测期内（前视偏差） | **not-fixed** | `backtest_zz500.py:609`、`gen_amplitude_pool.py:56`、`select_50_from_zz500.py:40` 仍用回测区内前 60 日 |
| M13 | A/B 无统计显著性检验 | **not-fixed** | `compare_l4_ab.py` 纯描述性；`compare_v2_ab.py` 无 p-value |
| M14 | `min_net_expected_return` 未接入开仓门槛 | **not-fixed** | `risk.py:115-130` `expected_net_return` 死代码；`_execute_trade:1204` 仅比毛 `expected_spread` |
| M15 | 回测 `approve_signal` 与实盘 `check_risk` 双入口 | **not-fixed** | `risk.py:256/647` 两套并行，靠手动复制对齐，有漂移风险 |
| M17 | qlib index 朝向 | **not-fixed 但实无缺陷** | `adapter.py:209` swaplevel 后 `(datetime, instrument)` 与输入 `(instrument, datetime)` 自洽；朝向担忧未兑现 |
| M18 | qlib 端到端未打通 | **not-fixed** | `v3/qlib_backtest_integration.py:137/140/158` 仅验证"可调用"/打印对齐，预测未喂回 `backtest_multi_day` |

---

## 4. 优先级行动清单（本轮更新）

1. **立即（高）**：修 **NEW-1**（`_to_minutes` 剥离日期；`_infer_frequency` 优先 `meta["frequency"]`）。这是本轮头号问题，否则 H3 形同未修、5min 回测 regime 退化。
2. **高**：修 **NEW-2**（实盘路径同步注入 `_stop_loss_ratio`/`minute_of_day`）+ 补单测。
3. **中**：修 **NEW-3**（frequency 贯通实时路径）。
4. **中**：M6（动态 `cache_tag` + 加载失败 `warnings.warn` + 暴露可用清单）。
5. **中**：M14/M15（统一风控入口、接入净收益门槛）。
6. **低/排期**：M1（特征增量缓存）、M2（东财分页）、M7（加日期键防御）、M12（筛选窗口前移）、M13（A/B 加显著性）、M18（qlib 端到端）。
7. **清理**：上一轮第 5 节的 `_` 前缀临时诊断脚本仍建议归档。

> 说明：本轮所有 Critical/High 修复经逐行核对确认真实有效，代码整体已更健康。NEW-1/2/3 是"修复过程中暴露/顺带带出"的隐藏缺陷，建议作为下一轮必改项；其余 Medium 属已记录待优化，不阻塞当前结论。
