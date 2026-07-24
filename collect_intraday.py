from __future__ import annotations

import argparse
import asyncio
import os

from marketdata.alpaca import AlpacaIEXProvider
from marketdata.collector import IntradayCollector
from marketdata.paths import DEFAULT_MARKET_DATA_DATABASE
from marketdata.storage import IntradayStore


def build_collector(argv=None):
    parser = argparse.ArgumentParser(
        description="Collect free Alpaca IEX intraday events"
    )
    parser.add_argument("--selected", default="SPY")
    parser.add_argument("--peer", action="append", default=[])
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument(
        "--database",
        default=str(DEFAULT_MARKET_DATA_DATABASE),
    )
    args = parser.parse_args(argv)
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise SystemExit("Alpaca credentials are required")
    collector = IntradayCollector(
        AlpacaIEXProvider(key, secret),
        IntradayStore(args.database),
    )
    collector.set_selection(args.selected, args.peer, args.candidate)
    return collector


def main():
    try:
        asyncio.run(build_collector().run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
