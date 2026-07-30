# A-T0 V3 策略平台文档

> **版本**: V3.0
> **更新日期**: 2026-07-30
> **定位**: A股 T+0 底仓做T策略平台（Strategy Platform）

---

## 目录

1. [项目概述](#1-项目概述)
2. [V3 架构设计](#2-v3-架构设计)
3. [目录结构](#3-目录结构)
4. [核心数据流](#4-核心数据流)
5. [Engine 层详解](#5-engine-层详解)
6. [Alpha 评分系统](#6-alpha-评分系统)
7. [Qlib 研究层集成](#7-qlib-研究层集成)
8. [回测引擎](#8-回测引擎)
9. [配置系统](#9-配置系统)
10. [V3 专属风控](#10-v3-专属风控)
11. [性能优化](#11-性能优化)
12. [A/B 验证结果](#12-ab-验证结果)
13. [使用指南](#13-使用指南)
14. [Stage-Gate 研发流程](#14-stage-gate-研发流程)

---

## 1. 项目概述

### 1.1 项目定位

A-T0 是一个 **A股 T+0 底仓做T回测系统**，V3 版本从"规则驱动"重构为"预测 + 排名 + 执行"的 **策略平台（Strategy Platform）**。

### 1.2 V3 优化目标

V3 不再以"胜率最高"为目标，而是：

> **保持胜率（65%~70%）不下降的情况下，提高 Capture Efficiency 和 Profit Factor**

优化目标公式：

```
Objective =
  0.35 × NetProfit
+ 0.25 × ProfitFactor
+ 0.20 × CaptureEfficiency
+ 0.10 × Sharpe
+ 0.05 × MaxDrawdown
+ 0.05 × TurnoverPenalty
```

核心指标：**CE（Capture Efficiency）最高**。

### 1.3 设计哲学

- **平台化**：各 Engine 可插拔，未来可引入新的 Alpha 因子、ML 模型、订单流特征
- **Stage-Gate**：分阶段门控研发，每完成一个 Stage 做全市场 A/B 回测
- **单变量改动**：每个 Stage 只改一个自由度，避免无法归因
- **降级链**：Qlib 不可用→自研 features.py，LightGBM 不可用→常数模型

---

## 2. V3 架构设计

### 2.1 整体架构

```
Market Data (zz500_5min JSON)
      │
      ▼
Feature Engine (features.py + Qlib Alpha158)
      │
      ▼
Regime Engine (G7) ─── 市场状态识别
      │
      ▼
Trend Engine (G1占位) ─── 趋势质量评分
      │
      ▼
Wave Engine (G4) ─── 波浪位置评分
      │
      ▼
Support Engine (G2) ─── 动态支撑/阻力评分
      │
      ▼
Expected Move Engine (G5) ─── 未来收益预测 (Qlib LightGBM)
      │
      ▼
Opportunity Engine (G3) ─── 综合机会评分（非线性组合）
      │
      ▼
Alpha Score Aggregator ─── 七维度加权 → alpha_score (0~100)
      │
      ▼
Execution Engine (G6) ─── 执行层（回踩/冲高确认）
      │
      ▼
Risk Engine ─── 风控 + Kelly sizing
      │
      ▼
Backtest Engine ─── 分钟级回测
```

### 2.2 核心约束

| 约束 | 说明 |
|---|---|
| `strategy.py` 三个 `evaluate_*` 入口签名不变 | 门面接口保持兼容 |
| `use_continuous_alpha=False` 时行为与改造前一致 | 向后兼容 |
| 权重从 `thresholds.yaml` 读取 | 版本管理 + A/B 测试 |
| Qlib 可选依赖 | 未安装时自动降级 |
| MR 保留为独立 mode | 与 trend_following 互斥 |

### 2.3 策略模式

V3 支持三种策略模式（通过 `strategy_mode` 切换）：

| 模式 | `strategy_mode` | `use_continuous_alpha` | 说明 |
|---|---|---|---|
| TF baseline | `"trend_following"` | `false` | 趋势跟随规则驱动（A组） |
| V3 Alpha | `"trend_following"` | `true` | V3 七维度 Engine 评分（B组） |
| MR | `"mean_reversion"` | `false` | 均值回归独立模式 |

---

## 3. 目录结构

```
d:\project\a-t0-v2\
├── src/at0/                          # 核心包
│   ├── __init__.py                   # 公共 API
│   ├── config.py                     # thresholds.yaml 加载
│   ├── paths.py                      # 路径常量
│   ├── data.py                       # 数据加载/代码归一化
│   ├── features.py                   # 特征计算（保留，V3 部分被 Engine 替代）
│   ├── strategy.py                   # 信号决策层（门面，签名不变）
│   ├── strategy_alpha.py             # Alpha 评分委托层
│   ├── risk.py                       # 风控（CostModel/ExposurePolicy）
│   ├── execution.py                  # 交易生命周期
│   ├── backtest.py                   # 回测引擎主循环
│   ├── cli.py                        # CLI 入口
│   ├── reports.py                    # 报告生成
│   ├── screener.py                   # 股票筛选
│   ├── regime.py                     # Regime 辅助
│   ├── logging_utils.py              # 日志
│   │
│   ├── models/                       # V3 数据模型
│   │   ├── feature_vector.py         # FeatureVector（七维度特征容器，frozen）
│   │   └── trade_candidate.py        # TradeCandidate（七维度子评分 + alpha_score）
│   │
│   ├── score/                        # V3 Alpha 评分
│   │   └── alpha_score.py            # AlphaScoreAggregator + compute_alpha_score_v3
│   │
│   ├── engines/                      # V3 Engine 层（G2-G7）
│   │   ├── base.py                   # BaseEngine 抽象基类
│   │   ├── support_engine.py         # G2: 动态支撑/阻力
│   │   ├── opportunity_engine.py     # G3: 综合机会评分
│   │   ├── wave_engine.py            # G4: 波浪位置
│   │   ├── expected_move_engine.py   # G5: 未来收益预测
│   │   ├── execution_engine.py       # G6: 执行层
│   │   ├── regime_engine.py          # G7: 市场状态识别
│   │   └── risk_engine.py            # Risk + Kelly sizing
│   │
│   ├── qlib/                         # V3 Qlib 适配层
│   │   ├── __init__.py               # 降级链检测
│   │   ├── loader.py                 # JSON → MultiIndex DataFrame
│   │   ├── adapter.py                # Alpha158 因子 + DatasetH
│   │   ├── dataset.py                # 因子 IC 分析 + 权重建议
│   │   └── model.py                  # LightGBM 训练 + 降级常量模型
│   │
│   ├── strategy_v3/                  # V3 门面占位
│   │   └── __init__.py
│   │
│   └── measurement/                  # 测量层（.pyc 加载）
│       ├── __init__.py               # SourcelessFileLoader 加载器
│       ├── loaders.py                # 数据加载
│       ├── rank_ic.py                # Rank IC
│       └── rules_parser.py           # 规则解析
│
├── scripts/                          # 脚本
│   ├── backtest_zz500.py             # 主回测入口（支持 --params-json）
│   ├── v3/                           # V3 专用脚本
│   │   ├── sample_100_stocks.py      # 100 股随机抽样
│   │   ├── qlib_smoke_test.py        # Qlib 适配器 smoke test
│   │   ├── qlib_backtest_integration.py  # Qlib↔AT0 集成验证
│   │   ├── diag_g1_single_b.py       # G1 单股诊断
│   │   └── perf_profile.py           # 性能分析
│   └── ...                           # 其他诊断/优化脚本
│
├── config/                           # 配置
│   ├── thresholds.yaml               # 权威参数文件
│   ├── v3_sample_100.txt             # 100 股抽样清单
│   ├── v3_sample_20.txt              # 20 股抽样清单
│   ├── v3_sample_5.txt               # 5 股抽样清单
│   ├── v3_ab_group_a_baseline.json   # A组参数（TF baseline）
│   └── v3_ab_group_b_alpha_v3.json   # B组参数（V3 Alpha + 专属止损）
│
├── outputs/                          # 输出（.gitignore）
│   ├── backtest/                     # 回测结果
│   └── runs/                         # 版本化 run 记录
│
└── docs/
    └── V3_ARCHITECTURE.md            # 本文档
```

---

## 4. 核心数据流

### 4.1 V3 Alpha 模式数据流

```
1. backtest_zz500.py 加载 zz500_5min JSON
2. features.py 计算特征快照 snap（含 adx/rsi/kdj/vwap/...）
3. strategy.py evaluate_all_signals 遍历每根 bar:
   ├─ use_continuous_alpha=False → 走 TF 三层布尔触发（baseline）
   └─ use_continuous_alpha=True  → 走 V3 Alpha 评分:
       ├─ snap["_bars"] = bars    # 注入完整 K 线序列
       ├─ compute_alpha_score(snap, params) 委托 strategy_alpha.py
       └─ strategy_alpha.py 调用 compute_alpha_score_v3(snap, params):
           ├─ FeatureVector.from_snapshot(snap)
           ├─ _load_v3_weights_from_yaml()  # 带 mtime 缓存
           ├─ Trend:    score_trend(fv) × 0.6 + RegimeEngine.score × 0.4
           ├─ Wave:     WaveEngine.score(bars, snap, direction)
           ├─ Support:  SupportEngine.score(bars, snap, direction)
           ├─ Momentum: score_momentum(fv)  # G1 占位
           ├─ Liquidity: score_liquidity(fv)  # G1 占位
           ├─ ExpectedMove: ExpectedMoveEngine.score(bars, snap, direction)
           ├─ Risk:    RiskEngine.score(bars, snap, direction)
           └─ 加权平均 → alpha_score (0~100)
4. alpha_score >= alpha_threshold_open (75) → 触发信号
5. backtest.py 执行交易（含 V3 专属止损 effective_stop_loss_ratio）
```

### 4.2 Qlib 研究层数据流

```
1. sample_100_stocks.py 抽样 100 股
2. qlib/loader.py 加载 JSON → MultiIndex DataFrame (instrument, datetime)
3. qlib/adapter.py:
   ├─ compute_alpha158_subset(features_df)  # 26 个 Alpha158 因子
   └─ compute_future_return_label(features_df, horizon=6)  # 6 bar 未来收益
4. qlib/adapter.py build_qlib_dataset:
   └─ DatasetH 时间切分 train/valid/test
5. qlib/model.py train_model:
   ├─ LightGBM 可用 → train_lightgbm_model (IC=0.3937)
   └─ 不可用 → _degraded_constant_model
6. 预测结果注入 ExpectedMoveEngine._qlib_predictions
7. ExpectedMoveEngine.score 优先查 Qlib 预测，降级为统计外推
```

---

## 5. Engine 层详解

### 5.1 BaseEngine 抽象基类

所有 Engine 继承 `BaseEngine`，统一接口：

```python
class BaseEngine(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def score(
        self,
        bars: list[dict],      # 完整 K 线序列（含历史）
        snap: dict,            # 当前 bar 特征快照
        direction: str = "reduce",  # "reduce" 或 "add"
    ) -> float:                # 返回 0~100 子评分
        ...
```

### 5.2 G2: SupportEngine（动态支撑/阻力评分）

**文件**: [engines/support_engine.py](file:///d:/project/a-t0-v2/src/at0/engines/support_engine.py)

**职责**: 识别当前价格附近的动态支撑/阻力位，评估价格是否在强支撑/阻力附近。

**支撑/阻力候选**:
- VWAP（日内 VWAP，权重 1.2）
- EMA20 / EMA30 / EMA60（权重 0.8~1.1）
- Yesterday Close（权重 0.9）
- Opening Range Mid/Low/High（权重 0.7~0.8）
- BB lower/upper（权重 0.7）

**评分逻辑**:
```
base = f(距离)  # <0.2%→90, <0.5%→80, <1%→70, <2%→55, >2%→40
base += 方向确认 (+5)
base *= vol_weight
base += bounce_count 奖励 (+3/+5)
```

**输出**: 0~100，90+ 表示价格在强支撑位附近。

### 5.3 G3: OpportunityEngine（综合机会评分）

**文件**: [engines/opportunity_engine.py](file:///d:/project/a-t0-v2/src/at0/engines/opportunity_engine.py)

**职责**: 将各维度子评分非线性组合，输出最终交易机会评分。

**算法**: 几何平均 + 算术平均混合（50/50）
- 几何平均：低评分维度拖累整体（乘法效应）
- 算术平均：避免几何平均过度惩罚

**仓位建议**:
| Opportunity Score | 仓位 |
|---|---|
| ≥90 | heavy（重仓）|
| ≥80 | normal（正常）|
| ≥65 | light（轻仓）|
| <65 | skip（不交易）|

### 5.4 G4: WaveEngine（波浪位置评分）

**文件**: [engines/wave_engine.py](file:///d:/project/a-t0-v2/src/at0/engines/wave_engine.py)

**职责**: 评估当前价格处于波浪的哪个位置，避免在波浪末期追高/追低。

**五因子加权**:
| 因子 | 权重 | 评分逻辑 |
|---|---|---|
| Trend Age | 25% | 趋势持续 1-8 根→高分，>20 根→低分 |
| HH Count | 25% | 连续创新高 1-3 次→高分，>6 次→低分 |
| ATR Expansion | 15% | 扩张>1.2→80，稳定→60，收缩→30 |
| Momentum Decay | 20% | 增强>0.8→80，衰减<0.5→20 |
| Volume Decay | 15% | 放量>1.0→80，缩量<0.7→25 |

### 5.5 G5: ExpectedMoveEngine（未来收益预测）

**文件**: [engines/expected_move_engine.py](file:///d:/project/a-t0-v2/src/at0/engines/expected_move_engine.py)

**职责**: 预测未来 N 根 K 线的收益，计算 Reward/Risk 比率。

**降级链**:
1. Qlib LightGBM 预测（IC=0.3937，优先）
2. 统计外推（最近 6 根 ROC / 20 根波动率）
3. 常数模型（返回中性 50）

**RR 映射**:
| RR | Score |
|---|---|
| ≥3.0 | 95 |
| 2.0 | 75 |
| 1.5 | 50（门槛）|
| 1.0 | 25 |
| <0.5 | 5 |

**Qlib 预测注入**:
```python
ExpectedMoveEngine.set_qlib_predictions({(code, datetime): predicted_return})
```

### 5.6 G6: ExecutionEngine（执行层评分）

**文件**: [engines/execution_engine.py](file:///d:/project/a-t0-v2/src/at0/engines/execution_engine.py)

**职责**: 评估当前 bar 是否是好的执行时机（回踩/冲高确认）。

**评分因子**:
- EMA 距离（回踩到位→90，追高→35）
- KDJ 位置（低位买入/KDJ 高位卖出加分）
- 量能确认（放量+5，缩量-5）
- 日内时间（避开开盘 15 分钟和收盘 5 分钟）

**执行延迟建议**:
```python
engine.compute_delay_bars(snap, direction)  # 0=立即, 1-5=延迟
```

### 5.7 G7: RegimeEngine（市场状态识别）

**文件**: [engines/regime_engine.py](file:///d:/project/a-t0-v2/src/at0/engines/regime_engine.py)

**职责**: 识别市场状态（趋势/震荡/极端），为其他 Engine 提供环境上下文。

**市场状态分类**:
| 状态 | 条件 | Score |
|---|---|---|
| trend_up | ADX≥25, DI+>DI- | 70~100 |
| trend_down | ADX≥25, DI->DI+ | 70~100 |
| range | ADX<20 | 30~50 |
| extreme | ADX≥40 + |VWAP偏离|≥2×ATR | 20（高风险）|

### 5.8 RiskEngine（风险评分 + Kelly sizing）

**文件**: [engines/risk_engine.py](file:///d:/project/a-t0-v2/src/at0/engines/risk_engine.py)

**职责**: 评估交易风险/收益比，输出风险评分 + Kelly 仓位建议。

**评分因子**:
- ATR%（50% 权重）：<0.3%→90, <0.5%→80, <0.8%→65, >2%→20
- 止损/ATR 比率（30% 权重）：1-2 ATR→80，太紧/太松减分
- 日内时间风险（20% 权重）：14:00 后→50，14:30 后→35

**Kelly 仓位计算**:
```python
RiskEngine.compute_kelly_size(
    win_rate=0.67, avg_win=150, avg_loss=100,
    confidence=0.8, max_size=0.25
)
# → 0.18（建议 18% 仓位）
```

---

## 6. Alpha 评分系统

### 6.1 七维度权重（用户方案 §十二）

| 维度 | 权重 | 实现阶段 | 当前实现 |
|---|---|---|---|
| Trend | 25% | G1 + G7 | 60% G1占位 + 40% RegimeEngine |
| Wave | 20% | G4 | WaveEngine（Trend Age / HH Count / ...）|
| Support | 15% | G2 | SupportEngine（动态支撑/阻力）|
| Momentum | 15% | G1 | G1 占位（RSI + KDJ + MFI）|
| Liquidity | 10% | G1 | G1 占位（量比）|
| ExpectedMove | 10% | G5 | ExpectedMoveEngine（Qlib / 统计外推）|
| Risk | 5% | G6 | RiskEngine（ATR + 止损 + 时间）|

### 6.2 权重配置

权重从 `config/thresholds.yaml` 的 `signal.v3_alpha_weights` 读取：

```yaml
v3_alpha_weights:
  trend: 0.25
  wave: 0.20
  support: 0.15
  momentum: 0.15
  liquidity: 0.10
  expected_move: 0.10
  risk: 0.05
```

### 6.3 评分聚合

```python
# score/alpha_score.py
def compute_alpha_score_v3(snap, params, direction) -> (alpha_score, sub_scores):
    fv = FeatureVector.from_snapshot(snap, direction)
    weights = _load_v3_weights_from_yaml()  # 带 mtime 缓存
    engines = _get_engines() if snap.get("_bars") else {}

    # 各维度计算（Engine 或 G1 占位）
    sub_scores = {
        "trend": ...,
        "wave": ...,
        "support": ...,
        "momentum": ...,
        "liquidity": ...,
        "expected_move": ...,
        "risk": ...,
    }

    # 加权平均
    alpha = sum(sub_scores[k] * weights[k] for k in sub_scores) / sum(weights.values())
    return clip(alpha, 0, 100), sub_scores
```

### 6.4 触发阈值

```yaml
alpha_threshold_open: 75.0  # alpha_score ≥ 75 才触发信号
```

---

## 7. Qlib 研究层集成

### 7.1 定位

Qlib 作为 **Research Layer** 嵌入，负责因子工程 + 模型训练 + FutureReturn 预测。AT0 自研回测/Execution/Risk 保留不动。

### 7.2 模块组成

| 模块 | 文件 | 职责 |
|---|---|---|
| 降级链检测 | [qlib/__init__.py](file:///d:/project/a-t0-v2/src/at0/qlib/__init__.py) | `is_qlib_available()` / `is_lightgbm_available()` |
| 数据加载 | [qlib/loader.py](file:///d:/project/a-t0-v2/src/at0/qlib/loader.py) | JSON → MultiIndex DataFrame |
| 因子计算 | [qlib/adapter.py](file:///d:/project/a-t0-v2/src/at0/qlib/adapter.py) | Alpha158 子集（26 因子）+ DatasetH |
| IC 分析 | [qlib/dataset.py](file:///d:/project/a-t0-v2/src/at0/qlib/dataset.py) | 横截面 IC + 因子排名 + 权重建议 |
| 模型训练 | [qlib/model.py](file:///d:/project/a-t0-v2/src/at0/qlib/model.py) | LightGBM + 降级常量模型 |

### 7.3 Alpha158 因子子集（26 个）

| 类别 | 因子 |
|---|---|
| K线形态 | KMID, KLEN, KMID2, KUP, KLOW, KSFT, OPEN0, HIGH0, LOW0 |
| 收益率 | ROC5, ROC10, ROC20 |
| 均线 | MA5, MA10, MA20, MA5_MA20 |
| 波动率 | STD5, STD10, STD20, ATR14 |
| 成交量 | VSTD5, VRATIO, VWAP_DEV |
| 日内位置 | CLOSE2HIGH, CLOSE2LOW, MIN_OF_DAY |

### 7.4 降级链

```
Qlib 已装 + LightGBM 可用 → Alpha158 + LightGBM（IC=0.3937）
        ↓ 不可用
统计外推（最近 ROC / 波动率）
        ↓ 数据不足
常数模型（返回中性 50）
```

### 7.5 验证结果

100 股 × 2024-01-01~2026-07-22:
- 数据量：2,904,572 条 5min bars
- DatasetH 切分：train=1,669,040 / valid=596,784 / test=631,948
- LightGBM IC: **0.3937**, RMSE: 0.0113
- Top5 重要因子：MIN_OF_DAY / CLOSE2HIGH / CLOSE2LOW / VWAP_DEV / ATR14

---

## 8. 回测引擎

### 8.1 回测入口

```bash
python scripts/backtest_zz500.py \
    --codes @config/v3_sample_100.txt \
    --start 2024-01-01 --end 2026-07-22 \
    --no-amplitude-filter \
    --params-json config/v3_ab_group_b_alpha_v3.json \
    --tag v3_full_100
```

### 8.2 参数覆盖机制

`--params-json` 支持 `sp`（SignalParams）和 `bp`（BacktestParams）两层覆盖：

```json
{
  "sp": {
    "use_continuous_alpha": true,
    "strategy_mode": "trend_following",
    "alpha_threshold_open": 75.0
  },
  "bp": {
    "v3_alpha_stop_loss_ratio": 0.004,
    "v3_alpha_trailing_ratio": 0.15
  }
}
```

### 8.3 输出

回测结果保存到 `outputs/backtest/{tag}/`:
- `batch_summary.json` — 整体汇总
- `{code}_report.json` — 单股报告
- `trades.json` — 交易明细

### 8.4 核心指标

| 指标 | 说明 |
|---|---|
| `paired_trades` | 已配对交易数 |
| `win_rate` | 胜率（基于已配对交易）|
| `payoff_ratio` | 盈亏比（avg_win / |avg_loss|）|
| `net_pnl` | 净盈亏（毛盈亏 - 总成本）|
| `net_pnl_with_unrealized` | 含未配对浮盈浮亏 |

---

## 9. 配置系统

### 9.1 权威参数文件

所有参数集中在 [config/thresholds.yaml](file:///d:/project/a-t0-v2/config/thresholds.yaml)。

### 9.2 V3 相关配置

```yaml
signal:
  # V3 Alpha 开关
  use_continuous_alpha: false        # false=TF baseline, true=V3 Alpha
  alpha_threshold_open: 60.0         # V3 开仓门槛（A/B 测试时用 75）

  # V3 七维度权重
  v3_alpha_weights:
    trend: 0.25
    wave: 0.20
    support: 0.15
    momentum: 0.15
    liquidity: 0.10
    expected_move: 0.10
    risk: 0.05

backtest:
  # V3 专属止损（use_continuous_alpha=true 时生效）
  v3_alpha_stop_loss_ratio: 0.004    # V3 止损 0.4%（TF 的 2x）
  v3_alpha_trailing_ratio: 0.15      # V3 移动止盈回撤 15%
```

### 9.3 加载机制

```python
# config.py
def load_signal_params() -> SignalParams
def load_risk_params() -> RiskParams
def load_backtest_params() -> BacktestParams
```

使用 `hasattr` 检查，新增字段自动从 yaml 加载。

---

## 10. V3 专属风控

### 10.1 动态止损覆盖

V3 alpha 模式下，`BacktestParams` 新增两个 property 自动覆盖 TF 止损：

```python
# backtest.py
@property
def effective_stop_loss_ratio(self) -> float:
    if self.is_mean_reversion and self.mr_stop_loss_ratio is not None:
        return self.mr_stop_loss_ratio
    if self.is_v3_alpha and self.v3_alpha_stop_loss_ratio is not None:
        return self.v3_alpha_stop_loss_ratio  # V3 覆盖
    return self.stop_loss_ratio  # TF 默认 0.002

@property
def effective_trailing_ratio(self) -> float:
    if self.is_v3_alpha and self.v3_alpha_trailing_ratio is not None:
        return self.v3_alpha_trailing_ratio  # V3 覆盖
    return self.trailing_ratio  # TF 默认 0.2
```

### 10.2 参数对比

| 参数 | TF baseline | V3 Alpha | 说明 |
|---|---|---|---|
| stop_loss_ratio | 0.002 (0.2%) | 0.004 (0.4%) | V3 给 alpha 信号更多空间 |
| trailing_ratio | 0.2 | 0.15 | V3 更紧保护利润 |
| alpha_threshold_open | - | 75 | 减少低质量信号 |

### 10.3 Kelly sizing（可选）

```python
from at0.engines import RiskEngine

size = RiskEngine.compute_kelly_size(
    win_rate=0.67,      # 胜率
    avg_win=150,        # 平均盈利
    avg_loss=100,       # 平均亏损
    confidence=0.8,     # 置信度
    max_size=0.25,      # 最大仓位
)
# → 0.18（建议 18% 仓位，半 Kelly + 置信度调整）
```

---

## 11. 性能优化

### 11.1 瓶颈定位

cProfile 分析显示，V3 Alpha 模式 95s 中 **92.8s（97.6%）** 花在 yaml 重复解析上：

```
ncalls  tottime  cumtime  filename
2736    92.8     92.8     alpha_score.py:_load_v3_weights_from_yaml
2736    90.5     90.5     config.py:_load_yaml → yaml.safe_load
```

每根 bar 调用一次 `compute_alpha_score_v3`，每次都重新解析整个 thresholds.yaml。

### 11.2 修复方案

在 [alpha_score.py](file:///d:/project/a-t0-v2/src/at0/score/alpha_score.py#L354-L386) 加入全局 mtime 缓存：

```python
_cached_weights = None
_cached_weights_mtime = None

def _load_v3_weights_from_yaml() -> dict:
    global _cached_weights, _cached_weights_mtime
    yaml_path = PROJECT_ROOT / "config" / "thresholds.yaml"
    mtime = yaml_path.stat().st_mtime
    if _cached_weights is not None and _cached_weights_mtime == mtime:
        return _cached_weights  # 缓存命中
    # 首次或文件变更：重新解析
    ...
```

### 11.3 优化效果

| 场景 | 优化前 | 优化后 | 提速 |
|---|---|---|---|
| 单股 × 2.5 年 | 358.9s | 18.6s | **19.3x** |
| 5 股 × 3 月 | 492s | 20s | **24.6x** |
| 20 股 × 1 年 | >2000s | 327s | **~6x** |

---

## 12. A/B 验证结果

### 12.1 20 股 × 1 年（2025-07-01 ~ 2026-07-22）

| 指标 | A组（TF baseline） | B组（V3 优化后） | 变化 |
|---|---|---|---|
| 配对笔数 | 1,270 | 723 | -43.1% |
| **胜率** | 66.5% | **80.9%** | **+14.4pp** |
| 盈亏比 | 1.47 | 1.05 | -28.6% |
| 净盈亏 | +58,086 | +24,814 | -57.3% |
| 耗时 | 91s | 327s | 3.6x |

### 12.2 5 股 × 3 月（优化前后对比）

| 指标 | V3 优化前（threshold=70, sl=0.002） | V3 优化后（threshold=75, sl=0.004） | 变化 |
|---|---|---|---|
| 胜率 | 68.1% | **79.6%** | **+11.5pp** |
| 盈亏比 | 0.82 | 0.40 | -51.2% |
| 净盈亏 | +520 | +180 | -65.4% |

### 12.3 关键发现

1. **胜率大幅提升**：80.9% 远超 65-70% 目标，V3 Engine 评分有效过滤低质量信号
2. **盈亏比仍是短板**：1.05，止损/止盈参数需进一步调优
3. **净盈亏下降**：高阈值过滤掉部分盈利信号，交易频率下降
4. **性能达标**：20 股×1 年 327s，100 股×3 年预计约 30 分钟

### 12.4 下一步优化方向

1. 放宽 trailing 到 0.25-0.30（让盈利单跑得更久）
2. 降低 threshold 到 72-73（增加信号数量）
3. 按 alpha_score 分档止损（alpha>85 用 0.005，75-85 用 0.003）

---

## 13. 使用指南

### 13.1 环境准备

```bash
# 必需依赖
pip install pyyaml pandas numpy

# V3 可选依赖（Qlib 研究层）
pip install pyqlib lightgbm
```

### 13.2 TF Baseline 回测（A组）

```bash
python scripts/backtest_zz500.py \
    --codes @config/v3_sample_100.txt \
    --start 2024-01-01 --end 2026-07-22 \
    --no-amplitude-filter \
    --params-json config/v3_ab_group_a_baseline.json \
    --tag v3_baseline_100
```

### 13.3 V3 Alpha 回测（B组）

```bash
python scripts/backtest_zz500.py \
    --codes @config/v3_sample_100.txt \
    --start 2024-01-01 --end 2026-07-22 \
    --no-amplitude-filter \
    --params-json config/v3_ab_group_b_alpha_v3.json \
    --tag v3_alpha_100
```

### 13.4 Qlib 研究层验证

```bash
# 100 股抽样
python scripts/v3/sample_100_stocks.py --seed 42 --n 100

# Qlib smoke test
python scripts/v3/qlib_smoke_test.py \
    --codes-file config/v3_sample_100.txt \
    --start 2024-01-01 --end 2026-07-22

# Qlib↔AT0 集成验证
python scripts/v3/qlib_backtest_integration.py \
    --codes-file config/v3_sample_100.txt \
    --start 2024-01-01 --end 2026-07-22
```

### 13.5 性能分析

```bash
python scripts/v3/perf_profile.py
```

### 13.6 自定义 A/B 测试

1. 创建参数 JSON（基于 [config/v3_ab_group_b_alpha_v3.json](file:///d:/project/a-t0-v2/config/v3_ab_group_b_alpha_v3.json)）
2. 修改 `alpha_threshold_open` / `v3_alpha_stop_loss_ratio` / `v3_alpha_trailing_ratio`
3. 运行回测对比

```json
{
  "sp": {
    "use_continuous_alpha": true,
    "strategy_mode": "trend_following",
    "alpha_threshold_open": 72.0
  },
  "bp": {
    "v3_alpha_stop_loss_ratio": 0.005,
    "v3_alpha_trailing_ratio": 0.25
  }
}
```

---

## 14. Stage-Gate 研发流程

### 14.1 实施记录

| Stage | 模块 | 状态 | 关键产出 |
|---|---|---|---|
| G1 | Alpha 连续评分 | ✅ 完成 | 七维度评分框架 + FeatureVector/TradeCandidate |
| G2 | Support Engine | ✅ 完成 | 动态支撑/阻力评分（VWAP+EMA+BB+OR）|
| G3 | Opportunity Engine | ✅ 完成 | 几何平均+算术平均混合 |
| G4 | Wave Engine | ✅ 完成 | Trend Age / HH Count / ATR Expansion / Momentum Decay / Volume Decay |
| G5 | Expected Move Engine | ✅ 完成 | Qlib LightGBM + 统计外推降级 |
| G6 | Execution Engine | ✅ 完成 | EMA 距离 + KDJ 位置 + 量能确认 |
| G7 | Regime Engine | ✅ 完成 | ADX + DI+/DI- + VWAP 偏离，4 种状态 |
| 性能优化 | yaml 缓存 | ✅ 完成 | mtime 缓存，提速 19-25x |
| 止损适配 | V3 专属风控 | ✅ 完成 | v3_alpha_stop_loss_ratio + v3_alpha_trailing_ratio |

### 14.2 研发原则

1. **单变量改动**：每个 Stage 只改一个自由度
2. **A/B 验证**：每完成一个 Stage 做全市场 A/B 回测
3. **汇报确认**：完成后汇报结果，等待确认再进入下一步
4. **归因记录**：保存完整实验记录，避免无法归因

### 14.3 待优化项

- [ ] trailing_ratio 调优（0.15 → 0.25-0.30）
- [ ] alpha_threshold_open 调优（75 → 72-73）
- [ ] 按 alpha_score 分档止损
- [ ] 100 股 × 3 年全量 A/B 验证
- [ ] G6 Execution Engine 实际执行延迟（当前仅评分，未修改回测执行逻辑）

---

## 附录

### A. 关键文件索引

| 文件 | 说明 |
|---|---|
| [strategy.py](file:///d:/project/a-t0-v2/src/at0/strategy.py) | 信号决策层（门面，签名不变）|
| [alpha_score.py](file:///d:/project/a-t0-v2/src/at0/score/alpha_score.py) | V3 Alpha 评分聚合 |
| [engines/](file:///d:/project/a-t0-v2/src/at0/engines/) | V3 Engine 层（G2-G7）|
| [qlib/](file:///d:/project/a-t0-v2/src/at0/qlib/) | Qlib 适配层 |
| [backtest.py](file:///d:/project/a-t0-v2/src/at0/backtest.py) | 回测引擎 |
| [thresholds.yaml](file:///d:/project/a-t0-v2/config/thresholds.yaml) | 权威参数 |

### B. Engine 接口契约

```python
class BaseEngine(ABC):
    @property
    def name(self) -> str: ...
    def score(self, bars: list[dict], snap: dict, direction: str = "reduce") -> float: ...
```

所有 Engine 返回 0~100 的子评分，100 表示最强信号。

### C. 降级链

```
Qlib + LightGBM → 统计外推 → 常数模型
Engine 评分 → G1 占位评分
V3 止损 → TF 默认止损
```
