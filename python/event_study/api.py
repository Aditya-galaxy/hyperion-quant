"""
HYPERION EVENTS: HTTP API
=========================
Read-only access to the event database, for non-commercial research.

    GET /v1/health                  no key needed
    GET /v1/meta                    kinds, horizons, fill delays; your plan and data cutoff
    GET /v1/events                  newest first; filter by kind, symbol, dates, status; CSV with format=csv
    GET /v1/events/{id}             one event with its full tradability grid
    GET /v1/stats?kind=listing      price reaction and "how fast would you have to be?"

Every request but /health carries a key, as `X-API-Key: hk_...` or
`Authorization: Bearer hk_...`. Keys are free; they exist so a heavy caller
can be slowed or cut off, and so every caller has been told the terms. The
plan on a key sets its request rate, and can hold back recent events (a
`delay`), which nothing uses today but a self-hosted licence may later.

Every response carries the data licence (see terms.py): CC BY-NC-SA 4.0,
credited to Binance Vision. Notice titles are Upbit's text and are not served;
each event links to its notice instead.

Run with `hyperion-events serve`. The rate limit is kept in memory, per
process: one process, or a shared store in front, before scaling out.
"""

# No `from __future__ import annotations` here: FastAPI reads these
# annotations at runtime, and the Who/Conn aliases live inside create_app,
# where string annotations can't be resolved.
import base64
import csv
import io
import sqlite3
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response

from . import events as ev
from . import store, study, summary, terms, tradability


@dataclass(frozen=True)
class Plan:
    delay: timedelta                # how old an event must be before this plan sees it
    per_minute: int                 # requests allowed per rolling minute


PLANS = {
    "research": Plan(delay=timedelta(0), per_minute=60),        # anyone who asks
    "collaborator": Plan(delay=timedelta(0), per_minute=600),   # bulk pulls, by arrangement
}

MAX_PAGE = 500
PUBLIC_FIELDS = ("id", "at", "source", "source_id", "symbol", "kind", "pair", "status")


class RateLimiter:
    """At most `limit` requests in any rolling 60 s, per key."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._seen: dict[int, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key_id: int, limit: int) -> tuple[bool, int, float]:
        """(allowed, remaining, seconds until a slot frees up)."""
        now = self._clock()
        with self._lock:
            seen = self._seen[key_id]
            while seen and seen[0] <= now - 60.0:
                seen.popleft()
            if len(seen) >= limit:
                return False, 0, 60.0 - (now - seen[0])
            seen.append(now)
            return True, limit - len(seen), 0.0


@dataclass
class Caller:
    key_id: int
    owner: str
    plan_name: str
    plan: Plan
    cutoff: datetime | None         # newest event time this caller may see (exclusive)


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _encode_cursor(row: dict) -> str:
    return base64.urlsafe_b64encode(f"{row['at']}|{row['id']}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, int]:
    try:
        at, event_id = base64.urlsafe_b64decode(cursor.encode()).decode().rsplit("|", 1)
        datetime.fromisoformat(at)
        return at, int(event_id)
    except ValueError:                         # bad base64, bad UTF-8, bad date or id
        raise HTTPException(400, "cursor is not one this API issued") from None


def _kinds(kind: list[str] | None) -> tuple[str, ...]:
    """`kind` may repeat or be comma-separated; none means every kind."""
    if not kind:
        return ev.KINDS
    wanted = tuple(k.strip() for part in kind for k in part.split(",") if k.strip())
    unknown = [k for k in wanted if k not in ev.KINDS]
    if unknown:
        raise HTTPException(422, f"unknown kind {', '.join(unknown)}; choose from {', '.join(ev.KINDS)}")
    return wanted


def _public(row: dict, full: bool) -> dict:
    out = {f: row[f] for f in PUBLIC_FIELDS}
    out["notice_url"] = terms.notice_url(row["source"], row["source_id"])
    out["abnormal"] = row["abnormal"]
    grid = row["tradability"]
    out["volume_10s"] = grid.get("volume_10s") if grid else None
    if full:
        out["tradability"] = grid
    return summary.clean(out)


def create_app(db_path: Path | str, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
               limiter: RateLimiter | None = None, plans: dict[str, Plan] = PLANS) -> FastAPI:
    store.connect(db_path).close()                      # create tables once, up front
    limiter = limiter or RateLimiter()
    app = FastAPI(title="Hyperion Events", version="1",
                  description="How crypto prices react to exchange notices, second by second. "
                              + terms.ATTRIBUTION)

    @app.middleware("http")
    async def licence_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Data-License"] = terms.DATA_LICENSE
        response.headers["Link"] = f'<{terms.DATA_LICENSE_URL}>; rel="license"'
        return response

    def db() -> Iterator:
        conn = store.connect(db_path, migrate=False)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(db)]

    def caller(response: Response, conn: Conn,
               x_api_key: Annotated[str | None, Header()] = None,
               authorization: Annotated[str | None, Header()] = None) -> Caller:
        key = x_api_key or (authorization[7:].strip() if authorization and authorization[:7].lower() == "bearer " else None)
        if not key:
            raise HTTPException(401, "send your key as X-API-Key or Authorization: Bearer",
                                headers={"WWW-Authenticate": "Bearer"})
        row = store.find_key(conn, key)
        if row is None or row["plan"] not in plans:
            raise HTTPException(401, "unknown or revoked key", headers={"WWW-Authenticate": "Bearer"})
        plan = plans[row["plan"]]
        allowed, remaining, retry = limiter.allow(row["id"], plan.per_minute)
        if not allowed:
            raise HTTPException(429, f"over {plan.per_minute} requests a minute",
                                headers={"Retry-After": str(max(1, int(retry + 0.999)))})
        response.headers["X-RateLimit-Limit"] = str(plan.per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return Caller(row["id"], row["owner"], row["plan"], plan,
                      now() - plan.delay if plan.delay else None)

    Who = Annotated[Caller, Depends(caller)]

    def capped(until: datetime | None, who: Caller) -> datetime | None:
        if who.cutoff is None:
            return until
        return who.cutoff if until is None else min(until, who.cutoff)

    @app.get("/v1/health")
    def health():
        return {"ok": True, "method": store.METHOD}

    @app.get("/v1/meta")
    def meta(who: Who):
        return {
            "kinds": {k: {"side": "long" if tradability.DIRECTION[k] > 0 else "short"} for k in ev.KINDS},
            "horizons_s": list(study.HORIZONS),
            "fill_delays_s": list(tradability.LATENCIES),
            "holds_s": list(tradability.HOLDS),
            "fills": list(tradability.FILLS),
            "taker_fee": tradability.TAKER_FEE,
            "method": store.METHOD,
            "plan": who.plan_name,
            "data_until": who.cutoff.isoformat() if who.cutoff else None,
            "license": terms.DATA_LICENSE,
            "license_url": terms.DATA_LICENSE_URL,
            "attribution": terms.ATTRIBUTION,
        }

    @app.get("/v1/events")
    def list_events(who: Who, conn: Conn,
                    kind: Annotated[list[str] | None, Query(description="repeat or comma-separate")] = None,
                    symbol: Annotated[str | None, Query(description="base asset, e.g. SOPH")] = None,
                    since: datetime | None = None, until: datetime | None = None,
                    status: Literal["ok", "no_pair", "no_price", "pending", "unmeasured"] | None = None,
                    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
                    cursor: str | None = None,
                    include_tradability: bool = False,
                    format: Literal["json", "csv"] = "json"):
        rows = store.page(conn, _kinds(kind), since=_utc(since), until=capped(_utc(until), who),
                          symbol=symbol.upper() if symbol else None, status=status, limit=limit,
                          before=_decode_cursor(cursor) if cursor else None)
        if format == "csv":
            buf = io.StringIO()
            csv.writer(buf).writerows(summary.csv_rows(rows, public=True))
            return Response(buf.getvalue(), media_type="text/csv; charset=utf-8")
        return {
            "data": [_public(r, include_tradability) for r in rows],
            "next_cursor": _encode_cursor(rows[-1]) if len(rows) == limit else None,
            "data_until": who.cutoff.isoformat() if who.cutoff else None,
        }

    @app.get("/v1/events/{event_id}")
    def get_event(event_id: int, who: Who, conn: Conn):
        row = store.get(conn, event_id)
        if row is None:
            raise HTTPException(404, "no such event")
        if who.cutoff and datetime.fromisoformat(row["at"]) >= who.cutoff:
            raise HTTPException(403, f"events this recent aren't included in the {who.plan_name} plan; "
                                     f"you'll see it from {(datetime.fromisoformat(row['at']) + who.plan.delay).date()}")
        return _public(row, full=True)

    @app.get("/v1/stats")
    def stats(who: Who, conn: Conn,
              kind: Annotated[str, Query(description="one kind, e.g. listing")],
              since: datetime | None = None, until: datetime | None = None,
              fill: Literal["worst", "fair"] = "worst",
              hold: Annotated[int, Query(description=f"one of {list(tradability.HOLDS)}")] = 300):
        kinds = _kinds([kind])
        if len(kinds) != 1:
            raise HTTPException(422, "stats take one kind at a time")
        only = kinds[0]
        if hold not in tradability.HOLDS:
            raise HTTPException(422, f"hold must be one of {list(tradability.HOLDS)}")
        rows = store.rows(conn, (only,), _utc(since), capped(_utc(until), who))
        out = summary.kind_summary(rows, only, fill, hold)
        out["counts"] = {s: sum(r["status"] == s for r in rows)
                         for s in ("ok", "no_pair", "no_price", "pending", "unmeasured")}
        out["data_until"] = who.cutoff.isoformat() if who.cutoff else None
        return out

    return app
