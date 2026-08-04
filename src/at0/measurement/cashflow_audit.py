"""
cashflow_audit — 全现金流对账（P1 对账铁律）
===========================================
防统计幻觉（P1 对账铁律）

正确对账公式:
  cashflow_pnl = cash_in - cash_out - total_cost
  paired_total = paired_pnl + unpaired_net_notional - total_cost
  其中 unpaired_net_notional = 未配对腿净额(卖出收入-买入支出, 不含成本)
  验证: cashflow_pnl == paired_total (差异 < tolerance)
  关键: 成本只在 total_cost 中扣减一次, 不在 unpaired 中重复扣减。
"""
from dataclasses import dataclass


def _get_qty(trade: dict) -> float:
    """获取交易数量，兼容 shares / fill_qty 两种字段名。"""
    return float(trade.get("shares", trade.get("fill_qty", 0)))


def _get_fill_price(trade: dict) -> float:
    return float(trade.get("fill_price", 0))


def _get_cost(trade: dict) -> float:
    return float(trade.get("cost", 0))


def _get_pnl(trade: dict) -> float:
    return float(trade.get("pnl", 0))


def _is_sell(direction: str) -> bool:
    return direction in ("sell", "reduce")


def _is_buy(direction: str) -> bool:
    return direction in ("buy", "add")


@dataclass
class CashflowAudit:
    """全现金流对账结果。"""

    cashflow_pnl: float = 0.0
    paired_total: float = 0.0
    discrepancy: float = 0.0
    is_matched: bool = False
    n_trades: int = 0

    def to_dict(self) -> dict:
        return {
            "cashflow_pnl": self.cashflow_pnl,
            "paired_total": self.paired_total,
            "discrepancy": self.discrepancy,
            "is_matched": self.is_matched,
            "n_trades": self.n_trades,
        }


def audit_cashflow(
    trades: list[dict],
    unrealized_pnl: float = 0,
    expired_pnl: float = 0,
    tolerance: float = 0.01,
) -> CashflowAudit:
    """全现金流对账 — 防统计幻觉（P1 对账铁律）。

    Parameters
    ----------
    trades : list[dict]
        交易记录列表，每条含 direction / fill_price / shares(fill_qty) / cost / pnl / paired 等字段。
    unrealized_pnl : float, default 0
        未配对敞口浮盈（用于调整 cashflow_pnl）。
    expired_pnl : float, default 0
        超时腿真实盈亏（用于调整 cashflow_pnl）。
    tolerance : float, default 0.01
        对账容差，cashflow_pnl 与 paired_total 之差小于此值视为对平。

    Returns
    -------
    CashflowAudit
    """
    cash_in = 0.0
    cash_out = 0.0
    total_cost = 0.0
    paired_pnl = 0.0
    unpaired_notional = 0.0

    for t in trades:
        direction = t.get("direction", "")
        price = _get_fill_price(t)
        qty = _get_qty(t)
        cost = _get_cost(t)

        total_cost += cost
        notional = price * qty

        if _is_sell(direction):
            cash_in += notional
        elif _is_buy(direction):
            cash_out += notional

        if t.get("paired"):
            paired_pnl += _get_pnl(t)
        else:
            # 未配对腿：卖出收入为正，买入支出为负
            if _is_sell(direction):
                unpaired_notional += notional
            elif _is_buy(direction):
                unpaired_notional -= notional

    cashflow_pnl = cash_in - cash_out - total_cost + unrealized_pnl + expired_pnl
    paired_total = paired_pnl + unpaired_notional - total_cost
    discrepancy = abs(cashflow_pnl - paired_total)
    is_matched = discrepancy < tolerance

    return CashflowAudit(
        cashflow_pnl=round(cashflow_pnl, 4),
        paired_total=round(paired_total, 4),
        discrepancy=round(discrepancy, 4),
        is_matched=is_matched,
        n_trades=len(trades),
    )