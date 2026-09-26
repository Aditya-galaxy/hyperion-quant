"""
HYPERION GUARD: REFERENCE PRICES FOR THE COLLAR
===============================================
The collar compares an order's price with an independent reference, not
with a price the agent supplies: an agent that has been talked into a bad
trade would supply a bad reference too.

Sources:
  * Pyth (Hermes). Since Pyth's core upgrade on 2026-08-26 Hermes wants an
    API key (PYTH_API_KEY). Prices older than `max_age` seconds are refused.
  * Fixed, for tests and demos.

A missing or stale price rejects the order (NO_REFERENCE_PRICE) rather than
waving it through: failing closed is the job.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Protocol

import httpx

# Pyth feed ids, checked against hermes.pyth.network/v2/price_feeds.
PYTH_FEEDS = {
    "BTC-USD": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH-USD": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "SOL-USD": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "EUR-USD": "a995d00bb36a63cef7fd2c287dc105fc8f3d93779f062f09551b0af3e81ec30b",
}


class PriceSource(Protocol):
    def price(self, symbol: str) -> Decimal | None: ...


class FixedPrices:
    def __init__(self, prices: dict[str, Decimal | str | float]):
        self.prices = {k.upper(): Decimal(str(v)) for k, v in prices.items()}

    def price(self, symbol: str) -> Decimal | None:
        return self.prices.get(symbol.upper())


class PythPrices:
    URL = "https://hermes.pyth.network/v2/updates/price/latest"

    def __init__(self, api_key: str, max_age: float = 10.0, client: httpx.Client | None = None,
                 clock=time.time):
        self.api_key, self.max_age, self.clock = api_key, max_age, clock
        self.client = client or httpx.Client(timeout=5)

    def price(self, symbol: str) -> Decimal | None:
        feed = PYTH_FEEDS.get(symbol.upper())
        if feed is None:
            return None
        try:
            r = self.client.get(self.URL, params={"ids[]": feed, "parsed": "true"},
                                headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
            p = r.json()["parsed"][0]["price"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return None
        if self.clock() - int(p["publish_time"]) > self.max_age:
            return None
        return Decimal(p["price"]).scaleb(int(p["expo"]))
