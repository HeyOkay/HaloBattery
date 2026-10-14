"""Tests for providers/turtlebeach.py. No hardware is needed.

A fake transmitter answers the CoAP GET /GSI with a framed JSON body, the way a
Stealth 700 Gen 3 does over its vendor collection. time.sleep is replaced so the
test counts reads rather than wall-clock time.

Run from the repository root:

    python -m unittest discover -s tests
"""
import json as _json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import turtlebeach as T  # noqa: E402

PATH = b"\\\\?\\hid#vid_10f5&pid_2251&mi_03&col02#x"


def info(product="Stealth 700X Gen 3", pid=0x2251, usage_page=0xFF13):
    return {"vendor_id": T.VENDOR_ID, "product_id": pid, "usage_page": usage_page,
            "usage": 0x0001, "path": PATH, "product_string": product}


def gsi_reports(kvp):
    """Frame a GSI JSON reply as the sequence of input reports the provider reads."""
    body = ('{"OR":"GSI","KVP":' + _json.dumps(kvp) + "}").encode()
    payload = b"\x05\x5b\x00\x00\x02\x99\xff" + body    # RACE/CoAP wrapper + 0xFF marker
    reports = []
    for i in range(0, len(payload), 59):
        chunk = payload[i:i + 59]
        rep = [0x07, len(chunk) & 0xFF, (len(chunk) >> 8) & 0xFF] + list(chunk)
        reports.append(rep + [0] * (62 - len(rep)))
    return reports


class FakeDevice:
    """Answers reads with the queued reports once the CoAP GET has been written."""

    def __init__(self, reports_after_coap):
        self.writes = 0
        self.queue = []
        self._after = reports_after_coap

    def open_path(self, path):
        pass

    def write(self, buf):
        self.writes += 1
        if self.writes == 2:          # 1 = enable CoAP client, 2 = the GET /GSI
            self.queue = list(self._after)
        return len(buf)

    def get_input_report(self, report_id, length):
        return self.queue.pop(0) if self.queue else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (T.hid, T.hidlist, T.time)
        T.time = types.SimpleNamespace(sleep=lambda *_a, **_k: None)

    def tearDown(self):
        T.hid, T.hidlist, T.time = self._saved

    def _provider(self, infos):
        T.hidlist = types.SimpleNamespace(enumerate=lambda vid: infos)
        return T.TurtleBeachProvider()

    def _with_reports(self, reports):
        T.hid = types.SimpleNamespace(device=lambda: FakeDevice(reports))

    def test_headset_on(self):
        p = self._provider([info()])
        self._with_reports(gsi_reports({"220": "My Headset", "230": "1",
                                        "240": "100", "250": "0"}))
        [st] = p.poll()
        self.assertEqual(st.level, 100)
        self.assertEqual(st.name, "Stealth 700X Gen 3")   # product string, not the nickname
        self.assertTrue(st.online)
        self.assertFalse(st.charging)
        self.assertEqual(st.kind, "headset")
        self.assertEqual(st.source, "turtlebeach")
        self.assertEqual(st.key, "turtlebeach:2251")

    def test_charging_flag(self):
        p = self._provider([info()])
        self._with_reports(gsi_reports({"230": "1", "240": "80", "250": "1"}))
        [st] = p.poll()
        self.assertTrue(st.charging)

    def test_headset_off_keeps_last_level_greyed(self):
        p = self._provider([info()])
        self._with_reports(gsi_reports({"230": "1", "240": "55"}))
        p.poll()                                           # on at 55
        self._with_reports(gsi_reports({"230": "0", "240": "55"}))
        [st] = p.poll()                                    # now off
        self.assertFalse(st.online)
        self.assertEqual(st.level, 55)

    def test_no_transmitter(self):
        p = self._provider([])
        self._with_reports([])
        self.assertEqual(p.poll(), [])

    def test_wrong_collection_ignored(self):
        # only the consumer-control collection (0x000C) is present, not the RACE channel
        p = self._provider([info(usage_page=0x000C)])
        self._with_reports([])
        self.assertEqual(p.poll(), [])

    def test_unknown_pid_skipped(self):
        p = self._provider([info(pid=0x9999)])
        self._with_reports([])
        self.assertEqual(p.poll(), [])


if __name__ == "__main__":
    unittest.main()
