"""PR #406 captured status; native cached caps and legacy report-role isolation."""
import types
import unittest
from unittest.mock import patch

from providers import eweadn_ydx as E


# PR #406 ad77d41, actual owner's 70% input, report ID retained by HIDAPI.
CAPTURE = bytes.fromhex("04 01 00 01 46 00 00 00 01 11") + bytes(22)
# HID 1.11 short items: UsagePage/Usage/Collection, ReportID/Size/Count,
# main Input / Feature. Logical payload is 31 bytes, plus one report ID.
INPUT_DESCRIPTOR = bytes.fromhex("06 0a 00 09 00 a1 01 85 04 75 08 95 1f 81 02 c0")
FEATURE_DESCRIPTOR = bytes.fromhex(
    "06 0a 00 09 00 a1 01 75 08 95 1f 85 05 b1 02 85 06 b1 02 c0")
INPUT_CAPS = {"input": 32, "output": 0, "feature": 0}
FEATURE_CAPS = {"input": 0, "output": 0, "feature": 32}


def report(level=70, charge=0, online=1):
    data = list(CAPTURE)
    data[1], data[4], data[5] = online, level, charge
    return data


def entry(collection, instance=b"first", **changes):
    path = (b"\\\\?\\hid#vid_04f3&pid_026f&mi_01&col0" + str(collection).encode()
            + b"#8&" + instance + b"&0&000" + str(collection - 1).encode() + b"#{hid}")
    return {"vendor_id": 0x04F3, "product_id": 0x026F, "interface_number": 1,
            "usage_page": 10, "usage": 0, "path": path, **changes}


class ParseTests(unittest.TestCase):
    def test_capture_zero_hundred_and_charge_states(self):
        self.assertEqual(E.parse_status(CAPTURE), (70, False, True, True))
        self.assertEqual(E.parse_status(report(0)), (0, False, True, True))
        self.assertEqual(E.parse_status(report(100)), (100, False, True, True))
        self.assertEqual(E.parse_status(report(70, 1)), (70, True, True, True))
        self.assertEqual(E.parse_status(report(70, 2)), (100, False, True, True))
        self.assertEqual(E.parse_status(report(70, 255)), (70, False, True, False))

    def test_invalid_raw_level_never_becomes_full(self):
        for value in (101, 255, -1, True, 70.0, None, "70"):
            self.assertIsNone(E.parse_status(report(value, 2)))

    def test_offline_has_no_level_or_known_charging(self):
        self.assertEqual(E.parse_status(report(255, 255, 0)), (None, False, False, False))

    def test_truncated_wrong_id_link_and_bad_byte_types(self):
        for data in (None, [], *[CAPTURE[:n] for n in range(32)], CAPTURE + b"\x00",
                     [5] + list(CAPTURE[1:]), report(online=2), report(online=True),
                     report(charge=True), report(charge=-1)):
            with self.subTest(data=data):
                self.assertIsNone(E.parse_status(data))


class DescriptorTests(unittest.TestCase):
    def test_complete_input_and_feature_report_layouts(self):
        self.assertEqual(E.descriptor_role(INPUT_DESCRIPTOR), "input")
        self.assertEqual(E.descriptor_role(FEATURE_DESCRIPTOR), "feature")

    def test_reports_are_associated_by_global_id_instead_of_substrings(self):
        # ID4 exists but only ID7 has an Input item. Old substring matching
        # would incorrectly call this the status reader.
        self.assertIsNone(E.descriptor_role(bytes.fromhex(
            "85 04 75 08 95 1f 85 07 81 02")))
        self.assertIsNone(E.descriptor_role(bytes.fromhex(
            "85 05 75 08 95 1f 85 07 b1 02")))

    def test_split_main_items_and_global_push_pop(self):
        self.assertEqual(E.descriptor_role(bytes.fromhex(
            "85 04 75 08 95 10 81 02 a4 85 07 75 01 95 01 b4 95 0f 81 02")),
                         "input")

    def test_ambiguous_ids_lengths_output_and_malformed_items_fail_closed(self):
        descriptors = (
            INPUT_DESCRIPTOR + FEATURE_DESCRIPTOR,
            INPUT_DESCRIPTOR.replace(b"\x95\x1f", b"\x95\x1e"),
            FEATURE_DESCRIPTOR.replace(b"\x95\x1f", b"\x95\x20"),
            FEATURE_DESCRIPTOR + bytes.fromhex("85 07 b1 02"),
            INPUT_DESCRIPTOR + bytes.fromhex("91 02"),
            bytes.fromhex("85 04 75 08 95"), bytes.fromhex("fe 01 00 00"),
            INPUT_DESCRIPTOR + b"\x00", INPUT_DESCRIPTOR.replace(b"\x81\x02", b"\x80"),
            bytes.fromhex("b4"), bytes.fromhex("a4"), [True], bytes(4097),
        )
        for descriptor in descriptors:
            with self.subTest(descriptor=descriptor):
                self.assertIsNone(E.descriptor_role(descriptor))


class NativeDevice:
    """Deliberately has NO get_report_descriptor method."""
    def __init__(self, owner):
        self.owner, self.path, self.closed = owner, None, False
        self.nonblocking = False

    def open_path(self, path):
        self.path = path
        self.owner.events.append(("open", path))
        if self.owner.fail == "open":
            raise OSError("busy")

    def get_report_lengths(self):
        self.owner.events.append(("caps", self.path))
        return dict(self.owner.caps[self.path])

    def set_nonblocking(self, value):
        self.nonblocking = value
        self.owner.events.append(("nonblocking", self.path, value))

    def get_feature_report(self, report_id, length):
        self.owner.events.append(("get", self.path, report_id, length))
        if self.owner.fail == "get":
            raise OSError("disconnected")
        self.owner.queried.add(E.receiver_path(self.path))
        return [5] + [0] * 31

    def read(self, length, timeout_ms=0):
        self.owner.events.append(("read", self.path, timeout_ms))
        if self.owner.fail == "read":
            raise OSError("disconnected")
        if self.nonblocking:
            return self.owner.stale.pop(0) if self.owner.stale else []
        if E.receiver_path(self.path) not in self.owner.queried:
            raise AssertionError("input read preceded GET_FEATURE")
        return self.owner.replies.pop(0) if self.owner.replies else []

    def close(self):
        self.closed = True
        self.owner.events.append(("close", self.path))


class LegacyDevice(NativeDevice):
    get_report_lengths = None

    def get_report_descriptor(self):
        return self.owner.descriptors[self.path]


class PollTests(unittest.TestCase):
    def setUp(self):
        self.entries = [entry(3), entry(4)]
        self.caps = {entry(3)["path"]: INPUT_CAPS, entry(4)["path"]: FEATURE_CAPS}
        self.descriptors = {entry(3)["path"]: INPUT_DESCRIPTOR,
                            entry(4)["path"]: FEATURE_DESCRIPTOR}
        self.events, self.devices, self.stale = [], [], []
        self.replies, self.queried, self.fail = [CAPTURE], set(), None
        self.device_class = NativeDevice
        self.clock = 0.0
        def tick():
            self.clock += .01
            return self.clock
        for context in (
            patch.object(E, "hid", types.SimpleNamespace(device=self.device)),
            patch.object(E.hidlist, "enumerate", side_effect=lambda vid: self.entries),
            patch.object(E.time, "monotonic", side_effect=tick),
        ):
            context.start()
            self.addCleanup(context.stop)

    def device(self):
        dev = self.device_class(self)
        self.devices.append(dev)
        return dev

    def poll(self, provider=None):
        result = (provider or E.EweadnYdxProvider()).poll()
        self.assertTrue(all(dev.closed for dev in self.devices))
        return result

    def queries(self):
        return [event for event in self.events if event[0] == "get"]

    def test_native_cached_metadata_without_descriptor_support(self):
        status = self.poll()[0]
        self.assertEqual((status.name, status.level, status.charging, status.online,
                          status.charging_known, status.source, status.kind),
                         ("EWEADN X23 Pro", 70, False, True, True, "eweadn_ydx", "mouse"))
        self.assertTrue(status.wake_on_input)
        self.assertEqual(self.queries(), [("get", entry(4)["path"], 5, 32)])

    def test_legacy_complete_descriptor_fallback(self):
        self.device_class = LegacyDevice
        self.assertEqual(self.poll()[0].level, 70)
        self.assertEqual(self.queries(), [("get", entry(4)["path"], 5, 32)])

    def test_strict_target_gate_excludes_wired_other_vid_and_os_collections(self):
        for changes in ({"product_id": 0x026E}, {"vendor_id": 0x1234},
                        {"interface_number": 0}, {"usage_page": 1}, {"usage": 1}):
            self.entries = [entry(3, **changes), entry(4, **changes)]
            self.assertEqual(self.poll(), [])
        self.assertEqual(self.devices, [])

    def test_ambiguous_duplicate_or_missing_roles_never_query(self):
        cases = ([entry(3)], [entry(3), entry(4), entry(5)])
        for rows in cases:
            self.entries = rows
            self.assertIsNone(self.poll()[0].level)
        self.entries = [entry(3), entry(4)]
        self.caps[entry(4)["path"]] = INPUT_CAPS
        self.assertIsNone(self.poll()[0].level)
        self.assertEqual(self.queries(), [])

    def test_native_wrong_lengths_both_roles_or_output_never_query(self):
        for lengths in (
            {"input": 32, "output": 0, "feature": 32},
            {"input": 64, "output": 0, "feature": 0},
            {"input": 32, "output": 1, "feature": 0},
            {"input": True, "output": 0, "feature": 0},
            {"input": -1, "output": 0, "feature": 0},
        ):
            self.caps[entry(3)["path"]] = lengths
            self.assertIsNone(self.poll()[0].level)
        self.assertEqual(self.queries(), [])

    def test_invalid_native_caps_do_not_fall_back_to_legacy_descriptor(self):
        with patch.object(NativeDevice, "get_report_descriptor", create=True,
                          side_effect=AssertionError("must not read descriptor")):
            self.caps[entry(3)["path"]] = {"input": 64, "output": 0, "feature": 0}
            self.assertIsNone(self.poll()[0].level)
        self.assertEqual(self.queries(), [])

    def test_stale_queue_is_drained_before_feature_query(self):
        self.stale = [report(99), report(98)]
        self.assertEqual(self.poll()[0].level, 70)
        events = [event[0] for event in self.events]
        get_at = events.index("get")
        self.assertEqual(events[:get_at].count("read"), 3)
        self.assertIn("read", events[get_at + 1:])

    def test_continuously_filled_queue_is_bounded_and_never_queries(self):
        self.stale = [CAPTURE] * 40
        self.assertIsNone(self.poll()[0].level)
        self.assertEqual(len(self.stale), 8)
        self.assertEqual(self.queries(), [])

    def test_open_query_or_read_failure_closes_every_opened_handle(self):
        for failure in ("open", "get", "read"):
            self.fail = failure
            self.assertIsNone(self.poll()[0].level)

    def test_unknown_charge_remains_known_false_and_offline_drops_level(self):
        self.replies = [report(charge=255)]
        status = self.poll()[0]
        self.assertEqual((status.level, status.charging_known), (70, False))
        self.replies = [report(online=0)]
        status = self.poll()[0]
        self.assertEqual((status.level, status.online), (None, False))

    def test_silence_and_garbage_have_one_shared_bounded_poll_budget(self):
        self.replies = [[0] * 32]
        status = self.poll()[0]
        self.assertIsNone(status.level)
        self.assertFalse(status.online)
        self.assertLess(self.clock, 1.6)

    def test_two_interleaved_receivers_never_mix_collection_roles(self):
        self.entries = [entry(3), entry(4, b"second"), entry(4), entry(3, b"second")]
        self.caps[entry(3, b"second")["path"]] = INPUT_CAPS
        self.caps[entry(4, b"second")["path"]] = FEATURE_CAPS
        self.replies = [CAPTURE, report(90)]
        statuses = self.poll()
        self.assertEqual([status.level for status in statuses], [70, 90])
        self.assertNotEqual(statuses[0].key, statuses[1].key)
        self.assertEqual(self.queries(), [("get", entry(4)["path"], 5, 32),
                                          ("get", entry(4, b"second")["path"], 5, 32)])

    def test_silent_first_receiver_cannot_starve_ready_second_receiver(self):
        self.entries += [entry(3, b"second"), entry(4, b"second")]
        self.caps[entry(3, b"second")["path"]] = INPUT_CAPS
        self.caps[entry(4, b"second")["path"]] = FEATURE_CAPS
        self.clock = 0.0
        first = E.receiver_path(entry(3)["path"])
        def read(dev, length, timeout_ms=0):
            self.events.append(("read", dev.path, timeout_ms))
            if dev.nonblocking:
                return []
            self.assertIn(E.receiver_path(dev.path), self.queried)
            if E.receiver_path(dev.path) == first:
                # A silent native read actually spends its requested timeout.
                self.clock += timeout_ms / 1000
                return []
            return CAPTURE
        with patch.object(E.time, "monotonic", side_effect=lambda: self.clock), \
                patch.object(NativeDevice, "read", read):
            statuses = self.poll()
        self.assertEqual([(status.level, status.online) for status in statuses],
                         [(None, False), (70, True)])
        self.assertEqual(self.queries(), [("get", entry(4)["path"], 5, 32),
                                          ("get", entry(4, b"second")["path"], 5, 32)])
        self.assertGreater(self.clock, 0)
        self.assertLessEqual(self.clock, 1.501)

    def test_enumeration_duplicates_do_not_create_duplicate_roles(self):
        self.entries += [entry(3), entry(4)]
        self.assertEqual(self.poll()[0].level, 70)
        self.assertEqual(len(self.devices), 2)

    def test_setup_latency_rotates_progress_and_deferred_cache_is_not_fresh(self):
        self.entries += [entry(3, b"second"), entry(4, b"second")]
        self.caps[entry(3, b"second")["path"]] = INPUT_CAPS
        self.caps[entry(4, b"second")["path"]] = FEATURE_CAPS
        self.clock = 0.0
        first = E.receiver_path(entry(3)["path"])
        starts = []
        original_open = NativeDevice.open_path
        def slow_open(dev, path):
            starts.append(self.clock)
            self.clock = round(self.clock + .4, 6)
            original_open(dev, path)
        def read(dev, length, timeout_ms=0):
            self.events.append(("read", dev.path, timeout_ms))
            if dev.nonblocking:
                return []
            self.assertIn(E.receiver_path(dev.path), self.queried)
            return report(70 if E.receiver_path(dev.path) == first else 90)
        provider = E.EweadnYdxProvider()
        with patch.object(E.time, "monotonic", side_effect=lambda: self.clock), \
                patch.object(NativeDevice, "open_path", slow_open), \
                patch.object(NativeDevice, "read", read):
            observed = set()
            for _ in range(2):
                self.queried.clear()
                before, query_count, started = len(starts), len(self.queries()), self.clock
                statuses = self.poll(provider)
                current = [s for s in statuses if not s.polling_stale]
                self.assertEqual(len(current), 1)
                observed.add(current[0].level)
                self.assertEqual(len(self.queries()) - query_count, 1)
                self.assertTrue(all(t < started + 1.5 for t in starts[before:]))
                # One already-issued native open can finish after the protocol
                # deadline. Its own transport timeout is a separate bound.
                self.assertLessEqual(round(self.clock - started, 6), 1.6)
            self.assertEqual(observed, {70, 90})
            [cached] = [s for s in statuses if s.polling_stale]
            self.assertEqual(cached.level, 70)
            self.assertFalse(cached.online)
            self.assertFalse(cached.charging)
            self.assertFalse(cached.charging_known)
            self.assertFalse(cached.wake_on_input)
            self.assertFalse(provider._last[cached.key].polling_stale)

    def test_expired_budget_never_opens_or_triggers_feature(self):
        provider = E.EweadnYdxProvider()
        self.assertIs(provider._read(self.entries, -1), E.DEFERRED)
        self.assertEqual(self.devices, [])
        self.assertEqual(self.queries(), [])

    def test_late_status_cannot_turn_cached_level_into_a_fresh_observation(self):
        provider = E.EweadnYdxProvider()
        self.assertEqual(self.poll(provider)[0].level, 70)
        original_open = NativeDevice.open_path
        self.clock = 0.0
        def slow_open(dev, path):
            self.clock += .4
            original_open(dev, path)
        def late_read(dev, length, timeout_ms=0):
            if dev.nonblocking:
                return []
            # Simulate delayed completion/scheduling after the protocol budget,
            # without an operation exceeding the native1.5s timeout.
            self.clock += .8
            return report(90)
        with patch.object(E.time, "monotonic", side_effect=lambda: self.clock), \
                patch.object(NativeDevice, "open_path", slow_open), \
                patch.object(NativeDevice, "read", late_read):
            [status] = self.poll(provider)
        self.assertEqual(status.level, 70)
        self.assertTrue(status.polling_stale)
        self.assertFalse(status.charging_known)

    def test_cache_and_status_count_are_bounded_and_unplugged_keys_are_pruned(self):
        self.entries = [row for n in range(257)
                        for row in (entry(3, str(n).encode()), entry(4, str(n).encode()))]
        provider = E.EweadnYdxProvider()
        with patch.object(provider, "_read", return_value=(70, False, True, True)), \
                patch.object(E.time, "monotonic", return_value=0):
            statuses = self.poll(provider)
            self.assertEqual(len(statuses), 256)
            self.assertEqual(len(provider._last), 256)
            self.entries = []
            self.assertEqual(self.poll(provider), [])
            self.assertEqual(provider._last, {})

    def test_no_receiver_or_enumeration_failure(self):
        self.entries = []
        self.assertEqual(self.poll(), [])
        with patch.object(E.hidlist, "enumerate", side_effect=OSError("unplugged")):
            self.assertEqual(self.poll(), [])


from test_hide_rename import HideRenameTestCase, make_app  # noqa: E402


class DeferredAppTests(HideRenameTestCase):
    def test_budget_deferred_value_cannot_replay_active_history_or_alerts(self):
        provider = E.EweadnYdxProvider()
        entries = [entry(3), entry(4)]
        with patch.object(E.hidlist, "enumerate", return_value=entries), \
                patch.object(provider, "_read", return_value=(70, True, True, True)):
            [initial] = provider.poll()
        with patch.object(E.hidlist, "enumerate", return_value=entries), \
                patch.object(provider, "_read", return_value=E.DEFERRED):
            [deferred] = provider.poll()
        self.assertEqual((initial.key, initial.level), (deferred.key, deferred.level))
        app = make_app({"bluetooth": False})
        app.key_provider[initial.key] = "eweadn_ydx"
        app.apply([initial], fresh_keys={initial.key})
        app.full_state[initial.key] = "charging"
        with patch.object(app.history, "record") as record, \
                patch.object(app, "check_alert") as alert, \
                patch.object(app, "check_full") as full:
            # Even an incorrect caller-supplied fresh-key edge cannot turn a
            # deferred provider cache into a live observation.
            app.apply([deferred], fresh_keys={deferred.key})
        self.assertFalse(record.call_args.args[3])
        self.assertEqual(record.call_args.args[1], 70)
        alert.assert_not_called()
        full.assert_not_called()
        self.assertNotIn(initial.key, app.full_state)


if __name__ == "__main__":
    unittest.main()
