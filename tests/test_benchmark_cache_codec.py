import hashlib
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.benchmark_cache_codec import (
    decode_frame_bundle,
    encode_frame_bundle,
)


def example_frame():
    return pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "observation_date": pd.to_datetime(
                ["2026-01-02", "2026-01-05"]
            ),
            "horizon": pd.Series([5, 20], dtype="int64"),
            "predicted_event": pd.Series([True, pd.NA], dtype="boolean"),
            "predicted_score": [0.7, np.nan],
            "evidence": [("volume", "trend"), tuple()],
        }
    )


class BenchmarkCacheCodecTest(unittest.TestCase):
    def test_round_trips_nullable_types_dates_tuples_and_missing_values(self):
        source = {"ridge_down": example_frame()}

        first_payload, first_checksum, first_rows = encode_frame_bundle(source)
        second_payload, second_checksum, second_rows = encode_frame_bundle(source)

        self.assertEqual(first_payload, second_payload)
        self.assertEqual(first_checksum, second_checksum)
        self.assertEqual(first_rows, 2)
        self.assertEqual(second_rows, 2)
        restored = decode_frame_bundle(first_payload, first_checksum)
        assert_frame_equal(restored["ridge_down"], source["ridge_down"])
        self.assertEqual(
            encode_frame_bundle(restored),
            (first_payload, first_checksum, first_rows),
        )

    def test_sorts_frame_names_but_preserves_column_and_row_order(self):
        frames = {
            "zeta": pd.DataFrame({"second": [2, 1], "first": ["b", "a"]}),
            "alpha": pd.DataFrame({"value": pd.Series([], dtype="float64")}),
        }

        payload, checksum, rows = encode_frame_bundle(frames)
        restored = decode_frame_bundle(payload, checksum)

        self.assertEqual(list(restored), ["alpha", "zeta"])
        self.assertEqual(list(restored["zeta"].columns), ["second", "first"])
        self.assertEqual(restored["zeta"]["first"].tolist(), ["b", "a"])
        self.assertEqual(rows, 2)

    def test_rejects_checksum_truncation_schema_and_expansion_limit(self):
        payload, checksum, _ = encode_frame_bundle({"ridge_down": example_frame()})

        with self.assertRaisesRegex(ValueError, "checksum"):
            decode_frame_bundle(payload + b"x", checksum)
        truncated = payload[:-3]
        with self.assertRaisesRegex(ValueError, "compressed payload"):
            decode_frame_bundle(
                truncated,
                hashlib.sha256(truncated).hexdigest(),
            )
        with self.assertRaisesRegex(ValueError, "maximum"):
            decode_frame_bundle(
                payload,
                checksum,
                maximum_uncompressed_bytes=8,
            )

    def test_rejects_unsupported_or_ambiguous_inputs(self):
        cases = [
            ({1: example_frame()}, "frame name"),
            ({"x": pd.DataFrame([[1, 2]], columns=["a", "a"])}, "duplicate"),
            ({"x": pd.DataFrame({"value": [{"nested": True}]})}, "unsupported"),
            ({"x": pd.DataFrame({"value": [object()]})}, "unsupported"),
            ({"x": pd.DataFrame({"value": [np.inf]})}, "finite"),
            (
                {
                    "x": pd.DataFrame(
                        {
                            "value": pd.date_range(
                                "2026-01-01", periods=1, tz="UTC"
                            )
                        }
                    )
                },
                "timezone",
            ),
        ]

        for frames, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex((TypeError, ValueError), message):
                    encode_frame_bundle(frames)

    def test_rejects_invalid_checksum_and_decode_configuration(self):
        payload, checksum, _ = encode_frame_bundle({"x": pd.DataFrame({"a": [1]})})

        with self.assertRaisesRegex(ValueError, "checksum"):
            decode_frame_bundle(payload, "not-a-checksum")
        with self.assertRaisesRegex(ValueError, "maximum"):
            decode_frame_bundle(
                payload,
                checksum,
                maximum_uncompressed_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()
