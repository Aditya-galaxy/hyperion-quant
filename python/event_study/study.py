"""
HYPERION QUANT: EVENT STUDY — WHAT THE PRICE DID AROUND EACH NOTICE
===================================================================
For every event, the asset's return on Binance over windows before and after
the notice's first publication, minus BTC's return over the same window (so a
market-wide move is not credited to the notice).

Negative horizons are the move *before* the notice. If most of the reaction is
already there at -60 s, the information was out before Upbit published it, and
no reader of Upbit notices — human or model — was early.

The labels here are what the market actually did. Nothing is simulated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .events import Event
from .prices import Series

HORIZONS = (-300, -60, -10, 10, 30, 60, 90, 300, 1800)
MARKET = "BTCUSDT"


def window_return(series: Series, t0: float, h: int) -> float | None:
    """h < 0: return over [t0+h, t0], the run-up before the notice.
    h > 0: return over [t0, t0+h], the reaction after it."""
    a, b = (t0 + h, t0) if h < 0 else (t0, t0 + h)
    pa, pb = series.price_at(a), series.price_at(b)
    if pa is None or pb is None or pa <= 0:
        return None
    return pb / pa - 1.0


def measure(event: Event, series: Series, market: Series | None,
            horizons: tuple[int, ...] = HORIZONS) -> dict[int, float] | None:
    """Market-adjusted returns at every horizon, or None.

    All or nothing: an event missing any horizon is dropped entirely, so every
    horizon is averaged over the same events. Otherwise the "price path" read
    across a table would partly be a change in which events had data.
    """
    t0 = event.at.timestamp()
    out: dict[int, float] = {}
    for h in horizons:
        r = window_return(series, t0, h)
        if r is None:
            return None
        if market is not None:
            m = window_return(market, t0, h)
            if m is None:
                return None
            r -= m
        out[h] = r
    return out


# Every second from a minute before to two minutes after, where the action
# is, then every 30 s out to half an hour: 237 points, enough to draw.
PATH_OFFSETS = tuple(range(-60, 121)) + tuple(range(150, 1801, 30))


def price_path(event: Event, series: Series,
               offsets: tuple[int, ...] = PATH_OFFSETS) -> list[float | None] | None:
    """The coin's own return from the last price before the notice, at each
    offset: what a chart of the event shows. None where nothing has traded
    for too long; None overall if there is no price before the notice."""
    t0 = event.at.timestamp()
    base = series.price_at(t0)
    if base is None or base <= 0:
        return None
    out: list[float | None] = []
    for dt in offsets:
        p = series.price_at(t0 + dt)
        out.append(None if p is None else round(p / base - 1.0, 6))
    return out


@dataclass
class Outcome:
    event: Event
    pair: str
    status: str                                   # ok | no_pair | no_price
    abnormal: dict[int, float] = field(default_factory=dict)


@dataclass
class Stat:
    n: int
    mean: float
    median: float
    t: float
    hit: float                                    # share of events with a positive move
    ci_low: float
    ci_high: float


def summarise(values: list[float], rng: np.random.Generator, resamples: int = 2000) -> Stat:
    """Mean with a t-statistic and a bootstrap 95% interval. Returns are fat-
    tailed, so the interval matters more than the t: one listing that tripled
    can carry a mean on its own, and the bootstrap shows that the t hides it."""
    x = np.asarray(values, dtype=np.float64)
    n = len(x)
    if n == 0:
        nan = float("nan")
        return Stat(0, nan, nan, nan, nan, nan, nan)
    sd = float(x.std(ddof=1)) if n > 1 else float("nan")
    t = float(x.mean() / (sd / math.sqrt(n))) if n > 1 and sd > 0 else float("nan")
    boots = rng.choice(x, size=(resamples, n), replace=True).mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return Stat(n=n, mean=float(x.mean()), median=float(np.median(x)), t=t,
                hit=float((x > 0).mean()), ci_low=float(lo), ci_high=float(hi))


def by_kind(outcomes: list[Outcome], horizons: tuple[int, ...] = HORIZONS,
            seed: int = 7) -> dict[str, dict[int, Stat]]:
    rng = np.random.default_rng(seed)
    kinds = sorted({o.event.kind for o in outcomes if o.status == "ok"})
    return {kind: {h: summarise([o.abnormal[h] for o in outcomes
                                 if o.status == "ok" and o.event.kind == kind], rng)
                   for h in horizons}
            for kind in kinds}


def score_lexicon(outcomes: list[Outcome], evaluate, horizon: int,
                  threshold: float = 0.1, entry: int = 0) -> dict:
    """How the keyword engine's calls compare with what the price did.

    `evaluate(title) -> impact in [-1, 1]`. A call is made when |impact| is
    above `threshold`. Reported next to the base rate — the hit rate of simply
    calling every event "up" — because a hit rate means nothing without it.

    `entry` scores the call against the move *after* that many seconds rather
    than from the notice itself. Knowing the direction is worth little if the
    move is over before anyone can act: with entry=10, a call is right only if
    the price kept going its way from +10 s to `horizon`.
    """
    if entry and not (0 < entry < horizon):
        raise ValueError("entry must be between 0 and the horizon")
    rows = []
    for o in outcomes:
        if o.status != "ok":
            continue
        impact = evaluate(o.event.title)
        call = 0 if abs(impact) <= threshold else (1 if impact > 0 else -1)
        moved = o.abnormal[horizon]
        if entry:
            moved = (1 + moved) / (1 + o.abnormal[entry]) - 1
        rows.append((o.event.kind, call, 1 if moved > 0 else -1 if moved < 0 else 0))

    called = [r for r in rows if r[1] != 0]
    per_kind: dict[str, dict[str, int]] = {}
    for kind, call, moved in rows:
        counts = per_kind.setdefault(kind, {"up": 0, "down": 0, "none": 0, "right": 0})
        counts["up" if call > 0 else "down" if call < 0 else "none"] += 1
        counts["right"] += int(call != 0 and call == moved)
    return {
        "horizon_s": horizon,
        "entry_s": entry,
        "events": len(rows),
        "calls": len(called),
        "coverage": len(called) / len(rows) if rows else float("nan"),
        "hit_rate": (sum(1 for _, c, m in called if c == m) / len(called)) if called else float("nan"),
        "base_rate_up": (sum(1 for *_, m in rows if m > 0) / len(rows)) if rows else float("nan"),
        "calls_by_kind": per_kind,
    }
