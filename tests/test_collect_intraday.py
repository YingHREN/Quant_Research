import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import collect_intraday
from web.app import DEFAULT_DATABASE as FLASK_DEFAULT_DATABASE


class CollectIntradayCliTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_missing_credentials_exits_without_printing_secret(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(SystemExit, "Alpaca credentials") as error:
                collect_intraday.build_collector(["--selected", "AMD"])
        self.assertNotIn("secret", str(error.exception).lower())

    def test_arguments_build_expected_initial_pool(self):
        with mock.patch.dict(
            "os.environ",
            {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"},
            clear=True,
        ):
            collector = collect_intraday.build_collector(
                ["--selected", "AMD", "--peer", "NVDA",
                 "--candidate", "NBIS", "--database", "data/prices.db"]
            )
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["desired_symbols"][:4],
                         ["SPY", "QQQ", "SOXX", "AMD"])
        self.assertNotIn("secret", str(snapshot).lower())

    def test_cli_and_flask_share_absolute_project_database_default(self):
        with mock.patch.dict(
            "os.environ",
            {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"},
            clear=True,
        ):
            collector = collect_intraday.build_collector(["--selected", "AMD"])
        self.assertTrue(collector._store.db_path.is_absolute())
        self.assertEqual(collector._store.db_path, FLASK_DEFAULT_DATABASE)

    def test_build_does_not_create_a_missing_database_path(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "missing" / "prices.db"
            with mock.patch.dict(
                "os.environ",
                {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"},
                clear=True,
            ):
                collector = collect_intraday.build_collector(
                    ["--selected", "AMD", "--database", str(database)]
                )
            self.assertEqual(collector._store.db_path, database)
            self.assertFalse(database.exists())

    def test_main_propagates_collector_cancellation(self):
        class CancelledCollector:
            async def run(self):
                raise asyncio.CancelledError

        with mock.patch.object(
            collect_intraday, "build_collector", return_value=CancelledCollector()
        ):
            with self.assertRaises(asyncio.CancelledError):
                collect_intraday.main()

    def test_main_returns_cleanly_after_keyboard_interrupt(self):
        collector = mock.Mock()
        collector.run.return_value = object()
        with mock.patch.object(
            collect_intraday, "build_collector", return_value=collector
        ), mock.patch.object(
            collect_intraday.asyncio, "run", side_effect=KeyboardInterrupt
        ):
            self.assertIsNone(collect_intraday.main())


if __name__ == "__main__":
    unittest.main()
