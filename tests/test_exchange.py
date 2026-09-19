import gzip
import hashlib
import hmac
import json
import unittest
from decimal import Decimal as D

from copier import crypto
from exchange.parsing import decode_ws_message, parse_fill_event
from exchange.signing import build_query, sign


class SigningTests(unittest.TestCase):
    def test_query_is_sorted_and_timestamped(self):
        q = build_query({"symbol": "BTC-USDT", "side": "BUY", "skip": None}, 1700000000000)
        self.assertEqual(q, "side=BUY&symbol=BTC-USDT&timestamp=1700000000000")

    def test_signature_is_hmac_sha256_hex(self):
        expected = hmac.new(b"secret", b"a=1", hashlib.sha256).hexdigest()
        self.assertEqual(sign("a=1", "secret"), expected)


class ParsingTests(unittest.TestCase):
    MSG = {"e": "ORDER_TRADE_UPDATE", "E": 1, "o": {
        "s": "BTC-USDT", "i": 123, "S": "BUY", "ps": "LONG", "X": "FILLED", "z": "0.5", "ap": "60000.1", "q": "0.5"}}

    def test_filled_order(self):
        f = parse_fill_event(self.MSG)
        self.assertEqual((f.order_id, f.symbol, f.side, f.position_side), ("123", "BTC-USDT", "BUY", "LONG"))
        self.assertEqual((f.quantity, f.avg_price), (D("0.5"), D("60000.1")))

    def test_ignores_non_fills(self):
        new = json.loads(json.dumps(self.MSG))
        new["o"]["X"] = "NEW"
        self.assertIsNone(parse_fill_event(new))
        self.assertIsNone(parse_fill_event({"e": "ACCOUNT_UPDATE"}))

    def test_gzip_and_plain_frames(self):
        self.assertEqual(decode_ws_message(gzip.compress(b"hello")), "hello")
        self.assertEqual(decode_ws_message("Ping"), "Ping")


class CryptoTests(unittest.TestCase):
    def test_roundtrip_and_rotation(self):
        from cryptography.fernet import Fernet
        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        token = crypto.encrypt(crypto.build_cipher(old), "s3cret")
        self.assertNotIn("s3cret", token)
        rotated = crypto.build_cipher(f"{new},{old}")           # new key first, old still decrypts
        self.assertEqual(crypto.decrypt(rotated, token), "s3cret")

    def test_empty_keys_rejected(self):
        with self.assertRaises(ValueError):
            crypto.build_cipher("")


if __name__ == "__main__":
    unittest.main()