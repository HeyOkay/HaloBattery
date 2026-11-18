"""Exact H9 II HID contract, fake receivers/native completions only.

Independent request bytes: zoneout 49ebedb, device.py139-167. Reply below is a
synthetic 64-byte status using documented offsets, NOT a captured owner packet.
No public successful packet capture was attached to #387; only level confirmed.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from providers import sony_h9ii as S


REQUEST = bytes.fromhex("02 0C 01 00 FC 08 96 C3 41 06 01 01 00 A2") + bytes(50)
STATUS = bytes.fromhex("02 00 00 00 00 00 96 C3 41 06 00 00 00 00 00 3C") + bytes(48)
CAPS = {"input": 64, "output": 64, "feature": 0}
DEFAULT_SENT = object()


def status(level=60, charge=0):
    data = list(STATUS)
    data[14], data[15] = charge, level
    return data


def entry(path=b"receiver", **changes):
    result = dict(vendor_id=0x054C, product_id=0x0FA8, interface_number=5,
                  usage_page=0xFF04, usage=1, path=path, serial_number="same")
    result.update(changes)
    return result


class Clock:
    now = 0.0
    def monotonic(self):
        return self.now
    def advance(self, milliseconds):
        self.now = round(self.now + milliseconds / 1000, 6)


class Device:
    def __init__(self, clock, answers, *, caps=CAPS, sent=DEFAULT_SENT, drain=False, error=False):
        self.clock, self.answers = clock, answers
        self.caps, self.sent, self.drain, self.error = caps, sent, drain, error
        self.writes, self.reads = [], []
        self.path, self.closed = None, False
        self.replies = []
        if caps is None:
            self.get_report_lengths = None  # legacy hidapi has no cached caps
    def open_path(self, path):
        self.path = path
        self.replies = list(self.answers.get(path, []))
    def get_report_lengths(self):
        return self.caps
    def read(self, size, timeout):
        self.reads.append((size, timeout))
        if self.error:
            raise OSError("receiver removed")
        if not self.writes:
            self.clock.advance(timeout)
            return STATUS if self.drain else []
        if self.replies:
            return self.replies.pop(0)
        self.clock.advance(timeout)
        return []
    def write(self, data):
        self.writes.append(bytes(data))
        if self.sent is not DEFAULT_SENT:
            return self.sent
        return self.caps["output"] if isinstance(self.caps, dict) else len(data)
    def close(self):
        self.closed = True


class ParseTests(unittest.TestCase):
    def test_literal_request_and_boundary_levels(self):
        self.assertEqual(S.QUERY, REQUEST)
        for level in (0, 60, 100):
            for charge in (0, 1):
                self.assertEqual(S.parse_status(status(level, charge)),
                                 (level, charge == 1, True))
        self.assertEqual(S.parse_status(status(60, 255)), (60, False, False))
        self.assertEqual(S.parse_status(status(60, 2)), (60, False, False))
    def test_short_garbage_types_and_invalid_percentages_are_refused(self):
        for data in (None, "x" * 64, {}, *[STATUS[:n] for n in range(64)], STATUS + b"\0"):
            self.assertIsNone(S.parse_status(data))
        for value in (-1, 101, 255, True, 60.0, "60", None):
            self.assertIsNone(S.parse_status(status(value)))
        for value in (-1, True, 0.0, "0", None):
            self.assertIsNone(S.parse_status(status(charge=value)))
    def test_report_magic_category_and_command_are_correlated(self):
        for offset, value in ((0, 3), (6, 0), (7, 0), (8, 0x14), (9, 4)):
            data = list(STATUS)
            data[offset] = value
            self.assertIsNone(S.parse_status(data))
        # No invented response checksum/sequence or full request-header echo.
        data = list(STATUS)
        data[11], data[13], data[22] = 77, 33, 55
        self.assertEqual(S.parse_status(data), (60, False, True))


class ProviderTests(unittest.TestCase):
    def run_poll(self, infos=None, answers=None, provider=None, clock=None, **options):
        infos = [entry()] if infos is None else infos
        answers = {b"receiver": [STATUS]} if answers is None else answers
        clock = clock or Clock()
        made = []
        def factory():
            dev = Device(clock, answers, **options)
            made.append(dev)
            return dev
        provider = provider or S.SonyH9IIProvider()
        with patch.object(S.hidlist, "enumerate", return_value=infos), \
                patch.object(S, "hid", SimpleNamespace(device=factory)), \
                patch.object(S, "time", clock):
            result = provider.poll()
        return result, made, provider, clock

    def test_exact_collection_and_only_literal_status_query(self):
        result, made, _, _ = self.run_poll(answers={b"receiver": [status(42, 1)]})
        self.assertEqual([(r.level, r.charging, r.charging_known, r.kind, r.source)
                          for r in result], [(42, True, True, "headset", "sony_h9ii")])
        self.assertEqual(made[0].writes, [REQUEST])
        self.assertTrue(made[0].closed)
        self.assertTrue(all(size == 64 for size, _ in made[0].reads))

    def test_unknown_ids_consumer_vendor_pages_interfaces_and_paths_never_open(self):
        variants = ({"vendor_id": 0x1234}, {"product_id": 0x0E53}, {"interface_number": 4},
                    {"interface_number": 5.0}, {"usage_page": 0xFF13}, {"usage_page": 0x000C},
                    {"usage": 2}, {"path": None}, {"path": b"\xff"}, {"path": "bad\0path"},
                    {"path": "\ud800"})
        for changes in variants:
            with self.subTest(changes=changes):
                result, made, _, _ = self.run_poll(infos=[entry(**changes)])
                self.assertEqual((result, made), ([], []))

    def test_invalid_cached_caps_are_rejected_before_read_or_write(self):
        for caps in ({}, {"input": 0, "output": 64}, {"input": 64, "output": 0},
                     {"input": 63, "output": 64}, {"input": 64, "output": 63},
                     {"input": 64.0, "output": 64}, {"input": 64, "output": True}, []):
            with self.subTest(caps=caps):
                result, made, _, _ = self.run_poll(caps=caps)
                self.assertEqual(result, [])
                self.assertEqual((made[0].reads, made[0].writes), ([], []))
                self.assertTrue(made[0].closed)

    def test_larger_collection_caps_and_legacy_keep_canonical_report(self):
        for caps in ({"input": 128, "output": 128, "feature": 65}, None):
            with self.subTest(caps=caps):
                result, made, _, _ = self.run_poll(caps=caps)
                self.assertEqual(result[0].level, 60)
                self.assertEqual(made[0].writes, [REQUEST])
                self.assertTrue(all(size == 64 for size, _ in made[0].reads))

    def test_short_or_refused_write_never_starts_reply_read(self):
        for sent in (None, 0, 1, 63, -1, True, "64", 64.0):
            with self.subTest(sent=sent):
                result, made, _, _ = self.run_poll(sent=sent)
                self.assertEqual(result, [])
                self.assertEqual(made[0].reads, [(64, 1)])
                self.assertTrue(made[0].closed)
        result, made, _, _ = self.run_poll(caps={"input": 128, "output": 128}, sent=64)
        self.assertEqual(result, [])  # native full padded write must complete
        self.assertEqual(made[0].reads, [(64, 1)])

    def test_drain_and_no_reply_are_bounded_and_close_handles(self):
        result, made, _, clock = self.run_poll(drain=True)
        self.assertEqual(result, [])
        self.assertEqual(len(made[0].reads), 32)
        self.assertEqual(made[0].writes, [])
        self.assertTrue(made[0].closed)
        result, made, _, clock = self.run_poll(answers={})
        self.assertEqual(result, [])
        self.assertLessEqual(len(made[0].reads), 41)
        self.assertLessEqual(clock.now, 1.001)
        self.assertTrue(made[0].closed)

    def test_bad_replies_are_skipped_and_disconnect_closes(self):
        bad = status(101)
        other = list(STATUS)
        other[9] = 7
        result, made, _, _ = self.run_poll(answers={b"receiver": [bad, other, status(0, 255)]})
        self.assertEqual((result[0].level, result[0].charging_known), (0, False))
        result, made, _, _ = self.run_poll(error=True)
        self.assertEqual(result, [])
        self.assertTrue(made[0].closed)

    def test_multiple_same_pid_receivers_have_independent_stable_path_keys(self):
        infos = [entry(b"one"), entry(b"two")]
        answers = {b"one": [status(10)], b"two": [status(90)]}
        result, _, provider, _ = self.run_poll(infos, answers)
        first = {r.level: r.key for r in result}
        result, _, _, _ = self.run_poll(list(reversed(infos)), answers, provider)
        self.assertEqual({r.level: r.key for r in result}, first)
        self.assertEqual(len(set(first.values())), 2)
        self.assertEqual(S.path_key(b"ONE"), S.path_key("one"))
        result, made, _, _ = self.run_poll([entry(b"one"), entry("ONE")], answers)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(made), 1)

    def test_silent_receivers_cannot_starve_later_receivers_across_polls(self):
        infos = [entry(str(n).encode()) for n in range(4)]
        result, made, provider, clock = self.run_poll(infos, answers={})
        first = {d.path for d in made}
        self.assertEqual(len(first), 3)
        self.assertLessEqual(clock.now, 3.0)
        missing = ({i["path"] for i in infos} - first).pop()
        result, made, _, _ = self.run_poll(infos, {missing: [status(77)]}, provider, clock)
        self.assertEqual(made[0].path, missing)
        self.assertEqual([r.level for r in result], [77])

    def test_factory_has_exactly64_and_serial_h9ii_stays_held(self):
        from providers import sony_inzone
        self.assertNotIn(0x0FA8, sony_inzone.ENABLED_PIDS)
        tree = ast.parse((Path(__file__).resolve().parents[1] / "halo_battery.pyw").read_text(encoding="utf-8"))
        factory = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "make_providers")
        result = next(n for n in ast.walk(factory) if isinstance(n, ast.Return))
        names = [n.func.id for n in result.value.elts]
        self.assertEqual(len(names), 64)
        self.assertEqual(names.count("SonyH9IIProvider"), 1)
        self.assertIn("SonyInzoneProvider", names)


class NativeContractTests(unittest.TestCase):
    def test_caps_preflight_avoids_invalid_parameter_and_larger_caps_preserve_report(self):
        from tests import test_experimental_hid as F
        from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
        class Api(F.Api):
            def __init__(self):
                super().__init__()
                self.read_sizes, self.ready = [], False
            def create_read(self, owner, size):
                self.read_sizes.append(size)
                request = F.Request(STATUS if self.ready else b"")
                self.ready = False
                self.requests.append(request)
                return request
            def create_write(self, owner, data):
                self.ready = True
                return super().create_write(owner, data)
        for caps in (ReportCaps(0, 64, 0), ReportCaps(64, 0, 0), ReportCaps(64, 64, 0),
                     ReportCaps(128, 128, 0)):
            with self.subTest(caps=caps):
                api = Api()
                runtime = TransportRuntime(api=api, timeout=.5)
                metadata = F.Metadata(caps)
                try:
                    provider = S.SonyH9IIProvider()
                    with patch.object(S, "hid", SimpleNamespace(
                            device=lambda: HidDevice(runtime, metadata=metadata))), \
                            patch.object(S.hidlist, "enumerate", return_value=[entry()]):
                        with runtime.provider_scope("sony-test") as scope:
                            result = provider.poll()
                    self.assertFalse(scope.failed, scope.error)
                    self.assertEqual(api.ioctls, [])
                    self.assertEqual(metadata.features, [])
                    if min(caps.input, caps.output) < 64:
                        self.assertEqual((result, api.read_sizes, api.writes), ([], [], []))
                    else:
                        self.assertEqual(result[0].level, 60)
                        self.assertTrue(all(size == caps.input for size in api.read_sizes))
                        self.assertEqual(api.writes, [REQUEST + bytes(caps.output - 64)])
                finally:
                    self.assertTrue(runtime.close(.5))


if __name__ == "__main__":
    unittest.main()
