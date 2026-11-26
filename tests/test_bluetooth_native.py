"""The in-process Bluetooth reader (providers/bluetooth_native.py) and NativeBluetoothWatcher.

No Bluetooth stack, no PowerShell, no real time: the watcher runs synchronously against a fake
reader and a fake clock, the provider's one-shot read and the app's fallback chain use mocks.

Run from the repository root:
    python -m unittest discover -s tests
"""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import hb, make_app  # noqa: E402
from providers import bluetooth, bluetooth_native  # noqa: E402

MAC = "AABBCCDDEEFF"
ROOT = "BTHENUM\\DEV_" + MAC + "\\7&123"
T0 = 1000.0


def snapshot(connected, level=75, cls=0x0404):
    return {"devs": [{"id": ROOT, "name": "Windows Headset", "status": "OK"}],
            "props": [{"id": ROOT, "key": bluetooth.K_BAT, "data": level},
                      {"id": ROOT, "key": bluetooth.K_CONN, "data": connected}],
            "aep": [{"addr": MAC, "conn": connected, "le": False, "err": None, "cls": cls}],
            "aep_error": None, "roots": [{"le": False, "mac": MAC}]}


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class FakeStop:
    """The watcher's stop event: wait(s) moves the fake clock instead of sleeping."""

    def __init__(self, clock, until):
        self.clock, self.until, self.flag = clock, until, False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, seconds):
        self.clock.t += seconds
        if self.clock.t >= self.until:
            self.flag = True
        return self.flag


class FakeReader:
    """Connected from `connect_at` on (never when None); records what the watcher asks."""

    def __init__(self, clock, connect_at=None, winrt_ok=True, fail_checks=False,
                 disconnect_at=None, cls=0x0404):
        self.clock, self.connect_at = clock, connect_at
        self.disconnect_at, self.cls = disconnect_at, cls
        self.winrt_ok = winrt_ok
        self.winrt_error = None if winrt_ok else "OSError: no WinRT"
        self.fail_checks = fail_checks
        self.checks, self.snaps, self.closed = [], [], False

    def connected(self):
        if self.connect_at is None or self.clock.t < self.connect_at:
            return False
        return self.disconnect_at is None or self.clock.t < self.disconnect_at

    def connections(self, roots):
        self.checks.append(self.clock.t)
        if self.fail_checks:
            raise OSError("check failed")
        return snapshot(self.connected(), cls=self.cls)["aep"]

    def snapshot(self):
        self.snaps.append(self.clock.t)
        return snapshot(self.connected(), cls=self.cls)

    def close(self):
        self.closed = True


class BlockedReader(FakeReader):
    """snapshot() blocks like a hung Windows call until `release` is set, then reads `level`."""

    def __init__(self, clock, level=10):
        super().__init__(clock, connect_at=T0)
        self.level = level
        self.entered, self.release = threading.Event(), threading.Event()

    def snapshot(self):
        self.snaps.append(self.clock.t)
        self.entered.set()
        self.release.wait(15)
        return snapshot(True, level=self.level)


def run_watcher(reader, until, provider=None):
    """Runs the watcher loop on this thread until the fake clock reaches `until`."""
    updates = []
    w = bluetooth.NativeBluetoothWatcher(provider or bluetooth.BluetoothProvider(), updates.append,
                                         reader_factory=lambda: reader, clock=reader.clock)
    w._stop = FakeStop(reader.clock, until)  # type: ignore[assignment]
    with mock.patch.object(bluetooth, "log"):
        w._run()
    return w, updates


class SnapshotHelpersTests(unittest.TestCase):
    def test_roots_are_the_device_nodes_with_upper_case_addresses(self):
        devs = [{"id": "BTHENUM\\DEV_a1b2c3d4e5f6\\7&1"},
                {"id": "BTHLE\\DEV_112233445566\\8&2"},
                {"id": "BTHLEDEVICE\\{00001800-0000-1000-8000-00805F9B34FB}_DEV_VID&01_112233445566\\9&3"},
                {"id": "BTHENUM\\{0000110B-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\\7&4"},
                {"id": None}, {}]
        self.assertEqual(bluetooth_native.roots_of(devs),
                         [{"le": False, "mac": "A1B2C3D4E5F6"}, {"le": True, "mac": "112233445566"}])

    def test_connection_key_changes_only_with_a_connection_state(self):
        a = [{"addr": MAC, "conn": True}, {"addr": "112233445566", "conn": None}]
        b = [{"addr": MAC, "conn": False}, {"addr": "112233445566", "conn": None}]
        key = bluetooth_native.connection_key
        self.assertEqual(key(a), key([dict(x) for x in a]))
        self.assertNotEqual(key(a), key(b))
        self.assertEqual(key([]), "")


class NativeWatcherCadenceTests(unittest.TestCase):
    """The same timing as WATCH_SCRIPT: a check every 2 s, snapshots at once and +3, +8 and +15 s
    after a connect or disconnect, and a full snapshot at least once a minute."""

    def test_snapshots_after_a_connect_follow_the_powershell_schedule(self):
        reader = FakeReader(Clock(), connect_at=T0 + 12)
        w, updates = run_watcher(reader, until=T0 + 90)
        self.assertEqual(reader.snaps, [T0, T0 + 12, T0 + 16, T0 + 20, T0 + 28, T0 + 88])
        self.assertEqual(reader.checks, [T0 + 2 * i for i in range(1, 45)])
        self.assertEqual(len(updates), 6)
        self.assertEqual(w.snapshots, 6)
        self.assertFalse(w.failed)
        self.assertTrue(reader.closed)

    def test_without_changes_a_full_snapshot_comes_once_a_minute(self):
        reader = FakeReader(Clock())
        run_watcher(reader, until=T0 + 122)
        self.assertEqual(reader.snaps, [T0, T0 + 60, T0 + 120])

    def test_without_winrt_it_reads_full_snapshots_and_never_checks(self):
        reader = FakeReader(Clock(), connect_at=T0 + 12, winrt_ok=False)
        w, _ = run_watcher(reader, until=T0 + 122)
        self.assertEqual(reader.checks, [])
        self.assertEqual(reader.snaps, [T0, T0 + 60, T0 + 120])
        self.assertFalse(w.failed)

    def test_each_snapshot_goes_through_the_provider(self):
        reader = FakeReader(Clock(), connect_at=T0)
        _, updates = run_watcher(reader, until=T0 + 2)
        self.assertEqual(len(updates), 1)
        (status,) = updates[0]
        self.assertEqual((status.key, status.level, status.online), ("bt:" + MAC, 75, True))


class NativeWatcherPhoneGraceTests(unittest.TestCase):
    """A phone's grace must end on the watcher's clock, not on the next 60 s read."""

    def test_the_loop_publishes_the_greyed_phone_at_the_grace_deadline(self):
        reader = FakeReader(Clock(), connect_at=T0, disconnect_at=T0 + 2, cls=0x0203)
        updates = []
        w = bluetooth.NativeBluetoothWatcher(
            bluetooth.BluetoothProvider(clock=reader.clock),
            lambda devices: updates.append((reader.clock.t, devices)),
            reader_factory=lambda: reader, clock=reader.clock)
        w._stop = FakeStop(reader.clock, T0 + 40)  # type: ignore[assignment]
        with mock.patch.object(bluetooth, "log"):
            w._run()

        by_time = dict(updates)
        self.assertIn(T0 + 32, by_time)
        phone = by_time[T0 + 32][0]
        self.assertEqual((phone.key, phone.level, phone.online),
                         ("bt:" + MAC, 75, False))
        self.assertNotIn(T0 + 78, by_time)          # stopped before the next full read
        self.assertFalse(w.failed)

    def test_a_headset_disconnect_keeps_the_plain_cadence(self):
        reader = FakeReader(Clock(), connect_at=T0, disconnect_at=T0 + 2)
        updates = []
        w = bluetooth.NativeBluetoothWatcher(
            bluetooth.BluetoothProvider(clock=reader.clock),
            lambda devices: updates.append((reader.clock.t, devices)),
            reader_factory=lambda: reader, clock=reader.clock)
        w._stop = FakeStop(reader.clock, T0 + 40)  # type: ignore[assignment]
        with mock.patch.object(bluetooth, "log"):
            w._run()

        self.assertNotIn(T0 + 32, dict(updates))    # no phone deadline for a headset
        self.assertFalse(w.failed)


class NativeWatcherFailureTests(unittest.TestCase):
    def test_gives_up_after_three_failed_rounds_in_a_row(self):
        reader = FakeReader(Clock(), fail_checks=True)
        w, updates = run_watcher(reader, until=T0 + 600)
        self.assertTrue(w.failed)
        self.assertEqual(len(reader.checks), bluetooth.NativeBluetoothWatcher.FAIL_LIMIT)
        self.assertEqual(len(updates), 1)                 # the first snapshot still arrived
        self.assertTrue(reader.closed)

    def test_a_reader_that_cannot_start_fails_at_once(self):
        def broken():
            raise OSError("cfgmgr32 missing")
        w = bluetooth.NativeBluetoothWatcher(bluetooth.BluetoothProvider(), lambda devices: None,
                                             reader_factory=broken, clock=Clock())
        with mock.patch.object(bluetooth, "log"):
            w._run()
        self.assertTrue(w.failed)

    def test_a_stuck_round_counts_as_failed_after_the_stall_timeout(self):
        clock, release, entered = Clock(), threading.Event(), threading.Event()

        class StuckReader(FakeReader):
            def snapshot(self):
                entered.set()
                release.wait(15)                          # a Windows call that hangs
                return super().snapshot()

        reader = StuckReader(clock)
        w = bluetooth.NativeBluetoothWatcher(bluetooth.BluetoothProvider(), lambda devices: None,
                                             reader_factory=lambda: reader, clock=clock)
        with mock.patch.object(bluetooth, "log"):
            w.start()
            try:
                self.assertTrue(entered.wait(15))
                clock.t = T0 + bluetooth.STALL_TIMEOUT - 1
                self.assertFalse(w.failed)
                clock.t = T0 + bluetooth.STALL_TIMEOUT + 1
                self.assertTrue(w.failed)
                self.assertEqual(w.stalls, 1)
                # abandoned while the call is still blocked: running() must be False
                # now, not only once the call returns years later
                self.assertFalse(w.running())
            finally:
                release.set()
                w._thread.join(15)
        self.assertFalse(w._thread.is_alive())            # it stops once the call returns
        self.assertTrue(reader.closed)


class OneShotReadTests(unittest.TestCase):
    """BluetoothProvider(native=True) reads in-process first and keeps PowerShell as the fallback."""

    def test_native_snapshot_needs_no_powershell(self):
        provider = bluetooth.BluetoothProvider(native=True)
        with mock.patch.object(bluetooth_native, "one_shot_snapshot", return_value=snapshot(True)), \
                mock.patch.object(bluetooth.subprocess, "run") as run:
            raw = provider._query()
        assert raw is not None
        run.assert_not_called()
        self.assertEqual(json.loads(raw), snapshot(True))
        self.assertTrue(any("native: 1 nodes, 2 properties, 1 device(s)" in line
                            for line in provider.diagnostics()))

    def test_a_failed_native_read_falls_back_to_powershell(self):
        provider = bluetooth.BluetoothProvider(native=True)
        done = mock.Mock(returncode=0, stdout=json.dumps(snapshot(True)).encode(), stderr=b"")
        with mock.patch.object(bluetooth_native, "one_shot_snapshot", side_effect=OSError("no cfgmgr32")), \
                mock.patch.object(bluetooth.subprocess, "run", return_value=done) as run:
            raw = provider._query()
        assert raw is not None
        run.assert_called_once()
        self.assertEqual(json.loads(raw)["devs"], snapshot(True)["devs"])
        self.assertTrue(any("native read failed (OSError: no cfgmgr32), using PowerShell" in line
                            for line in provider.diagnostics()))

    def test_the_default_provider_does_not_read_in_process(self):
        provider = bluetooth.BluetoothProvider()
        done = mock.Mock(returncode=0, stdout=json.dumps(snapshot(True)).encode(), stderr=b"")
        with mock.patch.object(bluetooth_native, "one_shot_snapshot") as native, \
                mock.patch.object(bluetooth.subprocess, "run", return_value=done):
            provider._query()
        native.assert_not_called()


class AppFallbackChainTests(unittest.TestCase):
    def test_a_failed_native_watcher_hands_over_to_the_powershell_watcher(self):
        app = make_app({"bluetooth": True})
        app.stop_evt, app.bt_wake = threading.Event(), threading.Event()
        app.bt_watch, app.bt_watch_failed = None, False
        app.bt = mock.Mock(native=True)       # takes the "no native reads" handover
        made = []

        class Native:
            alive = False

            def __init__(self, provider, on_update):
                self.failed = True
                made.append("native")

            def start(self):
                return self

            def stop(self):
                pass

        class PowerShell:
            alive = False

            def __init__(self, provider, on_update):
                self.failed = False
                made.append("powershell")
                app.stop_evt.set()                        # one round is enough
                app.bt_wake.set()

            def start(self):
                return self

            def stop(self):
                pass

        with mock.patch.object(hb, "NativeBluetoothWatcher", Native), \
                mock.patch.object(hb, "BluetoothWatcher", PowerShell), \
                mock.patch.object(hb.sys, "platform", "win32"), mock.patch.object(hb, "log"):
            t = threading.Thread(target=app.bt_loop, daemon=True)
            t.start()
            t.join(15)
        self.assertFalse(t.is_alive())
        self.assertEqual(made, ["native", "powershell"])
        self.assertTrue(app.bt_native_failed)
        self.assertFalse(app.bt_watch_failed)


class SnapshotOwnershipTests(unittest.TestCase):
    """#513 review: an abandoned watcher must not publish, and a failed in-process
    backend must never be retried by the one-shot fallback loop."""

    def app(self):
        app = make_app({"bluetooth": True})
        app.stop_evt, app.bt_wake, app.bt_fresh = (threading.Event(), threading.Event(),
                                                   threading.Event())
        app.bt_watch, app.bt_watch_failed, app.bt_native_failed = None, False, False
        app._bt_generation, app._bt_orphans = 0, []
        app.bt_cache, app._bt_revision = [], 0

        class Wake:
            def __init__(self):
                self.calls = 0

            def bluetooth(self):
                self.calls += 1

        app.wake = Wake()
        app.bt = mock.Mock(native=True)
        app.bt.poll.return_value = []
        return app

    def test_a_late_snapshot_from_an_abandoned_watcher_is_ignored(self):
        app = self.app()
        app._bt_generation = 4                 # the loop gave up on 1..3
        app._bt_update([], generation=3)
        self.assertEqual((app.bt_cache, app._bt_revision), ([], 0))
        self.assertFalse(app.bt_fresh.is_set())
        self.assertEqual(app.wake.calls, 0)

    def test_the_current_watcher_still_publishes(self):
        app = self.app()
        app._bt_generation = 4
        app._bt_update([], generation=4)
        self.assertEqual(app._bt_revision, 1)
        self.assertTrue(app.bt_fresh.is_set())
        self.assertEqual(app.wake.calls, 1)

    def test_the_fallback_loop_stops_admitting_native_work(self):
        app = self.app()
        app.bt_native_failed = True
        app.bt_watch_failed = True             # both watchers are out: one-shot path

        class OneWait:
            def wait(self, seconds):
                app.stop_evt.set()
                return True

            def clear(self):
                pass

        app.bt_wake = OneWait()
        with mock.patch.object(hb, "log"):
            app.bt_loop()
        self.assertFalse(app.bt.native)
        app.bt.poll.assert_called_once()

    def test_repeated_stalls_keep_one_watcher_per_backend(self):
        app = self.app()
        made = {"native": 0, "powershell": 0}
        stopped = []

        class Base:
            failed = True

            def __init__(self, provider, on_update, label):
                made[label] += 1

            def start(self):
                return self

            def stop(self):
                stopped.append(type(self).__name__)

            @property
            def alive(self):
                return False

        class Native(Base):
            def __init__(self, provider, on_update):
                super().__init__(provider, on_update, "native")

        class PowerShell(Base):
            def __init__(self, provider, on_update):
                super().__init__(provider, on_update, "powershell")

        rounds = {"n": 0}

        class Wake:
            def wait(self, seconds):
                rounds["n"] += 1
                if rounds["n"] >= 6:
                    app.stop_evt.set()
                return True

            def clear(self):
                pass

        app.bt_wake = Wake()
        with mock.patch.object(hb, "NativeBluetoothWatcher", Native), \
                mock.patch.object(hb, "BluetoothWatcher", PowerShell), \
                mock.patch.object(hb.sys, "platform", "win32"), mock.patch.object(hb, "log"):
            app.bt_loop()
        self.assertEqual(made, {"native": 1, "powershell": 1})
        self.assertEqual(stopped, ["Native", "PowerShell"])
        self.assertFalse(app.bt.native)
        self.assertTrue(app.bt_watch_failed)
        self.assertLessEqual(len(app._bt_orphans), 2)


class RetiredWatcherTests(unittest.TestCase):
    """#513 review, second round: the call genuinely stays blocked while the app moves on, and
    what it returns afterwards must change neither the provider's cache nor the tray."""

    def app(self):
        return SnapshotOwnershipTests.app(self)

    def test_a_released_owner_cannot_change_the_cache(self):
        provider = bluetooth.BluetoothProvider()
        old, new = object(), object()
        provider.claim(old)
        provider.handle(json.dumps(snapshot(True, level=50)), owner=old)
        provider.release(old)
        provider.claim(new)
        with mock.patch.object(bluetooth, "log"):
            self.assertIsNone(provider.handle(json.dumps(snapshot(True, level=10)), owner=old))
        self.assertEqual(provider._levels, {MAC: 50})

    def test_a_late_result_after_the_handover_leaves_the_provider_cache_alone(self):
        provider, clock = bluetooth.BluetoothProvider(), Clock()
        reader = BlockedReader(clock, level=10)
        published, late = [], {}
        w = bluetooth.NativeBluetoothWatcher(provider, published.append,
                                             reader_factory=lambda: reader, clock=clock)
        real_handle, handled = provider.handle, threading.Event()

        def spy(raw, owner=None):
            out = real_handle(raw, owner)
            if owner is w:
                late["result"] = out
                handled.set()
            return out

        provider.handle = spy  # type: ignore[method-assign]
        with mock.patch.object(bluetooth, "log"):
            w.start()
            try:
                self.assertTrue(reader.entered.wait(15))      # blocked inside "Windows"
                successor = object()                          # the fallback taking over
                provider.claim(successor)
                fresh = provider.handle(json.dumps(snapshot(True, level=90)), owner=successor)
                self.assertEqual([90], [st.level for st in fresh])
                reader.release.set()                          # the hung call returns 10 %
                self.assertTrue(handled.wait(15))             # ... and reaches the provider
            finally:
                reader.release.set()
                w.stop()
                w._thread.join(15)
        self.assertFalse(w._thread.is_alive())
        self.assertIsNone(late["result"])                     # refused by the provider itself
        self.assertEqual(published, [])
        self.assertEqual(provider._levels, {MAC: 90})
        self.assertEqual([90], [st.level for st in provider._shown.values()])
        # the next fallback poll fails: what stays on screen is the fresh 90 %, not the late 10 %
        with mock.patch.object(provider, "_query", return_value=None), \
                mock.patch.object(bluetooth.sys, "platform", "win32"), \
                mock.patch.object(bluetooth, "log"):
            kept = provider.poll()
        self.assertEqual([(90, True)], [(st.level, st.polling_stale) for st in kept])

    def test_a_stalled_native_call_returning_after_the_fallback_changes_nothing(self):
        app = self.app()
        app.bt = bluetooth.BluetoothProvider(native=True)
        clock = Clock()
        reader = BlockedReader(clock, level=10)
        natives = []

        class Native(bluetooth.NativeBluetoothWatcher):
            def __init__(self, provider, on_update):
                super().__init__(provider, on_update, reader_factory=lambda: reader, clock=clock)
                natives.append(self)

        class PowerShell:
            """Stands in for the PowerShell watcher: claims the cache and reads 90 % at once."""
            alive, failed = False, False

            def __init__(self, provider, on_update):
                self.provider, self.on_update = provider, on_update
                provider.claim(self)

            def start(self):
                self.on_update(self.provider.handle(json.dumps(snapshot(True, level=90)), owner=self))
                return self

            def stop(self):
                pass

        waits = {"n": 0}

        class Wake:
            def wait(self, seconds):
                waits["n"] += 1
                if waits["n"] == 1:
                    reader.entered.wait(15)                       # the native call is blocked
                    clock.t = T0 + bluetooth.STALL_TIMEOUT + 1    # ... long enough to be stuck
                else:
                    app.stop_evt.set()
                return True

            def clear(self):
                pass

        app.bt_wake = Wake()
        with mock.patch.object(hb, "NativeBluetoothWatcher", Native), \
                mock.patch.object(hb, "BluetoothWatcher", PowerShell), \
                mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.object(hb, "log"), mock.patch.object(bluetooth, "log"):
            try:
                app.bt_loop()
                self.assertEqual([90], [st.level for st in app.bt_cache])
            finally:
                reader.release.set()                              # the stuck call returns 10 %
                natives[0]._thread.join(15)
            self.assertFalse(natives[0]._thread.is_alive())
            self.assertEqual(natives[0].snapshots, 0)
            self.assertEqual([90], [st.level for st in app.bt_cache])
            self.assertEqual(app.bt._levels, {MAC: 90})
            with mock.patch.object(app.bt, "_query", return_value=None), \
                    mock.patch.object(bluetooth.sys, "platform", "win32"):
                self.assertEqual([90], [st.level for st in app.bt.poll()])
        self.assertEqual(len(natives), 1)
        self.assertTrue(app.bt_native_failed)
        self.assertFalse(app.bt.native)

    def test_switching_bluetooth_off_and_on_keeps_one_native_call_in_flight(self):
        app = self.app()
        app.bt = bluetooth.BluetoothProvider(native=True)
        clock = Clock()
        reader = BlockedReader(clock)
        natives, shells, peak = [], [], {"alive": 0}

        class Native(bluetooth.NativeBluetoothWatcher):
            def __init__(self, provider, on_update):
                super().__init__(provider, on_update, reader_factory=lambda: reader, clock=clock)
                natives.append(self)

        class PowerShell:
            alive, failed = False, False

            def __init__(self, provider, on_update):
                shells.append(self)
                provider.claim(self)

            def start(self):
                return self

            def stop(self):
                pass

        waits = {"n": 0}

        class Wake:
            """Each wait flips Bluetooth off/on in the menu while the first call stays blocked."""

            def wait(self, seconds):
                waits["n"] += 1
                reader.entered.wait(15)
                peak["alive"] = max(peak["alive"], sum(1 for w in natives if w.alive))
                if waits["n"] < 12:
                    app.cfg["bluetooth"] = not app.cfg["bluetooth"]
                elif waits["n"] == 12:
                    app.cfg["bluetooth"] = True
                    clock.t = T0 + bluetooth.STALL_TIMEOUT + 1    # the retired call is stuck
                else:
                    app.stop_evt.set()
                return True

            def clear(self):
                pass

        app.bt_wake = Wake()
        with mock.patch.object(hb, "NativeBluetoothWatcher", Native), \
                mock.patch.object(hb, "BluetoothWatcher", PowerShell), \
                mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.object(hb, "log"), mock.patch.object(bluetooth, "log"):
            try:
                app.bt_loop()
                self.assertEqual(len(natives), 1)          # never a second one beside it
                self.assertEqual(peak["alive"], 1)
                self.assertEqual(len(shells), 1)           # stuck past the stall timeout
                self.assertEqual(app._bt_orphans, natives)
            finally:
                reader.release.set()
                natives[0]._thread.join(15)
        self.assertFalse(natives[0]._thread.is_alive())
        self.assertEqual(natives[0].snapshots, 0)
        self.assertEqual(app.bt._levels, {})
        self.assertTrue(app.bt_native_failed)
        self.assertFalse(app.bt.native)


class OneShotBoundTests(unittest.TestCase):
    """#513 review: a one-shot read that timed out stays the only one until it returns."""

    def test_repeated_timeouts_never_start_a_second_reader_thread(self):
        entered, release = threading.Event(), threading.Event()
        made = []

        class HungReader:
            def __init__(self):
                made.append(self)

            def snapshot(self):
                entered.set()
                release.wait(15)
                return snapshot(True)

            def close(self):
                pass

        def live():
            return [t for t in threading.enumerate() if t.name == "bt-native-snapshot"]

        with mock.patch.object(bluetooth_native, "NativeReader", HungReader):
            try:
                with self.assertRaises(TimeoutError):
                    bluetooth_native.one_shot_snapshot(timeout=0.05)
                self.assertTrue(entered.wait(15))
                for _ in range(3):                          # refused at once, no new thread
                    with self.assertRaises(TimeoutError):
                        bluetooth_native.one_shot_snapshot(timeout=0.05)
                self.assertEqual(len(made), 1)
                self.assertEqual(len(live()), 1)
            finally:
                release.set()
                for t in live():
                    t.join(15)
            self.assertEqual(live(), [])
            self.assertEqual(bluetooth_native.one_shot_snapshot(timeout=15), snapshot(True))
        self.assertEqual(len(made), 2)                      # the slot is free again

    def test_a_failed_one_shot_read_is_not_admitted_again(self):
        provider = bluetooth.BluetoothProvider(native=True)
        done = mock.Mock(returncode=0, stdout=json.dumps(snapshot(True)).encode(), stderr=b"")
        with mock.patch.object(bluetooth_native, "one_shot_snapshot",
                               side_effect=TimeoutError("took longer than 30 s")) as native, \
                mock.patch.object(bluetooth.subprocess, "run", return_value=done):
            provider._query()
            provider._query()
        native.assert_called_once()
        self.assertFalse(provider.native)
        self.assertTrue(any("using PowerShell from now on" in line for line in provider.diagnostics()))


@unittest.skipUnless(sys.platform == "win32", "Windows DLLs")
class ReaderSetupTests(unittest.TestCase):
    """Loads the Windows DLLs and the WinRT factories; reads no device (no query, no snapshot)."""

    def test_reader_starts_and_closes_on_its_own_thread(self):
        box = {}

        def work():
            reader = bluetooth_native.NativeReader()
            box["ok"], box["error"] = reader.winrt_ok, reader.winrt_error
            self.assertEqual(reader.connections([]), [])
            reader.close()
            reader.close()                                # a second close is harmless

        t = threading.Thread(target=work)
        t.start()
        t.join(30)
        self.assertTrue(box.get("ok"), box.get("error"))


if __name__ == "__main__":
    unittest.main()
