"""Render README download totals using the SignPath badge geometry."""

import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


def download_total(repository, token):
    total = 0
    page = 1
    while True:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "HaloBattery-README"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}",
            headers=headers,
        )
        with urlopen(request, timeout=30) as response:
            releases = json.load(response)
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
    label_element = f'<text x="22" y="16">{label}</text>' if label else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width / 1.2:g}" height="24" viewBox="0 0 {width} 28.8" preserveAspectRatio="xMidYMin meet" role="img" aria-labelledby="title">
  <title id="title">{escape(title)} - {total:,}</title>
  <rect x=".5" y=".5" width="{width - 1}" height="23" rx="7" fill="{fill}" fill-opacity=".16" stroke="{border}"/>
  <g transform="translate(-2 -1)" fill="none" stroke="{foreground}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14 6v9m-3-3 3 3 3-3M9 17v3h10v-3"/>
  </g>
  <path d="M{divider} 5v14" stroke="{border}"/>
  <g font-family="Segoe UI, Microsoft YaHei, Arial, sans-serif" fill="{foreground}" font-size="12">
    {label_element}
    <text x="{divider + 6}" y="16" font-weight="700">{value}</text>
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
    for locale, (label, title) in labels.items():
        for theme in ("light", "dark"):
            badge = render_badge(label, title, total, theme == "dark")
            (args.output_dir / f"downloads-{locale}-{theme}.svg").write_text(badge, encoding="utf-8")
    print(f"Rendered download badges for {total:,} release asset downloads")


if __name__ == "__main__":
    main()
