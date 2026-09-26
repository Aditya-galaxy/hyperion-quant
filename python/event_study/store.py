"""
HYPERION QUANT: THE EVENT DATABASE
==================================
One SQLite file holding every notice as fetched, the events derived from it,
and what the price did around each one. It is what the API and dashboard
read, and what a daily `ingest` adds to.

Three layers, each rebuildable from the one before:

  notices       raw, exactly as the exchange published them (the source of truth)
  events        one per ticker per notice, re-derived when the classifier changes
  measurements  the price study and tradability grid, redone when the method
                changes (METHOD) or while the price archive is still pending

plus `api_keys`, the customers allowed to read it.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import events as ev

# Bump when the measurement changes, so stored numbers are recomputed rather
# than silently mixed with ones made the old way.
METHOD = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    source       TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    title        TEXT NOT NULL,
    published_at TEXT NOT NULL,           -- first publication, UTC ISO-8601
    raw          TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    source    TEXT NOT NULL,
    source_id TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    at        TEXT NOT NULL,
    title     TEXT NOT NULL,
    UNIQUE (source, source_id, symbol)
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
CREATE TABLE IF NOT EXISTS measurements (
    event_id     INTEGER PRIMARY KEY REFERENCES events (id) ON DELETE CASCADE,
    pair         TEXT NOT NULL,
    status       TEXT NOT NULL,           -- ok | no_pair | no_price | pending
    abnormal     TEXT,                    -- {horizon_s: return vs BTC}
    tradability  TEXT,                    -- see tradability.measure
    method       INTEGER NOT NULL,
    computed_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY,
    prefix       TEXT NOT NULL,           -- first characters, to tell keys apart in a list
    key_hash     TEXT NOT NULL UNIQUE,    -- SHA-256 of the key; the key itself is never stored
    owner        TEXT NOT NULL,
    plan         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT,
    last_used_at TEXT
);
"""


def connect(path: Path | str, migrate: bool = True) -> sqlite3.Connection:
    """`migrate=False` skips creating tables, for the API's per-request
    connections to a database that `migrate` has already set up."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if migrate:
        conn.execute("PRAGMA journal_mode = WAL")   # readers keep reading while ingest writes
        conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def known_ids(conn: sqlite3.Connection, source: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT source_id FROM notices WHERE source = ?", (source,))}


def add_notices(conn: sqlite3.Connection, source: str, notices: list[dict]) -> int:
    """Store notices not seen before; returns how many were new. A notice
    already stored is left alone, so its first publication time never moves."""
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO notices (source, source_id, title, published_at, raw, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(source, str(n["id"]), n["title"], ev.notice_time(n).isoformat(),
          json.dumps(n, ensure_ascii=False), _now()) for n in notices])
    conn.commit()
    return conn.total_changes - before


def derive_events(conn: sqlite3.Connection) -> int:
    """(Re)build events from stored notices. An event whose kind changed —
    the classifier was improved — loses its measurement, since the trade
    direction may have changed with it."""
    changed = 0
    for row in conn.execute("SELECT raw FROM notices WHERE source = 'upbit'").fetchall():
        for e in ev.from_notice(json.loads(row["raw"])):
            old = conn.execute("SELECT id, kind FROM events WHERE source = ? AND source_id = ? AND symbol = ?",
                               (e.source, e.source_id, e.symbol)).fetchone()
            if old is None:
                conn.execute("INSERT INTO events (source, source_id, symbol, kind, at, title) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (e.source, e.source_id, e.symbol, e.kind, e.at.isoformat(), e.title))
                changed += 1
            elif old["kind"] != e.kind:
                conn.execute("UPDATE events SET kind = ? WHERE id = ?", (e.kind, old["id"]))
                conn.execute("DELETE FROM measurements WHERE event_id = ?", (old["id"],))
                changed += 1
    conn.commit()
    return changed


def to_event(row: sqlite3.Row) -> ev.Event:
    return ev.Event(at=datetime.fromisoformat(row["at"]), symbol=row["symbol"], kind=row["kind"],
                    title=row["title"], language="ko", source=row["source"], source_id=row["source_id"])


def needing_measurement(conn: sqlite3.Connection, kinds: tuple[str, ...] = ev.KINDS) -> list[sqlite3.Row]:
    """Events never measured, still pending, or measured by an older method."""
    marks = ",".join("?" * len(kinds))
    return conn.execute(
        f"SELECT e.* FROM events e LEFT JOIN measurements m ON m.event_id = e.id "
        f"WHERE e.kind IN ({marks}) AND (m.event_id IS NULL OR m.status = 'pending' OR m.method != ?) "
        f"ORDER BY e.at", (*kinds, METHOD)).fetchall()


def save_measurement(conn: sqlite3.Connection, event_id: int, pair: str, status: str,
                     abnormal: dict | None = None, tradability: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO measurements (event_id, pair, status, abnormal, tradability, method, computed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (event_id) DO UPDATE SET pair = excluded.pair, "
        "status = excluded.status, abnormal = excluded.abnormal, tradability = excluded.tradability, "
        "method = excluded.method, computed_at = excluded.computed_at",
        (event_id, pair, status,
         json.dumps({str(h): r for h, r in abnormal.items()}) if abnormal else None,
         json.dumps(tradability) if tradability else None, METHOD, _now()))
    conn.commit()


_SELECT = ("SELECT e.*, m.pair, m.status, m.abnormal, m.tradability, m.computed_at FROM events e "
           "LEFT JOIN measurements m ON m.event_id = e.id")


def _decode(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["abnormal"] = json.loads(d["abnormal"]) if d["abnormal"] else None
    d["tradability"] = json.loads(d["tradability"]) if d["tradability"] else None
    d["status"] = d["status"] or "unmeasured"
    return d


def _filters(kinds: tuple[str, ...], since: datetime | None, until: datetime | None,
             symbol: str | None = None, status: str | None = None) -> tuple[str, list]:
    sql = f" WHERE e.kind IN ({','.join('?' * len(kinds))})"
    args: list = list(kinds)
    for clause, value in ((" AND e.at >= ?", since and since.isoformat()),
                          (" AND e.at < ?", until and until.isoformat()),
                          (" AND e.symbol = ?", symbol)):
        if value:
            sql += clause
            args.append(value)
    if status:
        sql += " AND COALESCE(m.status, 'unmeasured') = ?"
        args.append(status)
    return sql, args


def rows(conn: sqlite3.Connection, kinds: tuple[str, ...] = ev.KINDS,
         since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """Events joined with their measurements, oldest first, JSON decoded."""
    where, args = _filters(kinds, since, until)
    return [_decode(r) for r in conn.execute(_SELECT + where + " ORDER BY e.at, e.id", args)]


def page(conn: sqlite3.Connection, kinds: tuple[str, ...] = ev.KINDS, *, since: datetime | None = None,
         until: datetime | None = None, symbol: str | None = None, status: str | None = None,
         limit: int = 100, before: tuple[str, int] | None = None) -> list[dict]:
    """Newest first, at most `limit`, strictly older than the `before`
    (at, id) cursor. Keyset paging: a page never repeats or skips an event
    when new ones arrive between requests."""
    where, args = _filters(kinds, since, until, symbol, status)
    if before:
        where += " AND (e.at < ? OR (e.at = ? AND e.id < ?))"
        args += [before[0], before[0], before[1]]
    sql = _SELECT + where + " ORDER BY e.at DESC, e.id DESC LIMIT ?"
    return [_decode(r) for r in conn.execute(sql, [*args, limit])]


def get(conn: sqlite3.Connection, event_id: int) -> dict | None:
    r = conn.execute(_SELECT + " WHERE e.id = ?", (event_id,)).fetchone()
    return _decode(r) if r else None


# ── API keys ─────────────────────────────────────────────────────────────────

KEY_PREFIX = "hk_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(conn: sqlite3.Connection, owner: str, plan: str) -> str:
    """A new key for `owner`. The key is returned once and only its hash kept:
    lose it and the owner needs a new one."""
    key = KEY_PREFIX + secrets.token_urlsafe(24)
    conn.execute("INSERT INTO api_keys (prefix, key_hash, owner, plan, created_at) VALUES (?, ?, ?, ?, ?)",
                 (key[:10], _hash(key), owner, plan, _now()))
    conn.commit()
    return key


def find_key(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """The live key matching `key`, marking it used (at most once a minute,
    so reads don't turn into a write per request)."""
    row = conn.execute("SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
                       (_hash(key),)).fetchone()
    if row is not None:
        stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ? "
                     "AND (last_used_at IS NULL OR last_used_at < ?)", (_now(), row["id"], stale))
        conn.commit()
    return row


def list_keys(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, prefix, owner, plan, created_at, revoked_at, last_used_at "
                        "FROM api_keys ORDER BY id").fetchall()


def revoke_key(conn: sqlite3.Connection, key_id: int) -> bool:
    cur = conn.execute("UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                       (_now(), key_id))
    conn.commit()
    return cur.rowcount == 1
