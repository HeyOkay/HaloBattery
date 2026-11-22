"""Generate the pinned Lucide smartphone masks using the shared pipeline."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("phone", "smartphone",
                         "04e49a72309fe04fc063b6ddb5a0c4f7bb4843d8ae640df2af603162688c8776",
                         center_visible=True))
