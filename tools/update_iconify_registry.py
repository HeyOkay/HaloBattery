"""Reproduce the reviewed Iconify catalog (developer-only; never app startup).

    python tools/update_iconify_registry.py --evidence-dir D:/audit/evidence

Every input is pinned in halo/iconify_data/audit_sources.json after reading the
actual upstream license and its icon-versus-code scope. SPDX tags are discovery
hints, never approval. Full SHA-256 fingerprints are sharded by collection; no
SVG body is included in the installer. Unknown/ambiguous sets stay excluded.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from halo.iconify_catalog import canonical_icon, _hash, _json_bytes

ROOT = Path(__file__).resolve().parents[1]

def download(url, evidence):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = evidence / key
    if path.exists():
        raw = path.read_bytes()
        if not raw:
            raise FileNotFoundError(url)
        return raw
    req = urllib.request.Request(url, headers={"User-Agent": "HaloBattery-registry-audit"})
    with urllib.request.urlopen(req, timeout=45) as response:
        value = response.read(256 * 1024 * 1024 + 1)
    if len(value) > 256 * 1024 * 1024:
        raise ValueError("Oversized audit input: " + url)
    path.write_bytes(value)
    (evidence / (key + ".url")).write_text(url, encoding="utf-8")
    return value


def optional(url, evidence):
    try:
        return download(url, evidence)
    except FileNotFoundError:
        return b""
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return b""


def legal_sections(text):
    """Retain actual README license/credits scope, without unrelated marketing."""
    selected = []
    headings = list(re.finditer(r"(?m)^(#{1,6})\s*(.*)$", text))
    for index, match in enumerate(headings):
        if not re.search(r"licen[cs]e|legal notices|copyright|attribution|credits", match[2], re.I):
            continue
        end = len(text)
        for later in headings[index + 1:]:
            if len(later[1]) <= len(match[1]):
                end = later.start()
                break
        block = text[match.start():end].strip()
        if block and block not in selected:
            selected.append(block)
    return "\n\n".join(selected)


def notice(name, url, raw, *, text=None):
    return {"name": name, "source_url": url, "sha256": hashlib.sha256(raw).hexdigest(),
            "text": raw.decode("utf-8-sig") if text is None else text}


def fingerprints(data, excluded, *, propagate=True):
    aliases = data.get("aliases", {})
    # An alias cannot expand a grant to a parent outside its actual license scope.
    while propagate:
        more = {name for name, value in aliases.items() if value.get("parent") in excluded}
        if more <= excluded:
            break
        excluded.update(more)
    result = {}
    for name in sorted(set(data["icons"]) | set(aliases)):
        if name in excluded:
            continue
        try:
            result[name] = _hash(canonical_icon(data, name))
        except ValueError:
            excluded.add(name)  # Empty/invalid artwork cannot become a tray glyph.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "halo" / "iconify_data")
    parser.add_argument("--audit", type=Path, default=ROOT / "halo" / "iconify_data" / "audit_sources.json")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifests").mkdir(exist_ok=True)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.audit.read_text(encoding="utf-8"))
    historical = {}
    if config.get("historical_registry"):
        history_path = args.audit.parent / config["historical_registry"]
        historical_raw = history_path.read_bytes()
        if hashlib.sha256(historical_raw).hexdigest() != config["historical_registry_sha256"]:
            raise ValueError("Historical proof metadata failed integrity check")
        historical = json.loads(gzip.decompress(historical_raw))["collections"]
    revision = config["iconify_revision"]
    metadata_url = f"https://raw.githubusercontent.com/iconify/icon-sets/{revision}/collections.json"
    metadata = json.loads(download(metadata_url, args.evidence_dir))
    current = json.loads(download("https://api.iconify.design/collections", args.evidence_dir))
    ccby_url = "https://creativecommons.org/licenses/by/4.0/legalcode.txt"
    ccby = download(ccby_url, args.evidence_dir)
    registry = {"schema": 1, "iconify_revision": revision, "reviewed_utc": config["reviewed_utc"], "collections": {}}
    excluded_sets = dict(config["excluded"])
    prefixes = list(config["baseline"]) + sorted(config["sources"])

    def fetch_icon(prefix):
        url = f"https://raw.githubusercontent.com/iconify/icon-sets/{revision}/json/{prefix}.json"
        download(url, args.evidence_dir)
        return prefix, True

    # Download developer audit inputs in bounded parallel, never via public API.
    with ThreadPoolExecutor(max_workers=6) as pool:
        datasets = dict(pool.map(fetch_icon, prefixes))
    for prefix in prefixes:
        print("Verifying", prefix, flush=True)
        info = metadata[prefix]
        datasets.pop(prefix)
        data = json.loads(download(f"https://raw.githubusercontent.com/iconify/icon-sets/{revision}/json/{prefix}.json", args.evidence_dir))
        icon_url = f"https://raw.githubusercontent.com/iconify/icon-sets/{revision}/json/{prefix}.json"
        exception = config.get("license_exceptions", {}).get(prefix, {})
        denied = {name for name in set(data["icons"]) | set(data.get("aliases", {}))
                  if any(name.startswith(value) for value in exception.get("prefixes", []))}
        if prefix in historical or prefix in config["baseline"]:
            # Keep original proof metadata exact for cached selections. Its
            # historical excluded_names is evidence of an older policy only;
            # it is never consulted when computing the current license index.
            entry = copy.deepcopy(historical.get(prefix, config["baseline"].get(prefix)))
            for field in ("icons_manifest", "icons_sha256", "icons_count", "current_policy"):
                entry.pop(field, None)
            hashes = fingerprints(data, denied)
        else:
            spec = config["sources"][prefix]
            if info["license"] != spec["expected_license"]:
                raise ValueError("Audited metadata license identity changed: " + prefix)
            if any(current.get(prefix, {}).get(field) != info[field] for field in ("name", "author", "license")):
                excluded_sets[prefix] = "API legal identity differs from pinned metadata; re-review required."
                continue
            repo, upstream = spec["repo"], spec["revision"]
            base_url = f"https://raw.githubusercontent.com/{repo}/{upstream}/"
            license_url = base_url + spec["license_path"]
            raw = download(license_url, args.evidence_dir)
            if hashlib.sha256(raw).hexdigest() != spec["license_sha256"]:
                raise ValueError("Pinned license evidence hash mismatch: " + prefix)
            actual = raw.decode("utf-8-sig")
            notices = []
            for path in spec["extra_paths"]:
                content = download(base_url + path, args.evidence_dir)
                notices.append(notice(path, base_url + path, content))
            readme_url = base_url + spec.get("readme_path", "README.md")
            readme = optional(readme_url, args.evidence_dir)
            if readme and spec["license_path"] != spec.get("readme_path", "README.md"):
                sections = legal_sections(readme.decode("utf-8-sig"))
                if sections:
                    notices.append(notice("README license and attribution scope", readme_url, readme, text=sections))
            actual_license = spec["actual_license"]
            if actual_license["spdx"] == "CC-BY-4.0" and "Section 8" not in actual:
                notices.append(notice("CC BY 4.0 complete legal code", ccby_url, ccby))
            for proof in spec.get("external_license_proofs", []):
                content = download(proof["source_url"], args.evidence_dir)
                if hashlib.sha256(content).hexdigest() != proof["sha256"]:
                    raise ValueError("Original artwork license proof changed: " + prefix)
                notices.append(notice(proof["name"], proof["source_url"], content))
            if spec.get("per_icon_metadata"):
                source = spec["per_icon_metadata"]
                content = download(base_url + source["path"], args.evidence_dir)
                if hashlib.sha256(content).hexdigest() != source["sha256"]:
                    raise ValueError("Per-icon copyright metadata changed: " + prefix)
                records_data = json.loads(content)
                records = {item["title"]: item for item in (records_data["icons"] if isinstance(records_data, dict) else records_data)}
                selected = {}
                for icon_name, title in source["titles"].items():
                    record = records[title]
                    if record.get("license", {}).get("type") != actual_license["spdx"]:
                        raise ValueError("Per-icon artwork license differs from reviewed grant")
                    selected[icon_name] = {key: record.get(key) for key in ("title", "license", "source")}
                notices.append(notice("Verified per-icon copyright records", base_url + source["path"],
                                      content, text=json.dumps(selected, ensure_ascii=False, indent=2)))
            if spec.get("licensed_names") is not None:
                # A positive per-icon grant list is required only when upstream
                # expressly uses different copyright licenses for its artwork.
                denied.update((set(data["icons"]) | set(data.get("aliases", {}))) - set(spec["licensed_names"]))
            hashes = fingerprints(data, denied)
            entry = {"name": info["name"], "author": info["author"], "license": actual_license,
                     "license_text": actual, "notices": notices,
                     "provenance": {"upstream_repository": "https://github.com/" + repo,
                                    "upstream_revision": upstream, "license_source_url": license_url,
                                    "license_sha256": hashlib.sha256(raw).hexdigest(),
                                    "iconify_source_url": icon_url, "metadata_source_url": metadata_url,
                                    "scope": spec["scope"], "excluded_names": sorted(denied)}}
            if actual_license != info["license"]:
                entry["api_license"] = info["license"]
        entry["current_policy"] = {
            "selection": "verified artwork copyright license and static SVG safety only",
            "historical_provenance": "Any historical excluded_names/scope is retained solely for cached attribution compatibility; it is not a current content filter.",
            "license_scope_exceptions": exception,
        }
        if not hashes or len(hashes) > 60000:
            raise ValueError("Invalid reviewed fingerprint count: " + prefix)
        content = gzip.compress(_json_bytes(hashes), compresslevel=9, mtime=0)
        (args.output / "manifests" / (prefix + ".json.gz")).write_bytes(content)
        entry.update(icons_manifest=prefix + ".json.gz", icons_sha256=hashlib.sha256(content).hexdigest(), icons_count=len(hashes))
        registry["collections"][prefix] = entry
        (args.output / (prefix + "-LICENSE.txt")).write_text(entry["license_text"], encoding="utf-8", newline="\n")
        if entry.get("notices"):
            (args.output / (prefix + "-NOTICES.json")).write_bytes(_json_bytes(entry["notices"]))
    payload = _json_bytes(registry)
    (args.output / "registry.json.gz").write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    report = {prefix: {"status": "verified", "name": entry["name"], "license": entry["license"]["spdx"],
                       "upstream_revision": entry["provenance"]["upstream_revision"],
                       "license_source_url": entry["provenance"]["license_source_url"], "icons": entry["icons_count"]}
              for prefix, entry in registry["collections"].items()}
    report.update({prefix: {"status": "excluded", "reason": reason,
                           "license_metadata": metadata[prefix]["license"],
                           "evidence": config.get("excluded_evidence", {}).get(prefix, {})}
                   for prefix, reason in excluded_sets.items() if prefix not in report})
    missing = set(metadata) - set(report)
    if missing:
        raise ValueError("Missing coverage decisions: " + str(missing))
    (args.output / "audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(entry["icons_count"] for entry in registry["collections"].values())
    compressed = sum(path.stat().st_size for path in (args.output / "manifests").glob("*.json.gz"))
    summary = (f"Reviewed {len(metadata)} official collections; {len(registry['collections'])} verified, "
               f"{len(metadata)-len(registry['collections'])} excluded.\n{total} full SHA-256 fingerprints; "
               f"{compressed} compressed index bytes; {len(payload)} metadata JSON bytes.")
    readme = """Iconify catalog audit inputs (2026-11-14)

Generated by tools/update_iconify_registry.py from immutable reviewed inputs in
 audit_sources.json. audit_report.json records a decision for every official
collection in the pinned Iconify snapshot, including explicit exclusions.

registry.json.gz contains actual upstream legal texts and scope/provenance.
Each manifests/<prefix>.json.gz contains only names and full SHA-256 hashes of
canonical resolved icon data. No SVG artwork is bundled in these files.

License IDs are discovery hints, not approval. Upstream icon-versus-code scope,
additional notices and known exceptions were reviewed. Where API metadata uses
a different code/font license, api_license records it for drift checks, while
license records the actual reviewed artwork grant. No independent brand, logo, Bluetooth, name or category filters apply.
Only actual copyright-license scope exceptions and invalid artwork are excluded.
Mixed-license collections can expose a positive subset of independently
verified per-icon grants; their collection SPDX label alone is not approval.

All previously selectable names retain their exact source hashes and proof
metadata. Historical proof notes are preserved for cached attribution only;
current_policy describes the active license-only selection criteria.

To regenerate, provide the developer evidence cache via --evidence-dir; URLs
and source hashes are pinned. Refreshing pins requires another scope review.
No tests fetch network data. Application downloads use only the official API.
Official API: https://iconify.design/docs/api/
Search: https://iconify.design/docs/api/search.html
Metadata: https://iconify.design/docs/types/iconify-info.html
"""
    (args.output / "README.txt").write_text(readme + "\n" + summary + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
