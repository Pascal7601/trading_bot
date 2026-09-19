import unittest
from decimal import Decimal as D

from engine.fills import close_fraction
from engine.ledger import PositionLedger
from engine.pnl import roi_pct
from engine.risk import (adverse_slippage_pct, check_exposure, check_slippage, daily_loss_hit,
                         drawdown_pct, stop_loss_hit)
from engine.stats import Income, format_report, summarize
from engine.types import RiskLimits


class SlippageTests(unittest.TestCase):
    L = RiskLimits(max_slippage_pct=D("1"))

    def test_long_hurt_by_higher_price(self):
        self.assertEqual(adverse_slippage_pct("LONG", D("100"), D("102")), D("2"))
        self.assertEqual(adverse_slippage_pct("LONG", D("100"), D("98")), D("-2"))  # better fill

    def test_short_hurt_by_lower_price(self):
        self.assertEqual(adverse_slippage_pct("SHORT", D("100"), D("98")), D("2"))

    def test_check(self):
        self.assertIsNotNone(check_slippage(self.L, "LONG", D("100"), D("101.5")))
        self.assertIsNone(check_slippage(self.L, "LONG", D("100"), D("100.5")))
        self.assertIsNone(check_slippage(self.L, "LONG", D("100"), D("90")))          # favourable never blocks
        self.assertIsNone(check_slippage(RiskLimits(), "LONG", D("100"), D("150")))   # limit off
        self.assertIn("verify", check_slippage(self.L, "LONG", D("100"), None))        # unknown price: fail closed


class ExposureTests(unittest.TestCase):
    def test_check(self):
        lim = RiskLimits(max_exposure_pct=D("50"))
        self.assertIsNone(check_exposure(lim, D("300"), D("100"), D("1000")))   # 40%
        self.assertIsNotNone(check_exposure(lim, D("450"), D("100"), D("1000")))  # 55%
        self.assertIsNone(check_exposure(RiskLimits(), D("900"), D("900"), D("1000")))


class DrawdownTests(unittest.TestCase):
    def test_drawdown_and_trigger(self):
        self.assertEqual(drawdown_pct(D("1000"), D("950")), D("5"))
        self.assertEqual(drawdown_pct(D("1000"), D("1100")), D(0))
        lim = RiskLimits(max_daily_loss_pct=D("5"))
        self.assertTrue(daily_loss_hit(lim, D("1000"), D("950")))
        self.assertFalse(daily_loss_hit(lim, D("1000"), D("960")))
        self.assertFalse(daily_loss_hit(RiskLimits(), D("1000"), D("1")))

    def test_stop_loss(self):
        lim = RiskLimits(stop_loss_roi_pct=D("10"))
        roi = roi_pct("LONG", 10, D("100"), D("98.9"))   # -1.1% x10 = -11%
        self.assertTrue(stop_loss_hit(lim, roi))
        self.assertFalse(stop_loss_hit(lim, roi_pct("LONG", 10, D("100"), D("99.5"))))
        self.assertFalse(stop_loss_hit(RiskLimits(), D("-99")))


class LedgerScaleOutTests(unittest.TestCase):
    def test_three_partial_take_profits(self):
        led = PositionLedger()
        led.seed("BTC-USDT", "LONG", D("100"))
        fractions = []
        for qty in (D("25"), D("50"), D("25")):            # 25%, then 50%, then the rest (of the original)
            _, after = led.apply("BTC-USDT", "SELL", "LONG", qty)
            fractions.append(close_fraction(qty, after))
        self.assertEqual(fractions[0], D("0.25"))
        self.assertEqual(fractions[1].quantize(D("0.0001")), D("0.6667"))
        self.assertEqual(fractions[2], D(1))
        # a follower holding 10 ends flat: 10 -> 7.5 -> 2.5 -> 0
        pos = D("10")
        for f in fractions:
            pos -= pos * f
        self.assertEqual(pos.quantize(D("0.0000001")), D(0))

    def test_rapid_fills_are_not_misread_from_exchange_snapshot(self):
        led = PositionLedger()
        led.seed("ETH-USDT", "SHORT", D("100"))
        _, after1 = led.apply("ETH-USDT", "BUY", "SHORT", D("25"), exchange_qty_after=D("25"))  # snapshot ran ahead
        self.assertEqual(after1, D("75"))   # ledger ignores the (already-advanced) exchange number

    def test_open_then_add_then_close(self):
        led = PositionLedger()
        self.assertEqual(led.apply("BTC-USDT", "BUY", "LONG", D("1"), exchange_qty_after=D("1")), (D(0), D(1)))
        self.assertEqual(led.apply("BTC-USDT", "BUY", "LONG", D("1"), exchange_qty_after=D("2")), (D(1), D(2)))
        self.assertEqual(led.apply("BTC-USDT", "SELL", "LONG", D("2")), (D(2), D(0)))

    def test_unseen_close_takes_everything(self):
        _, after = PositionLedger().apply("BTC-USDT", "SELL", "LONG", D("3"))
        self.assertEqual(after, D(0))


class StatsTests(unittest.TestCase):
    ITEMS = [Income("REALIZED_PNL", D("10")), Income("REALIZED_PNL", D("-4")), Income("REALIZED_PNL", D("6")),
             Income("TRADING_FEE", D("-1.5")), Income("FUNDING_FEE", D("-0.5")), Income("TRANSFER", D("500"))]

    def test_summary(self):
        s = summarize(self.ITEMS, D("1000"))
        self.assertEqual((s.closes, s.wins, s.losses), (3, 2, 1))
        self.assertEqual((s.realized, s.fees, s.funding, s.net), (D("12"), D("-1.5"), D("-0.5"), D("10")))
        self.assertEqual(s.net_pct, D("1"))          # the 500 deposit is ignored
        self.assertEqual((s.best, s.worst), (D("10"), D("-4")))

    def test_report_text_and_empty(self):
        text = format_report(summarize(self.ITEMS, D("1000")), 7)
        self.assertIn("Closed trades: 3", text)
        self.assertIn("+10.00 USDT (+1.0%)", text)
        empty = format_report(summarize([], None), 7)
        self.assertIn("Closed trades: 0", empty)


if __name__ == "__main__":
    unittest.main()