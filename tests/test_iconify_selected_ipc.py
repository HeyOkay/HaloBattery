"""Selection deltas retain complete evidence without duplicating ID snapshots."""
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from halo.iconify_process import IconifyProcessJobs, MAX_WIRE, decode
from halo.iconify_worker import WorkerServer, _selected_snapshot


def request(number, action="licenses"):
    return dict(type="catalog", protocol=1, id=number, action=action,
                deviceKey="mouse" if action == "details" else None,
                query="rocket", start=0, size=16,
                iconId="lucide:rocket" if action == "details" else None)


class Controller:
    def __init__(self, root, selected):
        self.context = {"data_dir": root}
        self.selected = selected

    def custom_icon_ids(self):
        return self.selected[:]

    def custom_icon_bindings(self, icon_id):
        return {"mouse": (self.selected[0], 1)} if self.selected else {}

    def catalog_render_state(self, key):
        return (42, False, True, 20, False, "mouse", False, "", False, 16, "default", False, False), 16


class SelectedIPCTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def broker(self, selected):
        controller = Controller(self.temp.name, selected)
        replies = []
        broker = IconifyProcessJobs(controller, replies.append, lambda: None, lambda: True)
        broker._start = lambda: None
        return controller, broker, replies

    def drain(self, broker):
        messages = []
        while not broker._outgoing.empty():
            queued = broker._outgoing.get_nowait()
            self.assertTrue(queued.ready.is_set())
            messages.append(decode(queued.wire))
            broker._selected_queued_bytes -= queued.selected_bytes
        return messages

    def test_twenty_thousand_ids_are_sent_once_across_thirty_two_browse_requests(self):
        selected = [f"{number:064x}" for number in range(20000)]
        _, broker, _ = self.broker(selected)
        for number in range(32):
            broker.submit(request(number))
        queued = list(broker._outgoing.queue)
        self.assertEqual(len(queued), 32)
        self.assertLess(sum(len(item.wire.encode("utf-8")) for item in queued), 2 * 1024 * 1024)
        messages = self.drain(broker)
        self.assertEqual(messages[0]["selected"], {"add": selected, "remove": []})
        self.assertTrue(all(message["selected"] is None for message in messages[1:]))
        snapshot = []
        for message in messages:
            snapshot = _selected_snapshot(snapshot, message["selected"])
        self.assertEqual(snapshot, selected)

    def test_changed_or_cleared_selection_does_not_advance_after_full_queue_rejection(self):
        original = ["a" * 64, "b" * 64]
        for changed in (["b" * 64, "c" * 64], []):
            with self.subTest(changed=changed):
                controller, broker, replies = self.broker(original[:])
                for number in range(32):
                    broker.submit(request(number))
                controller.selected = changed
                broker.submit(request(32))
                self.assertEqual(replies[-1]["errorCode"], "busy")
                self.assertEqual(broker._selected_snapshot, frozenset(original))
                initial = broker._outgoing.get_nowait()
                snapshot = _selected_snapshot([], decode(initial.wire)["selected"])
                broker._selected_queued_bytes -= initial.selected_bytes
                broker.submit(request(33))
                for message in self.drain(broker):
                    snapshot = _selected_snapshot(snapshot, message["selected"])
                self.assertEqual(snapshot, sorted(changed))
                self.assertEqual(broker._selected_snapshot, frozenset(changed))

    def test_large_changing_deltas_have_a_shared_queue_byte_budget(self):
        selected = [f"{number:064x}" for number in range(20000)]
        controller, broker, replies = self.broker(selected)
        broker.submit(request(0))
        controller.selected = [f"{number + 20000:064x}" for number in range(20000)]
        broker.submit(request(1))
        admitted = broker._selected_snapshot
        controller.selected = selected
        broker.submit(request(2))
        self.assertEqual(replies[-1]["errorCode"], "busy")
        self.assertEqual(broker._selected_snapshot, admitted)
        self.assertLessEqual(broker._selected_queued_bytes, MAX_WIRE)

    def test_writer_releases_selection_payload_budget_after_flush(self):
        _, broker, _ = self.broker(["a" * 64])
        broker.submit(request(0))
        self.assertGreater(broker._selected_queued_bytes, 0)
        written = []
        stream = SimpleNamespace(write=written.append, flush=broker._stopped.set)
        broker._write(SimpleNamespace(stdin=stream))
        self.assertEqual(broker._selected_queued_bytes, 0)
        self.assertEqual(decode(written[0])["selected"]["add"], ["a" * 64])

    def test_worker_retains_full_license_ids_and_in_use_binding_outside_seed_cache(self):
        selected = [f"{number:064x}" for number in range(20000)]
        for action in ("licenses", "details"):
            with self.subTest(action=action):
                server = WorkerServer(io.StringIO(), io.StringIO())
                server.handle(dict(kind="init", protocol=1, dataDir=self.temp.name))
                completed = threading.Event()
                seen = []

                def execute(message, cancel):
                    seen.append(server.controller.custom_icon_ids())
                    if action == "details":
                        seen.append(server.controller.custom_icon_in_use("lucide:rocket"))
                    completed.set()
                    return {}

                server.jobs.execute = execute
                state, size = Controller(self.temp.name, selected).catalog_render_state("mouse")
                try:
                    server.handle(dict(kind="request", request=request(0, action),
                        state=list(state) if action == "details" else None,
                        actualSize=size if action == "details" else None, revision=None,
                        selected={"add": selected, "remove": []}, inUse=action == "details"))
                    self.assertTrue(completed.wait(3))
                    self.assertEqual(seen[0], selected)
                    if action == "details":
                        self.assertTrue(seen[1])
                finally:
                    server._close()
                    self.assertTrue(server._finished.wait(3))

    def test_invalid_delta_is_rejected_without_mutating_current_snapshot(self):
        current = ["a" * 64]
        for update in ({"add": ["a" * 64], "remove": []},
                       {"add": [], "remove": ["b" * 64]},
                       {"add": ["b" * 64] * 2, "remove": []},
                       {"add": ["bad"], "remove": []}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                _selected_snapshot(current, update)
            self.assertEqual(current, ["a" * 64])
        self.assertIs(_selected_snapshot(current, None), current)

    def test_cancellation_still_transfers_the_snapshot_before_unchanged_request(self):
        selected = ["a" * 64]
        _, broker, _ = self.broker(selected)
        broker.submit(request(0, "cancel"))
        broker.submit(request(1))
        first, second = self.drain(broker)
        self.assertEqual(first["selected"], {"add": selected, "remove": []})
        self.assertIsNone(second["selected"])
        server = WorkerServer(io.StringIO(), io.StringIO())
        server.handle(dict(kind="init", protocol=1, dataDir=self.temp.name))
        completed = threading.Event()
        seen = []

        def execute(message, cancel):
            seen.append(server.controller.custom_icon_ids())
            completed.set()
            return {}

        server.jobs.execute = execute
        try:
            server.handle(first)
            server.handle(second)
            self.assertTrue(completed.wait(3))
            self.assertEqual(seen, [selected])
        finally:
            server._close()
            self.assertTrue(server._finished.wait(3))
