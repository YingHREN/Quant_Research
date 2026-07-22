import math
import unittest

import numpy as np
import pandas as pd

from web.contracts import ErrorPayload, iso_date, json_safe


class WebContractTest(unittest.TestCase):
    def test_json_safe_normalizes_numpy_dates_and_non_finite_values(self):
        value = {
            "n": np.int64(3),
            "x": np.float64(1.5),
            "bad": math.nan,
            "date": pd.Timestamp("2026-07-21"),
        }
        self.assertEqual(
            json_safe(value),
            {"n": 3, "x": 1.5, "bad": None, "date": "2026-07-21"},
        )

    def test_error_payload_has_stable_safe_shape(self):
        self.assertEqual(
            ErrorPayload("unknown_ticker", "Ticker not found").to_dict(),
            {"error": {"code": "unknown_ticker", "message": "Ticker not found"}},
        )

    def test_iso_date_accepts_none(self):
        self.assertIsNone(iso_date(None))
