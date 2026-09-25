"""
Tests for the news event study.

Notice parsing is tested against 80 real Upbit notices saved in
fixtures/upbit_notices.json. The price arithmetic is tested on small
hand-built series, where the right answer can be worked out by hand; that is
what synthetic data is for. No test touches the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from event_study import events as ev
from event_study import prices, study

FIXTURE = Path(__file__).parent / "fixtures" / "upbit_notices.json"
NOTICES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def notice(fragment: str) -> dict:
    return next(n for n in NOTICES if fragment in n["title"])


# ── notices ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fragment, kind", [
    ("클러스터프로토콜(CP) 신규 거래지원", "listing"),
    ("바이프로스트(BFC) KRW, USDT 마켓 디지털 자산 추가", "listing"),
    ("KRW 마켓 디지털 자산 추가 (BLUR)", "listing"),
    ("아이콘(ICX) 거래지원 종료", "delisting"),
    ("소폰(SOPH) 거래 유의 종목 지정 안내", "caution_on"),
    ("리졸브(RESOLV) 거래 유의 종목 지정 안내", "caution_on"),
    ("베이직(BASIC) 유의 종목 지정 안내", "caution_on"),            # 2023 wording, no 거래
    ("만트라(MANTRA) 거래 유의 종목 지정 기간 연장", "caution_extended"),
    ("인젝티브(INJ) 거래 유의 종목 지정 해제", "caution_off"),     # contains the words for "designated" too
    ("샌드박스(SAND) 유의 촉구 안내", "warning"),
    ("헤미(HEMI), 유즈리스(USELESS) 신규 거래지원", "other"),     # carries a cancellation
])
def test_classify_real_titles(fragment, kind):
    assert ev.classify(notice(fragment)["title"]) == kind


def test_every_fixture_notice_gets_a_known_kind():
    kinds = {ev.classify(n["title"]) for n in NOTICES}
    assert kinds <= set(ev.KINDS) | {"other"}
    assert sum(ev.classify(n["title"]) == "other" for n in NOTICES) == 1


@pytest.mark.parametrize("title, expected", [
    ("소폰(SOPH) 거래 유의 종목 지정 안내", ["SOPH"]),
    ("이더리움클래식(ETC/KRW) 상장", ["ETC"]),
    ("KRW, BTC 마켓 디지털 자산 추가 (MASK, ACS)", ["MASK", "ACS"]),
    ("페이팔유에스디(PYUSD), 제이피와이코인(JPYC) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", ["PYUSD", "JPYC"]),
    ("아이콘(ICX) 거래지원 종료 안내 (10/19 15:00)", ["ICX"]),       # a date is not a ticker
    ("ETH 및 ERC 계열 디지털 자산 투자 유의 촉구 안내", []),           # nothing in brackets
])
def test_symbols(title, expected):
    assert ev.symbols(title) == expected


def test_market_lists_are_not_tickers():
    assert ev.symbols("(KRW, BTC, USDT 마켓)") == []


def test_amended_notice_is_timed_from_first_publication():
    """REZ was announced at 13:30:01 KST and re-stamped at 17:45:01 when amended.
    The event is the announcement."""
    rez = notice("렌조(REZ)")
    assert rez["listed_at"] != rez["first_listed_at"]
    (event,) = ev.from_notice(rez)
    assert event.at == datetime(2026, 9, 10, 4, 30, 1, tzinfo=timezone.utc)


def test_one_event_per_ticker():
    found = ev.from_notice(notice("(BICO, BMT, NIL, GWEI)"))
    assert [e.symbol for e in found] == ["BICO", "BMT", "NIL", "GWEI"]
    assert {e.kind for e in found} == {"listing"}


def test_load_events_filters_and_sorts():
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    found = ev.load_events(NOTICES, since, kinds=("delisting",))
    assert found and all(e.kind == "delisting" and e.at >= since for e in found)
    assert [e.at for e in found] == sorted(e.at for e in found)


# ── prices ───────────────────────────────────────────────────────────────────

def test_timestamps_in_milliseconds_and_microseconds_agree():
    ms = np.array([1_704_067_200_000.0])          # 2024-01-01T00:00:00Z
    us = np.array([1_735_689_600_000_000.0])      # 2025-01-01T00:00:00Z
    assert prices.to_seconds(ms)[0] == 1_704_067_200.0
    assert prices.to_seconds(us)[0] == 1_735_689_600.0


def series(start: float, closes: list[float]) -> prices.Series:
    return prices.Series(open_s=start + np.arange(len(closes), dtype=float),
                         close=np.array(closes, dtype=float))


def test_price_at_never_reads_an_unfinished_bar():
    s = series(100.0, [10.0, 11.0, 12.0])          # bars open at 100, 101, 102
    assert s.price_at(99.5) is None                 # before anything finished
    assert s.price_at(100.5) is None                # first bar still open
    assert s.price_at(101.0) == 10.0                # first bar just finished
    assert s.price_at(102.4) == 11.0                # third bar open, second finished


def test_price_at_refuses_a_stale_price():
    s = series(100.0, [10.0])
    assert s.price_at(101.0 + 120.0) == 10.0
    assert s.price_at(101.0 + 121.0) is None


def zipped(csv: str, name: str = "X-1s-2025-01-01.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(name, csv)
    return buf.getvalue()


def test_parse_day_reads_microsecond_files():
    blob = zipped("1735689600000000,1,1,1,100.5,0,0,0,0,0,0,0\n"
                  "1735689601000000,1,1,1,100.7,0,0,0,0,0,0,0\n")
    s = prices.parse_day(blob)
    assert list(s.open_s) == [1_735_689_600.0, 1_735_689_601.0]
    assert list(s.close) == [100.5, 100.7]


def test_checksum_mismatch_is_refused():
    blob = zipped("1,1,1,1,1,0,0,0,0,0,0,0\n")
    good = hashlib.sha256(blob).hexdigest() + "  X.zip"
    prices.verify(blob, good, "X.zip")
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        prices.verify(blob + b"tampered", good, "X.zip")


def test_missing_day_is_cached_and_not_asked_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(prices, "_get", lambda url: calls.append(url) or None)
    assert prices.fetch_day("NOPEUSDT", "2025-01-01", tmp_path) is None
    assert prices.fetch_day("NOPEUSDT", "2025-01-01", tmp_path) is None
    assert len(calls) == 1


# ── the study ────────────────────────────────────────────────────────────────

T0 = 1_000_000.0


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def event(kind: str = "listing", title: str = "t") -> ev.Event:
    return ev.Event(at=at(T0), symbol="X", kind=kind, title=title, language="ko",
                    source="test", source_id="1")


def flat_then_jump(before: float, after: float, jump_at: float = T0 + 5) -> prices.Series:
    start = T0 - 400
    times = start + np.arange(2400, dtype=float)
    closes = np.where(times < jump_at, before, after)
    return prices.Series(open_s=times, close=closes)


def test_returns_before_and_after_the_notice():
    s = flat_then_jump(100.0, 110.0)                # +10% five seconds after the notice
    assert study.window_return(s, T0, -60) == pytest.approx(0.0)
    assert study.window_return(s, T0, 10) == pytest.approx(0.10)
    assert study.window_return(s, T0, 1800) == pytest.approx(0.10)


def test_move_before_the_notice_shows_up_as_run_up():
    s = flat_then_jump(100.0, 110.0, jump_at=T0 - 30)   # the news was out early
    assert study.window_return(s, T0, -60) == pytest.approx(0.10)
    assert study.window_return(s, T0, 10) == pytest.approx(0.0)


def test_market_move_is_subtracted():
    coin = flat_then_jump(100.0, 110.0)             # +10%
    btc = flat_then_jump(100.0, 104.0)              # market +4% at the same moment
    got = study.measure(event(), coin, btc)
    assert got[60] == pytest.approx(0.06)


def test_an_event_missing_any_horizon_is_dropped_whole():
    short = prices.Series(open_s=T0 - 400 + np.arange(500, dtype=float), close=np.full(500, 100.0))
    assert study.measure(event(), short, None) is None   # has +60s, not +1800s


def test_summarise_known_values():
    stat = study.summarise([0.01, 0.03, -0.01, 0.05], np.random.default_rng(0))
    assert stat.n == 4
    assert stat.mean == pytest.approx(0.02)
    assert stat.median == pytest.approx(0.02)
    assert stat.hit == pytest.approx(0.75)
    sd = np.std([0.01, 0.03, -0.01, 0.05], ddof=1)
    assert stat.t == pytest.approx(0.02 / (sd / 2))
    assert stat.ci_low <= stat.mean <= stat.ci_high


def test_summarise_is_reproducible():
    a = study.summarise([0.1, -0.2, 0.3], np.random.default_rng(7))
    b = study.summarise([0.1, -0.2, 0.3], np.random.default_rng(7))
    assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high)


def test_summarise_empty_is_nan_not_an_error():
    assert math.isnan(study.summarise([], np.random.default_rng(0)).mean)


def outcome(kind: str, title: str, move: float) -> study.Outcome:
    return study.Outcome(event(kind, title), "XUSDT", "ok", {60: move})


def test_score_lexicon_reports_hit_rate_next_to_base_rate():
    outcomes = [
        outcome("listing", "bull", +0.02),          # called up, went up      ✓
        outcome("listing", "bull", -0.01),          # called up, went down    ✗
        outcome("delisting", "bear", -0.03),        # called down, went down  ✓
        outcome("caution_on", "meh", +0.01),        # no call
        study.Outcome(event(), "YUSDT", "no_pair"), # unmeasured: ignored
    ]
    impacts = {"bull": 0.45, "bear": -0.5, "meh": 0.05}
    got = study.score_lexicon(outcomes, impacts.__getitem__, horizon=60)
    assert got["events"] == 4
    assert got["calls"] == 3
    assert got["coverage"] == pytest.approx(0.75)
    assert got["hit_rate"] == pytest.approx(2 / 3)
    assert got["base_rate_up"] == pytest.approx(0.5)
    assert got["calls_by_kind"]["caution_on"] == {"up": 0, "down": 0, "none": 1, "right": 0}
    assert got["calls_by_kind"]["listing"] == {"up": 2, "down": 0, "none": 0, "right": 1}


def test_by_kind_groups_only_measured_events():
    outcomes = [study.Outcome(event("listing"), "A", "ok", {h: 0.01 for h in study.HORIZONS}),
                study.Outcome(event("listing"), "B", "no_price")]
    table = study.by_kind(outcomes)
    assert list(table) == ["listing"]
    assert table["listing"][60].n == 1


# ── the notice cache ─────────────────────────────────────────────────────────

class _Response:
    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._raw


def _page(notices: list[dict], total_pages: int) -> _Response:
    return _Response({"success": True, "data": {"notices": notices, "total_pages": total_pages}})


def _write_cache(path: Path, notices: list[dict]) -> None:
    path.write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notices), encoding="utf-8")


RECENT = [n for n in NOTICES if n["first_listed_at"] >= "2026-08-01"]


def test_cache_that_covers_since_is_reused_without_asking(tmp_path, monkeypatch):
    cache = tmp_path / "upbit.jsonl"
    _write_cache(cache, RECENT)
    def refuse(*a, **k):
        raise AssertionError("should not have asked Upbit")
    monkeypatch.setattr(ev.urllib.request, "urlopen", refuse)
    got = ev.fetch_upbit(cache, datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert len(got) == len(RECENT)


def test_cache_that_stops_short_of_since_is_refetched(tmp_path, monkeypatch):
    """The bug this guards: a cache built for a recent --since was reused for
    an older one, and the study quietly covered a fraction of the period."""
    cache = tmp_path / "upbit.jsonl"
    _write_cache(cache, RECENT)                    # reaches back only to August 2026
    older = [n for n in NOTICES if n["first_listed_at"] < "2023-07-01"]
    pages = iter([_page(RECENT, 2), _page(older, 2)])
    monkeypatch.setattr(ev.urllib.request, "urlopen", lambda *a, **k: next(pages))
    monkeypatch.setattr(ev.time, "sleep", lambda s: None)
    got = ev.fetch_upbit(cache, datetime(2023, 1, 1, tzinfo=timezone.utc))
    assert len(got) == len(RECENT) + len(older)
    assert json.loads((tmp_path / "upbit.jsonl.meta.json").read_text()) == {"complete": True}


def test_complete_history_is_reused_for_any_since(tmp_path, monkeypatch):
    cache = tmp_path / "upbit.jsonl"
    _write_cache(cache, RECENT)
    (tmp_path / "upbit.jsonl.meta.json").write_text(json.dumps({"complete": True}))
    def refuse(*a, **k):
        raise AssertionError("should not have asked Upbit")
    monkeypatch.setattr(ev.urllib.request, "urlopen", refuse)
    assert ev.fetch_upbit(cache, datetime(2010, 1, 1, tzinfo=timezone.utc))


def test_score_lexicon_from_an_entry_point():
    """Right from the notice isn't the same as right for someone acting at +10 s."""
    early = study.Outcome(event("listing", "bull"), "XUSDT", "ok", {10: 0.20, 60: 0.18})
    got = study.score_lexicon([early], {"bull": 0.45}.__getitem__, horizon=60)
    assert got["hit_rate"] == 1.0                 # up from the notice: +18%
    got = study.score_lexicon([early], {"bull": 0.45}.__getitem__, horizon=60, entry=10)
    assert got["hit_rate"] == 0.0                 # but down from +10 s: 1.18/1.20 - 1 < 0


def test_entry_must_come_before_the_horizon():
    with pytest.raises(ValueError):
        study.score_lexicon([], lambda t: 0.0, horizon=60, entry=60)
