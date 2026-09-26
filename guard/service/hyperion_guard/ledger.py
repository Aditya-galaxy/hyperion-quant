"""
HYPERION GUARD: THE VERDICT LEDGER AND ITS MERKLE ROOTS
======================================================
Every verdict gets the next sequence number and a row here before it's
returned. Periodically the Guard anchors the Merkle root of the verdicts
since the last anchor on Arc (HyperionGuard.anchor), in contiguous ranges,
so anyone holding a verdict can prove it's in the record, and the Guard
can't quietly drop one.

The tree is OpenZeppelin-compatible (MerkleProof.verify): leaves are
keccak256(verdict digest), pairs are hashed in sorted order, and an odd node
is carried up a level unchanged.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from eth_utils import keccak

SCHEMA = """
CREATE TABLE IF NOT EXISTS verdicts (
    seq            INTEGER PRIMARY KEY,
    issued_at      REAL NOT NULL,
    agent          TEXT NOT NULL,
    order_hash     TEXT NOT NULL,
    approved       INTEGER NOT NULL,
    reason         INTEGER NOT NULL,
    policy_version INTEGER NOT NULL,
    expires_at     INTEGER NOT NULL,
    symbol         TEXT,
    side           TEXT,
    qty            TEXT,
    price          TEXT,
    notional       INTEGER,
    reference      TEXT,
    digest         TEXT,
    signature      TEXT
);
CREATE INDEX IF NOT EXISTS verdicts_agent ON verdicts (agent, issued_at);
CREATE TABLE IF NOT EXISTS anchors (
    first_seq INTEGER PRIMARY KEY,
    last_seq  INTEGER NOT NULL,
    root      TEXT NOT NULL,
    tx_hash   TEXT,
    anchored_at REAL NOT NULL
);
"""


def _pair(a: bytes, b: bytes) -> bytes:
    return keccak(a + b) if a < b else keccak(b + a)


def leaf(digest_hex: str) -> bytes:
    return keccak(bytes.fromhex(digest_hex.removeprefix("0x")))


def merkle_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        raise ValueError("no leaves")
    level = leaves
    while len(level) > 1:
        level = [_pair(level[i], level[i + 1]) if i + 1 < len(level) else level[i]
                 for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: list[bytes], index: int) -> list[bytes]:
    proof, level = [], leaves
    while len(level) > 1:
        sibling = index ^ 1
        if sibling < len(level):
            proof.append(level[sibling])
        level = [_pair(level[i], level[i + 1]) if i + 1 < len(level) else level[i]
                 for i in range(0, len(level), 2)]
        index //= 2
    return proof


def verify(proof: list[bytes], root: bytes, leaf_: bytes) -> bool:
    node = leaf_
    for p in proof:
        node = _pair(node, p)
    return node == root


class Ledger:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def reserve(self, **fields) -> int:
        """Take the next sequence number for a verdict about to be signed."""
        cols = ", ".join(fields)
        with self.lock:
            cur = self.conn.execute(f"INSERT INTO verdicts ({cols}) VALUES ({', '.join('?' * len(fields))})",
                                    tuple(fields.values()))
            self.conn.commit()
            return cur.lastrowid

    def finish(self, seq: int, digest: str, signature: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE verdicts SET digest = ?, signature = ? WHERE seq = ?", (digest, signature, seq))
            self.conn.commit()

    def get(self, seq: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM verdicts WHERE seq = ?", (seq,)).fetchone()
        return dict(r) if r else None

    def approvals_since(self, agent: str, since: float) -> list[tuple[float, int]]:
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT issued_at, notional FROM verdicts WHERE agent = ? AND approved = 1 AND issued_at >= ?",
            (agent.lower(), since))]

    def recent(self, agent: str | None = None, limit: int = 50) -> list[dict]:
        sql, args = "SELECT * FROM verdicts", []
        if agent:
            sql += " WHERE agent = ?"
            args.append(agent.lower())
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY seq DESC LIMIT ?", (*args, limit))]

    # ── anchoring ────────────────────────────────────────────────────────────

    def unanchored(self, after_seq: int) -> list[tuple[int, str]]:
        """(seq, digest) of signed verdicts after `after_seq`, stopping at the
        first gap (a verdict reserved but not yet signed)."""
        out = []
        for seq, digest in self.conn.execute("SELECT seq, digest FROM verdicts WHERE seq > ? ORDER BY seq",
                                             (after_seq,)):
            if digest is None or seq != (out[-1][0] + 1 if out else after_seq + 1):
                break
            out.append((seq, digest))
        return out

    def record_anchor(self, first: int, last: int, root: bytes, tx_hash: str | None, at: float) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO anchors VALUES (?, ?, ?, ?, ?)",
                              (first, last, "0x" + root.hex(), tx_hash, at))
            self.conn.commit()

    def proof_for(self, seq: int) -> dict | None:
        """The anchored range holding `seq`, its root, and the proof."""
        a = self.conn.execute("SELECT * FROM anchors WHERE first_seq <= ? AND last_seq >= ?", (seq, seq)).fetchone()
        if a is None:
            return None
        digests = [r[0] for r in self.conn.execute(
            "SELECT digest FROM verdicts WHERE seq BETWEEN ? AND ? ORDER BY seq", (a["first_seq"], a["last_seq"]))]
        leaves = [leaf(d) for d in digests]
        i = seq - a["first_seq"]
        return {"first_seq": a["first_seq"], "last_seq": a["last_seq"], "root": a["root"], "tx_hash": a["tx_hash"],
                "leaf": "0x" + leaves[i].hex(), "proof": ["0x" + p.hex() for p in merkle_proof(leaves, i)]}
