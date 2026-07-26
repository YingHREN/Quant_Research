from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from data.universe_catalog import build_catalog, write_catalog


def universe_payload(securities):
    return {
        "asof": "2026-07-24",
        "selection_rule": "liquid_us_common_v1",
        "thresholds": {
            "min_price": 5,
            "min_market_cap": 300_000_000,
            "min_avg_dollar_volume_50d": 100_000_000,
        },
        "securities": securities,
    }


def security(ticker, name):
    return {
        "ticker": ticker,
        "name": name,
        "exchange": "NASDAQ",
        "isin": f"ISIN-{ticker}",
        "asof": "2026-07-24",
        "close": 100.0,
        "market_cap": 1_000_000_000,
        "avg_volume_50d": 2_000_000,
        "avg_dollar_volume_50d": 200_000_000,
        "already_local": False,
        "selection_rule": "liquid_us_common_v1",
    }


class UniverseCatalogTest(unittest.TestCase):
    def test_build_catalog_excludes_short_history_and_sorts_tickers(self):
        payload = universe_payload(
            [security("ZZZ", "Last"), security("AAA", "First"), security("NEW", "New")]
        )
        identities = {
            "AAA": {
                "cik": 1,
                "sic": "3674",
                "sicDescription": "Semiconductors",
            },
            "NEW": {
                "cik": 2,
                "sic": "7372",
                "sicDescription": "Software",
            },
            "ZZZ": {
                "cik": 3,
                "sic": "6022",
                "sicDescription": "Bank",
            },
        }

        result = build_catalog(
            payload,
            identities,
            {"AAA": 2513, "NEW": 59, "ZZZ": 1200},
            asof="2026-07-24",
        )

        self.assertEqual(result["schema_version"], "universe_catalog_v1")
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["eligible_count"], 2)
        self.assertEqual(
            [row["ticker"] for row in result["securities"]],
            ["AAA", "ZZZ"],
        )
        self.assertEqual(
            result["excluded"],
            [{"ticker": "NEW", "reason": "history_below_60_rows", "history_rows": 59}],
        )
        self.assertEqual(
            result["sector_counts"],
            {"financials": 1, "technology": 1},
        )
        aaa = result["securities"][0]
        self.assertEqual(aaa["history_rows"], 2513)
        self.assertEqual(aaa["classification"]["sector_key"], "technology")
        self.assertEqual(aaa["classification"]["theme_keys"], ["semiconductor"])
        self.assertEqual(aaa["classification"]["source"], "sec")
        self.assertEqual(aaa["classification"]["rule_version"], "sec_sic_v1")

    def test_duplicate_ticker_is_rejected_instead_of_silently_overwritten(self):
        payload = universe_payload(
            [security("AAA", "First"), security("AAA", "Duplicate")]
        )

        with self.assertRaisesRegex(ValueError, "duplicate ticker: AAA"):
            build_catalog(
                payload,
                {"AAA": {"cik": 1, "sic": "3674", "sicDescription": "Semi"}},
                {"AAA": 100},
                asof="2026-07-24",
            )

    def test_missing_sec_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing SEC identity: AAA"):
            build_catalog(
                universe_payload([security("AAA", "First")]),
                {},
                {"AAA": 100},
                asof="2026-07-24",
            )

    def test_write_catalog_reads_real_boundary_files_and_writes_stable_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            identities = root / "identities"
            source.mkdir()
            identities.mkdir()
            payload = universe_payload([security("AAA", "First")])
            (source / "expanded_universe_liquid100m_v1.json").write_text(
                json.dumps(payload)
            )
            (source / "AAA.json").write_text(
                json.dumps(
                    [
                        {
                            "date": f"2026-01-{day:02d}",
                            "open": 10,
                            "high": 11,
                            "low": 9,
                            "close": 10,
                            "adjusted_close": 10,
                            "volume": 100,
                        }
                        for day in range(1, 61)
                    ]
                )
            )
            (identities / "AAA.json").write_text(
                json.dumps(
                    {
                        "cik": "0000000001",
                        "sic": "7372",
                        "sicDescription": "Prepackaged Software",
                    }
                )
            )
            output = root / "catalog.json"

            first = write_catalog(source, identities, output)
            first_bytes = output.read_bytes()
            second = write_catalog(source, identities, output)

            self.assertEqual(first, second)
            self.assertEqual(first_bytes, output.read_bytes())
            self.assertEqual(first["eligible_count"], 1)
            self.assertTrue(first_bytes.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
