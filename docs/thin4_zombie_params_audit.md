# 步骤4：僵尸参数/死代码排查清单

搜索关键词：`保留...兼容` / `不再使用` / `已废弃` / `deprecated` / `legacy` / `向后兼容`

## 清单（13处匹配，逐个核实）

### 1. strategy.py — open_vwap_dev 参数（2处）
**位置**：[strategy.py:354](file:///d:/project/a-t0-v2/src/at0/strategy.py#L354), [strategy.py:517](file:///d:/project/a-t0-v2/src/at0/strategy.py#L517)
**注释**："趋势跟随不再使用动态平仓阈值（_compute_pairing_threshold），保留参数以兼容函数签名。"
**核实**：
- evaluate_reduce_signal / evaluate_add_signal 函数体里**不使用** open_vwap_dev
- 但 backtest.py:647/659 调用时仍传入该参数
- 删除需同时改 backtest.py 的调用方，属于跨文件改动
**建议**：⚠️ 需要人工确认 — 删除需同步改 backtest.py 2处调用 + execution.py 的 TradeLeg 字段。属于"均值回归转趋势跟随"遗留，建议与 `_compute_pairing_threshold` 一起在单独的"平仓逻辑清理"改动里处理，不在本次瘦身做。

### 2. strategy.py — _compute_pairing_threshold 函数（死代码）
**位置**：[strategy.py:251](file:///d:/project/a-t0-v2/src/at0/strategy.py#L251)
**核实**：函数定义存在，但**全项目无调用**（grep `_compute_pairing_threshold\(` 只匹配定义行）
**建议**：⚠️ 需要人工确认 — 虽是死代码，但与 open_vwap_dev 配套，建议一起清理。

### 3. strategy.py — min_rules_to_trigger 参数
**位置**：[strategy.py:100](file:///d:/project/a-t0-v2/src/at0/strategy.py#L100)
**注释**："总分阈值（向后兼容；实际触发还受 extreme_min/confirm_min 约束）"
**核实**：TSignal.triggered 属性里仍使用 `self.rules_score >= self.trigger_threshold`
**建议**：✅ 暂不处理 — 虽标注"向后兼容"但实际仍在触发逻辑里使用，不是僵尸参数。

### 4. backtest.py — 成本字段保留兼容
**位置**：[backtest.py:305](file:///d:/project/a-t0-v2/src/at0/backtest.py#L305), [backtest.py:399](file:///d:/project/a-t0-v2/src/at0/backtest.py#L399)
**注释**："P0-1: 统一收敛到 CostModel，旧字段保留兼容"
**建议**：✅ 暂不处理 — 涉及成本模型重构，不在本次瘦身范围。

### 5. data.py — MINUTE_DATA_DIR 别名
**位置**：[data.py:160](file:///d:/project/a-t0-v2/src/at0/data.py#L160)
**注释**："MINUTE_DATA_DIR = MINUTE_BARS_DIR  # 向后兼容别名"
**核实**：需确认是否有外部调用
**建议**：⚠️ 需要人工确认 — 需 grep 外部调用

### 6. execution.py — bar_low/bar_high 向后兼容
**位置**：[execution.py:233](file:///d:/project/a-t0-v2/src/at0/execution.py#L233)
**注释**："bar_low / bar_high 缺失时退化为收盘价口径（向后兼容）"
**建议**：✅ 暂不处理 — 是运行时容错逻辑，不是僵尸参数。

### 7. execution.py — LOCK_FILE 别名
**位置**：[execution.py:476](file:///d:/project/a-t0-v2/src/at0/execution.py#L476)
**注释**："LOCK_FILE = POSITIONS_LOCK_FILE  # 向后兼容别名"
**建议**：⚠️ 需要人工确认 — 需 grep 外部调用

### 8. risk.py — 默认值对齐注释
**位置**：[risk.py:531](file:///d:/project/a-t0-v2/src/at0/risk.py#L531)
**注释**："P0-1 整改（2026-07-24）：默认值对齐 config/thresholds.yaml，不再使用..."
**建议**：✅ 暂不处理 — 是历史沿革注释，描述已完成的对齐，不是僵尸参数。

### 9. risk.py — bar_idx/bars_count 向后兼容
**位置**：[risk.py:632](file:///d:/project/a-t0-v2/src/at0/risk.py#L632)
**注释**："bar_idx/bars_count/exposure_policy/open_legs 时才执行，保持向后兼容"
**建议**：✅ 暂不处理 — 是运行时容错逻辑。

### 10. test_regime_stepE.py — 已废弃注释
**位置**：[test_regime_stepE.py:113](file:///d:/project/a-t0-v2/scripts/test_regime_stepE.py#L113)
**注释**："v1（已废弃）：要求 close > MA20，下降趋势被误判 NO_TRADE"
**建议**：✅ 可安全删除 — 但这是测试代码里的历史说明，保留无害。

## 汇总

| 类别 | 数量 | 建议 |
|------|------|------|
| 可安全删除 | 1 | test_regime_stepE.py 的注释（可选） |
| 需要人工确认 | 4 | open_vwap_dev + _compute_pairing_threshold（配套死代码）+ 2个别名 |
| 暂不处理 | 8 | 运行时容错、历史沿革注释、仍在使用的参数 |

## 结论

本次瘦身**不删除任何僵尸参数**。核心死代码（open_vwap_dev + _compute_pairing_threshold）与"均值回归转趋势跟随"的平仓逻辑清理强相关，属于行为改动，应单独立项。其余匹配多为运行时容错或历史注释，非僵尸参数。
