"""Bluetooth snapshot read inside the app: the same data the PowerShell helper collects, through
Windows' own DLLs with ctypes. No PowerShell process, no extra packages.

    NativeReader().snapshot()   -> {"devs", "props", "aep", "aep_error", "roots"}  (the script's JSON shape)
    NativeReader().connections(roots)                                       (the watcher's 2 s check)

PnP   cfgmgr32: present BTHENUM / BTHLE / BTHLEDEVICE nodes, their name and status, and the
      battery {104EA319-...} 2 / connection {83DA6326-...} 15 properties, read node by node.
      PowerShell's bulk Get-PnpDeviceProperty returned a different subset on consecutive runs
      (4 to 13 of the same 13 values, sometimes without the battery); a per-node read does not.
WinRT combase: BluetoothDevice / BluetoothLEDevice.FromBluetoothAddressAsync(MAC) ->
      ConnectionStatus and ClassOfDevice / Appearance, exactly as the script's Get-BtConn.

Interface ids and vtable slots come from the metadata Windows ships
(C:\\Windows\\System32\\WinMetadata\\Windows.Devices.winmd, Windows.Foundation.winmd):
slot = 6 + method index (IUnknown 3 + IInspectable 3). Nothing is loaded at import time.
"""
from __future__ import annotations

import ctypes
import re
import threading
import time
import uuid
from ctypes import POINTER, byref, c_int32, c_uint16, c_uint32, c_uint64, c_ulong, c_void_p
from typing import List, Optional, Tuple

K_BAT = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"
K_CONN = "{83DA6326-97A6-4088-9453-A1923F573B29} 15"
DEVPKEY_NAME = "{B725F130-47EF-101A-A5F1-02608C9EEBAC} 10"   # what Get-PnpDevice shows as FriendlyName
ENUMERATORS = ("BTHENUM", "BTHLE", "BTHLEDEVICE")
QUERY_TIMEOUT_MS = 3000                                      # per device, as the script's Wait(3000)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


def _propkey(text: str) -> DEVPROPKEY:
    """'{GUID} pid' -> DEVPROPKEY"""
    g, pid = text.split()
    return DEVPROPKEY(_guid(g), int(pid))


# ----------------------------------------------------------------------------- DLLs (lazy)

_dll_lock = threading.Lock()
_cfg = None
_combase = None


def _dlls():
    """cfgmgr32 and combase with their prototypes, loaded on first use only."""
    global _cfg, _combase
    with _dll_lock:
        if _cfg is None:
            from ctypes import wintypes
            cfg = ctypes.WinDLL("cfgmgr32.dll")
            cfg.CM_Get_Device_ID_List_SizeW.argtypes = [POINTER(c_ulong), wintypes.LPCWSTR, c_ulong]
            cfg.CM_Get_Device_ID_List_SizeW.restype = c_ulong
            cfg.CM_Get_Device_ID_ListW.argtypes = [wintypes.LPCWSTR, c_void_p, c_ulong, c_ulong]
            cfg.CM_Get_Device_ID_ListW.restype = c_ulong
            cfg.CM_Locate_DevNodeW.argtypes = [POINTER(c_ulong), wintypes.LPCWSTR, c_ulong]
            cfg.CM_Locate_DevNodeW.restype = c_ulong
            cfg.CM_Get_DevNode_PropertyW.argtypes = [c_ulong, POINTER(DEVPROPKEY), POINTER(c_ulong),
                                                     c_void_p, POINTER(c_ulong), c_ulong]
            cfg.CM_Get_DevNode_PropertyW.restype = c_ulong
            cfg.CM_Get_DevNode_Status.argtypes = [POINTER(c_ulong), POINTER(c_ulong), c_ulong, c_ulong]
            cfg.CM_Get_DevNode_Status.restype = c_ulong
            com = ctypes.WinDLL("combase.dll")
            com.RoInitialize.argtypes = [c_int32]
            com.RoInitialize.restype = c_int32
            com.RoUninitialize.argtypes = []
            com.RoUninitialize.restype = None
            com.WindowsCreateString.argtypes = [wintypes.LPCWSTR, c_uint32, POINTER(c_void_p)]
            com.WindowsCreateString.restype = c_int32
            com.WindowsDeleteString.argtypes = [c_void_p]
            com.WindowsDeleteString.restype = c_int32
            com.RoGetActivationFactory.argtypes = [c_void_p, POINTER(GUID), POINTER(c_void_p)]
            com.RoGetActivationFactory.restype = c_int32
            _cfg, _combase = cfg, com
    assert _cfg is not None and _combase is not None
    return _cfg, _combase


# ----------------------------------------------------------------------------- PnP (cfgmgr32)

CR_SUCCESS, CR_BUFFER_SMALL = 0x00, 0x1A
CM_GETIDLIST_FILTER_ENUMERATOR, CM_GETIDLIST_FILTER_PRESENT = 0x001, 0x100
DN_HAS_PROBLEM = 0x00000400
DEVPROP_TYPE_BYTE, DEVPROP_TYPE_UINT32, DEVPROP_TYPE_BOOLEAN, DEVPROP_TYPE_STRING = 0x03, 0x07, 0x11, 0x12


def _id_list(cfg, enumerator: str) -> List[str]:
    flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    for _ in range(4):                                       # the list can grow between the two calls
        size = c_ulong(0)
        if cfg.CM_Get_Device_ID_List_SizeW(byref(size), enumerator, flags) != CR_SUCCESS:
            return []
        buf = ctypes.create_unicode_buffer(size.value + 1)
        cr = cfg.CM_Get_Device_ID_ListW(enumerator, buf, size.value + 1, flags)
        if cr == CR_BUFFER_SMALL:
            continue
        if cr != CR_SUCCESS:
            return []
        raw = ctypes.wstring_at(ctypes.addressof(buf), size.value + 1)
        return [s for s in raw.split("\x00") if s]
    return []


def _prop(cfg, devinst: int, key: DEVPROPKEY):
    """Typed value of one devnode property, or None when the node does not have it."""
    ptype, size = c_ulong(0), c_ulong(0)
    cr = cfg.CM_Get_DevNode_PropertyW(devinst, byref(key), byref(ptype), None, byref(size), 0)
    if cr != CR_BUFFER_SMALL or size.value == 0:
        return None
    buf = (ctypes.c_ubyte * size.value)()
    if cfg.CM_Get_DevNode_PropertyW(devinst, byref(key), byref(ptype), buf, byref(size), 0) != CR_SUCCESS:
        return None
    t = ptype.value
    if t == DEVPROP_TYPE_BYTE:
        return int(buf[0])
    if t == DEVPROP_TYPE_BOOLEAN:
        return buf[0] != 0
    if t == DEVPROP_TYPE_UINT32:
        return int.from_bytes(bytes(buf[:4]), "little")
    if t == DEVPROP_TYPE_STRING:
        return ctypes.wstring_at(ctypes.addressof(buf), size.value // 2).rstrip("\x00")
    return None                                              # a type the snapshot never used


def pnp_nodes() -> Tuple[List[dict], List[dict]]:
    """(devs, props) in the PowerShell snapshot's shape; instance ids upper-case like Get-PnpDevice."""
    cfg, _ = _dlls()
    k_name, k_bat, k_conn = _propkey(DEVPKEY_NAME), _propkey(K_BAT), _propkey(K_CONN)
    devs, props = [], []
    for enum in ENUMERATORS:
        for iid in _id_list(cfg, enum):
            devinst = c_ulong(0)
            if cfg.CM_Locate_DevNodeW(byref(devinst), iid, 0) != CR_SUCCESS:
                continue
            st, problem = c_ulong(0), c_ulong(0)
            if cfg.CM_Get_DevNode_Status(byref(st), byref(problem), devinst.value, 0) == CR_SUCCESS:
                status = "Error" if (st.value & DN_HAS_PROBLEM) or problem.value else "OK"
            else:
                status = "Unknown"
            uid = iid.upper()
            devs.append({"id": uid, "name": _prop(cfg, devinst.value, k_name), "status": status})
            for text, key in ((K_BAT, k_bat), (K_CONN, k_conn)):
                value = _prop(cfg, devinst.value, key)
                if value is not None:
                    props.append({"id": uid, "key": text, "data": value})
    return devs, props


_ROOT = re.compile(r"^(BTHENUM|BTHLE)\\DEV_([0-9A-F]{12})", re.I)


def roots_of(devs: List[dict]) -> List[dict]:
    """The device roots the WinRT query needs, as the script's $roots."""
    out = []
    for d in devs:
        m = _ROOT.match(d.get("id") or "")
        if m:
            out.append({"le": m.group(1).upper() == "BTHLE", "mac": m.group(2).upper()})
    return out


def connection_key(aep: List[dict]) -> str:
    """Changes whenever a device connects or disconnects (the script's Get-ConnKey)."""
    return ",".join(f"{a.get('addr')}={a.get('conn')}" for a in aep)


# ----------------------------------------------------------------------------- WinRT (combase)

IID_IBluetoothDeviceStatics = _guid("{0991DF51-57DB-4725-BBD7-84F64327EC2C}")
IID_IBluetoothLEDeviceStatics = _guid("{C8CF1A19-F0B6-4BF0-8689-41303DE2D9F4}")
IID_IBluetoothLEDevice2 = _guid("{26F062B3-7AEE-4D31-BABA-B1B9775F5916}")
IID_IAsyncInfo = _guid("{00000036-0000-0000-C000-000000000046}")
IID_IClosable = _guid("{30D5A829-7FA4-4026-83BB-D75BAE4EA99E}")

S_QI, S_RELEASE = 0, 2
S_CLASSIC_FROM_ADDR, S_LE_FROM_ADDR = 8, 7                   # *Statics.FromBluetoothAddressAsync
S_CLASSIC_CONN, S_CLASSIC_COD = 12, 9                        # IBluetoothDevice
S_LE_CONN, S_LE2_APPEARANCE = 9, 7                           # IBluetoothLEDevice, IBluetoothLEDevice2
S_RAWVALUE = 6                                               # IBluetoothClassOfDevice / IBluetoothLEAppearance
S_ASYNC_STATUS, S_ASYNC_ERROR, S_ASYNC_CANCEL, S_ASYNC_CLOSE = 7, 8, 9, 10
S_OP_RESULTS = 8                                             # IAsyncOperation<T>.GetResults
S_CLOSE = 6                                                  # IClosable.Close
ASYNC_STARTED, ASYNC_COMPLETED = 0, 1
CONNECTED = 1                                                # BluetoothConnectionStatus.Connected
RPC_E_CHANGED_MODE = -2147417850                             # 0x80010106: the thread is already STA


def _call(ptr: int, slot: int, *args, argtypes=()):
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    return ctypes.WINFUNCTYPE(c_int32, c_void_p, *argtypes)(vtbl[slot])(ptr, *args)


def _release(ptr) -> None:
    if ptr:
        vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
        ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vtbl[S_RELEASE])(ptr)


def _qi(ptr: int, iid: GUID) -> Optional[int]:
    out = c_void_p()
    hr = _call(ptr, S_QI, byref(iid), byref(out), argtypes=(POINTER(GUID), POINTER(c_void_p)))
    return out.value if hr >= 0 and out.value else None


def _hr(hr: int) -> str:
    return f"HRESULT 0x{hr & 0xFFFFFFFF:08X}"


class _WinRT:
    """The two activation factories, for the thread that created them."""

    def __init__(self):
        _, com = _dlls()
        self._com = com
        hr = com.RoInitialize(1)                             # RO_INIT_MULTITHREADED
        if hr < 0 and hr != RPC_E_CHANGED_MODE:
            raise OSError(f"RoInitialize failed: {_hr(hr)}")
        self._uninit = hr >= 0                               # S_OK / S_FALSE pair with RoUninitialize
        self.classic = self.le = 0
        try:
            self.classic = self._factory("Windows.Devices.Bluetooth.BluetoothDevice", IID_IBluetoothDeviceStatics)
            self.le = self._factory("Windows.Devices.Bluetooth.BluetoothLEDevice", IID_IBluetoothLEDeviceStatics)
        except Exception:
            self.close()
            raise

    def _factory(self, name: str, iid: GUID) -> int:
        hs, fac = c_void_p(), c_void_p()
        hr = self._com.WindowsCreateString(name, len(name), byref(hs))
        if hr < 0:
            raise OSError(f"WindowsCreateString failed: {_hr(hr)}")
        try:
            hr = self._com.RoGetActivationFactory(hs, byref(iid), byref(fac))
        finally:
            self._com.WindowsDeleteString(hs)
        if hr < 0 or not fac.value:
            raise OSError(f"RoGetActivationFactory({name}) failed: {_hr(hr)}")
        return fac.value

    def close(self) -> None:
        _release(self.classic)
        _release(self.le)
        self.classic = self.le = 0
        if self._uninit:
            self._uninit = False
            self._com.RoUninitialize()

    def _await(self, op: int, timeout_ms: int) -> Tuple[Optional[int], Optional[str]]:
        """(result pointer or None, error or None) for one IAsyncOperation<T>."""
        info = _qi(op, IID_IAsyncInfo)
        if not info:
            return None, "no IAsyncInfo"
        try:
            status = c_int32(ASYNC_STARTED)
            deadline = time.monotonic() + timeout_ms / 1000
            while True:
                hr = _call(info, S_ASYNC_STATUS, byref(status), argtypes=(POINTER(c_int32),))
                if hr < 0:
                    return None, f"get_Status {_hr(hr)}"
                if status.value != ASYNC_STARTED:
                    break
                if time.monotonic() >= deadline:
                    _call(info, S_ASYNC_CANCEL)
                    return None, "timeout"
                time.sleep(0.005)
            if status.value != ASYNC_COMPLETED:
                code = c_int32(0)
                _call(info, S_ASYNC_ERROR, byref(code), argtypes=(POINTER(c_int32),))
                return None, f"async status {status.value}, {_hr(code.value)}"
            result = c_void_p()
            hr = _call(op, S_OP_RESULTS, byref(result), argtypes=(POINTER(c_void_p),))
            if hr < 0:
                return None, f"GetResults {_hr(hr)}"
            return result.value, None                        # None: Windows does not know the address
        finally:
            _call(info, S_ASYNC_CLOSE)
            _release(info)

    def query(self, mac: str, le: bool, timeout_ms: int = QUERY_TIMEOUT_MS) -> dict:
        """One root: {"addr", "conn", "le", "err", "cls"}, as the script's Get-BtConn."""
        conn = err = cls = None
        op = c_void_p()
        try:
            addr = c_uint64(int(mac, 16))
            if le:
                hr = _call(self.le, S_LE_FROM_ADDR, addr, byref(op), argtypes=(c_uint64, POINTER(c_void_p)))
            else:
                hr = _call(self.classic, S_CLASSIC_FROM_ADDR, addr, byref(op),
                           argtypes=(c_uint64, POINTER(c_void_p)))
            if hr < 0 or not op.value:
                err = f"FromBluetoothAddressAsync {_hr(hr)}"
            else:
                dev, err = self._await(op.value, timeout_ms)
                if dev:
                    try:
                        st = c_int32(0)
                        hr = _call(dev, S_LE_CONN if le else S_CLASSIC_CONN, byref(st),
                                   argtypes=(POINTER(c_int32),))
                        if hr >= 0:
                            conn = st.value == CONNECTED
                        cls = self._class_value(dev, le)
                    finally:
                        closable = _qi(dev, IID_IClosable)
                        if closable:
                            _call(closable, S_CLOSE)
                            _release(closable)
                        _release(dev)
        except Exception as e:                               # one root must not break the snapshot
            err = f"{type(e).__name__}: {e}"
        finally:
            _release(op.value)
        return {"addr": mac, "conn": conn, "le": le, "err": err, "cls": cls}

    @staticmethod
    def _class_value(dev: int, le: bool) -> Optional[int]:
        holder = c_void_p()
        if le:
            dev2 = _qi(dev, IID_IBluetoothLEDevice2)
            if not dev2:
                return None
            try:
                if _call(dev2, S_LE2_APPEARANCE, byref(holder), argtypes=(POINTER(c_void_p),)) < 0:
                    return None
            finally:
                _release(dev2)
            raw = c_uint16(0)
        else:
            if _call(dev, S_CLASSIC_COD, byref(holder), argtypes=(POINTER(c_void_p),)) < 0:
                return None
            raw = c_uint32(0)
        try:
            if not holder.value:
                return None
            hr = _call(holder.value, S_RAWVALUE, byref(raw), argtypes=(POINTER(type(raw)),))
            return int(raw.value) if hr >= 0 else None
        finally:
            _release(holder.value)


# ----------------------------------------------------------------------------- the reader

class NativeReader:
    """Snapshot and connection checks for one thread. Create, use and close() it on that thread.
    When WinRT cannot start, the snapshot still has the PnP part and names the error in
    "aep_error", which makes the provider fall back to the PnP connection flag - the same as
    the script when Add-Type fails."""

    def __init__(self):
        self.winrt: Optional[_WinRT] = None
        self.winrt_error: Optional[str] = None
        try:
            self.winrt = _WinRT()
        except Exception as e:
            self.winrt_error = f"{type(e).__name__}: {e}"

    @property
    def winrt_ok(self) -> bool:
        return self.winrt is not None

    def connections(self, roots: List[dict]) -> List[dict]:
        if self.winrt is None:
            return []
        return [self.winrt.query(r["mac"], bool(r["le"])) for r in roots]

    def snapshot(self) -> dict:
        devs, props = pnp_nodes()
        roots = roots_of(devs)
        return {"devs": devs, "props": props, "aep": self.connections(roots),
                "aep_error": self.winrt_error, "roots": roots}

    def close(self) -> None:
        if self.winrt is not None:
            self.winrt.close()
            self.winrt = None


# The one-shot read's thread. A read that timed out can stay blocked in a Windows call; while
# it is, the next call is refused instead of starting a second thread beside it, so at most
# one such thread exists at any time (#513 review).
_ONE_SHOT_LOCK = threading.Lock()
_one_shot: Optional[threading.Thread] = None


def one_shot_snapshot(timeout: float = 30.0) -> dict:
    """A complete snapshot read on a short-lived thread of its own, so the caller's COM
    apartment does not matter. Raises on failure or timeout, and at once while an earlier
    read is still running."""
    global _one_shot
    box: dict = {}

    def work():
        reader = None
        try:
            reader = NativeReader()
            box["snap"] = reader.snapshot()
        except BaseException as e:                           # handed to the caller below
            box["err"] = e
        finally:
            if reader is not None:
                reader.close()

    with _ONE_SHOT_LOCK:
        if _one_shot is not None and _one_shot.is_alive():
            raise TimeoutError("an earlier in-process Bluetooth read is still running")
        t = threading.Thread(target=work, name="bt-native-snapshot", daemon=True)
        _one_shot = t
        t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"native Bluetooth snapshot took longer than {timeout:.0f} s")
    if "err" in box:
        raise box["err"]
    return box["snap"]
