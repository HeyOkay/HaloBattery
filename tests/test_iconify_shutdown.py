"""Session cancellation and selection lifetime, with no network or hardware."""
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.parse
import weakref

from halo.iconify_catalog import IconifyCatalog, _hash, canonical_icon
from halo.iconify_host import IconCatalogJobs


def request(action="licenses", **values):
    return dict(type="catalog", protocol=1, id=1, action=action, deviceKey=None,
                query="", start=0, size=16, iconId=None) | values


class Catalog:
    def __init__(self):
        self.interrupted = threading.Event()
        self.cleared = threading.Event()

    def close_transport(self):
        self.interrupted.set()

    def attributions(self, keep):
        return []

    def clear_browse_cache(self, keep):
        self.close_transport()
        self.cleared.set()


class CatalogShutdownTests(unittest.TestCase):
    def jobs(self, catalog, *, owned=True, factory=None, **controller_values):
        controller = SimpleNamespace(icon_catalog=lambda: catalog,
                                     custom_icon_ids=lambda: {"selected"}, **controller_values)
        jobs = IconCatalogJobs(controller, lambda message: None, lambda: None, lambda: True,
                               catalog_factory=(factory or (lambda: catalog)) if owned else None)
        self.addCleanup(jobs.close)
        return jobs

    def test_owned_http_is_interrupted_before_parallel_source_workers_are_joined(self):
        responses = {prefix: dict(prefix=prefix, icons={"one": dict(body='<path d="M0 0h1v1z"/>')})
                     for prefix in ("a", "b")}
        info = dict(name="Fixture", author=dict(name="Fixture"), license=dict(title="MIT"))
        registry = dict(schema=1, collections={prefix: dict(**info, license_text="Fixture MIT notice",
            icons={"one": _hash(canonical_icon(data, "one"))}) for prefix, data in responses.items()})
        entered = {prefix: threading.Event() for prefix in responses}
        interrupted, release = threading.Event(), threading.Event()

        def fetch(url):
            prefix = urllib.parse.urlsplit(url).path[1:].removesuffix(".json")
            entered[prefix].set()
            if not release.wait(3):
                raise OSError("fixture fetch was not released")
            return responses[prefix]

        def stop_http():
            interrupted.set()
            release.set()

        with tempfile.TemporaryDirectory() as folder:
            catalog = IconifyCatalog(folder, registry=registry, fetch=fetch, transient=True)
            catalog._infos.update({prefix: (catalog._clock(), info) for prefix in responses})
            catalog._http_transport = SimpleNamespace(close=stop_http)
            jobs = self.jobs(catalog)

            def execute(message, cancel):
                jobs._catalog().prefetch_sources(["a:one", "b:one"], cancel=cancel)
                return {}

            jobs.execute = execute
            jobs.submit(request())
            try:
                self.assertTrue(all(event.wait(2) for event in entered.values()))
                jobs.close()
                self.assertTrue(interrupted.wait(.5), "HTTP cancellation is blocked behind worker joins")
                self.assertTrue(jobs._cleanup_done.wait(2))
                self.assertFalse(jobs._thread.is_alive())
            finally:
                release.set()
                jobs.close()
                self.assertTrue(jobs._cleanup_done.wait(2))

    def test_shared_catalog_is_not_interrupted_or_cleared_by_view_close(self):
        catalog = Catalog()
        jobs = self.jobs(catalog, owned=False)
        entered, release = threading.Event(), threading.Event()

        def execute(message, cancel):
            jobs._catalog()
            entered.set()
            release.wait(3)
            return {}

        jobs.execute = execute
        jobs.submit(request())
        try:
            self.assertTrue(entered.wait(2))
            jobs.close()
            self.assertFalse(catalog.interrupted.is_set())
        finally:
            release.set()
            jobs.close()
            self.assertTrue(jobs._cleanup_done.wait(2))
        self.assertFalse(catalog.interrupted.is_set())
        self.assertFalse(catalog.cleared.is_set())

    def test_accepted_selection_finishes_before_its_assets_are_discarded(self):
        catalog = Catalog()
        asset = SimpleNamespace(id="selected")
        entered, release, committed = threading.Event(), threading.Event(), threading.Event()

        def persist_selection(selected, *, cancel):
            entered.set()
            if not release.wait(3):
                raise OSError("fixture commit was not released")

        def apply_custom_icon(key, selected, *, revision):
            committed.set()
            return True

        catalog.prepare = lambda *args, **kwargs: asset
        catalog.persist_selection = persist_selection
        jobs = self.jobs(catalog, catalog_render_state=lambda key: (None, 16),
                         begin_custom_selection=lambda key: 1, apply_custom_icon=apply_custom_icon)
        jobs.submit(request("apply", deviceKey="fixture", iconId="a:one"))
        try:
            self.assertTrue(entered.wait(2))
            jobs.close()
            self.assertTrue(catalog.interrupted.wait(.5))
            self.assertFalse(catalog.cleared.is_set())
        finally:
            release.set()
            jobs.close()
            self.assertTrue(jobs._cleanup_done.wait(2))
        self.assertTrue(committed.is_set())
        self.assertTrue(catalog.cleared.is_set())

    def test_catalog_creation_racing_with_close_is_interrupted(self):
        catalog = Catalog()
        initializing, release = threading.Event(), threading.Event()

        def factory():
            initializing.set()
            if not release.wait(3):
                raise OSError("fixture factory was not released")
            return catalog

        jobs = self.jobs(catalog, factory=factory)
        jobs.submit(request())
        try:
            self.assertTrue(initializing.wait(2))
            jobs.close()
            self.assertFalse(catalog.interrupted.is_set())
        finally:
            release.set()
            jobs.close()
            self.assertTrue(jobs._cleanup_done.wait(2))
        self.assertTrue(catalog.interrupted.is_set())
        self.assertTrue(catalog.cleared.is_set())

    def test_unused_catalog_is_not_created_during_close(self):
        created = []
        jobs = self.jobs(Catalog(), factory=lambda: created.append(True))
        jobs.close()
        self.assertTrue(jobs._cleanup_done.wait(2))
        self.assertEqual(created, [])


    def test_closed_window_drops_its_owned_catalog(self):
        refs = []

        def factory():
            catalog = Catalog()
            refs.append(weakref.ref(catalog))
            return catalog

        jobs = self.jobs(None, factory=factory)
        jobs._catalog()
        jobs.close()
        self.assertTrue(jobs._cleanup_done.wait(2))
        self.assertIsNone(refs[0]())


if __name__ == "__main__":
    unittest.main()
