"""Bounded, cancellable catalogue jobs; network and SVG work stay off tray/UI locks."""
from __future__ import annotations

import base64
from collections import deque
import io
import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field

from . import icons

PAGE_SIZE = 50
MAX_SEARCH_START = 500000
log = logging.getLogger("halo_battery")
ACTIONS = frozenset(("search", "details", "apply", "restore", "licenses", "cancel"))
FIELDS = frozenset(("type", "protocol", "id", "action", "deviceKey", "query", "start", "size", "iconId"))


def validate_request(message):
    if (set(message) != FIELDS or message.get("type") != "catalog" or
            type(message.get("protocol")) is not int or message["protocol"] != 1 or
            type(message.get("id")) is not int or not 0 <= message["id"] <= 2147483647 or
            not isinstance(message.get("action"), str) or message["action"] not in ACTIONS or
            message.get("deviceKey") is not None and (not isinstance(message["deviceKey"], str) or len(message["deviceKey"]) > 512) or
            not isinstance(message.get("query"), str) or len(message["query"]) > 100 or
            type(message.get("start")) is not int or not 0 <= message["start"] <= MAX_SEARCH_START or
            type(message.get("size")) is not int or not 8 <= message["size"] <= 192 or
            message.get("iconId") is not None and (not isinstance(message["iconId"], str) or len(message["iconId"]) > 258)):
        raise ValueError("invalid icon catalogue request")
    if message["action"] == "search" and message["start"] % PAGE_SIZE:
        raise ValueError("catalogue search must start at a page boundary")
    if message["action"] in ("search", "details", "apply", "restore") and not message["deviceKey"]:
        raise ValueError("catalogue request requires a device")
    if message["action"] in ("details", "apply") and not message["iconId"]:
        raise ValueError("catalogue request requires an icon")


def _png(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def attribution(metadata):
    author, license = metadata.get("author", {}), metadata.get("license", {})
    def notices(value):
        if isinstance(value, str):
            return value
        return "\n\n".join("\n".join(str(v.get(k, "")) for k in ("name", "source_url", "text") if v.get(k))
                              if isinstance(v, dict) else str(v) for v in value) if isinstance(value, list) else ""
    return dict(id=metadata.get("icon_id", ""), name=metadata.get("icon_id", "").split(":")[-1],
        collection=metadata.get("collection", ""),
        author=author.get("name", "") if isinstance(author, dict) else str(author),
        license=license.get("title", license.get("spdx", "")) if isinstance(license, dict) else str(license),
        licenseUrl=license.get("url", "") if isinstance(license, dict) else "",
        sourceUrl=metadata.get("source_url", ""), licenseText=metadata.get("license_text", ""),
        notices="\n\n".join(filter(None, (notices(metadata.get("notices", [])), metadata.get("modifications", "")))))


def candidate_preview(state, glyph, size):
    level, charging, online, low, light, _, animate, _, paused, _, brand, color, filled = state[:13]
    # A catalogue candidate is always visible inside the real ring. Applying it
    # does not change the user's global percentage/ring-only preference.
    return icons.render(level, charging, online, low, light, "", force_active=paused,
                        size=size, brand=brand, brand_color=color, filled=filled,
                        ring_style=state[14] if len(state) > 14 else "thin", custom_glyph=glyph)


@dataclass
class _PageFlight:
    ready: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: Exception | None = None


class IconCatalogJobs:
    """One daemon worker, one latest browse request, and at most three pending requests.

    Cancelling a view never undoes an accepted apply. Its acknowledgement may be
    ignored by a closed view, while the normal authoritative state still updates.
    """
    def __init__(self, controller, send, changed, current, *, catalog_factory=None):
        self.controller, self.send, self.changed, self.current = controller, send, changed, current
        self._condition = threading.Condition()
        self._pending = deque()
        self._active = None
        self._closed = False
        self._last_id = -1
        self._catalog_factory = catalog_factory or controller.icon_catalog
        self._owns_catalog = catalog_factory is not None
        self._catalog_instance = None
        self._catalog_lock = threading.Lock()
        self._cleanup_done = threading.Event()
        self._index_cancel = threading.Event()
        self._index_thread = None
        self._pages_lock = threading.RLock()
        self._pages = None
        self._flights = {}
        self._warm_pending = deque()
        self._warming = None
        self._thread = threading.Thread(target=self._run, name="iconify-catalogue", daemon=True)
        self._thread.start()
        self._warm_thread = threading.Thread(target=self._warm_loop, name="iconify-next-page", daemon=True)
        self._warm_thread.start()

    def submit(self, request):
        validate_request(request)
        with self._condition:
            if self._closed:
                return
            if request["id"] <= self._last_id:
                raise ValueError("duplicate or out of order catalogue request")
            self._last_id = request["id"]
            action = request["action"]
            warm_jobs = ([self._warming] if self._warming is not None else []) + list(self._warm_pending)
            if action == "cancel" or action != "licenses" and any(
                    not self._same_browse(request, warm[0]) for warm in warm_jobs):
                if self._warming is not None:
                    self._warming[2].set()
                for warm in self._warm_pending:
                    warm[2].set()
                self._warm_pending.clear()
            if action in ("search", "details", "cancel"):
                if self._active is not None and self._active[0]["action"] in ("search", "details", "licenses"):
                    self._active[1].set()
                self._pending = deque(job for job in self._pending if job[0]["action"] in ("apply", "restore"))
            if action == "cancel":
                return
            if len(self._pending) >= 3:
                self._reply(request, errorCode="busy")
                return
            request = dict(request)
            if action in ("apply", "restore"):
                request["selectionRevision"] = self.controller.begin_custom_selection(request["deviceKey"])
            self._pending.append((request, threading.Event()))
            self._condition.notify_all()

    def _catalog(self):
        from .iconify_catalog import CatalogCancelled
        with self._catalog_lock:
            if self._closed:
                raise CatalogCancelled("Catalogue window closed", "invalid_request")
            if self._catalog_instance is None:
                self._catalog_instance = self._catalog_factory()
            return self._catalog_instance

    def prewarm(self):
        with self._condition:
            if self._closed or self._index_thread is not None:
                return
            def warm():
                try:
                    self._catalog().warm_name_index(cancel=self._index_cancel)
                except (OSError, ValueError):
                    pass  # The first real search can retry and report failure.
            self._index_thread = threading.Thread(target=warm, name="iconify-name-index", daemon=True)
            self._index_thread.start()

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._index_cancel.set()
            if self._active is not None:
                self._active[1].set()
            self._pending.clear()
            if self._warming is not None:
                self._warming[2].set()
            for warm in self._warm_pending:
                warm[2].set()
            self._warm_pending.clear()
            self._condition.notify_all()
        with self._pages_lock:
            if self._pages is not None:
                self._pages.close()
        # A late HTTP response must finish cancellation before temporary caches
        # are discarded. Joining happens on a daemon, never on the native UI.
        threading.Thread(target=self._dispose, name="iconify-session-cleanup", daemon=True).start()

    def _dispose(self):
        try:
            for worker in (self._thread, self._warm_thread, self._index_thread):
                if worker is not None and worker is not threading.current_thread():
                    worker.join()
            if self._owns_catalog and self._catalog_instance is not None:
                self._catalog_instance.clear_browse_cache(self.controller.custom_icon_ids())
            with self._pages_lock:
                self._flights.clear()
            with self._catalog_lock:
                self._catalog_instance = None
        except (OSError, ValueError):
            log.warning("settings: temporary icon cache cleanup failed", exc_info=True)
        finally:
            self._cleanup_done.set()

    def _reply(self, request, **result):
        if self.current():
            self.send(dict(type="catalog", id=request["id"], action=request["action"],
                           deviceKey=request["deviceKey"], query=request["query"], start=request["start"], size=request["size"], **result))

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                request, cancel = self._active = self._pending.popleft()
            try:
                result = self.execute(request, cancel)
                if not cancel.is_set():
                    self._reply(request, **result)
                    if request["action"] == "search":
                        self._schedule_next(request, result, cancel)
            except Exception as error:
                if not cancel.is_set():
                    code = getattr(error, "code", "api_error")
                    if not hasattr(error, "code"):
                        log.exception("settings: icon catalogue job failed")
                    self._reply(request, errorCode=code, offline=code == "offline")
            finally:
                with self._condition:
                    self._active = None

    @staticmethod
    def _same_browse(left, right):
        return all(left.get(key) == right.get(key) for key in ("deviceKey", "query", "size"))

    @staticmethod
    def _same_page_request(left, right):
        return all(left.get(key) == right.get(key) for key in ("deviceKey", "query", "start", "size"))

    @staticmethod
    def _query(request, state):
        return request["query"].strip() or icons.KINDS.get(state[5], "battery").replace("_", " ")

    def _page_key(self, request, state, catalog):
        from .iconify_thumbnails import PREVIEW_VERSION
        from .svg_glyph import RENDERER_VERSION
        return dict(registry=catalog._registry_key, version=PREVIEW_VERSION + ":" + RENDERER_VERSION,
                    device=request["deviceKey"], query=" ".join(self._query(request, state).lower().split()),
                    start=request["start"], size=request["size"], pageSize=PAGE_SIZE,
                    ring=[*state[:5], state[8], state[14] if len(state) > 14 else "thin"])

    def _get_page(self, request, state, cancel):
        from .iconify_catalog import CatalogCancelled, _cancel
        from .iconify_page_cache import PageCache
        catalog = self._catalog()
        key = self._page_key(request, state, catalog)
        identity = json.dumps(key, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(2):
            _cancel(cancel)
            with self._pages_lock:
                if self._pages is None:
                    self._pages = PageCache(catalog.root / "preview-pages")
                cached = self._pages.get(key)
                if cached is not None:
                    return cached
                flight = self._flights.get(identity)
                owner = flight is None
                if owner:
                    flight = self._flights[identity] = _PageFlight()
            if owner:
                try:
                    result = self._build_page(request, state, cancel)
                    _cancel(cancel)
                    self._pages.put_async(key, result)
                    flight.result = result
                except Exception as error:
                    flight.error = error
                finally:
                    with self._pages_lock:
                        self._flights.pop(identity, None)
                        flight.ready.set()
            else:
                # Promote the prefetched page instead of cancelling and repeating
                # its HTTP/SVG work. New queries have their own foreground lane.
                while not flight.ready.wait(.02):
                    _cancel(cancel)
            _cancel(cancel)
            if flight.error is not None:
                if isinstance(flight.error, CatalogCancelled) and attempt == 0:
                    continue
                raise flight.error
            return dict(flight.result)
        raise CatalogCancelled("Page request cancelled", "invalid_request")

    def _build_page(self, request, state, cancel):
        from .iconify_catalog import CatalogError, _cancel
        catalog = self._catalog()
        result = catalog.search(self._query(request, state), start=request["start"], limit=PAGE_SIZE, cancel=cancel)
        offline = bool(result.get("offline"))
        names = [item["id"] for item in result["items"]]
        # Only the visible page belongs on the foreground lane. Neighbor SVG
        # requests and rasterization run independently after this response.
        if not offline:
            try:
                catalog.prefetch_sources(names, cancel=cancel)
            except CatalogError as error:
                _cancel(cancel)
                offline = error.code in ("offline", "rate_limit", "api_error")
        items, skipped, wire_bytes = [], 0, 0
        for item in result["items"]:
            _cancel(cancel)
            try:
                glyph = catalog.preview_glyph(item["id"], request["size"], cancel=cancel, offline=offline)
                row = {key: item.get(key, "") for key in ("id", "name", "collection", "author", "license")}
                row["preview"] = _png(candidate_preview(state, glyph, request["size"]))
                wire_bytes += len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                if wire_bytes > 3 * 1024 * 1024:
                    raise CatalogError("Icon preview page exceeds transport budget", "api_error")
                items.append(row)
            except CatalogError as error:
                _cancel(cancel)
                if error.code not in ("unsupported_svg", "license_unverified", "offline"):
                    raise
                skipped += 1
        more = bool(result.get("has_more"))
        context = self._page_key(request, state, catalog)
        context.pop("start")
        output = dict(items=items, nextStart=request["start"] + PAGE_SIZE if more else 0, hasMore=more,
                      cached=bool(result.get("cached")) or offline, offline=offline, skipped=skipped,
                      pageSize=PAGE_SIZE, contextKey=hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest())
        total = result.get("total")
        if type(total) is int and 0 <= total <= MAX_SEARCH_START:
            output.update(total=total, totalExact=True, pageCount=(total + PAGE_SIZE - 1) // PAGE_SIZE)
        return output

    def _schedule_next(self, request, result, cancel):
        if result.get("offline"):
            return
        try:
            state, _ = self.controller.catalog_render_state(request["deviceKey"])
        except (ValueError, KeyError):
            return
        starts = []
        if result.get("hasMore"):
            starts.append(request["start"] + PAGE_SIZE)
        if request["start"] >= PAGE_SIZE:
            starts.append(request["start"] - PAGE_SIZE)
        with self._condition:
            if self._closed or self._pending or cancel.is_set():
                return
            if self._warming is not None and (not self._same_browse(request, self._warming[0])
                    or self._warming[0]["start"] not in starts):
                self._warming[2].set()
            for previous in self._warm_pending:
                previous[2].set()
            self._warm_pending.clear()
            for start in starts:
                self._warm_pending.append((dict(request, start=start), state, threading.Event()))
            self._condition.notify_all()

    def _warm_loop(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or bool(self._warm_pending))
                if self._closed:
                    return
                job = self._warming = self._warm_pending.popleft()
            request, state, cancel = job
            try:
                result = self._get_page(request, state, cancel)
                if not cancel.is_set() and self.current():
                    # Native decodes these bitmaps while the user is still on
                    # the current page, so a click only swaps ready containers.
                    self._reply(request, prefetch=True, anchorId=request["id"], **result)
            except Exception:
                # Speculative work never publishes UI errors or loading state.
                pass
            finally:
                with self._condition:
                    if self._warming is job:
                        self._warming = None

    def execute(self, request, cancel):
        from .iconify_catalog import CatalogError
        controller, action = self.controller, request["action"]
        key = request["deviceKey"]
        if action == "restore":
            if not self.current() or cancel.is_set():
                return {"applied": False}
            applied = controller.apply_custom_icon(key, None, revision=request.get("selectionRevision"))
            if applied:
                self.changed()
            return {"applied": applied}
        catalog = self._catalog()
        if action == "licenses":
            # Show every icon's source while retaining each collection's long
            # license/NOTICE once; never repeat CC BY's full text per device.
            grouped = {}
            for metadata in catalog.attributions(controller.custom_icon_ids()):
                group_key = (metadata.get("collection_id"), metadata.get("license_text", ""))
                row = attribution(metadata)
                if group_key not in grouped:
                    grouped[group_key] = row
                else:
                    group = grouped[group_key]
                    group["id"] += "\n" + row["id"]
                    group["sourceUrl"] += "\n" + row["sourceUrl"]
            return {"attributions": list(grouped.values())}
        state, actual_size = controller.catalog_render_state(key)
        if action == "search":
            return self._get_page(request, state, cancel)
        asset = catalog.prepare(request["iconId"], cancel=cancel)
        if action == "details" and controller.custom_icon_in_use(asset.icon_id):
            catalog.persist_selection(asset, cancel=cancel)
            if controller.adopt_custom_icon_upgrade(asset) is True:
                self.changed()
        if action == "details":
            detail = attribution(asset.metadata)
            detail.update(preview80=_png(candidate_preview(state, asset.glyph, 80)),
                          previewActual=_png(candidate_preview(state, asset.glyph, actual_size)), actualSize=actual_size)
            return {"detail": detail}
        if action == "apply":
            if not self.current() or cancel.is_set():
                return {"applied": False}
            catalog.persist_selection(asset, cancel=cancel)
            applied = controller.apply_custom_icon(key, asset, revision=request.get("selectionRevision"))
            if applied:
                self.changed()
            return {"applied": applied}
        raise CatalogError("Invalid catalogue action", "invalid_request")
