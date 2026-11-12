"""Generate the pinned Lucide heart for Health; center its measured ink once."""
from make_microphone_artwork import main

if __name__ == "__main__":
    raise SystemExit(main("health", "heart",
                         "5822440c5dd334ead1855bc676cdccda748cf61a914083a94339cecd4c88a400",
                         center_visible=True))
