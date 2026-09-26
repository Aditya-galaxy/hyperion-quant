"""
HYPERION EVENTS: WHAT MAY BE DONE WITH THE NUMBERS
==================================================
Everything published from this database — the site, the API, the exports —
is derived from Binance Vision price archives, which are licensed CC BY-NC-SA
4.0 (Binance Vision Dataset Terms v1.0, 26 Aug 2026). Derived work must
credit Binance Vision, stay non-commercial, and carry the same licence
(clauses 2.4, 3.1, 4.5). This module holds that notice so every surface says
the same thing.

Upbit owns the text of its notices. Published output carries the facts —
when, which asset, what kind — and a link to the notice, not its title.

The code itself is MIT (see LICENSE); this licence covers the data only.
"""

from __future__ import annotations

DATA_LICENSE = "CC-BY-NC-SA-4.0"
DATA_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"

ATTRIBUTION = (
    "Prices: Binance Vision (data.binance.vision), CC BY-NC-SA 4.0. "
    "Event times: Upbit trade notices (upbit.com). "
    "Measurements derived by Hyperion Events and licensed CC BY-NC-SA 4.0: "
    "non-commercial use only, credit the sources, share alike. "
    "Not affiliated with or endorsed by Binance or Upbit. Research, not financial advice."
)


def notice_url(source: str, source_id: str) -> str | None:
    """Where to read the original notice."""
    if source == "upbit":
        return f"https://upbit.com/service_center/notice?id={source_id}"
    return None
