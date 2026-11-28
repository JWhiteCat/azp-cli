"""Window discovery, focus management, and screenshot capture for AzurPromilia."""
from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import win32con
import win32gui
import win32process
import win32ui
from PIL import Image, ImageDraw, ImageGrab

# Ensure physical pixel coordinates match real display coordinates under DPI scaling.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass
class GameWindow:
    hwnd: int
    pid: int
    title: str
    window_rect: Tuple[int, int, int, int]
    client_rect: Tuple[int, int, int, int]  # (left, top, right, bottom) in screen px

    @property
    def width(self) -> int:
        return self.client_rect[2] - self.client_rect[0]

    @property
    def height(self) -> int:
        return self.client_rect[3] - self.client_rect[1]

    def client_to_screen(self, x: float, y: float, normalized: bool = False) -> Tuple[int, int]:
        if normalized:
            sx = int(round(self.client_rect[0] + x * self.width))
            sy = int(round(self.client_rect[1] + y * self.height))
        else:
            sx = int(round(self.client_rect[0] + x))
            sy = int(round(self.client_rect[1] + y))
        return sx, sy


def find_game_window(title_substr: str = "AzurPromilia", class_name: str = "UnityWndClass") -> GameWindow:
    matches = []
    iconic_matches = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if title_substr.lower() in title.lower() and (not class_name or cls == class_name):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            wrect = win32gui.GetWindowRect(hwnd)
            cl = win32gui.GetClientRect(hwnd)
            if cl[2] > 100 and cl[3] > 100:
                p0 = win32gui.ClientToScreen(hwnd, (0, 0))
                p1 = win32gui.ClientToScreen(hwnd, (cl[2], cl[3]))
                matches.append(
                    GameWindow(
                        hwnd=hwnd,
                        pid=pid,
                        title=title,
                        window_rect=wrect,
                        client_rect=(p0[0], p0[1], p1[0], p1[1]),
                    )
                )
            elif win32gui.IsIconic(hwnd):
                iconic_matches.append(
                    GameWindow(
                        hwnd=hwnd,
                        pid=pid,
                        title=title,
                        window_rect=wrect,
                        client_rect=(0, 0, 0, 0),
                    )
                )

    win32gui.EnumWindows(_cb, None)
    if matches:
        return matches[0]
    if iconic_matches:
        return iconic_matches[0]
    raise RuntimeError(f"Game window not found (title~={title_substr!r}, class={class_name!r}).")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def check_input_privilege(gw: Optional[GameWindow] = None) -> dict:
    """Check whether current process can send SendInput events to the game window (UIPI check)."""
    gw = gw or find_game_window()
    self_admin = is_admin()
    # PROCESS_QUERY_INFORMATION (0x0400) fails with ERROR_ACCESS_DENIED (5) across UIPI if target is elevated.
    h = ctypes.windll.kernel32.OpenProcess(0x0400, False, gw.pid)
    can_access = bool(h)
    err = 0 if can_access else ctypes.GetLastError()
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
    return {
        "self_admin": self_admin,
        "can_send_input": can_access or self_admin,
        "open_process_err": err,
    }


def is_focused(gw: Optional[GameWindow] = None) -> bool:
    gw = gw or find_game_window()
    return win32gui.GetForegroundWindow() == gw.hwnd


def user_idle_seconds() -> float:
    """Seconds since the last physical mouse/keyboard input on the desktop."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    tick = ctypes.windll.kernel32.GetTickCount()
    elapsed_ms = (tick - lii.dwTime) & 0xFFFFFFFF
    return round(elapsed_ms / 1000.0, 2)


def capture_background(
    gw: Optional[GameWindow] = None,
    scale: float = 1.0,
    save_path: Optional[str] = None,
) -> Image.Image:
    """Capture AzurPromilia even when covered by other windows via PrintWindow(PW_RENDERFULLCONTENT=2)."""
    gw = gw or find_game_window()
    wl, wt, wr, wb = gw.window_rect
    ww, wh = wr - wl, wb - wt
    hwnd_dc = win32gui.GetWindowDC(gw.hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, ww, wh)
    save_dc.SelectObject(bmp)
    try:
        ctypes.windll.user32.PrintWindow(gw.hwnd, save_dc.GetSafeHdc(), 2)
        raw = bmp.GetBitmapBits(True)
        full = Image.frombuffer("RGB", (ww, wh), raw, "raw", "BGRX", 0, 1)
        # Crop strictly to client area (1920x1080)
        cl, ct, cr, cb = gw.client_rect
        img = full.crop((cl - wl, ct - wt, cr - wl, cb - wt))
    finally:
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(gw.hwnd, hwnd_dc)
        win32gui.DeleteObject(bmp.GetHandle())

    if scale != 1.0:
        img = img.resize((max(1, int(round(img.width * scale))), max(1, int(round(img.height * scale)))), Image.Resampling.BILINEAR)
    if save_path:
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        img.save(save_path)
    return img


def focus_game(gw: Optional[GameWindow] = None, wait: float = 0.2) -> GameWindow:
    gw = gw or find_game_window()
    if win32gui.IsIconic(gw.hwnd) or gw.width < 100 or gw.height < 100:
        win32gui.ShowWindow(gw.hwnd, win32con.SW_RESTORE)
        time.sleep(0.35)
        gw = find_game_window()
    if win32gui.GetForegroundWindow() != gw.hwnd:
        # Press/release Alt briefly to satisfy Windows foreground lock if needed
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        try:
            win32gui.SetForegroundWindow(gw.hwnd)
        except Exception:
            win32gui.BringWindowToTop(gw.hwnd)
        time.sleep(wait)
    return find_game_window()


def capture_client(
    gw: Optional[GameWindow] = None,
    roi: Optional[Tuple[int, int, int, int]] = None,
    scale: float = 1.0,
    grid: bool = False,
    grid_step: int = 160,
    save_path: Optional[str] = None,
) -> Image.Image:
    gw = gw or find_game_window()
    l, t, r, b = gw.client_rect
    if roi is not None:
        rx1, ry1, rx2, ry2 = roi
        bbox = (l + rx1, t + ry1, l + rx2, t + ry2)
    else:
        bbox = (l, t, r, b)
    img = ImageGrab.grab(bbox=bbox, all_screens=True)

    if grid:
        draw = ImageDraw.Draw(img)
        w, h = img.size
        ox = roi[0] if roi else 0
        oy = roi[1] if roi else 0
        for x in range(0, w, grid_step):
            draw.line([(x, 0), (x, h)], fill=(255, 255, 0), width=1)
            draw.text((x + 3, 4), str(ox + x), fill=(255, 255, 0))
        for y in range(0, h, grid_step):
            draw.line([(0, y), (w, y)], fill=(255, 255, 0), width=1)
            draw.text((4, y + 3), str(oy + y), fill=(255, 255, 0))

    if scale != 1.0:
        nw = max(1, int(round(img.width * scale)))
        nh = max(1, int(round(img.height * scale)))
        img = img.resize((nw, nh), Image.Resampling.BILINEAR)

    if save_path:
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        img.save(save_path)
    return img
