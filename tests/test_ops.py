import unittest

from engine.health import Throttle, failure_spike, stale_processes
from engine.keycheck import KeyPermissions, evaluate_key
from exchange.parsing import parse_key_permissions, parse_position_mode


class PermissionParsingTests(unittest.TestCase):
    def test_flag_style_payload(self):
        p = parse_key_permissions({"ipRestrict": True, "enableWithdrawals": False, "enableFutures": True})
        self.assertEqual((p.can_withdraw, p.can_trade, p.ip_restricted), (False, True, True))

    def test_withdraw_enabled_is_detected_in_any_payload(self):
        p = parse_key_permissions({"enableWithdrawals": False}, {"data": {"withdraw": "true"}})
        self.assertTrue(p.can_withdraw)

    def test_list_of_permission_names(self):
        ok = parse_key_permissions({"permissions": ["Read", "Perpetual Futures Trading"]})
        self.assertEqual((ok.can_withdraw, ok.can_trade), (False, True))
        bad = parse_key_permissions({"permissions": ["Read", "Withdraw"]})
        self.assertTrue(bad.can_withdraw)

    def test_unknown_shapes_are_unverified_not_guessed(self):
        for payload in ({}, None, [], {"permissions": [1, 2, 3]}, {"withdrawMinAmount": "10", "tradeFee": 0.001}):
            p = parse_key_permissions(payload)
            self.assertIsNone(p.can_withdraw, payload)

    def test_raw_is_kept_for_debugging(self):
        self.assertEqual(parse_key_permissions({"a": 1}).raw, ({"a": 1},))


class PositionModeTests(unittest.TestCase):
    def test_shapes(self):
        self.assertTrue(parse_position_mode({"dualSidePosition": "true"}))
        self.assertFalse(parse_position_mode({"dualSidePosition": False}))
        self.assertTrue(parse_position_mode({"data": {"dualSidePosition": "true"}}))
        self.assertIsNone(parse_position_mode({"something": 1}))
        self.assertIsNone(parse_position_mode("nope"))


class KeyVerdictTests(unittest.TestCase):
    GOOD = KeyPermissions(can_withdraw=False, can_trade=True, ip_restricted=True)

    def test_clean_key(self):
        v = evaluate_key(self.GOOD, True, strict=True)
        self.assertEqual((v.blockers, v.warnings, v.inconclusive), ([], [], []))

    def test_withdrawal_blocks(self):
        v = evaluate_key(KeyPermissions(can_withdraw=True, can_trade=True), True, strict=False)
        self.assertEqual(len(v.blockers), 1)
        self.assertIn("WITHDRAW", v.blockers[0])

    def test_unverifiable_blocks_only_in_strict_mode(self):
        unknown = KeyPermissions()
        self.assertEqual(len(evaluate_key(unknown, True, strict=True).blockers), 1)
        loose = evaluate_key(unknown, True, strict=False)
        self.assertEqual(loose.blockers, [])
        self.assertTrue(loose.inconclusive)

    def test_one_way_mode_blocks_but_unknown_mode_does_not(self):
        self.assertEqual(len(evaluate_key(self.GOOD, False, strict=True).blockers), 1)
        self.assertEqual(evaluate_key(self.GOOD, None, strict=True).blockers, [])

    def test_no_trade_permission_blocks_and_missing_ip_warns(self):
        v = evaluate_key(KeyPermissions(can_withdraw=False, can_trade=False, ip_restricted=False), True, strict=True)
        self.assertEqual(len(v.blockers), 1)
        self.assertEqual(len(v.warnings), 1)


class HealthTests(unittest.TestCase):
    def test_throttle(self):
        t = Throttle(600)
        self.assertTrue(t.allow("a", 0))
        self.assertFalse(t.allow("a", 100))
        self.assertTrue(t.allow("b", 100))
        self.assertTrue(t.allow("a", 601))
        self.assertTrue(t.allow("c", 0, cooldown=0))
        self.assertTrue(t.allow("c", 1, cooldown=0))

    def test_stale_processes(self):
        problems = stale_processes(["listener", "executor", "bot"], {"listener": 5, "executor": 200}, 60)
        self.assertEqual({p.key for p in problems}, {"executor", "bot"})

    def test_failure_spike(self):
        self.assertFalse(failure_spike(3, 3))      # sample too small
        self.assertTrue(failure_spike(10, 5))
        self.assertFalse(failure_spike(10, 2))


if __name__ == "__main__":
    unittest.main()