"""Generate pinned Lucide house for Smart home; center measured source ink once."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("smart_home", "house",
                         "b2a1a51bb3ed4f996d086f6ec9c15d09256c6a70ca7f41f5a00d892c55b03d7a",
                         center_visible=True))
