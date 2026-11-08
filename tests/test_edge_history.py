"""Edge cases for halo/history.py: odd clocks, odd readings, damaged history files.

The estimate feeds the tray tooltip, so a bad input must give "no estimate" and never
an exception or a wrong number. No hardware, no real history file.

Run from the repository root:

    python -m unittest discover -s tests
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers
import _sandbox  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from halo import history  # noqa: E402
from halo.history import History  # noqa: E402

K = "dev"


def drain(h, start_level=80, steps=10, step_s=600.0, t0=1000.0, key=K):
    """One reading every `step_s` seconds, one percentage point lower each time."""
    for i in range(steps + 1):
        h.record(key, start_level - i, False, True, t0 + i * step_s)
    return t0 + steps * step_s


class EstimateTests(unittest.TestCase):
    def test_single_sample_has_no_estimate(self):
        h = History()
        h.record(K, 50, False, True, 1000.0)
        self.assertIsNone(h.rate(K))
        self.assertIsNone(h.seconds_left(K, 50))

    def test_unknown_device_has_no_estimate(self):
        self.assertIsNone(History().rate("nobody"))
        self.assertIsNone(History().seconds_left("nobody", 50))

    def test_two_samples_at_the_same_instant_have_no_estimate(self):
        h = History()
        h.record(K, 50, False, True, 1000.0)
        h.record(K, 49, False, True, 1000.0)
        self.assertIsNone(h.rate(K))

    def test_steady_drain_gives_the_exact_rate(self):
        h = History()
        drain(h)                                   # 1 point every 600 s, 80 -> 70
        self.assertAlmostEqual(h.rate(K), 1 / 600.0, places=9)
        self.assertAlmostEqual(h.seconds_left(K, 70), 70 * 600.0, delta=1)

    def test_no_estimate_before_enough_use_or_drop(self):
        h = History()
        drain(h, steps=2)                          # 1200 s < MIN_SPAN
        self.assertIsNone(h.rate(K))
        h = History()
        for i in range(10):                        # long enough, but only 2 points lower (< MIN_DROP)
            h.record(K, 80 - (i >= 5) - (i >= 8), False, True, 1000.0 + i * 600.0)
        self.assertIsNone(h.rate(K))

    def test_level_none_and_coarse_readings_are_ignored(self):
        h = History()
        h.record(K, None, False, True, 1000.0)
        h.record(K, 50, False, True, 1000.0, coarse=True)
        self.assertEqual(h.devices, {})
        self.assertFalse(h.dirty)

    def test_seconds_left_with_unknown_or_empty_level(self):
        h = History()
        drain(h)
        self.assertIsNone(h.seconds_left(K, None))
        self.assertEqual(h.seconds_left(K, 0), 0.0)
        self.assertEqual(history.format_left(h.seconds_left(K, 0)), "no use left")
        self.assertGreater(h.seconds_left(K, 100), h.seconds_left(K, 1))

    def test_a_level_that_sits_still_slows_the_estimate(self):
        h = History()
        end = drain(h)
        before = h.seconds_left(K, 70)
        for i in range(1, 20):                     # 3 more hours at 70 %
            h.record(K, 70, False, True, end + i * 600.0)
        self.assertGreater(h.seconds_left(K, 70), before)


class ClockTests(unittest.TestCase):
    def test_clock_going_backwards_adds_no_use_and_does_not_raise(self):
        h = History()
        h.record(K, 80, False, True, 5000.0)
        h.record(K, 79, False, True, 4000.0)       # the clock jumped back 1000 s
        self.assertEqual(h.devices[K]["use"], 0.0)
        h.record(K, 78, False, True, 4300.0)       # ...and time continues from the new clock
        self.assertEqual(h.devices[K]["use"], 300.0)
        self.assertGreaterEqual(h.devices[K]["use"], 0)

    def test_estimate_survives_a_clock_jump_back(self):
        h = History()
        end = drain(h)
        h.record(K, 69, False, True, end - 86400.0)
        rate = h.rate(K)
        self.assertTrue(rate is None or rate > 0)

    def test_a_long_gap_counts_as_at_most_max_gap(self):
        h = History()
        h.record(K, 80, False, True, 0.0)
        h.record(K, 79, False, True, 30 * 86400.0)          # the PC slept for a month
        self.assertEqual(h.devices[K]["use"], history.MAX_GAP)

    def test_offline_time_does_not_count(self):
        h = History()
        h.record(K, 80, False, True, 0.0)
        h.record(K, 80, False, False, 300.0)                # asleep
        h.record(K, 79, False, True, 900.0)                 # awake again: the gap is not use
        self.assertEqual(h.devices[K]["use"], 0.0)


class LevelTests(unittest.TestCase):
    def test_small_rise_without_charging_is_jitter(self):
        h = History()
        h.record(K, 70, False, True, 0.0)
        h.record(K, 70 + history.RESET_RISE - 1, False, True, 60.0)
        self.assertEqual(h.devices[K]["samples"], [[0.0, 70]])
        h.record(K, 69, False, True, 120.0)
        self.assertEqual([s[1] for s in h.devices[K]["samples"]], [70, 69])

    def test_big_rise_without_charging_starts_over(self):
        h = History()
        end = drain(h)
        self.assertGreater(len(h.devices[K]["samples"]), 2)
        h.record(K, 100, False, True, end + 60.0)           # charged while the app was closed
        self.assertEqual(h.devices[K]["samples"], [[0.0, 100]])
        self.assertEqual(h.devices[K]["use"], 0.0)
        self.assertIsNone(h.rate(K))

    def test_charging_clears_the_history_and_the_estimate(self):
        h = History()
        end = drain(h)
        h.record(K, 71, True, True, end + 60.0)
        self.assertEqual(h.devices[K]["samples"], [])
        self.assertIsNone(h.rate(K))
        self.assertIsNone(h.devices[K]["last"])

    def test_first_ever_reading_while_charging_starts_empty(self):
        h = History()
        h.record(K, 40, True, True, 0.0)
        self.assertEqual(h.devices[K]["samples"], [])

    def test_levels_out_of_range_do_not_raise(self):
        h = History()
        for i, level in enumerate((50, 0, 100, -5, 255, 100, 0)):
            h.record(K, level, False, True, 1000.0 + i * 600.0)
        rate = h.rate(K)
        self.assertTrue(rate is None or rate > 0)

    def test_sample_list_is_bounded(self):
        h = History()
        for i in range(history.MAX_SAMPLES * 2):
            h.record(K, 1000 - i, False, True, 1000.0 + i * 10.0)
        self.assertEqual(len(h.devices[K]["samples"]), history.MAX_SAMPLES)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = _sandbox.mkdtemp(prefix="halo_history_")
        self.path = os.path.join(self.dir, "history.json")

    def load(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        h = History(self.path)
        h.load()
        return h

    def test_unreadable_files_give_an_empty_history(self):
        for name, text in (("empty", ""), ("not json", "{oops"), ("array", "[]"), ("null", "null"),
                           ("number", "5"), ("no devices", "{}"), ("null devices", '{"devices": null}'),
                           ("list devices", '{"devices": []}'), ("binary-ish", "\x00\x01")):
            with self.subTest(file=name):
                self.assertEqual(self.load(text).devices, {})

    def test_devices_of_the_wrong_type_give_an_empty_history(self):
        # a hand-edited or damaged file must not stop the app from starting
        for name, text in (("list", '{"devices": [1]}'), ("string", '{"devices": "abc"}'),
                           ("number", '{"devices": 5}'), ("true", '{"devices": true}')):
            with self.subTest(devices=name):
                self.assertEqual(self.load(text).devices, {})

    def test_damaged_entries_are_dropped_and_good_ones_kept(self):
        good = {"use": 10, "seen": time.time(), "samples": [[0, 80], [10, 79]]}
        bad_entries = {"null": None, "list": [], "string": "x", "short sample": {"samples": [[1]]},
                       "text sample": {"samples": [["a", 1]]}, "null samples": {"samples": None},
                       "text use": {"use": "x"}, "null seen": {"seen": None},
                       "infinite level": {"samples": [[1, float("inf")]]},
                       "samples not a list": {"samples": 5}}
        for name, entry in bad_entries.items():
            with self.subTest(entry=name):
                text = json.dumps({"devices": {"bad": entry, "good": good}})
                h = self.load(text)
                self.assertNotIn("bad", h.devices)
                self.assertEqual(h.devices["good"]["samples"], [[0.0, 80], [10.0, 79]])
                self.assertIsNone(h.devices["good"]["last"])

    def test_stale_devices_are_forgotten(self):
        now = time.time()
        text = json.dumps({"devices": {"old": {"seen": now - history.FORGET_AFTER - 10, "samples": []},
                                       "new": {"seen": now - 10, "samples": []},
                                       "unseen": {"samples": []}}})
        self.assertEqual(sorted(self.load(text).devices), ["new", "unseen"])

    def test_loaded_samples_are_capped(self):
        samples = [[i, 1000 - i] for i in range(history.MAX_SAMPLES + 50)]
        h = self.load(json.dumps({"devices": {K: {"samples": samples, "seen": time.time()}}}))
        self.assertEqual(len(h.devices[K]["samples"]), history.MAX_SAMPLES)
        self.assertEqual(h.devices[K]["samples"][-1], [float(samples[-1][0]), samples[-1][1]])

    def test_missing_file_and_missing_path(self):
        h = History(os.path.join(self.dir, "absent.json"))
        h.load()
        self.assertEqual(h.devices, {})
        History().load()                             # no path at all: nothing happens

    def test_save_and_load_round_trip(self):
        h = History(self.path)
        drain(h, t0=time.time() - 7000)
        h.save(force=True)
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        again = History(self.path)
        again.load()
        self.assertEqual(again.devices[K]["samples"], h.devices[K]["samples"])
        self.assertAlmostEqual(again.rate(K), h.rate(K), places=9)

    def test_save_is_throttled_unless_forced_and_only_when_dirty(self):
        h = History(self.path)
        h.save()
        self.assertFalse(os.path.exists(self.path))          # nothing changed: nothing written
        h.record(K, 50, False, True, 0.0)
        h.record(K, 49, False, True, 100.0)
        h.save()
        self.assertTrue(os.path.exists(self.path))
        os.remove(self.path)
        h.record(K, 48, False, True, 200.0)
        h.save()                                              # within SAVE_EVERY of the last write
        self.assertFalse(os.path.exists(self.path))
        h.save(force=True)
        self.assertTrue(os.path.exists(self.path))

    def test_failed_save_does_not_raise_and_stays_dirty(self):
        h = History(os.path.join(self.dir, "no", "such", "folder", "history.json"))
        h.record(K, 50, False, True, 0.0)
        h.record(K, 49, False, True, 100.0)
        h.save(force=True)
        self.assertTrue(h.dirty)

    def test_report_lines(self):
        self.assertEqual(History().report(), ["(no history yet)"])
        h = History()
        drain(h)
        (line,) = h.report()
        self.assertIn(f"[{K}]", line)
        self.assertIn("%/h of use", line)
        h = History()
        h.record(K, 50, False, True, 0.0)
        self.assertIn("no estimate yet", h.report()[0])


if __name__ == "__main__":
    unittest.main()
