"""Generate pinned Lucide radar for tracker; center source ink once."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("tracker", "radar",
                         "6092285ad2f49aecbd4521a31ac0ad3a17fa75a2406f5d98b8695df0205e0c13",
                         center_visible=True))
