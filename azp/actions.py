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
    abort_fn=None,
) -> bool:
    """Move character using WASD combinations with optional sprint, jump, and F-interact pickup.

    Returns True if completed normally, False if aborted early by abort_fn().
    """
    ensure_focus()
    keys = [ch for ch in direction.lower() if ch in ("w", "a", "s", "d")]
    if not keys:
        keys = ["w"]
    completed = True
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
            if abort_fn is not None and abort_fn():
                completed = False
                break
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
    return completed


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


# 1920x1080 Mount wheel (hold T) slot coordinates
MOUNT_WHEEL_SLOTS: Dict[str, tuple[int, int]] = {
    "bird": (960, 290),   # Slot 1: Bird mount (速 350, hover/fly)
    "wolf": (1140, 345),  # Slot 2: Wolf mount (速 800, fast ground sprint)
}


def toggle_mount() -> Dict[str, object]:
    """Summon or dismount Kibo mount via T (waits 0.95s so mount camera lock finishes)."""
    ensure_focus()
    ctl.tap("t", hold=0.06, after=0.95)
    return {"ok": True, "action": "toggle_mount"}


def select_mount(mount_name: str) -> Dict[str, object]:
    """Open the Mount Wheel (hold T for 0.85s, release -> wheel stays open) and click the target mount slot."""
    key = mount_name.lower().strip()
    if key not in MOUNT_WHEEL_SLOTS:
        raise ValueError(f"Unknown mount {mount_name!r}. Valid: {list(MOUNT_WHEEL_SLOTS)}")
    gw = ensure_focus()
    ctl.key_down("t")
    time.sleep(0.85)
    ctl.key_up("t")
    time.sleep(0.35)
    x, y = MOUNT_WHEEL_SLOTS[key]
    ctl.mouse_click_client(x, y, hold=0.08, gw=gw)
    time.sleep(0.95)
    return {"ok": True, "action": "select_mount", "mount": key, "slot": [x, y]}


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
    """Click a waypoint at (map_x, map_y) on the 1920x1080 map, click 传送 (1670, 1000), and wait for HUD.

    Automatically pans the map upward first if ``map_y >= 885`` so the bottom zoom bar never intercepts the click.
    """
    gw = ensure_focus()
    if open_map_first:
        ctl.tap("m", hold=0.06, after=0.85)
    click_x, click_y = float(map_x), float(map_y)
    dragged_dy = 0.0
    if click_y >= 885.0:
        dragged_dy = 600.0 - click_y  # negative -> drag map upward
        ctl.mouse_drag_client(960, 720, 960, 720 + dragged_dy, duration=0.35, steps=16, gw=gw)
        time.sleep(0.45)
        click_y = 600.0
    ctl.mouse_click_client(click_x, click_y, gw=gw)
    time.sleep(0.75)
    ctl.mouse_click_client(1670, 1000, gw=gw)
    time.sleep(1.5)
    res = wait_for_hud(timeout=timeout)
    return {"ok": res["ok"], "target": [map_x, map_y], "clicked": [click_x, click_y], "dragged_dy": dragged_dy, "wait": res}


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


# Calibrated world-overview zone coordinates on 1920x1080 map after clicking 世界地图 (1810, 950)
WORLD_ZONES: Dict[str, tuple[int, int]] = {
    "charlulu": (800, 680),       # 夏露露村
    "sprout_valley": (910, 440),  # 新芽山谷
}


def switch_world_zone(zone: str, open_map_first: bool = True) -> Dict[str, object]:
    """Open map -> click 世界地图 (1810, 950) -> enter target region map."""
    key = zone.lower().strip()
    if key not in WORLD_ZONES:
        raise ValueError(f"Unknown zone {zone!r}. Valid: {list(WORLD_ZONES)}")
    gw = ensure_focus()
    if open_map_first:
        ctl.tap("m", hold=0.06, after=0.85)
    ctl.mouse_click_client(1810, 950, gw=gw)
    time.sleep(0.85)
    zx, zy = WORLD_ZONES[key]
    ctl.mouse_click_client(zx, zy, gw=gw)
    time.sleep(0.9)
    return {"ok": True, "zone": key, "click": [zx, zy]}


def exit_home_ranch(timeout: float = 25.0) -> Dict[str, object]:
    """Exit Home Ranch (星临者的家园) via H -> 确认 (1245, 660) and wait for open-world HUD."""
    gw = ensure_focus()
    img = capture_client(gw=gw)
    if vision.is_confirm_dialog(img):
        ctl.mouse_click_client(1245, 660, gw=gw)
        time.sleep(0.8)
    else:
        ctl.tap("h", hold=0.06, after=0.9)
        chk = capture_client(gw=gw)
        if vision.is_confirm_dialog(chk):
            ctl.mouse_click_client(1245, 660, gw=gw)
            time.sleep(0.8)
    wait_res = wait_for_hud(timeout=timeout, poll=0.8)
    return {"ok": wait_res["ok"], "wait": wait_res}


def home_harvest(return_to_world: bool = True, timeout: float = 25.0) -> Dict[str, object]:
    """Enter Home Ranch (H), one-click harvest all production buildings (G -> 建筑管理 -> 一键收取), and optionally exit.

    Live-verified to collect accumulated 尘石 (Dust Stone), 风铜矿, wood, crops, and all 7 elemental crystals.
    """
    from . import coop

    gw = ensure_focus()
    img = capture_client(gw=gw)
    entered = False
    if not coop.is_home_ranch(img):
        ctl.tap("h", hold=0.06, after=1.1)
        chk = capture_client(gw=gw)
        if vision.is_confirm_dialog(chk):
            ctl.mouse_click_client(1245, 660, gw=gw)
            entered = True
            t0 = time.time()
            while time.time() - t0 < timeout:
                time.sleep(0.8)
                cur = capture_client(gw=gw)
                if coop.is_home_ranch(cur):
                    break
        else:
            return {"ok": False, "error": "home_enter_dialog_not_shown"}

    # Open 家园中枢 (G) -> 建筑管理 (180, 730) -> 一键收取 (105, 895)
    ctl.tap("g", hold=0.06, after=1.1)
    ctl.mouse_click_client(180, 730, gw=gw)
    time.sleep(1.0)
    ctl.mouse_click_client(105, 895, gw=gw)
    time.sleep(1.2)
    # Close 收获物品 popup X (1855, 245) and dismiss any Home Ranch level-up banner (960, 950)
    ctl.mouse_click_client(1855, 245, gw=gw)
    time.sleep(0.8)
    ctl.mouse_click_client(960, 950, gw=gw)
    time.sleep(0.6)
    ctl.mouse_click_client(960, 950, gw=gw)
    time.sleep(0.5)
    # Exit 建筑管理 & 家园中枢
    ctl.tap("esc", hold=0.06, after=0.6)
    ctl.tap("esc", hold=0.06, after=0.7)

    exit_res = exit_home_ranch(timeout=timeout) if return_to_world else None
    return {
        "ok": True if exit_res is None else bool(exit_res["ok"]),
        "entered_home": entered,
        "returned_to_world": return_to_world,
        "exit": exit_res,
    }


def claim_valley_treasure(lv_clicks: int = 2) -> Dict[str, object]:
    """Open M -> 探索度 (1850, 815) -> 山谷秘藏 (1200, 795) -> claim unlocked milestone rewards."""
    gw = ensure_focus()
    ctl.tap("m", hold=0.06, after=1.0)
    ctl.mouse_click_client(1850, 815, gw=gw)
    time.sleep(0.8)
    ctl.mouse_click_client(1200, 795, gw=gw)
    time.sleep(0.8)
    for _ in range(lv_clicks):
        ctl.mouse_click_client(1380, 415, gw=gw)
        time.sleep(0.75)
        ctl.mouse_click_client(960, 920, gw=gw)
        time.sleep(0.45)
    for _ in range(3):
        ctl.tap("esc", hold=0.06, after=0.55)
    return {"ok": True, "panel": "valley_treasure", "clicks": lv_clicks}


# 1920x1080 Y (星之旅录) tab coordinates and bottom milestone orbs (50/120/190/220/260)
JOURNAL_TABS: Dict[str, tuple[int, int]] = {
    "main": (230, 175),       # 星轨旅录
    "home": (230, 290),       # 筑梦据点
    "adventure": (230, 390),  # 大冒险家
    "companion": (230, 485),  # 旅伴手记
    "trial": (230, 575),      # 试炼之路
}
JOURNAL_MILESTONE_X = (590, 780, 975, 1170, 1365)


def claim_journal_rewards(
    tabs: tuple[str, ...] = ("home", "adventure", "companion", "trial"),
    clicks_per_tab: int = 2,
    claim_milestones: bool = True,
) -> Dict[str, object]:
    """Open Y (星之旅录), sweep completed top-row tasks across tabs, and claim unlocked point milestone orbs."""
    gw = ensure_focus()
    ctl.tap("y", hold=0.06, after=1.0)
    visited: List[str] = []
    for name in tabs:
        key = name.lower().strip()
        if key not in JOURNAL_TABS:
            continue
        tx, ty = JOURNAL_TABS[key]
        ctl.mouse_click_client(tx, ty, gw=gw)
        time.sleep(0.65)
        visited.append(key)
        for _ in range(max(1, clicks_per_tab)):
            # Completed tasks automatically sort to Row 1 (680, 195)
            ctl.mouse_click_client(680, 195, gw=gw)
            time.sleep(0.65)
            ctl.mouse_click_client(960, 950, gw=gw)
            time.sleep(0.45)
    if claim_milestones:
        for mx in JOURNAL_MILESTONE_X:
            ctl.mouse_click_client(mx, 955, gw=gw)
            time.sleep(0.55)
            ctl.mouse_click_client(960, 950, gw=gw)
            time.sleep(0.35)
    ctl.tap("esc", hold=0.06, after=0.6)
    return {"ok": True, "panel": "y_star_journal", "tabs": visited, "milestones": claim_milestones}


def claim_event_rewards(journal_clicks: int = 2) -> Dict[str, object]:
    """Open F1 (活动) -> 星临旅记 (165, 890) -> claim available rewards safely (avoids shifting 前往 rows)."""
    gw = ensure_focus()
    ctl.tap("f1", hold=0.06, after=1.1)
    ctl.mouse_click_client(165, 890, gw=gw)
    time.sleep(0.8)
    for _ in range(journal_clicks):
        ctl.mouse_click_client(1430, 680, gw=gw)
        time.sleep(0.7)
        ctl.mouse_click_client(960, 920, gw=gw)
        time.sleep(0.45)
    ctl.tap("esc", hold=0.06, after=0.6)
    return {"ok": True, "panel": "f1_star_journal"}


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


def claim_codex_rewards(clicks: int = 3) -> Dict[str, object]:
    """Open Esc -> 星临图志 (1345, 990) -> 调查奖励 (1835, 995) and claim star-level Codex rewards."""
    gw = ensure_focus()
    ctl.tap("esc", hold=0.06, after=0.9)
    ctl.mouse_click_client(1345, 990, gw=gw)
    time.sleep(1.1)
    for _ in range(max(1, clicks)):
        ctl.mouse_click_client(1835, 995, gw=gw)
        time.sleep(0.8)
        ctl.mouse_click_client(960, 950, gw=gw)
        time.sleep(0.5)
    ctl.mouse_click_client(1865, 50, gw=gw)
    time.sleep(0.6)
    ctl.tap("esc", hold=0.06, after=0.5)
    return {"ok": True, "panel": "codex_investigation", "clicks": clicks}


def build_booster_facility(wait_after: float = 4.2) -> Dict[str, object]:
    """Interact with a nearby Booster Facility (增效设施): handles both white repair modal and side build panel.

    Works both from open-world (taps F first) and when the modal/panel is already open (e.g. after goto-bearing).
    """
    gw = ensure_focus()
    img = capture_client(gw=gw)
    hud = vision.check_hud_state(img)
    if hud["in_world_hud"]:
        ctl.tap("f", hold=0.06, after=0.85)
        img = capture_client(gw=gw)
        hud = vision.check_hud_state(img)

    if vision.is_confirm_dialog(img):
        ctl.mouse_click_client(1245, 660, gw=gw)
        time.sleep(wait_after)
        dismiss_reward(clicks=2, interval=0.5)
        return {"ok": True, "mode": "repair_modal"}
    if not hud["in_world_hud"] and not hud["loading"]:
        # Right-hand 建造增效设施 parchment card: click 建造 (1570, 885), wait for cutscene, dismiss & Esc
        ctl.mouse_click_client(1570, 885, gw=gw)
        time.sleep(wait_after)
        ctl.mouse_click_client(960, 950, gw=gw)
        time.sleep(0.6)
        ctl.tap("esc", hold=0.06, after=0.5)
        return {"ok": True, "mode": "build_panel"}
    return {"ok": False, "mode": "no_dialog", "hud": hud}


def combat_burst(
    duration: float = 6.0,
    switch_chars: bool = True,
    summon_kibo: bool = True,
    early_exit: bool = True,
    abort_fn=None,
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
    aborted = False
    while time.time() < t_end:
        if abort_fn is not None and abort_fn():
            aborted = True
            break
        cycle += 1
        # Basic attack combo (4 rapid LMB clicks)
        for _ in range(4):
            if abort_fn is not None and abort_fn():
                aborted = True
                break
            ctl.mouse_click_here("left", clicks=1, hold=0.05)
            time.sleep(0.16)
            if time.time() >= t_end:
                break
        if aborted or time.time() >= t_end:
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

    # Collect nearby loot drops after combat unless user took over
    if not aborted:
        interact_spam(taps=4, interval=0.18)
    img = capture_client(gw=gw)
    return {"elapsed": round(time.time() - t0, 2), "cycles": cycle, "aborted": aborted, "hud": vision.check_hud_state(img)}


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
