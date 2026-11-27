"""MuMu Player 12 / NX (v5/v6) backend with 截图增强 (IPC screencap) & 触控增强 (IPC multi-touch/key).

Architecture modeled after MaaAssistantArknights (MumuExtras.cpp / MumuController.cpp / AdbController.cpp)
and maa-cli:
- Zero-focus-hijack background screencap via `nemu_capture_display` (~10ms RGBA raw buffer).
- Zero-focus-hijack background multi-touch & key input via `nemu_input_event_finger_touch_down/up`,
  `nemu_input_event_key_down/up`, and `nemu_input_text` (gated on MuMuManager version >= 6.3.2.0).
- Automatic instance discovery (`MuMuManager.exe info -v all`), MAA port/emulator-xxxx index parsing,
  target package display binding (`com.manjuu.promilia`), and safe ADB fallback.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


@contextmanager
def _silence_c_stdout() -> Iterator[None]:
    """Suppress noisy C-level stdout from external_renderer_ipc.dll so CLI output stays pure JSON."""
    devnull_fd = None
    saved_fd = None
    try:
        sys_stdout_fd = 1
        saved_fd = os.dup(sys_stdout_fd)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, sys_stdout_fd)
        yield
    except Exception:
        yield
    finally:
        if saved_fd is not None:
            try:
                os.dup2(saved_fd, 1)
            except Exception:
                pass
            try:
                os.close(saved_fd)
            except Exception:
                pass
        if devnull_fd is not None:
            try:
                os.close(devnull_fd)
            except Exception:
                pass

from PIL import Image, ImageDraw

DEFAULT_PACKAGE = "com.manjuu.promilia"
MIN_INPUT_VERSION: Tuple[int, int, int, int] = (6, 3, 2, 0)
TOUCH_HOLD_SEC = 0.035
SWIPE_INTERVAL_SEC = 0.010

# Candidate relative paths ordered newest -> oldest (matching MAA MumuExtras.cpp)
DLL_CANDIDATE_RELPATHS: Tuple[str, ...] = (
    os.path.join("nx_device", "15.0", "shell", "sdk", "external_renderer_ipc.dll"),
    os.path.join("nx_device", "12.0", "shell", "sdk", "external_renderer_ipc.dll"),
    os.path.join("nx_main", "sdk", "external_renderer_ipc.dll"),
    os.path.join("shell", "sdk", "external_renderer_ipc.dll"),
)

MANAGER_CANDIDATE_RELPATHS: Tuple[str, ...] = (
    os.path.join("nx_main", "MuMuManager.exe"),
    os.path.join("shell", "MuMuManager.exe"),
)

ADB_CANDIDATE_RELPATHS: Tuple[str, ...] = (
    os.path.join("nx_main", "adb.exe"),
    os.path.join("shell", "adb.exe"),
)

# Android KeyEvent -> Linux input-event-codes.h (ported from MAA MumuExtras.cpp)
ANDROID_TO_LINUX_KEYCODE: Dict[int, int] = {
    # Letters
    29: 30, 30: 48, 31: 46, 32: 32, 33: 18, 34: 33, 35: 34, 36: 35, 37: 23,
    38: 36, 39: 37, 40: 38, 41: 50, 42: 49, 43: 24, 44: 25, 45: 16, 46: 19,
    47: 31, 48: 20, 49: 22, 50: 47, 51: 17, 52: 45, 53: 21, 54: 44,
    # Digits 0..9
    7: 11, 8: 2, 9: 3, 10: 4, 11: 5, 12: 6, 13: 7, 14: 8, 15: 9, 16: 10,
    # Function keys F1..F12
    131: 59, 132: 60, 133: 61, 134: 62, 135: 63, 136: 64,
    137: 65, 138: 66, 139: 67, 140: 68, 141: 87, 142: 88,
    # Navigation & common keys
    19: 103, 20: 108, 21: 105, 22: 106, 23: 28,
    62: 57, 66: 28, 67: 14, 61: 15, 111: 1,
    # Modifiers
    59: 42, 60: 54, 113: 29, 114: 97, 57: 56, 58: 100, 115: 58, 117: 125, 118: 126,
    # Symbols & Android system keys
    76: 53, 122: 102, 123: 107, 124: 110, 92: 104, 93: 109, 112: 111,
    4: 158, 3: 102, 82: 139, 84: 217, 24: 115, 25: 114, 164: 113,
}

# Friendly key name -> Android KeyEvent code
KEY_NAME_TO_ANDROID: Dict[str, int] = {
    **{chr(ord("a") + i): 29 + i for i in range(26)},
    **{str(i): 7 + i for i in range(10)},
    **{f"f{i}": 130 + i for i in range(1, 13)},
    "up": 19,
    "down": 20,
    "left": 21,
    "right": 22,
    "space": 62,
    "enter": 66,
    "return": 66,
    "backspace": 67,
    "del": 67,
    "tab": 61,
    "esc": 111,
    "escape": 111,
    "shift": 59,
    "lshift": 59,
    "rshift": 60,
    "ctrl": 113,
    "lctrl": 113,
    "rctrl": 114,
    "alt": 57,
    "lalt": 57,
    "ralt": 58,
    "back": 4,
    "home": 3,
    "menu": 82,
    "volume_up": 24,
    "volume_down": 25,
    "mute": 164,
}


def parse_version_tuple(version_str: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse dotted MuMu version string (e.g. '6.8.8.0' or '6.4') into a 4-int tuple."""
    if not version_str:
        return None
    parts = version_str.strip().split(".")
    vals: List[int] = []
    for p in parts[:4]:
        if not p.isdigit():
            return None
        vals.append(int(p))
    while len(vals) < 4:
        vals.append(0)
    return (vals[0], vals[1], vals[2], vals[3])


def is_input_version_supported(version_str: str) -> bool:
    """Return True iff MuMuManager version >= 6.3.2.0 (matching MAA MumuExtras::check_input_version)."""
    vt = parse_version_tuple(version_str)
    return vt is not None and vt >= MIN_INPUT_VERSION


def parse_mumu_index(address: str) -> Optional[int]:
    """Parse MuMu instance index from ADB address (matching MAA AdbController::get_mumu_index).

    Supports:
    - 'emulator-5554' -> 0, 'emulator-5556' -> 1, ...
    - '127.0.0.1:16384' -> 0, '127.0.0.1:16416' -> 1, including >= 32 instance bit-swizzle
    - '127.0.0.1:7555' -> 0 (legacy MuMu 6)
    - '127.0.0.1:5555' -> 0, '127.0.0.1:5557' -> 1, ...
    """
    addr = address.strip()
    if addr.startswith("emulator-"):
        port_s = addr[len("emulator-") :]
        if not port_s.isdigit():
            return None
        port = int(port_s)
        if port < 5554:
            return None
        return (port - 5554) // 2

    if ":" not in addr:
        return int(addr) if addr.isdigit() else None

    port_s = addr.rsplit(":", 1)[1]
    if not port_s.isdigit():
        return None
    port = int(port_s)
    if port >= 16384:
        k = (port - 16384) // 4
        return ((k & 7) << 5) | (k >> 3)
    if port == 7555:
        return 0
    if port >= 5555:
        return (port - 5555) // 2
    return None


def resolve_linux_keycode(key: int | str) -> Optional[int]:
    """Convert friendly key name or Android KeyEvent code to Linux input-event-code."""
    if isinstance(key, int):
        return ANDROID_TO_LINUX_KEYCODE.get(key)
    s = key.strip().lower()
    if s.isdigit():
        return ANDROID_TO_LINUX_KEYCODE.get(int(s))
    android_code = KEY_NAME_TO_ANDROID.get(s)
    if android_code is None:
        return None
    return ANDROID_TO_LINUX_KEYCODE.get(android_code)


def _find_first_existing(root: str, relpaths: Sequence[str]) -> Optional[str]:
    for rel in relpaths:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return None


def _extract_root_from_path(p: str) -> Optional[str]:
    """Walk upward from an executable/DLL path to locate MuMu installation root."""
    cur = os.path.abspath(p)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    for _ in range(6):
        if _find_first_existing(cur, DLL_CANDIDATE_RELPATHS) or _find_first_existing(cur, MANAGER_CANDIDATE_RELPATHS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def discover_mumu_root(custom_path: Optional[str] = None) -> str:
    """Locate MuMu installation root via explicit arg, env, MAA/maa-cli configs, running process, or common dirs."""
    candidates: List[str] = []
    if custom_path:
        candidates.append(custom_path)
    env_p = os.environ.get("AZP_MUMU_PATH")
    if env_p:
        candidates.append(env_p)

    # 1. Inspect MAA / maa-cli configs if present on this machine
    maa_gui = r"D:\GameScripts\MAA\config\gui.new.json"
    if os.path.isfile(maa_gui):
        try:
            with open(maa_gui, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            m = re.search(r'"EmulatorPath"\s*:\s*"([^"]+MuMu[^"]*)"', txt, re.IGNORECASE)
            if m:
                candidates.append(m.group(1).replace("\\\\", "\\"))
            m2 = re.search(r'"AdbPath"\s*:\s*"([^"]+MuMu[^"]+)"', txt, re.IGNORECASE)
            if m2:
                candidates.append(m2.group(1).replace("\\\\", "\\"))
        except Exception:
            pass

    appdata = os.environ.get("APPDATA", "")
    maa_toml = os.path.join(appdata, "loong", "maa", "config", "profiles", "default.toml")
    if os.path.isfile(maa_toml):
        try:
            with open(maa_toml, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            m = re.search(r"adb_path\s*=\s*['\"]([^'\"]+MuMu[^'\"]+)['\"]", txt, re.IGNORECASE)
            if m:
                candidates.append(m.group(1))
        except Exception:
            pass

    # 2. Common MuMu installation directories
    candidates.extend(
        [
            r"D:\software\Mumu\emulator\MuMuPlayer-12.0",
            r"C:\Program Files\Netease\MuMuPlayer-12.0",
            r"D:\Program Files\Netease\MuMuPlayer-12.0",
            r"C:\Program Files\Netease\MuMu Player 12",
            r"D:\Program Files\Netease\MuMu Player 12",
            r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0",
        ]
    )

    for c in candidates:
        root = _extract_root_from_path(c)
        if root:
            return root

    # 3. Query running MuMu processes via PowerShell / WMI as last resort
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^MuMu' } | Select-Object -ExpandProperty Path -First 3)",
            ],
            text=True,
            timeout=4,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            line = line.strip()
            if line:
                root = _extract_root_from_path(line)
                if root:
                    return root
    except Exception:
        pass

    raise FileNotFoundError(
        "MuMu Player installation root not found. Pass --mumu-path or set AZP_MUMU_PATH."
    )


def get_mumu_manager_path(mumu_root: str) -> Optional[str]:
    return _find_first_existing(mumu_root, MANAGER_CANDIDATE_RELPATHS)


def get_mumu_adb_path(mumu_root: str) -> Optional[str]:
    return _find_first_existing(mumu_root, ADB_CANDIDATE_RELPATHS)


def get_mumu_ipc_dll_path(mumu_root: str) -> Optional[str]:
    return _find_first_existing(mumu_root, DLL_CANDIDATE_RELPATHS)


def query_mumu_version(mumu_root: str, timeout: float = 5.0) -> Optional[str]:
    """Query MuMuManager.exe version JSON (returns e.g. '6.8.8.0')."""
    mgr = get_mumu_manager_path(mumu_root)
    if not mgr:
        return None
    try:
        out = subprocess.check_output(
            [mgr, "version"],
            cwd=os.path.dirname(mgr),
            text=True,
            timeout=timeout,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out)
        ver = data.get("version")
        return str(ver) if ver else None
    except Exception:
        return None


def list_mumu_instances(mumu_root: Optional[str] = None, timeout: float = 6.0) -> List[Dict[str, object]]:
    """List all MuMu instances via `MuMuManager.exe info -v all`."""
    root = discover_mumu_root(mumu_root)
    mgr = get_mumu_manager_path(root)
    if not mgr:
        raise FileNotFoundError(f"MuMuManager.exe not found under {root}")
    raw_bytes = subprocess.check_output(
        [mgr, "info", "-v", "all"],
        cwd=os.path.dirname(mgr),
        timeout=timeout,
        stderr=subprocess.DEVNULL,
    )
    try:
        out = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        out = raw_bytes.decode("gbk", errors="replace")
    raw = json.loads(out)
    # Single-instance installations may return a flat object with "index"
    if isinstance(raw, dict) and "index" in raw:
        items = [raw]
    elif isinstance(raw, dict):
        items = list(raw.values())
    else:
        items = []

    instances: List[Dict[str, object]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        idx = int(it.get("index", 0))
        host = str(it.get("adb_host_ip", "127.0.0.1"))
        port = int(it.get("adb_port", 0) or 0)
        instances.append(
            {
                "index": idx,
                "name": str(it.get("name", "")),
                "android_version": str(it.get("android_version", "")),
                "adb_address": f"{host}:{port}" if port else None,
                "adb_port": port,
                "pid": int(it.get("pid", 0) or 0),
                "is_android_started": bool(it.get("is_android_started", False)),
                "is_process_started": bool(it.get("is_process_started", False)),
                "player_state": str(it.get("player_state", "")),
            }
        )
    instances.sort(key=lambda x: int(x["index"]))
    return instances


@dataclass
class MumuConfig:
    mumu_path: Optional[str] = None
    index: Optional[int] = None
    adb_address: Optional[str] = None
    package_name: str = DEFAULT_PACKAGE
    enable_input: bool = True


class MumuExtras:
    """Direct ctypes binding to MuMu `external_renderer_ipc.dll` (截图增强 + 触控增强)."""

    def __init__(
        self,
        mumu_path: Optional[str] = None,
        index: Optional[int] = None,
        adb_address: Optional[str] = None,
        package_name: str = DEFAULT_PACKAGE,
        enable_input: bool = True,
    ) -> None:
        self.mumu_root = discover_mumu_root(mumu_path)
        self.package_name = package_name or DEFAULT_PACKAGE
        self.enable_input = enable_input

        # Resolve target instance index
        self.adb_address: Optional[str] = adb_address
        if index is not None:
            self.index = int(index)
        elif adb_address:
            parsed = parse_mumu_index(adb_address)
            if parsed is None:
                raise ValueError(f"Cannot parse MuMu index from address: {adb_address!r}")
            self.index = parsed
        else:
            self.index = self._auto_select_index()

        # Look up matching adb_address from MuMuManager if not provided
        if not self.adb_address:
            try:
                for inst in list_mumu_instances(self.mumu_root):
                    if inst["index"] == self.index and inst.get("adb_address"):
                        self.adb_address = str(inst["adb_address"])
                        break
            except Exception:
                pass

        self.dll_path: Optional[str] = None
        self._dll = None
        self._handle: int = 0
        self._display_id_cache: Optional[int] = None
        self.display_width: int = 0
        self.display_height: int = 0
        self._buf = None
        self.version: Optional[str] = None
        self.input_version_ok: bool = False
        self.input_available: bool = False
        self._has_finger_touch: bool = False
        self._has_legacy_touch: bool = False
        self._has_key_input: bool = False
        self._has_text_input: bool = False
        self._adb_path: Optional[str] = get_mumu_adb_path(self.mumu_root)

    def _auto_select_index(self) -> int:
        """Pick the running instance currently displaying `package_name`, else first running instance."""
        instances = list_mumu_instances(self.mumu_root)
        running = [i for i in instances if i["is_android_started"]]
        if not running:
            if instances:
                return int(instances[0]["index"])
            return 0
        if len(running) == 1:
            return int(running[0]["index"])

        # Probe each running instance via nemu_get_display_id(pkg) without disturbing other games
        dll_path = get_mumu_ipc_dll_path(self.mumu_root)
        if dll_path:
            try:
                dll = ctypes.CDLL(dll_path)
                dll.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
                dll.nemu_connect.restype = ctypes.c_int
                dll.nemu_disconnect.argtypes = [ctypes.c_int]
                dll.nemu_get_display_id.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
                dll.nemu_get_display_id.restype = ctypes.c_int
                pkg_b = self.package_name.encode("utf-8")
                for inst in running:
                    idx = int(inst["index"])
                    with _silence_c_stdout():
                        h = dll.nemu_connect(self.mumu_root, idx)
                    if h:
                        try:
                            did = dll.nemu_get_display_id(h, pkg_b, 0)
                            if did >= 0:
                                return idx
                        finally:
                            with _silence_c_stdout():
                                dll.nemu_disconnect(h)
            except Exception:
                pass
        return int(running[0]["index"])

    def connect(self) -> "MumuExtras":
        if self._handle:
            return self

        self.dll_path = get_mumu_ipc_dll_path(self.mumu_root)
        if not self.dll_path:
            raise FileNotFoundError(f"external_renderer_ipc.dll not found under {self.mumu_root}")

        dll = ctypes.CDLL(self.dll_path)
        self._dll = dll

        # Required capture functions
        dll.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        dll.nemu_connect.restype = ctypes.c_int
        dll.nemu_disconnect.argtypes = [ctypes.c_int]
        dll.nemu_disconnect.restype = None
        if hasattr(dll, "nemu_get_display_id"):
            dll.nemu_get_display_id.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            dll.nemu_get_display_id.restype = ctypes.c_int
        dll.nemu_capture_display.argtypes = [
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_void_p,
        ]
        dll.nemu_capture_display.restype = ctypes.c_int

        # Optional enhanced input functions (gated by MuMu version >= 6.3.2.0)
        self.version = query_mumu_version(self.mumu_root)
        self.input_version_ok = bool(self.version and is_input_version_supported(self.version))

        self._has_finger_touch = hasattr(dll, "nemu_input_event_finger_touch_down") and hasattr(
            dll, "nemu_input_event_finger_touch_up"
        )
        if self._has_finger_touch:
            dll.nemu_input_event_finger_touch_down.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int
            ]
            dll.nemu_input_event_finger_touch_down.restype = ctypes.c_int
            dll.nemu_input_event_finger_touch_up.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.nemu_input_event_finger_touch_up.restype = ctypes.c_int

        self._has_legacy_touch = hasattr(dll, "nemu_input_event_touch_down") and hasattr(
            dll, "nemu_input_event_touch_up"
        )
        if self._has_legacy_touch:
            dll.nemu_input_event_touch_down.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.nemu_input_event_touch_down.restype = ctypes.c_int
            dll.nemu_input_event_touch_up.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.nemu_input_event_touch_up.restype = ctypes.c_int

        self._has_key_input = hasattr(dll, "nemu_input_event_key_down") and hasattr(dll, "nemu_input_event_key_up")
        if self._has_key_input:
            dll.nemu_input_event_key_down.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.nemu_input_event_key_down.restype = ctypes.c_int
            dll.nemu_input_event_key_up.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.nemu_input_event_key_up.restype = ctypes.c_int

        self._has_text_input = hasattr(dll, "nemu_input_text")
        if self._has_text_input:
            dll.nemu_input_text.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
            dll.nemu_input_text.restype = ctypes.c_int

        self.input_available = (
            self.enable_input
            and self.input_version_ok
            and (self._has_finger_touch or self._has_legacy_touch)
            and self._has_key_input
        )

        with _silence_c_stdout():
            h = dll.nemu_connect(self.mumu_root, self.index)
        if h == 0:
            raise RuntimeError(f"nemu_connect failed for root={self.mumu_root!r}, index={self.index}")
        self._handle = h
        self._init_screencap()
        return self

    def close(self) -> None:
        self._display_id_cache = None
        if self._handle and self._dll is not None:
            try:
                with _silence_c_stdout():
                    self._dll.nemu_disconnect(self._handle)
            except Exception:
                pass
            self._handle = 0

    def reload(self) -> None:
        self.close()
        self.connect()

    def __enter__(self) -> "MumuExtras":
        return self.connect()

    def __exit__(self, *args) -> None:
        self.close()

    @property
    def has_target_display(self) -> bool:
        return self._display_id_cache is not None

    def get_display_id(self) -> int:
        """Resolve display_id for target package; cache only when real package hits (matching MAA)."""
        if self._display_id_cache is not None:
            return self._display_id_cache
        if not hasattr(self._dll, "nemu_get_display_id"):
            self._display_id_cache = 0
            return 0

        did = int(self._dll.nemu_get_display_id(self._handle, self.package_name.encode("utf-8"), 0))
        if did >= 0:
            self._display_id_cache = did
            return did

        # Fallback without caching so later game launch binds automatically
        if self.package_name != "default":
            did = int(self._dll.nemu_get_display_id(self._handle, b"default", 0))
        return did if did >= 0 else 0

    def _init_screencap(self) -> None:
        did = self.get_display_id()
        w, h = ctypes.c_int(0), ctypes.c_int(0)
        rc = self._dll.nemu_capture_display(self._handle, did, 0, ctypes.byref(w), ctypes.byref(h), None)
        if rc != 0 or w.value <= 0 or h.value <= 0:
            raise RuntimeError(f"nemu_capture_display size query failed: rc={rc}, size=({w.value},{h.value})")
        self.display_width = int(w.value)
        self.display_height = int(h.value)
        self._buf = (ctypes.c_ubyte * (self.display_width * self.display_height * 4))()

    def screencap(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
        scale: float = 1.0,
        grid: bool = False,
        grid_step: int = 160,
        save_path: Optional[str] = None,
    ) -> Image.Image:
        """Capture background RGBA frame via MuMu IPC (~10ms), auto-reloading once on error."""
        if not self._handle:
            self.connect()

        did = self.get_display_id()
        w = ctypes.c_int(self.display_width)
        h = ctypes.c_int(self.display_height)
        rc = self._dll.nemu_capture_display(self._handle, did, len(self._buf), ctypes.byref(w), ctypes.byref(h), self._buf)
        if rc != 0:
            self.reload()
            did = self.get_display_id()
            w = ctypes.c_int(self.display_width)
            h = ctypes.c_int(self.display_height)
            rc = self._dll.nemu_capture_display(self._handle, did, len(self._buf), ctypes.byref(w), ctypes.byref(h), self._buf)
            if rc != 0:
                raise RuntimeError(f"nemu_capture_display failed after reload: rc={rc}, display_id={did}")

        if w.value != self.display_width or h.value != self.display_height:
            self.display_width = int(w.value)
            self.display_height = int(h.value)
            self._buf = (ctypes.c_ubyte * (self.display_width * self.display_height * 4))()
            rc = self._dll.nemu_capture_display(self._handle, did, len(self._buf), ctypes.byref(w), ctypes.byref(h), self._buf)
            if rc != 0:
                raise RuntimeError(f"nemu_capture_display failed after resize: rc={rc}")

        # Buffer is RGBA bottom-up; stride=-1 flips vertically into standard top-down RGB
        img = Image.frombuffer(
            "RGBA",
            (self.display_width, self.display_height),
            bytes(self._buf),
            "raw",
            "RGBA",
            0,
            -1,
        ).convert("RGB")

        if roi is not None:
            img = img.crop(roi)

        if grid:
            draw = ImageDraw.Draw(img)
            iw, ih = img.size
            ox = roi[0] if roi else 0
            oy = roi[1] if roi else 0
            for x in range(0, iw, grid_step):
                draw.line([(x, 0), (x, ih)], fill=(255, 255, 0), width=1)
                draw.text((x + 3, 4), str(ox + x), fill=(255, 255, 0))
            for y in range(0, ih, grid_step):
                draw.line([(0, y), (iw, y)], fill=(255, 255, 0), width=1)
                draw.text((4, y + 3), str(oy + y), fill=(255, 255, 0))

        if scale != 1.0:
            nw = max(1, int(round(img.width * scale)))
            nh = max(1, int(round(img.height * scale)))
            img = img.resize((nw, nh), Image.Resampling.BILINEAR)

        if save_path:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            img.save(save_path)
        return img

    # ---- Enhanced Touch & Key IPC (触控增强) ----

    def _clamp_xy(self, x: float, y: float) -> Tuple[int, int]:
        ix = max(0, min(max(0, self.display_width - 1), int(round(x))))
        iy = max(0, min(max(0, self.display_height - 1), int(round(y))))
        return ix, iy

    def touch_down(self, x: float, y: float, contact: int = 0) -> bool:
        """Finger down/move at native display coordinates (contact is 0-based -> MuMu 1-based finger_id)."""
        if not self.input_available:
            return False
        did = self.get_display_id()
        ix, iy = self._clamp_xy(x, y)
        if self._has_finger_touch:
            rc = self._dll.nemu_input_event_finger_touch_down(self._handle, did, int(contact) + 1, ix, iy)
            return rc == 0
        if self._has_legacy_touch and contact == 0:
            rc = self._dll.nemu_input_event_touch_down(self._handle, did, ix, iy)
            return rc == 0
        return False

    def touch_move(self, x: float, y: float, contact: int = 0) -> bool:
        """MuMu IPC has no separate move event; repeated touch_down moves the active finger."""
        return self.touch_down(x, y, contact=contact)

    def touch_up(self, contact: int = 0) -> bool:
        """Release finger contact (0-based -> MuMu 1-based finger_id)."""
        if not self.input_available:
            return False
        did = self.get_display_id()
        if self._has_finger_touch:
            rc = self._dll.nemu_input_event_finger_touch_up(self._handle, did, int(contact) + 1)
            return rc == 0
        if self._has_legacy_touch and contact == 0:
            rc = self._dll.nemu_input_event_touch_up(self._handle, did)
            return rc == 0
        return False

    def _adb_shell(self, *args: str, timeout: float = 6.0) -> bool:
        if not self._adb_path or not self.adb_address:
            return False
        try:
            subprocess.run(
                [self._adb_path, "-s", self.adb_address, "shell", *args],
                check=True,
                timeout=timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def tap(
        self,
        x: float,
        y: float,
        hold: float = TOUCH_HOLD_SEC,
        repeat: int = 1,
        interval: float = 0.08,
        contact: int = 0,
    ) -> bool:
        """Tap at native display coordinates via MuMu IPC touch enhancement (with ADB fallback)."""
        if not self._handle:
            self.connect()
        ok = True
        for i in range(max(1, repeat)):
            if self.input_available:
                down_ok = self.touch_down(x, y, contact=contact)
                try:
                    time.sleep(max(0.015, hold))
                finally:
                    up_ok = self.touch_up(contact=contact)
                ok = ok and down_ok and up_ok
            else:
                ix, iy = self._clamp_xy(x, y)
                ok = ok and self._adb_shell("input", "tap", str(ix), str(iy))
            if i + 1 < repeat:
                time.sleep(max(0.02, interval))
            else:
                time.sleep(TOUCH_HOLD_SEC)
        return ok

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.30,
        steps: Optional[int] = None,
        contact: int = 0,
        hold_end: float = 0.03,
    ) -> bool:
        """Smooth absolute-paced swipe via MuMu IPC touch enhancement (always lifts finger in finally)."""
        if not self._handle:
            self.connect()
        ix1, iy1 = self._clamp_xy(x1, y1)
        ix2, iy2 = self._clamp_xy(x2, y2)

        if not self.input_available:
            dur_ms = max(50, int(round(duration * 1000)))
            return self._adb_shell("input", "swipe", str(ix1), str(iy1), str(ix2), str(iy2), str(dur_ms))

        n_steps = steps if steps is not None else max(8, int(round(duration / SWIPE_INTERVAL_SEC)))
        step_dt = max(0.005, duration / max(1, n_steps))
        if not self.touch_down(ix1, iy1, contact=contact):
            self.touch_up(contact=contact)
            return False

        moved = True
        try:
            time.sleep(TOUCH_HOLD_SEC)
            t0 = time.perf_counter()
            for i in range(1, n_steps + 1):
                t = i / n_steps
                # Smoothstep easing for natural camera/scroll drag without sudden momentum jump
                s = t * t * (3.0 - 2.0 * t)
                cx = ix1 + (ix2 - ix1) * s
                cy = iy1 + (iy2 - iy1) * s
                if not self.touch_move(cx, cy, contact=contact):
                    moved = False
                    break
                target_t = t0 + i * step_dt
                rem = target_t - time.perf_counter()
                if rem > 0:
                    time.sleep(rem)
            if hold_end > 0:
                time.sleep(hold_end)
        finally:
            up_ok = self.touch_up(contact=contact)
            time.sleep(TOUCH_HOLD_SEC)
        return moved and up_ok

    def key_down(self, key: int | str) -> bool:
        """Send Linux key_down via MuMu IPC (mapped from friendly key name or Android KeyEvent)."""
        if not self._handle:
            self.connect()
        if not (self.input_available and self._has_key_input):
            return False
        linux_code = resolve_linux_keycode(key)
        if linux_code is None:
            raise ValueError(f"Unsupported MuMu key: {key!r}")
        did = self.get_display_id()
        return self._dll.nemu_input_event_key_down(self._handle, did, linux_code) == 0

    def key_up(self, key: int | str) -> bool:
        """Send Linux key_up via MuMu IPC."""
        if not self._handle:
            self.connect()
        if not (self.input_available and self._has_key_input):
            return False
        linux_code = resolve_linux_keycode(key)
        if linux_code is None:
            raise ValueError(f"Unsupported MuMu key: {key!r}")
        did = self.get_display_id()
        return self._dll.nemu_input_event_key_up(self._handle, did, linux_code) == 0

    def press_key(self, key: int | str, hold: float = 0.05, repeat: int = 1, interval: float = 0.10) -> bool:
        """Tap key via MuMu IPC key enhancement (with ADB keyevent fallback)."""
        if not self._handle:
            self.connect()
        ok = True
        for i in range(max(1, repeat)):
            if self.input_available and self._has_key_input:
                d_ok = self.key_down(key)
                try:
                    time.sleep(max(0.015, hold))
                finally:
                    u_ok = self.key_up(key)
                ok = ok and d_ok and u_ok
            else:
                code = key if isinstance(key, int) else KEY_NAME_TO_ANDROID.get(str(key).lower())
                if code is None:
                    raise ValueError(f"Unsupported key for ADB fallback: {key!r}")
                ok = ok and self._adb_shell("input", "keyevent", str(code))
            if i + 1 < repeat:
                time.sleep(max(0.02, interval))
        return ok

    def input_text(self, text: str) -> bool:
        """Send text directly via `nemu_input_text` (with ADB fallback)."""
        if not self._handle:
            self.connect()
        if self.input_available and self._has_text_input:
            raw = text.encode("utf-8")
            return self._dll.nemu_input_text(self._handle, len(raw), raw) == 0
        return self._adb_shell("input", "text", text)

    def joystick_walk(
        self,
        direction: str = "w",
        duration: float = 2.0,
        sprint: bool = False,
        jump: bool = False,
        spam_f: bool = False,
        joy_center: Tuple[int, int] = (298, 795),
        joy_radius: int = 135,
        sprint_btn: Tuple[int, int] = (1535, 985),
        jump_btn: Tuple[int, int] = (1600, 595),
        interact_btn: Tuple[int, int] = (1345, 570),
    ) -> bool:
        """Multi-touch virtual joystick movement (contact 0 holds stick, contact 1 taps sprint/jump/interact)."""
        if not self._handle:
            self.connect()
        vx, vy = 0.0, 0.0
        for ch in direction.lower():
            if ch == "w":
                vy -= 1.0
            elif ch == "s":
                vy += 1.0
            elif ch == "a":
                vx -= 1.0
            elif ch == "d":
                vx += 1.0
        if vx == 0.0 and vy == 0.0:
            vy = -1.0
        norm = math.hypot(vx, vy)
        tx = joy_center[0] + int(round((vx / norm) * joy_radius))
        ty = joy_center[1] + int(round((vy / norm) * joy_radius))

        if not self.input_available:
            return self.swipe(joy_center[0], joy_center[1], tx, ty, duration=duration, contact=0)

        ok = self.touch_down(joy_center[0], joy_center[1], contact=0)
        try:
            time.sleep(0.03)
            self.touch_move(tx, ty, contact=0)
            if sprint:
                time.sleep(0.05)
                self.touch_down(sprint_btn[0], sprint_btn[1], contact=1)
                time.sleep(0.04)
                self.touch_up(contact=1)

            t_end = time.perf_counter() + max(0.08, duration)
            next_f = time.perf_counter() + 0.18
            next_jump = time.perf_counter() + 0.45
            while time.perf_counter() < t_end:
                now = time.perf_counter()
                if spam_f and now >= next_f:
                    self.touch_down(interact_btn[0], interact_btn[1], contact=1)
                    time.sleep(0.03)
                    self.touch_up(contact=1)
                    next_f = now + 0.25
                if jump and now >= next_jump:
                    self.touch_down(jump_btn[0], jump_btn[1], contact=1)
                    time.sleep(0.03)
                    self.touch_up(contact=1)
                    next_jump = now + 0.90
                time.sleep(0.025)
        finally:
            self.touch_up(contact=1)
            self.touch_up(contact=0)
            time.sleep(TOUCH_HOLD_SEC)
        return ok

    def turn_camera(
        self,
        dx: int,
        dy: int = 0,
        duration: float = 0.24,
        steps: int = 14,
        anchor: Tuple[int, int] = (1180, 480),
    ) -> bool:
        """Rotate 3D camera via right-half touch drag (contact 0)."""
        x1, y1 = anchor
        x2 = max(300, min(self.display_width - 80, x1 + int(dx)))
        y2 = max(120, min(self.display_height - 160, y1 + int(dy)))
        return self.swipe(x1, y1, x2, y2, duration=duration, steps=steps, contact=0)

    def info(self) -> Dict[str, object]:
        """Return diagnostic summary of MuMu IPC connection and enhancement capabilities."""
        if not self._handle:
            self.connect()
        t0 = time.perf_counter()
        _ = self.screencap()
        cap_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "backend": "mumu",
            "mumu_root": self.mumu_root,
            "dll_path": self.dll_path,
            "version": self.version,
            "index": self.index,
            "adb_address": self.adb_address,
            "package_name": self.package_name,
            "display_id": self.get_display_id(),
            "target_display_matched": self.has_target_display,
            "size": [self.display_width, self.display_height],
            "screencap_enhanced": True,
            "capture_ms": cap_ms,
            "touch_enhanced": self.input_available,
            "input_version_ok": self.input_version_ok,
            "multi_touch_supported": self._has_finger_touch and self.input_available,
            "key_input_supported": self._has_key_input and self.input_available,
            "text_input_supported": self._has_text_input and self.input_available,
        }


def open_mumu(
    mumu_path: Optional[str] = None,
    index: Optional[int] = None,
    adb_address: Optional[str] = None,
    package_name: str = DEFAULT_PACKAGE,
    enable_input: bool = True,
) -> MumuExtras:
    """Create and connect a MumuExtras instance."""
    return MumuExtras(
        mumu_path=mumu_path,
        index=index,
        adb_address=adb_address,
        package_name=package_name,
        enable_input=enable_input,
    ).connect()
