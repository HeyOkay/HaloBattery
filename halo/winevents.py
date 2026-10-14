"""Instant notice of window changes, for following MyDockFinder's bar colour.

MyDockFinder switches its menu bar colour the moment a window is maximized,
restored, snapped, minimized, closed or brought to the front. Polling the
screen can only catch that on the next tick, so this module listens to the
same system events (SetWinEventHook, out of context: nothing is injected into
other processes) and sets a threading.Event on each one. The app then
re-checks the colour right away.

Only top-level windows are considered; cursor and caret movements, and events
inside windows (controls, text) are ignored. Windows only; start() is a no-op
elsewhere.
"""
from __future__ import annotations

import sys
import threading
from typing import Optional

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_SYSTEM_MINIMIZEEND = 0x0017
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
WM_QUIT = 0x0012

# (first, last) event ranges to hook
RANGES = (
    (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
    (EVENT_SYSTEM_MOVESIZEEND, EVENT_SYSTEM_MOVESIZEEND),
    (EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND),
    (EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE),
    (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),  # maximize / restore / snap / drag
)


class WindowEventWatcher:
    """Sets `event` whenever a top-level window appears, disappears, moves,
    changes size or becomes the foreground window."""

    def __init__(self, event: threading.Event):
        self.event = event
        self.count = 0
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ok = False

    def start(self) -> bool:
        if sys.platform != "win32" or self._thread is not None:
            return self._ok
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True,
                                        name="winevents")
        self._thread.start()
        ready.wait(2.0)
        return self._ok

    def running(self) -> bool:
        return self._ok and self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        if self._thread_id:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass

    def _run(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

        proc_type = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)
        user32.SetWinEventHook.restype = ctypes.c_void_p
        user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, proc_type,
                                           wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
        user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT]

        def callback(_hook, event, hwnd, id_object, id_child, _thread, _time):
            try:
                if event != EVENT_SYSTEM_FOREGROUND:
                    # only whole windows, and only top-level ones
                    if id_object != OBJID_WINDOW or id_child != 0 or not hwnd:
                        return
                    if event != EVENT_OBJECT_DESTROY and user32.GetAncestor(hwnd, 2) != hwnd:  # GA_ROOT
                        return
                self.count += 1
                self.event.set()
            except Exception:
                pass

        self._proc = proc_type(callback)            # keep a reference: ctypes callbacks are not owned
        hooks = []
        for first, last in RANGES:
            h = user32.SetWinEventHook(first, last, None, self._proc, 0, 0,
                                       WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
            if h:
                hooks.append(h)
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ok = bool(hooks)
        ready.set()
        if not hooks:
            return
        msg = wintypes.MSG()
        try:
            # out-of-context hooks are delivered through this thread's message queue
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for h in hooks:
                user32.UnhookWinEvent(h)
            self._ok = False
