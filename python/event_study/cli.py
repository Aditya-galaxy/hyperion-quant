"""
HYPERION EVENTS: COMMAND LINE
=============================
    hyperion-events ingest                    # fetch new notices, measure what's ready
    hyperion-events ingest --seed data/event_study/upbit_trade.jsonl
    hyperion-events report --kinds listing    # price reaction + how fast you'd have to be
    hyperion-events export --format csv > events.csv
    hyperion-events keys create alice@fund.com --plan pro
    hyperion-events serve --port 8000         # the HTTP API (pip install "./python[api]")

Everything lives in one SQLite file (--db, default data/hyperion.db) and the
Binance price cache (--prices). Read only against the exchanges: no keys,
no accounts, no orders.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import events as ev
from . import pipeline, store, study, summary, tradability

DATA = Path("data")


def _date(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _pct(x: float) -> str:
    return "     —" if math.isnan(x) else f"{x * 100:+6.1f}%"


def cmd_ingest(args, conn) -> int:
    if args.seed:
        seeded = [json.loads(line) for line in Path(args.seed).read_text(encoding="utf-8").splitlines() if line]
        print(f"  seeded {store.add_notices(conn, 'upbit', seeded)} notices from {args.seed}")
    report = pipeline.ingest(conn, Path(args.prices), _date(args.since),
                             fetch=None if args.offline else ev.fetch_new,
                             progress=(lambda line: print("  " + line)) if args.verbose else (lambda line: None))
    print(f"  {report.new_notices} new notices, {report.new_events} new or reclassified events; "
          f"measured {report.measured}, pending {report.pending} (price archive not out yet), "
          f"{report.unavailable} not tradable on Binance")
    return 0


def cmd_report(args, conn) -> int:
    kinds = tuple(k.strip() for k in args.kinds.split(","))
    data = store.rows(conn, kinds, _date(args.since) if args.since else None,
                      _date(args.until) if args.until else None)
    ok = [r for r in data if r["status"] == "ok"]
    print(f"\n  {len(data)} events, {len(ok)} measured "
          f"({sum(r['status'] == 'pending' for r in data)} pending, "
          f"{sum(r['status'] in ('no_pair', 'no_price') for r in data)} not on Binance).")
    for kind in kinds:
        s = summary.kind_summary(data, kind, args.fill, args.hold)
        if not s["n"]:
            continue
        print(f"\n  {kind}  (n = {s['n']})")
        print("    abnormal return vs BTC   " + "".join(f"{h:>+8d}s" for h in study.HORIZONS))
        for stat in ("median", "mean"):
            print(f"      {stat:<23}" + "".join(f"{_pct(s['abnormal'][str(h)][stat]):>9}" for h in study.HORIZONS))

        t = s["tradability"]
        if not t["n"]:
            continue
        print(f"    {s['side']} trade, {args.fill} fill, net of fees — median (win rate) by fill delay and hold")
        print("      filled at   " + "".join(f"{'hold +' + str(h) + 's':>18}" for h in tradability.HOLDS))
        for lat, row in t["table"].items():
            cells = "".join(f"{_pct(row[str(h)]['median']) + f' ({row[str(h)]['win'] * 100:3.0f}%)':>18}"
                            if str(h) in row else f"{'':>18}" for h in tradability.HOLDS)
            print(f"      +{lat:<2}s        {cells}")
        edge = t["last_profitable_delay_s"]
        print(f"    → holding {args.hold}s, the typical trade made money only if filled by "
              + (f"+{edge}s." if edge is not None else "— never, even at the fastest fill."))
    print()
    return 0


def cmd_export(args, conn) -> int:
    data = store.rows(conn, tuple(k.strip() for k in args.kinds.split(",")))
    if args.format == "json":
        json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return 0
    csv.writer(sys.stdout).writerows(summary.csv_rows(data))
    return 0


def cmd_keys(args, conn) -> int:
    if args.action == "create":
        from .api import PLANS
        if args.plan not in PLANS:
            print(f"  unknown plan {args.plan!r}; choose from {', '.join(PLANS)}")
            return 2
        key = store.create_key(conn, args.owner, args.plan)
        print(f"  {key}\n  ↑ give this to {args.owner} ({args.plan} plan). It is not stored and won't be shown again.")
    elif args.action == "list":
        for k in store.list_keys(conn):
            state = f"revoked {k['revoked_at'][:10]}" if k["revoked_at"] else f"last used {(k['last_used_at'] or 'never')[:16]}"
            print(f"  #{k['id']:<4} {k['prefix']}…  {k['owner']:<24} {k['plan']:<9} {state}")
    else:
        ok = store.revoke_key(conn, args.id)
        print(f"  key #{args.id} " + ("revoked." if ok else "not found or already revoked."))
        return 0 if ok else 1
    return 0


def cmd_serve(args, conn) -> int:
    import uvicorn

    from .api import create_app
    conn.close()                                        # the app opens its own, one per request
    uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hyperion-events", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DATA / "hyperion.db"))
    parser.add_argument("--prices", default=str(DATA / "event_study" / "binance_1s"))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="fetch new notices and measure every event whose prices are out")
    p.add_argument("--since", default="2024-01-01", help="earliest notice to fetch (UTC)")
    p.add_argument("--seed", help="load notices from a saved JSON-lines file first")
    p.add_argument("--offline", action="store_true", help="don't ask Upbit; only measure what's stored")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(run=cmd_ingest)

    p = sub.add_parser("report", help="price reaction and tradability by kind")
    p.add_argument("--kinds", default="listing,delisting")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--fill", choices=tradability.FILLS, default="worst")
    p.add_argument("--hold", type=int, choices=tradability.HOLDS, default=300)
    p.set_defaults(run=cmd_report)

    p = sub.add_parser("export", help="every event and its measurement, to stdout")
    p.add_argument("--kinds", default=",".join(ev.KINDS))
    p.add_argument("--format", choices=("csv", "json"), default="csv")
    p.set_defaults(run=cmd_export)

    p = sub.add_parser("keys", help="create, list or revoke customer API keys")
    keys = p.add_subparsers(dest="action", required=True)
    k = keys.add_parser("create")
    k.add_argument("owner", help="who the key is for, e.g. an email")
    k.add_argument("--plan", default="research")
    keys.add_parser("list")
    k = keys.add_parser("revoke")
    k.add_argument("id", type=int)
    p.set_defaults(run=cmd_keys)

    p = sub.add_parser("serve", help="run the HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(run=cmd_serve)

    args = parser.parse_args(argv)
    conn = store.connect(args.db)
    try:
        return args.run(args, conn)
    except BrokenPipeError:                # output piped into `head` and closed early
        sys.stderr.close()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
