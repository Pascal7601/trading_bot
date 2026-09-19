import unittest
from decimal import Decimal as D

from engine.fills import close_fraction, is_open
from engine.risk import check_open, effective_leverage
from engine.sizing import compute_open_quantity, round_down
from engine.types import RiskLimits, SizingMode, SymbolRules

RULES = SymbolRules(step_size=D("0.001"), min_qty=D("0.001"), min_notional=D("5"))
BASE = dict(master_qty=D("0.5"), price=D("60000"), master_equity=D("100000"), rules=RULES)


class FillTests(unittest.TestCase):
    def test_open_close_classification(self):
        self.assertTrue(is_open("BUY", "LONG"))
        self.assertTrue(is_open("SELL", "SHORT"))
        self.assertFalse(is_open("SELL", "LONG"))
        self.assertFalse(is_open("BUY", "SHORT"))

    def test_one_way_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            is_open("BUY", "BOTH")

    def test_close_fraction(self):
        self.assertEqual(close_fraction(D("1"), D("3")), D("0.25"))   # closed 1 of 4
        self.assertEqual(close_fraction(D("2"), D("0")), D("1"))      # full close
        self.assertEqual(close_fraction(D("0"), D("2")), D("0"))


class SizingTests(unittest.TestCase):
    def test_proportional_scales_by_equity(self):
        # master risks 30,000 notional of 100,000 equity; follower has 1,000 -> 300 notional -> 0.005 BTC
        r = compute_open_quantity(mode=SizingMode.PROPORTIONAL, value=D(1), follower_equity=D("1000"), **BASE)
        self.assertEqual(r.quantity, D("0.005"))

    def test_fixed_notional(self):
        r = compute_open_quantity(mode=SizingMode.FIXED_NOTIONAL, value=D("120"), follower_equity=D("1000"), **BASE)
        self.assertEqual(r.quantity, D("0.002"))

    def test_multiplier(self):
        r = compute_open_quantity(mode=SizingMode.MULTIPLIER, value=D("0.1"), follower_equity=D("1000"), **BASE)
        self.assertEqual(r.quantity, D("0.050"))

    def test_rounds_down_never_up(self):
        self.assertEqual(round_down(D("0.0079999"), D("0.001")), D("0.007"))

    def test_below_minimum_is_skipped(self):
        r = compute_open_quantity(mode=SizingMode.FIXED_NOTIONAL, value=D("3"), follower_equity=D("1000"), **BASE)
        self.assertIsNone(r.quantity)
        self.assertIn("minimum", r.skip_reason)

    def test_max_notional_cap(self):
        lim = RiskLimits(max_notional_per_trade=D("60"))
        r = compute_open_quantity(mode=SizingMode.MULTIPLIER, value=D(1), follower_equity=D("1000"),
                                  limits=lim, **BASE)
        self.assertEqual(r.quantity, D("0.001"))  # 60 / 60000

    def test_bad_inputs(self):
        for kw in (dict(follower_equity=D(0)), dict(value=D(0))):
            args = dict(mode=SizingMode.PROPORTIONAL, value=D(1), follower_equity=D("1000"), **BASE)
            args.update(kw)
            self.assertIsNone(compute_open_quantity(**args).quantity)


class RiskTests(unittest.TestCase):
    def test_symbol_filters(self):
        self.assertIsNotNone(check_open(RiskLimits(blocked_symbols=frozenset({"DOGE-USDT"})), "doge-usdt"))
        allow = RiskLimits(allowed_symbols=frozenset({"BTC-USDT"}))
        self.assertIsNone(check_open(allow, "BTC-USDT"))
        self.assertIsNotNone(check_open(allow, "ETH-USDT"))

    def test_leverage_cap(self):
        self.assertEqual(effective_leverage(20, RiskLimits(max_leverage=10)), 10)
        self.assertEqual(effective_leverage(5, RiskLimits(max_leverage=10)), 5)
        self.assertEqual(effective_leverage(20, RiskLimits()), 20)
        self.assertIsNone(effective_leverage(None, RiskLimits(max_leverage=10)))


if __name__ == "__main__":
    unittest.main()