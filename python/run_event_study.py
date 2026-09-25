"""
HYPERION QUANT: NEWS EVENT STUDY ON REAL PRICES
===============================================
Measures what Binance prices actually did around Upbit's trade notices —
listings, delistings, caution designations — at one-second resolution, and
scores the multilingual keyword engine against those real moves.

    python3 python/run_event_study.py                       # 30 most recent events
    python3 python/run_event_study.py --since 2025-01-01 --limit 200
    python3 python/run_event_study.py --kinds listing,delisting --horizon 30

Downloads are Binance's public archive (about 0.5–3 MB per symbol-day, SHA-256
verified) and Upbit's notice list, both cached under data/event_study/. Read
only: no keys, no accounts, no orders.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from event_study import events as ev
from event_study import prices, study

ROOT = HERE.parent
CACHE = ROOT / "data" / "event_study"


def bps(x: float) -> str:
    return "     —" if math.isnan(x) else f"{x * 1e4:+7.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2025-01-01", help="earliest notice date (UTC)")
    parser.add_argument("--until", default=None, help="latest notice date (UTC), exclusive")
    parser.add_argument("--kinds", default=",".join(ev.KINDS))
    parser.add_argument("--limit", type=int, default=30,
                        help="most recent N events (each costs ~1–6 MB of downloads the first time)")
    parser.add_argument("--horizon", type=int, default=60, help="seconds after the notice used to score the keyword engine")
    parser.add_argument("--refresh", action="store_true", help="re-fetch the Upbit notice list")
    parser.add_argument("--out", default=str(CACHE / "results.json"))
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc) if args.until else None
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    if args.horizon not in study.HORIZONS or args.horizon <= 0:
        print(f"--horizon must be one of {[h for h in study.HORIZONS if h > 0]}")
        return 2

    notices = ev.fetch_upbit(CACHE / "upbit_trade.jsonl", since, refresh=args.refresh)
    events = ev.load_events(notices, since, until, kinds)[-args.limit:]
    print(f"\n  {len(notices)} Upbit trade notices cached; {len(events)} events selected "
          f"({args.since} onward, kinds: {', '.join(kinds)}).\n")

    price_cache = CACHE / "binance_1s"
    lead = max(-h for h in study.HORIZONS if h < 0) + 60
    tail = max(study.HORIZONS) + 60
    outcomes: list[study.Outcome] = []
    for n, event in enumerate(events, 1):
        pair = f"{event.symbol}USDT"
        start, end = event.at - timedelta(seconds=lead), event.at + timedelta(seconds=tail)
        series = prices.load(pair, start, end, price_cache)
        if series is None:
            outcomes.append(study.Outcome(event, pair, "no_pair"))
            print(f"  [{n:>3}/{len(events)}] {pair:<14} {event.kind:<17} not on Binance that day")
            continue
        market = None if pair == study.MARKET else prices.load(study.MARKET, start, end, price_cache)
        abnormal = study.measure(event, series, market)
        status = "ok" if abnormal is not None else "no_price"
        outcomes.append(study.Outcome(event, pair, status, abnormal or {}))
        detail = (f"{bps(abnormal[60])} bps at +60s" if abnormal else "no trades in part of the window")
        print(f"  [{n:>3}/{len(events)}] {pair:<14} {event.kind:<17} {detail}")
        time.sleep(0.2)

    ok = [o for o in outcomes if o.status == "ok"]
    print(f"\n  Measured {len(ok)} of {len(outcomes)} events "
          f"({sum(o.status == 'no_pair' for o in outcomes)} not listed on Binance, "
          f"{sum(o.status == 'no_price' for o in outcomes)} with gaps in trading).")

    table = study.by_kind(outcomes)
    for kind, stats in table.items():
        n = next(iter(stats.values())).n
        print(f"\n  {kind}  (n = {n}; abnormal return vs BTC, basis points)")
        print("    horizon     mean   median      t    up%    95% CI of mean")
        for h, s in stats.items():
            label = f"{h:+d}s" + (" before" if h < 0 else "")
            ci = f"[{bps(s.ci_low).strip()}, {bps(s.ci_high).strip()}]"
            t = "   —" if math.isnan(s.t) else f"{s.t:+5.2f}"
            print(f"    {label:<11} {bps(s.mean)} {bps(s.median)}  {t}  {s.hit * 100:4.0f}%   {ci}")
        if n < 20:
            print(f"    ↳ {n} events is too few to conclude anything; widen --since or --limit.")

    lexicon = None
    try:
        from multilingual_alpha_engine import MultilingualNewsAlphaEngine
        engine = MultilingualNewsAlphaEngine()
        lexicon = study.score_lexicon(
            outcomes, lambda title: engine.evaluate_headline(title, "ko", "upbit").expected_impact,
            args.horizon)
        print(f"\n  Keyword engine vs the real move at +{args.horizon}s: "
              f"called {lexicon['calls']} of {lexicon['events']} events "
              f"({lexicon['coverage'] * 100:.0f}% coverage), right on "
              f"{lexicon['hit_rate'] * 100:.0f}% of its calls; "
              f"'always up' would score {lexicon['base_rate_up'] * 100:.0f}%.")
        for kind, c in lexicon["calls_by_kind"].items():
            print(f"    {kind:<17} up {c['up']:>3}   down {c['down']:>3}   no call {c['none']:>3}")
    except ImportError as exc:
        print(f"\n  (keyword engine not scored: {exc})")

    print("\n  Nine horizons across several kinds is many comparisons; expect a few")
    print("  'significant' cells by chance alone. Trust a pattern, not a cell.\n")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "args": vars(args),
        "events": [{"at": o.event.at.isoformat(), "symbol": o.event.symbol, "pair": o.pair,
                    "kind": o.event.kind, "title": o.event.title, "status": o.status,
                    "abnormal": {str(h): r for h, r in o.abnormal.items()}} for o in outcomes],
        "summary": {kind: {str(h): vars(s) for h, s in stats.items()} for kind, stats in table.items()},
        "lexicon": lexicon,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  Per-event results: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
