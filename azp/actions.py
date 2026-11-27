"""High-level reusable game actions and open-world exploration loops."""
from __future__ import annotations

import json
import os
import random
import time
from typing import Dict, List, Optional

from . import input_ctl as ctl
from . import vision
from .win import GameWindow, capture_client, find_game_window, focus_game


def ensure_focus() -> GameWindow:
    return focus_game()


def walk(
    direction: str = "w",
    duration: float = 2.0,
    sprint: bool = False,
    jump: bool = False,
    spam_f: bool = False,
) -> None:
    """Move character using WASD combinations with optional sprint, jump, and F-interact pickup."""
    ensure_focus()
    keys = [ch for ch in direction.lower() if ch in ("w", "a", "s", "d")]
    if not keys:
        keys = ["w"]
    try:
        for k in keys:
            ctl.key_down(k)
        if sprint:
            time.sleep(0.05)
            ctl.tap("shift", hold=0.06, after=0.02)
            ctl.key_down("shift")

        t_end = time.time() + max(0.05, duration)
        next_f = time.time() + 0.15
        next_jump = time.time() + 0.35
        while time.time() < t_end:
            now = time.time()
            if spam_f and now >= next_f:
                ctl.tap("f", hold=0.04, after=0.0)
                next_f = now + 0.22
            if jump and now >= next_jump:
                ctl.tap("space", hold=0.06, after=0.0)
                next_jump = now + 0.9
            time.sleep(0.03)
    finally:
        if sprint:
            ctl.key_up("shift")
        for k in reversed(keys):
            ctl.key_up(k)


def turn_camera(dx: int, dy: int = 0, steps: int = 12, step_delay: float = 0.012) -> None:
    ensure_focus()
    ctl.mouse_move_rel(dx, dy, steps=steps, step_delay=step_delay)


def interact_spam(taps: int = 5, interval: float = 0.2) -> None:
    ensure_focus()
    for i in range(taps):
        ctl.tap("f", hold=0.05, after=interval if i + 1 < taps else 0.05)


# 1920x1080 Kibo puzzle wheel (hold Q) 3x3 diamond grid coordinates
KIBO_WHEEL_SLOTS: Dict[str, tuple[int, int]] = {
    "fire": (960, 355),      # 火 (top)
    "earth": (805, 435),     # 地 (upper-left)
    "grass": (1115, 435),    # 木/草 (upper-right)
    "ice": (650, 515),       # 冰 (left)
    "water": (960, 515),     # 水 (center)
    "thunder": (1270, 515),  # 雷/地 (right)
    "light": (805, 600),     # 光 (lower-left)
}


def kibo_puzzle(element: str, wait_after: float = 2.8) -> Dict[str, object]:
    """Open the Kibo puzzle wheel (hold Q) and click the requested element skill."""
    key = element.lower().strip()
    if key not in KIBO_WHEEL_SLOTS:
        raise ValueError(f"Unknown element {element!r}. Valid: {list(KIBO_WHEEL_SLOTS)}")
    gw = ensure_focus()
    ctl.key_down("q")
    time.sleep(0.85)
    ctl.key_up("q")
    time.sleep(0.45)
    x, y = KIBO_WHEEL_SLOTS[key]
    ctl.mouse_click_client(x, y, hold=0.08, gw=gw)
    time.sleep(wait_after)
    return {"ok": True, "element": key, "slot": [x, y]}


def toggle_mount() -> Dict[str, object]:
    """Summon or dismount Kibo mount via T."""
    ensure_focus()
    ctl.tap("t", hold=0.06, after=0.4)
    return {"ok": True, "action": "toggle_mount"}


def dismiss_reward(clicks: int = 2, interval: float = 0.7) -> Dict[str, object]:
    """Dismiss full-screen reward popups by clicking lower-center blank area."""
    gw = ensure_focus()
    for i in range(clicks):
        ctl.mouse_click_client(960, 920, gw=gw)
        if i + 1 < clicks:
            time.sleep(interval)
    return {"ok": True, "clicks": clicks}


def wait_for_hud(timeout: float = 18.0, poll: float = 0.6) -> Dict[str, object]:
    """Poll until open-world HUD returns (e.g. after teleporting or loading)."""
    gw = find_game_window()
    t0 = time.time()
    last_hud = {}
    while time.time() - t0 < timeout:
        img = capture_client(gw=gw)
        last_hud = vision.check_hud_state(img)
        if last_hud["in_world_hud"]:
            return {"ok": True, "elapsed": round(time.time() - t0, 2), "hud": last_hud}
        time.sleep(poll)
    return {"ok": False, "elapsed": round(time.time() - t0, 2), "hud": last_hud}


def map_teleport(map_x: float, map_y: float, open_map_first: bool = True, timeout: float = 18.0) -> Dict[str, object]:
    """Click a waypoint at (map_x, map_y) on the 1920x1080 map, click 传送 (1670, 1000), and wait for HUD."""
    gw = ensure_focus()
    if open_map_first:
        ctl.tap("m", hold=0.06, after=0.85)
    ctl.mouse_click_client(map_x, map_y, gw=gw)
    time.sleep(0.75)
    ctl.mouse_click_client(1670, 1000, gw=gw)
    time.sleep(1.5)
    res = wait_for_hud(timeout=timeout)
    return {"ok": res["ok"], "target": [map_x, map_y], "wait": res}


def scan360(out_dir: str, prefix: str = "scan", yaw_step: int = 750, scale: float = 0.75) -> Dict[str, object]:
    """Capture 4-direction 360-degree panorama (0, 90, 180, 270 deg) for rapid scouting."""
    gw = ensure_focus()
    os.makedirs(out_dir, exist_ok=True)
    names = ["front", "right", "back", "left"]
    files: List[str] = []
    for idx, name in enumerate(names):
        if idx > 0:
            ctl.mouse_move_rel(yaw_step, 0, steps=12, step_delay=0.012)
            time.sleep(0.25)
        p = os.path.join(out_dir, f"{prefix}_{name}.png")
        capture_client(gw=gw, scale=scale, save_path=p)
        files.append(p)
    # Return to roughly original heading
    ctl.mouse_move_rel(yaw_step, 0, steps=12, step_delay=0.012)
    return {"ok": True, "files": files}


def capture_kibo(throws: int = 1, wait_sec: float = 3.5) -> Dict[str, object]:
    """Enter Star-Bond aim mode (G), throw card(s) with LMB, wait, and exit aim mode (G)."""
    ensure_focus()
    ctl.tap("g", hold=0.06, after=0.6)
    for i in range(throws):
        ctl.mouse_click_here("left", clicks=1, hold=0.08)
        time.sleep(wait_sec)
    ctl.tap("g", hold=0.06, after=0.4)
    return {"ok": True, "throws": throws}


def claim_daily_activity(task_clicks: int = 4) -> Dict[str, object]:
    """Open F5 (日常活跃), claim completed daily tasks and unlocked milestone chests (20..100)."""
    gw = ensure_focus()
    ctl.tap("f5", hold=0.06, after=1.5)
    # Claim top-slot completed daily tasks (completed items shift up into row 1)
    for _ in range(task_clicks):
        ctl.mouse_click_client(1680, 235, gw=gw)
        time.sleep(0.45)
    # Claim milestone chests at 20, 40, 60, 80, 100
    for cx in (800, 1010, 1215, 1425, 1635):
        ctl.mouse_click_client(cx, 990, gw=gw)
        time.sleep(0.65)
        ctl.mouse_click_client(960, 920, gw=gw)
        time.sleep(0.35)
    ctl.tap("esc", hold=0.06, after=0.6)
    return {"ok": True, "panel": "f5_daily"}


def claim_travelogue_bp() -> Dict[str, object]:
    """Open F2 (普罗米利亚游记), click 一键领取 on both 任务 and 奖励 tabs."""
    gw = ensure_focus()
    ctl.tap("f2", hold=0.06, after=1.0)
    ctl.mouse_click_client(1740, 860, gw=gw)
    time.sleep(0.8)
    ctl.mouse_click_client(960, 920, gw=gw)
    time.sleep(0.5)
    ctl.mouse_click_client(815, 1030, gw=gw)
    time.sleep(0.8)
    ctl.mouse_click_client(1740, 860, gw=gw)
    time.sleep(0.8)
    ctl.mouse_click_client(960, 920, gw=gw)
    time.sleep(0.5)
    ctl.tap("esc", hold=0.06, after=0.6)
    return {"ok": True, "panel": "f2_travelogue"}


def combat_burst(
    duration: float = 6.0,
    switch_chars: bool = True,
    summon_kibo: bool = True,
    early_exit: bool = True,
) -> Dict[str, object]:
    """Execute an active combat rotation: Kibo summon + LMB combos + E/Q/R + dodge + char swap."""
    gw = ensure_focus()
    if summon_kibo:
        ctl.tap("z", hold=0.05, after=0.08)

    t0 = time.time()
    t_end = t0 + max(1.0, duration)
    cycle = 0
    char_idx = 1
    no_combat_streak = 0
    while time.time() < t_end:
        cycle += 1
        # Basic attack combo (4 rapid LMB clicks)
        for _ in range(4):
            ctl.mouse_click_here("left", clicks=1, hold=0.05)
            time.sleep(0.16)
            if time.time() >= t_end:
                break
        if time.time() >= t_end:
            break

        # Elemental skill E, Kibo skill Q, Ultimate R
        ctl.tap("e", hold=0.06, after=0.14)
        if cycle % 2 == 0:
            ctl.tap("q", hold=0.06, after=0.14)
        if cycle % 3 == 0:
            ctl.tap("r", hold=0.06, after=0.18)

        # Occasional dodge
        if cycle % 2 == 1:
            ctl.tap("shift", hold=0.06, after=0.10)

        # Rotate party members 1 -> 2 -> 3
        if switch_chars and cycle % 3 == 0:
            char_idx = (char_idx % 3) + 1
            ctl.tap(str(char_idx), hold=0.06, after=0.14)
            ctl.tap("e", hold=0.06, after=0.12)

        # Require 2 consecutive non-combat checks before early exit (disabled for multi-wave fights)
        if early_exit and cycle >= 2:
            img = capture_client(gw=gw)
            st = vision.check_hud_state(img)
            if st["in_world_hud"] and not st["in_combat"]:
                no_combat_streak += 1
                if no_combat_streak >= 2:
                    break
            else:
                no_combat_streak = 0

    # Collect nearby loot drops after combat
    interact_spam(taps=4, interval=0.18)
    img = capture_client(gw=gw)
    return {"elapsed": round(time.time() - t0, 2), "cycles": cycle, "hud": vision.check_hud_state(img)}


def dialog_advance(max_steps: int = 20, interval: float = 0.45) -> Dict[str, object]:
    """Advance dialogue/cutscene/reward popups until open-world HUD returns."""
    gw = ensure_focus()
    steps = 0
    for i in range(max_steps):
        img = capture_client(gw=gw)
        st = vision.check_hud_state(img)
        if st["in_world_hud"]:
            return {"steps": steps, "returned_to_hud": True, "hud": st}
        steps += 1
        # Click lower-center / right choice area and press Space + F
        ctl.tap("space", hold=0.05, after=0.08)
        ctl.tap("f", hold=0.05, after=0.08)
        ctl.mouse_click_client(1350, 740, gw=gw)
        time.sleep(interval)
    img = capture_client(gw=gw)
    return {"steps": steps, "returned_to_hud": False, "hud": vision.check_hud_state(img)}


def explore_loop(
    steps: int = 12,
    step_sec: float = 2.6,
    sprint: bool = True,
    snap_dir: Optional[str] = None,
    yaw_bias: int = 0,
) -> Dict[str, object]:
    """Autonomous open-world patrol loop with combat, F-looting, and stuck recovery."""
    gw = ensure_focus()
    if snap_dir:
        os.makedirs(snap_dir, exist_ok=True)

    log: List[Dict[str, object]] = []
    combats = 0
    unstucks = 0
    dialogs = 0

    prev_img = capture_client(gw=gw)
    for i in range(steps):
        st = vision.check_hud_state(prev_img)
        entry: Dict[str, object] = {"step": i + 1, "hud_before": st}

        if st["loading"]:
            time.sleep(1.5)
            prev_img = capture_client(gw=gw)
            entry["action"] = "wait_loading"
            log.append(entry)
            continue

        if not st["in_world_hud"]:
            res = dialog_advance(max_steps=8, interval=0.35)
            dialogs += 1
            entry["action"] = "dialog_advance"
            entry["result"] = res
            prev_img = capture_client(gw=gw)
            log.append(entry)
            continue

        if st["in_combat"]:
            res = combat_burst(duration=7.0, switch_chars=True)
            combats += 1
            entry["action"] = "combat"
            entry["result"] = res
            prev_img = capture_client(gw=gw)
            log.append(entry)
            continue

        # Normal exploration walk with continuous F-pickup spam
        if yaw_bias != 0:
            turn_camera(yaw_bias, 0, steps=6)
        walk("w", duration=step_sec, sprint=sprint, jump=(i % 3 == 2), spam_f=True)

        curr_img = capture_client(gw=gw)
        diff = vision.frame_diff(prev_img, curr_img)
        # Minimap ROI diff is much more immune to mount idle bob / grass sway
        mm_diff = vision.frame_diff(prev_img, curr_img, roi=(65, 45, 220, 198))
        entry["frame_diff"] = round(diff, 2)
        entry["minimap_diff"] = round(mm_diff, 2)
        entry["action"] = "walk_collect"

        # Stuck detection: small 3D visual change OR stagnant minimap terrain
        if diff < 7.5 or mm_diff < 3.2:
            unstucks += 1
            entry["unstuck"] = True
            walk("s", duration=0.65, sprint=False)
            turn_dx = random.choice([-480, 480, -720, 720])
            turn_camera(turn_dx, 0, steps=10)
            walk("w", duration=1.2, sprint=False, jump=True, spam_f=True)
            curr_img = capture_client(gw=gw)

        if snap_dir and (i == 0 or (i + 1) % 4 == 0 or i == steps - 1):
            p = os.path.join(snap_dir, f"step_{i + 1:02d}.png")
            curr_img.save(p)
            entry["snap"] = p

        prev_img = curr_img
        log.append(entry)

    ctl.release_movement_keys()
    return {
        "steps_run": len(log),
        "combats": combats,
        "unstucks": unstucks,
        "dialogs": dialogs,
        "log": log,
    }
