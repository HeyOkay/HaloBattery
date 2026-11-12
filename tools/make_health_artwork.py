"""Generate the pinned Lucide heart-pulse for Health; center its measured ink once."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("health", "heart-pulse",
                         "b4854916e71088f488cf0c20de6695979e4b617a36cb95f1a3eb1fb2bfb306ed",
                         center_visible=True, optical_offset=(0, .25)))
