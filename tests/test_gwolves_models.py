"""Tests for the G-Wolves models with their own receiver (providers/gwolves.py MODELS).

The fake collections answer as mouse.xyz's web driver expects (static/js/index-*.js):
getOldBattery for "IsNewProtocol": "0" models (request 00 02 8f <01 receiver / 00 cable>,
reply a1 02 8f .. <charging> <battery %>) and getBatPer for "1" models (the 0x83
exchange). feature_length() is replaced, because the real one asks Windows.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import gwolves as G  # noqa: E402


class Mouse:
    """One collection with the 64-byte feature report. answer: (charging, battery) or None."""

    def __init__(self, answer=(0, 64), feature_len=G.FEATURE_LENGTH):
        self.answer = answer
        self.feature_len = feature_len
        self.sent = []
        self.opened = 0

    def reply(self):
        if not self.sent or self.answer is None:
            return [0x00] * 65
        req = self.sent[-1][1:]
        chg, batt = self.answer
        if req[1:3] == [0x02, 0x8F]:                            # getOldBattery
            return [0x00, 0xA1, 0x02, 0x8F, req[3], chg, batt] + [0x00] * 58
        return [0x00, 0xA1, 0x00, 0x02, 0x02, 0x00, 0x83, chg, batt] + [0x00] * 56


class FakeBus:
    def __init__(self, cols):
        self.cols = cols

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.c = bus.cols[path]
                self.c.opened += 1

            def send_feature_report(self, data):
                self.c.sent.append(list(data))
                return len(data)

            def get_feature_report(self, report_id, n):
                return self.c.reply()

            def close(self):
                pass

        return FakeDevice


def issue_105_entries(pid=0x5803, prefix=b"rx"):
    # the G-Wolves HSK Pro Receiver-N in the #105 diagnostics
    shape = [(0, 0x0001, 2), (1, 0x000C, 1), (1, 0x0001, 6), (1, 0x0001, 0x80),
             (1, 0xFFA0, 1), (1, 0xFFFF, 1), (2, 0xFFFF, 0)]
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": prefix + b"-%d" % n, "product_string": "G-Wolves HSK Pro Receiver-N"}
            for n, (i, p, u) in enumerate(shape)]


class ModelTest(unittest.TestCase):
    def setUp(self):
        self._saved = (G.hid, G.hidlist, G.feature_length, G.time)
        G.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)
        self.provider = G.GWolvesProvider()

    def tearDown(self):
        G.hid, G.hidlist, G.feature_length, G.time = self._saved

    def poll(self, entries, cols):
        G.hid = types.SimpleNamespace(device=FakeBus(cols).device_class())
        G.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        G.feature_length = lambda path: cols[path].feature_len
        return self.provider.poll()

    def receiver(self, entries, target=6, **kw):
        # only the collection at `target` has the 64-byte feature report
        return {e["path"]: Mouse(feature_len=G.FEATURE_LENGTH if n == target else 9, **kw)
                for n, e in enumerate(entries)}

    def test_hsk_pro_ace_issue_105(self):
        entries = issue_105_entries()
        cols = self.receiver(entries, answer=(0, 64))
        res = self.poll(entries, cols)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.online, r.kind) for r in res],
                         [("gwolves:5803", "G-Wolves HSK Pro ACE", 64, False, True, "mouse")])
        sent = cols[entries[6]["path"]].sent
        self.assertEqual(sent, [[0x00, 0x00, 0x02, 0x8F, 0x01] + [0x00] * 60])
        self.assertTrue(all(not c.sent for p, c in cols.items() if p != entries[6]["path"]))

    def test_the_cable_sends_00_and_shares_the_icon(self):
        rx = issue_105_entries()
        cable = issue_105_entries(0x5804, b"cable")[:1]
        cols = {**self.receiver(rx, answer=(0, 60)), **self.receiver(cable, target=0, answer=(1, 61))}
        res = self.poll(rx + cable, cols)
        self.assertEqual([(r.key, r.level, r.charging) for r in res], [("gwolves:5803", 61, True)])
        self.assertEqual(cols[b"cable-0"].sent[0][:5], [0x00, 0x00, 0x02, 0x8F, 0x00])
        self.assertFalse(cols[rx[6]["path"]].sent)             # the receiver is not asked

    def test_reply_with_or_without_a_leading_report_id(self):
        self.assertEqual(G.parse_old([0x00, 0xA1, 0x02, 0x8F, 0x01, 0x01, 0x37]), (55, True))
        self.assertEqual(G.parse_old([0xA1, 0x02, 0x8F, 0x01, 0x00, 0x37]), (55, False))
        self.assertEqual(G.parse_old([0x00, 0xA2, 0x02, 0x8F, 0x01, 0x00, 0x37]), (None, None))
        self.assertEqual(G.parse_old([0x00, 0xA1, 0x02, 0x8F, 0x01, 0x00, 0xC8]), (None, None))
        self.assertEqual(G.parse_old([0x00, 0xA1, 0x00, 0x02, 0x02, 0x00, 0x83, 0, 50]),
                         (None, None))                          # a new-protocol reply

    def test_asleep_keeps_the_last_value_greyed_then_goes(self):
        entries = issue_105_entries()
        cols = self.receiver(entries)
        self.poll(entries, cols)
        for c in cols.values():
            c.answer = None
        res = self.poll(entries, cols)
        self.assertEqual([(r.level, r.online) for r in res], [(64, False)])
        self.provider._model_last[0x5803] = (64, False, time.time() - G.ASLEEP_KEEP)
        self.assertEqual(self.poll(entries, cols), [])

    def test_a_new_protocol_model_on_its_own_receiver(self):
        entries = issue_105_entries(0x3817)                    # HTM Plus
        cols = self.receiver(entries, answer=(0, 42))
        res = self.poll(entries, cols)
        self.assertEqual([(r.key, r.name, r.level) for r in res],
                         [("gwolves:3817", "G-Wolves HTM Plus", 42)])
        self.assertEqual(cols[entries[6]["path"]].sent[0][:7],
                         [0x00, 0x00, 0x00, 0x02, 0x02, 0x00, 0x83])

    def test_two_models_are_two_icons_next_to_the_shared_receiver(self):
        a = issue_105_entries(0x5803, b"a")
        b = issue_105_entries(0x7913, b"b")                    # HT-S2
        cols = {**self.receiver(a, answer=(0, 70)), **self.receiver(b, answer=(0, 30))}
        res = self.poll(a + b, cols)
        self.assertEqual(sorted((r.name, r.level) for r in res),
                         [("G-Wolves HSK Pro ACE", 70), ("G-Wolves HT-S2", 30)])

    def test_no_64_byte_feature_report_sends_nothing(self):
        entries = issue_105_entries()
        cols = self.receiver(entries, target=-1)
        self.assertEqual(self.poll(entries, cols), [])
        self.assertTrue(all(not c.sent and not c.opened for c in cols.values()))


class TableTest(unittest.TestCase):
    def test_hsk_pro_ace_is_an_old_protocol_model(self):
        self.assertEqual(G.MODELS[0x5803], ("G-Wolves HSK Pro ACE", 0x5803, G.OLD, False))
        self.assertEqual(G.MODELS[0x5804], ("G-Wolves HSK Pro ACE", 0x5803, G.OLD, True))

    def test_every_model_has_its_receiver_and_a_cable(self):
        groups = {}
        for pid, (name, model, kind, wired) in G.MODELS.items():
            groups.setdefault(model, []).append((pid, name, kind, wired))
            self.assertIn(model, G.MODELS, f"{pid:04X}")
            self.assertIn(kind, (G.NEW, G.OLD))
        for model, rows in groups.items():
            self.assertEqual(len({r[1] for r in rows}), 1, f"{model:04X}")
            self.assertEqual(len({r[2] for r in rows}), 1, f"{model:04X}")
            self.assertEqual(sum(r[3] for r in rows), 1, f"{model:04X}")   # one cable id

    def test_no_overlap_with_the_shared_receiver(self):
        self.assertNotIn(G.RECEIVER, G.MODELS)
        self.assertFalse(set(G.MODELS) & set(G.WIRED))

    def test_request_bytes(self):
        self.assertEqual(G.old_request(False)[:4], [0x00, 0x02, 0x8F, 0x01])
        self.assertEqual(G.old_request(True)[:4], [0x00, 0x02, 0x8F, 0x00])
        self.assertEqual(len(G.old_request(True)), 64)


if __name__ == "__main__":
    unittest.main()
