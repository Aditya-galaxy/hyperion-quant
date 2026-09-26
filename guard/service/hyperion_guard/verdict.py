"""
HYPERION GUARD: SIGNED VERDICTS
===============================
A verdict is an EIP-712 message that HyperionGuard.sol can verify: the same
type string, field order and domain, so `hashVerdict` on-chain and `digest`
here agree bit for bit (tests/test_verdict.py pins a vector the Solidity
tests check too).

Two kinds of order hash:
  * executor orders — exactly GuardedExecutor.orderHash, binding the chain,
    the wallet, the target, the calldata, the notional and the nonce;
  * off-chain orders (a CEX, a venue without a Guard hook) — a hash of the
    order's canonical fields, which the agent's venue adapter can recompute.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_checksum_address

VERDICT_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Verdict": [
        {"name": "agent", "type": "address"},
        {"name": "orderHash", "type": "bytes32"},
        {"name": "approved", "type": "bool"},
        {"name": "reason", "type": "uint16"},
        {"name": "policyVersion", "type": "uint32"},
        {"name": "seq", "type": "uint64"},
        {"name": "expiresAt", "type": "uint64"},
    ],
}


@dataclass(frozen=True)
class Verdict:
    agent: str
    order_hash: str               # 0x-hex, 32 bytes
    approved: bool
    reason: int
    policy_version: int
    seq: int
    expires_at: int

    def message(self) -> dict:
        return {"agent": to_checksum_address(self.agent), "orderHash": bytes.fromhex(self.order_hash[2:]),
                "approved": self.approved, "reason": self.reason, "policyVersion": self.policy_version,
                "seq": self.seq, "expiresAt": self.expires_at}

    def as_solidity_tuple(self) -> tuple:
        """The struct as a contract call takes it."""
        m = self.message()
        return (m["agent"], m["orderHash"], m["approved"], m["reason"], m["policyVersion"], m["seq"], m["expiresAt"])

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Domain:
    chain_id: int
    contract: str                 # HyperionGuard address

    def as_dict(self) -> dict:
        return {"name": "HyperionGuard", "version": "1", "chainId": self.chain_id,
                "verifyingContract": to_checksum_address(self.contract)}


def _typed(domain: Domain, v: Verdict):
    return encode_typed_data(full_message={"types": VERDICT_TYPES, "primaryType": "Verdict",
                                           "domain": domain.as_dict(), "message": v.message()})


def sign(domain: Domain, v: Verdict, private_key: str) -> tuple[str, str]:
    """(signature, digest), both 0x-hex. The digest is also the verdict's
    leaf in the audit trail's Merkle tree."""
    signed = Account.sign_message(_typed(domain, v), private_key=private_key)
    return "0x" + signed.signature.hex().removeprefix("0x"), "0x" + signed.message_hash.hex().removeprefix("0x")


def recover(domain: Domain, v: Verdict, signature: str) -> str:
    return Account.recover_message(_typed(domain, v), signature=signature)


def executor_order_hash(chain_id: int, executor: str, target: str, data: str, notional: int, nonce: int) -> str:
    """GuardedExecutor.orderHash, byte for byte."""
    call = bytes.fromhex(data.removeprefix("0x"))
    packed = encode(["uint256", "address", "address", "bytes32", "uint256", "uint256"],
                    [chain_id, to_checksum_address(executor), to_checksum_address(target), keccak(call),
                     notional, nonce])
    return "0x" + keccak(packed).hex()


def _canon(x: Decimal) -> str:
    """One spelling per number: 1.50, 1.5 and 1.500 hash the same."""
    s = format(x.normalize(), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def offchain_order_hash(chain_id: int, agent: str, symbol: str, side: str, qty: Decimal, price: Decimal,
                        client_order_id: str) -> str:
    """For orders placed off-chain. The client order id makes each order
    unique, so an approval can't be reused for an identical second order."""
    packed = encode(["string", "uint256", "address", "string", "string", "string", "string", "string"],
                    ["hyperion-guard/order/v1", chain_id, to_checksum_address(agent), symbol.upper(), side,
                     _canon(qty), _canon(price), client_order_id])
    return "0x" + keccak(packed).hex()
