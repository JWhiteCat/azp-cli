"""Idle-aware cooperative open-world runner that yields instantly when the user touches mouse/keyboard."""
from __future__ import annotations

import os
import random
import time
from typing import Dict, List, Optional

import win32api

from . import actions
from . import input_ctl as ctl
from . import nav
from . import vision
from .win import capture_background, capture_client, find_game_window, focus_game, is_focused, user_idle_seconds


def _cursor_pos() -> tuple[int, int]:
    try:
        return win32api.GetCursorPos()
    except Exception:
        return (0, 0)


def _user_interrupted(expected_pos: Optional[tuple[int, int]], move_thresh: int = 35) -> bool:
    """Detect physical user takeover via unexpected cursor jump or manual window switch."""
    if expected_pos is not None:
        cx, cy = _cursor_pos()
        if abs(cx - expected_pos[0]) > move_thresh or abs(cy - expected_pos[1]) > move_thresh:
            return True
    return False


def wait_until_user_idle(idle_threshold: float = 15.0, max_wait: float = 120.0, poll: float = 1.0) -> bool:
    """Wait without touching desktop until user has been idle for >= idle_threshold seconds."""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if user_idle_seconds() >= idle_threshold:
            return True
        time.sleep(poll)
    return False


def coop_explore(
    steps: int = 12,
    idle_threshold: float = 15.0,
    max_idle_wait: float = 90.0,
    step_sec: float = 2.4,
    snap_dir: Optional[str] = None,
) -> Dict[str, object]:
    """Explore open world ONLY when user is idle; yield immediately if user takes over."""
    if snap_dir:
        os.makedirs(snap_dir, exist_ok=True)

    log: List[Dict[str, object]] = []
    yields = 0
    combats = 0
    puzzles_tried = 0
    unstucks = 0

    for i in range(steps):
        # 1. Wait passively if user is currently active
        if user_idle_seconds() < idle_threshold and not is_focused():
            yields += 1
            if not wait_until_user_idle(idle_threshold=idle_threshold, max_wait=max_idle_wait):
                log.append({"step": i + 1, "action": "stopped_user_active"})
                break

        gw = find_game_window()
        # Focus game only after user idle threshold is met
        if not is_focused(gw):
            if user_idle_seconds() < idle_threshold:
                yields += 1
                continue
            focus_game(gw)

        prev_img = capture_client(gw=gw)
        st = vision.check_hud_state(prev_img)
        entry: Dict[str, object] = {"step": i + 1, "hud_before": st}

        if st["loading"]:
            time.sleep(1.5)
            entry["action"] = "wait_loading"
            log.append(entry)
            continue

        if not st["in_world_hud"]:
            entry["action"] = "paused_non_hud"
            log.append(entry)
            time.sleep(1.0)
            continue

        # Check cursor baseline before acting so we detect user takeover immediately
        c_before = _cursor_pos()

        if st["in_combat"]:
            res = actions.combat_burst(duration=7.0, switch_chars=True)
            combats += 1
            entry["action"] = "combat"
            entry["result"] = res
        elif st["near_kibo_puzzle"]:
            res = nav.solve_puzzle_auto(elements=("grass", "fire", "light", "water", "earth"))
            puzzles_tried += 1
            entry["action"] = "solve_puzzle"
            entry["result"] = res
        else:
            pois = [p for p in nav.detect_minimap_pois(prev_img) if not p["completed"]]
            if pois and pois[0]["dist_px"] > 18:
                res = nav.approach_poi(target_bearing=pois[0]["bearing_deg"], walk_sec=step_sec, sprint=True)
                entry["action"] = "approach_poi"
                entry["target_poi"] = pois[0]
            else:
                actions.walk("w", duration=step_sec, sprint=True, jump=(i % 3 == 2), spam_f=True)
                entry["action"] = "walk_collect"

        # Immediately yield if user switched focus away or moved cursor dramatically
        if not is_focused(gw) or _user_interrupted(c_before, move_thresh=180):
            ctl.release_movement_keys()
            yields += 1
            entry["yielded_to_user"] = True
            log.append(entry)
            if not wait_until_user_idle(idle_threshold=idle_threshold, max_wait=max_idle_wait):
                break
            continue

        curr_img = capture_client(gw=gw)
        mm_diff = vision.frame_diff(prev_img, curr_img, roi=(65, 45, 220, 198))
        entry["minimap_diff"] = round(mm_diff, 2)

        if entry["action"] in ("walk_collect", "approach_poi") and mm_diff < 3.2:
            unstucks += 1
            entry["unstuck"] = True
            actions.walk("s", duration=0.6, sprint=False)
            actions.turn_camera(random.choice([-520, 520, -760, 760]), 0, steps=10)
            actions.walk("w", duration=1.1, sprint=False, jump=True, spam_f=True)
            curr_img = capture_client(gw=gw)

        if snap_dir and (i == 0 or (i + 1) % 3 == 0 or i == steps - 1):
            p = os.path.join(snap_dir, f"coop_{i + 1:02d}.png")
            curr_img.save(p)
            entry["snap"] = p

        log.append(entry)

    ctl.release_movement_keys()
    return {
        "steps_run": len(log),
        "combats": combats,
        "puzzles_tried": puzzles_tried,
        "unstucks": unstucks,
        "yields": yields,
        "log": log,
    }
