"""Minimap POI clustering, heading estimation, and autonomous puzzle solving."""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Sequence

import numpy as np
from scipy import ndimage

from . import actions
from . import input_ctl as ctl
from . import vision
from .win import capture_client, find_game_window, focus_game


# Minimap geometry on 1920x1080 client
MM_CX = 143
MM_CY = 121
MM_R = 86


def detect_minimap_pois(img) -> List[Dict[str, object]]:
    """Cluster bright white POI icons on the circular minimap (Kibo puzzles, Wumi, Star Melodies, locks)."""
    arr = vision._norm_arr(img)
    r = MM_R
    sub = arr[MM_CY - r : MM_CY + r, MM_CX - r : MM_CX + r]
    yy, xx = np.ogrid[-r:r, -r:r]
    dist2 = xx * xx + yy * yy
    # Exclude center player arrow (r < 17) and outer rim icons (r > 77)
    ring = (dist2 <= (r - 9) ** 2) & (dist2 >= 17 ** 2)

    white = ring & (sub[:, :, 0] > 228) & (sub[:, :, 1] > 228) & (sub[:, :, 2] > 228)
    # Dilate slightly to merge fragmented icon pixels (like padlock or bunny ears)
    merged = ndimage.binary_dilation(white, iterations=2)
    labeled, n = ndimage.label(merged)

    pois: List[Dict[str, object]] = []
    for idx in range(1, n + 1):
        comp = (labeled == idx) & white
        px = int(comp.sum())
        if px < 14:
            continue
        ys, xs = np.where(comp)
        dx = float(xs.mean() - r)
        dy = float(ys.mean() - r)
        dist = math.hypot(dx, dy)
        # Bearing in degrees: 0=North(up), 90=East(right), 180=South(down), 270=West(left)
        bearing = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0

        # Check if there is a tiny green checkmark immediately bottom-right (completed challenge)
        cx_i, cy_i = int(round(xs.mean())), int(round(ys.mean()))
        chk_roi = sub[max(0, cy_i) : min(2 * r, cy_i + 16), max(0, cx_i) : min(2 * r, cx_i + 16)].astype(np.int16)
        green_chk = int(((chk_roi[:, :, 1] > 165) & (chk_roi[:, :, 1] - chk_roi[:, :, 0] > 45) & (chk_roi[:, :, 2] < 160)).sum())
        completed = green_chk >= 6

        pois.append(
            {
                "dx": round(dx, 1),
                "dy": round(dy, 1),
                "dist_px": round(dist, 1),
                "bearing_deg": round(bearing, 1),
                "pixels": px,
                "completed": completed,
            }
        )

    pois.sort(key=lambda p: p["dist_px"])
    return pois


def estimate_player_heading(img) -> Optional[float]:
    """Estimate player golden arrow heading (0=North, 90=East, 180=South, 270=West) from minimap center."""
    arr = vision._norm_arr(img)
    s = 15
    sub = arr[MM_CY - s : MM_CY + s, MM_CX - s : MM_CX + s].astype(np.int16)
    # Bright golden-yellow arrow body
    gold = (sub[:, :, 0] > 205) & (sub[:, :, 1] > 165) & (sub[:, :, 2] < 140)
    ys, xs = np.where(gold)
    if len(xs) < 12:
        return None
    cx = float(xs.mean())
    cy = float(ys.mean())
    # Tip of the arrow is the furthest golden pixel from the centroid (acute apex of isosceles chevron)
    dists = (xs - cx) ** 2 + (ys - cy) ** 2
    tip_i = int(np.argmax(dists))
    vx = float(xs[tip_i] - cx)
    vy = float(ys[tip_i] - cy)
    if math.hypot(vx, vy) < 1.5:
        return None
    return round((math.degrees(math.atan2(vx, -vy)) + 360.0) % 360.0, 1)


def solve_puzzle_auto(
    elements: Sequence[str] = ("grass", "fire", "light", "water", "earth", "ice", "thunder"),
    wait_after: float = 2.6,
) -> Dict[str, object]:
    """Try Kibo puzzle wheel elements sequentially until the Q puzzle glow disappears (solved)."""
    gw = focus_game()
    tried: List[str] = []
    solved_by: Optional[str] = None

    for el in elements:
        actions.kibo_puzzle(el, wait_after=wait_after)
        tried.append(el)
        img = capture_client(gw=gw)
        hud = vision.check_hud_state(img)
        if not hud["near_kibo_puzzle"]:
            solved_by = el
            break

    # Loot any spawned rewards/drops
    actions.interact_spam(taps=4, interval=0.2)
    return {"ok": solved_by is not None, "solved_by": solved_by, "tried": tried}


def approach_poi(
    target_bearing: float,
    walk_sec: float = 2.4,
    sprint: bool = True,
    px_per_deg: float = 8.4,
    abort_fn=None,
) -> Dict[str, object]:
    """Turn toward target_bearing (0=N, 90=E) using current player arrow heading and sprint forward."""
    gw = focus_game()
    if abort_fn is not None and abort_fn():
        return {"aborted": True}
    # Brief tap forward so character arrow aligns with camera
    ctl.hold_keys(["w"], 0.14)
    time.sleep(0.12)
    if abort_fn is not None and abort_fn():
        return {"aborted": True}
    img = capture_client(gw=gw)
    heading = estimate_player_heading(img)
    turned_dx = 0
    if heading is not None:
        delta = (target_bearing - heading + 180.0) % 360.0 - 180.0
        turned_dx = int(round(delta * px_per_deg))
        if abs(turned_dx) > 25:
            actions.turn_camera(turned_dx, 0, steps=max(6, min(18, abs(turned_dx) // 70)))
            time.sleep(0.12)
    if abort_fn is not None and abort_fn():
        return {"aborted": True}

    completed = actions.walk("w", duration=walk_sec, sprint=sprint, spam_f=True, abort_fn=abort_fn)
    img2 = capture_client(gw=gw)
    return {
        "heading_before": heading,
        "target_bearing": target_bearing,
        "turned_dx": turned_dx,
        "aborted": not completed,
        "hud_after": vision.check_hud_state(img2),
        "pois_after": detect_minimap_pois(img2),
    }
