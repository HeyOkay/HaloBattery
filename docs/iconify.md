# Iconify custom tray pictograms

## User behavior

In **Settings > Devices**, expand **Custom icon (Iconify)** below the selected
device's controls. Search, choose a result, inspect its preview and license, then
apply it. Search results use 50-candidate pages, with round previous/next and
number buttons; unsupported SVGs are omitted after safety checks. Matching is
against reviewed icon names, including `prefix:name` queries. **Restore default icon** returns to the device's saved standard
pictogram. Selecting a standard pictogram also clears that device's custom choice.

Only the centre changes. The battery ring, colors, low-level warnings, offline
opacity, known-charge breathing and unknown-charge rotating arc use the existing
tray renderer. **Tray icons > Ring thickness** switches between the current
1-logical-pixel ring and the classic 1.625-logical-pixel ring at 16 px; pictograms
and charging behavior remain the same. The global percentage and ring-only modes keep priority; a custom
choice remains saved while either mode hides the pictogram. Candidate previews
show the actual ring with the proposed symbol, including a preview at the taskbar's
physical icon size and one at 80 physical pixels.

Search is opt-in. Normal device polling, tray animation and startup with current
caches never contact Iconify. Old selected white-only masks receive one cancellable
background source upgrade to recover original colors; the saved fallback remains
visible immediately if offline. Unchanged built-in Lucide profiles need no fetch. A selected PNG and its attribution are stored under `iconify/` in the
same data directory as `config.json`. This follows the existing portable-mode
data directory. Previously applied selections work without the network. Search, source and
preview caches exist only for the settings-window session and are discarded
when it closes. Inspecting an icon does not persist its full-size master;
applying it saves the artwork and complete attribution for offline use.
**About > Custom icon licenses** and the device catalogue expose attribution and
complete license texts, including additional notices and modifications.

## Architecture

- `halo/iconify_catalog.py`: official API, reviewed registry, batched fetching,
  cancellation, bounded caches, offline assets and attribution.
- `halo/svg_glyph.py`: static SVG allowlist, true painted bounds, uniform scaling
  and normalized monochrome, fixed-color or mixed-theme masters. It never draws a battery ring.
- `halo/_svg_windows.py`: Windows Direct2D SVG rasterization on a software WARP
  device, without a browser, UI dispatcher or bundled graphics library.
- `halo/iconify_host.py`: one bounded, cancellable worker per settings session;
  separate catalogue IPC responses, with stale-response suppression in WinUI.
- `SettingsController` binds a verified local asset to a stable device key.
  `App.icon_render_state` includes its immutable ID in the existing art cache.
- `icons.render(..., custom_glyph=...)` composites only the central mask after
  the unchanged ring is rendered. Charging frames use the same optional input.

Network access, SVG parsing and native rasterization happen outside the tray and
settings model locks. A compact verified name index is prepared in the background
when Settings opens; search and exact candidate totals use that local index.
No SVG bodies are bundled. Search matches names rather than remote keyword tags.
Each page contains up to 50 safe previews, and GridView containers are virtualized.
Browse previews rasterize at 128/256/512 pixels; only Details/Apply prepares a
1024-pixel master, and only an applied selection persists it.

Only the visible page's sources are fetched on the foreground lane. A separate
cancellable lane prepares neighboring pages and pushes their completed PNGs to
WinUI for asynchronous bitmap decoding before navigation. Ready pages switch by
reusing decoded images; an in-flight neighbor request is promoted without
cancelling and repeating its network work. A new query does not wait for an old
speculative query. Native tiles use O(1) lookup and keep at most three 50-item
pages around the current page. Ring-state, theme and DPI changes invalidate
those previews without losing the search text or focus.

Final PNG pages are held in a bounded session-only RAM cache (12 pages / 48 MiB),
with no disk reads or writes. Closing the window cancels its workers and clears
search indices, SVG sources, bitmaps and pages; a newly opened window owns a fresh
catalog. Applied glyphs and their licenses use a separate persistent asset store.
Remote SVGs are never loaded directly into XAML. Unit tests use no network or
hardware; live API timings are separate manual checks.

## API constraints

The client uses official collection metadata and batched
`/{prefix}.json?icons=...` requests at `https://api.iconify.design`.
The API has no commercial-use filter. The
[documented search API](https://iconify.design/docs/api/search.html) describes a
maximum of 999, but the public deployment was observed returning at most 128
on 2026-11-14. Its `start` slices an already limited prefix and must be smaller
than `limit`; it cannot retrieve the rest of a large result set. See the
[official 3.2.0 search handler](https://github.com/iconify/api/blob/407792e2bc59eaf5cc683496c688733b5aa14ba3/src/http/responses/search.ts#L52-L95).
The 128 value is an observed deployment limit, not a claim about that source's
published default. Local matching over the authenticated reviewed names avoids
this cap and provides stable page numbers and exact candidate totals. SVG safety
and live source/license checks still run before a symbol can be displayed or used.
New or changed upstream artwork requires registry review.

Queries are debounced in WinUI. HTTPS requests retain response-size limits,
timeouts, cancellation checks and `429` backoff, with at most four concurrent
requests across foreground and background work. A settings session reuses bounded
verified HTTPS connections, supports HTTP CONNECT proxies, and falls back to the
existing urllib transport for complex proxy configurations. Connections close
with the session; redirects remain rejected. One metadata-only response can
verify all collections; SVG downloads remain limited to needed pages. First-time
pages depend on network speed; prepared neighbors and revisited pages reuse RAM.
The service has no assumed availability guarantee or invented fixed quota; see
the [official API documentation](https://iconify.design/docs/api/).

## License policy

The mandatory allowlist covers 207 verified collections with 400,056 approved
icon names and full source hashes. MIT, Apache 2.0, ISC, BSD, CC0 and CC BY grants
are included where their actual artwork scope and notices are confirmed.
Attribution-required licenses remain eligible. All 276 collections in the pinned
Iconify snapshot have an explicit decision in
`halo/iconify_data/audit_report.json`; 69 remain excluded for unverified,
restricted or ambiguous artwork rights, unsupported license obligations. There is no six-collection cap or user bypass switch. There are no independent
brand, logo, Bluetooth, filename or category exclusions. Copyright-license scope
exceptions and unverified per-icon grants remain excluded on that basis alone.
Historical `provenance.excluded_names` fields are retained to keep old cached
proof intact; `current_policy` states the current license-only rule.

`registry.json.gz` stores the reviewed actual license texts and provenance. Each
`manifests/<prefix>.json.gz` contains approved names and full canonical SHA-256
hashes, and is authenticated by the metadata registry. Only used manifests enter
a bounded LRU cache (up to eight collections / 100,000 names). No SVG bodies are
bundled. Runtime packaging includes metadata and hash manifests; duplicate
plaintext legal exports and developer audit inputs remain in source.

API license labels and actual artwork grants can differ. Where reviewed upstream
terms resolve that difference, `api_license` retains the API identity for drift
checks while `license` records the actual artwork license displayed and saved.
Unknown or unsafe SVGs remain excluded. This catalogue policy is not a universal
legal guarantee covering every possible use or trademark right.

The full upstream legal sources are pinned by
`tools/update_iconify_registry.py`; adjacent LICENSE files preserve the actual
texts. In particular, Codicons' icons/content are CC BY 4.0 while repository code
is MIT, and Lucide has embedded Feather-derived exceptions. Simple Icons and
mixed-license Material Design Icons are not blanket-approved from their top-level
labels. Review actual upstream license scope and extra notices before adding a
collection or updating pins. Run the updater deliberately with an evidence
cache; it is never run during startup or tests.

Each asset saves the author, collection, icon source, source hash, acquisition
time, upstream revisions, full license/NOTICE and a modification statement
(theme recoloring only for monochrome icons, preserved fixed colors, scaling and rasterization). A cached asset is accepted
only when its metadata and PNG integrity match the reviewed registry. A future
registry revision that changes its source or legal evidence deliberately fails
closed; retaining previous selections across such an update requires retaining
and auditing their historical evidence as part of that update.

## SVG limits and rendering

A bounded XML static subset permits groups, paths and basic shapes, transforms,
solid fills/strokes, opacity and fill rules. DTDs/entities, scripts/events,
external resources, URLs, `use`, embedded images/fonts, text, styles, gradients,
filters, masks, clipping and animation are rejected before Direct2D sees them.
Byte, depth, node, path-operation and numeric limits also apply. The verified SVG's effective paints select the mode, independently of collection
labels: symbols painted only with `currentColor` follow the taskbar foreground; fixed-color artwork
keeps its RGB; mixed artwork preserves fixed colors while `currentColor` follows
the taskbar. A single explicit color (including black or white), implicit SVG black and black/white contrast artwork
are preserved too. Color pixels are not dimmed by the neutral-ink opacity; the
existing offline device opacity still applies.

New assets retain canonical source data in `source.json`, checked against the
reviewed full source hash before reuse. This enables future renderer upgrades
offline. The v3 cache uses L1024-square monochrome masters, RGBA1024-square fixed
color masters, or paired black/white-currentColor RGBA masters in one 2048×1024 PNG.
The pair shares exact alpha and placement, preserving occlusion and opacity.
Catalog master RAM is capped at 64 MiB plus separately held active selections.

Conservative geometry bounds include strokes and transforms, followed by two
real-alpha raster passes to remove empty viewBox margins. The ink is centered
and uniformly fitted to the established 11 / 16 maximum ink span and 5.9 / 16 radial budget, preserving
clearance from the ring. The 13 already bundled Lucide symbols reuse the exact
existing per-size masks, coverage and reviewed optical placement, so choosing
`lucide:mouse` produces the same pixels as the standard mouse. Other names use
the general real-ink fitting policy. Previously saved v1 masters are validated
against their original evidence and refitted offline without modifying their
stored PNG or losing selections. Physical-size coverage sampling and theme/offline
opacity follow the existing tray conventions. Highly detailed source artwork
may still be less legible at 16 pixels; the actual-size preview lets the user
judge it before applying.

Direct2D SVG requires Windows 10 version 1703 or later; the settings companion
already targets Windows 10 version 2004 or later. PyInstaller includes the small
registry/legal data directory and Python modules. Rasterization uses operating
system DLLs, so it adds no browser, .NET module or Python SVG dependency.
