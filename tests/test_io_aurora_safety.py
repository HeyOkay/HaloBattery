"""IO Aurora endpoint/freshness regressions. Literal frames, fake HID only."""
from types import SimpleNamespace
import unittest
from unittest import mock

from providers import io_aurora as A


# #493 diagnostics-charging.txt lines 84-99: both real identities, interface 1 FF02:2.
RECEIVER = dict(vendor_id=0x3554, product_id=0xfb08, product_string="IO Aurora Receiver",
                interface_number=1, usage_page=0xff02, usage=2, path=b"receiver")
CABLE = dict(RECEIVER, product_id=0xfb09, product_string="IO Aurora", path=b"cable")


def frame(level=42, power=0):
    data = [8, 4, 0, 0, 0, 0, level, power, *([0] * 8)]
    return [*data, (0x55 - sum(data)) % 256]


class Bus:
    def __init__(self, before=None, after=None, *, caps=None, sent=17, feature=None, fail=None):
        self.before = {path: list(values) for path, values in (before or {}).items()}
        self.after = {path: list(values) for path, values in (after or {}).items()}
        self.caps, self.sent, self.feature, self.fail = caps, sent, feature, fail
        self.writes, self.opens, self.closes, self.features, self.cap_calls = [], [], [], [], []
        self.reads = []

    def device(self):
        bus = self

        class Device:
            def open_path(self, path):
                self.path, self.written = path, False
                bus.opens.append(path)
                if bus.fail == "open":
                    raise OSError("device disappeared")

            def read(self, size, timeout):
                bus.reads.append((self.path, self.written))
                if bus.fail == "read":
                    raise OSError("device disappeared")
                queue = (bus.after if self.written else bus.before).get(self.path, [])
                return queue.pop(0) if queue else []

            def write(self, payload):
                bus.writes.append((self.path, bytes(payload)))
                if bus.fail == "write":
                    raise OSError("device disappeared")
                self.written = True
                return bus.sent

            def get_feature_report(self, report, size):
                bus.features.append((report, size))
                return bus.feature or []

            def close(self):
                bus.closes.append(self.path)

        device = Device()
        if bus.caps is not None:
            def metadata():
                bus.cap_calls.append(device.path)
                return bus.caps
            device.get_report_lengths = metadata
        return device


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.provider = A.IoAuroraProvider()

    def poll(self, bus, descriptors=None):
        descriptors = [RECEIVER] if descriptors is None else descriptors
        with mock.patch.object(A, "hid", SimpleNamespace(device=bus.device)), \
                mock.patch.object(A, "hidlist", SimpleNamespace(enumerate=lambda vid: descriptors)), \
                mock.patch.object(A, "time", SimpleNamespace(time=lambda: self.now, sleep=lambda delay: None)):
            return self.provider.poll()

    def test_wrong_collection_or_identity_never_opens_or_writes(self):
        for changes in (dict(vendor_id=0x1234), dict(product_id=0xf510),
                        dict(product_string="IO Aurora clone"), dict(product_string="IO Aurora"),
                        dict(product_string=None), dict(product_string=42), dict(product_id=[]),
                        dict(path=None), dict(path=b""), dict(path=42),
                        dict(interface_number=0), dict(interface_number=2), dict(interface_number=True),
                        dict(usage_page=1, usage=2), dict(usage_page=0xff03), dict(usage=1),
                        dict(usage_page=True), dict(usage=True)):
            with self.subTest(changes=changes):
                bus = Bus()
                self.poll(bus, [dict(RECEIVER, **changes)])
                self.assertEqual(bus.opens, [])
                self.assertEqual(bus.writes, [])
        # Product strings must match each captured identity, not just contain the name.
        self.assertIsNone(A.candidate([dict(CABLE, product_string="IO Aurora Receiver")]))

    def test_each_captured_control_collection_is_selected_without_caps_probing(self):
        for item in (RECEIVER, CABLE):
            with self.subTest(item=item):
                self.assertIs(A.candidate([dict(item, usage_page=1, usage=2), item]), item)
        self.assertIsNone(A.candidate([RECEIVER, dict(RECEIVER, path=b"another-mouse")]))

    def test_old_queued_battery_is_discarded_and_does_not_refresh_last_sample(self):
        self.poll(Bus(after={b"receiver": [frame(73)]}))
        original = self.provider._last
        self.now += 10
        bus = Bus(before={b"receiver": [frame(42, 1)]})
        values = self.poll(bus)
        self.assertEqual((values[0].level, values[0].charging, values[0].online), (73, False, False))
        self.assertEqual(self.provider._last, original)
        self.assertEqual(bus.writes, [(b"receiver", bytes.fromhex("0804000000000000000000000000000049"))])
        self.assertTrue(values[0].wake_on_input)

    def test_only_post_write_reply_is_used_after_old_frame_is_drained(self):
        bus = Bus(before={b"receiver": [frame(42, 1)]}, after={b"receiver": [frame(73)]})
        values = self.poll(bus)
        self.assertEqual((values[0].level, values[0].charging, values[0].online), (73, False, True))

    def test_queue_must_reach_empty_before_any_query_or_feature_read(self):
        bus = Bus(before={b"receiver": [frame(42)] * 6})
        values = self.poll(bus)
        self.assertEqual(bus.writes, [])
        self.assertEqual(bus.features, [])
        self.assertIsNone(values[0].level)
        self.assertFalse(values[0].online)

    def test_current_cable_wins_without_opening_receiver(self):
        bus = Bus(before={b"receiver": [frame(42)]},
                  after={b"receiver": [frame(42)], b"cable": [frame(73, 1)]})
        values = self.poll(bus, [RECEIVER, CABLE])
        self.assertEqual((values[0].level, values[0].charging, values[0].online), (73, True, True))
        self.assertEqual(bus.opens, [b"cable"])

    def test_silent_cable_can_use_only_a_fresh_receiver_reply(self):
        bus = Bus(after={b"receiver": [frame(42)]})
        values = self.poll(bus, [RECEIVER, CABLE])
        self.assertEqual(values[0].level, 42)
        self.assertEqual(bus.opens, [b"cable", b"receiver"])

    def test_failed_or_incomplete_write_cannot_accept_input_or_feature(self):
        for sent in (-1, 0, 16, 18, None, True, 17.0):
            with self.subTest(sent=sent):
                bus = Bus(after={b"receiver": [frame(42)]}, sent=sent, feature=frame(99, 1))
                values = self.poll(bus)
                self.assertIsNone(values[0].level)
                self.assertFalse(values[0].online)
                self.assertEqual(bus.features, [])
                self.assertFalse(any(written for _, written in bus.reads))

    def test_native_padded_write_uses_only_cached_opened_handle_caps(self):
        caps = dict(input=128, output=128, feature=128)
        bus = Bus(after={b"receiver": [frame(42)]}, caps=caps, sent=128)
        self.assertEqual(self.poll(bus)[0].level, 42)
        self.assertEqual(bus.cap_calls, [b"receiver"])
        self.assertEqual(bus.writes, [(b"receiver", bytes.fromhex("0804000000000000000000000000000049"))])
        for sent in (17, 127, 129):
            failed = Bus(after={b"receiver": [frame(99)]}, caps=caps, sent=sent)
            self.assertFalse(self.poll(failed)[0].online)

    def test_invalid_cached_caps_prevent_any_request(self):
        for caps in ({}, dict(input=17, output=0, feature=17), dict(input=16, output=17, feature=17),
                     dict(input=17, output=16, feature=17), dict(input=17, output=17, feature=1),
                     dict(input=17, output=True, feature=17), dict(input=17, output=17, feature="17"),
                     dict(input=17, output=32769, feature=17)):
            with self.subTest(caps=caps):
                bus = Bus(caps=caps)
                self.poll(bus)
                self.assertEqual(bus.writes, [])
                self.assertEqual(bus.reads, [])
                self.assertEqual(bus.closes, [b"receiver"])

    def test_feature_fallback_after_successful_query_uses_cached_capacity(self):
        caps = dict(input=128, output=128, feature=128)
        bus = Bus(caps=caps, sent=128, feature=frame(42) + [0] * 111)
        self.assertEqual(self.poll(bus)[0].level, 42)
        self.assertEqual(bus.features, [(8, 128)])
        no_feature = Bus(caps=dict(input=17, output=17, feature=0), feature=frame(99))
        self.assertFalse(self.poll(no_feature)[0].online)
        self.assertEqual(no_feature.features, [])

    def test_literal_boundaries_and_known_charge_states(self):
        for level in (0, 100):
            for power in (0, 1, 2):
                with self.subTest(level=level, power=power):
                    status = self.poll(Bus(after={b"receiver": [frame(level, power)]}))[0]
                    self.assertEqual((status.level, status.charging, status.charging_known),
                                     (level, power != 0, True))

    def test_unknown_power_is_not_an_invented_charging_state(self):
        status = self.poll(Bus(after={b"receiver": [frame(42, 3)]}))[0]
        self.assertEqual((status.level, status.charging, status.charging_known), (42, False, False))
        cached = self.poll(Bus())[0]
        self.assertEqual((cached.level, cached.online, cached.charging_known), (42, False, False))

    def test_malformed_frames_never_become_readings(self):
        good = frame(42)
        malformed = [None, [], good[:1], good[:-1], frame(101), frame(255), frame(42, 4),
                     frame(42, 255), [9] + good[1:], [8, 3] + good[2:], good[:-1] + [0],
                     [8, 4, True] + good[3:], [8, 4, -1] + good[3:], [8, 4, 1.0] + good[3:]]
        for reply in malformed:
            with self.subTest(reply=reply):
                status = self.poll(Bus(after={b"receiver": [reply]}))[0]
                self.assertFalse(status.online)
                self.assertIsNone(status.level)

    def test_disappearance_does_not_refresh_saved_reading_or_leak_open_handle(self):
        self.poll(Bus(after={b"receiver": [frame(73)]}))
        original = self.provider._last
        self.now += 10
        for failure in ("read", "write", "open"):
            with self.subTest(failure=failure):
                bus = Bus(fail=failure)
                status = self.poll(bus)[0]
                self.assertEqual((status.level, status.online), (73, False))
                self.assertEqual(self.provider._last, original)
                if failure != "open":
                    self.assertEqual(bus.closes, [b"receiver"])
        self.now += 300
        self.assertIsNone(self.poll(Bus())[0].level)
        self.assertEqual(self.provider._last, original)


class NativeContractTests(unittest.TestCase):
    def test_cached_native_caps_pad_the_write_and_do_not_probe_live_caps(self):
        from tests import test_experimental_hid as F
        from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime

        class Api(F.Api):
            def __init__(self):
                super().__init__()
                self.read_sizes, self.ready = [], False

            def create_read(self, owner, size):
                self.read_sizes.append(size)
                request = F.Request(bytes(frame(42)) if self.ready else b"")
                self.ready = False
                self.requests.append(request)
                return request

            def create_write(self, owner, data):
                self.ready = True
                return super().create_write(owner, data)

        for caps in (ReportCaps(0, 17, 0), ReportCaps(17, 0, 0),
                     ReportCaps(17, 17, 0), ReportCaps(128, 128, 0)):
            with self.subTest(caps=caps):
                api = Api()
                runtime = TransportRuntime(api=api, timeout=.5)
                metadata = F.Metadata(caps)
                try:
                    provider = A.IoAuroraProvider()
                    with mock.patch.object(A, "hid", SimpleNamespace(
                            device=lambda: HidDevice(runtime, metadata=metadata))), \
                            mock.patch.object(A.hidlist, "enumerate", return_value=[RECEIVER]):
                        with runtime.provider_scope("aurora-test") as scope:
                            values = provider.poll()
                    self.assertFalse(scope.failed, scope.error)
                    self.assertEqual(api.ioctls, [])
                    self.assertEqual(metadata.features, [])
                    if min(caps.input, caps.output) < 17:
                        self.assertEqual(api.writes, [])
                        self.assertEqual(api.read_sizes, [])
                        self.assertFalse(values[0].online)
                    else:
                        self.assertEqual(values[0].level, 42)
                        self.assertTrue(all(size == caps.input for size in api.read_sizes))
                        expected = bytes.fromhex("0804000000000000000000000000000049")
                        self.assertEqual(api.writes, [expected + bytes(caps.output - 17)])
                finally:
                    self.assertTrue(runtime.close(.5))


if __name__ == "__main__":
    unittest.main()
