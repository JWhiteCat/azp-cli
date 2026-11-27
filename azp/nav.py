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
    """Estimate player golden arrow heading (0=North, 90=East, 180=S, 270=W) via PCA major axis + skewness.

    Using the 3rd central moment along the principal axis uniquely identifies the sharp acute nose tip
    of the chevron and avoids 135-180 deg flips onto the rear wingtips.
    """
    arr = vision._norm_arr(img)
    s = 11
    sub = arr[MM_CY - s : MM_CY + s, MM_CX - s : MM_CX + s].astype(np.int16)
    # Bright golden-yellow arrow body; isolate the connected component closest to minimap center (s, s)
    # so nearby yellow fog-border lines or egg/boss icons cannot corrupt the arrow PCA.
    gold = (sub[:, :, 0] > 200) & (sub[:, :, 1] > 160) & (sub[:, :, 2] < 140)
    labeled, n = ndimage.label(gold)
    if n > 1:
        best_lbl = 0
        best_d2 = 1e9
        for lbl_id in range(1, n + 1):
            ly, lx = np.where(labeled == lbl_id)
            if len(lx) < 8:
                continue
            d2 = float((lx.mean() - s) ** 2 + (ly.mean() - s) ** 2)
            if d2 < best_d2:
                best_d2 = d2
                best_lbl = lbl_id
        if best_lbl > 0:
            gold = labeled == best_lbl
    ys, xs = np.where(gold)
    if len(xs) < 12 or len(xs) > 150:
        return None
    pts = np.stack([xs, ys], axis=1).astype(np.float64)
    centered = pts - pts.mean(axis=0)
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    major = eigvecs[:, 1]
    minor = eigvecs[:, 0]
    proj = centered @ major
    perp = centered @ minor
    # An arrowhead is always much narrower at its acute nose tip (~1.3 px std)
    # than at its flared rear wings (~2.8 px std). Comparing transverse spread on
    # each side of the centroid eliminates 180-deg flips where 3rd-moment skew is near 0.
    pos_perp = perp[proj > 0]
    neg_perp = perp[proj < 0]
    if len(pos_perp) >= 3 and len(neg_perp) >= 3:
        w_pos = float(np.std(pos_perp))
        w_neg = float(np.std(neg_perp))
        tip = major * (1.0 if w_pos < w_neg else -1.0)
    else:
        skew = float(np.mean(proj ** 3))
        tip = major * (1.0 if skew >= 0 else -1.0)
    return round((math.degrees(math.atan2(float(tip[0]), -float(tip[1]))) + 360.0) % 360.0, 1)


def solve_puzzle_auto(
    elements: Sequence[str] = ("grass", "fire", "light", "water", "thunder", "ice", "earth"),
    wait_after: float = 2.6,
) -> Dict[str, object]:
    """Try Kibo puzzle wheel elements sequentially until the Q puzzle glow disappears (solved).

    Guards against empty wheel slots: if clicking a slot leaves the Kibo wheel open
    (``in_world_hud == False``), presses Esc to dismiss the wheel and continues trying
    instead of falsely treating the cleared HUD as solved.
    """
    gw = focus_game()
    tried: List[str] = []
    skipped_empty: List[str] = []
    solved_by: Optional[str] = None

    for el in elements:
        actions.kibo_puzzle(el, wait_after=wait_after)
        tried.append(el)
        img = capture_client(gw=gw)
        hud = vision.check_hud_state(img)
        if not hud["in_world_hud"] and not hud["loading"]:
            # Clicked an empty slot on the wheel; close wheel and keep trying
            ctl.tap("esc", hold=0.06, after=0.35)
            skipped_empty.append(el)
            continue
        if not hud["near_kibo_puzzle"]:
            # Wait briefly and re-verify so temporary skill-cooldown dimming is not mistaken for a solve
            time.sleep(1.1)
            hud2 = vision.check_hud_state(capture_client(gw=gw))
            if hud2["in_world_hud"] and not hud2["near_kibo_puzzle"]:
                solved_by = el
                break

    # Loot any spawned rewards/drops
    actions.interact_spam(taps=4, interval=0.2)
    return {"ok": solved_by is not None, "solved_by": solved_by, "tried": tried, "skipped_empty": skipped_empty}


def approach_poi(
    target_bearing: float,
    walk_sec: float = 2.4,
    sprint: bool = True,
    px_per_deg: float = 6.35,
    spam_f: bool = True,
    unstuck: bool = True,
    abort_fn=None,
) -> Dict[str, object]:
    """Turn toward target_bearing (0=N, 90=E) using current player arrow heading and sprint forward.

    When ``unstuck`` is enabled on runs longer than ~2.5s, monitors minimap frame diff between
    ~2.2s segments and performs an automatic jump + sidestep maneuver if snagged on terrain.
    """
    gw = focus_game()
    if abort_fn is not None and abort_fn():
        return {"aborted": True}
    # Brief tap forward so character/mount arrow aligns with camera even after S/A/D movement
    ctl.hold_keys(["w"], 0.24)
    time.sleep(0.14)
    if abort_fn is not None and abort_fn():
        return {"aborted": True}
    img = capture_client(gw=gw)
    hud0 = vision.check_hud_state(img)
    flying = bool(hud0.get("is_flying"))
    heading = estimate_player_heading(img)
    turned_dx = 0
    if heading is not None:
        # Closed-loop yaw alignment: compensates for foot vs mount vs flight camera sensitivity.
        # Flying mounts have ~2x lower yaw sensitivity (~13 px/deg) and banking inertia (~0.5s W settle),
        # so give flight up to 3 passes with calibrated gain to lock onto target_bearing within +-5 deg.
        cur_heading = heading
        max_passes = 3 if flying else 2
        for pass_idx in range(max_passes):
            delta = (target_bearing - cur_heading + 180.0) % 360.0 - 180.0
            tol = 6.0 if pass_idx < max_passes - 1 else 10.0
            if abs(delta) <= tol:
                break
            if flying:
                gain = 13.5 if pass_idx == 0 else 12.0
            else:
                gain = px_per_deg if pass_idx == 0 else 9.5
            step_dx = int(round(delta * gain))
            if abs(step_dx) <= 25:
                break
            # Keep per-step delta <= ~38px so Unity Raw Input never drops large single-frame deltas
            actions.turn_camera(step_dx, 0, steps=max(8, abs(step_dx) // 35), step_delay=0.010)
            turned_dx += step_dx
            time.sleep(0.12)
            if pass_idx < max_passes - 1:
                if abort_fn is not None and abort_fn():
                    return {"aborted": True}
                ctl.hold_keys(["w"], 0.50 if flying else 0.22)
                time.sleep(0.12)
                h2 = estimate_player_heading(capture_client(gw=gw))
                if h2 is None:
                    break
                cur_heading = h2
    if abort_fn is not None and abort_fn():
        return {"aborted": True}

    unstuck_count = 0
    completed = True
    if not unstuck or walk_sec <= 2.6:
        completed = actions.walk("w", duration=walk_sec, sprint=sprint, spam_f=spam_f, abort_fn=abort_fn)
    else:
        remaining = float(walk_sec)
        prev_img = capture_client(gw=gw)
        side_idx = 0
        while remaining > 0.05:
            seg = min(2.2, remaining)
            completed = actions.walk("w", duration=seg, sprint=sprint, spam_f=spam_f, abort_fn=abort_fn)
            remaining -= seg
            if not completed:
                break
            cur_img = capture_client(gw=gw)
            cur_hud = vision.check_hud_state(cur_img)
            if not cur_hud["in_world_hud"]:
                # Opened a repair/build dialog, tutorial, or cutscene — stop immediately
                break
            mm_diff = vision.frame_diff(prev_img, cur_img, roi=(65, 45, 220, 198))
            prev_img = cur_img
            if mm_diff < 2.8 and remaining > 0.4:
                unstuck_count += 1
                side_key = "d" if (side_idx % 2 == 0) else "a"
                side_idx += 1
                actions.walk(f"w{side_key}", duration=0.85, sprint=sprint, jump=True, spam_f=False, abort_fn=abort_fn)
                prev_img = capture_client(gw=gw)
    img2 = capture_client(gw=gw)
    return {
        "heading_before": heading,
        "target_bearing": target_bearing,
        "turned_dx": turned_dx,
        "unstuck_count": unstuck_count,
        "aborted": not completed,
        "hud_after": vision.check_hud_state(img2),
        "pois_after": detect_minimap_pois(img2),
    }


def safe_land(max_sec: float = 6.5, dismount: bool = True, abort_fn=None) -> Dict[str, object]:
    """Safely descend with Ctrl until touchdown (viewport stops shifting), then dismount with T (zero fall damage)."""
    gw = focus_game()
    img = capture_client(gw=gw)
    hud = vision.check_hud_state(img)
    if not hud.get("is_flying"):
        return {"ok": True, "was_flying": False, "descend_sec": 0.0, "hud": hud}

    t0 = time.time()
    prev_img = img
    settled_streak = 0
    while time.time() - t0 < max_sec:
        if abort_fn is not None and abort_fn():
            return {"ok": False, "aborted": True, "descend_sec": round(time.time() - t0, 2)}
        ctl.hold_keys(["ctrl"], 0.65)
        cur_img = capture_client(gw=gw)
        cur_hud = vision.check_hud_state(cur_img)
        if not cur_hud.get("is_flying"):
            return {"ok": True, "was_flying": True, "descend_sec": round(time.time() - t0, 2), "hud": cur_hud}
        # Compare lower/central viewport (excluding wings at top) to detect touchdown stall
        diff = vision.frame_diff(prev_img, cur_img, roi=(280, 560, 1640, 920))
        prev_img = cur_img
        if diff < 3.0:
            settled_streak += 1
            if settled_streak >= 2:
                break
        else:
            settled_streak = 0

    if dismount:
        ctl.tap("t", hold=0.06, after=0.95)
    final_hud = vision.check_hud_state(capture_client(gw=gw))
    return {
        "ok": True,
        "was_flying": True,
        "descend_sec": round(time.time() - t0, 2),
        "dismounted": not final_hud.get("is_flying", False),
        "hud": final_hud,
    }


def fly_to_bearing(
    target_bearing: float,
    fly_sec: float = 2.4,
    climb_sec: float = 2.5,
    boost: bool = True,
    land: bool = False,
    abort_fn=None,
) -> Dict[str, object]:
    """3D flight navigation: auto-summon flying mount (T), climb vertically (Space, never S), align compass, and cruise.

    If blocked by a cliff or canopy (minimap diff < 2.5), climbs vertically with Space instead of sidestepping.
    Set ``land=True`` to safely descend with Ctrl until ground touchdown and dismount (zero fall damage).
    """
    gw = focus_game()
    img0 = capture_client(gw=gw)
    hud0 = vision.check_hud_state(img0)
    took_off = False
    if not hud0.get("is_flying"):
        ctl.tap("t", hold=0.06, after=1.05)
        took_off = True
        if not vision.check_hud_state(capture_client(gw=gw)).get("is_flying"):
            # Blocked by rock overhang or tight geometry -> step back briefly and retry takeoff
            ctl.hold_keys(["s"], 0.65)
            time.sleep(0.15)
            ctl.tap("t", hold=0.06, after=1.05)

    # Align compass cleanly first so any forward+up climb heads straight toward target_bearing
    align_res = approach_poi(
        target_bearing=target_bearing,
        walk_sec=0.25,
        sprint=False,
        spam_f=False,
        unstuck=False,
        abort_fn=abort_fn,
    )
    if align_res.get("aborted"):
        return {"ok": False, "aborted": True, "align": align_res}

    # In Azur Promilia, Space alone rises slowly while W+Space climbs steeply at ~730 speed.
    # Start with a brief vertical Space lift, then hold W+Space to clear cliffs/canopies along target_bearing.
    if climb_sec > 0.05:
        vert_sec = min(1.0, float(climb_sec) * 0.35)
        fwd_climb_sec = max(0.0, float(climb_sec) - vert_sec)
        if vert_sec > 0.05:
            ctl.hold_keys(["space"], vert_sec)
        if fwd_climb_sec > 0.05:
            ctl.hold_keys(["w", "space"], fwd_climb_sec)
        time.sleep(0.08)

    remaining = max(0.0, float(fly_sec))
    prev_img = capture_client(gw=gw)
    climbs_unstuck = 0
    aborted = False
    while remaining > 0.05:
        if abort_fn is not None and abort_fn():
            aborted = True
            break
        seg = min(1.8, remaining)
        keys = ["w", "shift"] if boost else ["w"]
        ctl.hold_keys(keys, seg)
        remaining -= seg
        cur_img = capture_client(gw=gw)
        cur_hud = vision.check_hud_state(cur_img)
        if not cur_hud["in_world_hud"]:
            break
        mm_diff = vision.frame_diff(prev_img, cur_img, roi=(65, 45, 220, 198))
        prev_img = cur_img
        if mm_diff < 2.5 and remaining > 0.25:
            # Blocked by cliff wall -> lift with Space then climb diagonally with W+Space (never press S!)
            climbs_unstuck += 1
            ctl.hold_keys(["space"], 1.4)
            ctl.hold_keys(["w", "space"], 1.8)
            prev_img = capture_client(gw=gw)

    land_res = safe_land(dismount=True, abort_fn=abort_fn) if (land and not aborted) else None
    img_end = capture_client(gw=gw)
    return {
        "ok": not aborted,
        "took_off": took_off,
        "target_bearing": target_bearing,
        "heading_before": align_res.get("heading_before"),
        "turned_dx": align_res.get("turned_dx"),
        "climbs_unstuck": climbs_unstuck,
        "landed": land_res,
        "hud_after": vision.check_hud_state(img_end),
        "pois_after": detect_minimap_pois(img_end),
    }
