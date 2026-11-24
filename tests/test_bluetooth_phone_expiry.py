"""Phone expiry is a presentation transition, never another Windows snapshot."""
import unittest
from unittest import mock

from providers import bluetooth
from providers.base import DeviceStatus
from test_bluetooth_native import Clock, FakeReader, FakeStop, T0
from test_bluetooth_provider import raw_phone
from test_bluetooth_watcher import (BlockingStdout, FakeClock, ScriptedQueue,
                                    phone_snapshot)
import test_bluetooth_watcher as watcher_fixtures


KEY = "bt:AABBCCDDEEFF"


class PhoneExpiryProviderTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.provider = bluetooth.BluetoothProvider(clock=self.clock)

    def at(self, seconds):
        self.clock.t = T0 + seconds

    def test_expired_missing_phone_cannot_start_another_grace(self):
        p = self.provider
        p.handle(raw_phone())
        self.at(2)
        p.handle(raw_phone(connected=False))
        self.at(32)
        self.assertFalse(p.handle(raw_phone(connected=False))[0].online)
        self.at(78)
        self.assertEqual(p.handle(raw_phone(connected=False)), [])
        self.assertIsNone(p.next_phone_grace_deadline())

        # Only a genuine connected return admits a new grace.
        self.at(90)
        self.assertTrue(p.handle(raw_phone())[0].online)
        self.at(92)
        self.assertTrue(p.handle(raw_phone(connected=False))[0].online)
        self.assertEqual(p.next_phone_grace_deadline(), T0 + 122)

    def test_expiry_preserves_unrelated_misses_and_published_status(self):
        p = self.provider
        phone = DeviceStatus(KEY, "Phone", 75, source="bluetooth", kind="phone")
        headset = DeviceStatus("bt:headset", "Headset", 60, source="bluetooth", kind="headset")
        p.merge([phone, headset])
        self.at(2)
        p.merge([headset])
        self.at(18)
        previous = {s.key: s for s in p.merge([])}
        self.assertEqual(p._misses[headset.key], 1)
        self.at(32)
        expired = {s.key: s for s in p.expire_phone_graces()}
        self.assertIs(expired[headset.key], previous[headset.key])
        self.assertEqual(p._misses[headset.key], 1)
        self.assertFalse(expired[KEY].online)
        self.assertEqual(p._misses[KEY], 1)
        self.assertIsNone(p.expire_phone_graces())
        self.assertEqual(p.merge([]), [])  # next actual snapshot is the second miss

    def test_failed_snapshot_does_not_repeat_expiry_or_restore_other_devices(self):
        p = self.provider
        headset = DeviceStatus("bt:headset", "Headset", 60, source="bluetooth")
        p.merge([DeviceStatus(KEY, "Phone", 75, kind="phone"), headset])
        self.at(2)
        p.merge([headset])
        self.at(20)
        failed = {s.key: s for s in p.handle("{}")}
        self.assertTrue(all(s.polling_stale for s in failed.values()))
        self.at(32)
        expired = {s.key: s for s in p.expire_phone_graces()}
        self.assertIs(expired[headset.key], failed[headset.key])
        self.assertEqual(p._misses[headset.key], 0)
        self.assertEqual(p._misses[KEY], 1)
        self.assertIsNone(p.next_phone_grace_deadline())
        for second in (33, 78, 138):
            self.at(second)
            self.assertIsNone(p.expire_phone_graces())
        self.assertEqual(p._misses[KEY], 1)

    def test_retired_owner_cannot_expire_successor_state(self):
        p = self.provider
        old, new = object(), object()
        p.claim(old)
        p.handle(raw_phone(), owner=old)
        self.at(2)
        p.handle(raw_phone(connected=False), owner=old)
        p.release(old)
        p.claim(new)
        self.at(32)
        self.assertIsNone(p.expire_phone_graces(owner=old))
        self.assertEqual(p._misses[KEY], 0)
        self.assertEqual(p.next_phone_grace_deadline(), T0 + 32)
        self.assertFalse(p.expire_phone_graces(owner=new)[0].online)
        self.assertIsNone(p.expire_phone_graces(owner=new))

    def test_expiring_one_phone_preserves_another_phones_deadline(self):
        p = self.provider
        first = DeviceStatus(KEY, "Phone", 75, kind="phone")
        second = DeviceStatus("bt:second", "Other phone", 60, kind="phone")
        p.merge([first, second])
        self.at(2)
        p.merge([second])
        self.at(10)
        p.merge([])
        self.at(32)
        statuses = {s.key: s for s in p.expire_phone_graces()}
        self.assertFalse(statuses[KEY].online)
        self.assertTrue(statuses[second.key].online)
        self.assertEqual(p._misses[second.key], 0)
        self.assertEqual(p.next_phone_grace_deadline(), T0 + 40)
        self.at(40)
        self.assertTrue(all(not s.online for s in p.expire_phone_graces()))
        self.assertEqual(p._misses[KEY], 1)
        self.assertIsNone(p.next_phone_grace_deadline())


class PhoneExpiryWatcherTests(unittest.TestCase):
    def native(self, reader, until):
        updates = []
        p = bluetooth.BluetoothProvider(clock=reader.clock)
        w = bluetooth.NativeBluetoothWatcher(
            p, lambda ds: updates.append((reader.clock.t - T0, ds)),
            reader_factory=lambda: reader, clock=reader.clock)
        w._stop = FakeStop(reader.clock, T0 + until)
        with mock.patch.object(bluetooth, "log"):
            w._run()
        self.assertFalse(w.failed)
        self.assertEqual(w.snapshots, len(reader.snaps))
        return dict(updates)

    def powershell(self, events, until):
        # Reuse the existing fake process, pipe and queue; no PowerShell is launched.
        fixture = watcher_fixtures.WatcherTest("test_phone_grace_expiry_is_published_between_child_lines")
        fixture.setUp()
        w = None
        try:
            fixture.make_stdout = lambda: BlockingStdout([])
            clock = FakeClock()
            start = clock.t
            updates = []
            p = bluetooth.BluetoothProvider(clock=clock)
            w = bluetooth.BluetoothWatcher(
                p, lambda ds: updates.append((clock.t - start, ds)), clock=clock)
            q = ScriptedQueue(clock, [(start + t, raw) for t, raw in events],
                              until=start + until, stop=w._stop)
            with mock.patch.object(bluetooth.queue, "Queue", lambda: q), \
                    mock.patch.object(bluetooth, "log"):
                w._run()
            self.assertFalse(w.failed)
            self.assertEqual(w.snapshots, len(events))
            return dict(updates)
        finally:
            if w is not None:
                w.stop()
            for proc in fixture.procs:
                proc.stdout.close()
            fixture.tearDown()

    def test_native_stays_disconnected_after_expiry_and_removal(self):
        reader = FakeReader(Clock(), connect_at=T0, disconnect_at=T0 + 2, cls=0x020C)
        updates = self.native(reader, 142)
        self.assertFalse(updates[32][0].online)
        self.assertEqual(updates[78], [])
        self.assertEqual(updates[138], [])

    def test_powershell_stays_disconnected_after_expiry_and_removal(self):
        events = [(t, phone_snapshot(t == 0)) for t in (0, 2, 6, 10, 18, 78, 138)]
        updates = self.powershell(events, 142)
        self.assertFalse(updates[32][0].online)
        self.assertEqual(updates[78], [])
        self.assertEqual(updates[138], [])

    def test_native_reconnect_admits_one_new_grace(self):
        class ReturningReader(FakeReader):
            def connected(self):
                t = self.clock.t - T0
                return t < 2 or 100 <= t < 110

        updates = self.native(ReturningReader(Clock(), cls=0x020C), 146)
        self.assertEqual(updates[78], [])
        self.assertTrue(updates[100][0].online)
        self.assertTrue(updates[110][0].online)
        self.assertFalse(updates[140][0].online)

    def test_powershell_reconnect_admits_one_new_grace(self):
        events = [(t, phone_snapshot(c)) for t, c in
                  ((0, True), (2, False), (18, False), (78, False),
                   (100, True), (110, False), (126, False), (186, False))]
        updates = self.powershell(events, 190)
        self.assertEqual(updates[78], [])
        self.assertTrue(updates[100][0].online)
        self.assertTrue(updates[110][0].online)
        self.assertFalse(updates[140][0].online)
        self.assertEqual(updates[186], [])

    def test_invalid_child_line_does_not_cause_endless_expiry_publications(self):
        updates = self.powershell(
            [(0, phone_snapshot(True)), (2, phone_snapshot(False)), (20, "{}")], 142)
        self.assertEqual(sorted(updates), [0, 2, 20, 32])
        self.assertTrue(updates[20][0].polling_stale)
        self.assertTrue(updates[32][0].polling_stale)

    def test_invalid_native_snapshot_does_not_cause_repeated_expiry(self):
        class BadOnceReader(FakeReader):
            def snapshot(self):
                snap = super().snapshot()
                if self.clock.t == T0 + 18:
                    snap["devs"] = "bad"
                return snap

        reader = BadOnceReader(Clock(), connect_at=T0, disconnect_at=T0 + 2, cls=0x020C)
        updates = self.native(reader, 142)
        self.assertEqual(sorted(updates), [0, 2, 6, 10, 18, 32, 78, 138])
        self.assertTrue(updates[18][0].polling_stale)
        self.assertTrue(updates[32][0].polling_stale)
        self.assertEqual(updates[78], [])


if __name__ == "__main__":
    unittest.main()
