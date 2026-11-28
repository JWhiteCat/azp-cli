"""Idle-aware cooperative open-world runner that yields instantly when the user touches physical mouse/keyboard."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import random
import sys
import threading
import time
from typing import Dict, List, Optional

from . import actions
from . import input_ctl as ctl
from . import nav
from . import vision
from .win import capture_client, find_game_window, focus_game, is_focused, user_idle_seconds

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class PhysicalInputMonitor:
    """Read-only low-level hook that distinguishes physical user input from our own SendInput calls."""

    def __init__(self) -> None:
        self.last_physical_ts: float = time.time() - user_idle_seconds()
        self._interrupted = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tid: int = 0
        self._kb_proc = None
        self._ms_proc = None

    def start(self) -> "PhysicalInputMonitor":
        self._thread = threading.Thread(target=self._run, name="azp-phys-input", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.5)
        return self

    def stop(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._tid = 0

    def __enter__(self) -> "PhysicalInputMonitor":
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()

    def _mark_physical(self) -> None:
        self.last_physical_ts = time.time()
        self._interrupted.set()

    def physical_idle_seconds(self) -> float:
        return max(0.0, round(time.time() - self.last_physical_ts, 2))

    def clear_interrupted(self) -> None:
        self._interrupted.clear()

    def is_interrupted(self) -> bool:
        return self._interrupted.is_set()

    def _run(self) -> None:
        self._tid = _kernel32.GetCurrentThreadId()

        def _kb(nCode, wParam, lParam):
            if nCode >= 0 and lParam:
                info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if (info.flags & LLKHF_INJECTED) == 0:
                    self._mark_physical()
            return _user32.CallNextHookEx(0, nCode, wParam, lParam)

        def _ms(nCode, wParam, lParam):
            if nCode >= 0 and lParam:
                info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if (info.flags & LLMHF_INJECTED) == 0:
                    self._mark_physical()
            return _user32.CallNextHookEx(0, nCode, wParam, lParam)

        self._kb_proc = HOOKPROC(_kb)
        self._ms_proc = HOOKPROC(_ms)
        hk_kb = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, 0, 0)
        hk_ms = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, 0, 0)
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if hk_kb:
                _user32.UnhookWindowsHookEx(hk_kb)
            if hk_ms:
                _user32.UnhookWindowsHookEx(hk_ms)


def is_home_ranch(img) -> bool:
    """Detect Home Ranch (星临者的家园): minimap player arrow visible, not loading, no combat skill bar."""
    st = vision.check_hud_state(img)
    if st["loading"] or st["in_world_hud"]:
        return False
    return nav.estimate_player_heading(img) is not None


def is_confirm_dialog(img) -> bool:
    """Re-exported centered white modal confirmation detector from vision.py."""
    return vision.is_confirm_dialog(img)


def wait_until_user_idle(
    monitor: Optional[PhysicalInputMonitor] = None,
    idle_threshold: float = 15.0,
    max_wait: float = 120.0,
    poll: float = 0.5,
) -> bool:
    """Wait passively without touching desktop until physical user input has been idle >= idle_threshold."""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        idle = monitor.physical_idle_seconds() if monitor is not None else user_idle_seconds()
        if idle >= idle_threshold:
            return True
        time.sleep(poll)
    return False


def coop_explore(
    steps: int = 12,
    idle_threshold: float = 15.0,
    max_idle_wait: float = 90.0,
    step_sec: float = 2.4,
    snap_dir: Optional[str] = None,
    auto_exit_home: bool = True,
) -> Dict[str, object]:
    """Explore open world ONLY when user is physically idle; yield within ~30ms if user touches mouse/keys."""
    if snap_dir:
        os.makedirs(snap_dir, exist_ok=True)

    log: List[Dict[str, object]] = []
    yields = 0
    combats = 0
    puzzles_tried = 0
    unstucks = 0
    home_exits = 0

    with PhysicalInputMonitor() as mon:
        active_session = False

        def _is_cloud_active() -> bool:
            mod = sys.modules.get("azp.cloud")
            return bool(mod is not None and mod.get_active_cloud_backend() is not None)

        def _should_abort() -> bool:
            if _is_cloud_active():
                return mon.is_interrupted()
            return mon.is_interrupted() or not is_focused(gw)

        for i in range(steps):
            # 1. Always wait passively until physical user has been idle >= idle_threshold
            if (not active_session) or mon.is_interrupted():
                if active_session and mon.is_interrupted():
                    ctl.release_movement_keys()
                    yields += 1
                active_session = False
                if mon.physical_idle_seconds() < idle_threshold:
                    if not wait_until_user_idle(monitor=mon, idle_threshold=idle_threshold, max_wait=max_idle_wait):
                        log.append({"step": i + 1, "action": "stopped_user_active", "idle_sec": mon.physical_idle_seconds()})
                        break
                mon.clear_interrupted()
                active_session = True

            gw = find_game_window()
            if not is_focused(gw):
                if mon.is_interrupted() or mon.physical_idle_seconds() < idle_threshold:
                    active_session = False
                    yields += 1
                    continue
                focus_game(gw)

            if _should_abort():
                ctl.release_movement_keys()
                active_session = False
                yields += 1
                log.append({"step": i + 1, "action": "yielded_before_step"})
                continue

            prev_img = capture_client(gw=gw)
            st = vision.check_hud_state(prev_img)
            entry: Dict[str, object] = {"step": i + 1, "hud_before": st}

            if st["loading"]:
                time.sleep(1.5)
                entry["action"] = "wait_loading"
                log.append(entry)
                continue

            if not st["in_world_hud"]:
                if auto_exit_home and (is_confirm_dialog(prev_img) or is_home_ranch(prev_img)):
                    exit_res = actions.exit_home_ranch(timeout=18.0)
                    home_exits += 1
                    entry["action"] = "exit_home_ranch"
                    entry["result"] = exit_res
                    log.append(entry)
                    continue
                entry["action"] = "paused_non_hud"
                log.append(entry)
                time.sleep(1.0)
                continue

            if st["in_combat"]:
                res = actions.combat_burst(duration=7.0, switch_chars=True, abort_fn=_should_abort)
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
                    res = nav.approach_poi(
                        target_bearing=pois[0]["bearing_deg"],
                        walk_sec=step_sec,
                        sprint=True,
                        abort_fn=_should_abort,
                    )
                    entry["action"] = "approach_poi"
                    entry["target_poi"] = pois[0]
                    entry["aborted"] = res.get("aborted", False)
                else:
                    completed = actions.walk(
                        "w",
                        duration=step_sec,
                        sprint=True,
                        jump=(i % 3 == 2),
                        spam_f=True,
                        abort_fn=_should_abort,
                    )
                    entry["action"] = "walk_collect"
                    entry["aborted"] = not completed

            # Immediately yield if physical user touched mouse/keys or switched focus
            if _should_abort():
                ctl.release_movement_keys()
                active_session = False
                yields += 1
                entry["yielded_to_user"] = True
                log.append(entry)
                continue

            curr_img = capture_client(gw=gw)
            mm_diff = vision.frame_diff(prev_img, curr_img, roi=(65, 45, 220, 198))
            entry["minimap_diff"] = round(mm_diff, 2)

            if entry["action"] in ("walk_collect", "approach_poi") and mm_diff < 3.2:
                unstucks += 1
                entry["unstuck"] = True
                if actions.walk("s", duration=0.6, sprint=False, abort_fn=_should_abort):
                    if not _should_abort():
                        actions.turn_camera(random.choice([-520, 520, -760, 760]), 0, steps=10)
                    actions.walk("w", duration=1.1, sprint=False, jump=True, spam_f=True, abort_fn=_should_abort)
                if not _should_abort():
                    curr_img = capture_client(gw=gw)

            if snap_dir and not _should_abort() and (i == 0 or (i + 1) % 3 == 0 or i == steps - 1):
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
        "home_exits": home_exits,
        "yields": yields,
        "log": log,
    }
