"""Exercise the real pystray transactions with fake windows, handles and shell."""
import os
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tray_native_identity import hb


@unittest.skipUnless(sys.platform == "win32", "Windows pystray backend")
class NativeTrayLifetimeTests(unittest.TestCase):
    def setUp(self):
        from pystray import _base, _win32
        from pystray._util import win32
        self.win32 = win32

        class NoWindowIcon(_win32.Icon):
            def __init__(self, *args, **kwargs):
                _base.Icon.__init__(self, *args, **kwargs)
                self._hwnd = 12345
                self._icon_handle = None
                self._message_handlers = {}

            def __del__(self):
                pass

            def _update_menu(self):
                pass

        patcher = mock.patch.object(hb.pystray, "Icon", NoWindowIcon)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.old, self.new = object(), object()
        self.icon = hb.tray_icon("test:device", "test", self.old)
        self.destroyed = set()
        patcher = mock.patch.object(hb.tray_dpi, "destroy_icon_handle", self.destroyed.add)
        patcher.start()
        self.addCleanup(patcher.stop)
        handles = {id(self.old): 101, id(self.new): 202}
        def load(img):
            return handles.setdefault(id(img), 300 + len(handles))
        patcher = mock.patch.object(hb.tray_dpi, "load_icon_handle", side_effect=load)
        self.load = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(win32, "Shell_NotifyIcon", return_value=True)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def test_failed_add_retries_without_modifying_a_nonexistent_entry(self):
        self.notify.side_effect = [False, True]
        with self.assertRaisesRegex(OSError, "Shell_NotifyIcon failed"):
            self.icon.visible = True
        self.assertFalse(self.icon.visible)
        self.assertFalse(self.icon._icon_valid)
        self.icon.visible = True
        self.assertTrue(self.icon.visible)
        self.assertTrue(self.icon._icon_valid)
        self.assertEqual([c.args[0] for c in self.notify.call_args_list],
                         [self.win32.NIM_ADD, self.win32.NIM_ADD])
        self.assertEqual(self.load.call_count, 1)

    def test_failed_delete_is_best_effort(self):
        self.icon.visible = True
        self.notify.return_value = False
        self.icon.visible = False
        self.icon.forget_handles()
        self.assertFalse(self.icon.visible)
        self.assertEqual(self.destroyed, {101})

    def _restore_during_swap(self, pause_at, forget=False, hide=False):
        self.icon._visible = True
        paused, resume, attempted, completed = (threading.Event() for _ in range(4))
        errors, sent, contended, codes = [], [], [], []
        real_lock = self.icon._hb_handle_lock

        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name == "dpi-swap" and not attempted.is_set():
                    acquired = real_lock.acquire(blocking=False)
                    contended.append(not acquired)
                    attempted.set()
                    if acquired:
                        return self
                real_lock.acquire()
                return self

            def __exit__(self, *args):
                real_lock.release()

        self.icon._hb_handle_lock = ObservedLock()

        def pause():
            paused.set()
            if not resume.wait(5):
                raise AssertionError("test did not release the native operation")

        def load(img):
            if img is self.old and pause_at == "load":
                pause()
            return 101 if img is self.old else 202

        def notify(code, data):
            if data.hIcon == 101 and pause_at == "notify":
                pause()
            self.assertNotIn(data.hIcon, self.destroyed)
            sent.append(data.hIcon)
            codes.append(code)
            return True

        self.load.side_effect = load
        self.notify.side_effect = notify

        def restore():
            try:
                self.icon._show()  # Explorer's TaskbarCreated path
            except BaseException as error:
                errors.append(error)

        def swap():
            try:
                if hide:
                    self.icon.visible = False
                elif forget:
                    self.icon.forget_handles()
                else:
                    self.icon.icon = self.new
                    self.icon.discard_cached_handles()
                completed.set()
            except BaseException as error:
                errors.append(error)

        restore_thread = threading.Thread(target=restore, daemon=True)
        swap_thread = threading.Thread(target=swap, name="dpi-swap", daemon=True)
        restore_thread.start()
        try:
            self.assertTrue(paused.wait(5))
            swap_thread.start()
            self.assertTrue(attempted.wait(5))
            self.assertEqual(contended, [True], "native operation did not retain the handle lock")
            self.assertFalse(completed.is_set(), "DPI swap escaped the native transaction lock")
        finally:
            resume.set()
            restore_thread.join(5)
            if swap_thread.ident is not None:
                swap_thread.join(5)
        self.assertFalse(restore_thread.is_alive())
        self.assertFalse(swap_thread.is_alive())
        self.assertEqual(errors, [])
        if hide:
            self.assertEqual(codes, [self.win32.NIM_ADD, self.win32.NIM_DELETE])
            self.assertFalse(self.icon.visible)
            self.assertEqual(self.destroyed, set())
            self.icon.forget_handles()
            self.icon._hide()  # teardown must still DELETE after handles are closed
            self.assertEqual(codes[-1], self.win32.NIM_DELETE)
            self.assertEqual(len(codes), 3)
            return
        self.assertEqual(sent, [101] if forget else [101, 202])
        self.assertEqual(self.destroyed, {101})
        self.assertEqual(list(self.icon._hb_handles.values()), [] if forget else [(self.new, 202)])
        if forget:
            self.icon._show()  # a queued TaskbarCreated after stop must not resurrect it
            self.icon.icon = self.new
            self.assertEqual(sent, [101])
            self.assertEqual(self.load.call_count, 1)

    def test_restore_loading_is_serialized_with_dpi_swap(self):
        self._restore_during_swap("load")

    def test_restore_notification_is_serialized_with_dpi_swap(self):
        self._restore_during_swap("notify")

    def test_hide_waits_for_inflight_explorer_restore_before_deleting(self):
        self._restore_during_swap("notify", hide=True)

    def test_stop_waits_for_restore_and_rejects_late_restores(self):
        self._restore_during_swap("notify", forget=True)

    def test_failed_modify_retries_device_state_and_prunes_only_after_success(self):
        self._failed_swap_retries(visible=True)

    def test_failed_add_during_dpi_change_retries_before_pruning(self):
        self._failed_swap_retries(visible=False)

    def _device_owner(self):
        st = hb.DeviceStatus(key="test:device", name="Test mouse", level=50,
                             charging=False, online=True, source="test")
        app = types.SimpleNamespace(
            lock=threading.RLock(), cfg=dict(hb.DEFAULTS), light_taskbar=False,
            tray_size=20, anim_tick=0, placeholder=None,
            pictogram=lambda st: "mouse", controller_style=lambda st: ("default", False),
            low_for=lambda st: 20, display_name=lambda st: st.name,
            time_left_text=lambda st: "", compute_light=lambda: False)
        owner = hb.DeviceIcon.__new__(hb.DeviceIcon)
        owner.app, owner.key, owner.status = app, st.key, None
        owner.frames, owner._state, owner._images = None, None, {}
        owner.icon = self.icon
        app.icons = {st.key: owner}
        return app, st, owner

    def test_tooltip_failure_is_best_effort_and_image_failure_still_retries(self):
        app, st, owner = self._device_owner()
        self.icon._visible = True
        self.notify.side_effect = [True, False]  # image accepted; tooltip rejected
        owner.update(st)
        self.assertIsNotNone(owner._state)
        self.assertTrue(self.icon._icon_valid)
        self.assertEqual([call.args[1].uFlags for call in self.notify.call_args_list],
                         [self.win32.NIF_ICON, self.win32.NIF_TIP])
        self.notify.reset_mock()
        self.notify.side_effect = None
        owner.update(st)
        self.notify.assert_not_called()
        old_state = owner._state
        st.level = 51
        self.notify.side_effect = [False, True, True]
        with self.assertRaisesRegex(OSError, "Shell_NotifyIcon failed"):
            owner.update(st)
        self.assertEqual(owner._state, old_state)
        self.assertFalse(self.icon._icon_valid)
        owner.update(st)
        self.assertEqual(owner._state[0], 51)
        self.assertTrue(self.icon._icon_valid)

    def _failed_swap_retries(self, visible):
        app, st, owner = self._device_owner()
        # Seed a visible 20px icon with a successful update.
        self.icon._visible = True
        owner.update(st)
        self.assertTrue(self.icon._icon_valid)
        old_handles = dict(self.icon._hb_handles)
        self.icon._visible = visible
        self.notify.return_value = False
        with mock.patch.object(hb.tray_dpi, "tray_pixels", return_value=24), \
                mock.patch.object(hb.icons, "mydockfinder_running", return_value=False):
            with self.assertRaisesRegex(OSError, "Shell_NotifyIcon failed"):
                hb.App.refresh_theme(app)
            self.assertEqual(app.tray_size, 20)
            self.assertIsNone(owner._state)
            self.assertFalse(self.icon._icon_valid)
            self.assertTrue(all(k in self.icon._hb_handles for k in old_handles))
            self.assertEqual(self.destroyed, set())
            self.notify.return_value = True
            hb.App.refresh_theme(app)
        self.assertEqual(app.tray_size, 24)
        self.assertEqual(owner._state[9], 24)
        self.assertTrue(self.icon._icon_valid)
        self.assertEqual(len(self.icon._hb_handles), 1)
        self.assertEqual(self.notify.call_args.args[0],
                         self.win32.NIM_MODIFY if visible else self.win32.NIM_ADD)
        self.assertTrue({h for _, h in old_handles.values()} <= self.destroyed)
        self.assertNotIn(self.icon._icon_handle, self.destroyed)


if __name__ == "__main__":
    unittest.main()
