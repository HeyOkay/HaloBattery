"""Experimental XM2w status reader, held from normal release polling.

Status framing: johanneszab/vole re/PROTOCOL.md sections 1-3 at
4ba29dea54b4628df03cfe2f94f4576c124afd80, and
stuffz/mouse-battery-monitor docs/ENDGAME.md at
25871cce3a8266d8d2bc05b0ca81fbb74b3a38b4 (A1/0E/B4, FF01:0002).
Only those status requests are permitted. No automatic recovery/wake command.
Public issue: https://github.com/ahmedkhursheed23/HaloBattery/issues/48 .
"""
import time
import logging
from dataclasses import replace
try:
    import hid
except ImportError:
    hid = None
from . import hidlist
from .base import DeviceStatus, Provider
from .endgame4k_protocol import Reader, ProtocolError, identity, battery

ASLEEP_KEEP = 300
# Keep the upstream release hold until reliability and final review are resolved.
ENABLED_PIDS = frozenset()
UNIT_KEYS = {'1968': 'xm2w4k', '1982': 'xm2w4kv2'}


class Endgame4kProvider(Provider):
    name = 'endgame_4k'

    def __init__(self):
        self._diag = []
        self._last = None
        self._seen = 0
        from endgame4k_transaction import Transactions
        self._transactions = Transactions()

    def close(self):
        self._transactions.close()

    def diagnostics(self):
        return list(self._diag)

    def _read(self, info):
        result = self._transactions.run(info)
        self._diag.extend(result['diagnostics'])
        if result['status'] is None:
            logging.getLogger(__name__).info('Endgame bounded failure: %s', ' | '.join(result['diagnostics']))
            return None
        return DeviceStatus(**result['status'])

    def _read_local(self, info):
        device = None
        reader = None
        started = time.monotonic()
        phase = 'open'
        samples = []
        try:
            device = hid.device()
            device.open_path(info['path'])
            reader = Reader(device)
            phase = 'identity'
            found = identity(reader.exchange(0x0E))
            if found['mouse_pid'] not in UNIT_KEYS:
                self._diag.append('  paired model is not enabled; only original XM2w 4k and v2 identities are permitted')
                return None
            wired = info['product_id'] != 0x1970
            if wired and int(found['mouse_pid'], 16) != info['product_id']:
                raise ProtocolError('wired identity does not match selected device')
            previous = None
            phase = 'battery'
            for _ in range(3):
                sample = battery(reader.exchange(0xB4), wired)
                current = sample['raw_battery_byte']
                samples.append(current)
                if wired or current == previous:
                    break
                previous = current
            else:
                raise ProtocolError('no two consecutive agreeing battery reads')
            # Only one unit is supported: keep its preferences across transports.
            st = DeviceStatus('endgame_4k:' + UNIT_KEYS[found['mouse_pid']], 'Endgame Gear ' + found['model'],
                              None if wired else current, wired, True, self.name,
                              kind='mouse', charging_known=False)
            if wired:
                st.approx = 'charging (level not reported while charging)'
                st.ui_message = 'state.charging_level_unknown'
            self._diag.append(f"  {found['model']} FW bytes={found['firmware_bytes']}; "
                              f"transport={'wired' if wired else 'receiver'}")
            return st
        except (ProtocolError, OSError, ValueError) as exc:
            # Diagnostic-only: never publish report payloads or HID paths.
            event = reader.events[-1] if reader and reader.events else None
            if event:
                replies = event['replies']
                last = bytes.fromhex(replies[-1]) if replies else b''
                status = f'0x{last[1]:02x}' if len(last) > 1 else 'unavailable'
                self._diag.append(
                    f"  command=0x{event['command']} reply_count={len(replies)} "
                    f"last_length={len(last)} status={status} "
                    f"phase={phase} poll_elapsed_ms={(time.monotonic() - started) * 1000:.0f}")
            else:
                self._diag.append('  no recorded exchange; failure before request recording')
            self._diag.append('  ' + (str(exc) if isinstance(exc, ProtocolError) else
                                     type(exc).__name__ + ' during status exchange'))
            # Only already-validated battery bytes, at most three per poll. Never
            # dump the response buffer, paths, serials, or device exception text.
            self._diag.append(f'  phase={phase} validated_battery_samples={samples}')
            logging.getLogger(__name__).info('Endgame recovery failure: %s', ' | '.join(self._diag[-3:]))
            return None
        finally:
            if device is not None:
                try:
                    device.close()
                except OSError:
                    pass

    def poll(self):
        self._diag = ['[Endgame 4K] A1 feature status on ff01:0002; no wake probes']
        if not ENABLED_PIDS:
            self._diag.append('  release hold: corrected reader remains experimental')
            return []
        if hid is None:
            return []
        try:
            raw = hidlist.enumerate(0x3367)
        except OSError:
            self._diag.append('  enumeration failed')
            return []
        infos = {}
        for d in raw:
            if d.get('product_id') in ENABLED_PIDS and (
                    d.get('usage_page'), d.get('usage')) == (0xFF01, 2) and d.get('path'):
                infos[d['path']] = d
        selected = list(infos.values())
        if not selected:
            self._last = None
            return []
        receivers = [d for d in selected if d['product_id'] == 0x1970]
        wired = [d for d in selected if d['product_id'] in (0x1968, 0x1982)]
        if len(receivers) > 1 or len(wired) > 1:
            self._diag.append('  multiple units unsupported; no status requests sent')
            return []
        st = self._read((wired or receivers)[0])
        if st:
            self._last, self._seen = st, time.monotonic()
            return [st]
        if self._last and time.monotonic() - self._seen < ASLEEP_KEEP:
            if self._last.level is not None:
                return [replace(self._last, online=False, charging=False, wake_on_input=True)]
        return []
