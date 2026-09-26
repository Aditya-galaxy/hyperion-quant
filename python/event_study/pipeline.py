"""
HYPERION QUANT: INGEST — KEEP THE DATABASE CURRENT
==================================================
One pass: fetch notices published since the last run, derive events, and
measure every event whose price data is ready. Safe to run as often as you
like; a run with nothing new does one or two requests and no downloads.

An event is "pending" until Binance's archive has published every day its
price window touches (about a day after the fact). Pending events are
retried on each run rather than recorded as missing.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import events as ev
from . import prices, store, study, tradability

LEAD = max(-h for h in study.HORIZONS if h < 0) + 60
TAIL = max(study.HORIZONS) + 60

Loader = Callable[[str, datetime, datetime, Path], "prices.Series | None"]


@dataclass
class Report:
    new_notices: int = 0
    new_events: int = 0
    measured: int = 0
    pending: int = 0
    unavailable: int = 0


def ingest(conn: sqlite3.Connection, price_cache: Path, since: datetime,
           fetch: Callable[[set[str], datetime], list[dict]] | None = ev.fetch_new,
           load: Loader = prices.load, now: datetime | None = None,
           progress: Callable[[str], None] = lambda line: None,
           pause: float = 0.2) -> Report:
    report = Report()
    if fetch is not None:
        report.new_notices = store.add_notices(conn, "upbit", fetch(store.known_ids(conn, "upbit"), since))
    report.new_events = store.derive_events(conn)

    todo = store.needing_measurement(conn)
    for n, row in enumerate(todo, 1):
        event = store.to_event(row)
        status = measure_one(conn, row["id"], event, price_cache, load, now)
        report.measured += status == "ok"
        report.pending += status == "pending"
        report.unavailable += status in ("no_pair", "no_price")
        progress(f"[{n:>4}/{len(todo)}] {event.symbol + 'USDT':<14} {event.kind:<17} {status}")
        if status != "pending" and pause:
            time.sleep(pause)
    return report


def measure_one(conn: sqlite3.Connection, event_id: int, event: ev.Event, price_cache: Path,
                load: Loader = prices.load, now: datetime | None = None) -> str:
    pair = f"{event.symbol}USDT"
    start, end = event.at - timedelta(seconds=LEAD), event.at + timedelta(seconds=TAIL)
    if not prices.archive_settled(end.astimezone(timezone.utc).date(), now):
        store.save_measurement(conn, event_id, pair, "pending")
        return "pending"

    series = load(pair, start, end, price_cache)
    if series is None:
        store.save_measurement(conn, event_id, pair, "no_pair")
        return "no_pair"
    market = None if pair == study.MARKET else load(study.MARKET, start, end, price_cache)
    abnormal = study.measure(event, series, market)
    if abnormal is None:
        store.save_measurement(conn, event_id, pair, "no_price")
        return "no_price"
    store.save_measurement(conn, event_id, pair, "ok", abnormal, tradability.measure(event, series),
                           study.price_path(event, series))
    return "ok"
