"""
Stage D: DecisionEngineV4 — 统一决策层
========================================

职责（从 backtest.py 提取内联决策逻辑）：
  将各 Engine 评分整合为统一的决策输出，替代 ~200 行 inline 代码。

设计原则：
  1. 单一决策入口：一次 decide() 调用完成所有决策
  2. 过程可审计：decision_log 记录每步决策理由
  3. 行为不变：重构不改变决策结果（与旧 inline 逻辑逐笔一致）
  4. 可插拔：各子引擎可独立开关

决策流：
  ┌─────────────────────────────────────────────────────────┐
  │ 1. collect_scores()   ← Alpha + L4 + L6/L7 + Stage X  │
  │ 2. evaluate_open()    ← Alpha threshold + L4 RR 闸门  │
  │ 3. evaluate_close()   ← L6/L7 + Stage X 强制退出      │
  │ 4. apply_constraints() ← Cooldown                      │
  │ 5. resolve_priority()  ← reduce > add                  │
  └─────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DecisionResult:
    """决策输出。

    :param reduce_ok: 是否允许卖出（reduce / 平仓 buy 仓）
    :param add_ok: 是否允许买入（add / 平仓 sell 仓）
    :param decision_log: 完整决策过程记录，用于审计和调试
    """
    reduce_ok: bool = False
    add_ok: bool = False
    decision_log: dict[str, Any] = field(default_factory=dict)


class DecisionEngineV4:
    """V4 统一决策引擎。

    用法（在 backtest.py 中替换 inline 逻辑）：:

        engine = DecisionEngineV4()
        result = engine.decide(
            bars_up_to_now=bars_up_to_now,
            snap_r=_snap_r, snap_a=_snap_a,
            alpha_r=_alpha_r, alpha_a=_alpha_a,
            sub_r=_sub_r, sub_a=_sub_a,
            has_buy_open=has_buy_open,
            has_sell_open=has_sell_open,
            params=params,
            state=state,
            is_limit_up_locked=is_limit_up_locked,
            is_limit_down_locked=is_limit_down_locked,
        )
        reduce_ok = result.reduce_ok
        add_ok = result.add_ok
        state.alpha_ctx.update(result.decision_log)
    """

    @staticmethod
    def decide(
        *,
        bars_up_to_now: list[dict],
        snap_r: dict,
        snap_a: dict,
        alpha_r: float,
        alpha_a: float,
        sub_r: Optional[dict] = None,
        sub_a: Optional[dict] = None,
        has_buy_open: bool = False,
        has_sell_open: bool = False,
        params: Any = None,
        state: Any = None,
        is_limit_up_locked: bool = False,
        is_limit_down_locked: bool = False,
    ) -> DecisionResult:
        """执行完整决策流，返回 reduce_ok / add_ok + 决策日志。

        :param bars_up_to_now: 截至当前 K 线的完整 K 线切片
        :param snap_r: reduce 方向特征快照（含 _direction, _bars）
        :param snap_a: add 方向特征快照（含 _direction, _bars）
        :param alpha_r: reduce 方向 Alpha 评分 (0~100)
        :param alpha_a: add 方向 Alpha 评分 (0~100)
        :param sub_r: reduce 方向子评分 dict
        :param sub_a: add 方向子评分 dict
        :param has_buy_open: 是否有 buy 方向未配对腿
        :param has_sell_open: 是否有 sell 方向未配对腿
        :param params: BacktestParams 实例（含 signal_params）
        :param state: 回测状态（含 alpha_ctx, lifecycle, last_signal_bar 等）
        :param is_limit_up_locked: 是否涨停封板
        :param is_limit_down_locked: 是否跌停封板
        :return: DecisionResult 实例
        """
        log: dict[str, Any] = {}
        sp = params.signal_params if params else None
        if sp is None:
            return DecisionResult(reduce_ok=False, add_ok=False, decision_log=log)

        # ── 1. 收集评分 ──
        _close_th = sp.v3_alpha_close_threshold
        if _close_th is None:
            _close_th = sp.alpha_threshold_open
        _open_th = sp.alpha_threshold_open
        _reduce_th = _close_th if has_buy_open else _open_th
        _add_th = _close_th if has_sell_open else _open_th

        reduce_ok = alpha_r >= _reduce_th and not is_limit_up_locked
        add_ok = alpha_a >= _add_th and not is_limit_down_locked

        log["alpha_reduce"] = round(alpha_r, 2)
        log["alpha_add"] = round(alpha_a, 2)
        log["reduce_threshold"] = _reduce_th
        log["add_threshold"] = _add_th
        log["alpha_reduce_ok"] = reduce_ok
        log["alpha_add_ok"] = add_ok

        # ── 2. L4 Expected Move 开仓闸门 ──
        if sp.expected_move_gate_enabled:
            from at0.score.alpha_score import compute_expected_move_rr as _em_rr
            _rr_min = sp.expected_move_rr_min

            # reduce 开仓（not has_buy_open = 新建 sell 仓）
            if reduce_ok and not has_buy_open:
                _rr_r = _em_rr(bars_up_to_now, snap_r, "reduce")
                log["expected_rr_reduce"] = _rr_r
                if _rr_r is not None and _rr_r < _rr_min:
                    reduce_ok = False
                    log["expected_rr_reduce_blocked"] = True

            # add 开仓（not has_sell_open = 新建 buy 仓）
            if add_ok and not has_sell_open:
                _rr_a = _em_rr(bars_up_to_now, snap_a, "add")
                log["expected_rr_add"] = _rr_a
                if _rr_a is not None and _rr_a < _rr_min:
                    add_ok = False
                    log["expected_rr_add_blocked"] = True

        # ── 3. L6/L7 HoldConfidence + TrendFailure 退出 ──
        if sp.hold_confidence_exit_enabled:
            from at0.engines.hold_confidence_engine import (
                HoldConfidenceEngine as _HCE,
                check_trend_failure as _CTF,
            )
            _hc_engine = _HCE()
            _hc_th = sp.hold_confidence_exit_threshold

            # buy 仓平仓（reduce 信号）
            if has_buy_open and not reduce_ok:
                _hc = _hc_engine.score(bars_up_to_now, snap_r, "reduce")
                _adx_th = sp.l7_adx_weak_threshold
                _tf, _tf_reason = _CTF(snap_r, "reduce", adx_weak_threshold=_adx_th)
                log["hc_reduce"] = round(_hc, 2)
                log["tf_reduce"] = _tf
                log["tf_reduce_reason"] = _tf_reason
                if _tf or _hc < _hc_th:
                    reduce_ok = True and not is_limit_up_locked
                    log["hc_forced_exit_reduce"] = True

            # sell 仓平仓（add 信号）
            if has_sell_open and not add_ok:
                _hc = _hc_engine.score(bars_up_to_now, snap_a, "add")
                _adx_th = sp.l7_adx_weak_threshold
                _tf, _tf_reason = _CTF(snap_a, "add", adx_weak_threshold=_adx_th)
                log["hc_add"] = round(_hc, 2)
                log["tf_add"] = _tf
                log["tf_add_reason"] = _tf_reason
                if _tf or _hc < _hc_th:
                    add_ok = True and not is_limit_down_locked
                    log["hc_forced_exit_add"] = True

        # ── 4. Stage X Predictive Exit 趋势衰竭预测退出 ──
        if sp.predictive_exit_enabled:
            from at0.engines.predictive_exit_engine import PredictiveExitEngine as _PEE
            _pe_engine = _PEE()
            _pe_th = sp.predictive_exit_threshold

            # buy 仓平仓（reduce 信号）
            if has_buy_open and not reduce_ok:
                _pe = _pe_engine.score(bars_up_to_now, snap_r, "reduce")
                log["pe_reduce"] = round(_pe, 2)
                if _pe >= _pe_th:
                    reduce_ok = True and not is_limit_up_locked
                    log["pe_forced_exit_reduce"] = True

            # sell 仓平仓（add 信号）
            if has_sell_open and not add_ok:
                _pe = _pe_engine.score(bars_up_to_now, snap_a, "add")
                log["pe_add"] = round(_pe, 2)
                if _pe >= _pe_th:
                    add_ok = True and not is_limit_down_locked
                    log["pe_forced_exit_add"] = True

        return DecisionResult(
            reduce_ok=reduce_ok,
            add_ok=add_ok,
            decision_log=log,
        )