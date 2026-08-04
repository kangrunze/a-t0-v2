"""
QuantWeb — 参数配置页面
=======================
在线查看和编辑 thresholds.yaml 配置。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import streamlit as st
import yaml

from web.results_db import get_conn, get_runs


CONFIG_PATH = PROJECT_ROOT / "config" / "thresholds.yaml"


def show() -> None:
    st.title("🔧 参数配置")
    st.caption("编辑 thresholds.yaml 策略参数")

    # 读取当前配置
    if not CONFIG_PATH.exists():
        st.error(f"配置文件不存在: {CONFIG_PATH}")
        return

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        yaml_content = f.read()

    try:
        config = yaml.safe_load(yaml_content)
    except Exception as e:
        st.error(f"YAML 解析错误: {e}")
        config = {}

    # ── 显示配置结构 ──
    tab1, tab2, tab3, tab4 = st.tabs(["📝 七维度权重", "⚙️ 信号参数", "📊 风险参数", "📋 原始 YAML"])

    with tab1:
        weights = config.get("signal", {}).get("v3_alpha_weights", {})
        st.subheader("V3 Alpha 七维度权重")

        st.markdown("""
        七维度权重决定 Alpha 评分中各 Engine 的贡献比例。
        调整后需运行 A/B 回测验证 Rank IC 变化。
        """)

        col1, col2 = st.columns(2)
        weight_keys = ["trend", "wave", "support", "momentum", "liquidity", "expected_move", "risk"]
        weight_labels = {
            "trend": "趋势 (Trend)", "wave": "波浪 (Wave)", "support": "支撑 (Support)",
            "momentum": "动量 (Momentum)", "liquidity": "流动性 (Liquidity)",
            "expected_move": "预期波动 (Expected Move)", "risk": "风险 (Risk)",
        }

        new_weights = {}
        for i, key in enumerate(weight_keys):
            current = weights.get(key, 0.0)
            with (col1 if i < 4 else col2):
                new_val = st.slider(
                    weight_labels.get(key, key),
                    min_value=0.0, max_value=0.50,
                    value=float(current),
                    step=0.01,
                    format="%.2f",
                    key=f"weight_{key}",
                )
                new_weights[key] = new_val

        total_weight = sum(new_weights.values())
        st.metric("权重总和", f"{total_weight:.2f}",
                  delta=f"{'✅ 等于 1.0' if abs(total_weight - 1.0) < 0.01 else '❌ 不等于 1.0'}")

        if st.button("💾 保存权重配置", type="primary"):
            _update_yaml_weight(config, new_weights)
            st.success("✅ 权重已更新！请运行回测验证效果。")

    with tab2:
        signal = config.get("signal", {})
        st.subheader("信号参数")

        # 只读显示关键参数
        param_display = {
            "alpha_threshold_open": "开仓阈值",
            "v3_alpha_close_threshold": "平仓阈值",
            "expected_move_rr_min": "Expected Move RR 最小值",
            "trend_trailing_healthy": "趋势健康 Trailing",
            "trend_trailing_weakening": "趋势减弱 Trailing",
            "trend_trailing_failing": "趋势失败 Trailing",
            "l7_adx_weak_threshold": "L7 ADX 弱阈值",
        }

        param_data = []
        for key, label in param_display.items():
            val = signal.get(key, "N/A")
            param_data.append({"参数": key, "说明": label, "当前值": val})

        st.dataframe(param_data, use_container_width=True, hide_index=True)

        st.info("💡 如需修改，请前往「原始 YAML」标签页直接编辑。")

    with tab3:
        risk = config.get("risk", {})
        st.subheader("风险参数")

        risk_params = {
            "min_capture_spread": "最小捕获空间",
            "max_position_ratio": "最大仓位比例",
            "max_daily_loss_ratio": "最大日亏损比例",
        }

        for key, label in risk_params.items():
            val = risk.get(key, "N/A")
            st.metric(label, f"{val:.4f}" if isinstance(val, float) else val)

        backtest = config.get("backtest", {})
        st.subheader("回测参数")
        bt_params = {
            "stop_loss_ratio": "止损比例",
            "trailing_ratio": "移动止盈回撤",
            "max_holding_bars": "最大持仓 K 线数",
            "cooldown_bars": "冷却 K 线数",
        }
        for key, label in bt_params.items():
            val = backtest.get(key) or signal.get(key, "N/A")
            st.metric(label, f"{val:.4f}" if isinstance(val, float) else val)

    with tab4:
        st.subheader("原始 YAML")
        edited_yaml = st.text_area("编辑 YAML", yaml_content, height=600)

        col1, col2 = st.columns([1, 5])
        with col1:
            if st.button("💾 保存", type="primary"):
                try:
                    yaml.safe_load(edited_yaml)  # 验证
                    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                        f.write(edited_yaml)
                    st.success("✅ 配置已保存!")
                except Exception as e:
                    st.error(f"YAML 格式错误: {e}")

        with col2:
            if st.button("↩️ 撤销"):
                st.rerun()


def _update_yaml_weight(config: dict, new_weights: dict) -> None:
    """更新 YAML 中的 v3_alpha_weights。"""
    if "signal" not in config:
        config["signal"] = {}
    config["signal"]["v3_alpha_weights"] = new_weights

    # 写回文件
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)