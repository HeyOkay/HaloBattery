"""Render README download totals and verified signing-service attribution."""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


def github_json(repository, path, token):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "HaloBattery-README"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://api.github.com/repos/{repository}/{path}", headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def verified_production_run(repository, token):
    """Only a published, verified production release activates attribution."""
    page = 1
    while True:
        result = github_json(
            repository,
            f"actions/workflows/release.yml/runs?status=success&per_page=100&page={page}",
            token,
        )
        runs = result["workflow_runs"]
        for run in runs:
            trusted_source = run.get("event") == "push" and re.fullmatch(
                r"v[0-9]+\.[0-9]+\.[0-9]+", run["head_branch"] or ""
            )
            if not trusted_source or run["head_repository"]["full_name"] != repository:
                continue
            if run["status"] != "completed" or run["conclusion"] != "success":
                continue
            # This workflow has fewer than 100 jobs. Check the latest attempt,
            # so a successful earlier attempt cannot mask a failed verification.
            jobs = github_json(repository, f'actions/runs/{run["id"]}/jobs?per_page=100', token)
            conclusions = {job["name"]: job["conclusion"] for job in jobs["jobs"]}
            required = ("release_sign_payload", "release_sign_internal", "release_sign_installer", "signing_verify", "publish")
            if all(conclusions.get(name) == "success" for name in required):
                return {
                    "run_id": run["id"], "source_sha": run["head_sha"],
                    "url": run["html_url"], "release_tag": run["head_branch"],
                }
        if len(runs) < 100:
            return None
        page += 1


def render_signing_badges(output_dir, evidence):
    assets = Path(__file__).resolve().parents[1] / "docs/readme"
    for source in assets.glob("signpath*.svg"):
        badge = source.read_text(encoding="utf-8") if evidence else (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        )
        (output_dir / source.name).write_text(badge, encoding="utf-8")


def download_total(repository, token):
    total = 0
    page = 1
    while True:
        releases = github_json(repository, f"releases?per_page=100&page={page}", token)
        for release in releases:
            for asset in release["assets"]:
                total += asset["download_count"]
        if len(releases) < 100:
            return total
        page += 1


def render_badge(label, title, total, dark):
    value = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
    divider = {"Downloads": 91, "Скачивания": 100, "下载量": 64, "": 25}[label]
    width = divider + 46
    border, foreground = ("#75B88C", "#D8F1DE") if dark else ("#75B88C", "#234C30")
    fill = "#A1E6B5"
    label_element = f'<text x="22" y="14.4">{label}</text>' if label else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width / 1.2:g}" height="24" viewBox="0 0 {width} 28.8" preserveAspectRatio="xMidYMin meet" role="img" aria-labelledby="title">
  <title id="title">{escape(title)} - {total:,}</title>
  <rect x=".5" y=".5" width="{width - 1}" height="23" rx="7" fill="{fill}" fill-opacity=".16" stroke="{border}"/>
  <g transform="translate(-2 -1)" fill="none" stroke="{foreground}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14 6v9m-3-3 3 3 3-3M9 17v3h10v-3"/>
  </g>
  <path d="M{divider} 5v14" stroke="{border}"/>
  <g font-family="Segoe UI, Microsoft YaHei, Arial, sans-serif" fill="{foreground}" font-size="12">
    {label_element}
    <text x="{divider + 6}" y="14.4" font-weight="700">{value}</text>
  </g>
</svg>
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository", default="HeyOkay/HaloBattery")
    args = parser.parse_args()
    total = download_total(args.repository, os.environ.get("GH_TOKEN"))
    labels = {
        "en": ("Downloads", "Total release asset downloads"),
        "ru": ("Скачивания", "Всего скачиваний файлов выпусков"),
        "zh": ("下载量", "发布文件的总下载量"),
        "compact": ("", "Total release asset downloads"),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = args.output_dir / "signing-active.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else None
    if not evidence or not evidence.get("release_tag"):
        evidence = verified_production_run(args.repository, os.environ.get("GH_TOKEN"))
    render_signing_badges(args.output_dir, evidence)
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    for locale, (label, title) in labels.items():
        for theme in ("light", "dark"):
            badge = render_badge(label, title, total, theme == "dark")
            (args.output_dir / f"downloads-{locale}-{theme}.svg").write_text(badge, encoding="utf-8")
    print(f"Rendered download badges for {total:,} release asset downloads")


if __name__ == "__main__":
    main()
