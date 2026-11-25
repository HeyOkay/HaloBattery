"""Model names of Pulsar's XS-1 generation mice, by the id the mouse itself reports.

Pulsar's 8K Dongle Gen.2 (3710:5406) is shared by the whole generation, so its product
id does not say which mouse is paired with it. The mouse answers command 0x10
(ReadCIDMID, see providers/pulsar.py) with a customer id and a model id; Pulsar's own
web driver maps them to a name with https://bbb.pulsar.gg/cMouse/devicename.json
(customer id 87, entry = model id - 1). This table is that file, with the colourways
and editions of one shape folded into the shape's name. Model ids it only calls
"Pulsar device" are left out, so those mice keep the name of their product id.

Confirmed on hardware for model id 26 (X2N CrazyLight Mini), through 3710:5406 and on
its cable 3710:3510; the other names are taken from the table as published.

Three entries carry no size in the file: 102 "X3 Crazylight Desert", 116 "Xlite
Crazylight Rock" and 117 "X2H Crazylight Volcano". They are listed as Mini here because
the same colourway also has an entry that says Medium (103, 118, 119).
"""
from __future__ import annotations

from typing import Dict, Optional

PULSAR_CID = 87

_MODELS = (
    ("Pulsar X2 CrazyLight Mini",
     (1, 2, 3, 4, 5, 6, 9, 10, 12, 13, 14,
      15, 16, 43, 44, 82, 85, 86, 95, 96, 130, 131, 132)),
    ("Pulsar X2 CrazyLight Medium", (27, 70, 79, 80, 81, 83, 84, 87, 88, 89, 90, 109)),
    ("Pulsar X2 CrazyLight 5th Anniversary Edition", (41,)),
    ("Pulsar X2 CrazyLight WildScape Desert Edition", (42,)),
    ("Pulsar X2 CrazyLight 2 Mini", (139, 146)),
    ("Pulsar X2 CrazyLight 2 Medium", (140, 147)),
    ("Pulsar X2H CrazyLight Mini", (23, 24, 97, 117)),
    ("Pulsar X2H CrazyLight Medium", (29, 76, 98, 119)),
    ("Pulsar X2N CrazyLight Mini", (25, 26, 105)),
    ("Pulsar X2N CrazyLight Medium", (77, 78, 106)),
    ("Pulsar X2N CrazyLight 2 Mini", (137,)),
    ("Pulsar X2N CrazyLight 2 Medium", (138, 150, 151)),
    ("Pulsar X2F CrazyLight", (126,)),
    ("Pulsar X3 CrazyLight Mini", (19, 20, 102)),
    ("Pulsar X3 CrazyLight Medium", (72, 73, 103)),
    ("Pulsar X3 LHD CrazyLight Mini", (21, 22)),
    ("Pulsar X3 LHD CrazyLight Medium", (74, 75, 104)),
    ("Pulsar Xlite CrazyLight Mini", (17, 18, 116)),
    ("Pulsar Xlite CrazyLight Medium", (34, 71, 118)),
    ("Pulsar X2 Mini By You XS-1", (28,)),
    ("Pulsar X2H Medium By You XS-1", (30,)),
    ("Pulsar X2A Medium By You XS-1", (31,)),
    ("Pulsar X2A Mini By You XS-1", (32,)),
    ("Pulsar Xlite Mini By You XS-1", (33,)),
    ("Pulsar Xlite Large By You XS-1", (35,)),
    ("Pulsar Lab. X2F", (36, 67)),
    ("Pulsar eS FS-1", (91, 127, 128, 129)),
    ("Pulsar Feinmann F01", (101,)),
    ("Pulsar TenZ", (7, 8, 37, 40)),
    ("Pulsar TenZ 2.0", (107, 123)),
    ("Pulsar TenZ Signature Edition 2.0", (110, 122)),
    ("Pulsar ZywOo The Chosen Mouse Gen.2", (68, 69, 100)),
    ("Pulsar ZywOo The Chosen Mouse Gen.3", (133, 134, 135, 136)),
    ("Pulsar JV-X", (38,)),
    ("Pulsar Susanto-X", (39,)),
    ("Pulsar Susanto-X 2.0", (112, 120, 124, 125)),
    ("Pulsar BUZZ-X", (92,)),
    ("Pulsar STA-X", (93,)),
    ("Pulsar CARPE-X", (94,)),
    ("Pulsar Jinggg-X", (99,)),
    ("Pulsar X VSPO!",
     (11, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54,
      55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66)),
)

NAMES: Dict[int, str] = {mid: name for name, mids in _MODELS for mid in mids}


def model_name(cid: int, mid: int) -> Optional[str]:
    """The mouse's name, or None for another customer id or an id the table lacks."""
    if cid != PULSAR_CID:
        return None
    return NAMES.get(mid)
