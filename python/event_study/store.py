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
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
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
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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


def rows(conn: sqlite3.Connection, kinds: tuple[str, ...] = ev.KINDS,
         since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """Events joined with their measurements, oldest first, JSON decoded."""
    marks = ",".join("?" * len(kinds))
    sql = (f"SELECT e.*, m.pair, m.status, m.abnormal, m.tradability, m.computed_at FROM events e "
           f"LEFT JOIN measurements m ON m.event_id = e.id WHERE e.kind IN ({marks})")
    args: list = list(kinds)
    if since:
        sql += " AND e.at >= ?"
        args.append(since.isoformat())
    if until:
        sql += " AND e.at < ?"
        args.append(until.isoformat())
    out = []
    for r in conn.execute(sql + " ORDER BY e.at", args):
        d = dict(r)
        d["abnormal"] = json.loads(d["abnormal"]) if d["abnormal"] else None
        d["tradability"] = json.loads(d["tradability"]) if d["tradability"] else None
        d["status"] = d["status"] or "unmeasured"
        out.append(d)
    return out
