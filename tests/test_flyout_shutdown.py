"""Owner-thread Tk shutdown; no real window, device or network in unit tests."""
import queue
import threading
import time
import types
import unittest
from unittest import mock

from halo import flyout


class FlyoutShutdownTests(unittest.TestCase):
    def test_concurrent_first_menu_and_window_start_only_one_owner(self):
        host = flyout.FlyoutHost(object())
        entered, release = threading.Event(), threading.Event()
        second_constructed, window_entered = threading.Event(), threading.Event()
        real_thread = threading.Thread
        owners, results, errors = [], [], []
        callback = mock.Mock()
        host._root = types.SimpleNamespace(after=lambda delay, fn: fn())
        host._main = host._ready.set
        host._show = mock.Mock()

        def owner_factory(*args, **kwargs):
            owner = real_thread(*args, **kwargs)
            owners.append(owner)
            if len(owners) == 1:
                entered.set()
                if not release.wait(2):
                    raise AssertionError("first owner construction was not released")
            else:
                second_constructed.set()
            return owner

        def menu():
            try:
                results.append(host.show(None, None, (0, 0)))
            except Exception as error:
                errors.append(error)

        def window():
            window_entered.set()
            try:
                results.append(host.call(callback))
            except Exception as error:
                errors.append(error)

        menu_caller = real_thread(target=menu)
        window_caller = real_thread(target=window)
        with mock.patch.object(flyout.threading, "Thread", side_effect=owner_factory):
            menu_caller.start()
            try:
                self.assertTrue(entered.wait(1))
                window_caller.start()
                self.assertTrue(window_entered.wait(1))
                # Keep the first constructor paused while a second first-use
                # callback arrives. It must wait instead of constructing Tk again.
                raced = second_constructed.wait(.15)
            finally:
                release.set()
                menu_caller.join(2)
                if window_caller.ident is not None:
                    window_caller.join(2)
        for owner in owners:
            owner.join(1)
        self.assertFalse(menu_caller.is_alive())
        self.assertFalse(window_caller.is_alive())
        self.assertFalse(raced)
        self.assertEqual(errors, [])
        self.assertEqual(len(owners), 1)
        self.assertIs(host._thread, owners[0])
        self.assertEqual(results, [True, True])
        host._show.assert_called_once_with(None, None, (0, 0))
        callback.assert_called_once_with()
        host.start()
        self.assertIs(host._thread, owners[0])

    def test_failed_owner_start_can_be_retried(self):
        host = flyout.FlyoutHost(object())
        failed, ready = mock.Mock(), mock.Mock()
        failed.start.side_effect = RuntimeError("thread unavailable")
        with mock.patch.object(flyout.threading, "Thread", side_effect=[failed, ready]):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                host.start()
            self.assertIsNone(host._thread)
            host.start()
            host.start()
        self.assertIs(host._thread, ready)
        ready.start.assert_called_once_with()

    def test_stop_never_calls_tk_when_the_owner_loop_cannot_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        class StalledRoot:
            def after(self, *args):
                entered.set()
                release.wait(2)
            def quit(self):
                raise AssertionError("non-owner stop must not invoke Tk")
        host = flyout.FlyoutHost(object())
        host._root = StalledRoot()
        worker = threading.Thread(target=host.stop)
        self.addCleanup(release.set)
        worker.start()
        try:
            worker.join(0.2)
            self.assertFalse(worker.is_alive(), "stop waited for the stalled Tk owner")
            self.assertFalse(entered.is_set(), "stop made a cross-thread Tk call")
        finally:
            release.set()
            worker.join(2)

    def test_stopped_host_does_not_start_or_accept_new_menu_work(self):
        host = flyout.FlyoutHost(object())
        host.stop()
        with mock.patch.object(threading, "Thread", side_effect=AssertionError("late thread")):
            host.start()
            self.assertFalse(host.call(lambda: self.fail("late callback")))
            self.assertFalse(host.show(None, None))
        self.assertIsNone(host._thread)

    def test_existing_owner_loop_quits_and_destroys_on_its_own_thread(self):
        recorded = {}
        normal_callback = threading.Event()
        class Root:
            def __init__(self):
                self.owner = threading.get_ident()
                self.pending = queue.Queue()
                self.stopped = False
                recorded["root"] = self
                self.calls = []
            def check(self, operation):
                ident = threading.get_ident()
                self.calls.append((operation, ident))
                if ident != self.owner:
                    raise AssertionError("Tk called from a non-owner thread")
            def withdraw(self):
                self.check("withdraw")
            def bind_all(self, *args, **kwargs):
                self.check("bind_all")
            def after(self, milliseconds, callback):
                self.check("after")
                self.pending.put((milliseconds, callback))
                return "timer"
            def mainloop(self):
                self.check("mainloop")
                self.after(0, normal_callback.set)
                deadline = time.monotonic() + 2
                while not self.stopped and time.monotonic() < deadline:
                    milliseconds, callback = self.pending.get(timeout=1)
                    if milliseconds:
                        time.sleep(milliseconds / 1000)
                    callback()
            def quit(self):
                self.check("quit")
                self.stopped = True
            def destroy(self):
                self.check("destroy")
        tkinter = types.ModuleType("tkinter")
        tkinter.Tk = Root
        font = types.ModuleType("tkinter.font")
        font.families = lambda root: ()
        tkinter.font = font
        host = flyout.FlyoutHost(object())
        with mock.patch.dict("sys.modules", {"tkinter": tkinter, "tkinter.font": font}):
            host.start()
            self.assertTrue(host._ready.wait(1))
            self.assertTrue(normal_callback.wait(1), "normal callbacks stopped running")
            host.stop()
            host._thread.join(1)
        self.assertFalse(host._thread.is_alive())
        root = recorded["root"]
        self.assertTrue(root.stopped)
        self.assertIn(("destroy", root.owner), root.calls)
        self.assertTrue(all(ident == root.owner for _, ident in root.calls))
        self.assertIsNone(host._root)
        self.assertFalse(host.call(lambda: self.fail("post-shutdown callback")))


if __name__ == "__main__":
    unittest.main()
