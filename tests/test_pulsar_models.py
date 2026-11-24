"""Tests for the Pulsar model lookup (providers/pulsar.py, providers/pulsar_models.py).

No hardware is needed. The fake is Pulsar's 8K Dongle Gen.2 (3710:5406) with the two
frames an X2N CrazyLight Mini answered through it: the power reply and the ReadCIDMID
reply (customer id 87, model id 26). The cable id 3710:3510 gets the same exchange.

Run from the repository root:

    python -m unittest discover -s tests
"""

# Wire fixtures are the bytes read from the hardware, written out here so they stay
# independent of the provider constants under test.
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import pulsar as P  # noqa: E402
from providers import pulsar_models as M  # noqa: E402

POWER = bytes.fromhex("08 04 00 00 00 02 28 00 0e dc 00 00 00 00 00 00 35")
MODEL = bytes.fromhex("08 10 00 00 00 03 57 1a e4 00 00 00 00 00 00 00 e5")
POWER_REQUEST = [0x08, 0x04] + [0x00] * 14 + [0x49]
MODEL_REQUEST = [0x08, 0x10] + [0x00] * 14 + [0x3D]


def model_reply(cid=87, mid=26, status=0, length=3, command=0x10, break_checksum=False):
    frame = [0x08, command, status, 0x00, 0x00, length, cid, mid] + [0x00] * 8
    ck = (0x55 - sum(frame)) & 0xFF
    if break_checksum:
        ck ^= 0xFF
    return bytes(frame + [ck])


class FakeDongle:
    """One ff02:0002 collection that answers each request from its script."""

    def __init__(self, pid=0x5406, answers=None, present=True):
        self.pid = pid
        self.present = present
        self.sent = []
        self.answers = answers if answers is not None else {0x04: POWER, 0x10: MODEL}
        self.pending = b""
        self.path = b"\\\\?\\hid#vid_3710&pid_%04x#col05" % pid

    def enumerate(self, vid):
        if vid != 0x3710 or not self.present:
            return []
        return [{"path": self.path, "interface_number": 1, "usage_page": 0xFF02,
                 "usage": 0x0002, "product_id": self.pid, "vendor_id": 0x3710}]

    def device_class(self):
        dongle = self

        class FakeDevice:
            def open_path(self, path):
                if not dongle.present:
                    raise OSError("device gone")

            def write(self, frame):
                dongle.sent.append(list(frame))
                dongle.pending = dongle.answers.get(frame[1], b"")

            def read(self, length, timeout_ms=0):
                r, dongle.pending = dongle.pending, b""
                return r

            def close(self):
                pass

        return FakeDevice

    def commands(self):
        return [frame[1] for frame in self.sent]


class PulsarModelTest(unittest.TestCase):
    def setUp(self):
        self._hid, self._hidlist, self._outlen = P.hid, P.hidlist, P.output_length
        P.output_length = lambda path: 17

    def tearDown(self):
        P.hid, P.hidlist, P.output_length = self._hid, self._hidlist, self._outlen

    def plug(self, **kw):
        dongle = FakeDongle(**kw)
        P.hidlist = types.SimpleNamespace(enumerate=dongle.enumerate)
        P.hid = types.SimpleNamespace(device=dongle.device_class())
        return dongle

    def poll_one(self, provider):
        found = [d for d in provider.poll() if d.key.startswith("pulsar:3710")]
        self.assertEqual(1, len(found))
        return found[0]

    def test_the_shared_dongle_shows_the_mouse_it_carries(self):
        dongle = self.plug()
        d = self.poll_one(P.PulsarProvider())
        self.assertEqual(("pulsar:37105406", "Pulsar X2N CrazyLight Mini (2.4 GHz)", 40, False),
                         (d.key, d.name, d.level, d.charging))
        self.assertEqual([POWER_REQUEST, MODEL_REQUEST], dongle.sent)

    def test_the_cable_id_is_read_and_named_as_wired(self):
        self.plug(pid=0x3510)
        d = self.poll_one(P.PulsarProvider())
        self.assertEqual(("pulsar:37103510", "Pulsar X2N CrazyLight Mini (wired)"),
                         (d.key, d.name))

    def test_the_model_is_asked_once_per_receiver(self):
        dongle = self.plug()
        provider = P.PulsarProvider()
        for _ in range(3):
            d = self.poll_one(provider)
        self.assertEqual("Pulsar X2N CrazyLight Mini (2.4 GHz)", d.name)
        self.assertEqual([0x04, 0x10, 0x04, 0x04], dongle.commands())

    def test_the_model_is_asked_again_after_the_receiver_was_unplugged(self):
        dongle = self.plug()
        provider = P.PulsarProvider()
        self.poll_one(provider)
        dongle.present = False
        self.assertEqual([], [d for d in provider.poll() if d.key.startswith("pulsar:3710")])
        dongle.present = True
        dongle.answers[0x10] = model_reply(mid=19)
        self.assertEqual("Pulsar X3 CrazyLight Mini (2.4 GHz)", self.poll_one(provider).name)

    def test_no_model_request_without_a_power_reply(self):
        dongle = self.plug(answers={})
        found = [d for d in P.PulsarProvider().poll() if d.key.startswith("pulsar:3710")]
        self.assertEqual([], found)
        self.assertEqual([0x04], dongle.commands())

    def test_a_sleeping_mouse_keeps_the_reported_name(self):
        dongle = self.plug()
        provider = P.PulsarProvider()
        self.poll_one(provider)
        dongle.answers = {}
        d = self.poll_one(provider)
        self.assertEqual(("Pulsar X2N CrazyLight Mini (2.4 GHz)", 40, False),
                         (d.name, d.level, d.online))

    def test_a_bad_model_reply_keeps_the_name_of_the_product_id(self):
        cases = {
            "silent": b"",
            "short": MODEL[:9],
            "garbage": bytes(range(17)),
            "bad checksum": model_reply(break_checksum=True),
            "error status": model_reply(status=1),
            "no payload": model_reply(length=0),
            "other command": model_reply(command=0x0A),
            "other customer": model_reply(cid=1),
            "model id 0": model_reply(mid=0),
            "placeholder model id": model_reply(mid=111),
            "model id out of the table": model_reply(mid=255),
        }
        for what, answer in cases.items():
            with self.subTest(what):
                dongle = self.plug(answers={0x04: POWER, 0x10: answer})
                provider = P.PulsarProvider()
                d = self.poll_one(provider)
                self.assertEqual(("Pulsar X2 CrazyLight Mini (2.4 GHz)", 40), (d.name, d.level))
                self.poll_one(provider)
                self.assertEqual(1, dongle.commands().count(0x10))

    def test_other_pulsar_ids_are_not_asked(self):
        dongle = self.plug(pid=0x3414)
        d = self.poll_one(P.PulsarProvider())
        self.assertEqual("Pulsar X2 CrazyLight Mini (wired)", d.name)
        self.assertEqual([0x04], dongle.commands())

    def test_parse_model_takes_a_longer_buffer_and_refuses_none(self):
        self.assertEqual((87, 26), P.parse_model(MODEL + b"\x00" * 47))
        self.assertIsNone(P.parse_model(None))
        self.assertIsNone(P.parse_model(POWER))


class PulsarTableTest(unittest.TestCase):
    def test_every_model_id_has_one_name(self):
        ids = [mid for _, mids in M._MODELS for mid in mids]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(M.NAMES))
        self.assertTrue(all(1 <= mid <= 255 for mid in ids))

    def test_names_are_ascii_and_start_with_the_maker(self):
        for name in M.NAMES.values():
            self.assertTrue(name.isascii() and name.startswith("Pulsar "), name)

    def test_lookup(self):
        self.assertEqual("Pulsar X2N CrazyLight Mini", M.model_name(87, 26))
        self.assertEqual("Pulsar X2 CrazyLight Mini", M.model_name(87, 1))
        self.assertIsNone(M.model_name(87, 0))
        self.assertIsNone(M.model_name(87, 111))
        self.assertIsNone(M.model_name(86, 26))


if __name__ == "__main__":
    unittest.main()
