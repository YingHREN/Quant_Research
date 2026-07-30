import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from fetch_macro_data import fetch_initial_release_observations, main
from web.services.macro_store import MacroObservationStore


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class MacroDataFetchTest(unittest.TestCase):
    def test_skips_window_before_series_entered_alfred(self):
        requests = []

        def opener(request, timeout):
            requests.append((request.full_url, timeout))
            if len(requests) == 1:
                payload = {
                    "error_code": 400,
                    "error_message": (
                        "The series does not exist in ALFRED "
                        "but may exist in FRED."
                    ),
                }
                raise HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    None,
                    io.BytesIO(json.dumps(payload).encode("utf-8")),
                )
            return Response(
                {
                    "observations": [
                        {
                            "realtime_start": "2025-01-02",
                            "realtime_end": "9999-12-31",
                            "date": "2025-01-01",
                            "value": "2.75",
                        }
                    ]
                }
            )

        rows = fetch_initial_release_observations(
            "secret",
            "BAMLH0A0HYM2",
            observation_start="2020-01-01",
            observation_end="2026-07-01",
            opener=opener,
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 2.75)

    def test_chunks_long_realtime_window_below_fred_vintage_limit(self):
        requests = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            requests.append((query, timeout))
            release_date = query["realtime_start"][0]
            return Response(
                {
                    "observations": [
                        {
                            "realtime_start": release_date,
                            "realtime_end": "9999-12-31",
                            "date": release_date,
                            "value": "4.75",
                        }
                    ]
                }
            )

        rows = fetch_initial_release_observations(
            "secret",
            "DGS2",
            observation_start="2020-01-01",
            observation_end="2026-07-01",
            opener=opener,
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(
            [
                (
                    request["realtime_start"][0],
                    request["realtime_end"][0],
                )
                for request, _timeout in requests
            ],
            [
                ("2020-01-01", "2024-12-31"),
                ("2025-01-01", "9999-12-31"),
            ],
        )
        self.assertTrue(
            all(request["output_type"] == ["4"] for request, _ in requests)
        )
        self.assertEqual(
            [row["realtime_start"] for row in rows],
            ["2020-01-01", "2025-01-01"],
        )

    def test_parses_initial_release_vintage_and_skips_missing_values(self):
        requests = []

        def opener(request, timeout):
            requests.append((request.full_url, timeout))
            return Response(
                {
                    "observations": [
                        {
                            "realtime_start": "2026-07-01",
                            "realtime_end": "2026-07-02",
                            "date": "2026-06-30",
                            "value": "4.75",
                        },
                        {
                            "realtime_start": "2026-07-02",
                            "realtime_end": "9999-12-31",
                            "date": "2026-07-01",
                            "value": ".",
                        },
                    ]
                }
            )

        rows = fetch_initial_release_observations(
            "secret",
            "DGS2",
            observation_start="2026-01-01",
            observation_end="2026-07-01",
            opener=opener,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 4.75)
        self.assertEqual(
            rows[0]["available_at"],
            "2026-07-01T23:59:59+00:00",
        )
        self.assertEqual(rows[0]["source"], "ALFRED_initial_release")
        self.assertEqual(rows[0]["revision_policy"], "initial_release_only")
        self.assertIn("output_type=4", requests[0][0])
        self.assertIn("series_id=DGS2", requests[0][0])
        self.assertNotIn("secret", str(rows))

    def test_cli_initializes_separate_database_and_upserts_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.db"

            def fetcher(*_args, **_kwargs):
                return [
                    {
                        "series_id": "VIXCLS",
                        "observation_date": "2026-07-01",
                        "available_at": "2026-07-01T23:59:59+00:00",
                        "value": 28.0,
                        "realtime_start": "2026-07-01",
                        "realtime_end": "9999-12-31",
                        "source": "ALFRED_initial_release",
                        "revision_policy": "initial_release_only",
                    }
                ]

            result = main(
                [
                    "--database",
                    str(path),
                    "--api-key",
                    "secret",
                    "--series",
                    "VIXCLS",
                    "--start",
                    "2026-01-01",
                    "--end",
                    "2026-07-01",
                ],
                fetcher=fetcher,
            )

            rows = MacroObservationStore(path).load_available(
                "2026-07-02T00:00:00+00:00"
            )
            self.assertEqual(result, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows.iloc[0]["series_id"], "VIXCLS")


if __name__ == "__main__":
    unittest.main()
