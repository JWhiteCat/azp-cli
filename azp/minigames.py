"""Reusable computer-vision solvers for AzurPromilia open-world minigames."""
from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import ndimage
import win32con
import win32gui
import win32ui

from . import detector as det_mod
from . import input_ctl as ctl
from . import vision
from .win import capture_client, focus_game


class FastROIGrabber:
    """Ultra-fast (~3-5ms) Win32 GDI BitBlt screen ROI grabber (or background PrintWindow on CloudShell)."""

    def __init__(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        *,
        logical_roi: Optional[Tuple[int, int, int, int]] = None,
        gw=None,
    ) -> None:
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self._logical_roi = logical_roi
        self._gw = gw

        cloud_mod = sys.modules.get("azp.cloud")
        self._cloud = cloud_mod.get_active_cloud_backend() if cloud_mod is not None else None
        if self._cloud is not None:
            self._hdesktop = None
            self._desktop_dc = None
            self._img_dc = None
            self._mem_dc = None
            self._bmp = None
            return

        self._hdesktop = win32gui.GetDesktopWindow()
        self._desktop_dc = win32gui.GetWindowDC(self._hdesktop)
        self._img_dc = win32ui.CreateDCFromHandle(self._desktop_dc)
        self._mem_dc = self._img_dc.CreateCompatibleDC()
        self._bmp = win32ui.CreateBitmap()
        self._bmp.CreateCompatibleBitmap(self._img_dc, width, height)
        self._mem_dc.SelectObject(self._bmp)

    def grab_rgb(self) -> np.ndarray:
        if self._cloud is not None:
            img = self._cloud.capture(gw=self._gw)
            arr = vision._norm_arr(img)
            if self._logical_roi is not None:
                x1, y1, x2, y2 = self._logical_roi
            else:
                gw = self._gw or self._cloud.find_window()
                x1 = max(0, self.left - gw.client_rect[0])
                y1 = max(0, self.top - gw.client_rect[1])
                x2 = min(1920, x1 + self.width)
                y2 = min(1080, y1 + self.height)
            return arr[y1:y2, x1:x2]
        self._mem_dc.BitBlt(
            (0, 0), (self.width, self.height), self._img_dc, (self.left, self.top), win32con.SRCCOPY
        )
        raw = self._bmp.GetBitmapBits(True)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 4))
        # BGRA -> RGB view
        return arr[:, :, 2::-1]

    def close(self) -> None:
        if self._cloud is not None:
            return
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

    with FastROIGrabber(l + x1, t + y1, x2 - x1, y2 - y1, logical_roi=(x1, y1, x2, y2), gw=gw) as grabber:
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


def find_race_waypoint(
    rgb: np.ndarray,
    x_offset: int = 80,
    y_offset: int = 130,
    yolo: Optional[det_mod.YoloOnnxDetector] = None,
) -> Optional[Tuple[float, float, int]]:
    """Detect the next waypoint ring in 坐骑挑战 (ONNX YOLO if loaded, else anti-crystal geometric CV)."""
    if yolo is not None:
        dets, _ = yolo.detect_rgb(rgb, classes=("race_ring",))
        if dets:
            d = dets[0]
            return (x_offset + d.cx, y_offset + d.cy, int(d.w * d.h))
        return None
    dets = det_mod.detect_race_rings_cv(rgb, roi=(0, 0, rgb.shape[1], rgb.shape[0]))
    if not dets:
        return None
    d = dets[0]
    return (x_offset + d.cx, y_offset + d.cy, int(d.w * d.h))


def solve_mount_race(
    max_sec: float = 65.0,
    gain: float = 0.28,
    restart: bool = False,
    sprint: bool = True,
    model_path: Optional[str] = None,
) -> Dict[str, object]:
    """Real-time (~25 Hz BitBlt) steering solver for 坐骑挑战 (ONNX YOLO or anti-crystal CV)."""
    gw = focus_game()
    yolo: Optional[det_mod.YoloOnnxDetector] = None
    if model_path:
        yolo = det_mod.YoloOnnxDetector(model_path=model_path)
    elif det_mod.is_onnxruntime_available() and os.path.isfile(det_mod.DEFAULT_MODEL_PATH):
        yolo = det_mod.YoloOnnxDetector(model_path=det_mod.DEFAULT_MODEL_PATH)

    if restart:
        ctl.tap("p", hold=0.06, after=0.3)
        time.sleep(5.5)

    l, t, _, _ = gw.client_rect
    x1, y1, x2, y2 = 80, 130, 1840, 780
    t0 = time.time()
    frames = 0
    seen_frames = 0
    missed_streak = 0
    last_err = 0.0
    next_dash = t0 + 1.2
    next_hud_chk = t0 + 2.5

    with FastROIGrabber(l + x1, t + y1, x2 - x1, y2 - y1, logical_roi=(x1, y1, x2, y2), gw=gw) as grabber:
        ctl.key_down("w")
        if sprint:
            time.sleep(0.04)
            ctl.tap("shift", hold=0.05, after=0.02)
            ctl.key_down("shift")
        try:
            while time.time() - t0 < max_sec:
                now = time.time()
                rgb = grabber.grab_rgb()
                frames += 1
                wp = find_race_waypoint(rgb, x1, y1, yolo=yolo)
                if wp is not None:
                    seen_frames += 1
                    missed_streak = 0
                    cx, cy, _ = wp
                    err_x = cx - 960.0
                    last_err = err_x
                    if abs(err_x) > 18.0:
                        step_dx = int(max(-260.0, min(260.0, err_x * gain)))
                        ctl.mouse_move_rel(step_dx, 0, steps=1, step_delay=0.0)
                    if abs(err_x) < 150.0 and now >= next_dash:
                        ctl.tap("e", hold=0.04, after=0.0)
                        next_dash = now + 3.8
                else:
                    missed_streak += 1
                    # If target orb is behind us (marker briefly vanished), sweep in last known direction
                    if 6 <= missed_streak <= 45:
                        sweep = 140 if last_err >= 0 else -140
                        ctl.mouse_move_rel(sweep, 0, steps=1, step_delay=0.0)
                    elif missed_streak > 65:
                        break

                if now >= next_hud_chk:
                    full = capture_client(gw=gw)
                    if not vision.check_hud_state(full)["in_world_hud"]:
                        break
                    next_hud_chk = now + 1.8

                time.sleep(0.032)
        finally:
            if sprint:
                ctl.key_up("shift")
            ctl.key_up("w")
            ctl.release_movement_keys()

    elapsed = round(time.time() - t0, 2)
    return {
        "ok": True,
        "detector": "onnx_yolo" if yolo is not None else "cv_race_v2",
        "elapsed": elapsed,
        "frames": frames,
        "seen_frames": seen_frames,
        "missed_streak": missed_streak,
    }


def solve_cabbage_run(
    max_sec: float = 45.0,
    gain: float = 0.25,
    model_path: Optional[str] = None,
) -> Dict[str, object]:
    """Real-time (~25 Hz BitBlt) seed-chasing solver for 变身菜鸡 (ONNX YOLO or lime-glow CV)."""
    gw = focus_game()
    yolo: Optional[det_mod.YoloOnnxDetector] = None
    if model_path:
        yolo = det_mod.YoloOnnxDetector(model_path=model_path)
    elif det_mod.is_onnxruntime_available() and os.path.isfile(det_mod.DEFAULT_MODEL_PATH):
        yolo = det_mod.YoloOnnxDetector(model_path=det_mod.DEFAULT_MODEL_PATH)

    l, t, _, _ = gw.client_rect
    x1, y1, x2, y2 = 140, 140, 1780, 880
    t0 = time.time()
    frames = 0
    seen_frames = 0
    missed_streak = 0
    last_err = 0.0
    next_hop = t0 + 0.9
    next_boost = t0 + 1.4

    with FastROIGrabber(l + x1, t + y1, x2 - x1, y2 - y1, logical_roi=(x1, y1, x2, y2), gw=gw) as grabber:
        ctl.key_down("w")
        try:
            while time.time() - t0 < max_sec:
                rgb = grabber.grab_rgb()
                frames += 1
                if yolo is not None:
                    dets, _ = yolo.detect_rgb(rgb, classes=("cabbage_seed",))
                else:
                    dets = det_mod.detect_cabbage_seeds_cv(rgb, roi=(0, 0, rgb.shape[1], rgb.shape[0]))
                if dets:
                    seen_frames += 1
                    missed_streak = 0
                    # Prefer lower/ground-reachable seeds near horizontal center (avoid getting stuck staring at treetop seeds)
                    best = max(
                        dets,
                        key=lambda d: (y1 + d.cy) - 0.35 * abs((x1 + d.cx) - 960.0),
                    )
                    cx = x1 + best.cx
                    cy = y1 + best.cy
                    err_x = cx - 960.0
                    last_err = err_x
                    if abs(err_x) > 18.0:
                        step_dx = int(max(-240.0, min(240.0, err_x * gain)))
                        steps = max(1, abs(step_dx) // 34)
                        ctl.mouse_move_rel(step_dx, 0, steps=steps, step_delay=0.002)
                    now = time.time()
                    # 3D altitude control for helicopter cabbage chicken (Space=ascend, Ctrl=descend)
                    if cy < 470.0 and now >= next_hop:
                        ctl.tap("space", hold=0.18, after=0.0)
                        next_hop = now + 0.30
                    elif cy > 640.0 and now >= next_hop:
                        ctl.tap("ctrl", hold=0.18, after=0.0)
                        next_hop = now + 0.30
                    if now >= next_boost:
                        ctl.tap("r", hold=0.05, after=0.0)
                        next_boost = now + 2.4
                else:
                    missed_streak += 1
                    now = time.time()
                    if now >= next_boost:
                        ctl.tap("r", hold=0.05, after=0.0)
                        next_boost = now + 2.4
                    if 6 <= missed_streak <= 65:
                        sweep = 160 if last_err >= 0 else -160
                        ctl.mouse_move_rel(sweep, 0, steps=5, step_delay=0.002)
                    elif missed_streak > 85:
                        break
                time.sleep(0.032)
        finally:
            ctl.key_up("w")
            ctl.release_movement_keys()

    return {
        "ok": True,
        "detector": "onnx_yolo" if yolo is not None else "cv_cabbage_badge",
        "elapsed": round(time.time() - t0, 2),
        "frames": frames,
        "seen_frames": seen_frames,
    }
