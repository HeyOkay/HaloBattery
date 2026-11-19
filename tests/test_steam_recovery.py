"""Recover PR #265's USB/multi-puck intent without adding worker threads.

Literal reports use the documented 43/01/5f and 43/04/64 prefixes from #58,
padded to SDL's 15-byte battery structure; no provider constants build replies.
"""
import unittest
from unittest import mock

from providers import steam


AWAKE = bytes.fromhex("43 01 5f 00 00 00 00 00 00 00 00 00 00 00 00")
CHARGED = bytes.fromhex("43 04 64 00 00 00 00 00 00 00 00 00 00 00 00")
STATE = bytes.fromhex("42 00 00 00")


def endpoint(instance, *, pid=0x1304, iface=2, page=0xFF00, usage=1, vid=0x28DE):
    return dict(vendor_id=vid, product_id=pid, interface_number=iface,
                usage_page=page, usage=usage, serial_number="duplicate-placeholder",
                path=(f"\\\\?\\hid#vid_{vid:04x}&pid_{pid:04x}&mi_{iface:02x}"
                      f"#{instance}&0000#{{4d1e55b2-f16f-11cf-88cb-001111000030}}").encode())


class Clock:
    now = 1000.0
    def monotonic(self):
        return self.now
    def sleep(self, duration):
        self.now += duration


class Device:
    def __init__(self, owner, frames=(), *, endless=False, nonblocking_error=False, open_error=False, open_delay=0):
        self.owner, self.frames = owner, list(frames)
        self.endless, self.nonblocking_error, self.open_error = endless, nonblocking_error, open_error
        self.closed, self.reads, self.path = False, 0, None
        self.open_delay = open_delay
    def open_path(self, path):
        self.path = path
        self.owner.clock.now += self.open_delay
        if self.open_error:
            raise OSError("disappeared while opening")
    def set_nonblocking(self, enabled):
        if self.nonblocking_error:
            raise OSError("cannot make read nonblocking")
    def read(self, size):
        self.reads += 1
        self.owner.events.append((self.path, self.owner.clock.now))
        frame = self.frames.pop(0) if self.frames else STATE if self.endless else b""
        if isinstance(frame, Exception):
            raise frame
        if frame:
            self.owner.clock.now += 0.004
        return frame
    def close(self):
        self.closed = True
    def write(self, *args):
        raise AssertionError("Steam recovery must remain listen-only")
    send_feature_report = write


class SteamRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.events = Clock(), []
        self.provider = steam.SteamProvider()
        patch = mock.patch.object(steam, "time", self.clock)
        patch.start(); self.addCleanup(patch.stop)

    def poll(self, entries, devices):
        # The provider sorts endpoint identities; the fake maps by opened path,
        # not enumeration position or a provider's protocol constants.
        class MappedDevice:
            def open_path(inner, path):
                inner.device = devices[path]
                inner.device.open_path(path)
            def __getattr__(inner, name):
                return getattr(inner.device, name)
        with mock.patch.object(steam.hidlist, "enumerate", return_value=entries), \
                mock.patch.object(steam.hid, "device", side_effect=MappedDevice), \
                mock.patch("threading.Thread", side_effect=AssertionError("provider created a thread")):
            return self.provider.poll()

    def test_wired_usb_uses_only_known_vendor_collection_and_preserves_protocol(self):
        usb = endpoint("usb", pid=0x1302, iface=0)
        refused = [endpoint("bt", pid=0x1303, iface=-1), endpoint("old", pid=0x1142),
                   endpoint("unknown", pid=0x9999), endpoint("other-vendor", vid=0x1234),
                   endpoint("control", pid=0x1302, iface=0, usage=2),
                   endpoint("keyboard", pid=0x1302, iface=0, page=1, usage=6),
                   endpoint("bt-interface", pid=0x1302, iface=-1),
                   dict(endpoint("bt-bus", pid=0x1302, iface=0), bus_type=2)]
        devices = {item["path"]: Device(self, [AWAKE]) for item in [usb] + refused}
        result = self.poll([usb] + refused, devices)
        self.assertEqual([(s.level, s.charging, s.online, s.source, s.via) for s in result],
                         [(95, False, True, "steam", "usb")])
        self.assertTrue(devices[usb["path"]].closed)
        self.assertTrue(all(devices[item["path"]].reads == 0 for item in refused))
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_same_model_same_slot_and_duplicate_serials_remain_independent_and_stable(self):
        a, b = endpoint("puck-a"), endpoint("puck-b")
        def observe(entries):
            return self.poll(entries, {a["path"]: Device(self, [AWAKE]),
                                       b["path"]: Device(self, [CHARGED])})
        first = {s.key: (s.level, s.charging) for s in observe([a, b])}
        self.assertEqual(len(first), 2)
        self.assertEqual(sorted(first.values()), [(95, False), (100, True)])
        self.assertEqual({s.key: (s.level, s.charging) for s in observe([b, a])}, first)
        remaining = observe([a])[0]
        self.assertEqual((remaining.level, remaining.charging), first[remaining.key])
        self.assertEqual(self.provider.legacy_key_aliases(), {})
        self.assertEqual(set(self.provider._last), {remaining.key})

    def test_singleton_legacy_alias_is_cached_copied_and_retires_after_ambiguity(self):
        a, b = endpoint("puck-a"), endpoint("puck-b")
        result = self.poll([a], {a["path"]: Device(self, [AWAKE])})
        aliases = self.provider.legacy_key_aliases()
        self.assertEqual(aliases, {"steam:1304:2": result[0].key})
        aliases.clear()
        self.assertTrue(self.provider.legacy_key_aliases())
        self.poll([a, b], {a["path"]: Device(self, [AWAKE]), b["path"]: Device(self, [CHARGED])})
        self.assertEqual(self.provider.legacy_key_aliases(), {})
        self.poll([a], {a["path"]: Device(self, [AWAKE])})
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_live_slots_share_one_window_instead_of_accumulating_five_seconds_each(self):
        entries = [endpoint("puck", iface=iface) for iface in (2, 3, 4, 5)]
        devices = {item["path"]: Device(self, [STATE]) for item in entries}
        started = self.clock.now
        result = self.poll(entries, devices)
        self.assertEqual(len(result), 4)
        self.assertTrue(all(s.level is None and s.online for s in result))
        self.assertLessEqual(self.clock.now - started, 5.02)
        self.assertTrue(all(dev.closed for dev in devices.values()))

    def test_busy_slot_does_not_delay_another_slots_ready_battery(self):
        a, b = endpoint("a"), endpoint("b")
        busy, ready = Device(self, endless=True), Device(self, [CHARGED])
        started = self.clock.now
        result = self.poll([a, b], {a["path"]: busy, b["path"]: ready})
        self.assertEqual(sorted(s.level for s in result if s.level is not None), [100])
        first_ready = next(at for path, at in self.events if path == b["path"])
        self.assertLess(first_ready - started, 0.01)
        self.assertEqual(ready.reads, 1)
        self.assertLessEqual(self.clock.now - started, 5.02)
        self.assertTrue(busy.closed and ready.closed)

    def test_nonblocking_setup_or_open_failure_closes_handle_and_keeps_other_reading(self):
        for failure in ("nonblocking_error", "open_error"):
            with self.subTest(failure=failure):
                a, b = endpoint("failed"), endpoint("healthy")
                failed, healthy = Device(self, [AWAKE], **{failure: True}), Device(self, [CHARGED])
                result = self.poll([a, b], {a["path"]: failed, b["path"]: healthy})
                self.assertEqual([s.level for s in result], [100])
                self.assertEqual(failed.reads, 0)
                self.assertTrue(failed.closed and healthy.closed)

    def test_opening_other_handles_does_not_discard_already_queued_battery(self):
        a, b = endpoint("a"), endpoint("b")
        devices = {a["path"]: Device(self, [AWAKE], open_delay=0.3),
                   b["path"]: Device(self, [CHARGED], open_delay=0.3)}
        started = self.clock.now
        result = self.poll([a, b], devices)
        self.assertEqual(sorted(s.level for s in result), [95, 100])
        self.assertLess(self.clock.now - started, 1.0)
        self.assertTrue(all(dev.closed for dev in devices.values()))

    def test_malformed_battery_fields_are_refused_without_clamping(self):
        for bad in (None, "43 01 5f", [], list(AWAKE[:14]),
                    [0x43, 1, -1] + [0] * 12, [0x43, 1, 101] + [0] * 12,
                    [0x43, True, 95] + [0] * 12, [0x43, 1, True] + [0] * 12,
                    [0x43, 3, 95] + [0] * 12):
            with self.subTest(report=bad):
                self.assertIsNone(steam.parse_battery(bad))

    def test_disappearance_during_read_keeps_only_a_grey_last_known_value(self):
        item = endpoint("puck")
        first = self.poll([item], {item["path"]: Device(self, [AWAKE])})[0]
        failed = Device(self, [STATE, OSError("unplugged during read")])
        result = self.poll([item], {item["path"]: failed})
        self.assertEqual([(s.key, s.level, s.online, s.charging) for s in result],
                         [(first.key, 95, False, False)])
        self.assertTrue(failed.closed)

    def test_report_budget_bounds_a_continuous_stream_and_keeps_ready_slot(self):
        a, b = endpoint("busy"), endpoint("ready")
        busy, ready = Device(self, endless=True), Device(self, [CHARGED])
        with mock.patch.object(steam, "MAX_REPORTS", 3):
            result = self.poll([a, b], {a["path"]: busy, b["path"]: ready})
        self.assertEqual(busy.reads, 3)
        self.assertEqual(ready.reads, 1)
        self.assertEqual(sorted(s.level for s in result if s.level is not None), [100])
        self.assertTrue(busy.closed and ready.closed)

    def test_case_only_path_changes_and_changed_serial_do_not_change_endpoint_key(self):
        item = endpoint("puck")
        first = self.poll([item], {item["path"]: Device(self, [AWAKE])})[0]
        changed = dict(item, path=item["path"].upper(), serial_number=None)
        current = self.poll([changed], {changed["path"]: Device(self, [CHARGED])})[0]
        self.assertEqual(current.key, first.key)
        self.assertEqual((current.level, current.charging), (100, True))

    def test_slot_capacity_bounds_cache_to_selected_endpoints(self):
        entries = [endpoint(f"puck-{i}") for i in range(17)]
        devices = {item["path"]: Device(self, [AWAKE]) for item in entries}
        all_keys = {f"steam:1304:2:path-{steam.SteamProvider._endpoint(item['path'])}" for item in entries}
        # Simulate last readings retained before a deterministic selection change.
        self.provider._last = dict.fromkeys(all_keys, (95, False))
        values = self.poll(list(reversed(entries)), devices)
        self.assertEqual(len(values), 16)
        self.assertEqual(set(self.provider._last), {value.key for value in values})
        self.assertEqual(sum(device.reads > 0 for device in devices.values()), 16)
        self.assertEqual(self.provider.legacy_key_aliases(), {})
