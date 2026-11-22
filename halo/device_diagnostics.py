"""Explicit device diagnostics, usable before normal app initialization.

Only the acknowledged runner imports HID/transport/provider code. Protocols and
endpoint restrictions remain in the providers; this module never polls the
normal provider factory or publishes a tray/history observation.
"""
import sys


_TARGETED_DEVICES = ("aj159", "inzone-h9ii", "logitech-pro-x3")
_DEVICES = ("endgame-4k", "lamzu-tachi") + _TARGETED_DEVICES
# At most one admitted diagnostic can retain native ownership. Do not let a
# subsequent run drop that named object before this diagnostic process exits.
_retained_guard = None
_OWNERS = {"endgame-4k": "endgame-4k-local-tester",
           "lamzu-tachi": "lamzu-tachi-local-tester",
           "aj159": "aj159-local-tester",
           "inzone-h9ii": "inzone-h9ii-local-tester",
           "logitech-pro-x3": "logitech-pro-x3-local-tester"}


def _load_backend():
    from halo.hid_backend import load_hid
    return load_hid()[0]


def _create_runtime():
    from halo.hid_transport import TransportRuntime
    return TransportRuntime()


def _create_instance_guard():
    from halo.diagnostic_guard import InstanceGuard
    try:
        return InstanceGuard()
    except AttributeError as error:
        raise OSError("Windows instance guard unavailable") from error


def _create_provider(device):
    if device == "endgame-4k":
        from providers.endgame_4k import Endgame4kProvider
        return Endgame4kProvider()
    if device == "lamzu-tachi":
        from providers.lamzu_sw import LamzuSinowealthProvider
        return LamzuSinowealthProvider()
    from halo.device_test_adapters import TargetedDeviceTester
    return TargetedDeviceTester(device)


def run(device, *, transport=None, acknowledged=False, load_backend=None,
        create_runtime=None, create_provider=None, create_guard=None):
    """One existing native runtime/scope, with no device access before consent."""
    global _retained_guard
    result = {"experimental": True, "release_hold": device in ("endgame-4k", "lamzu-tachi", "logitech-pro-x3"), "freshness": "unverified",
              "reading": None, "diagnostics": []}
    if (device not in _DEVICES or acknowledged is not True
            or (device == "endgame-4k" and transport not in ("receiver", "wired"))
            or (device != "endgame-4k" and transport is not None)):
        result["diagnostics"].append("explicit acknowledgement and valid device/transport required")
        return result, 1
    runtime = guard = None
    code = 1
    try:
        # Ordinary startup and EVERY diagnostic share this name, including held
        # Endgame/Tachi modes. Hold it through native cleanup so two diagnostic
        # processes cannot query the same receiver concurrently.
        if _retained_guard is not None:
            result["diagnostics"].append("prior native ownership pending; exit this diagnostic process first")
            return result, code
        guard = (create_guard or _create_instance_guard)()
        if not guard.acquire():
            result["diagnostics"].append("Halo Battery or another diagnostic is running; close it first")
            return result, code
        backend = (load_backend or _load_backend)()
        if backend is None:
            result["diagnostics"].append("HID backend unavailable")
            return result, code
        runtime = (create_runtime or _create_runtime)()
        runtime.install(backend)
        provider = create_provider() if create_provider is not None else _create_provider(device)
        with runtime.provider_scope(_OWNERS[device]) as scope:
            if device == "endgame-4k":
                reading = provider.probe_experimental(transport, acknowledged=True)
            else:
                reading = provider.probe_experimental(acknowledged=True)
        result["diagnostics"] = provider.diagnostics()
        if scope.failed:
            result["diagnostics"].append("native transport failed; reading discarded")
        elif reading is not None:
            result["reading"], code = reading, 0
    except (OSError, ValueError, TypeError, RuntimeError, ImportError):
        result["reading"] = None
        result["diagnostics"].append("local test unavailable; no reading accepted")
    finally:
        native_clean = runtime is None
        try:
            if runtime is not None:
                try:
                    native_clean = runtime.close(.5)
                except (OSError, ValueError, RuntimeError):
                    native_clean = False
                if not native_clean:
                    result["reading"] = None
                    result["diagnostics"].append("native cleanup pending; ownership retained")
                    code = 1
        finally:
            if guard is not None:
                if not native_clean:
                    _retained_guard = guard
                    result["diagnostics"].append("instance guard retained until process exit")
                else:
                    try:
                        clean = guard.close()
                    except (OSError, ValueError, RuntimeError):
                        clean = False
                    if not clean:
                        _retained_guard = guard
                        result["reading"] = None
                        result["diagnostics"].append("instance guard cleanup incomplete; reading discarded")
                        code = 1
    return result, code


def main(argv=None, *, device=None, runner=None):
    """Parse either the executable command or a fixed-device source wrapper."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    if device is None:
        parser.add_argument("--diagnose-device", choices=_DEVICES, required=True)
    parser.add_argument("--acknowledge-experimental", action="store_true", required=True,
                        help="accept unverified freshness, receiver behavior and recovery")
    parser.add_argument("--transport", choices=("receiver", "wired"),
                        help="required for Endgame; test exactly one matching endpoint")
    parser.add_argument("--diagnostic-output", metavar="NEW_JSON_FILE",
                        help="create a new JSON report; required in the frozen executable")
    args = parser.parse_args(argv)
    selected = device if device is not None else args.diagnose_device
    if (selected not in _DEVICES
            or (selected == "endgame-4k" and args.transport is None)
            or (selected != "endgame-4k" and args.transport is not None)):
        parser.error("targeted diagnostics use their receiver endpoint only" if selected in _TARGETED_DEVICES
                     else "Endgame requires --transport; Tachi uses its receiver endpoint only")
    if sys.platform != "win32":
        parser.error("the local tester requires the Windows native transport")
    if not args.diagnostic_output and (getattr(sys, "frozen", False) or sys.stdout is None):
        parser.error("--diagnostic-output is required without a console or in the frozen executable")
    output = None
    if args.diagnostic_output:
        try:
            # Reserve the explicitly requested new report before loading HID.
            # Never overwrite an existing file, create directories or use app data.
            output = open(args.diagnostic_output, "x", encoding="utf-8")
        except (OSError, ValueError):
            parser.error("cannot create the requested new diagnostic report")
    code = 1
    try:
        if runner is not None:
            if selected == "endgame-4k":
                result, code = runner(args.transport, acknowledged=True)
            else:
                result, code = runner(acknowledged=True)
        else:
            result, code = run(selected, transport=args.transport, acknowledged=True)
        text = json.dumps(result, ensure_ascii=True)
        if output is not None:
            output.write(text + "\n")
            output.flush()
        if sys.stdout is not None:
            print(text)
    except (OSError, ValueError):
        code = 1
    finally:
        if output is not None:
            try:
                output.close()
            except OSError:
                code = 1
    return code


def dispatch(argv):
    """Return None for normal startup; diagnostic intent must never fall through."""
    # Reserve these diagnostic-only families, including misspelled suffixes or
    # abbreviations. The strict parser rejects them before normal startup.
    families = ("--diagnos", "--acknowledge")
    switches = {"--transport", "--help", "-h"}
    if any(argument.partition("=")[0] in switches or argument.startswith(families)
           for argument in argv):
        return main(argv)
    return None
