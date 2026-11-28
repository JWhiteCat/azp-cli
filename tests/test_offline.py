"""Offline regression tests for vision.py, nav.py, and coop.py using UID-masked real AzurPromilia frames."""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from azp import coop, detector, mumu, nav, navmesh, ocr_tpl, vision

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

    # 2. Loading screen detection on AzurPromilia dark-navy night & warm-cream daytime banners
    st_load = vision.check_hud_state(_load("xinya_arrived.png"))
    assert st_load["loading"] is True, st_load
    assert st_load["in_world_hud"] is False, st_load
    day_load = Image.new("RGB", (1920, 1080), (60, 120, 80))
    day_load.paste(Image.new("RGB", (1920, 300), (235, 232, 224)), (0, 780))
    assert vision.is_loading_screen(day_load) is True

    # 2b. Cream/white full-screen modal (e.g. Y 星之旅录 / 山谷秘藏) must NOT falsely trigger in_world_hud or near_kibo_puzzle
    cream_modal = Image.new("RGB", (1920, 1080), (228, 224, 212))
    mdr = ImageDraw.Draw(cream_modal)
    # Paint golden milestone badge over bottom-right Q-slot area to verify Q-glow is gated on real open-world HUD
    mdr.ellipse([1605, 945, 1690, 1030], fill=(245, 220, 145), outline=(60, 50, 40), width=4)
    st_modal = vision.check_hud_state(cream_modal)
    assert st_modal["in_world_hud"] is False and st_modal["near_kibo_puzzle"] is False, st_modal

    # 2c. High-altitude flight HUD detection: Q combat slot hidden over smooth sky, G card slot + [Ctrl] descent pill present
    fly_hud = Image.new("RGB", (1920, 1080), (95, 140, 185))
    fdr = ImageDraw.Draw(fly_hud)
    # Dark surround + bright white HP bar (y=996..1006, x=830..1090)
    fdr.rectangle([820, 970, 1100, 1035], fill=(28, 36, 48))
    fdr.rectangle([830, 996, 1090, 1006], fill=(240, 244, 248))
    # High-contrast G Star-Bond card icon (x=1158..1202, y=972..1025)
    fdr.rectangle([1158, 972, 1202, 1025], fill=(20, 24, 30))
    fdr.rectangle([1166, 980, 1194, 1016], fill=(235, 240, 245))
    # Right-hand [Ctrl] white keycap pill for 降低高度 (x=1812..1864, y=591..619)
    fdr.rectangle([1816, 594, 1860, 616], fill=(242, 245, 248))
    st_fly = vision.check_hud_state(fly_hud)
    assert st_fly["in_world_hud"] is True and st_fly["is_flying"] is True and st_fly["hazard_vignette"] is False, st_fly
    assert st_world["is_flying"] is False and st_world["hazard_vignette"] is False, st_world

    # 2d. Subtitle-proof bounded is_flying: NPC subtitles hide [Ctrl] pill, but "Space"+"Shift" captions stay in [0.055..0.145]/[0.045..0.135].
    # Meanwhile bright sunlit grass/flowers (>0.145 white ratio) behind bottom-right ground buttons must NOT false-trigger is_flying.
    sub_fly = fly_hud.copy()
    sdr = ImageDraw.Draw(sub_fly)
    # Erase [Ctrl] pill as if hidden by NPC subtitle
    sdr.rectangle([1810, 588, 1868, 622], fill=(95, 140, 185))
    # Draw dark bottom-right background + ~7.5% white "Space" text & ~6.2% white "Shift" text
    sdr.rectangle([1715, 1028, 1885, 1056], fill=(32, 42, 52))
    sdr.rectangle([1732, 1038, 1772, 1041], fill=(235, 240, 245))  # 40*3 / (68*24) = 0.0735
    sdr.rectangle([1828, 1038, 1862, 1041], fill=(235, 240, 245))  # 34*3 / (68*24) = 0.0625
    st_sub_fly = vision.check_hud_state(sub_fly)
    assert st_sub_fly["in_world_hud"] is True and st_sub_fly["is_flying"] is True, st_sub_fly

    sunlit_ground = sub_fly.copy()
    gdr = ImageDraw.Draw(sunlit_ground)
    # Flood bottom-right button caption area with bright sunlit grass/white flowers (ratio >> 0.145)
    gdr.rectangle([1718, 1030, 1882, 1054], fill=(235, 240, 238))
    st_sunlit = vision.check_hud_state(sunlit_ground)
    assert st_sunlit["in_world_hud"] is True and st_sunlit["is_flying"] is False, st_sunlit

    # 2e. Environmental DoT hazard detection (HP bar crimson damage chunk while not in combat, e.g. 迹影崖谷 -389 HP/s)
    haz_hud = fly_hud.copy()
    hdr = ImageDraw.Draw(haz_hud)
    hdr.rectangle([920, 996, 1020, 1006], fill=(215, 50, 55))
    st_haz = vision.check_hud_state(haz_hud)
    assert st_haz["in_world_hud"] is True and st_haz["hazard_vignette"] is True, st_haz

    # 2f. [F] prompt classification: locked facilities ("建造"/"修复") vs already-built facilities ("元素祭坛"/"采集小屋"/"星辉矿场"/"伐木场")
    assert ocr_tpl._classify_interact_text("建造") == "facility"
    assert ocr_tpl._classify_interact_text("修复增效设施") == "facility"
    assert ocr_tpl._classify_interact_text("元素祭坛") == "built_facility"
    assert ocr_tpl._classify_interact_text("元素察坛") == "built_facility"
    assert ocr_tpl._classify_interact_text("星辉矿场") == "built_facility"
    assert ocr_tpl._classify_interact_text("缘岩山地采集小屋") == "built_facility"
    assert ocr_tpl._classify_interact_text("伐木场") == "built_facility"
    assert ocr_tpl._classify_interact_text("风铜矿") == "gather"

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

    # 8. MuMu index parsing, input version gate (>= 6.3.2.0), and keycode mapping (matching MAA)
    assert mumu.parse_mumu_index("emulator-5554") == 0
    assert mumu.parse_mumu_index("emulator-5556") == 1
    assert mumu.parse_mumu_index("127.0.0.1:16384") == 0
    assert mumu.parse_mumu_index("127.0.0.1:16416") == 1
    assert mumu.parse_mumu_index("127.0.0.1:16388") == 32  # >= 32 bit-swizzle
    assert mumu.parse_mumu_index("127.0.0.1:7555") == 0
    assert mumu.parse_mumu_index("127.0.0.1:5555") == 0
    assert mumu.is_input_version_supported("6.3.1.9") is False
    assert mumu.is_input_version_supported("6.3.2.0") is True
    assert mumu.is_input_version_supported("6.8.8.0") is True
    assert mumu.is_input_version_supported("6.4") is True
    assert mumu.resolve_linux_keycode("esc") == 1
    assert mumu.resolve_linux_keycode("w") == 17
    assert mumu.resolve_linux_keycode("space") == 57
    assert mumu.resolve_linux_keycode("back") == 158
    assert mumu.resolve_linux_keycode(111) == 1

    # 9. 2.5D world-map costmap baking, A* ground vs fly, online obstacle replanning & 3D portal edges
    syn = Image.new("RGB", (1920, 1080), (42, 86, 42))  # Flat green grass
    dr = ImageDraw.Draw(syn)
    # Paint a large blue lake blocking the direct horizontal line y=400..700, x=750..1150
    dr.rectangle([750, 380, 1150, 720], fill=(70, 155, 165))
    # Paint a beige dirt road curving north around the lake (y=260)
    dr.line([(400, 540), (620, 260), (1280, 260), (1460, 540)], fill=(166, 164, 108), width=26)
    # Paint a red World Boss danger marker south of the lake
    dr.ellipse([900, 790, 990, 880], fill=(225, 55, 50))
    # Draw synthetic yellow --grid overlay lines to verify gridline stripping
    for gx in range(160, 1920, 160):
        dr.line([(gx, 0), (gx, 1080)], fill=(235, 235, 40), width=1)

    cmap = navmesh.CostMap25D.bake_from_image(syn, name="unit_test_map", cell_size=10)
    stats = cmap.terrain_stats()
    assert stats["water_pct"] > 5.0 and stats["road_pct"] > 0.5 and stats["boss_pct"] > 0.3, stats

    # Ground path from west (420, 540) to east (1440, 540) must detour north via the road & avoid the lake
    p_ground = cmap.find_path((420, 540), (1440, 540), mode="ground")
    assert p_ground["ok"] is True and len(p_ground["waypoints"]) >= 3, p_ground
    min_y_ground = min(wp[1] for wp in p_ground["waypoints"])
    assert min_y_ground < 360.0, f"Expected ground path to route north around lake, got {p_ground['waypoints']}"

    # Fly path goes straight across the lake with much lower cost
    p_fly = cmap.find_path((420, 540), (1440, 540), mode="fly")
    assert p_fly["ok"] is True and p_fly["total_cost"] < p_ground["total_cost"], (p_fly, p_ground)

    # Stamp an online obstacle directly on the northern road bridge (950, 260) -> A* must detour around it
    cmap.memory.record_obstacle(950, 260, radius=70, penalty=45.0, reason="fallen_tree")
    p_detour = cmap.find_path((420, 540), (1440, 540), mode="ground")
    assert p_detour["ok"] is True
    # Verify no waypoint lands right inside the blocked spot (950, 260)
    assert all(
        ((wp[0] - 950) ** 2 + (wp[1] - 260) ** 2) ** 0.5 > 35.0 for wp in p_detour["waypoints"]
    ), p_detour["waypoints"]

    # Add a 3D portal shortcut across the lake -> A* should pick the portal and drastically cut cost
    cmap.memory.add_portal(720, 540, 1180, 540, action="mushroom", cost=2.0, label="lake_bounce")
    p_portal = cmap.find_path((420, 540), (1440, 540), mode="ground")
    assert p_portal["ok"] is True
    assert any(seg["action"] == "mushroom" for seg in p_portal["segments"]), p_portal["segments"]
    assert p_portal["total_cost"] < p_detour["total_cost"]

    # Verify micro-leg merging collapses tiny road bends while keeping portal edges intact
    tiny_wps = [[100.0, 100.0], [110.0, 102.0], [122.0, 105.0], [134.0, 108.0], [220.0, 120.0]]
    merged_segs = cmap.waypoints_to_segments(tiny_wps, ["move", "move", "move", "move"])
    assert len(merged_segs) < len(tiny_wps) - 1, merged_segs

    # Round-trip save/load cache + obstacle memory persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        cmap.save(cache_dir=tmpdir)
        loaded = navmesh.CostMap25D.load("unit_test_map", cache_dir=tmpdir)
        assert len(loaded.memory.obstacles) == 1
        assert len(loaded.memory.portals) == 1
        assert loaded.memory.obstacles[0].reason == "fallen_tree"

    # 10. Part 1: Template matching + RapidOCR + Anomaly detector + World-Map POI extraction
    assert ocr_tpl.is_cv2_available() is True
    assert ocr_tpl.is_ocr_available() is True
    tpls = ocr_tpl.list_templates()
    assert "hud_chest" in tpls and "map_star_vein_active" in tpls and "map_padlock" in tpls, tpls

    # Verify confirm_dialog anomaly detection + OCR on UID-masked home_exit_confirm.png
    anom_confirm = ocr_tpl.detect_screen_anomaly(_load("home_exit_confirm.png"), use_ocr=True)
    assert anom_confirm["anomaly"] == "confirm_dialog", anom_confirm
    assert anom_confirm["action"] == "confirm", anom_confirm

    # Verify [F] prompt OCR on UID-masked back_world.png
    f_info = ocr_tpl.read_interact_prompt(_load("back_world.png"))
    assert f_info["has_interact_f"] is True and len(f_info["options"]) >= 1, f_info

    # Verify normal world frame returns anomaly='normal' quickly
    anom_normal = ocr_tpl.detect_screen_anomaly(_load("lake_puz2.png"), use_ocr=True)
    assert anom_normal["anomaly"] == "normal", anom_normal

    # Verify synthetic map POI matching (paste real templates onto synthetic map and verify detection + bottom-edge flag)
    syn_map = Image.new("RGB", (1920, 1080), (42, 86, 42))
    t_sv = Image.open(os.path.join(ocr_tpl.DEFAULT_TEMPLATES_DIR, "map_star_vein_active.png")).convert("RGB")
    t_lock = Image.open(os.path.join(ocr_tpl.DEFAULT_TEMPLATES_DIR, "map_padlock.png")).convert("RGB")
    syn_map.paste(t_sv, (500, 300))
    syn_map.paste(t_sv, (1200, 910))  # near bottom zoom bar -> safe_click must be False
    syn_map.paste(t_lock, (820, 460))
    mpois = ocr_tpl.detect_world_map_pois(syn_map)
    assert mpois["counts"].get("star_vein_active", 0) == 2, mpois
    assert mpois["counts"].get("facility_locked", 0) == 1, mpois
    low_sv = [p for p in mpois["pois"] if p["kind"] == "star_vein_active" and p["y"] > 885]
    assert len(low_sv) == 1 and low_sv[0]["safe_click"] is False and low_sv[0]["needs_drag_dy"] < 0, low_sv

    # 11. Part 2: ONNX YOLO plugin (letterbox, multi-format decoder, NMS, HybridDetector, CV minigames, dataset pipeline)
    assert detector.is_onnxruntime_available() is True
    rgb_dummy = np.zeros((1080, 1920, 3), dtype=np.uint8)
    blob, scale, pad_xy = detector.letterbox_image(rgb_dummy, new_shape=(640, 640))
    assert blob.shape == (1, 3, 640, 640) and blob.dtype == np.float32
    assert abs(scale - (640.0 / 1920.0)) < 1e-4

    # Test standard Ultralytics YOLOv8/YOLO11 [1, 4+C, N] decode + NMS (2 overlapping chest boxes + 1 wumi box)
    n_cls = len(detector.DEFAULT_CLASSES)
    raw = np.zeros((1, 4 + n_cls, 3), dtype=np.float32)
    # Candidate 0: chest at center (960, 540) in 1920x1080 -> (320, 320) in 640x640 letterbox
    raw[0, 0:4, 0] = [320.0, 320.0, 40.0, 40.0]
    raw[0, 4 + 0, 0] = 0.91
    # Candidate 1: heavily overlapping duplicate chest -> must be suppressed by NMS
    raw[0, 0:4, 1] = [322.0, 321.0, 40.0, 40.0]
    raw[0, 4 + 0, 1] = 0.72
    # Candidate 2: wumi at left side
    raw[0, 0:4, 2] = [100.0, 260.0, 30.0, 34.0]
    raw[0, 4 + 3, 2] = 0.84
    decoded = detector.decode_yolo_output(raw, orig_hw=(1080, 1920), scale=scale, pad_xy=pad_xy)
    assert len(decoded) == 2, decoded
    assert decoded[0].label == "chest" and abs(decoded[0].cx - 960.0) < 4.0 and abs(decoded[0].cy - 540.0) < 4.0
    assert decoded[1].label == "wumi" and detector.steer_target_dx(decoded[1]) < 0

    # Test upgraded anti-crystal race_ring CV detector: accepts ring with white core, rejects giant blue mountain
    syn_race = np.full((1080, 1920, 3), (30, 50, 40), dtype=np.uint8)
    # Giant blue crystal wall on left (no white inner diamond, too big) -> must be rejected
    syn_race[200:500, 120:420] = (40, 165, 240)
    # Real compact blue waypoint ring with bright white inner diamond at (1200, 400)
    syn_race[386:414, 1186:1214] = (35, 165, 245)
    syn_race[394:406, 1194:1206] = (245, 248, 252)
    race_dets = detector.detect_race_rings_cv(syn_race)
    assert len(race_dets) == 1 and abs(race_dets[0].cx - 1200.0) < 12.0, race_dets

    # Test cabbage seed CV detector on BOTH Priority-1 R-sonar blue badge AND Priority-2 glowing lime seed
    syn_cab = np.full((1080, 1920, 3), (55, 115, 50), dtype=np.uint8)
    syn_cab[420:444, 1320:1336] = (205, 248, 70)
    cab_dets = detector.detect_cabbage_seeds_cv(syn_cab)
    assert len(cab_dets) == 1 and abs(cab_dets[0].cx - 1328.0) < 12.0, cab_dets

    syn_sonar = np.full((1080, 1920, 3), (45, 95, 50), dtype=np.uint8)
    # 28x28 electric-blue circular sonar badge with bright white star core at (1400, 340)
    syn_sonar[326:354, 1386:1414] = (20, 175, 250)
    syn_sonar[335:345, 1395:1405] = (248, 250, 255)
    sonar_dets = detector.detect_cabbage_seeds_cv(syn_sonar)
    assert len(sonar_dets) >= 1 and abs(sonar_dets[0].cx - 1400.0) < 12.0 and sonar_dets[0].score >= 0.90, sonar_dets

    # Test HybridDetector fallback + dataset scaffold + prelabel round-trip in temp dir
    hd = detector.HybridDetector(model_path=None)
    assert hd.backend_name == "hybrid_cv_template"
    hres = hd.detect(syn_race, classes=("race_ring",))
    assert hres["ok"] is True and hres["count"] == 1, hres

    with tempfile.TemporaryDirectory() as ds_dir:
        ds_info = detector.init_yolo_dataset(out_dir=ds_dir, snaps_dir=SNAPS, max_images=6, mask_uid=True)
        assert ds_info["ok"] is True and os.path.isfile(ds_info["data_yaml"]), ds_info
        pre_info = detector.prelabel_yolo_dataset(dataset_dir=ds_dir, min_score=0.75)
        assert pre_info["ok"] is True, pre_info

    print(
        "ALL_OFFLINE_TESTS_PASSED",
        {
            "heading": heading,
            "poi0": pois[0],
            "f_ratio": ratio,
            "f_ocr": f_info["primary"],
            "nav_ground_ms": p_ground["elapsed_ms"],
            "nav_ground_wps": len(p_ground["waypoints"]),
            "templates": len(tpls),
            "ort_providers": detector.available_providers(),
        },
    )


if __name__ == "__main__":
    run_tests()
