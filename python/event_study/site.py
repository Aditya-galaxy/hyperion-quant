"""
HYPERION EVENTS: THE PUBLIC SITE
================================
Builds a static site from the event database: one page with the findings,
charts and an event feed, plus the data behind it as CSV and JSON. No server
and no keys, so it can be hosted anywhere static files can (GitHub Pages, a
GCS bucket).

Every number on the page is computed here from the database; nothing is typed
in by hand, so a rebuild after `ingest` is the whole update. What's published
follows terms.py: CC BY-NC-SA 4.0, credited to Binance Vision, and links to
Upbit's notices rather than their text.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import warnings
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import numpy as np

from . import events as ev
from . import store, study, summary, terms, tradability

LABELS = {"listing": "Listings", "delisting": "Delistings", "caution_on": "Caution designations",
          "caution_extended": "Caution extended", "caution_off": "Caution lifted", "warning": "Warnings"}
MIN_EVENTS = 5          # fewer than this and a kind gets no summary charts, only feed rows


def _path_bands(paths: list[list[float | None]]) -> dict:
    """Median and middle half (25th–75th percentile) across events at each
    offset, with how many events had a price there."""
    x = np.array([[np.nan if v is None else v for v in p] for p in paths], dtype=float)
    n = np.sum(~np.isnan(x), axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)           # an all-empty offset is expected
        med, lo, hi = (np.nanpercentile(x, q, axis=0) for q in (50, 25, 75))
    def r(a):
        return [None if np.isnan(v) else round(float(v), 5) for v in a]
    return {"median": r(med), "p25": r(lo), "p75": r(hi), "n": [int(v) for v in n]}


def _kind_block(rows: list[dict], kind: str) -> dict | None:
    mine = [r for r in rows if r["kind"] == kind and r["status"] == "ok"]
    if len(mine) < MIN_EVENTS:
        return None
    worst = summary.kind_summary(rows, kind, "worst")
    grids = [r["tradability"] for r in mine if r["tradability"]]
    tables = {fill: tradability.summarise(grids, fill) for fill in tradability.FILLS}
    return summary.clean({
        "label": LABELS[kind],
        "n": worst["n"],
        "side": worst["side"],
        "counts": {s: sum(r["kind"] == kind and r["status"] == s for r in rows)
                   for s in ("ok", "no_pair", "no_price", "pending")},
        "abnormal": worst["abnormal"],
        "trade": {fill: {str(lat): {str(h): {"median": c.median, "mean": c.mean, "win": c.win, "n": c.n}
                                    for h, c in row.items()} for lat, row in table.items()}
                  for fill, table in tables.items()},
        "last_profitable": {fill: {str(h): tradability.last_profitable_delay(table, h) for h in tradability.HOLDS}
                            for fill, table in tables.items()},
        "path": _path_bands([r["path"] for r in mine if r["path"]]),
    })


def _event(r: dict) -> dict:
    a = r["abnormal"] or {}
    return summary.clean({
        "id": r["id"], "at": r["at"], "symbol": r["symbol"], "kind": r["kind"], "pair": r["pair"],
        "status": r["status"], "url": terms.notice_url(r["source"], r["source_id"]),
        "r10": a.get("10"), "r300": a.get("300"),
        "path": r["path"],
    })


def collect(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    rows = store.rows(conn)
    kinds = {k: b for k in ev.KINDS if (b := _kind_block(rows, k)) is not None}
    return {
        "generated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "method": store.METHOD,
        "offsets": list(study.PATH_OFFSETS),
        "horizons": list(study.HORIZONS),
        "latencies": list(tradability.LATENCIES),
        "holds": list(tradability.HOLDS),
        "fee": tradability.TAKER_FEE,
        "labels": LABELS,
        "kinds": kinds,
        "span": {"first": rows[0]["at"] if rows else None, "last": rows[-1]["at"] if rows else None},
        "totals": {"events": len(rows), "measured": sum(r["status"] == "ok" for r in rows),
                   "not_on_binance": sum(r["status"] in ("no_pair", "no_price") for r in rows),
                   "pending": sum(r["status"] == "pending" for r in rows)},
        "license": terms.DATA_LICENSE, "license_url": terms.DATA_LICENSE_URL, "attribution": terms.ATTRIBUTION,
        "events": [_event(r) for r in reversed(rows)],               # newest first
    }


def build(conn: sqlite3.Connection, out: Path, now: datetime | None = None) -> dict:
    """Write index.html, events.csv, data.json and LICENSE-DATA.txt into `out`."""
    data = collect(conn, now)
    out.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    page = resources.files(__package__).joinpath("site_template.html").read_text(encoding="utf-8")
    # "</" can't appear inside the <script> block that carries the data
    (out / "index.html").write_text(page.replace("__DATA__", blob.replace("</", "<\\/")), encoding="utf-8")
    (out / "data.json").write_text(blob, encoding="utf-8")
    with (out / "events.csv").open("w", newline="", encoding="utf-8") as fh:    # plain CSV: the licence is beside it
        csv.writer(fh).writerows(summary.csv_rows(store.rows(conn), public=True))
    (out / "LICENSE-DATA.txt").write_text(f"{terms.ATTRIBUTION}\n\nLicence: {terms.DATA_LICENSE_URL}\n",
                                         encoding="utf-8")
    return data
