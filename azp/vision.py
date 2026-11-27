"""Lightweight PIL+NumPy vision utilities calibrated for 1920x1080 AzurPromilia."""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw

# Calibrated bottom-left UID bounding box normalized to 1440x810 (matches (80, 778, 272, 810))
UID_BOX_NORM: Tuple[float, float, float, float] = (80.0 / 1440.0, 778.0 / 810.0, 272.0 / 1440.0, 1.0)
DEFAULT_UID_MASK_COLOR: Tuple[int, int, int] = (18, 22, 26)


# Top-right menu icon red-dot approximate centers on 1920x1080
TOP_MENU_DOTS: Dict[str, Tuple[int, int, int, int]] = {
    "esc": (56, 22, 82, 48),
    "y": (1280, 22, 1306, 48),
    "f1": (1356, 22, 1382, 48),
    "f2": (1430, 22, 1456, 48),
    "f3": (1504, 22, 1530, 48),
    "f4": (1578, 22, 1604, 48),
    "f5": (1652, 22, 1678, 48),
    "f6": (1726, 22, 1752, 48),
    "p": (1796, 22, 1824, 48),
    "c": (1868, 22, 1894, 48),
}


def to_rgb_array(img: Image.Image) -> np.ndarray:
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img, dtype=np.uint8)


def _norm_arr(img: Image.Image) -> np.ndarray:
    arr = to_rgb_array(img)
    if arr.shape[:2] != (1080, 1920):
        arr = to_rgb_array(img.resize((1920, 1080), Image.Resampling.BILINEAR))
    return arr


def frame_diff(img1: Image.Image, img2: Image.Image, roi: Tuple[int, int, int, int] = (260, 140, 1660, 940)) -> float:
    """Mean absolute pixel difference in central 3D viewport (0..255)."""
    a = _norm_arr(img1)[roi[1]:roi[3], roi[0]:roi[2]].astype(np.int16)
    b = _norm_arr(img2)[roi[1]:roi[3], roi[0]:roi[2]].astype(np.int16)
    return float(np.mean(np.abs(a - b)))


def is_loading_screen(img: Image.Image) -> bool:
    """Detect solid black/white transitions OR AzurPromilia's dark navy bottom loading banner."""
    arr = _norm_arr(img)
    mean_v = float(arr.mean())
    std_v = float(arr.std())
    if (mean_v < 18.0 and std_v < 25.0) or (mean_v > 235.0 and std_v < 25.0):
        return True
    # AzurPromilia loading screen has either:
    # 1) Dark-navy (blue-dominant) night tip banner, OR
    # 2) Bright warm-cream daytime tip banner at y=810..1075
    bot = arr[820:1070, 80:1840]
    bot_rgb = bot.mean(axis=(0, 1))
    bot_mean = float(bot_rgb.mean())
    bot_std = float(bot.std())
    dark_navy = bot_mean < 42.0 and bot_std < 22.0 and float(bot_rgb[2]) >= float(bot_rgb[1])
    day_cream = bot_mean > 220.0 and bot_std < 22.0
    return dark_navy or day_cream


def is_confirm_dialog(img: Image.Image) -> bool:
    """Detect centered white modal confirmation box (e.g. 是否要离开家园?) with 确认 at (1258, 676)."""
    arr = _norm_arr(img)
    dlg = arr[420:560, 520:1400]
    white_ratio = float(((dlg[:, :, 0] > 215) & (dlg[:, :, 1] > 215) & (dlg[:, :, 2] > 215)).mean())
    return white_ratio > 0.78


def mask_uid_image(
    src: Union[str, Image.Image],
    out_path: Optional[str] = None,
    color: Tuple[int, int, int] = DEFAULT_UID_MASK_COLOR,
) -> Dict[str, object]:
    """Paint an opaque solid box over the bottom-left UID area (safe for committing screenshots)."""
    if isinstance(src, str):
        im = Image.open(src).convert("RGB")
        target_path = out_path or src
    else:
        im = src.convert("RGB")
        target_path = out_path

    w, h = im.size
    x1 = int(round(UID_BOX_NORM[0] * w))
    y1 = int(round(UID_BOX_NORM[1] * h))
    x2 = int(round(UID_BOX_NORM[2] * w))
    y2 = int(round(UID_BOX_NORM[3] * h))
    draw = ImageDraw.Draw(im)
    draw.rectangle([x1, y1, x2, y2], fill=color)

    if target_path:
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        im.save(target_path)

    return {
        "saved": target_path,
        "size": [w, h],
        "uid_box": [x1, y1, x2, y2],
        "color": list(color),
    }


def detect_red_dots(img: Image.Image) -> List[str]:
    """Return names of top HUD buttons currently displaying a red notification dot."""
    arr = _norm_arr(img).astype(np.int16)
    found: List[str] = []
    for name, (x1, y1, x2, y2) in TOP_MENU_DOTS.items():
        roi = arr[y1:y2, x1:x2]
        red = (roi[:, :, 0] > 195) & (roi[:, :, 1] < 95) & (roi[:, :, 2] < 95) & ((roi[:, :, 0] - roi[:, :, 1]) > 110)
        if int(red.sum()) >= 14:
            found.append(name)
    return found


def detect_interact_prompt(img: Image.Image) -> Tuple[bool, float]:
    """Detect the calibrated [F] interaction pill on right-center (x=1140..1460, y=545..592)."""
    arr = _norm_arr(img)
    pill = arr[548:590, 1145:1420]
    # The active [F] option pill is a very bright, low-saturation light-gray/white bar
    bright = (pill[:, :, 0] > 205) & (pill[:, :, 1] > 205) & (pill[:, :, 2] > 205)
    spread = np.max(pill, axis=2).astype(np.int16) - np.min(pill, axis=2).astype(np.int16)
    white_pill_ratio = float((bright & (spread < 26)).mean())
    return (white_pill_ratio > 0.35), round(white_pill_ratio, 3)


def detect_kibo_puzzle_hint(img: Image.Image) -> bool:
    """Detect when Q glows brightly near a Kibo puzzle (gold/green aura around Q button)."""
    arr = _norm_arr(img)
    # Q skill ring around x=1600..1696, y=942..1038
    q_roi = arr[942:1038, 1600:1696].astype(np.int16)
    # Bright yellow-white or bright green-white ring pixels
    glow = ((q_roi[:, :, 0] > 210) & (q_roi[:, :, 1] > 215) & (q_roi[:, :, 2] > 140)) | (
        (q_roi[:, :, 1] > 220) & (q_roi[:, :, 0] > 160) & (q_roi[:, :, 2] < 160)
    )
    return float(glow.mean()) > 0.20


def check_hud_state(img: Image.Image) -> Dict[str, object]:
    """Comprehensive HUD & state detection calibrated for 1920x1080 AzurPromilia."""
    arr = _norm_arr(img)

    # 1. Bottom-center HP bar (calibrated: y=996..1006, x=830..1090)
    # Require vertical contrast against bands above/below so bright cream modals (Y 星之旅录 / 山谷秘藏 / 建筑管理)
    # never falsely pass as an open-world HP bar.
    hp_strip = arr[996:1006, 830:1090]
    hp_above = arr[972:988, 830:1090]
    hp_below = arr[1014:1030, 830:1090]
    hp_bright = float(((hp_strip[:, :, 0] > 180) & (hp_strip[:, :, 1] > 180) & (hp_strip[:, :, 2] > 180)).mean())
    above_bright = float(((hp_above[:, :, 0] > 180) & (hp_above[:, :, 1] > 180) & (hp_above[:, :, 2] > 180)).mean())
    below_bright = float(((hp_below[:, :, 0] > 180) & (hp_below[:, :, 1] > 180) & (hp_below[:, :, 2] > 180)).mean())
    hp_contrast = float(hp_strip.mean()) - 0.5 * (float(hp_above.mean()) + float(hp_below.mean()))
    hp_bar_valid = (hp_bright > 0.14) and (hp_contrast > 24.0) and (max(above_bright, below_bright) < 0.42)

    # 2. Combat Q-skill slot bottom-right (y=942..1038, x=1600..1696; absent in Home Ranch)
    q_slot = arr[942:1038, 1600:1696]
    skill_std = float(q_slot.std())

    # 3. Loading check
    loading = is_loading_screen(img)

    # 4. Enemy red HP bars: strict horizontal bright-red thin bar detector in central viewport
    mid = arr[150:740, 260:1540].astype(np.int16)
    red_mask = (mid[:, :, 0] > 205) & (mid[:, :, 1] < 72) & (mid[:, :, 2] < 72) & ((mid[:, :, 0] - mid[:, :, 1]) > 140)
    # Check contiguous horizontal red runs of length >= 36px on each row (excludes scattered red flowers/banners)
    bar_rows = 0
    row_sums = red_mask.sum(axis=1)
    for r_idx in np.where(row_sums >= 36)[0]:
        row = red_mask[r_idx]
        # Fast contiguous run check
        padded = np.concatenate(([False], row, [False]))
        diff = np.diff(padded.astype(np.int8))
        starts = np.where(diff == 1)[0]
        ends = np.where(diff == -1)[0]
        if len(starts) > 0 and int((ends - starts).max()) >= 36:
            bar_rows += 1
    # An enemy HP bar is ~4-10px tall; tall red patches (>28 rows) are usually scenery banners/roofs
    in_combat = 3 <= bar_rows <= 32

    # 5. [F] interact pill
    has_interact, interact_ratio = detect_interact_prompt(img)

    in_world_hud = (not loading) and hp_bar_valid and (skill_std > 28.0)
    confirm_dialog = (not loading) and (not in_world_hud) and is_confirm_dialog(img)

    # 6. Kibo puzzle glow on Q (gated on open-world HUD so bright gold icons on menus never false-trigger)
    near_kibo_puzzle = in_world_hud and detect_kibo_puzzle_hint(img)

    return {
        "in_world_hud": in_world_hud,
        "loading": loading,
        "confirm_dialog": confirm_dialog,
        "in_combat": in_combat,
        "enemy_bar_rows": bar_rows,
        "hp_bright_ratio": round(hp_bright, 3),
        "has_interact_f": has_interact,
        "interact_pill_ratio": interact_ratio,
        "near_kibo_puzzle": near_kibo_puzzle,
    }


def analyze_minimap(img: Image.Image) -> Dict[str, object]:
    """Analyze the top-left circular minimap (center ~ 143, 121, radius ~ 88)."""
    arr = _norm_arr(img)

    cx, cy, r = 143, 121, 86
    sub = arr[cy - r : cy + r, cx - r : cx + r]
    yy, xx = np.ogrid[-r:r, -r:r]
    dist2 = xx * xx + yy * yy
    ring = (dist2 <= (r - 8) ** 2) & (dist2 >= 15 ** 2)

    # Bright white POI icons (Wumi, Kibo puzzles, Star Melodies)
    white_mask = (
        ring
        & (sub[:, :, 0] > 235)
        & (sub[:, :, 1] > 235)
        & (sub[:, :, 2] > 235)
    )
    # Gold/yellow markers (chests, quests, exploration challenges)
    gold_mask = (
        ring
        & (sub[:, :, 0] > 215)
        & (sub[:, :, 1] > 175)
        & (sub[:, :, 2] < 105)
    )
    # Cyan/blue markers (teleporters, star veins)
    cyan_mask = (
        ring
        & (sub[:, :, 0] < 110)
        & (sub[:, :, 1] > 185)
        & (sub[:, :, 2] > 220)
    )

    def _centroid(mask) -> Optional[Tuple[float, float, int]]:
        ys, xs = np.where(mask)
        if len(xs) < 10:
            return None
        return (round(float(xs.mean() - r), 1), round(float(ys.mean() - r), 1), int(len(xs)))

    return {
        "white_poi_rel": _centroid(white_mask),
        "gold_marker_rel": _centroid(gold_mask),
        "cyan_marker_rel": _centroid(cyan_mask),
    }
