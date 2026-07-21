import unittest

from scoring.engine import evaluate


def legacy_vcp_factor():
    return {
        "ticker": "TEST",
        "ma": {
            "close": 100.0,
            "ema10": 98.0,
            "ema20": 96.0,
            "ema50": 92.0,
            "sma50": 91.0,
            "sma200": 80.0,
        },
        "hl52": {
            "pct_above_low": 100.0,
            "pct_from_high": 3.0,
            "approx": False,
        },
        "fundamentals": {},
        "rs": 96.0,
        "avg_dollar_vol": 20_000_000,
        "vcp": {
            "n_contractions": 3,
            "is_decreasing": True,
            "vol_dryup": True,
            "vola_contract": True,
            "tightness": 2.0,
        },
        "pivot": {
            "breakout": True,
            "vol_confirm": True,
            "vol_ratio": 1.6,
            "pct_over_pivot": 2.0,
        },
        "pocket_pivot": False,
        "volume": {"vol_ratio": 1.6},
        "adr_pct": 3.0,
        "overheat": {"overheat_score": 0.0},
    }


class LegacyScoringContractTest(unittest.TestCase):
    def test_price_only_score_still_contains_vcp_points(self):
        result = evaluate(legacy_vcp_factor(), market_ok=True, price_only=True)

        self.assertGreater(result.breakdown["VCP结构"], 0)

    def test_legacy_trigger_still_calls_vcp_breakout_buyable(self):
        result = evaluate(legacy_vcp_factor(), market_ok=True, price_only=True)

        self.assertTrue(result.trigger["vcp_breakout"])
        self.assertTrue(result.trigger["buyable_now"])


if __name__ == "__main__":
    unittest.main()

