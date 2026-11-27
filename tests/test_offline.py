"""Offline regression tests for vision.py and nav.py using real captured AzurPromilia frames."""
from __future__ import annotations

import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from azp import nav, vision

SNAPS = os.path.join(ROOT, "snaps")


def _load(name: str) -> Image.Image:
    p = os.path.join(SNAPS, name)
    assert os.path.isfile(p), f"Missing fixture: {p}"
    return Image.open(p)


def run_tests() -> None:
    # 1. Open-world HUD detection on normal exploration frame
    st_world = vision.check_hud_state(_load("lake_puz2.png"))
    assert st_world["in_world_hud"] is True, st_world
    assert st_world["loading"] is False, st_world
    assert st_world["in_combat"] is False, st_world

    # 2. Loading screen detection on AzurPromilia dark-navy bottom banner
    st_load = vision.check_hud_state(_load("xinya_arrived.png"))
    assert st_load["loading"] is True, st_load
    assert st_load["in_world_hud"] is False, st_load

    # 3. [F] interact pill detection on real mining prompt
    has_f, ratio = vision.detect_interact_prompt(_load("back_world.png"))
    assert has_f is True and ratio > 0.35, (has_f, ratio)

    # 4. Kibo puzzle Q-glow detection
    st_puz = vision.check_hud_state(_load("kibo_puz3.png"))
    assert st_puz["near_kibo_puzzle"] is True, st_puz

    # 5. Minimap player heading & POI cluster detection
    im_poi = _load("lake_puz2.png")
    heading = nav.estimate_player_heading(im_poi)
    pois = nav.detect_minimap_pois(im_poi)
    assert heading is not None and 320 <= heading <= 360 or 0 <= heading <= 20, heading
    assert len(pois) >= 1 and 250 <= pois[0]["bearing_deg"] <= 295, pois

    print("ALL_OFFLINE_TESTS_PASSED", {"heading": heading, "poi0": pois[0], "f_ratio": ratio})


if __name__ == "__main__":
    run_tests()
