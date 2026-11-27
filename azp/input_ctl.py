"""Hardware scan-code keyboard and mouse SendInput controller for Unity games."""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Iterable, Optional

from .win import GameWindow, find_game_window

user32 = ctypes.windll.user32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800

# Standard US PS/2 Set-1 scan codes (code, is_extended)
SCANCODES = {
    "esc": (0x01, False),
    "1": (0x02, False), "2": (0x03, False), "3": (0x04, False), "4": (0x05, False),
    "5": (0x06, False), "6": (0x07, False), "7": (0x08, False), "8": (0x09, False),
    "9": (0x0A, False), "0": (0x0B, False), "-": (0x0C, False), "=": (0x0D, False),
    "backspace": (0x0E, False), "tab": (0x0F, False),
    "q": (0x10, False), "w": (0x11, False), "e": (0x12, False), "r": (0x13, False),
    "t": (0x14, False), "y": (0x15, False), "u": (0x16, False), "i": (0x17, False),
    "o": (0x18, False), "p": (0x19, False), "[": (0x1A, False), "]": (0x1B, False),
    "enter": (0x1C, False), "ctrl": (0x1D, False), "lctrl": (0x1D, False),
    "a": (0x1E, False), "s": (0x1F, False), "d": (0x20, False), "f": (0x21, False),
    "g": (0x22, False), "h": (0x23, False), "j": (0x24, False), "k": (0x25, False),
    "l": (0x26, False), ";": (0x27, False), "'": (0x28, False), "`": (0x29, False),
    "shift": (0x2A, False), "lshift": (0x2A, False), "\\": (0x2B, False),
    "z": (0x2C, False), "x": (0x2D, False), "c": (0x2E, False), "v": (0x2F, False),
    "b": (0x30, False), "n": (0x31, False), "m": (0x32, False), ",": (0x33, False),
    ".": (0x34, False), "/": (0x35, False), "rshift": (0x36, False),
    "alt": (0x38, False), "lalt": (0x38, False), "space": (0x39, False),
    "caps": (0x3A, False), "capslock": (0x3A, False),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False), "f4": (0x3E, False),
    "f5": (0x3F, False), "f6": (0x40, False), "f7": (0x41, False), "f8": (0x42, False),
    "f9": (0x43, False), "f10": (0x44, False), "f11": (0x57, False), "f12": (0x58, False),
    "up": (0x48, True), "left": (0x4B, True), "right": (0x4D, True), "down": (0x50, True),
}

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


def _send_input(*inputs: INPUT) -> int:
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(INPUT))


def _key_event(key: str, up: bool = False) -> None:
    k = key.lower().strip()
    if k not in SCANCODES:
        raise ValueError(f"Unknown key: {key!r}")
    scan, ext = SCANCODES[k]
    flags = KEYEVENTF_SCANCODE
    if ext:
        flags |= KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= KEYEVENTF_KEYUP
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    _send_input(inp)


def key_down(key: str) -> None:
    _key_event(key, up=False)


def key_up(key: str) -> None:
    _key_event(key, up=True)


def tap(key: str, hold: float = 0.06, after: float = 0.04) -> None:
    key_down(key)
    time.sleep(hold)
    key_up(key)
    if after > 0:
        time.sleep(after)


def hold_keys(keys: Iterable[str], duration: float) -> None:
    keys_list = list(keys)
    try:
        for k in keys_list:
            key_down(k)
        time.sleep(max(0.0, duration))
    finally:
        for k in reversed(keys_list):
            key_up(k)


def release_movement_keys() -> None:
    for k in ("w", "a", "s", "d", "shift", "alt", "space"):
        key_up(k)


def mouse_move_rel(dx: int, dy: int = 0, steps: int = 10, step_delay: float = 0.012) -> None:
    """Smooth relative mouse movement for 3D camera yaw/pitch control."""
    steps = max(1, steps)
    acc_x = 0.0
    acc_y = 0.0
    sent_x = 0
    sent_y = 0
    for i in range(1, steps + 1):
        acc_x = dx * i / steps
        acc_y = dy * i / steps
        ix = int(round(acc_x)) - sent_x
        iy = int(round(acc_y)) - sent_y
        if ix != 0 or iy != 0:
            inp = INPUT(type=INPUT_MOUSE)
            inp.u.mi = MOUSEINPUT(dx=ix, dy=iy, mouseData=0, dwFlags=MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0)
            _send_input(inp)
            sent_x += ix
            sent_y += iy
        if step_delay > 0 and i < steps:
            time.sleep(step_delay)


def mouse_btn(button: str = "left", down: bool = True) -> None:
    b = button.lower()
    flag_map = {
        ("left", True): MOUSEEVENTF_LEFTDOWN,
        ("left", False): MOUSEEVENTF_LEFTUP,
        ("right", True): MOUSEEVENTF_RIGHTDOWN,
        ("right", False): MOUSEEVENTF_RIGHTUP,
        ("middle", True): MOUSEEVENTF_MIDDLEDOWN,
        ("middle", False): MOUSEEVENTF_MIDDLEUP,
    }
    flag = flag_map[(b, down)]
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)
    _send_input(inp)


def mouse_click_here(button: str = "left", clicks: int = 1, hold: float = 0.05, interval: float = 0.08) -> None:
    for i in range(clicks):
        mouse_btn(button, True)
        time.sleep(hold)
        mouse_btn(button, False)
        if i + 1 < clicks:
            time.sleep(interval)


def set_cursor_client(x: float, y: float, gw: Optional[GameWindow] = None, normalized: bool = False) -> None:
    gw = gw or find_game_window()
    sx, sy = gw.client_to_screen(x, y, normalized=normalized)
    user32.SetCursorPos(sx, sy)


def mouse_click_client(
    x: float,
    y: float,
    button: str = "left",
    clicks: int = 1,
    hold: float = 0.05,
    hold_alt: bool = False,
    gw: Optional[GameWindow] = None,
    normalized: bool = False,
) -> None:
    gw = gw or find_game_window()
    if hold_alt:
        key_down("alt")
        time.sleep(0.08)
    try:
        set_cursor_client(x, y, gw=gw, normalized=normalized)
        time.sleep(0.05)
        mouse_click_here(button=button, clicks=clicks, hold=hold)
    finally:
        if hold_alt:
            time.sleep(0.05)
            key_up("alt")


def mouse_drag_client(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    duration: float = 0.35,
    steps: int = 18,
    button: str = "left",
    gw: Optional[GameWindow] = None,
) -> None:
    gw = gw or find_game_window()
    sx1, sy1 = gw.client_to_screen(x1, y1)
    sx2, sy2 = gw.client_to_screen(x2, y2)
    user32.SetCursorPos(sx1, sy1)
    time.sleep(0.05)
    mouse_btn(button, True)
    time.sleep(0.04)
    try:
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(round(sx1 + (sx2 - sx1) * t))
            cy = int(round(sy1 + (sy2 - sy1) * t))
            user32.SetCursorPos(cx, cy)
            time.sleep(duration / steps)
    finally:
        time.sleep(0.04)
        mouse_btn(button, False)


def mouse_scroll(delta: int, x: Optional[float] = None, y: Optional[float] = None, gw: Optional[GameWindow] = None) -> None:
    """Scroll mouse wheel; positive=up/zoom-in, negative=down/zoom-out (in notches)."""
    if x is not None and y is not None:
        set_cursor_client(x, y, gw=gw)
        time.sleep(0.04)
    wheel_amount = int(delta * 120)
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=wintypes.DWORD(wheel_amount & 0xFFFFFFFF),
        dwFlags=MOUSEEVENTF_WHEEL,
        time=0,
        dwExtraInfo=0,
    )
    _send_input(inp)
