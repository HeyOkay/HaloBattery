"""Print the CHANGELOG.md section for one version, for use as release notes.

Usage: python tools/release_notes.py 1.9.0 [--out release_notes.md] [--changelog CHANGELOG.md]
Without --out the notes are printed.

Exits with an error if the changelog has no "## [1.9.0]" section, so a release
cannot be built without a changelog entry.
"""
import os
import re
import sys


def section(text: str, version: str) -> str:
    head = re.compile(r"^## \[" + re.escape(version) + r"\][^\n]*\n", re.M)
    m = head.search(text)
    if not m:
        raise LookupError(version)
    rest = text[m.end():]
    end = re.search(r"^## \[|^\[[^\]]+\]:", rest, re.M)   # next version or the link list
    return (rest[:end.start()] if end else rest).strip() + "\n"


def main() -> int:
    args = sys.argv[1:]
    opts = {}
    for flag in ("--out", "--changelog"):
        if flag in args:
            i = args.index(flag)
            opts[flag] = args[i + 1]
            del args[i:i + 2]
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    version = args[0].lstrip("v")
    path = opts.get("--changelog") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        notes = section(text, version)
    except LookupError:
        print(f"CHANGELOG.md has no '## [{version}]' section: add one before releasing v{version}",
              file=sys.stderr)
        return 1
    if "--out" in opts:
        with open(opts["--out"], "w", encoding="utf-8", newline="\n") as f:
            f.write(notes)
        print(f"wrote {opts['--out']} ({len(notes.splitlines())} lines)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
