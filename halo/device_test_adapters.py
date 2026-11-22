"""Targeted diagnostic adapters for three already sourced release readers.

The protocols, report sizes and native I/O remain in their existing providers:
AJ159 F7/05 (providers.ajazz), H9 II status (providers.sony_h9ii), and PRO X3
50/23 (providers.logitech_centurion). This module selects exactly one recorded
endpoint and calls only that target's reader; it never calls a family poll().
No protocol command, sibling fallback or normal polling policy is added here.
"""
import time


_TARGETS = {
    "aj159": (0x3151, 0x402D, 2, 0xFFFF, 2, "AJAZZ AJ159 PRO"),
    "inzone-h9ii": (0x054C, 0x0FA8, 5, 0xFF04, 1, "Sony INZONE H9 II"),
    "logitech-pro-x3": (0x046D, 0x0B45, 3, 0xFFA0, 1, "Logitech PRO X 3 LIGHTSPEED"),
}


def _load_provider(device):
    if device == "aj159":
        from providers import ajazz as module
        return module, module.AjazzProvider()
    if device == "inzone-h9ii":
        from providers import sony_h9ii as module
        return module, module.SonyH9IIProvider()
    from providers import logitech_centurion as module
    return module, module.LogitechCenturionProvider()


def _select_endpoint(inventory, target):
    fields = ("vendor_id", "product_id", "interface_number", "usage_page", "usage")
    endpoints = {}
    for info in inventory:
        if (not isinstance(info, dict)
                or any(type(info.get(field)) is not int for field in fields)
                or tuple(info[field] for field in fields) != target[:5]):
            continue
        path = info.get("path")
        if not isinstance(path, (str, bytes)):
            continue
        try:
            normalized = path.decode("utf-8") if isinstance(path, bytes) else path
            normalized.encode("utf-8")
        except UnicodeError:
            continue
        if normalized and "\0" not in normalized:
            endpoints.setdefault(normalized.casefold(), info)
    if len(endpoints) != 1:
        raise ValueError("expected exactly one matching status endpoint")
    return next(iter(endpoints.values()))


class TargetedDeviceTester:
    """One uncached reading, used only within the shared diagnostic runtime."""

    def __init__(self, device):
        if device not in _TARGETS:
            raise ValueError("unsupported targeted diagnostic")
        self.device = device
        self._diag = []

    def probe_experimental(self, *, acknowledged=False):
        self._diag = ["[Targeted diagnostic] Windows integration unverified"]
        if acknowledged is not True:
            raise ValueError("experimental acknowledgement required before device access")
        target = _TARGETS[self.device]
        phase = "enumeration"
        offline_errors = ()
        try:
            module, provider = _load_provider(self.device)
            offline = getattr(module, "Offline", None)
            if self.device == "logitech-pro-x3" and isinstance(offline, type) and issubclass(offline, Exception):
                offline_errors = (offline,)
            endpoint = _select_endpoint(module.hidlist.enumerate(target[0]), target)
            phase = "status"
            if self.device == "aj159":
                level, accepted = provider._diagnose_aj159(endpoint)
                result = (level, False, False) if accepted is True else None
            elif self.device == "inzone-h9ii":
                result = provider._read(endpoint["path"], time.monotonic() + module.RECEIVER_BUDGET)
            else:
                result = provider._read_x3(endpoint["path"])
            if result is None:
                self._diag.append("no current status reading accepted")
                return None
            level, charging, known = result
            if (type(level) is not int or not 0 <= level <= 100
                    or type(charging) is not bool or type(known) is not bool):
                self._diag.append("invalid status reading discarded")
                return None
            return {"model": target[5], "transport": "receiver", "percentage": level,
                    "charging": charging if known else None, "charging_known": known,
                    "charging_basis": "sourced status flag" if known else "unknown",
                    "freshness": "unverified"}
        except offline_errors:
            self._diag.append("phase=status: headset offline; no reading accepted")
            return None
        except (OSError, ValueError, TypeError, RuntimeError, ImportError, AttributeError, IndexError) as error:
            # Existing reader diagnostics may contain raw paths, reports or
            # exception text. Publish only a phase and exception class here.
            self._diag.append(f"phase={phase}: {type(error).__name__}; no reading accepted")
            return None

    def diagnostics(self):
        return list(self._diag)
