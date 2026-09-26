"""
HYPERION QUANT: BINANCE 1-SECOND PRICES FROM THE PUBLIC ARCHIVE
===============================================================
Reads Binance's own public archive (data.binance.vision): one zip per symbol
per day, about 0.5–3 MB, each with a published SHA-256 that is checked before
the file is used. No API key, no account, no order endpoints — this module
can only read historical prices.

One-second bars, because the claim being tested is about seconds: a notice
that moves the price within 10 s and one that moves it over 90 s look the same
in one-minute bars.
"""

from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ARCHIVE = "https://data.binance.vision/data/spot/daily/klines/{sym}/1s/{sym}-1s-{day}.zip"
USER_AGENT = "hyperion-quant event-study (research; github.com/Aditya-galaxy/hyperion-quant)"
# Binance publishes a day's file some hours after the day ends. Until then a
# 404 means "not yet", not "never", and must not be cached as missing.
ARCHIVE_LAG = timedelta(days=2)


def archive_settled(day: date, now: datetime | None = None) -> bool:
    """Whether the archive has had time to publish `day`, so that a missing
    file really means the symbol was not trading."""
    now = now or datetime.now(timezone.utc)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1) + ARCHIVE_LAG <= now


def to_seconds(raw: np.ndarray) -> np.ndarray:
    """Archive timestamps are milliseconds up to 2024 and microseconds from
    2025 onward. Read one as the other and every bar lands a thousand times
    too early or too late, silently."""
    raw = np.asarray(raw, dtype=np.float64)
    if raw.size == 0:
        return raw
    if raw.max() > 1e14:          # microseconds
        return raw / 1e6
    if raw.max() > 1e11:          # milliseconds
        return raw / 1e3
    return raw


@dataclass
class Series:
    """Consecutive one-second bars. A second with no trades has no bar."""
    open_s: np.ndarray            # bar open time, seconds since the epoch, ascending
    close: np.ndarray
    high: np.ndarray | None = None
    low: np.ndarray | None = None
    quote_volume: np.ndarray | None = None   # traded value in the quote asset (USDT)

    def bar_covering(self, t: float) -> int | None:
        """Index of the bar that covers [T, T+1) around `t`, or None if
        nothing traded in that second."""
        idx = int(np.searchsorted(self.open_s, t, side="right")) - 1
        if idx < 0 or t >= self.open_s[idx] + 1.0:
            return None
        return idx

    def price_at(self, t: float, max_stale: float = 120.0) -> float | None:
        """The last price known at time `t`: the close of the latest bar that
        had *finished* by then. A bar opening at T covers [T, T+1), so its
        close is only known from T+1 — using it earlier would read the future.
        """
        idx = int(np.searchsorted(self.open_s + 1.0, t, side="right")) - 1
        if idx < 0:
            return None
        if t - (self.open_s[idx] + 1.0) > max_stale:
            return None           # nothing traded for too long to call it a price
        return float(self.close[idx])


def _get(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def fetch_day(symbol: str, day: str, cache: Path) -> bytes | None:
    """The day's zip, from cache or the archive. None means Binance has no
    such file — usually a symbol that was not trading there that day. That
    answer is cached too, so a missing day is asked about once."""
    name = f"{symbol}-1s-{day}.zip"
    path, missing = cache / name, cache / (name + ".missing")
    if path.exists():
        return path.read_bytes()
    if missing.exists():
        return None

    url = ARCHIVE.format(sym=symbol, day=day)
    blob = _get(url)
    cache.mkdir(parents=True, exist_ok=True)
    if blob is None:
        if archive_settled(date.fromisoformat(day)):
            missing.touch()
        return None

    checksum = _get(url + ".CHECKSUM")
    if checksum is None:
        raise RuntimeError(f"{name}: archive has the file but no checksum; refusing to use it")
    verify(blob, checksum.decode(), name)
    path.write_bytes(blob)
    return blob


def verify(blob: bytes, checksum_text: str, name: str) -> None:
    expected = checksum_text.split()[0].strip().lower()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise RuntimeError(f"{name}: SHA-256 mismatch (expected {expected[:12]}…, got {actual[:12]}…)")


def parse_day(blob: bytes) -> Series:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        text = archive.read(archive.namelist()[0])
    # Columns: open_time, open, high, low, close, volume, close_time, quote_volume, ...
    data = np.loadtxt(io.BytesIO(text), delimiter=",", usecols=(0, 2, 3, 4, 7), ndmin=2)
    return Series(open_s=to_seconds(data[:, 0]), high=data[:, 1], low=data[:, 2],
                  close=data[:, 3], quote_volume=data[:, 4])


def load(symbol: str, start: datetime, end: datetime, cache: Path) -> Series | None:
    """Every one-second bar between `start` and `end`, spanning day files as
    needed. None if any day in the range is missing from the archive."""
    parts: list[Series] = []
    day = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    while day <= last:
        blob = fetch_day(symbol, day.isoformat(), cache)
        if blob is None:
            return None
        parts.append(parse_day(blob))
        day += timedelta(days=1)
    def joined(field: str) -> np.ndarray | None:
        columns = [getattr(p, field) for p in parts]
        return None if any(c is None for c in columns) else np.concatenate(columns)
    return Series(open_s=joined("open_s"), close=joined("close"), high=joined("high"),
                  low=joined("low"), quote_volume=joined("quote_volume"))
