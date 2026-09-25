"""
HYPERION QUANT: EXCHANGE NOTICES AS TIMESTAMPED EVENTS
======================================================
Turns Upbit's trade notices (listings, delistings, caution designations) into
events with a UTC timestamp, a base-asset symbol and a kind.

Two details decide whether an event study built on these is worth anything:

  * The timestamp is `first_listed_at`, not `listed_at`. Upbit re-stamps a
    notice when it is amended ("거래지원 개시 시점 변경 안내" — start time
    changed), and in a sample of 80 notices 12 carried an update time hours
    after the original publication. Measuring price moves from the update
    time starts the clock after the market has already reacted.

  * Classification is deterministic string matching on Upbit's own notice
    templates, so a result can always be traced to the rule that produced it.
    Anything that does not match a known template is kind "other" and is left
    out of the study, not guessed at.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

UPBIT_NOTICES = ("https://api-manager.upbit.com/api/v1/announcements"
                 "?os=web&page={page}&per_page={per_page}&category=trade")
# The endpoint rejects page sizes above a small cap ("Bad request" at 40 and 100).
PER_PAGE = 20
# One request every few seconds. This is an endpoint the website uses, not a
# published API, and being throttled mid-backfill costs more than waiting.
REQUEST_GAP_SECONDS = 3.0
USER_AGENT = "hyperion-quant event-study (research; github.com/Aditya-galaxy/hyperion-quant)"

KINDS = ("listing", "delisting", "caution_on", "caution_extended", "caution_off", "warning")


@dataclass(frozen=True)
class Event:
    at: datetime          # UTC, timezone-aware
    symbol: str           # base asset, e.g. "SOPH"
    kind: str             # one of KINDS, or "other"
    title: str
    language: str
    source: str
    source_id: str


def classify(title: str) -> str:
    """Which kind of notice this is. Order matters: a lifted designation also
    contains the words for a designation, and a cancellation contains the
    words for the listing it cancels."""
    if "취소" in title:                       # a listing cancelled — not a listing
        return "other"
    if "지정 해제" in title:                  # caution designation lifted
        return "caution_off"
    if "지정 기간 연장" in title:             # caution designation extended
        return "caution_extended"
    if "유의 종목 지정" in title:             # designated a caution item
        return "caution_on"
    if "유의 촉구" in title:                  # public warning, often the step before
        return "warning"
    if "거래지원 종료" in title or "상장폐지" in title:
        return "delisting"
    if ("신규 거래지원" in title or "디지털 자산 추가" in title
            or re.search(r"\)\s*상장", title) or re.search(r"마켓.*오픈", title)):
        return "listing"
    return "other"


# A bracket holding only tickers: "(INJ)", "(ETC/KRW)", "(BICO, BMT, NIL, GWEI)".
# Market lists "(KRW, BTC, USDT 마켓)" and dates "(10/19 15:00)" do not match.
_TICKER_GROUP = re.compile(
    r"\(((?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,15}(?:\s*,\s*(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,15})*)(?:/[A-Z]{3,4})?\)")


def symbols(title: str) -> list[str]:
    """Base-asset tickers named in a notice title, in order, without repeats."""
    found: list[str] = []
    for group in _TICKER_GROUP.findall(title):
        for token in re.split(r"\s*,\s*", group):
            if token not in found:
                found.append(token)
    return found


def parse_time(stamp: str) -> datetime:
    """Upbit stamps carry their own offset (+09:00); normalise to UTC."""
    return datetime.fromisoformat(stamp).astimezone(timezone.utc)


def from_notice(notice: dict) -> list[Event]:
    """One event per ticker named in the notice. A notice with no ticker
    ("ETH 및 ERC 계열 디지털 자산 투자 유의 촉구") produces none."""
    kind = classify(notice["title"])
    at = notice_time(notice)
    return [Event(at=at, symbol=sym, kind=kind, title=notice["title"], language="ko",
                  source="upbit", source_id=str(notice["id"]))
            for sym in symbols(notice["title"])]


def notice_time(notice: dict) -> datetime:
    return parse_time(notice.get("first_listed_at") or notice["listed_at"])


def _meta_path(cache: Path) -> Path:
    return cache.with_name(cache.name + ".meta.json")


def _cache_covers(notices: list[dict], since: datetime, complete: bool) -> bool:
    """A cache can answer for `since` only if it reaches back past it, or holds
    Upbit's whole history. A cache fetched for a later `since` would otherwise
    be reused for an earlier one and quietly study a fraction of the period."""
    if complete:
        return True
    return bool(notices) and min(notice_time(n) for n in notices) < since


def fetch_upbit(cache: Path, since: datetime, refresh: bool = False) -> list[dict]:
    """Every trade notice back to `since`, cached as JSON lines.

    The cache is reused only when it covers `since`; otherwise, or with
    `refresh`, pages are fetched newest first until one is entirely older than
    `since` or the history runs out.
    """
    if cache.exists() and not refresh:
        cached = [json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines() if line]
        meta = _meta_path(cache)
        complete = meta.exists() and json.loads(meta.read_text()).get("complete", False)
        if _cache_covers(cached, since, complete):
            return cached

    notices: list[dict] = []
    complete = False
    page = 1
    while True:
        request = urllib.request.Request(UPBIT_NOTICES.format(page=page, per_page=PER_PAGE),
                                         headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
        if not body.get("success"):
            raise RuntimeError(f"Upbit refused page {page}: {body.get('error_message')}")
        batch = body["data"]["notices"]
        if not batch:
            complete = True
            break
        notices.extend(batch)
        if all(notice_time(n) < since for n in batch):
            break
        if page >= body["data"]["total_pages"]:
            complete = True
            break
        page += 1
        time.sleep(REQUEST_GAP_SECONDS)

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notices),
                     encoding="utf-8")
    _meta_path(cache).write_text(json.dumps({"complete": complete}))
    return notices


def load_events(notices: list[dict], since: datetime, until: datetime | None = None,
                kinds: tuple[str, ...] = KINDS) -> list[Event]:
    events = [e for n in notices for e in from_notice(n)]
    return sorted((e for e in events
                   if e.kind in kinds and e.at >= since and (until is None or e.at < until)),
                  key=lambda e: e.at)
