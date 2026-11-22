"""Tests for providers/wraith.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import wraith as W  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_status(self):
        self.assertEqual(W.parse_status([0x09, 0x80 | 44]), (44, True))
        self.assertEqual(W.parse_status([0x09, 44]), (44, False))

    def test_bad(self):
        for r in ([], [0x08, 44], [0x09, 101]):
            self.assertIsNone(W.parse_status(r), r)

    def test_names(self):
        self.assertEqual(W.model_name(18), "Wraith W1")
        self.assertEqual(W.model_name(20), "Wraith W1 Freestyle")
        self.assertEqual(W.model_name(1), "Wraith W1")


class ProbeTests(unittest.TestCase):
    def test_non_w1_model_is_not_claimed(self):
        provider = W.WraithProvider()
        dev = types.SimpleNamespace(
            open_path=lambda p: None,
            send_feature_report=lambda d: 2,
            get_feature_report=lambda r, n: [0x09, 0x8F, 0, 1],
            close=lambda: None,
        )
        W.hid = types.SimpleNamespace(device=lambda: dev)
        try:
            self.assertIsNone(provider._probe([{"path": b"x"}]))
        finally:
            W.hid = None


if __name__ == "__main__":
    unittest.main()
