import json
import tempfile
import unittest
from pathlib import Path

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
