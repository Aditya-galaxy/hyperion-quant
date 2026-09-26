"""
HYPERION GUARD: COMMAND LINE
============================
    hyperion-guard serve              run the API on localhost (the paywall proxy fronts it)
    hyperion-guard anchor             put the Merkle root of new verdicts on Arc

Configuration comes from the environment, never from flags, so keys don't
end up in shell history:

    GUARD_RPC_URL        Arc RPC (testnet: https://rpc.testnet.arc.network)
    GUARD_CHAIN_ID       5042002 on testnet, 5042 on mainnet
    GUARD_CONTRACT       HyperionGuard address
    GUARD_SIGNER_KEY     the Guard's signing key (0x…); its address must be
                         the contract's guardSigner
    GUARD_DB             verdict ledger (default data/guard.db)
    GUARD_TTL            verdict lifetime in seconds (default 60, max 300)
    PYTH_API_KEY         reference prices from Pyth, or
    GUARD_FIXED_PRICES   JSON like {"BTC-USD": "65000"} for demos and tests
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .chain import Chain
from .engine import Guard
from .ledger import Ledger
from .prices import FixedPrices, PythPrices


def _need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"  set {name} (see `hyperion-guard --help`)")
    return value


def build_guard() -> Guard:
    chain = Chain(_need("GUARD_RPC_URL"), _need("GUARD_CONTRACT"), int(_need("GUARD_CHAIN_ID")))
    if os.environ.get("GUARD_FIXED_PRICES"):
        prices = FixedPrices(json.loads(os.environ["GUARD_FIXED_PRICES"]))
    elif os.environ.get("PYTH_API_KEY"):
        prices = PythPrices(os.environ["PYTH_API_KEY"])
    else:
        sys.exit("  set PYTH_API_KEY, or GUARD_FIXED_PRICES for a demo")
    return Guard(chain, prices, Ledger(os.environ.get("GUARD_DB", "data/guard.db")), _need("GUARD_SIGNER_KEY"),
                 ttl=int(os.environ.get("GUARD_TTL", "60")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hyperion-guard", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--port", type=int, default=8100)
    a = sub.add_parser("anchor", help="anchor new verdicts on Arc")
    a.add_argument("--dry-run", action="store_true", help="compute the root without sending")
    args = parser.parse_args(argv)

    guard = build_guard()
    if args.command == "serve":
        import uvicorn

        from .api import create_app
        uvicorn.run(create_app(guard), host="127.0.0.1", port=args.port)   # localhost: the proxy is the front door
        return 0
    out = guard.anchor(send=not args.dry_run)
    print("  nothing new to anchor" if out is None else f"  anchored {json.dumps(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
