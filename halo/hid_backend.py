"""Keep non-HID app features available when the optional native HID import fails.

Providers historically import ``hid`` directly. A failed native import must not
make importing those providers terminate the application before it can explain
the failure. The unavailable facade reports no discovered devices and refuses
every device construction; it does not emulate a functioning HID backend.
"""
from __future__ import annotations

import importlib
import sys
import threading
from types import ModuleType

_lock = threading.RLock()
_UNAVAILABLE_MARKER = "_halobattery_hid_unavailable"
_ERROR_ATTRIBUTE = "_halobattery_hid_import_error"


class _UnavailableHid(ModuleType):
    def __init__(self, error: str):
        super().__init__("hid", "Native HID backend unavailable; no device access is permitted.")
        setattr(self, _UNAVAILABLE_MARKER, True)
        setattr(self, _ERROR_ATTRIBUTE, error)

    def enumerate(self, *args, **kwargs):
        """No native backend means no discoverable HID devices."""
        return []

    def device(self, *args, **kwargs):
        raise OSError("HID backend unavailable: " + getattr(self, _ERROR_ATTRIBUTE))


def load_hid() -> tuple[ModuleType | None, str]:
    """Return the genuine backend unchanged, or None plus its import failure.

    A failed import installs one process-local facade for legacy provider imports.
    Repeated calls, including after this helper is reloaded, preserve that failure
    rather than silently switching an already-running application to another
    backend. The application itself receives None and must disclose the error.
    """
    with _lock:
        current = sys.modules.get("hid")
        if getattr(current, _UNAVAILABLE_MARKER, False) is True:
            return None, getattr(current, _ERROR_ATTRIBUTE)
        try:
            backend = importlib.import_module("hid")
        except (ImportError, OSError) as exc:
            error = type(exc).__name__ + ": " + str(exc)
            # importlib normally removes a failed extension's partial module.
            # Do not overwrite a genuine backend supplied by another importer.
            current = sys.modules.get("hid")
            if current is not None and getattr(current, _UNAVAILABLE_MARKER, False) is not True:
                return current, ""
            if current is None:
                current = _UnavailableHid(error)
                sys.modules["hid"] = current
            return None, getattr(current, _ERROR_ATTRIBUTE)
        if getattr(backend, _UNAVAILABLE_MARKER, False) is True:
            return None, getattr(backend, _ERROR_ATTRIBUTE)
        return backend, ""
