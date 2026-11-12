"""Generate pinned Lucide tv-minimal for display; center source ink once."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("display", "tv-minimal",
                         "fb1260e517492839289f2607871a055b9eac9b82f765a88f897d3f27db1a2141",
                         center_visible=True, logical_scale=.48))
