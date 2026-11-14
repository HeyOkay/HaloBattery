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

    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        pass
    else:
        list_ports.comports = no_ports
        serial.Serial = NoSerial
    try:
        import usb.core
    except ImportError:
        pass
    else:
        usb.core.find = no_usb
