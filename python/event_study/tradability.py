"""
HYPERION QUANT: TRADABILITY — WHAT WAS LEFT FOR SOMEONE WHO ACTED ON IT
=======================================================================
The event study says how far the price moved. This says how much of that
move a trader could have kept, given how many seconds after the notice their
order was filled, after fees.

For each event and each fill delay L, the trade goes in the direction the
notice implies (long a listing, short a delisting) and is closed at several
holding horizons. Two fills are recorded, because a one-second bar cannot say
where inside it an order landed:

  * worst: the bar's extreme against the trade (its high for a buy, its low
    for a sell) in the second the order arrives. A market order into a spike
    fills near here, or worse if the book is thin.
  * fair: the last trade price known when the order arrives. Optimistic in a
    fast market.

At L = 0 the fair fill is the price before the notice: an upper bound no one
reaches, kept to show the size of the whole move. Every return is raw (the
coin itself is traded, not hedged against BTC) and net of taker fees both ways.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .events import Event
from .prices import Series

LATENCIES = (0, 1, 2, 3, 5, 10)     # seconds from the notice to the fill
HOLDS = (30, 60, 300, 1800)         # seconds from the notice to the exit
TAKER_FEE = 0.001                   # Binance spot, per side, before discounts
FILLS = ("worst", "fair")

# The side a trader would take on each kind of notice. Shorting needs margin
# or a perpetual, which many of these coins do not have; the number is what a
# short would have made, not a claim that one was available.
DIRECTION = {"listing": 1, "caution_off": 1,
             "delisting": -1, "caution_on": -1, "caution_extended": -1, "warning": -1}


def measure(event: Event, series: Series, fee: float = TAKER_FEE,
            latencies: tuple[int, ...] = LATENCIES,
            holds: tuple[int, ...] = HOLDS) -> dict | None:
    """Net return of the trade for every (fill delay, hold, fill), or None if
    the kind has no direction or a price is missing anywhere in the grid."""
    side = DIRECTION.get(event.kind)
    if side is None:
        return None
    t0 = event.at.timestamp()
    extreme = series.high if side > 0 else series.low
    grid: dict[str, dict[str, dict[str, float]]] = {}
    for lat in latencies:
        fair = series.price_at(t0 + lat)
        if fair is None or fair <= 0:
            return None
        bar = series.bar_covering(t0 + lat)
        worst = float(extreme[bar]) if extreme is not None and bar is not None else fair
        row: dict[str, dict[str, float]] = {}
        for hold in holds:
            if hold <= lat:
                continue
            exit_price = series.price_at(t0 + hold)
            if exit_price is None:
                return None
            row[str(hold)] = {name: side * (exit_price / entry - 1.0) - 2.0 * fee
                              for name, entry in (("worst", worst), ("fair", fair))}
        grid[str(lat)] = row
    return {"side": side, "fee": fee, "grid": grid, "volume_10s": _volume(series, t0, 10)}


def _volume(series: Series, t0: float, seconds: int) -> float | None:
    """Value traded in the first `seconds` after the notice: how much size the
    move could have absorbed at all."""
    if series.quote_volume is None:
        return None
    mask = (series.open_s >= t0) & (series.open_s < t0 + seconds)
    return float(series.quote_volume[mask].sum())


@dataclass
class Cell:
    n: int
    mean: float
    median: float
    win: float                      # share of trades that made money


def summarise(results: list[dict], fill: str = "worst") -> dict[int, dict[int, Cell]]:
    """Mean, median and win rate for each (fill delay, hold) across events.
    The median is the number to read: a few listings that doubled carry the
    mean, and a trader lives on the typical event."""
    table: dict[int, dict[int, Cell]] = {}
    for lat in LATENCIES:
        for hold in HOLDS:
            x = np.array([r["grid"][str(lat)][str(hold)][fill] for r in results
                          if str(hold) in r["grid"].get(str(lat), {})])
            if not x.size:
                continue
            table.setdefault(lat, {})[hold] = Cell(int(x.size), float(x.mean()),
                                                   float(np.median(x)), float((x > 0).mean()))
    return table


def last_profitable_delay(table: dict[int, dict[int, Cell]], hold: int) -> int | None:
    """The slowest fill delay at which the typical (median) trade still made
    money, holding to `hold`. None if even the fastest fill lost. The headline
    answer to "how fast would I have to be?"."""
    ok = [lat for lat, row in table.items()
          if hold in row and not math.isnan(row[hold].median) and row[hold].median > 0]
    if not ok:
        return None
    best = None
    for lat in sorted(table):             # contiguous from the fastest: a lucky +10 s after a losing +5 s is noise
        if lat not in ok:
            break
        best = lat
    return best
