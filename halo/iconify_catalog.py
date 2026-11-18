"""Verified Iconify picker data, selected assets, and offline attribution.

The registry binds every selectable name to canonical icon data, not merely an
SPDX label. Unknown collections and modified upstream bodies fail closed. See
tools/update_iconify_registry.py for the reproducible, pinned audit inputs.
"""
from __future__ import annotations

from array import array
from bisect import bisect_right
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.iconify.design"
MAX_RESPONSE = 2 * 1024 * 1024
MAX_REGISTRY = 24 * 1024 * 1024
CACHE_LIMIT = 48
CACHE_TTL = 24 * 3600
ASSET_CACHE_LIMIT = 256
ASSET_CACHE_BYTES = 64 * 1024 * 1024
RAM_CACHE_LIMIT = 128
RAM_CACHE_BYTES = 64 * 1024 * 1024
RAW_CACHE_BYTES = 8 * 1024 * 1024
NEGATIVE_CACHE_LIMIT = 512
ASSET_REVISION = "iconify-source-v1"
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CatalogError(ValueError):
    """The requested icon or remote response could not be verified."""
    def __init__(self, message, code="api_error"):
        super().__init__(message)
        self.code = code


class CatalogCancelled(CatalogError):
    pass


@dataclass(frozen=True)
class IconifyAsset:
    id: str
    icon_id: str
    glyph: object
    metadata: dict


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _cancel(cancel):
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise CatalogCancelled("Icon request cancelled", "invalid_request")


def _icon_parts(icon_id):
    if not isinstance(icon_id, str) or len(icon_id) > 258 or icon_id.count(":") != 1:
        raise CatalogError("Invalid icon name", "invalid_request")
    prefix, name = icon_id.split(":")
    if not _NAME.fullmatch(prefix) or not _NAME.fullmatch(name):
        raise CatalogError("Invalid icon name", "invalid_request")
    return prefix, name


def canonical_icon(data, name):
    """Resolve aliases using Iconify's XOR flips/additive quarter-turn rules.

    Algorithm reference: iconify/utils src/icon/{merge,transformations}.ts.
    Canonical fields include the body, dimensions, offsets and transformations.
    """
    if not isinstance(data, dict) or not isinstance(data.get("icons"), dict):
        raise CatalogError("Malformed icon data")
    icons, aliases = data["icons"], data.get("aliases", {})
    if not isinstance(aliases, dict) or len(icons) + len(aliases) > 60000:
        raise CatalogError("Malformed aliases")
    seen = set()

    def resolve(key):
        if key in seen or len(seen) >= 32:
            raise CatalogError("Cyclic or excessive icon aliases")
        seen.add(key)
        if key in icons:
            result = icons[key]
            if not isinstance(result, dict):
                raise CatalogError("Malformed icon")
            return dict(result)
        child = aliases.get(key)
        if not isinstance(child, dict) or not isinstance(child.get("parent"), str):
            raise CatalogError("Missing icon")
        parent = resolve(child["parent"])
        result = {**parent, **child}
        for prop in ("hFlip", "vFlip"):
            for entry in (parent, child):
                if prop in entry and type(entry[prop]) is not bool:
                    raise CatalogError("Invalid flip")
            result[prop] = parent.get(prop, False) != child.get(prop, False)
        for entry in (parent, child):
            if "rotate" in entry and type(entry["rotate"]) is not int:
                raise CatalogError("Invalid rotation")
        result["rotate"] = (parent.get("rotate", 0) + child.get("rotate", 0)) % 4
        return result

    raw = resolve(name)
    body = raw.get("body")
    if not isinstance(body, str) or not body or len(body.encode("utf-8")) > 256 * 1024:
        raise CatalogError("Invalid icon body")
    result = {"body": body}
    for prop, default in (("width", 16), ("height", 16), ("left", 0), ("top", 0)):
        value = raw.get(prop, data.get(prop, default))
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 65536:
            raise CatalogError("Invalid icon dimensions")
        if prop in ("width", "height") and value <= 0:
            raise CatalogError("Invalid icon dimensions")
        result[prop] = int(value) if value == int(value) else value
    for prop in ("hFlip", "vFlip"):
        value = raw.get(prop, False)
        if type(value) is not bool:
            raise CatalogError("Invalid flip")
        result[prop] = value
    rotate = raw.get("rotate", 0)
    if type(rotate) is not int:
        raise CatalogError("Invalid rotation")
    result["rotate"] = rotate % 4
    return result


def icon_svg(icon):
    """Translate resolved coordinates into a zero-origin, transformed SVG."""
    width, height = icon["width"], icon["height"]
    body = icon["body"]
    left, top = icon["left"], icon["top"]
    if left or top:
        body = f'<g transform="translate({-left} {-top})">{body}</g>'
    if icon["hFlip"]:
        body = f'<g transform="translate({width} 0) scale(-1 1)">{body}</g>'
    if icon["vFlip"]:
        body = f'<g transform="translate(0 {height}) scale(1 -1)">{body}</g>'
    rotation = icon["rotate"]
    if rotation == 1:
        body = f'<g transform="translate({height} 0) rotate(90)">{body}</g>'
    elif rotation == 2:
        body = f'<g transform="translate({width} {height}) rotate(180)">{body}</g>'
    elif rotation == 3:
        body = f'<g transform="translate(0 {width}) rotate(270)">{body}</g>'
    if rotation % 2:
        width, height = height, width
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}">{body}</svg>')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CatalogError("Icon API redirects are not accepted")


def _official_fetch(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "api.iconify.design"
            or parsed.username or parsed.password or parsed.fragment):
        raise CatalogError("Untrusted icon API URL")
    request = urllib.request.Request(url, headers={"User-Agent": "HaloBattery/IconPicker",
                                                  "Accept": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=8) as response:
        content_type = response.headers.get_content_type()
        if content_type != "application/json":
            raise CatalogError("Unexpected icon API content type")
        size = response.headers.get("Content-Length")
        if size and (not size.isdigit() or int(size) > MAX_RESPONSE):
            raise CatalogError("Oversized icon API response")
        payload = response.read(MAX_RESPONSE + 1)
    if len(payload) > MAX_RESPONSE:
        raise CatalogError("Oversized icon API response")
    return payload


def _atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".iconify-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_json(path, maximum=MAX_RESPONSE):
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise CatalogError("Oversized cache file")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise CatalogError("Malformed cache file")
    return result


class IconifyCatalog:
    """Network work belongs on a worker; load selected glyphs into tray RAM once.

    ``fetch(url)`` is injectable and returns bytes or a JSON dictionary. It is
    never called by load() or attributions(). ``glyph_factory(svg)`` is also
    injectable, so catalog tests do not need Windows or a native renderer.
    """
    def __init__(self, data_dir, *, fetch=None, glyph_factory=None, registry=None,
                 clock=time.time, registry_dir=None, preview_factory=None, transient=False):
        self.root = Path(data_dir) / "iconify"
        self._http_transport = None
        if fetch is None and transient:
            from .iconify_http import IconifyHTTP
            self._http_transport = IconifyHTTP(fallback=_official_fetch, max_connections=4)
        self._fetch = fetch or self._http_transport or _official_fetch
        self._glyph_factory = glyph_factory
        self._preview_factory = preview_factory
        self.transient = bool(transient)
        self._search_lock = threading.RLock()
        self._name_blob = None
        self._name_ranges = ()
        self._name_starts = ()
        self._searches = OrderedDict()
        self._http_slots = threading.BoundedSemaphore(4)
        self._info_lock = threading.Lock()
        self._preview_glyphs = OrderedDict()
        self._clock = clock
        self._retry_after = 0.0
        self._lock = threading.RLock()
        self._source_disk_lock = threading.Lock()
        self._assets = OrderedDict()
        self._pinned = set()
        try:
            pins = _read_json(self.root / "pins.json")
            if isinstance(pins.get("ids"), list) and len(pins["ids"]) <= 20000:
                self._pinned = {key for key in pins["ids"]
                                if isinstance(key, str) and _DIGEST.fullmatch(key)}
        except (OSError, ValueError, UnicodeError, RecursionError):
            pass
        self._source_failures = OrderedDict()
        self._raw_icons = OrderedDict()
        self._raw_bytes = 0
        self._infos = {}
        self._indices = OrderedDict()
        self._index_names = 0
        self._registry_dir = Path(registry_dir) if registry_dir is not None else Path(__file__).with_name("iconify_data")
        if registry is None:
            path = self._registry_dir / "registry.json.gz"
            with gzip.open(path, "rb") as stream:
                payload = stream.read(MAX_REGISTRY + 1)
            if len(payload) > MAX_REGISTRY:
                raise CatalogError("Oversized registry")
            registry = json.loads(payload)
        if not isinstance(registry, dict) or registry.get("schema") != 1:
            raise CatalogError("Unsupported icon registry")
        self.registry = registry
        self._registry_key = _hash(registry)
        self._collections = registry.get("collections")
        if not isinstance(self._collections, dict) or not self._collections:
            raise CatalogError("Missing verified collections")
        for prefix, entry in self._collections.items():
            if not isinstance(prefix, str) or not _NAME.fullmatch(prefix) or not isinstance(entry, dict):
                raise CatalogError("Malformed verified collection")
            if (not isinstance(entry.get("author"), dict)
                    or not isinstance(entry.get("license"), dict)
                    or not isinstance(entry.get("license_text"), str)
                    or not entry["license_text"].strip()):
                raise CatalogError("Missing verified license evidence")
            if "icons" in entry:
                self._validate_index(entry["icons"])
            elif (entry.get("icons_manifest") != prefix + ".json.gz"
                    or not isinstance(entry.get("icons_sha256"), str)
                    or not _DIGEST.fullmatch(entry["icons_sha256"])
                    or type(entry.get("icons_count")) is not int
                    or not 0 < entry["icons_count"] <= 60000):
                raise CatalogError("Missing verified icon manifest")

    @staticmethod
    def _validate_index(index):
        if not isinstance(index, dict) or len(index) > 60000:
            raise CatalogError("Oversized verified collection")
        for name, digest in index.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise CatalogError("Malformed verified icon fingerprint")

    def _icon_index(self, prefix, entry):
        if "icons" in entry:
            return entry["icons"]
        with self._lock:
            if prefix in self._indices:
                self._indices.move_to_end(prefix)
                return self._indices[prefix]
            path = self._registry_dir / "manifests" / entry["icons_manifest"]
            try:
                with path.open("rb") as stream:
                    raw = stream.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != entry["icons_sha256"]:
                    raise CatalogError("Verified icon manifest failed integrity check", "license_unverified")
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    payload = stream.read(MAX_REGISTRY + 1)
                if len(payload) > MAX_REGISTRY:
                    raise CatalogError("Oversized verified icon manifest")
                index = json.loads(payload)
                self._validate_index(index)
                if len(index) != entry["icons_count"]:
                    raise CatalogError("Verified icon manifest count changed")
            except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                if isinstance(exc, CatalogError):
                    raise
                raise CatalogError("Verified icon manifest unavailable", "license_unverified") from exc
            self._indices[prefix] = index
            self._index_names += len(index)
            while len(self._indices) > 8 or self._index_names > 100000 and len(self._indices) > 1:
                _, old = self._indices.popitem(last=False)
                self._index_names -= len(old)
            return index

    def collections(self):
        return [{"id": prefix, "name": entry["name"],
                 "author": entry["author"]["name"],
                 "license": entry["license"]["title"],
                 "total": entry.get("icons_count", len(entry.get("icons", {})))}
                for prefix, entry in self._collections.items()]

    def _request(self, route, params, cancel):
        _cancel(cancel)
        if self._clock() < self._retry_after:
            raise CatalogError("Icon API is temporarily rate limited", "rate_limit")
        url = API + route + "?" + urllib.parse.urlencode(params)
        try:
            while not self._http_slots.acquire(timeout=0.05):
                _cancel(cancel)
            try:
                _cancel(cancel)
                if self._clock() < self._retry_after:
                    raise CatalogError("Icon API is temporarily rate limited", "rate_limit")
                value = self._fetch(url)
            finally:
                self._http_slots.release()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raw = exc.headers.get("Retry-After", "30") if exc.headers else "30"
                try:
                    delay = min(3600, max(1, int(raw)))
                except (TypeError, ValueError):
                    delay = 30
                with self._lock:
                    self._retry_after = max(self._retry_after, self._clock() + delay)
            exc.close()
            code = ("rate_limit" if exc.code == 429 else
                    "license_unverified" if exc.code == 404 and route.endswith(".json") else "api_error")
            raise CatalogError("Icon source not found" if exc.code == 404 and route.endswith(".json")
                               else "Icon API request failed", code) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise CatalogError("Icon API unavailable", "offline") from exc
        _cancel(cancel)
        if isinstance(value, dict):
            if len(_json_bytes(value)) > MAX_RESPONSE:
                raise CatalogError("Oversized icon API response")
            return value
        if not isinstance(value, (bytes, str)) or len(value) > MAX_RESPONSE:
            raise CatalogError("Malformed icon API response")
        try:
            result = json.loads(value)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise CatalogError("Malformed icon API response") from exc
        if not isinstance(result, dict):
            raise CatalogError("Malformed icon API response")
        return result

    def _entry(self, icon_id):
        prefix, name = _icon_parts(icon_id)
        entry = self._collections.get(prefix)
        if entry is None:
            raise CatalogError("Icon has no verified commercial-use evidence", "license_unverified")
        index = self._icon_index(prefix, entry)
        if name not in index:
            raise CatalogError("Icon has no verified commercial-use evidence", "license_unverified")
        return prefix, name, dict(entry, icons=index)

    @staticmethod
    def _metadata_matches(entry, info):
        if not isinstance(info, dict):
            return False
        author, license_info = info.get("author"), info.get("license")
        expected_license = entry.get("api_license", entry["license"])
        return (info.get("name") == entry["name"] and author == entry["author"]
                and isinstance(license_info, dict)
                and license_info.get("spdx") == expected_license.get("spdx")
                and license_info.get("title") == expected_license.get("title")
                and license_info.get("url") == expected_license.get("url"))

    def _item(self, icon_id, *, verified=False):
        if verified:
            prefix, name = _icon_parts(icon_id)
            entry = self._collections[prefix]
        else:
            prefix, name, entry = self._entry(icon_id)
        return {"id": icon_id, "icon_id": icon_id, "name": name,
                "collection": entry["name"], "collection_id": prefix,
                "author": entry["author"]["name"],
                "license": entry["license"]["title"],
                "license_spdx": entry["license"].get("spdx"),
                "license_url": entry["license"].get("url", ""),
                "source_url": f"https://icon-sets.iconify.design/{prefix}/{name}/"}

    def warm_name_index(self, cancel=None):
        """Build the compact, verified name-only index once, without SVG/network.

        Public Iconify search returns a bounded prefix, not pageable results:
        its `start` must be smaller than `limit`. Matching our pinned names
        locally gives stable complete pages even when that server cap changes.
        """
        with self._search_lock:
            if self._name_blob is not None:
                _cancel(cancel)
                return
            chunks, ranges, offset = [], [], 0
            for prefix in sorted(self._collections):
                _cancel(cancel)
                names = self._icon_index(prefix, self._collections[prefix])
                blob = b"\n" + b"\n".join(name.encode("ascii") for name in sorted(names)) + b"\n"
                chunks.append(blob)
                ranges.append((prefix, offset, offset + len(blob)))
                offset += len(blob)
            _cancel(cancel)
            self._name_blob = b"".join(chunks)
            self._name_ranges = tuple(ranges)
            self._name_starts = tuple(value[1] for value in ranges)

    def _name_at(self, offset):
        prefix = self._name_ranges[bisect_right(self._name_starts, offset) - 1][0]
        name = self._name_blob[offset:self._name_blob.find(b"\n", offset)].decode("ascii")
        return prefix + ":" + name

    def search(self, query, start=0, limit=50, collection=None, cancel=None):
        """Page all licensed name matches; totals count candidates, not SVGs.

        Only per-page preview/selection work downloads SVG data and verifies its
        full fingerprint and live license metadata. Unsupported SVGs may fail
        that later step; a name match alone never authorizes rendering.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > 160:
            raise CatalogError("Enter an icon search of 1 to 160 characters", "invalid_request")
        if type(start) is not int or not 0 <= start <= 2 ** 31 - 1:
            raise CatalogError("Invalid search page", "invalid_request")
        if type(limit) is not int or not 1 <= limit <= 64:
            raise CatalogError("Invalid search limit", "invalid_request")
        if collection is not None and (not isinstance(collection, str) or collection not in self._collections):
            raise CatalogError("Unknown verified collection", "invalid_request")
        query = " ".join(query.strip().lower().split())
        query_prefix = None
        if ":" in query:
            query_prefix, query = query.split(":", 1)
        tokens = tuple(token.encode("ascii") for token in re.findall(r"[a-z0-9]+", query))
        if not tokens:
            raise CatalogError("Enter an icon name", "invalid_request")
        key = (tokens, collection, query_prefix)
        _cancel(cancel)
        self.warm_name_index(cancel)
        with self._search_lock:
            offsets = self._searches.get(key)
            cached = offsets is not None
            if offsets is None:
                buckets = {}
                anchor = max(tokens, key=len)
                exact = b"-".join(tokens)
                blob = self._name_blob
                for prefix, begin, end in self._name_ranges:
                    _cancel(cancel)
                    if (collection is not None and prefix != collection or
                            query_prefix is not None and prefix != query_prefix):
                        continue
                    cursor = begin
                    scanned = 0
                    while True:
                        hit = blob.find(anchor, cursor, end)
                        if hit < 0:
                            break
                        left = blob.rfind(b"\n", begin, hit) + 1
                        right = blob.find(b"\n", hit, end)
                        cursor = right + 1
                        scanned += 1
                        if scanned % 256 == 0:
                            _cancel(cancel)
                        name = blob[left:right]
                        if not all(token in name for token in tokens):
                            continue
                        words = name.split(b"-")
                        rank = (0 if name == exact else
                                1 if all(token in words for token in tokens) else
                                2 if all(any(word.startswith(token) for word in words) for token in tokens) else 3)
                        buckets.setdefault((rank, len(name)), array("I")).append(left)
                offsets = array("I")
                for bucket in sorted(buckets):
                    offsets.extend(buckets[bucket])
                _cancel(cancel)
                self._searches[key] = offsets
                while (len(self._searches) > 8 or len(self._searches) > 1 and
                       sum(len(value) * value.itemsize for value in self._searches.values()) > 8 * 1024 * 1024):
                    self._searches.popitem(last=False)
            else:
                self._searches.move_to_end(key)
            total = len(offsets)
            page = [self._name_at(value) for value in offsets[start:start + limit]]
            next_ids = [self._name_at(value) for value in offsets[start + limit:start + 2 * limit]]
        _cancel(cancel)
        more = start + limit < total
        return {"items": [self._item(icon_id, verified=True) for icon_id in page],
                "next_start": start + limit if more else None,
                "has_more": more, "cached": cached, "offline": False,
                "total": total, "known_count": total, "total_exact": True,
                "page": start // limit + 1, "page_size": limit,
                "page_count": (total + limit - 1) // limit,
                "search_mode": "local", "next_ids": next_ids}

    def _save_browse(self, path, value):
        if self.transient:
            return
        try:
            with self._lock:
                _atomic(path, _json_bytes(value))
                files = sorted(path.parent.glob("*.json"), key=lambda p: p.stat().st_mtime,
                               reverse=True)
                for old in files[CACHE_LIMIT:]:
                    old.unlink(missing_ok=True)
        except OSError:
            pass  # Read-only profiles can still browse and use already saved glyphs.

    def _remember_source_failure(self, icon_id, failure):
        # Immutable source/license/sanitizer refusals are valid for this window.
        # Network errors and 429 are retryable and must never poison an icon.
        if failure.code not in ("license_unverified", "unsupported_svg"):
            return
        with self._lock:
            self._source_failures[icon_id] = (failure.code, str(failure))
            self._source_failures.move_to_end(icon_id)
            while len(self._source_failures) > NEGATIVE_CACHE_LIMIT:
                self._source_failures.popitem(last=False)

    def _source_failure(self, icon_id):
        with self._lock:
            saved = self._source_failures.get(icon_id)
            if saved is not None:
                self._source_failures.move_to_end(icon_id)
                return CatalogError(saved[1], saved[0])
        return None

    def _check_source_failure(self, icon_id):
        failure = self._source_failure(icon_id)
        if failure is not None:
            raise failure

    def _remember_source(self, icon_id, icon):
        prefix, name, _ = self._entry(icon_id)
        isolated = {"prefix": prefix, "icons": {name: icon}}
        with self._lock:
            old = self._raw_icons.pop(icon_id, None)
            if old is not None:
                self._raw_bytes -= len(_json_bytes(old))
            self._raw_icons[icon_id] = isolated
            self._raw_bytes += len(_json_bytes(isolated))
            while len(self._raw_icons) > 128 or self._raw_bytes > RAW_CACHE_BYTES:
                _, old = self._raw_icons.popitem(last=False)
                self._raw_bytes -= len(_json_bytes(old))

    def _local_source(self, icon_id):
        _, name, entry = self._entry(icon_id)
        digest = entry["icons"][name]
        with self._lock:
            data = self._raw_icons.get(icon_id)
        if data is not None:
            return canonical_icon(data, name)
        try:
            if self.transient:
                raise FileNotFoundError
            value = _read_json(self.root / "sources" / (digest + ".json"))
            if isinstance(value, dict) and _hash(value) == digest:
                self._remember_source(icon_id, value)
                return value
        except (OSError, ValueError, UnicodeError, RecursionError):
            pass
        value = self._cached_source(icon_id)
        if value is not None:
            self._remember_source(icon_id, value)
        return value

    def _persist_sources(self, values):
        # Optional disk writes never hold the source/master RAM lock. A slow
        # portable disk or antivirus cannot stall foreground RAM cache hits.
        if self.transient or not self._source_disk_lock.acquire(blocking=False):
            return
        try:
            for digest, icon in values:
                _atomic(self.root / "sources" / (digest + ".json"), _json_bytes(icon))
            files = sorted((self.root / "sources").glob("*.json"),
                           key=lambda path: path.stat().st_mtime, reverse=True)
            total = 0
            for index, path in enumerate(files):
                total += path.stat().st_size
                if index >= 512 or total > 16 * 1024 * 1024:
                    path.unlink(missing_ok=True)
        except OSError:
            pass  # Read-only portable profiles still browse using RAM.
        finally:
            self._source_disk_lock.release()

    def prefetch(self, icon_ids, cancel=None):
        """Batch sources for selected full assets; retain legacy offline behavior."""
        return self._prefetch_sources(icon_ids, cancel, browse=False)

    def prefetch_sources(self, icon_ids, cancel=None):
        """Batch verified SVG data without decoding any 1024px cache master."""
        return self._prefetch_sources(icon_ids, cancel, browse=True)

    def _prefetch_sources(self, icon_ids, cancel, *, browse):
        if not isinstance(icon_ids, (list, tuple)) or len(icon_ids) > 64:
            raise CatalogError("Invalid icon page", "invalid_request")
        groups, failure = {}, None

        def prefer_failure(exc):
            nonlocal failure
            transient = ("offline", "rate_limit", "api_error")
            if (failure is None or isinstance(exc, CatalogCancelled) or
                    not isinstance(failure, CatalogCancelled) and
                    exc.code in transient and failure.code not in transient):
                failure = exc

        for icon_id in icon_ids:
            _cancel(cancel)
            prefix, name, _ = self._entry(icon_id)
            refusal = self._source_failure(icon_id)
            if refusal is not None:
                prefer_failure(refusal)
                continue
            if browse:
                if self._local_source(icon_id) is not None:
                    continue
            else:
                cached = self.cached(icon_id)
                if cached is not None and not self.needs_upgrade(cached):
                    continue
                if self._local_source(icon_id) is not None:
                    continue
            groups.setdefault(prefix, []).append(name)

        def fetch_group(prefix, names):
            try:
                _cancel(cancel)
                self._collection_info(prefix, cancel)
                data = self._request("/" + prefix + ".json", {"icons": ",".join(names)}, cancel)
                if data.get("prefix") != prefix:
                    raise CatalogError("Unexpected icon collection", "license_unverified")
            except CatalogError as exc:
                for name in names:
                    self._remember_source_failure(prefix + ":" + name, exc)
                raise
            verified, refusal = [], None
            for name in names:
                _cancel(cancel)
                icon_id = prefix + ":" + name
                try:
                    icon = canonical_icon(data, name)
                    digest = self._entry(icon_id)[2]["icons"][name]
                    if _hash(icon) != digest:
                        raise CatalogError("Icon changed since license review; review required", "license_unverified")
                except CatalogCancelled:
                    raise
                except CatalogError as exc:
                    refused = CatalogError(str(exc), "license_unverified")
                    self._remember_source_failure(icon_id, refused)
                    refusal = refusal or refused
                    continue
                self._remember_source(icon_id, icon)
                verified.append((digest, icon))
            return verified, refusal

        saved = []
        try:
            if len(groups) > 1:
                self._collection_infos(tuple(groups), cancel)
            if len(groups) == 1:
                prefix, names = next(iter(groups.items()))
                values, refused = fetch_group(prefix, names)
                saved.extend(values)
                if refused is not None:
                    prefer_failure(refused)
            elif groups:
                with ThreadPoolExecutor(max_workers=min(4, len(groups)),
                                        thread_name_prefix="IconifySource") as workers:
                    pending = [workers.submit(fetch_group, prefix, names)
                               for prefix, names in groups.items()]
                    for future in as_completed(pending):
                        try:
                            values, refused = future.result()
                            saved.extend(values)
                            if refused is not None:
                                prefer_failure(refused)
                        except CatalogError as exc:
                            prefer_failure(exc)
            if failure is not None:
                raise failure
        finally:
            if saved and not (cancel is not None and (cancel() if callable(cancel) else cancel.is_set())):
                self._persist_sources(saved)

    def icon_data(self, icon_id, cancel=None, *, offline=False):
        """Return canonical approved data, without building a selected asset."""
        prefix, name, entry = self._entry(icon_id)
        _cancel(cancel)
        self._check_source_failure(icon_id)
        icon = self._local_source(icon_id)
        if icon is None:
            if offline:
                raise CatalogError("Icon source is not cached", "offline")
            self._prefetch_sources([icon_id], cancel, browse=True)
            icon = self._local_source(icon_id)
        if icon is None or _hash(icon) != entry["icons"][name]:
            raise CatalogError("Icon source cannot be verified", "license_unverified")
        return icon

    def preview_glyph(self, icon_id, size, cancel=None, *, offline=False):
        """Small browse glyph; no full asset, PNG round trip, or asset pruning."""
        if type(size) is not int or not 8 <= size <= 192:
            raise CatalogError("Invalid preview size", "invalid_request")
        _cancel(cancel)
        _, name, entry = self._entry(icon_id)
        self._check_source_failure(icon_id)
        from .iconify_thumbnails import PREVIEW_VERSION, prepare_preview
        raster = next(value for value in (128, 256, 512) if value >= 2 * size)
        from .svg_glyph import RENDERER_VERSION
        key = (entry["icons"][name], icon_id, raster, PREVIEW_VERSION, RENDERER_VERSION)
        with self._lock:
            cached = self._preview_glyphs.get(key)
            if cached is not None:
                self._preview_glyphs.move_to_end(key)
                return cached
        try:
            self._check_source_failure(icon_id)
            source = self.icon_data(icon_id, cancel, offline=offline)
            _cancel(cancel)
            glyph = (self._preview_factory or prepare_preview)(icon_svg(source), raster_size=raster)
        except CatalogError as exc:
            self._remember_source_failure(icon_id, exc)
            if offline:
                asset = self.cached(icon_id)
                if asset is not None:
                    return asset.glyph
            raise
        except (OSError, ValueError) as exc:
            failure = CatalogError("Icon preview is unsupported", "unsupported_svg")
            self._remember_source_failure(icon_id, failure)
            raise failure from exc
        from .custom_icon_profile import with_builtin_profile
        glyph = with_builtin_profile(icon_id, glyph)
        _cancel(cancel)
        with self._lock:
            self._preview_glyphs[key] = glyph
            while (len(self._preview_glyphs) > 128 or len(self._preview_glyphs) > 1 and
                   sum(item.memory_bytes for item in self._preview_glyphs.values()) > 32 * 1024 * 1024):
                self._preview_glyphs.popitem(last=False)
        return glyph

    def _collection_infos(self, prefixes, cancel):
        # A page can contain fifty exact-name matches from fifty collections.
        # One metadata-only response avoids fifty extra network round trips.
        with self._info_lock:
            _cancel(cancel)
            if all(prefix in self._infos and self._clock() - self._infos[prefix][0] <= 300
                   for prefix in prefixes):
                return
            response = self._request("/collections", {}, cancel)
            for prefix, entry in self._collections.items():
                info = response.get(prefix)
                if self._metadata_matches(entry, info):
                    self._infos[prefix] = (self._clock(), info)

    def _collection_info(self, prefix, cancel):
        _cancel(cancel)
        saved = self._infos.get(prefix)
        if saved is None or self._clock() - saved[0] > 300:
            response = self._request("/collections", {"prefix": prefix}, cancel)
            info = response.get(prefix)
            if not self._metadata_matches(self._collections[prefix], info):
                raise CatalogError("Collection license metadata changed; review required", "license_unverified")
            self._infos[prefix] = (self._clock(), info)

    def _asset_id(self, icon_id):
        _, name, entry = self._entry(icon_id)
        from . import svg_glyph
        return _hash([ASSET_REVISION, icon_id, entry["icons"][name],
                      svg_glyph.RENDERER_VERSION])

    def cached(self, icon_id):
        """Read current pixels or a validated older fallback; never use network."""
        try:
            current = self.load(self._asset_id(icon_id))
            if current is not None:
                return current
            _, name, entry = self._entry(icon_id)
            from .svg_glyph import LEGACY_RENDERER_VERSION, PREVIOUS_RENDERER_VERSION
            for version in (PREVIOUS_RENDERER_VERSION, LEGACY_RENDERER_VERSION):
                asset_id = _hash([ASSET_REVISION, icon_id, entry["icons"][name], version])
                asset = self.load(asset_id)
                if asset is not None:
                    return asset
        except CatalogError:
            pass
        return None

    @staticmethod
    def needs_upgrade(asset):
        from .svg_glyph import RENDERER_VERSION
        return (asset.metadata.get("renderer_revision") != RENDERER_VERSION
                and not getattr(asset.glyph, "uses_builtin_profile", False))

    def _cached_source(self, icon_id):
        """Recover verified canonical SVG data for a one-time offline upgrade."""
        _, name, entry = self._entry(icon_id)
        from .svg_glyph import RENDERER_VERSION, PREVIOUS_RENDERER_VERSION, LEGACY_RENDERER_VERSION
        for version in (RENDERER_VERSION, PREVIOUS_RENDERER_VERSION, LEGACY_RENDERER_VERSION):
            asset_id = _hash([ASSET_REVISION, icon_id, entry["icons"][name], version])
            try:
                source = _read_json(self.root / "assets" / asset_id / "source.json")
                if isinstance(source, dict) and _hash(source) == entry["icons"][name]:
                    return source
            except (OSError, ValueError, UnicodeError, RecursionError):
                continue
        return None

    def pin(self, asset_id):
        """Retain an applied selection forever, outside transient cache limits."""
        if self.load(asset_id) is None:
            raise CatalogError("Selected icon is not cached", "cache_error")
        with self._lock:
            if asset_id not in self._pinned:
                if len(self._pinned) >= 20000:
                    raise CatalogError("Selected icon retention limit reached", "cache_error")
                pending = self._pinned | {asset_id}
                try:
                    _atomic(self.root / "pins.json", _json_bytes({"ids": sorted(pending)}))
                except OSError as exc:
                    raise CatalogError("Selected icon retention could not be saved", "cache_error") from exc
                self._pinned = pending
        return asset_id

    def _remember(self, asset):
        self._assets[asset.id] = asset
        self._assets.move_to_end(asset.id)
        # Selection masters have their own application RAM map. The catalog's
        # worker cache stays bounded even after many previous selections.
        while (len(self._assets) > RAM_CACHE_LIMIT or len(self._assets) > 1 and
               sum(getattr(item.glyph, "memory_bytes", 1024 * 1024) for item in self._assets.values()) > RAM_CACHE_BYTES):
            self._assets.popitem(last=False)

    def _prune_assets(self):
        try:
            candidates = []
            root = self.root / "assets"
            for folder in root.iterdir():
                if (not _DIGEST.fullmatch(folder.name) or folder.name in self._pinned
                        or folder.is_symlink() or not folder.is_dir()):
                    continue
                metadata, image = folder / "metadata.json", folder / "glyph.png"
                sizes = [path.stat() for path in (metadata, image, folder / "source.json") if path.is_file()]
                candidates.append((max((info.st_mtime for info in sizes), default=0),
                                   sum(info.st_size for info in sizes), folder))
            candidates.sort(reverse=True)
            total = 0
            for index, (_, size, folder) in enumerate(candidates):
                total += size
                if index < ASSET_CACHE_LIMIT and total <= ASSET_CACHE_BYTES:
                    continue
                for filename in ("metadata.json", "glyph.png", "source.json"):
                    (folder / filename).unlink(missing_ok=True)
                folder.rmdir()
                self._assets.pop(folder.name, None)
        except OSError:
            pass

    def prepare(self, icon_id, cancel=None):
        prefix, name, entry = self._entry(icon_id)
        _cancel(cancel)
        previous = self.cached(icon_id)
        if previous is not None and not self.needs_upgrade(previous):
            return previous
        try:
            return self._prepare_current(icon_id, prefix, name, entry, cancel)
        except CatalogCancelled:
            raise
        except CatalogError as exc:
            self._remember_source_failure(icon_id, exc)
            # Old white masks cannot recover authored RGB without the source.
            # Keep a valid selection visible when offline or an upgrade fails.
            if previous is not None:
                return previous
            raise

    def _prepare_current(self, icon_id, prefix, name, entry, cancel):
        from . import svg_glyph
        renderer_revision = svg_glyph.RENDERER_VERSION
        asset_id = self._asset_id(icon_id)
        self._check_source_failure(icon_id)
        icon = self._local_source(icon_id)
        if icon is None:
            self._collection_info(prefix, cancel)
            data = self._raw_icons.get(icon_id)
            if data is None:
                data = self._request("/" + prefix + ".json", {"icons": name}, cancel)
            if data.get("prefix") != prefix:
                raise CatalogError("Unexpected icon collection")
            icon = canonical_icon(data, name)
        if _hash(icon) != entry["icons"][name]:
            raise CatalogError("Icon changed since license review; review required", "license_unverified")
        _cancel(cancel)
        self._remember_source(icon_id, icon)
        factory = self._glyph_factory or svg_glyph.prepare_glyph
        try:
            glyph = factory(icon_svg(icon))
        except (ValueError, OSError) as exc:
            raise CatalogError("Icon cannot be used by the tray renderer", "unsupported_svg") from exc
        from .custom_icon_profile import with_builtin_profile
        glyph = with_builtin_profile(icon_id, glyph)
        metadata = {"asset_id": asset_id, "icon_id": icon_id,
                    "collection": entry["name"], "collection_id": prefix,
                    "author": dict(entry["author"]), "license": dict(entry["license"]),
                    "license_text": entry["license_text"], "notices": entry.get("notices", []),
                    "provenance": entry.get("provenance", {}),
                    "current_policy": entry.get("current_policy", {}),
                    "source_url": f"https://icon-sets.iconify.design/{prefix}/{name}/",
                    "source_hash": entry["icons"][name], "renderer_revision": renderer_revision,
                    "iconify_revision": self.registry.get("iconify_revision"),
                    "acquired_utc": datetime.fromtimestamp(self._clock(), timezone.utc).isoformat(),
                    "color_mode": getattr(glyph, "color_mode", "monochrome"),
                    "modifications": ({
                        "color": "Original SVG colors preserved; scaled and rasterized. ",
                        "mixed": "Fixed SVG colors preserved; currentColor follows the taskbar theme; scaled and rasterized. "
                    }.get(getattr(glyph, "color_mode", "monochrome"),
                          "Rendered as a theme-aware monochrome tray glyph; scaled and rasterized. ") +
                                      "Iconify may have optimized the original SVG.")}
        png = glyph.png_bytes
        if not isinstance(png, bytes) or len(png) > MAX_RESPONSE:
            raise CatalogError("Invalid rendered glyph")
        metadata["png_sha256"] = hashlib.sha256(png).hexdigest()
        _cancel(cancel)
        asset = IconifyAsset(asset_id, icon_id, glyph, metadata)
        with self._lock:
            self._remember(asset)
            if not self.transient:
                self.persist_selection(asset)
                self._prune_assets()
        return asset

    def persist_selection(self, asset, cancel=None):
        """Save a prepared selection before applying it; previews stay RAM-only."""
        if not isinstance(asset, IconifyAsset) or not _DIGEST.fullmatch(asset.id):
            raise CatalogError("Invalid selected icon", "invalid_request")
        _, name, entry = self._entry(asset.icon_id)
        metadata = asset.metadata
        expected = _hash([ASSET_REVISION, asset.icon_id, entry["icons"][name],
                          metadata.get("renderer_revision")])
        if (asset.id != expected or metadata.get("asset_id") != asset.id or
                metadata.get("icon_id") != asset.icon_id or
                metadata.get("source_hash") != entry["icons"][name] or
                any(metadata.get(key) != entry.get(key) for key in
                    ("author", "license", "license_text")) or
                metadata.get("collection") != entry["name"] or
                metadata.get("notices") != entry.get("notices", []) or
                metadata.get("provenance") != entry.get("provenance", {})):
            raise CatalogError("Selected icon evidence changed", "license_unverified")
        png = asset.glyph.png_bytes
        if (not isinstance(png, bytes) or len(png) > MAX_RESPONSE or
                hashlib.sha256(png).hexdigest() != metadata.get("png_sha256")):
            raise CatalogError("Invalid selected icon pixels", "cache_error")
        _cancel(cancel)
        source = self._local_source(asset.icon_id)
        if source is None:
            from .svg_glyph import RENDERER_VERSION
            if metadata.get("renderer_revision") == RENDERER_VERSION:
                source = self.icon_data(asset.icon_id, cancel)
        if source is not None and _hash(source) != entry["icons"][name]:
            raise CatalogError("Selected icon source changed", "license_unverified")
        _cancel(cancel)
        with self._lock:
            folder = self.root / "assets" / asset.id
            try:
                if source is not None:
                    _atomic(folder / "source.json", _json_bytes(source))
                _atomic(folder / "glyph.png", png)
                _atomic(folder / "metadata.json", _json_bytes(metadata))
            except OSError as exc:
                raise CatalogError("Selected icon could not be saved", "cache_error") from exc
        return asset.id

    def load(self, asset_id):
        """Load a selected asset with local integrity checks; never use network."""
        if not isinstance(asset_id, str) or not _DIGEST.fullmatch(asset_id):
            return None
        with self._lock:
            if asset_id in self._assets:
                self._assets.move_to_end(asset_id)
                return self._assets[asset_id]
            try:
                folder = self.root / "assets" / asset_id
                metadata = _read_json(folder / "metadata.json")
                icon_id = metadata.get("icon_id")
                _, name, entry = self._entry(icon_id)
                if (metadata.get("asset_id") != asset_id
                        or metadata.get("source_hash") != entry["icons"][name]
                        or metadata.get("license_text") != entry["license_text"]
                        or metadata.get("author") != entry["author"]
                        or metadata.get("license") != entry["license"]
                        or metadata.get("notices") != entry.get("notices", [])
                        or metadata.get("provenance") != entry.get("provenance", {})
                        or metadata.get("collection") != entry["name"]):
                    return None
                expected = _hash([ASSET_REVISION, icon_id, metadata["source_hash"],
                                  metadata.get("renderer_revision")])
                if expected != asset_id:
                    return None
                with (folder / "glyph.png").open("rb") as stream:
                    png = stream.read(MAX_RESPONSE + 1)
                if len(png) > MAX_RESPONSE or hashlib.sha256(png).hexdigest() != metadata.get("png_sha256"):
                    return None
                from . import svg_glyph
                glyph = svg_glyph.CustomGlyph.from_png(png, renderer_version=metadata.get("renderer_revision"))
                from .custom_icon_profile import with_builtin_profile
                glyph = with_builtin_profile(icon_id, glyph)
                if metadata.get("color_mode", glyph.color_mode) != glyph.color_mode:
                    return None
                asset = IconifyAsset(asset_id, icon_id, glyph, metadata)
                self._remember(asset)
                return asset
            except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                return None

    def _remove_cache_folder(self, folder):
        # Resolve and check every recursive-delete target. A redirected assets
        # parent must never turn cache cleanup into deletion outside this root.
        root = self.root.resolve()
        resolved = folder.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            return
        try:
            if folder.is_symlink():
                folder.unlink(missing_ok=True)
            elif folder.is_dir():
                shutil.rmtree(folder)
        except OSError:
            pass

    def purge_unselected_assets(self, keep_ids):
        """One-time migration before opening any browsing workers/windows."""
        if not isinstance(keep_ids, (list, tuple, set, frozenset)) or len(keep_ids) > 20000:
            raise CatalogError("Invalid selected icon list", "invalid_request")
        keep = {value for value in keep_ids if isinstance(value, str) and _DIGEST.fullmatch(value)}
        try:
            for folder in (self.root / "assets").iterdir():
                if _DIGEST.fullmatch(folder.name) and folder.name not in keep:
                    self._remove_cache_folder(folder)
        except OSError:
            pass

    def clear_browse_cache(self, keep_ids):
        """Release a stopped window's RAM and legacy browse/source disk caches.

        Transient details never create asset folders. Applied assets and shared
        pins are untouched, including selections made by a newer window.
        """
        if not isinstance(keep_ids, (list, tuple, set, frozenset)) or len(keep_ids) > 20000:
            raise CatalogError("Invalid selected icon list", "invalid_request")
        keep = {value for value in keep_ids if isinstance(value, str) and _DIGEST.fullmatch(value)}
        if self._http_transport is not None:
            self._http_transport.close()
        with self._search_lock, self._lock:
            self._searches.clear()
            self._name_blob = None
            self._name_ranges = self._name_starts = ()
            self._raw_icons.clear()
            self._source_failures.clear()
            self._raw_bytes = 0
            self._preview_glyphs.clear()
            self._infos.clear()
            self._indices.clear()
            self._index_names = 0
            self._assets = OrderedDict((key, value) for key, value in self._assets.items() if key in keep)
            self._pinned = keep
            for name in ("browse", "sources", "preview-pages"):
                self._remove_cache_folder(self.root / name)

    def attributions(self, asset_ids):
        if not isinstance(asset_ids, (list, tuple, set)) or len(asset_ids) > 1000:
            return []
        result, seen = [], set()
        for asset_id in asset_ids:
            if isinstance(asset_id, str) and asset_id not in seen:
                seen.add(asset_id)
                asset = self.load(asset_id)
                if asset is not None:
                    result.append(dict(asset.metadata))
        return result
