"""Bounded private Iconify IPC and real subprocess lifecycle, offline."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from halo.iconify_process import IconifyProcessJobs, decode, encode
from halo.iconify_worker import WorkerServer

ROOT = Path(__file__).resolve().parents[1]


def request(number, action="search", key="mouse"):
    return dict(type="catalog", protocol=1, id=number, action=action, deviceKey=key,
                query="rocket", start=0, size=16,
                iconId="lucide:rocket" if action in ("apply", "details") else None)


class Controller:
    def __init__(self, root):
        self.context = {"data_dir": root}
        self.applied = threading.Event()
        self.revision = 0
        self.commits = []

    def custom_icon_ids(self):
        return []

    def begin_custom_selection(self, key):
        self.revision += 1
        return self.revision

    def custom_icon_bindings(self, icon_id):
        return {"mouse": ("a" * 64, self.revision)}

    def catalog_render_state(self, key):
        return (42, False, True, 20, False, "mouse", False, "", False, 16, "default", False, False), 16

    def apply_custom_icon(self, key, asset, *, revision):
        self.commits.append((key, asset, revision))
        self.applied.set()
        return revision == self.revision


class IconifyProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_wire_rejects_unterminated_oversized_nonobject_and_nonfinite_messages(self):
        from halo.iconify_process import MAX_WIRE
        for line in ('{"kind":"warm"}', '[]\n', '{"kind":1}\n', '{"kind":"x","x":NaN}\n',
                     '{"kind":"' + 'x' * MAX_WIRE + '"}\n'):
            with self.subTest(prefix=line[:32]), self.assertRaises(ValueError):
                decode(line)
        self.assertEqual(decode(encode(dict(kind="warm"))), {"kind": "warm"})

    def test_source_and_early_app_dispatch_exit_without_creating_a_profile(self):
        wire = encode(dict(kind="init", protocol=1, dataDir=self.temp.name)) + encode(dict(kind="close"))
        for command in ([sys.executable, "-m", "halo.iconify_worker"],
                        [sys.executable, str(ROOT / "halo_battery.pyw"), "--iconify-worker"]):
            with self.subTest(command=command):
                result = subprocess.run(command, cwd=ROOT, input=wire, capture_output=True,
                    encoding="utf-8", timeout=10, env={**os.environ, "APPDATA": self.temp.name},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([decode(line + "\n") for line in result.stdout.splitlines()], [{"kind": "closed"}])
                self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_admitted_restore_survives_view_close_and_real_child_is_reaped(self):
        controller = Controller(self.temp.name)
        visible = threading.Event()
        visible.set()
        changed = threading.Event()
        replies = []
        broker = IconifyProcessJobs(controller, replies.append, changed.set, visible.is_set)
        broker.submit(request(1, "restore"))
        visible.clear()
        broker.close()
        try:
            self.assertTrue(controller.applied.wait(8), "Closing cancelled the admitted restore")
            self.assertTrue(broker._cleanup_done.wait(8), "Worker was not reaped")
            self.assertEqual(controller.commits, [("mouse", None, 1)])
            self.assertTrue(changed.is_set())
            self.assertIsNone(broker._process)
            self.assertEqual(replies, [])
        finally:
            broker.close()
            broker._cleanup_done.wait(8)

    def test_worker_keeps_render_snapshots_for_each_queued_device(self):
        server = WorkerServer(io.StringIO(), io.StringIO())
        server.handle(dict(kind="init", protocol=1, dataDir=self.temp.name))
        entered, release, complete = threading.Event(), threading.Event(), threading.Event()
        seen = []

        def execute(message, cancel):
            if message["id"] == 1:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Missing test release")
            seen.append((message["deviceKey"], server.controller.catalog_render_state(message["deviceKey"])))
            if message["id"] == 2:
                complete.set()
            return {"items": []}

        server.jobs.execute = execute
        state = list(Controller(self.temp.name).catalog_render_state("mouse")[0])
        try:
            for number, key in ((1, "mouse"), (2, "keyboard")):
                snapshot = list(state)
                snapshot[0] = number * 20
                server.handle(dict(kind="request", request=request(number, "apply", key), state=snapshot,
                    actualSize=16, revision=number, selected=[]))
                if number == 1:
                    self.assertTrue(entered.wait(3))
            release.set()
            self.assertTrue(complete.wait(3))
            self.assertEqual([(key, value[0][0]) for key, value in seen], [("mouse", 20), ("keyboard", 40)])
        finally:
            release.set()
            server._close()
            self.assertTrue(server._finished.wait(3))

    def test_unadmitted_mismatched_and_duplicate_commits_fail_closed(self):
        controller = Controller(self.temp.name)
        broker = IconifyProcessJobs(controller, lambda result: None, lambda: None, lambda: True)
        broker._requests[1] = dict(request(1, "restore"), selectionRevision=1)
        commit = dict(kind="commit", rpc=1, requestId=1, op="apply", assetId=None,
                      iconId=None, deviceKey="mouse", revision=1)
        for change in ({"requestId": 2}, {"deviceKey": "keyboard"}, {"revision": 2}, {"iconId": "lucide:rocket"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                broker._commit(dict(commit, **change))
        controller.revision = 1
        self.assertTrue(broker._commit(commit))
        with self.assertRaises(ValueError):
            broker._commit(commit)
        self.assertEqual(controller.commits, [("mouse", None, 1)])

    def test_result_page_and_prefetch_are_bound_to_the_admitted_search(self):
        controller = Controller(self.temp.name)
        replies = []
        broker = IconifyProcessJobs(controller, replies.append, lambda: None, lambda: True)
        broker._requests[1] = dict(request(1), selectionRevision=None)
        result = dict(request(1), items=[])
        broker._receive(dict(kind="reply", result=result))
        prefetched = dict(result, start=50, prefetch=True, anchorId=1)
        broker._receive(dict(kind="reply", result=prefetched))
        self.assertEqual(replies, [result, prefetched])
        for change in ({"start": 100}, {"anchorId": 2}, {"size": 32}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                broker._receive(dict(kind="reply", result=dict(prefetched, **change)))

    def test_worker_failure_answers_new_requests_and_silently_skips_prewarm(self):
        replies = []
        broker = IconifyProcessJobs(Controller(self.temp.name), replies.append, lambda: None, lambda: True)
        broker._stopped.set()
        broker.prewarm()
        broker.submit(request(1))
        self.assertEqual(replies[0]["errorCode"], "api_error")
        self.assertIsNone(broker._thread)

    def test_close_deadline_reaps_an_unresponsive_real_child(self):
        launched = threading.Event()
        children = []

        def popen(_command, **kwargs):
            child = subprocess.Popen([sys.executable, "-c",
                "import threading; threading.Event().wait(30)"], **kwargs)
            children.append(child)
            launched.set()
            return child

        broker = IconifyProcessJobs(Controller(self.temp.name), lambda result: None,
            lambda: None, lambda: True, popen=popen, close_timeout=.1)
        broker.prewarm()
        self.assertTrue(launched.wait(3))
        broker.close()
        self.assertTrue(broker._cleanup_done.wait(5))
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(all(stream.closed for stream in (children[0].stdin, children[0].stdout, children[0].stderr)))

    def test_full_pipe_queue_does_not_supersede_an_admitted_apply(self):
        replies = []
        controller = Controller(self.temp.name)
        controller.revision = 1
        broker = IconifyProcessJobs(controller, replies.append, lambda: None, lambda: True)
        broker._accepted.add(1)
        broker._requests[1] = dict(request(1, "apply"), selectionRevision=1)
        for _ in range(32):
            broker._outgoing.put_nowait(encode(dict(kind="warm")))
        broker.submit(request(2, "apply"))
        self.assertEqual(controller.revision, 1)
        self.assertEqual(broker._accepted, {1})
        self.assertEqual(replies[0]["errorCode"], "busy")
        self.assertIsNone(broker._thread)

    def test_evicted_optional_adoption_does_not_abort_an_admitted_apply(self):
        controller = Controller(self.temp.name)
        controller.revision = 1
        broker = IconifyProcessJobs(controller, lambda result: None, lambda: None, lambda: True)
        # Drive real admission/history eviction while replacing only process
        # startup and consuming the reserved messages as a pipe writer would.
        broker._start = lambda: None
        broker._requests[0] = dict(request(0, "details"), selectionRevision=None)
        broker._requests[1] = dict(request(1, "apply"), selectionRevision=1)
        broker._accepted.add(1)
        for number in range(2, 40):
            broker.submit(request(number))
            queued = broker._outgoing.get_nowait()
            self.assertTrue(queued.ready.is_set())
        self.assertNotIn(0, broker._requests)
        self.assertIn(1, broker._requests)
        self.assertLessEqual(len(broker._requests), 32)
        loaded = []
        asset = object()

        def load_asset(asset_id, icon_id):
            loaded.append((asset_id, icon_id))
            return asset

        broker._load_asset = load_asset
        broker._receive(dict(kind="commit", rpc=1, requestId=0, op="adopt", assetId="a" * 64,
            iconId="lucide:rocket", deviceKey="mouse", revision=None))
        self.assertEqual(decode(broker._outgoing.get_nowait()), {"kind": "committed", "rpc": 1, "applied": False})
        self.assertEqual(loaded, [])
        self.assertEqual(broker._accepted, {1})
        broker._receive(dict(kind="commit", rpc=2, requestId=1, op="apply", assetId="a" * 64,
            iconId="lucide:rocket", deviceKey="mouse", revision=1))
        self.assertEqual(decode(broker._outgoing.get_nowait()), {"kind": "committed", "rpc": 2, "applied": True})
        self.assertEqual(controller.commits, [("mouse", asset, 1)])
        self.assertEqual(loaded, [("a" * 64, "lucide:rocket")])

    @unittest.skipUnless(os.name == "nt", "Windowed Python standard handles are Windows-only")
    def test_worker_recovers_inherited_pipes_when_python_streams_are_none(self):
        wire = encode(dict(kind="init", protocol=1, dataDir=self.temp.name)) + encode(dict(kind="close"))
        result = subprocess.run([sys.executable, "-c",
            "import sys; sys.stdin=sys.stdout=sys.stderr=None; "
            "from halo.iconify_worker import main; raise SystemExit(main())"],
            cwd=ROOT, input=wire, capture_output=True, encoding="utf-8", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result.stdout), {"kind": "closed"})

    def test_worker_protocol_failure_exits_without_initializing_an_app(self):
        result = subprocess.run([sys.executable, "-m", "halo.iconify_worker"],
            cwd=ROOT, input=encode(dict(kind="unknown")), capture_output=True,
            encoding="utf-8", timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 2)
        self.assertIn("protocol or execution failed", result.stderr)

    def test_worker_uses_utf8_for_existing_legacy_code_page_streams(self):
        self.check_unicode_worker_streams(("cp1251", "cp1252", "ascii"))

    @unittest.skipUnless(os.name == "nt", "Inherited windowed handles are Windows-only")
    def test_windowed_worker_preserves_unicode_on_restored_streams(self):
        self.check_unicode_worker_streams((None,))

    def check_unicode_worker_streams(self, encodings):
        author = "Błażej Élodie 中文 日本語 Қазақша"
        profile = str(Path(self.temp.name) / "Реєстр 中文")
        wire = encode(dict(kind="init", protocol=1, dataDir=profile)) + encode(dict(kind="close"))
        # Emit generated catalogue metadata, not just an echo: decoding and
        # re-encoding UTF-8 bytes in the same legacy code page can mask the bug.
        for encoding in encodings:
            with self.subTest(encoding=encoding):
                setup = ("sys.stdin=sys.stdout=sys.stderr=None" if encoding is None else
                         f"[getattr(sys,n).reconfigure(encoding={encoding!r}, errors='strict') "
                         "for n in ('stdin','stdout','stderr')]")
                script = f"""import sys
from halo import iconify_worker as worker
{setup}
OriginalServer = worker.WorkerServer
class UnicodeServer(OriginalServer):
    def handle(self, message):
        super().handle(message)
        if message['kind'] == 'init':
            assert message['dataDir'] == {json.dumps(profile, ensure_ascii=True)}
            self.send(dict(kind='fixture', profile=message['dataDir'],
                           author={json.dumps(author, ensure_ascii=True)}))
worker.WorkerServer = UnicodeServer
code = worker.main()
sys.stderr.write({json.dumps(author, ensure_ascii=True)})
raise SystemExit(code)
"""
                result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                    input=wire.encode("utf-8"), capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                replies = [decode(line + "\n") for line in result.stdout.decode("utf-8").splitlines()]
                self.assertEqual(replies, [{"kind": "fixture", "profile": profile, "author": author},
                                          {"kind": "closed"}])
                self.assertEqual(result.stderr.decode("utf-8"), author)
