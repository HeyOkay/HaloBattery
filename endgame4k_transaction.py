"""One disposable process owns open/send/read/close; the tray never closes its HID handle."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import atexit
import threading
import shutil


class WorkerDirectory:
    """Explicit cleanup only: no exit finalizer may remove a live child's files."""
    def __init__(self):
        self.parent = Path(tempfile.gettempdir()).resolve()
        self.name = tempfile.mkdtemp(prefix='halobattery-4k-', dir=self.parent)

    def cleanup(self):
        path = Path(self.name).resolve()
        if path.parent != self.parent or not path.name.startswith('halobattery-4k-'):
            raise ValueError('unexpected worker cleanup path')
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass

_children = {}  # process -> temporary directory, retained while a child may live
_lifecycle = threading.Lock()
_stopping = False


def shutdown():
    global _stopping
    with _lifecycle:
        _stopping = True
        children = list(_children.items())
    for child, temporary in children:
        try:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=1)
            with _lifecycle:
                _children.pop(child, None)
            temporary.cleanup()
        except (OSError, subprocess.TimeoutExpired):
            pass


atexit.register(shutdown)


def last_progress(directory):
    """Read only whitelisted breadcrumbs, never arbitrary worker text/payloads."""
    try:
        path = directory / 'progress.json'
        if path.stat().st_size > 256:
            return 'last worker operation unavailable'
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            return 'last worker operation unavailable'
        operation, command = value.get('operation'), value.get('command')
        if operation not in ('create', 'open', 'send', 'read', 'close'):
            return 'last worker operation unavailable'
        if command is not None and (type(command) is not int or command not in (0x0E, 0xB4)):
            return 'last worker operation unavailable'
        detail = '' if command is None else f' command=0x{command:02x}'
        return f'last recorded worker operation={operation}{detail}; not proof of root cause'
    except (OSError, ValueError, UnicodeError):
        return 'last worker operation unavailable'


class Transactions:
    def __init__(self, deadline=8.0, cleanup_wait=1.0):
        self.deadline = deadline
        self.cleanup_wait = cleanup_wait
        self.pending = None
        self.closed = False
        self.active = None
        self.busy = threading.Lock()

    def close(self):
        # Serialize with process creation. A pending child stays owned until reaped.
        with _lifecycle:
            self.closed = True
            active = self.active
        if active is not None:
            try:
                if active.poll() is None:
                    active.kill()
                active.wait(timeout=self.cleanup_wait)
            except (OSError, subprocess.TimeoutExpired):
                pass  # the active run retains ownership and its bounded cleanup
        if not self.busy.acquire(blocking=False):
            return  # the active call has a bounded wait and owns cleanup
        try:
            if self.pending:
                process, temp = self.pending
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=self.cleanup_wait)
                except (OSError, subprocess.TimeoutExpired):
                    return
                with _lifecycle:
                    _children.pop(process, None)
                temp.cleanup()
                self.pending = None
        finally:
            self.busy.release()

    def run(self, info, simulate=None):
        if not self.busy.acquire(blocking=False):
            return {'status': None, 'diagnostics': ['transaction already running; no new request sent']}
        try:
            return self._run(info, simulate)
        finally:
            self.busy.release()

    def _run(self, info, simulate):
        if self.pending:
            process, temp = self.pending
            if process.poll() is None:
                return {'status': None, 'diagnostics': ['prior worker still terminating; no new request sent']}
            process.wait()
            with _lifecycle:
                _children.pop(process, None)
            temp.cleanup()
            self.pending = None
        temp = WorkerDirectory()
        directory = Path(temp.name)
        request = {'path_hex': info['path'].hex(), 'product_id': info['product_id']}
        if simulate is not None:
            request['simulate'] = simulate
        (directory / 'request.json').write_text(json.dumps(request), encoding='utf-8')
        launcher = Path(__file__).with_name('halo_battery.pyw')
        command = [sys.executable]
        if not getattr(sys, 'frozen', False):
            command.append(str(launcher))
        command += ['--endgame-worker', str(directory)]
        process = None
        try:
            with _lifecycle:
                if _stopping or self.closed:
                    return {'status': None, 'diagnostics': ['application shutting down; no request sent']}
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                _children[process] = temp
                self.active = process
            try:
                process.wait(timeout=self.deadline)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=self.cleanup_wait)
                except subprocess.TimeoutExpired:
                    self.pending = (process, temp)
                return {'status': None, 'diagnostics': [
                    f'transaction timeout after {self.deadline:.1f}s; no reading accepted',
                    last_progress(directory),
                    'worker termination pending; next request blocked' if self.pending else
                    'worker terminated and reaped; next poll may retry']}
            if process.returncode != 0:
                return {'status': None, 'diagnostics': ['transaction worker exited without a valid result']}
            result_file = directory / 'result.json'
            if result_file.stat().st_size > 16384:
                raise ValueError('oversized worker result')
            result = json.loads(result_file.read_text(encoding='utf-8'))
            if not isinstance(result, dict) or not isinstance(result.get('diagnostics'), list):
                raise ValueError('invalid worker result')
            return result
        except (OSError, ValueError, TypeError):
            # Never discard ownership of a live child, even on an unusual OS error.
            if process is not None and process.poll() is None:
                self.pending = (process, temp)
            return {'status': None, 'diagnostics': ['transaction worker unavailable; no reading accepted']}
        finally:
            with _lifecycle:
                if self.active is process:
                    self.active = None
            if process is not None and process.poll() is not None:
                with _lifecycle:
                    _children.pop(process, None)
            if not self.pending or self.pending[0] is not process:
                temp.cleanup()
