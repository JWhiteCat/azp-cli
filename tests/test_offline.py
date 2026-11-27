"""Offline regression tests for vision.py, nav.py, and coop.py using UID-masked real AzurPromilia frames."""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from azp import coop, nav, vision

SNAPS = os.path.join(ROOT, "snaps")


def _load(name: str) -> Image.Image:
    p = os.path.join(SNAPS, name)
    assert os.path.isfile(p), f"Missing fixture: {p}"
    return Image.open(p)


def _assert_uid_masked(im: Image.Image, name: str) -> None:
    arr = np.asarray(im.convert("RGB"))
    h, w = arr.shape[:2]
    x1 = int(round(83.0 / 1440.0 * w))
    y1 = int(round(781.0 / 810.0 * h))
    x2 = int(round(266.0 / 1440.0 * w))
    box = arr[y1:h, x1:x2]
    assert float(box.std()) < 10.0, f"Unmasked UID detected in {name}: std={box.std():.2f}"


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
    assert heading is not None and (320 <= heading <= 360 or 0 <= heading <= 20), heading
    assert len(pois) >= 1 and 250 <= pois[0]["bearing_deg"] <= 295, pois

    # 6. Home Ranch, confirm dialog & night-time dark grass non-loading check
    im_home = _load("home_ranch.png")
    im_unlock = _load("home_ranch_unlock.png")
    im_confirm = _load("home_exit_confirm.png")
    im_night = _load("home_night_grass.png")

    assert coop.is_home_ranch(im_home) is True
    assert coop.is_home_ranch(im_unlock) is True
    assert coop.is_confirm_dialog(im_confirm) is True
    assert vision.check_hud_state(im_confirm)["confirm_dialog"] is True
    assert coop.is_confirm_dialog(im_poi) is False
    st_night = vision.check_hud_state(im_night)
    assert st_night["loading"] is False and st_night["in_world_hud"] is False, st_night
    assert coop.is_home_ranch(im_night) is True
    assert coop.is_home_ranch(im_poi) is False
    assert coop.is_home_ranch(_load("xinya_arrived.png")) is False

    # 7. Ensure all committed fixtures have masked UID boxes & mask_uid_image works in memory
    for fname in (
        "lake_puz2.png",
        "xinya_arrived.png",
        "back_world.png",
        "kibo_puz3.png",
        "home_ranch.png",
        "home_ranch_unlock.png",
        "home_exit_confirm.png",
        "home_night_grass.png",
    ):
        _assert_uid_masked(_load(fname), fname)

    dummy = Image.new("RGB", (1920, 1080), (240, 240, 240))
    res_mask = vision.mask_uid_image(dummy)
    assert res_mask["uid_box"] == [107, 1037, 363, 1080], res_mask

    print("ALL_OFFLINE_TESTS_PASSED", {"heading": heading, "poi0": pois[0], "f_ratio": ratio})


if __name__ == "__main__":
    run_tests()
