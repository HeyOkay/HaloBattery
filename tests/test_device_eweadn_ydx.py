"""X23 Pro's official GET_FEATURE/input exchange. No hardware or writes."""
import types
import unittest
from unittest.mock import patch

from providers import eweadn_ydx as E


def report(level=70, charge=0, online=1):
    return [4, online, 0, 1, level, charge, 0, 0, 1, 0x11] + [0] * 22


def entry(collection, instance=b"first", **kwargs):
    path = (b"\\\\?\\hid#vid_04f3&pid_026f&mi_01&col0" + str(collection).encode()
            + b"#8&" + instance + b"&0&000" + str(collection - 1).encode() + b"#{hid}")
    return {"vendor_id": E.VID, "product_id": E.PID, "interface_number": 1,
            "usage_page": 10, "usage": 0, "path": path, **kwargs}


class FakeDevice:
    def __init__(self, owner):
        self.owner = owner
        self.path = None
        self.closed = False

    def open_path(self, path):
        self.path = path
        self.owner.opened.append(path)

    def get_report_descriptor(self):
        return (b"\x85\x04\x81\x02" if b"col03" in self.path
                else b"\x85\x05\xb1\x02\x85\x06\xb1\x02")

    def set_nonblocking(self, value):
        pass

    def get_feature_report(self, report_id, length):
        self.owner.queries.append((self.path, report_id, length))
        if self.owner.error:
            raise OSError("receiver unplugged")

    def read(self, length, timeout_ms=0):
        if not timeout_ms:
            return []
        return self.owner.replies.pop(0) if self.owner.replies else []

    def close(self):
        self.closed = True


class ParseTests(unittest.TestCase):
    def test_captured_report_and_bounds(self):
        for level in (0, 70, 100):
            self.assertEqual(E.parse_status(report(level)), (level, False, True, True))

    def test_charging_full_and_unknown(self):
        self.assertEqual(E.parse_status(report(charge=1)), (70, True, True, True))
        self.assertEqual(E.parse_status(report(charge=2)), (100, False, True, True))
        self.assertEqual(E.parse_status(report(charge=255)), (70, False, True, False))

    def test_offline_never_reports_cached_level(self):
        self.assertEqual(E.parse_status(report(255, 255, 0)), (None, False, False, False))

    def test_invalid_packets(self):
        for data in (None, [], report()[:10], report() + [0], [0] * 32,
                     [5] + report()[1:], report(101), report(255), report(online=2)):
            with self.subTest(data=data):
                self.assertIsNone(E.parse_status(data))


class PollTests(unittest.TestCase):
    def setUp(self):
        self.opened, self.queries, self.devices = [], [], []
        self.replies = [report()]
        self.error = False
        self.entries = [entry(3), entry(4)]

    def device(self):
        dev = FakeDevice(self)
        self.devices.append(dev)
        return dev

    def poll(self):
        with patch.object(E.hidlist, "enumerate", return_value=self.entries), \
                patch.object(E, "hid", types.SimpleNamespace(device=self.device)):
            result = E.EweadnYdxProvider().poll()
        self.assertTrue(all(dev.closed for dev in self.devices))
        return result

    def test_only_vendor_collections_and_get_feature_5(self):
        self.entries += [entry(1, usage_page=12), entry(5, usage_page=1),
                         entry(3, product_id=0x026E), entry(3, interface_number=0)]
        result = self.poll()
        self.assertEqual(len(result), 1)
        status = result[0]
        self.assertEqual((status.name, status.level, status.online, status.source, status.kind),
                         ("EWEADN X23 Pro", 70, True, "eweadn_ydx", "mouse"))
        self.assertTrue(status.wake_on_input)
        self.assertEqual(self.opened, [entry(3)["path"], entry(4)["path"]])
        self.assertEqual(self.queries, [(entry(4)["path"], 5, 32)])

    def test_online_offline_and_unknown_charge(self):
        for data, expected in ((report(online=0), (None, False, False)),
                               (report(charge=255), (70, True, False))):
            with self.subTest(data=data):
                self.replies = [data]
                status = self.poll()[0]
                self.assertEqual((status.level, status.online, status.charging_known), expected)

    def test_unplug_during_query_closes_both_handles(self):
        self.error = True
        status = self.poll()[0]
        self.assertIsNone(status.level)
        self.assertFalse(status.online)
        self.assertEqual(len(self.devices), 2)

    def test_open_failure(self):
        with patch.object(FakeDevice, "open_path", side_effect=OSError("busy")):
            self.assertIsNone(self.poll()[0].level)

    def test_missing_feature_collection_is_not_queried(self):
        self.entries = [entry(3)]
        self.assertIsNone(self.poll()[0].level)
        self.assertEqual(self.queries, [])

    def test_timeout_and_garbage_do_not_reuse_a_level(self):
        self.replies = [[0] * 32]
        with patch.object(E.time, "monotonic", side_effect=[0, 0, 2]):
            status = self.poll()[0]
        self.assertIsNone(status.level)
        self.assertFalse(status.online)

    def test_two_receivers_are_paired_by_instance_not_enumeration_order(self):
        self.entries = [entry(3), entry(4, b"second"), entry(4), entry(3, b"second")]
        self.replies = [report(70), report(90)]
        result = self.poll()
        self.assertEqual([s.level for s in result], [70, 90])
        self.assertNotEqual(result[0].key, result[1].key)
        self.assertEqual(self.opened, [entry(3)["path"], entry(4)["path"],
                                      entry(4, b"second")["path"], entry(3, b"second")["path"]])

    def test_no_receiver_or_enumeration_failure(self):
        self.entries = []
        self.assertEqual(self.poll(), [])
        with patch.object(E.hidlist, "enumerate", side_effect=OSError("unplugged")):
            self.assertEqual(E.EweadnYdxProvider().poll(), [])


if __name__ == "__main__":
    unittest.main()
