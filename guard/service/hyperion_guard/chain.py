"""
HYPERION GUARD: READING AND WRITING ARC
=======================================
Plain JSON-RPC over httpx: read an agent's record from HyperionGuard, and
send the Guard signer's `anchor` transactions.

The agent's record is read on every check by default (`cache_seconds=0`).
That's the point of the on-chain kill switch: Arc finalises in under a
second, so the order after an owner hits kill is refused, not the order
after some cache expires.
"""

from __future__ import annotations

import time

import httpx
from eth_abi import decode, encode
from eth_account import Account
from eth_utils import function_signature_to_4byte_selector, to_checksum_address

from .policy import AgentState, Policy

ZERO = "0x0000000000000000000000000000000000000000"
MIN_BASE_FEE = 20 * 10**9          # Arc drops transactions priced under 20 gwei

_AGENT_OF = function_signature_to_4byte_selector("agentOf(address)")
_ANCHOR = function_signature_to_4byte_selector("anchor(uint64,uint64,bytes32)")
_LAST_ANCHORED = function_signature_to_4byte_selector("lastAnchoredSeq()")
_AGENT_TUPLE = "(address,address,bool,uint32,(uint64,uint64,uint16,uint16))"


class RpcError(RuntimeError):
    pass


class Chain:
    def __init__(self, rpc_url: str, guard_address: str, chain_id: int,
                 client: httpx.Client | None = None, cache_seconds: float = 0.0):
        self.rpc_url = rpc_url
        self.guard = to_checksum_address(guard_address)
        self.chain_id = chain_id
        self.client = client or httpx.Client(timeout=10)
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, AgentState | None]] = {}
        self._id = 0

    def rpc(self, method: str, params: list):
        self._id += 1
        r = self.client.post(self.rpc_url, json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RpcError(f"{method}: {body['error']}")
        return body["result"]

    def call(self, data: bytes) -> bytes:
        out = self.rpc("eth_call", [{"to": self.guard, "data": "0x" + data.hex()}, "latest"])
        return bytes.fromhex(out[2:])

    def agent(self, address: str) -> AgentState | None:
        """The agent's record, or None if it isn't registered."""
        key = address.lower()
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return hit[1]
        raw = self.call(_AGENT_OF + encode(["address"], [to_checksum_address(address)]))
        owner, guardian, killed, version, (max_order, max_daily, collar, per_min) = decode([_AGENT_TUPLE], raw)[0]
        state = None if owner == ZERO else AgentState(
            owner=owner, guardian=guardian, killed=killed, policy_version=version,
            policy=Policy(max_order, max_daily, collar, per_min))
        self._cache[key] = (time.monotonic(), state)
        return state

    def last_anchored_seq(self) -> int:
        return decode(["uint64"], self.call(_LAST_ANCHORED))[0]

    def send_anchor(self, private_key: str, first: int, last: int, root: bytes, wait: float = 30.0) -> str:
        """Send `anchor(first, last, root)` from the Guard signer; returns the
        transaction hash once it's mined."""
        data = _ANCHOR + encode(["uint64", "uint64", "bytes32"], [first, last, root])
        return self.transact(private_key, data, wait)

    def transact(self, private_key: str, data: bytes, wait: float = 30.0) -> str:
        acct = Account.from_key(private_key)
        tx = {"from": acct.address, "to": self.guard, "data": "0x" + data.hex(), "value": 0}
        gas = int(self.rpc("eth_estimateGas", [tx]), 16)
        base = int(self.rpc("eth_gasPrice", []), 16)
        tip = 10**9
        signed = acct.sign_transaction({
            "type": 2, "chainId": self.chain_id, "to": self.guard, "data": tx["data"], "value": 0,
            "nonce": int(self.rpc("eth_getTransactionCount", [acct.address, "pending"]), 16),
            "gas": gas * 12 // 10, "maxPriorityFeePerGas": tip,
            "maxFeePerGas": max(base, MIN_BASE_FEE) * 2 + tip,
        })
        raw = signed.raw_transaction.hex()
        txh = self.rpc("eth_sendRawTransaction", ["0x" + raw.removeprefix("0x")])
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            receipt = self.rpc("eth_getTransactionReceipt", [txh])
            if receipt:
                if int(receipt["status"], 16) != 1:
                    raise RpcError(f"transaction {txh} reverted")
                return txh
            time.sleep(0.5)
        raise RpcError(f"transaction {txh} not mined within {wait:.0f}s")
