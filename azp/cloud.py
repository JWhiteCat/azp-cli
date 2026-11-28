"""Built-in WebView2 CloudShell (`azp-cloud.exe`) backend for AzurPromilia (蓝色星原：旅谣).

Provides zero-focus-hijack background automation over the native Windows CloudShell
window (`Azp.CloudShell`):
- Background screenshot capture via Win32 `PrintWindow(..., PW_RENDERFULLCONTENT)`.
- Background mouse, scroll, keyboard, text, and relative 3D camera input via
  `PostMessageW` (bridged to WebView2 CDP by `CloudShell/src/InputBridge.cpp`).
- Proportional coordinate scaling from canonical 1920x1080 logical coordinates
  (or normalized 0..1 coordinates) to the physical CloudShell client size (default 1280x720).
- Detached process launch, reuse, graceful `WM_CLOSE` shutdown, and URL preset resolution.
"""
from __future__ import annotations

import ctypes
import math
import os
import struct
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw

from .win import GameWindow, capture_background, find_game_window

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WINDOW_CLASS = "Azp.CloudShell"
DEFAULT_TITLE = "AZP CloudShell"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
LOGICAL_WIDTH = 1920
LOGICAL_HEIGHT = 1080

# Official & third-party cloud gaming web client presets
URL_PRESETS: Dict[str, str] = {
    "manjuu": "https://azurpromilia.manjuu.com/cloudgame",
    "manjuu-alt": "https://azurpromilia.manjuu.com/cloud/",
    "163": "https://cg.163.com",
    "netease": "https://cg.163.com",
}
DEFAULT_URL_PRESET = "manjuu"
DEFAULT_URL = URL_PRESETS[DEFAULT_URL_PRESET]

# Default executable search paths (relative to repo root)
EXE_CANDIDATE_RELPATHS: Tuple[str, ...] = (
    os.path.join("CloudShell", "dist", "azp-cloud.exe"),
    os.path.join("CloudShell", "build", "bin", "RelWithDebInfo", "azp-cloud.exe"),
    os.path.join("CloudShell", "build", "bin", "Release", "azp-cloud.exe"),
    os.path.join("bin", "azp-cloud.exe"),
)

# Win32 window messages
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

# Mouse key state flags (wParam)
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_SHIFT = 0x0004
MK_CONTROL = 0x0008
MK_MBUTTON = 0x0010
MK_XBUTTON1 = 0x0020
MK_XBUTTON2 = 0x0040

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

WHEEL_DELTA = 120

# Camera steering calibration defaults (inspired by MaaEnd CloudWeb + LSXY 6.35 px/deg)
DEFAULT_CAMERA_PX_PER_DEG = 6.35
DEFAULT_MAX_REL_STEP = 120
DEFAULT_REL_STEP_DELAY = 0.012

# Process creation flags for detached CloudShell launch
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Extended key bit in Win32 WM_KEYDOWN / WM_KEYUP lParam (bit 24)
KF_EXTENDED_LPARAM_BIT = 1 << 24
KF_KEYUP_LPARAM_BITS = (1 << 30) | (1 << 31)

# Canonical key name -> (Win32 VK code, is_extended)
# Matches CloudShell/src/KeyMap.cpp and InputBridge.cpp contract.
KEY_NAME_TO_VK: Dict[str, Tuple[int, bool]] = {
    # Letters A-Z
    **{chr(ord("a") + i): (0x41 + i, False) for i in range(26)},
    # Top-row digits 0-9
    **{str(i): (0x30 + i, False) for i in range(10)},
    # Function keys F1-F24
    **{f"f{i}": (0x6F + i, False) for i in range(1, 25)},
    # Numpad digits
    **{f"num{i}": (0x60 + i, False) for i in range(10)},
    **{f"numpad{i}": (0x60 + i, False) for i in range(10)},
    # Control & navigation keys
    "esc": (0x1B, False),
    "escape": (0x1B, False),
    "enter": (0x0D, False),
    "return": (0x0D, False),
    "numpadenter": (0x0D, True),
    "tab": (0x09, False),
    "space": (0x20, False),
    "backspace": (0x08, False),
    "back": (0x08, False),
    "caps": (0x14, False),
    "capslock": (0x14, False),
    # Extended navigation keys (bit 24 set)
    "up": (0x26, True),
    "down": (0x28, True),
    "left": (0x25, True),
    "right": (0x27, True),
    "insert": (0x2D, True),
    "ins": (0x2D, True),
    "delete": (0x2E, True),
    "del": (0x2E, True),
    "home": (0x24, True),
    "end": (0x23, True),
    "pageup": (0x21, True),
    "pgup": (0x21, True),
    "pagedown": (0x22, True),
    "pgdn": (0x22, True),
    # Modifiers
    "shift": (0xA0, False),
    "lshift": (0xA0, False),
    "rshift": (0xA1, False),
    "ctrl": (0xA2, False),
    "lctrl": (0xA2, False),
    "rctrl": (0xA3, True),
    "alt": (0xA4, False),
    "lalt": (0xA4, False),
    "ralt": (0xA5, True),
    "win": (0x5B, True),
    "lwin": (0x5B, True),
    "meta": (0x5B, True),
    "rwin": (0x5C, True),
    # Numpad operators
    "multiply": (0x6A, False),
    "add": (0x6B, False),
    "subtract": (0x6D, False),
    "decimal": (0x6E, False),
    "divide": (0x6F, True),
    # US OEM punctuation
    ";": (0xBA, False),
    "semicolon": (0xBA, False),
    "=": (0xBB, False),
    "equal": (0xBB, False),
    ",": (0xBC, False),
    "comma": (0xBC, False),
    "-": (0xBD, False),
    "minus": (0xBD, False),
    ".": (0xBE, False),
    "period": (0xBE, False),
    "/": (0xBF, False),
    "slash": (0xBF, False),
    "`": (0xC0, False),
    "backquote": (0xC0, False),
    "[": (0xDB, False),
    "bracketleft": (0xDB, False),
    "\\": (0xDC, False),
    "backslash": (0xDC, False),
    "]": (0xDD, False),
    "bracketright": (0xDD, False),
    "'": (0xDE, False),
    "quote": (0xDE, False),
}

# Set of VKs that should default to extended=True if supplied as raw integer
EXTENDED_VKS = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0xA3, 0xA5})

_ACTIVE_CLOUD_BACKEND: Optional["CloudBackend"] = None


def normalize_backend_name(name: Optional[str]) -> str:
    """Normalize backend CLI/env names, mapping cloud_web/cloudweb/cloud-web -> cloud."""
    raw = (name or "pc").strip().lower()
    if raw in ("cloud", "cloud_web", "cloudweb", "cloud-web", "cloudshell"):
        return "cloud"
    if raw in ("pc", "mumu"):
        return raw
    raise ValueError(f"Unsupported backend {name!r}. Expected one of: pc, mumu, cloud (aliases: cloud_web, cloudweb).")


def get_active_cloud_backend() -> Optional["CloudBackend"]:
    """Return the process-wide active CloudBackend, or None when running in pc/mumu mode."""
    return _ACTIVE_CLOUD_BACKEND


def set_active_cloud_backend(backend: Optional["CloudBackend"]) -> None:
    """Set or clear the process-wide active CloudBackend."""
    global _ACTIVE_CLOUD_BACKEND
    _ACTIVE_CLOUD_BACKEND = backend


@contextmanager
def use_cloud_backend(backend: Optional["CloudBackend"]) -> Iterator[Optional["CloudBackend"]]:
    """Temporarily activate a CloudBackend within a context block."""
    prev = get_active_cloud_backend()
    set_active_cloud_backend(backend)
    try:
        yield backend
    finally:
        set_active_cloud_backend(prev)


def resolve_url(url_or_preset: Optional[str]) -> str:
    """Resolve a preset name (manjuu, manjuu-alt, 163) or explicit http(s)/file URL."""
    if url_or_preset is None:
        return DEFAULT_URL
    val = str(url_or_preset).strip()
    if not val:
        return DEFAULT_URL
    low = val.lower()
    if low in URL_PRESETS:
        return URL_PRESETS[low]
    if low.startswith(("http://", "https://", "file://")):
        return val
    presets_str = ", ".join(sorted(URL_PRESETS.keys()))
    raise ValueError(f"Unknown cloud URL or preset {url_or_preset!r}. Use http(s)://, file://, or a preset: {presets_str}")


def find_cloud_exe(explicit_path: Optional[str] = None, repo_root: Optional[str] = None) -> Optional[str]:
    """Locate `azp-cloud.exe` following the canonical precedence order."""
    candidates: List[str] = []
    if explicit_path:
        candidates.append(explicit_path)
    env_exe = os.environ.get("AZP_CLOUD_EXE")
    if env_exe:
        candidates.append(env_exe)

    root = os.path.abspath(repo_root or ROOT_DIR)
    for rel in EXE_CANDIDATE_RELPATHS:
        candidates.append(os.path.join(root, rel))

    for path in candidates:
        expanded = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(expanded):
            return expanded
    return None


def make_lparam(x: int, y: int) -> int:
    """Pack signed 16-bit (x, y) into a Win32 LPARAM (`MAKELPARAM`)."""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def unpack_lparam(lparam: int) -> Tuple[int, int]:
    """Unpack Win32 LPARAM into signed 16-bit (x, y) coordinates."""
    x = lparam & 0xFFFF
    y = (lparam >> 16) & 0xFFFF
    if x >= 0x8000:
        x -= 0x10000
    if y >= 0x8000:
        y -= 0x10000
    return x, y


def make_wheel_wparam(delta: int, buttons: int = 0) -> int:
    """Pack wheel delta (high 16-bit signed) and button state (low 16-bit) into WPARAM."""
    return ((int(delta) & 0xFFFF) << 16) | (int(buttons) & 0xFFFF)


def make_key_lparam(extended: bool = False, key_up: bool = False, repeat_count: int = 1) -> int:
    """Construct Win32 WM_KEYDOWN / WM_KEYUP lParam with bit 24 set for extended keys."""
    lp = max(1, int(repeat_count)) & 0xFFFF
    if extended:
        lp |= KF_EXTENDED_LPARAM_BIT
    if key_up:
        lp |= KF_KEYUP_LPARAM_BITS
    return lp


def resolve_vk(key: Union[str, int]) -> Tuple[int, bool]:
    """Resolve key name or raw VK int to `(vk_code, is_extended)`."""
    if isinstance(key, int):
        vk = int(key)
        if not (0 <= vk <= 0xFF):
            raise ValueError(f"VK code out of range: {vk}")
        return vk, (vk in EXTENDED_VKS)
    k = str(key).strip().lower()
    if k in KEY_NAME_TO_VK:
        return KEY_NAME_TO_VK[k]
    raise ValueError(f"Unsupported key for CloudShell: {key!r}")


def encode_utf16_units(text: str) -> List[int]:
    """Encode a Python string into UTF-16 code units (including surrogate pairs) for `WM_CHAR`."""
    if not text:
        return []
    raw = text.encode("utf-16-le")
    count = len(raw) // 2
    return list(struct.unpack(f"<{count}H", raw))


def batch_relative_deltas(
    dx: int,
    dy: int = 0,
    max_step: int = DEFAULT_MAX_REL_STEP,
    steps: Optional[int] = None,
) -> List[Tuple[int, int]]:
    """Split `(dx, dy)` into integer micro-steps with `|sx|, |sy| <= max_step` and exact sum."""
    dx_i = int(round(dx))
    dy_i = int(round(dy))
    if dx_i == 0 and dy_i == 0:
        return []
    cap = max(1, int(max_step))
    min_steps = max(1, math.ceil(abs(dx_i) / cap), math.ceil(abs(dy_i) / cap))
    n_steps = max(min_steps, int(steps) if steps is not None else min_steps)

    out: List[Tuple[int, int]] = []
    sent_x = 0
    sent_y = 0
    for i in range(1, n_steps + 1):
        target_x = int(round(dx_i * i / n_steps))
        target_y = int(round(dy_i * i / n_steps))
        sx = target_x - sent_x
        sy = target_y - sent_y
        # Guarantee strict clamp even under rounding edge cases
        sx = max(-cap, min(cap, sx))
        sy = max(-cap, min(cap, sy))
        if sx != 0 or sy != 0:
            out.append((sx, sy))
            sent_x += sx
            sent_y += sy

    # Flush any residual (should be 0, safeguarded for extreme rounding cases)
    rem_x = dx_i - sent_x
    rem_y = dy_i - sent_y
    while rem_x != 0 or rem_y != 0:
        sx = max(-cap, min(cap, rem_x))
        sy = max(-cap, min(cap, rem_y))
        out.append((sx, sy))
        rem_x -= sx
        rem_y -= sy
    return out


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


@dataclass
class CloudConfig:
    """Configuration for launching and controlling `azp-cloud.exe`."""

    exe: Optional[str] = None
    url: str = DEFAULT_URL
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    title: str = DEFAULT_TITLE
    window_class: str = WINDOW_CLASS
    user_data_dir: Optional[str] = None
    log_file: Optional[str] = None
    inject: Tuple[str, ...] = field(default_factory=tuple)
    devtools: bool = False
    debug_port: Optional[int] = None
    disable_webgl2: bool = False
    auto_launch: bool = False
    camera_px_per_deg: float = DEFAULT_CAMERA_PX_PER_DEG
    camera_scale: float = 1.0
    max_rel_step: int = DEFAULT_MAX_REL_STEP
    rel_step_delay: float = DEFAULT_REL_STEP_DELAY
    repo_root: str = ROOT_DIR

    def __post_init__(self) -> None:
        self.url = resolve_url(self.url)
        self.width = max(320, int(self.width))
        self.height = max(180, int(self.height))
        self.title = str(self.title or DEFAULT_TITLE)
        self.window_class = str(self.window_class or WINDOW_CLASS)
        if not self.user_data_dir:
            self.user_data_dir = os.path.join(self.repo_root, "cache", "cloud-shell", "WebView2")
        if not self.log_file:
            self.log_file = os.path.join(self.repo_root, "cache", "cloud-shell", "cloud-shell.log")
        self.inject = tuple(str(p) for p in self.inject if p)

    @classmethod
    def from_env(cls, **overrides) -> "CloudConfig":
        """Build a CloudConfig populated from `AZP_CLOUD_*` env vars with explicit overrides."""
        base: Dict[str, object] = {
            "exe": os.environ.get("AZP_CLOUD_EXE") or None,
            "url": os.environ.get("AZP_CLOUD_URL") or DEFAULT_URL,
            "width": _env_int("AZP_CLOUD_WIDTH", DEFAULT_WIDTH) or DEFAULT_WIDTH,
            "height": _env_int("AZP_CLOUD_HEIGHT", DEFAULT_HEIGHT) or DEFAULT_HEIGHT,
            "title": os.environ.get("AZP_CLOUD_TITLE") or DEFAULT_TITLE,
            "user_data_dir": os.environ.get("AZP_CLOUD_USER_DATA") or None,
            "log_file": os.environ.get("AZP_CLOUD_LOG_FILE") or None,
            "devtools": _env_bool("AZP_CLOUD_DEVTOOLS", False),
            "debug_port": _env_int("AZP_CLOUD_DEBUG_PORT", None),
            "disable_webgl2": _env_bool("AZP_CLOUD_DISABLE_WEBGL2", False),
            "auto_launch": _env_bool("AZP_CLOUD_AUTO_LAUNCH", False),
            "camera_scale": _env_float("AZP_CLOUD_CAMERA_SCALE", 1.0),
        }
        for k, v in overrides.items():
            if v is not None:
                base[k] = v
        return cls(**base)  # type: ignore[arg-type]

    def resolve_exe(self) -> Optional[str]:
        return find_cloud_exe(explicit_path=self.exe, repo_root=self.repo_root)

    def build_cli_args(self, exe_path: Optional[str] = None) -> List[str]:
        """Construct the exact command-line invocation for `azp-cloud.exe`."""
        exe = exe_path or self.resolve_exe() or "azp-cloud.exe"
        args: List[str] = [
            exe,
            "--url",
            self.url,
            "--width",
            str(self.width),
            "--height",
            str(self.height),
            "--title",
            self.title,
            "--user-data-dir",
            os.path.abspath(str(self.user_data_dir)),
            "--log-file",
            os.path.abspath(str(self.log_file)),
        ]
        for script_path in self.inject:
            args.extend(["--inject", os.path.abspath(script_path)])
        if self.devtools:
            args.append("--devtools")
        if self.debug_port is not None:
            args.extend(["--debug-port", str(int(self.debug_port))])
        if self.disable_webgl2:
            args.append("--disable-webgl2")
        return args


def _default_post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = ctypes.c_int
    # Mask to pointer-width unsigned / signed safely
    wp = int(wparam) & ((1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1)
    lp = ctypes.c_ssize_t(int(lparam)).value
    return bool(user32.PostMessageW(ctypes.c_void_p(int(hwnd)), int(msg), wp, lp))


def _default_terminate_pid(pid: int) -> bool:
    PROCESS_TERMINATE = 0x0001
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not h:
        return False
    try:
        return bool(ctypes.windll.kernel32.TerminateProcess(h, 0))
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


class CloudBackend:
    """Zero-focus-hijack backend controlling the built-in `azp-cloud.exe` WebView2 shell."""

    def __init__(
        self,
        config: Optional[CloudConfig] = None,
        *,
        _post_message_fn: Optional[Callable[[int, int, int, int], bool]] = None,
        _find_window_fn: Optional[Callable[[], GameWindow]] = None,
        _capture_fn: Optional[Callable[[GameWindow], Image.Image]] = None,
        _terminate_pid_fn: Optional[Callable[[int], bool]] = None,
        _sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.config = config or CloudConfig.from_env()
        self._post_message_fn = _post_message_fn or _default_post_message
        self._find_window_fn = _find_window_fn
        self._capture_fn = _capture_fn
        self._terminate_pid_fn = _terminate_pid_fn or _default_terminate_pid
        self._sleep_fn = _sleep_fn or time.sleep

        self._held_buttons: int = 0
        self._last_cursor_client: Optional[Tuple[int, int]] = None
        self._last_proc: Optional[subprocess.Popen] = None

    def _sleep(self, sec: float) -> None:
        if sec > 0:
            self._sleep_fn(sec)

    # ---- Window Discovery & Lifecycle ----

    def find_window(self, auto_launch: Optional[bool] = None, wait_timeout: float = 30.0) -> GameWindow:
        """Locate the running `Azp.CloudShell` window (optionally auto-launching if missing)."""
        should_launch = self.config.auto_launch if auto_launch is None else bool(auto_launch)
        try:
            if self._find_window_fn is not None:
                return self._find_window_fn()
            return find_game_window(
                title_substr=self.config.title,
                class_name=self.config.window_class,
                use_active_backend=False,
            )
        except RuntimeError:
            if not should_launch:
                raise
            return self.launch(wait_timeout=wait_timeout, reuse_existing=False)

    def launch(self, wait_timeout: float = 30.0, reuse_existing: bool = True) -> GameWindow:
        """Spawn `azp-cloud.exe` detached and wait until its `Azp.CloudShell` window appears."""
        if reuse_existing:
            try:
                return self.find_window(auto_launch=False)
            except RuntimeError:
                pass

        exe = self.config.resolve_exe()
        if not exe:
            searched = ", ".join(os.path.join(self.config.repo_root, rel) for rel in EXE_CANDIDATE_RELPATHS)
            raise FileNotFoundError(
                f"azp-cloud.exe not found (searched: {searched}). "
                "Build it with `python azp.py cloud build` or pass --cloud-exe / AZP_CLOUD_EXE."
            )

        os.makedirs(os.path.abspath(str(self.config.user_data_dir)), exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(str(self.config.log_file))), exist_ok=True)

        cmd = self.config.build_cli_args(exe_path=exe)
        flags_breakaway = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
        flags_fallback = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.config.repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags_breakaway,
                close_fds=True,
            )
        except (PermissionError, OSError):
            proc = subprocess.Popen(
                cmd,
                cwd=self.config.repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags_fallback,
                close_fds=True,
            )
        self._last_proc = proc

        deadline = time.monotonic() + max(1.0, float(wait_timeout))
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                raise RuntimeError(
                    f"azp-cloud.exe exited early with code {rc}. Check log: {self.config.log_file}"
                )
            try:
                gw = self.find_window(auto_launch=False)
                if gw.width >= 100 and gw.height >= 100:
                    return gw
            except RuntimeError:
                pass
            self._sleep(0.25)

        raise TimeoutError(
            f"Timed out after {wait_timeout:.1f}s waiting for CloudShell window "
            f"(class={self.config.window_class!r}, title={self.config.title!r})."
        )

    def close(self, timeout: float = 5.0) -> Dict[str, object]:
        """Gracefully close CloudShell via `WM_CLOSE`, terminating only if it does not exit in time.

        Preserves `cache/cloud-shell/WebView2` cookies/session data.
        """
        try:
            gw = self.find_window(auto_launch=False)
        except RuntimeError:
            return {"ok": True, "was_running": False, "closed_gracefully": True}

        self._post(gw.hwnd, WM_CLOSE, 0, 0)
        deadline = time.monotonic() + max(0.2, float(timeout))
        while time.monotonic() < deadline:
            try:
                cur = self.find_window(auto_launch=False)
                if cur.hwnd != gw.hwnd:
                    return {"ok": True, "was_running": True, "hwnd": gw.hwnd, "pid": gw.pid, "closed_gracefully": True}
            except RuntimeError:
                return {"ok": True, "was_running": True, "hwnd": gw.hwnd, "pid": gw.pid, "closed_gracefully": True}
            self._sleep(0.15)

        terminated = self._terminate_pid_fn(gw.pid)
        return {
            "ok": bool(terminated),
            "was_running": True,
            "hwnd": gw.hwnd,
            "pid": gw.pid,
            "closed_gracefully": False,
            "terminated": bool(terminated),
        }

    # ---- Coordinate Scaling ----

    def scale_coords(
        self,
        x: float,
        y: float,
        gw: Optional[GameWindow] = None,
        *,
        normalized: bool = False,
        logical_size: Tuple[int, int] = (LOGICAL_WIDTH, LOGICAL_HEIGHT),
    ) -> Tuple[int, int]:
        """Map canonical 1920x1080 logical (or normalized 0..1) coords to physical CloudShell client px."""
        gw = gw or self.find_window()
        w = max(1, gw.width)
        h = max(1, gw.height)
        if normalized:
            cx = int(round(float(x) * w))
            cy = int(round(float(y) * h))
        else:
            lw, lh = logical_size
            cx = int(round(float(x) * w / float(lw)))
            cy = int(round(float(y) * h / float(lh)))
        cx = max(0, min(w - 1, cx))
        cy = max(0, min(h - 1, cy))
        return cx, cy

    def _resolve_client_xy(
        self,
        x: Optional[float],
        y: Optional[float],
        gw: GameWindow,
        normalized: bool = False,
    ) -> Tuple[int, int]:
        if x is not None and y is not None:
            cx, cy = self.scale_coords(x, y, gw=gw, normalized=normalized)
            self._last_cursor_client = (cx, cy)
            return cx, cy
        if self._last_cursor_client is not None:
            cx, cy = self._last_cursor_client
            return max(0, min(max(0, gw.width - 1), cx)), max(0, min(max(0, gw.height - 1), cy))
        return gw.width // 2, gw.height // 2

    def _client_to_screen(self, gw: GameWindow, cx: int, cy: int) -> Tuple[int, int]:
        return gw.client_rect[0] + int(cx), gw.client_rect[1] + int(cy)

    # ---- Background Capture ----

    def capture(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
        scale: float = 1.0,
        grid: bool = False,
        grid_step: int = 160,
        save_path: Optional[str] = None,
        gw: Optional[GameWindow] = None,
        logical_roi: bool = False,
    ) -> Image.Image:
        """Capture CloudShell client area in the background via `PrintWindow(PW_RENDERFULLCONTENT)`."""
        gw = gw or self.find_window()
        if self._capture_fn is not None:
            img = self._capture_fn(gw).convert("RGB")
        else:
            img = capture_background(gw=gw, scale=1.0, save_path=None, use_active_backend=False)

        ox, oy = 0, 0
        if roi is not None:
            rx1, ry1, rx2, ry2 = roi
            if logical_roi or rx2 > img.width or ry2 > img.height:
                sx = img.width / float(LOGICAL_WIDTH)
                sy = img.height / float(LOGICAL_HEIGHT)
                rx1 = int(round(rx1 * sx))
                ry1 = int(round(ry1 * sy))
                rx2 = int(round(rx2 * sx))
                ry2 = int(round(ry2 * sy))
            rx1 = max(0, min(img.width, int(rx1)))
            ry1 = max(0, min(img.height, int(ry1)))
            rx2 = max(rx1 + 1, min(img.width, int(rx2)))
            ry2 = max(ry1 + 1, min(img.height, int(ry2)))
            ox, oy = rx1, ry1
            img = img.crop((rx1, ry1, rx2, ry2))

        if grid:
            draw = ImageDraw.Draw(img)
            iw, ih = img.size
            for gx in range(0, iw, grid_step):
                draw.line([(gx, 0), (gx, ih)], fill=(255, 255, 0), width=1)
                draw.text((gx + 3, 4), str(ox + gx), fill=(255, 255, 0))
            for gy in range(0, ih, grid_step):
                draw.line([(0, gy), (iw, gy)], fill=(255, 255, 0), width=1)
                draw.text((4, gy + 3), str(oy + gy), fill=(255, 255, 0))

        if scale != 1.0:
            nw = max(1, int(round(img.width * scale)))
            nh = max(1, int(round(img.height * scale)))
            img = img.resize((nw, nh), Image.Resampling.BILINEAR)

        if save_path:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            img.save(save_path)
        return img

    def screencap(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
        scale: float = 1.0,
        grid: bool = False,
        grid_step: int = 160,
        save_path: Optional[str] = None,
        gw: Optional[GameWindow] = None,
    ) -> Image.Image:
        """Alias for `capture()` mirroring `MumuExtras.screencap()`."""
        return self.capture(roi=roi, scale=scale, grid=grid, grid_step=grid_step, save_path=save_path, gw=gw)

    # ---- Background PostMessageW Input ----

    def _post(self, hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
        return bool(self._post_message_fn(int(hwnd), int(msg), int(wparam), int(lparam)))

    def move_to(
        self,
        x: float,
        y: float,
        *,
        normalized: bool = False,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Move virtual pointer to scaled client coordinates without stealing desktop cursor.

        Per CloudShell `InputBridge` contract, `WM_MOUSEMOVE` with `wParam == 0` is ignored to
        filter stray physical cursor events; `InputBridge` tracks pressed buttons internally
        from `WM_*BUTTONDOWN/UP`, so passing `MK_LBUTTON` when no button is held dispatches
        CDP `mouseMoved` with `button="none", buttons=0`.
        """
        gw = gw or self.find_window()
        cx, cy = self.scale_coords(x, y, gw=gw, normalized=normalized)
        self._last_cursor_client = (cx, cy)
        wparam = self._held_buttons if self._held_buttons else MK_LBUTTON
        return self._post(gw.hwnd, WM_MOUSEMOVE, wparam, make_lparam(cx, cy))

    def mouse_button(
        self,
        button: str = "left",
        down: bool = True,
        x: Optional[float] = None,
        y: Optional[float] = None,
        *,
        normalized: bool = False,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Press or release a mouse button at scaled client coordinates."""
        gw = gw or self.find_window()
        cx, cy = self._resolve_client_xy(x, y, gw=gw, normalized=normalized)
        b = button.lower().strip()

        btn_specs = {
            "left": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, 0),
            "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON, 0),
            "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON, 0),
            "x1": (WM_XBUTTONDOWN, WM_XBUTTONUP, MK_XBUTTON1, XBUTTON1),
            "back": (WM_XBUTTONDOWN, WM_XBUTTONUP, MK_XBUTTON1, XBUTTON1),
            "x2": (WM_XBUTTONDOWN, WM_XBUTTONUP, MK_XBUTTON2, XBUTTON2),
            "forward": (WM_XBUTTONDOWN, WM_XBUTTONUP, MK_XBUTTON2, XBUTTON2),
        }
        if b not in btn_specs:
            raise ValueError(f"Unsupported mouse button: {button!r}")

        msg_down, msg_up, mk_flag, xbtn_hi = btn_specs[b]
        if down:
            self._held_buttons |= mk_flag
            msg = msg_down
        else:
            self._held_buttons &= ~mk_flag
            msg = msg_up

        wparam = self._held_buttons | (xbtn_hi << 16)
        return self._post(gw.hwnd, msg, wparam, make_lparam(cx, cy))

    def click(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        *,
        button: str = "left",
        clicks: int = 1,
        hold: float = 0.05,
        interval: float = 0.08,
        hold_alt: bool = False,
        normalized: bool = False,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Click at logical 1920x1080 (or normalized) client coordinates in the background."""
        gw = gw or self.find_window()
        ok = True
        if hold_alt:
            ok = self.key_down("alt", gw=gw) and ok
            self._sleep(0.06)
        try:
            for i in range(max(1, int(clicks))):
                d_ok = self.mouse_button(button, True, x=x, y=y, normalized=normalized, gw=gw)
                try:
                    self._sleep(max(0.015, float(hold)))
                finally:
                    u_ok = self.mouse_button(button, False, x=x, y=y, normalized=normalized, gw=gw)
                ok = ok and d_ok and u_ok
                if i + 1 < clicks:
                    self._sleep(max(0.02, float(interval)))
        finally:
            if hold_alt:
                self._sleep(0.04)
                self.key_up("alt", gw=gw)
        return ok

    def tap(
        self,
        x: float,
        y: float,
        hold: float = 0.05,
        repeat: int = 1,
        interval: float = 0.08,
        normalized: bool = False,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Tap helper matching `MumuExtras.tap`."""
        return self.click(x, y, button="left", clicks=repeat, hold=hold, interval=interval, normalized=normalized, gw=gw)

    def drag(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        duration: float = 0.35,
        steps: int = 18,
        button: str = "left",
        normalized: bool = False,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Smooth background drag between logical 1920x1080 (or normalized) coordinates."""
        gw = gw or self.find_window()
        cx1, cy1 = self.scale_coords(x1, y1, gw=gw, normalized=normalized)
        cx2, cy2 = self.scale_coords(x2, y2, gw=gw, normalized=normalized)
        n_steps = max(1, int(steps))
        step_dt = max(0.005, float(duration) / n_steps)

        ok = self.mouse_button(button, True, x=x1, y=y1, normalized=normalized, gw=gw)
        self._sleep(0.035)
        try:
            for i in range(1, n_steps + 1):
                t = i / n_steps
                cx = int(round(cx1 + (cx2 - cx1) * t))
                cy = int(round(cy1 + (cy2 - cy1) * t))
                self._last_cursor_client = (cx, cy)
                # Held button flag in wParam ensures InputBridge forwards the drag move
                ok = self._post(gw.hwnd, WM_MOUSEMOVE, self._held_buttons, make_lparam(cx, cy)) and ok
                self._sleep(step_dt)
        finally:
            self._sleep(0.03)
            ok = self.mouse_button(button, False, x=x2, y=y2, normalized=normalized, gw=gw) and ok
        return ok

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.35,
        steps: int = 18,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Alias for `drag()` matching `MumuExtras.swipe`."""
        return self.drag(x1, y1, x2, y2, duration=duration, steps=steps, gw=gw)

    def scroll(
        self,
        delta: int,
        x: Optional[float] = None,
        y: Optional[float] = None,
        *,
        horizontal: bool = False,
        normalized: bool = False,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Send mouse wheel notches (+up/zoom-in, -down/zoom-out) using screen coords in `lParam`."""
        gw = gw or self.find_window()
        cx, cy = self._resolve_client_xy(x, y, gw=gw, normalized=normalized)
        sx, sy = self._client_to_screen(gw, cx, cy)
        msg = WM_MOUSEHWHEEL if horizontal else WM_MOUSEWHEEL
        wparam = make_wheel_wparam(int(delta) * WHEEL_DELTA, self._held_buttons)
        return self._post(gw.hwnd, msg, wparam, make_lparam(sx, sy))

    def mouse_move_rel(
        self,
        dx: int,
        dy: int = 0,
        *,
        steps: Optional[int] = None,
        step_delay: Optional[float] = None,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Send relative 3D camera movement via CloudShell's `MK_XBUTTON2` protocol.

        Encodes each clamped step `(sx, sy)` (`|sx|, |sy| <= 120`) as:
        `PostMessageW(hwnd, WM_MOUSEMOVE, MK_XBUTTON2, MAKELPARAM(center_x + sx, center_y + sy))`.
        """
        gw = gw or self.find_window()
        scaled_dx = int(round(dx * self.config.camera_scale))
        scaled_dy = int(round(dy * self.config.camera_scale))
        chunks = batch_relative_deltas(
            scaled_dx,
            scaled_dy,
            max_step=self.config.max_rel_step,
            steps=steps,
        )
        if not chunks:
            return True

        mid_x = gw.width // 2
        mid_y = gw.height // 2
        delay = self.config.rel_step_delay if step_delay is None else float(step_delay)
        ok = True
        for idx, (sx, sy) in enumerate(chunks):
            lp = make_lparam(mid_x + sx, mid_y + sy)
            ok = self._post(gw.hwnd, WM_MOUSEMOVE, MK_XBUTTON2, lp) and ok
            if delay > 0 and idx + 1 < len(chunks):
                self._sleep(delay)
        return ok

    def turn_camera(
        self,
        dx: int,
        dy: int = 0,
        steps: Optional[int] = 12,
        step_delay: Optional[float] = None,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Rotate 3D camera via relative `MK_XBUTTON2` deltas."""
        return self.mouse_move_rel(dx, dy, steps=steps, step_delay=step_delay, gw=gw)

    def key_down(self, key: Union[str, int], gw: Optional[GameWindow] = None) -> bool:
        """Send background `WM_KEYDOWN` with extended key bit 24 when required."""
        gw = gw or self.find_window()
        vk, extended = resolve_vk(key)
        return self._post(gw.hwnd, WM_KEYDOWN, vk, make_key_lparam(extended=extended, key_up=False))

    def key_up(self, key: Union[str, int], gw: Optional[GameWindow] = None) -> bool:
        """Send background `WM_KEYUP` with extended key bit 24 when required."""
        gw = gw or self.find_window()
        vk, extended = resolve_vk(key)
        return self._post(gw.hwnd, WM_KEYUP, vk, make_key_lparam(extended=extended, key_up=True))

    def key_press(
        self,
        key: Union[str, int],
        hold: float = 0.06,
        repeat: int = 1,
        interval: float = 0.10,
        after: float = 0.04,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Tap a key via background `WM_KEYDOWN` / `WM_KEYUP`."""
        gw = gw or self.find_window()
        ok = True
        for i in range(max(1, int(repeat))):
            d_ok = self.key_down(key, gw=gw)
            try:
                self._sleep(max(0.015, float(hold)))
            finally:
                u_ok = self.key_up(key, gw=gw)
            ok = ok and d_ok and u_ok
            if i + 1 < repeat:
                self._sleep(max(0.02, float(interval)))
            elif after > 0:
                self._sleep(float(after))
        return ok

    def press_key(
        self,
        key: Union[str, int],
        hold: float = 0.06,
        repeat: int = 1,
        interval: float = 0.10,
        gw: Optional[GameWindow] = None,
    ) -> bool:
        """Alias for `key_press()` matching `MumuExtras.press_key`."""
        return self.key_press(key, hold=hold, repeat=repeat, interval=interval, after=0.0, gw=gw)

    def hold_keys(self, keys: Iterable[Union[str, int]], duration: float, gw: Optional[GameWindow] = None) -> bool:
        """Hold multiple keys simultaneously via background `WM_KEYDOWN` and release cleanly."""
        gw = gw or self.find_window()
        keys_list = list(keys)
        ok = True
        try:
            for k in keys_list:
                ok = self.key_down(k, gw=gw) and ok
            self._sleep(max(0.0, float(duration)))
        finally:
            for k in reversed(keys_list):
                ok = self.key_up(k, gw=gw) and ok
        return ok

    def release_movement_keys(self, gw: Optional[GameWindow] = None) -> None:
        """Release all common movement/modifier keys cleanly."""
        gw = gw or self.find_window()
        for k in ("w", "a", "s", "d", "shift", "alt", "space", "ctrl"):
            self.key_up(k, gw=gw)

    def type_text(self, text: str, char_delay: float = 0.005, gw: Optional[GameWindow] = None) -> bool:
        """Send text via `WM_CHAR` with UTF-16 code units (including surrogate pairs) and `lParam=0`."""
        gw = gw or self.find_window()
        units = encode_utf16_units(text)
        ok = True
        for idx, unit in enumerate(units):
            ok = self._post(gw.hwnd, WM_CHAR, unit, 0) and ok
            if char_delay > 0 and idx + 1 < len(units):
                self._sleep(char_delay)
        return ok

    def input_text(self, text: str, gw: Optional[GameWindow] = None) -> bool:
        """Alias for `type_text()` matching `MumuExtras.input_text`."""
        return self.type_text(text, gw=gw)

    def info(self) -> Dict[str, object]:
        """Return diagnostic metadata about the CloudShell executable and running window."""
        exe = self.config.resolve_exe()
        out: Dict[str, object] = {
            "backend": "cloud",
            "exe": exe,
            "exe_exists": bool(exe and os.path.isfile(exe)),
            "url": self.config.url,
            "window_class": self.config.window_class,
            "title": self.config.title,
            "configured_size": [self.config.width, self.config.height],
            "user_data_dir": os.path.abspath(str(self.config.user_data_dir)),
            "log_file": os.path.abspath(str(self.config.log_file)),
            "running": False,
        }
        try:
            gw = self.find_window(auto_launch=False)
            t0 = time.perf_counter()
            img = self.capture(gw=gw)
            cap_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            out.update(
                {
                    "running": True,
                    "hwnd": gw.hwnd,
                    "pid": gw.pid,
                    "window_title": gw.title,
                    "client_rect": list(gw.client_rect),
                    "size": [gw.width, gw.height],
                    "capture_size": list(img.size),
                    "capture_ms": cap_ms,
                }
            )
        except RuntimeError:
            pass
        return out
