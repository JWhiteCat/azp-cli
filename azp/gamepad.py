"""Virtual Xbox 360 controller via ViGEmBus (ViGEmClient.dll) for keyboard/mouse-free control."""
from __future__ import annotations

import ctypes
import os
import time
from typing import Dict, Optional

DEFAULT_DLL_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ViGEmClient.dll"),
    r"D:\GameScripts\MaaEnd\maafw\ViGEmClient.dll",
    r"C:\Windows\System32\ViGEmClient.dll",
)

XUSB_BUTTONS: Dict[str, int] = {
    "up": 0x0001,
    "down": 0x0002,
    "left": 0x0004,
    "right": 0x0008,
    "start": 0x0010,
    "back": 0x0020,
    "ls": 0x0040,
    "rs": 0x0080,
    "lb": 0x0100,
    "rb": 0x0200,
    "guide": 0x0400,
    "a": 0x1000,
    "b": 0x2000,
    "x": 0x4000,
    "y": 0x8000,
}


class XUSB_REPORT(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


def find_vigem_dll(custom_path: Optional[str] = None) -> str:
    if custom_path and os.path.isfile(custom_path):
        return custom_path
    for p in DEFAULT_DLL_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "ViGEmClient.dll not found. Place ViGEmClient.dll in the azp project root or install vgamepad."
    )


class VirtualX360:
    """Context-managed virtual Xbox 360 controller backed by Nefarius ViGEmBus."""

    def __init__(self, dll_path: Optional[str] = None) -> None:
        self.dll_path = find_vigem_dll(dll_path)
        self._dll = ctypes.CDLL(self.dll_path)
        self._setup_signatures()
        self._client = None
        self._target = None
        self._report = XUSB_REPORT()

    def _setup_signatures(self) -> None:
        d = self._dll
        d.vigem_alloc.restype = ctypes.c_void_p
        d.vigem_connect.argtypes = [ctypes.c_void_p]
        d.vigem_connect.restype = ctypes.c_uint32
        d.vigem_disconnect.argtypes = [ctypes.c_void_p]
        d.vigem_free.argtypes = [ctypes.c_void_p]
        d.vigem_target_x360_alloc.restype = ctypes.c_void_p
        d.vigem_target_add.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.vigem_target_add.restype = ctypes.c_uint32
        d.vigem_target_remove.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.vigem_target_free.argtypes = [ctypes.c_void_p]
        d.vigem_target_x360_update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, XUSB_REPORT]
        d.vigem_target_x360_update.restype = ctypes.c_uint32

    def connect(self) -> None:
        self._client = self._dll.vigem_alloc()
        rc = self._dll.vigem_connect(self._client)
        if rc != 0x20000000:
            raise RuntimeError(f"vigem_connect failed: {hex(rc)}")
        self._target = self._dll.vigem_target_x360_alloc()
        ra = self._dll.vigem_target_add(self._client, self._target)
        if ra != 0x20000000:
            raise RuntimeError(f"vigem_target_add failed: {hex(ra)}")
        time.sleep(0.35)

    def close(self) -> None:
        if self._client and self._target:
            try:
                self.reset()
                self._dll.vigem_target_remove(self._client, self._target)
                self._dll.vigem_target_free(self._target)
            except Exception:
                pass
            self._target = None
        if self._client:
            try:
                self._dll.vigem_disconnect(self._client)
                self._dll.vigem_free(self._client)
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "VirtualX360":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _flush(self) -> None:
        self._dll.vigem_target_x360_update(self._client, self._target, self._report)

    def reset(self) -> None:
        self._report = XUSB_REPORT()
        self._flush()

    def press(self, button: str, hold: float = 0.10, after: float = 0.12) -> None:
        mask = XUSB_BUTTONS[button.lower()]
        self._report.wButtons |= mask
        self._flush()
        time.sleep(hold)
        self._report.wButtons &= ~mask
        self._flush()
        if after > 0:
            time.sleep(after)

    def left_stick(self, x: float, y: float, duration: float = 0.5) -> None:
        """x, y in [-1.0, 1.0]."""
        self._report.sThumbLX = int(max(-32768, min(32767, round(x * 32767))))
        self._report.sThumbLY = int(max(-32768, min(32767, round(y * 32767))))
        self._flush()
        time.sleep(duration)
        self._report.sThumbLX = 0
        self._report.sThumbLY = 0
        self._flush()

    def right_stick(self, x: float, y: float, duration: float = 0.4) -> None:
        """x, y in [-1.0, 1.0] for 3D camera rotation."""
        self._report.sThumbRX = int(max(-32768, min(32767, round(x * 32767))))
        self._report.sThumbRY = int(max(-32768, min(32767, round(y * 32767))))
        self._flush()
        time.sleep(duration)
        self._report.sThumbRX = 0
        self._report.sThumbRY = 0
        self._flush()
