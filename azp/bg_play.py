"""Zero-foreground-hijack background controller using PrintWindow + ViGEmBus Xbox 360 gamepad."""
from __future__ import annotations

import os
import random
import time
from typing import Dict, List, Optional

from . import gamepad
from . import vision
from .win import capture_background, find_game_window, is_focused


def probe_background_gamepad() -> Dict[str, object]:
    """Non-intrusively test whether AzurPromilia accepts ViGEmBus XInput while unfocused."""
    gw = find_game_window()
    was_fg = is_focused(gw)
    img1 = capture_background(gw=gw)
    with gamepad.VirtualX360() as pad:
        pad.right_stick(0.85, 0.0, duration=0.6)
        time.sleep(0.25)
    img2 = capture_background(gw=gw)
    diff = vision.frame_diff(img1, img2)
    return {
        "was_focused": was_fg,
        "frame_diff": round(diff, 2),
        "bg_gamepad_supported": (not was_fg) and diff > 4.0,
    }


def bg_combat_burst(pad: gamepad.VirtualX360, gw, duration: float = 7.0) -> Dict[str, object]:
    """Execute gamepad combat rotation in background (X attack, B dodge, RB/RT skills, Y loot)."""
    t0 = time.time()
    t_end = t0 + max(1.0, duration)
    cycles = 0
    while time.time() < t_end:
        cycles += 1
        for _ in range(4):
            pad.press("x", hold=0.06, after=0.14)
            if time.time() >= t_end:
                break
        pad.press("rb", hold=0.08, after=0.14)
        if cycles % 2 == 0:
            pad.press("b", hold=0.07, after=0.12)
        if cycles % 3 == 0:
            pad.press("y", hold=0.08, after=0.14)
        img = capture_background(gw=gw)
        st = vision.check_hud_state(img)
        if st["in_world_hud"] and not st["in_combat"] and cycles >= 2:
            break
    # Collect drops
    for _ in range(4):
        pad.press("y", hold=0.06, after=0.15)
    return {"elapsed": round(time.time() - t0, 2), "cycles": cycles}


def bg_explore(
    steps: int = 10,
    step_sec: float = 2.4,
    snap_dir: Optional[str] = None,
    require_unfocused: bool = False,
) -> Dict[str, object]:
    """Pure background open-world exploration loop using PrintWindow + ViGEmBus Xbox 360 gamepad.

    Never touches foreground window focus, physical mouse cursor, or keyboard!
    """
    gw = find_game_window()
    if snap_dir:
        os.makedirs(snap_dir, exist_ok=True)

    log: List[Dict[str, object]] = []
    combats = 0
    unstucks = 0

    with gamepad.VirtualX360() as pad:
        prev_img = capture_background(gw=gw)
        for i in range(steps):
            if require_unfocused and is_focused(gw):
                log.append({"step": i + 1, "action": "skipped_game_focused"})
                time.sleep(1.0)
                continue

            st = vision.check_hud_state(prev_img)
            entry: Dict[str, object] = {"step": i + 1, "focused": is_focused(gw), "hud_before": st}

            if st["loading"] or not st["in_world_hud"]:
                time.sleep(1.2)
                prev_img = capture_background(gw=gw)
                entry["action"] = "wait_hud"
                log.append(entry)
                continue

            if st["in_combat"]:
                res = bg_combat_burst(pad, gw, duration=7.5)
                combats += 1
                entry["action"] = "bg_combat"
                entry["result"] = res
                prev_img = capture_background(gw=gw)
                log.append(entry)
                continue

            # Walk forward with left stick + periodic Y/A interact taps
            pad.left_stick(0.0, 1.0, duration=step_sec)
            pad.press("y", hold=0.05, after=0.08)
            pad.press("a", hold=0.05, after=0.08)

            curr_img = capture_background(gw=gw)
            mm_diff = vision.frame_diff(prev_img, curr_img, roi=(65, 45, 220, 198))
            vp_diff = vision.frame_diff(prev_img, curr_img)
            entry["minimap_diff"] = round(mm_diff, 2)
            entry["frame_diff"] = round(vp_diff, 2)
            entry["action"] = "bg_walk"

            if mm_diff < 2.8 and vp_diff < 5.0:
                unstucks += 1
                entry["unstuck"] = True
                pad.left_stick(0.0, -0.8, duration=0.5)
                pad.right_stick(random.choice([-0.85, 0.85]), 0.0, duration=0.55)
                pad.left_stick(0.0, 1.0, duration=0.9)
                curr_img = capture_background(gw=gw)

            if snap_dir and (i == 0 or (i + 1) % 3 == 0 or i == steps - 1):
                p = os.path.join(snap_dir, f"bg_step_{i + 1:02d}.png")
                curr_img.save(p)
                entry["snap"] = p

            prev_img = curr_img
            log.append(entry)

    return {"steps_run": len(log), "combats": combats, "unstucks": unstucks, "log": log}
