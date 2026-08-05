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
- **本仓库禁用 `git rm` 删除 `__pycache__/` 下文件**：git 2.47.1.windows.1 + core.ignorecase=true 下，
  `git rm` 删 measurement/__pycache__/*.pyc 会连带删除整个 `src/at0/measurement/` worktree（单文件精确路径可复现，与并发无关）。
  删除改用：Python `os.remove`/`shutil.rmtree`（绝对 `D:\...` 路径）→ `git add -A` 记录。Bash `rm` 也被 safe-delete 包装拦截（`/d/...` 路径转换失败），同样绕开。
- measurement 已于 2026-08-05 移除 loader shim：`__init__.py` 为标准导入，`.py.bak`（TSD 加密占位）与 `__pycache__/*.pyc` 已从 git 移除，`.gitignore` 的"例外保留 .pyc"规则已删。dualrun 仍为真黑盒（仅 .py.bak + .pyc，无源码），其 loader 不能删。

## 分支与 Git 结构（2026-08-05 合并后）
- 主分支是 **`main`**（不是 `master`，仓库无 master）。`dev-v2` 与 `dev-alpha` 内容等价（`dev-alpha` 已 fast-forward 到 `dev-v2`，均指向 `f39ae8d`）。
- **`main` 与 dev 分支是"无关联历史"（no common ancestor）**。远端 `main` 原本是另一条独立线，只含旧 `src/`+`scripts/` 老代码、以及 **752 个市场数据文件（`data/minute_local/`、`data/zz500_5min/`）**——这批数据**只存在于远端 main 的 git，本地磁盘没有**。
- 合并做法：`git merge --allow-unrelated-histories origin/main`，**所有代码/配置冲突一律取 dev-v2（`--ours`）**，数据文件保留。合并提交 `97ea71d`（双 parent：`628f40d` + `ae2da67`），已推送 `ae2da67..97ea71d`。
- 结论：远端 `main` 现在同时含现代代码（web/30、engines/10）与 752 个市场数据。
- 2026-08-05 傍晚：main 已推进到 `b1e4c96`（`73f7545` measurement 源码化+死代码归档 → `b1e4c96` Web Flask 化+README 重写）。**Web 平台已从 Streamlit 全面迁移到 Flask**（5 蓝图 33 路由，`flask --app web.app run --port 8501`），README 已同步，Streamlit 仅存 `web/streamlit_app.py`+`web/pages/` 作回退验证。
- 教训：push 被拒（non-fast-forward）先 `git fetch` 看是否"无关联历史 + 远端有独特数据"，**勿直接 `--force-with-lease`**，否则会丢了只存于远端的 752 个数据文件。
- `data/` 目录在 dev 分支 .gitignore 里被排除，但 main 里仍按上述合并保留了数据；若后续只想要干净代码，需另做数据备份再处理。
</content>
