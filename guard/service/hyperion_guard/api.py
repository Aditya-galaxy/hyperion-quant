"""
HYPERION GUARD: HTTP API
========================
    GET  /v1/health                  signer, contract, chain
    POST /v1/check                   one order → one signed verdict (paid, via the proxy)
    GET  /v1/agents/{address}        the agent's policy and kill state, read from Arc
    GET  /v1/verdicts?agent=0x…      recent verdicts
    GET  /v1/verdicts/{seq}          one verdict, with its Merkle proof once anchored

Payment is not handled here. In production this app listens on localhost
only and the x402 payment proxy (guard/paywall) sits in front of it:
`/v1/check` costs a fraction of a cent per call in USDC via Circle Gateway,
and everything else passes through free, so anyone can audit a verdict.
"""

from __future__ import annotations

import re
from typing import Annotated

from eth_account import Account
from fastapi import Body, FastAPI, HTTPException

from .engine import ExecutorCall, Guard, GuardUnavailable, parse_order

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX = re.compile(r"^0x([0-9a-fA-F]{2})*$")


def _address(value: str, name: str) -> str:
    if not isinstance(value, str) or not ADDRESS.match(value):
        raise HTTPException(422, f"{name} must be a 0x-prefixed 20-byte address")
    return value


def create_app(guard: Guard) -> FastAPI:
    app = FastAPI(title="Hyperion Guard", version="1",
                  description="Pre-trade risk checks for autonomous trading agents, with the kill switch on Arc.")
    signer = Account.from_key(guard.signer_key).address

    @app.get("/v1/health")
    def health():
        return {"ok": True, "signer": signer, "contract": guard.domain.contract, "chain_id": guard.domain.chain_id,
                "verdict_ttl_s": guard.ttl}

    @app.post("/v1/check")
    def check(body: Annotated[dict, Body()]):
        agent = _address(body.get("agent"), "agent")
        try:
            order = parse_order(body.get("order") or {})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        executor = None
        if body.get("executor") is not None:
            e = body["executor"]
            if not isinstance(e, dict) or not HEX.match(str(e.get("data", ""))) or not isinstance(e.get("nonce"), int) \
                    or e["nonce"] < 0:
                raise HTTPException(422, "executor needs address, target, data (0x-hex) and a non-negative nonce")
            executor = ExecutorCall(executor=_address(e.get("address"), "executor.address"),
                                    target=_address(e.get("target"), "executor.target"),
                                    data=e["data"], nonce=e["nonce"])
        client_order_id = str(body.get("client_order_id", ""))[:128]
        try:
            return guard.check(agent, order, executor=executor, client_order_id=client_order_id)
        except GuardUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/v1/agents/{address}")
    def agent(address: str):
        _address(address, "address")
        try:
            state = guard.chain.agent(address)
        except Exception as exc:  # noqa: BLE001 — any RPC failure is "can't tell right now"
            raise HTTPException(503, f"can't read Arc: {exc}") from None
        if state is None:
            raise HTTPException(404, "agent isn't registered")
        p = state.policy
        return {"agent": address, "owner": state.owner, "guardian": state.guardian, "killed": state.killed,
                "policy_version": state.policy_version,
                "policy": {"max_order_notional": p.max_order_notional, "max_daily_notional": p.max_daily_notional,
                           "collar_bps": p.collar_bps, "max_orders_per_minute": p.max_orders_per_minute}}

    @app.get("/v1/verdicts")
    def verdicts(agent: str | None = None, limit: int = 50):
        if agent:
            _address(agent, "agent")
        return {"data": guard.ledger.recent(agent, max(1, min(limit, 500)))}

    @app.get("/v1/verdicts/{seq}")
    def verdict(seq: int):
        row = guard.ledger.get(seq)
        if row is None:
            raise HTTPException(404, "no such verdict")
        return {"verdict": row, "anchor": guard.ledger.proof_for(seq)}

    return app
