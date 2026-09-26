"""
HYPERION GUARD: ONE ORDER IN, ONE SIGNED VERDICT OUT
===================================================
Reads the agent's policy and kill state from Arc, fetches an independent
reference price, runs the checks, and signs the result, approved or not.
Rejections are signed and logged too: the record of what was blocked is the
evidence that the Guard did its job.

If Arc can't be read, no verdict is issued (GuardUnavailable). A Guard that
can't see the kill switch must not approve anything.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from . import verdict as vd
from .chain import RpcError
from .ledger import Ledger, leaf, merkle_root
from .policy import EXPLAIN, Order, Reason, RiskBook
from .prices import PriceSource

MAX_TTL = 300                     # HyperionGuard.MAX_VERDICT_TTL


class GuardUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutorCall:
    """An order that will run through a GuardedExecutor on Arc."""
    executor: str
    target: str
    data: str
    nonce: int


class Guard:
    def __init__(self, chain, prices: PriceSource, ledger: Ledger, signer_key: str,
                 ttl: int = 60, clock=time.time):
        if not 0 < ttl <= MAX_TTL:
            raise ValueError(f"ttl must be 1..{MAX_TTL} seconds")
        self.chain, self.prices, self.ledger = chain, prices, ledger
        self.signer_key, self.ttl, self.clock = signer_key, ttl, clock
        self.domain = vd.Domain(chain.chain_id, chain.guard)
        self.book = RiskBook()
        self._restored: set[str] = set()

    def check(self, agent: str, order: Order, *, executor: ExecutorCall | None = None,
              client_order_id: str = "") -> dict:
        now = self.clock()
        try:
            state = self.chain.agent(agent)
        except (RpcError, OSError) as exc:
            raise GuardUnavailable(f"can't read the agent's record on Arc: {exc}") from exc

        key = agent.lower()
        if key not in self._restored:          # after a restart, today's usage comes back from the ledger
            day_start = datetime.fromtimestamp(now, tz=UTC).replace(hour=0, minute=0, second=0,
                                                                              microsecond=0).timestamp()
            self.book.restore(key, self.ledger.approvals_since(key, day_start), now)
            self._restored.add(key)

        reference = self.prices.price(order.symbol) if order.validate() else None
        reason = self.book.check(key, state, order, reference, now)

        if executor is not None:
            order_hash = vd.executor_order_hash(self.domain.chain_id, executor.executor, executor.target,
                                                executor.data, order.notional if order.validate() else 0,
                                                executor.nonce)
        else:
            order_hash = vd.offchain_order_hash(self.domain.chain_id, agent, order.symbol, order.side, order.qty,
                                                order.price, client_order_id)

        approved = reason == Reason.APPROVED
        version = state.policy_version if state else 0
        expires = int(now) + self.ttl
        seq = self.ledger.reserve(
            issued_at=now, agent=key, order_hash=order_hash, approved=int(approved), reason=int(reason),
            policy_version=version, expires_at=expires, symbol=order.symbol, side=order.side, qty=str(order.qty),
            price=str(order.price), notional=order.notional if order.validate() else 0,
            reference=str(reference) if reference is not None else None)
        v = vd.Verdict(agent=agent, order_hash=order_hash, approved=approved, reason=int(reason),
                       policy_version=version, seq=seq, expires_at=expires)
        signature, digest = vd.sign(self.domain, v, self.signer_key)
        self.ledger.finish(seq, digest, signature)
        return {
            "verdict": v.to_json(),
            "signature": signature,
            "digest": digest,
            "approved": approved,
            "reason": reason.name.lower(),
            "explanation": EXPLAIN[reason],
            "notional": order.notional if order.validate() else None,
            "reference_price": str(reference) if reference is not None else None,
            "domain": self.domain.as_dict(),
        }

    def anchor(self, send: bool = True, max_batch: int = 10_000) -> dict | None:
        """Anchor the Merkle root of verdicts since the last on-chain anchor.
        Returns None when there's nothing to anchor."""
        last = self.chain.last_anchored_seq()
        batch = self.ledger.unanchored(last)[:max_batch]
        if not batch:
            return None
        first, final = batch[0][0], batch[-1][0]
        root = merkle_root([leaf(d) for _, d in batch])
        tx = self.chain.send_anchor(self.signer_key, first, final, root) if send else None
        self.ledger.record_anchor(first, final, root, tx, self.clock())
        return {"first_seq": first, "last_seq": final, "root": "0x" + root.hex(), "tx_hash": tx}


def parse_order(body: dict) -> Order:
    try:
        return Order(symbol=str(body["symbol"]).upper(), side=str(body["side"]).lower(),
                     qty=Decimal(str(body["qty"])), price=Decimal(str(body["price"])))
    except (KeyError, ArithmeticError, ValueError) as exc:
        raise ValueError(f"bad order: {exc}") from exc
