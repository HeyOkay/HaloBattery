"""One temporary root for everything the tests create, removed when the run ends.

Tests call ``mkdtemp`` from here instead of ``tempfile.mkdtemp``; nothing is left
behind in %TEMP%, even for tests that never clean up after themselves.
"""
import atexit
import logging
import shutil
import tempfile

_root = None


def root():
    """The folder every sandboxed directory lives in (created on first use)."""
    global _root
    if _root is None:
        _root = tempfile.mkdtemp(prefix="halo_tests_")
        atexit.register(_cleanup)
    return _root


def mkdtemp(prefix="halo_battery_test_"):
    return tempfile.mkdtemp(prefix=prefix, dir=root())


def _cleanup():
    logging.shutdown()          # the app's log file lives in one of the folders
    if _root:
        shutil.rmtree(_root, ignore_errors=True)


def install_guards():
    """Make a test that reaches the real network or a real device fail loudly.

    Tests replace hid.enumerate / hid.device / urlopen with fakes (mock.patch puts the
    guard back afterwards). Anything that gets through to the real one would hit the
    developer's own mouse or the internet, and pass or fail depending on the machine.
    Serial ports and raw USB are covered by install_port_guards().
    """
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to use the network")

    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    install_port_guards()
    install_phone_cache_guard()
    try:
        import hid
    except ImportError:
        return

    def no_enumerate(*args, **kwargs):
        raise AssertionError("a test tried to enumerate real HID devices")

    class NoDevice:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a test tried to open a real HID device")

    hid.enumerate = no_enumerate
    hid.device = NoDevice


def install_phone_cache_guard():
    """No all-provider test may inspect the developer's linked-phone cache.

    Dedicated reader tests may explicitly supply a directory below our shared
    temporary root. The ordinary default boundary fails loudly until a test
    injects its missing/synthetic snapshot.
    """
    import sys

    # Startup tests deliberately have not selected the app's import path yet.
    # Importing providers here can execute an unrelated package in their cwd.
    module = sys.modules.get("providers.phone_link")
    if module is not None:
        _guard_phone_cache_reader(module)
    if not any(getattr(finder, "_phone_cache_sandbox", False) for finder in sys.meta_path):
        sys.meta_path.insert(0, _PhoneCacheFinder())


def _guard_phone_cache_reader(phone_link):
    from pathlib import Path

    reader = phone_link._read_snapshot
    if getattr(reader, "_phone_cache_guard", False):
        return

    def sandboxed_reader(package_root=None):
        if (package_root is None
                or not Path(package_root).resolve().is_relative_to(Path(root()).resolve())):
            raise AssertionError("a test tried to read real Phone Link local state")
        return reader(package_root)

    sandboxed_reader._phone_cache_guard = True
    phone_link._read_snapshot = sandboxed_reader


class _PhoneCacheFinder:
    """Wrap only the cache module after normal import resolution chooses it."""
    _phone_cache_sandbox = True

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "providers.phone_link":
            return None
        import sys

        # Preserve the normal finder order, including any bundled-module finder.
        # Never resolve/import the parent package ourselves.
        for finder in sys.meta_path:
            if finder is self:
                continue
            find_spec = getattr(finder, "find_spec", None)
            spec = find_spec(fullname, path, target) if find_spec else None
            if spec is not None:
                if not callable(getattr(spec.loader, "exec_module", None)):
                    raise ImportError("cannot install the Phone Link test cache guard")
                spec.loader = _PhoneCacheLoader(spec.loader)
                return spec
        return None


class _PhoneCacheLoader:
    def __init__(self, loader):
        self._loader = loader

    def create_module(self, spec):
        create = getattr(self._loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        self._loader.exec_module(module)
        _guard_phone_cache_reader(module)

    def __getattr__(self, name):
        return getattr(self._loader, name)


def install_port_guards():
    """The same for the two ways to a device that are not HID.

    The Sony INZONE provider lists the serial ports and writes to the one it recognises,
    and the DJI provider looks for its receiver on raw USB. Neither goes through hid.*,
    so a test that polls every provider reached the real ports of the machine running it.
    """
    def no_ports(*args, **kwargs):
        raise AssertionError("a test tried to list the real serial ports")

    class NoSerial:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a test tried to open a real serial port")

    def no_usb(*args, **kwargs):
        raise AssertionError("a test tried to look for a real USB device")

    def no_backend(*args, **kwargs):
        raise AssertionError("a test tried to load the native USB backend")

    try:
        import libusb_package
    except ImportError:
        pass
    else:
        libusb_package.get_libusb1_backend = no_backend
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        pass
    else:
        list_ports.comports = no_ports
        # Existing imports and subclasses retain this class when Serial is replaced.
        serial.Serial.open = NoSerial.__init__
        serial.Serial = NoSerial
    try:
        import usb.core
    except ImportError:
        pass
    else:
        usb.core.find = no_usb
