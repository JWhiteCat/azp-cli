"""Reusable computer-vision solvers for AzurPromilia open-world minigames."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import numpy as np
import win32con
import win32gui
import win32ui

from . import input_ctl as ctl
from . import vision
from .win import capture_client, focus_game


class FastROIGrabber:
    """Ultra-fast (~3-5ms) Win32 GDI BitBlt screen ROI grabber for real-time CV minigames."""

    def __init__(self, left: int, top: int, width: int, height: int) -> None:
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self._hdesktop = win32gui.GetDesktopWindow()
        self._desktop_dc = win32gui.GetWindowDC(self._hdesktop)
        self._img_dc = win32ui.CreateDCFromHandle(self._desktop_dc)
        self._mem_dc = self._img_dc.CreateCompatibleDC()
        self._bmp = win32ui.CreateBitmap()
        self._bmp.CreateCompatibleBitmap(self._img_dc, width, height)
        self._mem_dc.SelectObject(self._bmp)

    def grab_rgb(self) -> np.ndarray:
        self._mem_dc.BitBlt(
            (0, 0), (self.width, self.height), self._img_dc, (self.left, self.top), win32con.SRCCOPY
        )
        raw = self._bmp.GetBitmapBits(True)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 4))
        # BGRA -> RGB view
        return arr[:, :, 2::-1]

    def close(self) -> None:
        try:
            self._mem_dc.DeleteDC()
            self._img_dc.DeleteDC()
            win32gui.ReleaseDC(self._hdesktop, self._desktop_dc)
            win32gui.DeleteObject(self._bmp.GetHandle())
        except Exception:
            pass

    def __enter__(self) -> "FastROIGrabber":
        return self

    def __exit__(self, *args) -> None:
        self.close()


def _bird_x_from_rgb(arr: np.ndarray, x_offset: int) -> Optional[float]:
    """Detect X center of the bright golden-winged carrying bird in RGB ROI."""
    a = arr.astype(np.int16)
    gold = (a[:, :, 0] > 220) & (a[:, :, 1] > 165) & (a[:, :, 2] < 125) & ((a[:, :, 0] - a[:, :, 2]) > 105)
    ys, xs = np.where(gold)
    if len(xs) < 25:
        return None
    return float(x_offset + np.median(xs))


def solve_zuzanka_ladder(
    target_x: float = 960.0,
    tolerance_px: float = 12.0,
    lead_sec: float = 0.23,
    max_drops: int = 12,
    settle_sec: float = 2.05,
    timeout_per_drop: float = 10.0,
) -> Dict[str, object]:
    """Real-time (~100 Hz BitBlt) CV solver for 搭建祖赞卡之梯 (Zuzanka Ladder totem stacking).

    Tracks the carrying bird's golden wings across y=70..330, x=420..1500, predicts
    its crossing over target_x (default 960px center) using velocity + lead_sec,
    and fires F to drop each totem cleanly onto the stack.
    """
    gw = focus_game()
    l, t, _, _ = gw.client_rect
    x1, y1, x2, y2 = 420, 70, 1500, 330
    drops: List[Dict[str, object]] = []

    with FastROIGrabber(l + x1, t + y1, x2 - x1, y2 - y1) as grabber:
        for drop_idx in range(1, max_drops + 1):
            full = capture_client(gw=gw)
            hud = vision.check_hud_state(full)
            if hud["in_world_hud"]:
                break

            t_start = time.time()
            prev_x: Optional[float] = None
            prev_t: Optional[float] = None
            smooth_vx = 0.0
            has_vx = False
            dropped = False

            while time.time() - t_start < timeout_per_drop:
                now = time.time()
                rgb = grabber.grab_rgb()
                bx = _bird_x_from_rgb(rgb, x1)
                if bx is None:
                    if now - t_start > 2.5:
                        break
                    time.sleep(0.015)
                    continue

                if prev_x is not None and prev_t is not None:
                    dt = now - prev_t
                    if dt >= 0.004:
                        inst_vx = (bx - prev_x) / dt
                        if abs(inst_vx) < 2200:
                            smooth_vx = inst_vx if not has_vx else (0.55 * smooth_vx + 0.45 * inst_vx)
                            has_vx = True

                # Require at least 3 samples and stable velocity before dropping
                if has_vx and (now - t_start) > 0.15:
                    pred_x = bx + smooth_vx * lead_sec
                    if abs(pred_x - target_x) <= tolerance_px:
                        ctl.tap("f", hold=0.03, after=0.0)
                        drops.append(
                            {
                                "drop": drop_idx,
                                "raw_x": round(bx, 1),
                                "pred_x": round(pred_x, 1),
                                "vx": round(smooth_vx, 1),
                            }
                        )
                        dropped = True
                        break

                prev_x = bx
                prev_t = now
                time.sleep(0.005)

            if not dropped:
                break
            time.sleep(settle_sec)

    time.sleep(1.5)
    return {"ok": True, "drops_count": len(drops), "drops": drops}
