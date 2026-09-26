"""
HYPERION GUARD: THE PRE-TRADE CHECKS
====================================
Modelled on the Rust controller in src/risk/controller.rs (kill switch
first, then size, collar and throttle), with a daily notional cap in place of
its position limit, since the Guard sees orders, not fills. Applied to the
agent's policy as its owner set it on Arc:

  1. kill switch          the owner (or guardian) stopped the agent on-chain
  2. order notional       a single order bigger than the policy allows
  3. daily notional       this order would take the UTC day past its cap
  4. price collar         the price is too far from an independent reference
  5. throttle             too many approved orders in the last 60 seconds

Only approved orders use up the day's budget and the throttle, as in the
Rust controller: a flood of rejected orders can't lock an agent out.

Amounts are integers in USDC's 6-decimal units, as on-chain. Prices are
Decimals: an order's notional is qty × price, rounded up so rounding never
lets an order slip under a limit.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from enum import IntEnum

USDC = Decimal(10) ** 6


class Reason(IntEnum):
    """Rejection codes carried in the signed verdict (0 = approved)."""
    APPROVED = 0
    KILLED = 1
    ORDER_NOTIONAL = 2
    DAILY_NOTIONAL = 3
    PRICE_COLLAR = 4
    RATE_LIMIT = 5
    NO_REFERENCE_PRICE = 6
    UNKNOWN_AGENT = 7
    BAD_ORDER = 8


EXPLAIN = {
    Reason.APPROVED: "approved",
    Reason.KILLED: "the agent's owner or guardian has stopped it",
    Reason.ORDER_NOTIONAL: "order is larger than the policy's per-order limit",
    Reason.DAILY_NOTIONAL: "order would exceed the policy's daily limit",
    Reason.PRICE_COLLAR: "price is too far from the reference price",
    Reason.RATE_LIMIT: "too many orders in the last minute",
    Reason.NO_REFERENCE_PRICE: "no fresh reference price for this market",
    Reason.UNKNOWN_AGENT: "agent isn't registered with the Guard contract",
    Reason.BAD_ORDER: "order is malformed",
}


@dataclass(frozen=True)
class Policy:
    """As stored in HyperionGuard.sol."""
    max_order_notional: int          # USDC 6-dp
    max_daily_notional: int          # USDC 6-dp
    collar_bps: int
    max_orders_per_minute: int


@dataclass(frozen=True)
class AgentState:
    """An agent's record as read from the contract."""
    owner: str
    guardian: str
    killed: bool
    policy_version: int
    policy: Policy


@dataclass(frozen=True)
class Order:
    symbol: str                      # e.g. "BTC-USD"
    side: str                        # "buy" | "sell"
    qty: Decimal
    price: Decimal

    @property
    def notional(self) -> int:
        """USDC 6-dp, rounded up."""
        return int((self.qty * self.price * USDC).to_integral_value(rounding=ROUND_CEILING))

    def validate(self) -> bool:
        # is_finite first: comparing a NaN Decimal raises instead of returning False
        return (self.side in ("buy", "sell") and bool(self.symbol) and self.qty.is_finite()
                and self.price.is_finite() and self.qty > 0 and self.price > 0)


@dataclass
class _Usage:
    day: str = ""
    spent: int = 0
    recent: deque = field(default_factory=deque)     # unix seconds of approved orders


class RiskBook:
    """Per-agent usage: the day's approved notional and recent approvals.

    Kept in memory, per process, like the Rust controller's window. The
    verdict ledger holds the durable record; `restore` rebuilds usage from it
    after a restart.
    """

    def __init__(self) -> None:
        self._usage: dict[str, _Usage] = defaultdict(_Usage)
        self._lock = threading.Lock()

    def check(self, agent: str, state: AgentState | None, order: Order,
              reference: Decimal | None, now: float) -> Reason:
        """Decide one order and, if approved, record its use of the budget."""
        if state is None:
            return Reason.UNKNOWN_AGENT
        if not order.validate():
            return Reason.BAD_ORDER
        if state.killed:
            return Reason.KILLED
        p, notional = state.policy, order.notional
        if notional > p.max_order_notional:
            return Reason.ORDER_NOTIONAL
        with self._lock:
            u = self._usage[agent.lower()]
            today = datetime.fromtimestamp(now, tz=UTC).date().isoformat()
            if u.day != today:
                u.day, u.spent = today, 0
            if u.spent + notional > p.max_daily_notional:
                return Reason.DAILY_NOTIONAL
            if reference is None or reference <= 0:
                return Reason.NO_REFERENCE_PRICE
            if outside_collar(order, reference, p.collar_bps):
                return Reason.PRICE_COLLAR
            while u.recent and u.recent[0] <= now - 60:
                u.recent.popleft()
            if len(u.recent) >= p.max_orders_per_minute:
                return Reason.RATE_LIMIT
            u.spent += notional
            u.recent.append(now)
        return Reason.APPROVED

    def restore(self, agent: str, approvals: list[tuple[float, int]], now: float) -> None:
        """Rebuild usage from (time, notional) of today's approved verdicts."""
        today = datetime.fromtimestamp(now, tz=UTC).date().isoformat()
        with self._lock:
            u = self._usage[agent.lower()] = _Usage(day=today)
            for t, notional in sorted(approvals):
                if datetime.fromtimestamp(t, tz=UTC).date().isoformat() == today:
                    u.spent += notional
                if t > now - 60:
                    u.recent.append(t)


def outside_collar(order: Order, reference: Decimal, collar_bps: int) -> bool:
    """A buy may pay at most reference × (1 + collar); a sell may take no
    less than reference × (1 − collar). The side that could hurt is the only
    one bounded, as in the Rust controller: a cheap buy or a rich sell only
    fails to fill."""
    band = Decimal(collar_bps) / Decimal(10_000)
    if order.side == "buy":
        return order.price > reference * (1 + band)
    return order.price < reference * (1 - band)
