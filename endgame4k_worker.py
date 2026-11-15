"""Private child entry point. Results are published only after native cleanup returns."""
from dataclasses import asdict
import json
from pathlib import Path
import time
import types


def main(directory):
    directory = Path(directory)
    request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
    from providers import endgame_4k as provider_module
    mode = request.get('simulate')
    if mode is not None:
        allowed = {'success', 'success_v2', 'status08', 'unstable', 'hang_open', 'hang_send', 'hang_read', 'hang_close'}
        if mode not in allowed:
            raise ValueError('unknown simulation')
        def frame(payload=b'', status=1):
            raw = bytearray(63)
            raw[:2] = bytes((0xA1, status))
            raw[16:16+len(payload)] = payload
            return raw
        class Fake:
            def __init__(self):
                self.command = 0
                self.index = 0
            def open_path(self, path):
                if mode == 'hang_open': time.sleep(60)
            def send_feature_report(self, data):
                if mode == 'hang_send': time.sleep(60)
                self.command = data[1]
                return len(data)
            def get_feature_report(self, *_):
                if mode == 'hang_read': time.sleep(60)
                if mode == 'status08': return frame(status=8)
                if self.command == 14:
                    return frame(bytes.fromhex('67 33 82 19 00 00 01 07' if mode == 'success_v2' else '67 33 68 19 00 00 01 10'))
                self.index += 1
                value = (30 if self.index % 2 else 35) if mode == 'unstable' else 45
                return frame(bytes([value]))
            def close(self):
                if mode == 'hang_close': time.sleep(60)
        provider_module.hid = types.SimpleNamespace(device=Fake)
    # Keep a small breadcrumb outside the final result: a blocked native call
    # prevents _read_local's cleanup and result publication from completing.
    factory = provider_module.hid.device

    class TracedDevice:
        def __init__(self):
            self.command = None
            self.mark('create')
            self.device = factory()

        def mark(self, operation):
            try:
                temporary = directory / 'progress.tmp'
                temporary.write_text(json.dumps({
                    'operation': operation, 'command': self.command,
                }), encoding='utf-8')
                temporary.replace(directory / 'progress.json')
            except OSError:
                pass  # optional diagnostics must not change status exchanges

        def open_path(self, path):
            self.mark('open')
            return self.device.open_path(path)

        def send_feature_report(self, data):
            self.command = data[1] if len(data) > 1 and data[1] in (0x0E, 0xB4) else None
            self.mark('send')
            return self.device.send_feature_report(data)

        def get_feature_report(self, *args):
            self.mark('read')
            return self.device.get_feature_report(*args)

        def close(self):
            self.mark('close')
            return self.device.close()

    provider_module.hid = types.SimpleNamespace(device=TracedDevice)
    provider = provider_module.Endgame4kProvider()
    status = provider._read_local({'path': bytes.fromhex(request['path_hex']),
                                   'product_id': request['product_id']})
    result = {'status': asdict(status) if status else None,
              'diagnostics': provider.diagnostics(), 'simulation': mode is not None}
    temporary = directory / 'result.tmp'
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(directory / 'result.json')
