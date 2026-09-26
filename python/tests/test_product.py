"""
Tests for the product layer: tradability, the event database and ingest.

Prices are small hand-built series where the right answer can be worked out
by hand. Notices are the real ones in fixtures/upbit_notices.json. No test
touches the network.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from event_study import events as ev
from event_study import pipeline, prices, store, study, tradability

NOTICES = json.loads((Path(__file__).parent / "fixtures" / "upbit_notices.json").read_text(encoding="utf-8"))
T0 = 1_000_000.0


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def event(kind: str = "listing") -> ev.Event:
    return ev.Event(at=at(T0), symbol="X", kind=kind, title="t", language="ko", source="test", source_id="1")


def spike(before: float, peak: float, after: float, rise_at: float = T0 + 1) -> prices.Series:
    """Flat at `before`; in the second starting at `rise_at` it trades up to
    `peak` and closes at `after`, where it stays."""
    times = T0 - 400 + np.arange(2400, dtype=float)
    close = np.where(times < rise_at, before, after)
    high = close.copy()
    high[times == rise_at] = peak
    low = np.minimum(close, np.where(times < rise_at, before, before))
    return prices.Series(open_s=times, close=close, high=high, low=low,
                         quote_volume=np.where(times >= T0, 1000.0, 1.0))


# ── tradability ──────────────────────────────────────────────────────────────

def test_fill_delay_decides_the_trade():
    s = spike(100.0, 130.0, 120.0)                        # jumps in the second after the notice
    got = tradability.measure(event(), s, fee=0.0)["grid"]
    assert got["0"]["60"]["fair"] == pytest.approx(0.20)  # the whole move: unreachable bound
    assert got["1"]["60"]["worst"] == pytest.approx(120 / 130 - 1)   # bought the top of the spike
    assert got["2"]["60"]["fair"] == pytest.approx(0.0)   # move already over


def test_fees_come_off_both_sides():
    s = spike(100.0, 100.0, 100.0)
    got = tradability.measure(event(), s, fee=0.001)["grid"]
    assert got["5"]["300"]["fair"] == pytest.approx(-0.002)


def test_short_side_for_delistings():
    s = spike(100.0, 100.0, 90.0)                         # falls 10%
    got = tradability.measure(event("delisting"), s, fee=0.0)
    assert got["side"] == -1
    assert got["grid"]["0"]["60"]["fair"] == pytest.approx(0.10)


def test_no_bar_in_the_fill_second_falls_back_to_last_price():
    times = T0 - 400 + np.arange(0, 2400, 5, dtype=float)       # a trade only every 5 s
    s = prices.Series(open_s=times, close=np.full(times.size, 50.0), high=np.full(times.size, 99.0))
    got = tradability.measure(event(), s, fee=0.0)["grid"]
    assert s.bar_covering(T0 + 1) is None
    assert got["1"]["60"]["worst"] == pytest.approx(0.0)        # not the 99 high of a bar that isn't there


def test_holds_no_shorter_than_the_delay_are_skipped():
    got = tradability.measure(event(), spike(1, 1, 1), latencies=(0, 60), holds=(30, 60, 300))["grid"]
    assert set(got["60"]) == {"300"}


def test_volume_in_the_first_ten_seconds():
    assert tradability.measure(event(), spike(1, 1, 1))["volume_10s"] == pytest.approx(10_000.0)


def test_kinds_without_a_direction_are_not_measured():
    assert tradability.measure(event("other"), spike(1, 1, 1)) is None


def test_last_profitable_delay_stops_at_the_first_loss():
    def cell(median):
        return {300: tradability.Cell(10, median, median, 0.5)}
    table = {0: cell(0.2), 1: cell(0.05), 2: cell(-0.01), 3: cell(0.03)}   # +3 s after a loss: noise
    assert tradability.last_profitable_delay(table, 300) == 1
    assert tradability.last_profitable_delay({0: cell(-0.1)}, 300) is None


# ── the price archive ────────────────────────────────────────────────────────

def test_archive_is_settled_only_after_the_lag():
    now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    assert prices.archive_settled(date(2026, 9, 20), now)
    assert not prices.archive_settled(date(2026, 9, 25), now)


def test_a_recent_missing_day_is_asked_again(tmp_path, monkeypatch):
    """The bug this guards: a day not yet published was cached as missing, and
    every recent event became 'not on Binance' forever."""
    calls = []
    monkeypatch.setattr(prices, "_get", lambda url: calls.append(url) or None)
    today = datetime.now(timezone.utc).date().isoformat()
    assert prices.fetch_day("NEWUSDT", today, tmp_path) is None
    assert prices.fetch_day("NEWUSDT", today, tmp_path) is None
    assert len(calls) == 2
    assert not list(tmp_path.glob("*.missing"))


# ── the database and ingest ──────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = store.connect(":memory:")
    yield c
    c.close()


def test_notices_are_stored_once(conn):
    assert store.add_notices(conn, "upbit", NOTICES) == len(NOTICES)
    assert store.add_notices(conn, "upbit", NOTICES) == 0
    assert len(store.known_ids(conn, "upbit")) == len(NOTICES)


def test_events_are_derived_one_per_ticker(conn):
    store.add_notices(conn, "upbit", NOTICES)
    n = store.derive_events(conn)
    assert n == sum(len(ev.from_notice(x)) for x in NOTICES)
    assert store.derive_events(conn) == 0                        # idempotent


def test_reclassified_event_loses_its_measurement(conn, monkeypatch):
    store.add_notices(conn, "upbit", NOTICES)
    store.derive_events(conn)
    row = conn.execute("SELECT id FROM events WHERE kind = 'listing' LIMIT 1").fetchone()
    store.save_measurement(conn, row["id"], "XUSDT", "ok", {60: 0.1})
    monkeypatch.setattr(ev, "classify", lambda title: "warning")
    store.derive_events(conn)
    assert conn.execute("SELECT kind FROM events WHERE id = ?", (row["id"],)).fetchone()["kind"] == "warning"
    assert conn.execute("SELECT 1 FROM measurements WHERE event_id = ?", (row["id"],)).fetchone() is None


def _one_listing(conn) -> ev.Event:
    listing = next(n for n in NOTICES if ev.classify(n["title"]) == "listing" and len(ev.symbols(n["title"])) == 1)
    store.add_notices(conn, "upbit", [listing])
    return ev.from_notice(listing)[0]


def fake_loader(pair, start, end, cache):
    """Every pair is flat at 100 and jumps to 120 one second after the notice;
    BTC is flat. 'GONEUSDT' doesn't exist."""
    t0 = start.timestamp() + pipeline.LEAD
    if pair == "GONEUSDT":
        return None
    level = 100.0 if pair == study.MARKET else 120.0
    s = spike(100.0, level, level)                         # built around T0, moved to t0
    return prices.Series(open_s=s.open_s - T0 + t0, close=s.close, high=s.high, low=s.low,
                         quote_volume=s.quote_volume)


def test_ingest_measures_what_is_ready(conn, tmp_path):
    e = _one_listing(conn)
    report = pipeline.ingest(conn, tmp_path, datetime(2000, 1, 1, tzinfo=timezone.utc), fetch=None,
                             load=fake_loader, now=e.at + timedelta(days=5), pause=0)
    assert (report.measured, report.pending) == (1, 0)
    (row,) = store.rows(conn, ("listing",))
    assert row["status"] == "ok"
    assert row["abnormal"]["60"] == pytest.approx(0.20)
    assert row["tradability"]["grid"]["2"]["60"]["fair"] == pytest.approx(-0.002)
    # nothing left to do on the next run
    assert pipeline.ingest(conn, tmp_path, datetime(2000, 1, 1, tzinfo=timezone.utc), fetch=None,
                           load=fake_loader, now=e.at + timedelta(days=5), pause=0).measured == 0


def test_recent_events_stay_pending_until_the_archive_is_out(conn, tmp_path):
    e = _one_listing(conn)
    loaded = []
    early = pipeline.ingest(conn, tmp_path, datetime(2000, 1, 1, tzinfo=timezone.utc), fetch=None,
                            load=lambda *a: loaded.append(a) or None, now=e.at + timedelta(hours=3), pause=0)
    assert early.pending == 1 and not loaded                      # didn't even try the archive
    later = pipeline.ingest(conn, tmp_path, datetime(2000, 1, 1, tzinfo=timezone.utc), fetch=None,
                            load=fake_loader, now=e.at + timedelta(days=5), pause=0)
    assert later.measured == 1


def test_fetch_only_asks_for_what_is_new(conn, tmp_path):
    seen = []
    def fetch(known, since):
        seen.append(set(known))
        return NOTICES[:3]
    pipeline.ingest(conn, tmp_path, datetime(2000, 1, 1, tzinfo=timezone.utc), fetch=fetch,
                    load=lambda *a: None, now=datetime(2000, 1, 1, tzinfo=timezone.utc), pause=0)
    pipeline.ingest(conn, tmp_path, datetime(2000, 1, 1, tzinfo=timezone.utc), fetch=fetch,
                    load=lambda *a: None, now=datetime(2000, 1, 1, tzinfo=timezone.utc), pause=0)
    assert seen[0] == set() and seen[1] == {str(n["id"]) for n in NOTICES[:3]}


def test_fetch_new_stops_at_the_first_page_with_nothing_new(monkeypatch):
    pages = [NOTICES[:20], NOTICES[20:40], NOTICES[40:60]]
    asked = []
    def fake_pages():
        for p in pages:
            asked.append(1)
            yield p
    monkeypatch.setattr(ev, "pages", fake_pages)
    known = {str(n["id"]) for n in NOTICES[5:]}
    got = ev.fetch_new(known, datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert [n["id"] for n in got] == [n["id"] for n in NOTICES[:5]]
    assert len(asked) == 2
