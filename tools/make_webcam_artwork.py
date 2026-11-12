"""Generate pinned Lucide webcam masks with the shared offline Chromium pipeline."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("webcam", "webcam",
                         "5301b9ff75048743f5436594c0c7ce1fada1653620b1c9f6f34fa3c667b09b60",
                         (3.5, 2.5, 12.5, 13.5)))
