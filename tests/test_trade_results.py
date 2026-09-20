import unittest
from decimal import Decimal as D

from engine.ledger import PositionLedger
from engine.pnl import roi_pct


class TradeResultTests(unittest.TestCase):
    def test_average_entry_and_exit_over_scale_in_and_out(self):
        led = PositionLedger()
        led.apply("BTC-USDT", "BUY", "LONG", D("1"), exchange_qty_after=D("1"), price=D("100"), leverage=10)
        led.apply("BTC-USDT", "BUY", "LONG", D("1"), price=D("110"))            # average entry 105
        _, after = led.apply("BTC-USDT", "SELL", "LONG", D("1"), price=D("120"))
        self.assertEqual(after, D("1"))
        self.assertIsNone(led.take_result("BTC-USDT", "LONG"))                    # not fully closed yet
        _, after = led.apply("BTC-USDT", "SELL", "LONG", D("1"), price=D("130"))
        self.assertEqual(after, D(0))
        r = led.take_result("BTC-USDT", "LONG")
        self.assertEqual((r.entry_price, r.exit_price, r.leverage, r.closed_qty), (D("105"), D("125"), 10, D("2")))
        self.assertEqual(roi_pct("LONG", r.leverage, r.entry_price, r.exit_price).quantize(D("0.01")), D("190.48"))
        self.assertIsNone(led.take_result("BTC-USDT", "LONG"))                    # only once

    def test_short_position(self):
        led = PositionLedger()
        led.apply("ETH-USDT", "SELL", "SHORT", D("2"), exchange_qty_after=D("2"), price=D("3000"), leverage=5)
        led.apply("ETH-USDT", "BUY", "SHORT", D("2"), price=D("2900"))
        r = led.take_result("ETH-USDT", "SHORT")
        self.assertEqual((r.entry_price, r.exit_price), (D("3000"), D("2900")))
        self.assertGreater(roi_pct("SHORT", r.leverage, r.entry_price, r.exit_price), 0)

    def test_next_trade_starts_clean(self):
        led = PositionLedger()
        led.apply("BTC-USDT", "BUY", "LONG", D("1"), exchange_qty_after=D("1"), price=D("100"), leverage=10)
        led.apply("BTC-USDT", "SELL", "LONG", D("1"), price=D("101"))
        led.take_result("BTC-USDT", "LONG")
        led.apply("BTC-USDT", "BUY", "LONG", D("1"), exchange_qty_after=D("1"), price=D("200"), leverage=3)
        led.apply("BTC-USDT", "SELL", "LONG", D("1"), price=D("210"))
        r = led.take_result("BTC-USDT", "LONG")
        self.assertEqual((r.entry_price, r.exit_price, r.leverage), (D("200"), D("210"), 3))

    def test_seeded_position_keeps_entry_and_resync_does_not_erase_it(self):
        led = PositionLedger()
        led.seed("BTC-USDT", "LONG", D("2"), avg_entry=D("100"), leverage=8)
        led.seed("BTC-USDT", "LONG", D("2"))                                     # periodic resync: qty only
        led.apply("BTC-USDT", "SELL", "LONG", D("2"), price=D("110"))
        r = led.take_result("BTC-USDT", "LONG")
        self.assertEqual((r.entry_price, r.exit_price, r.leverage), (D("100"), D("110"), 8))

    def test_unknown_history_gives_no_result_rather_than_a_guess(self):
        led = PositionLedger()
        led.apply("BTC-USDT", "SELL", "LONG", D("1"), price=D("100"))            # never saw the open
        self.assertIsNone(led.take_result("BTC-USDT", "LONG"))


if __name__ == "__main__":
    unittest.main()