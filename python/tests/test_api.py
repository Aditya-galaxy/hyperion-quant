"""
Tests for the HTTP API: keys, plans, rate limits, paging and the shapes
customers will build on. A small database is written by hand; nothing
touches the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
from event_study import api, store, study
from fastapi.testclient import TestClient

NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def _grid(value: float) -> dict:
    return {"side": 1, "fee": 0.001, "volume_10s": 5000.0,
            "grid": {"1": {"60": {"worst": value, "fair": value + 0.01}, "300": {"worst": value, "fair": value}}}}


def add_event(conn, symbol: str, kind: str, days_ago: float, status: str = "ok", move: float = 0.1) -> int:
    at = (NOW - timedelta(days=days_ago)).isoformat()
    cur = conn.execute("INSERT INTO events (source, source_id, symbol, kind, at, title) VALUES (?, ?, ?, ?, ?, ?)",
                       ("upbit", f"{symbol}-{days_ago}", symbol, kind, at, f"{symbol} notice"))
    if status == "ok":
        store.save_measurement(conn, cur.lastrowid, f"{symbol}USDT", "ok",
                               {h: move for h in study.HORIZONS}, _grid(move - 0.05))
    elif status != "unmeasured":
        store.save_measurement(conn, cur.lastrowid, f"{symbol}USDT", status)
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "h.db"
    conn = store.connect(path)
    ids = {
        "old_listing": add_event(conn, "AAA", "listing", 90, move=0.20),
        "old_listing2": add_event(conn, "BBB", "listing", 60, move=0.10),
        "old_delisting": add_event(conn, "CCC", "delisting", 45, move=-0.05),
        "gone": add_event(conn, "DDD", "listing", 40, status="no_pair"),
        "recent_listing": add_event(conn, "EEE", "listing", 5, move=0.30),
    }
    keys = {"pro": store.create_key(conn, "lab@uni.edu", "collaborator"),
            "research": store.create_key(conn, "student@uni.edu", "research"),
            "delayed": store.create_key(conn, "later@licence.com", "delayed")}
    conn.close()
    clock = [0.0]
    plans = {**api.PLANS, "delayed": api.Plan(delay=timedelta(days=30), per_minute=60)}
    client = TestClient(api.create_app(path, now=lambda: NOW, limiter=api.RateLimiter(lambda: clock[0]),
                                       plans=plans))
    return client, keys, ids, path, clock


def get(client, key, url, **params):
    return client.get(url, params=params, headers={"X-API-Key": key} if key else {})


# ── keys ─────────────────────────────────────────────────────────────────────

def test_health_needs_no_key(setup):
    client, *_ = setup
    assert client.get("/v1/health").json()["ok"] is True


def test_no_key_bad_key_and_bearer(setup):
    client, keys, *_ = setup
    assert get(client, None, "/v1/meta").status_code == 401
    assert get(client, "hk_nope", "/v1/meta").status_code == 401
    ok = client.get("/v1/meta", headers={"Authorization": f"Bearer {keys['pro']}"})
    assert ok.status_code == 200 and ok.json()["plan"] == "collaborator"


def test_revoked_key_is_refused(setup):
    client, keys, _, path, _ = setup
    conn = store.connect(path)
    key_id = next(k["id"] for k in store.list_keys(conn) if k["owner"] == "lab@uni.edu")
    assert store.revoke_key(conn, key_id)
    assert not store.revoke_key(conn, key_id)
    conn.close()
    assert get(client, keys["pro"], "/v1/meta").status_code == 401


def test_keys_are_stored_hashed(setup):
    _, keys, _, path, _ = setup
    raw = b"".join(f.read_bytes() for f in path.parent.glob(path.name + "*"))   # the -wal file too
    assert path.with_name(path.name + "-wal").exists() or len(raw) > 0
    assert keys["pro"].encode() not in raw and keys["research"].encode() not in raw
    assert store._hash(keys["pro"]).encode() in raw


# ── plans ────────────────────────────────────────────────────────────────────
# The free plans see everything. A plan with a delay (kept for a later
# self-hosted licence) is tested with a plan made up for the test.

def test_research_plan_sees_everything(setup):
    client, keys, ids, *_ = setup
    got = get(client, keys["research"], "/v1/events").json()
    assert ids["recent_listing"] in [e["id"] for e in got["data"]]
    assert got["data_until"] is None


def test_delayed_plan_sees_nothing_newer_than_its_delay(setup):
    client, keys, ids, *_ = setup
    got = get(client, keys["delayed"], "/v1/events").json()
    assert ids["recent_listing"] not in [e["id"] for e in got["data"]]
    assert got["data_until"] == (NOW - timedelta(days=30)).isoformat()


def test_delayed_plan_cannot_widen_its_window(setup):
    client, keys, ids, *_ = setup
    got = get(client, keys["delayed"], "/v1/events", until="2030-01-01T00:00:00").json()
    assert ids["recent_listing"] not in [e["id"] for e in got["data"]]


def test_recent_event_detail_is_gated_with_a_date(setup):
    client, keys, ids, *_ = setup
    r = get(client, keys["delayed"], f"/v1/events/{ids['recent_listing']}")
    assert r.status_code == 403 and "2026-10-21" in r.json()["detail"]
    assert get(client, keys["research"], f"/v1/events/{ids['recent_listing']}").status_code == 200


def test_stats_follow_the_plan(setup):
    client, keys, *_ = setup
    assert get(client, keys["delayed"], "/v1/stats", kind="listing").json()["n"] == 2
    assert get(client, keys["research"], "/v1/stats", kind="listing").json()["n"] == 3


# ── terms ────────────────────────────────────────────────────────────────────

def test_every_response_carries_the_data_licence(setup):
    client, keys, *_ = setup
    for r in (client.get("/v1/health"), get(client, keys["research"], "/v1/events"),
              get(client, None, "/v1/meta")):                      # even a refusal
        assert r.headers["X-Data-License"] == "CC-BY-NC-SA-4.0"
        assert "creativecommons.org/licenses/by-nc-sa/4.0" in r.headers["Link"]
    meta = get(client, keys["research"], "/v1/meta").json()
    assert "Binance Vision" in meta["attribution"] and meta["license"] == "CC-BY-NC-SA-4.0"


def test_notice_titles_are_not_served_but_linked(setup):
    client, keys, ids, *_ = setup
    listed = get(client, keys["research"], "/v1/events").json()["data"][0]
    one = get(client, keys["research"], f"/v1/events/{ids['old_listing']}").json()
    csv_text = get(client, keys["research"], "/v1/events", format="csv").text
    for e in (listed, one):
        assert "title" not in e and e["notice_url"].startswith("https://upbit.com/service_center/notice?id=")
    assert "notice" not in csv_text.split("\n", 1)[1].replace("notice?id", "")   # no title text in rows
    assert csv_text.startswith("id,at,source,source_id,symbol,kind,notice_url,")


# ── rate limit ───────────────────────────────────────────────────────────────

def test_rate_limit_then_recovery(setup):
    client, keys, _, _, clock = setup
    for _ in range(api.PLANS["research"].per_minute):
        assert get(client, keys["research"], "/v1/meta").status_code == 200
    blocked = get(client, keys["research"], "/v1/meta")
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
    assert get(client, keys["pro"], "/v1/meta").status_code == 200     # limits are per key
    clock[0] += 61
    assert get(client, keys["research"], "/v1/meta").status_code == 200


# ── events ───────────────────────────────────────────────────────────────────

def test_events_are_newest_first_and_page_without_repeats(setup):
    client, keys, ids, *_ = setup
    seen, cursor = [], None
    while True:
        page = get(client, keys["pro"], "/v1/events", limit=2, **({"cursor": cursor} if cursor else {})).json()
        seen += [e["id"] for e in page["data"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [ids[k] for k in ("recent_listing", "gone", "old_delisting", "old_listing2", "old_listing")]


def test_filters(setup):
    client, keys, ids, *_ = setup
    by_kind = get(client, keys["pro"], "/v1/events", kind="delisting").json()["data"]
    assert [e["id"] for e in by_kind] == [ids["old_delisting"]]
    by_symbol = get(client, keys["pro"], "/v1/events", symbol="aaa").json()["data"]
    assert [e["symbol"] for e in by_symbol] == ["AAA"]
    missing = get(client, keys["pro"], "/v1/events", status="no_pair").json()["data"]
    assert [e["id"] for e in missing] == [ids["gone"]]
    both = get(client, keys["pro"], "/v1/events", kind="listing,delisting").json()["data"]
    assert len(both) == 5


def test_bad_inputs_are_422_or_400(setup):
    client, keys, *_ = setup
    assert get(client, keys["pro"], "/v1/events", kind="moonshot").status_code == 422
    assert get(client, keys["pro"], "/v1/events", limit=10_000).status_code == 422
    assert get(client, keys["pro"], "/v1/events", cursor="garbage").status_code == 400
    assert get(client, keys["pro"], "/v1/stats", kind="listing,delisting").status_code == 422
    assert get(client, keys["pro"], "/v1/stats", kind="listing", hold=7).status_code == 422


def test_list_omits_the_grid_unless_asked(setup):
    client, keys, *_ = setup
    plain = get(client, keys["pro"], "/v1/events", kind="listing").json()["data"][0]
    full = get(client, keys["pro"], "/v1/events", kind="listing", include_tradability=True).json()["data"][0]
    assert "tradability" not in plain and plain["volume_10s"] == 5000.0
    assert full["tradability"]["grid"]["1"]["60"]["worst"] == pytest.approx(0.25)


def test_detail_and_404(setup):
    client, keys, ids, *_ = setup
    got = get(client, keys["pro"], f"/v1/events/{ids['old_listing']}").json()
    assert got["symbol"] == "AAA" and got["abnormal"]["60"] == pytest.approx(0.20)
    assert got["tradability"]["grid"]["1"]["300"]["worst"] == pytest.approx(0.15)
    assert get(client, keys["pro"], "/v1/events/99999").status_code == 404


def test_csv_export(setup):
    client, keys, *_ = setup
    r = get(client, keys["pro"], "/v1/events", kind="listing", format="csv")
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,at,source") and len(lines) == 1 + 4


# ── stats ────────────────────────────────────────────────────────────────────

def test_stats_shape_and_no_nan(setup):
    client, keys, *_ = setup
    s = get(client, keys["pro"], "/v1/stats", kind="listing", hold=60).json()
    assert s["side"] == "long" and s["n"] == 3
    assert s["counts"] == {"ok": 3, "no_pair": 1, "no_price": 0, "pending": 0, "unmeasured": 0}
    assert s["abnormal"]["60"]["median"] == pytest.approx(0.20)
    assert s["tradability"]["table"]["1"]["60"]["median"] == pytest.approx(0.15)
    assert s["tradability"]["last_profitable_delay_s"] == 1


def test_stats_for_a_kind_with_no_events_is_nulls_not_an_error(setup):
    client, keys, *_ = setup
    s = get(client, keys["pro"], "/v1/stats", kind="warning")
    assert s.status_code == 200 and s.json()["n"] == 0 and s.json()["abnormal"]["60"]["mean"] is None
