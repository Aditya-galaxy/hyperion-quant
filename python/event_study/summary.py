"""
HYPERION EVENTS: ONE KIND OF EVENT, SUMMARISED
==============================================
The numbers the CLI report prints and the API serves, computed in one place
so the two never disagree. Plain JSON-ready dicts: no NaN (JSON has none),
just null where there is nothing to average.
"""

from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np

from . import study, tradability


def clean(x):
    """NaN → None, recursively."""
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return x


def kind_summary(rows: list[dict], kind: str, fill: str = "worst", hold: int = 300, seed: int = 7) -> dict:
    """Price reaction and tradability for the measured events of one kind.

    `rows` are store rows; anything not of `kind` or not measured is ignored.
    """
    mine = [r for r in rows if r["kind"] == kind and r["status"] == "ok"]
    rng = np.random.default_rng(seed)
    abnormal = {str(h): asdict(study.summarise([r["abnormal"][str(h)] for r in mine], rng))
                for h in study.HORIZONS}
    grids = [r["tradability"] for r in mine if r["tradability"]]
    table = tradability.summarise(grids, fill)
    return clean({
        "kind": kind,
        "n": len(mine),
        "side": "long" if tradability.DIRECTION.get(kind, 1) > 0 else "short",
        "abnormal": abnormal,
        "tradability": {
            "fill": fill,
            "hold_s": hold,
            "n": len(grids),
            "table": {str(lat): {str(h): asdict(c) for h, c in row.items()} for lat, row in table.items()},
            "last_profitable_delay_s": tradability.last_profitable_delay(table, hold),
        },
    })


CSV_FIELDS = ["id", "at", "source", "source_id", "symbol", "kind", "title", "pair", "status"]


def csv_rows(rows: list[dict]):
    """Header, then one flat row per event: identity, status, and the abnormal
    return at every horizon (blank when unmeasured)."""
    horizons = [str(h) for h in study.HORIZONS]
    yield CSV_FIELDS + [f"abnormal_{h}s" for h in horizons]
    for r in rows:
        yield [r[f] for f in CSV_FIELDS] + [(r["abnormal"] or {}).get(h, "") for h in horizons]
