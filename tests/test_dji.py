"""Tests for the DJI microphone provider (personal build).

The two fixtures are real frames, captured from the receiver on 2026-09-27 with the
receiver on USB-C and both Mic Mini 2 transmitters paired - not hand-written byte
strings, so they fail if the decoder's idea of the layout drifts from the device's.

    python -m unittest discover -s tests -v
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import dji  # noqa: E402

# A v2 status push (118 bytes = 2 transmitters) and the v1-style command frame from
# DJI-Mic-Control's PROTOCOL.md, whose CRC is published, as an outside check on our
# CRC-16 (v2 seeds 0x3692 so the whole frame checksums to zero).
STATUS = (
    "557604a65a020000005b03036600030000000020002100000000000000000000000000000000"
    "000000000000030000001e00000002010000001a982404010078000000000000000000000000"
    "000000000000000002020000001a982404410078000000000000000000000000000000000000"
    "0000c3f2"
)
# The same receiver 30 seconds later with TX2 switched off: 86 bytes, one slot, and the
# connected bitmask in header offset 44 down from 0x03 to 0x01.
STATUS_TX2_OFF = (
    "555604675a020000005b03034600030000000020003100000000000000000000000000000000"
    "000000000000010000001e00000002010000001a982404010078000000000000000000000000"
    "0000000000000000755c"
)

IDENTITY = (
    "55b604125a020000005b0303a60001000000001200110302385043545036463031354d374155"
    "04000000000664636165396606000000000c444a49204d6963204d696e690101000000120011"
    "030242334d5450364a3031343838395804010000000630393935393906010000000e444a4920"
    "4d6963204d696e6920320102000000120011030242334d5450364a3031345237584804020000"
    "000666653037333906020000000e444a49204d6963204d696e69203267d9"
)


class CrcTests(unittest.TestCase):
    def test_documented_v2_frame_checksums_to_zero(self):
        # PROTOCOL.md's example command: "55 16 04 fc 02 5a 40 b6 40 5b 01 02 ff ff 00 00
        # 37 00 01 00 dd e2". Its published CRC-16 is the last two bytes (dd e2), so the
        # v2 seed must make the whole frame land on zero - an oracle outside our own code.
        frame = bytes.fromhex("551604fc025a40b6405b0102ffff000037000100dde2")
        self.assertEqual(0, dji.crc16(frame, 0x3692))
        self.assertTrue(dji.crc_ok(frame))

    def test_a_single_flipped_byte_fails_the_check(self):
        frame = bytearray(bytes.fromhex(STATUS))
        frame[60] ^= 0x01
        self.assertFalse(dji.crc_ok(bytes(frame)))


class FrameTests(unittest.TestCase):
    def test_split_keeps_a_partial_tail(self):
        whole = bytes.fromhex(STATUS) + bytes.fromhex(IDENTITY)[:40]
        frames, rest = dji.split_frames(whole)
        self.assertEqual([bytes.fromhex(STATUS)], frames)
        self.assertEqual(bytes.fromhex(IDENTITY)[:40], rest)

    def test_split_skips_leading_noise(self):
        frames, rest = dji.split_frames(b"\x00\x55\x01" + bytes.fromhex(STATUS))
        self.assertEqual([bytes.fromhex(STATUS)], frames)
        self.assertEqual(b"", rest)


class StatusTests(unittest.TestCase):
    def test_both_transmitters_decode(self):
        status = dji.decode_status(bytes.fromhex(STATUS))
        self.assertIsNotNone(status)
        self.assertEqual({"tx1", "tx2"}, set(status))
        for name, tx in status.items():
            self.assertEqual({"gauge", "charging", "unit", "raw"}, set(tx))
            self.assertEqual(int(name[-1]), tx["unit"])
            self.assertTrue(1 <= tx["gauge"] <= 7, "gauge %d is outside 1..7" % tx["gauge"])

    def test_a_non_status_frame_is_not_decoded_as_one(self):
        self.assertIsNone(dji.decode_status(bytes.fromhex(IDENTITY)))

    def test_dialect(self):
        self.assertEqual("v2", dji.dialect(bytes.fromhex(STATUS)))
        self.assertEqual("?", dji.dialect(bytes.fromhex("550e0466") + b"\x00" * 8))


class IdentityTests(unittest.TestCase):
    def test_names_and_serials(self):
        units = dji.identity_records(bytes.fromhex(IDENTITY))
        self.assertIn("rx", units)
        self.assertEqual("8PCTP6F015M7AU", units["rx"]["serial"])
        self.assertEqual("DJI Mic Mini", units["rx"]["product"])
        self.assertEqual("2.3.17.0", units["rx"]["firmware"])

    def test_tags_distinguish_identity_from_audio_level(self):
        self.assertTrue(dji.record_tags(bytes.fromhex(IDENTITY)) & {0x01, 0x06})


class PollTests(unittest.TestCase):
    """poll() against an injected cache - no receiver, no driver, no USB."""

    def setUp(self):
        self.provider = dji.DjiProvider(start=False)
        self.rx = self.provider._receiver

    def _tx(self, name, serial, gauge, charging=False, seen_ago=0.0, gone=False):
        entry = {"unit": int(name[-1]), "gauge": gauge, "charging": charging,
                 "raw": "00", "seen": time.time() - seen_ago}
        if gone:
            entry["gone"] = time.time()
        self.rx.state[name] = entry
        self.rx.info[name] = {"serial": serial, "product": "DJI Mic Mini 2",
                              "firmware": "2.3.17.0"}

    def test_keys_and_names_come_from_the_identity(self):
        self._tx("tx1", "B3MTP6J014889X", 3)
        (status,) = self.provider.poll()
        self.assertEqual("dji:B3MTP6J014889X", status.key)     # stable across re-plugs
        self.assertEqual("DJI Mic Mini 2 TX1", status.name)
        self.assertEqual(75, status.level)
        self.assertEqual("about 75% (gauge 3 of 7)", status.approx)
        self.assertEqual("dji", status.source)

    def test_charging_is_reported_per_transmitter(self):
        self._tx("tx1", "B3MTP6J014889X", 1, charging=True)
        self._tx("tx2", "B3MTP6J014R7XH", 4)
        got = {s.key: s for s in self.provider.poll()}
        self.assertTrue(got["dji:B3MTP6J014889X"].charging)
        self.assertFalse(got["dji:B3MTP6J014R7XH"].charging)

    def test_a_switched_off_transmitter_leaves_the_tray(self):
        self._tx("tx1", "B3MTP6J014889X", 2)
        self._tx("tx2", "B3MTP6J014R7XH", 2, gone=True)       # no slot in the newest push
        keys = [s.key for s in self.provider.poll()]
        self.assertEqual(["dji:B3MTP6J014889X"], keys)

    def test_a_dropout_shorter_than_the_grace_keeps_the_icon(self):
        """A slot missing once is a dropout: the transmitter is still connected per the
        bitmask. Three consecutive misses is a switched-off transmitter."""
        rx = self.rx
        rx._absorb(bytes.fromhex(IDENTITY))      # names and serials, as on a real connect
        rx._absorb(bytes.fromhex(STATUS))        # both on (bitmask 0x03): present
        rx._absorb(bytes.fromhex(STATUS_TX2_OFF))
        keys = {s.key for s in self.provider.poll()}
        self.assertEqual({"dji:B3MTP6J014889X", "dji:B3MTP6J014R7XH"}, keys)

    def test_the_grace_drops_the_transmitter_after_three_misses(self):
        rx = self.rx
        rx._absorb(bytes.fromhex(IDENTITY))
        rx._absorb(bytes.fromhex(STATUS))
        for _ in range(dji.GRACE_PUSHES):
            rx._absorb(bytes.fromhex(STATUS_TX2_OFF))
        keys = {s.key for s in self.provider.poll()}
        self.assertEqual({"dji:B3MTP6J014889X"}, keys)

    def test_the_bitmask_is_read_from_the_frame(self):
        self.rx._absorb(bytes.fromhex(STATUS))
        self.assertEqual(0x03, self.rx.bitmask)
        self.rx._absorb(bytes.fromhex(STATUS_TX2_OFF))
        self.assertEqual(0x01, self.rx.bitmask)

    def test_a_stale_transmitter_is_dropped(self):
        self._tx("tx1", "B3MTP6J014889X", 2, seen_ago=dji.STALE_SECONDS + 1)
        self.assertEqual([], self.provider.poll())

    def test_an_unknown_gauge_is_reported_as_unknown_not_as_full(self):
        self._tx("tx1", "B3MTP6J014889X", 0)
        (status,) = self.provider.poll()
        self.assertIsNone(status.level)
        self.assertEqual("", status.approx)

    def test_a_gauge_change_moves_the_level(self):
        self._tx("tx1", "B3MTP6J014889X", 1)
        first = self.provider.poll()[0].level
        self._tx("tx1", "B3MTP6J014889X", 6)
        self.assertGreater(first, self.provider.poll()[0].level)


if __name__ == "__main__":
    unittest.main()
