"""Owner-thread Tk shutdown; no real window, device or network in unit tests."""
import queue
import threading
import time
import types
import unittest
from unittest import mock

from halo import flyout


class FlyoutShutdownTests(unittest.TestCase):
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
