"""Generate pinned Lucide speaker masks with the shared offline Chromium pipeline."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("speaker", "speaker",
                         "a7169d896cf1f1dcadebd67a8a3c9d650c84e27b9395fa85e63462154b2052c3",
                         (3.5, 2.5, 12.5, 13.5)))
