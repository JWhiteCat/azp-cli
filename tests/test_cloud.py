"""Offline unit tests for azp/cloud.py, Win32 PostMessageW protocol, scaling, and CLI wiring."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from typing import List, Tuple

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from azp import cloud, input_ctl as ctl, minigames, win


def _load_azp_cli():
    spec = importlib.util.spec_from_file_location("azp_cli_mod", os.path.join(ROOT, "azp.py"))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


azp_cli = _load_azp_cli()


def _make_fake_gw(
    width: int = 1280,
    height: int = 720,
    origin_x: int = 100,
    origin_y: int = 50,
    hwnd: int = 0x12345,
    pid: int = 4242,
) -> win.GameWindow:
    return win.GameWindow(
        hwnd=hwnd,
        pid=pid,
        title=cloud.DEFAULT_TITLE,
        window_rect=(origin_x - 8, origin_y - 31, origin_x + width + 8, origin_y + height + 8),
        client_rect=(origin_x, origin_y, origin_x + width, origin_y + height),
    )


def _test_url_presets() -> None:
    assert cloud.resolve_url(None) == "https://azurpromilia.manjuu.com/cloudgame"
    assert cloud.resolve_url("") == "https://azurpromilia.manjuu.com/cloudgame"
    assert cloud.resolve_url("manjuu") == "https://azurpromilia.manjuu.com/cloudgame"
    assert cloud.resolve_url("MANJUU") == "https://azurpromilia.manjuu.com/cloudgame"
    assert cloud.resolve_url("manjuu-alt") == "https://azurpromilia.manjuu.com/cloud/"
    assert cloud.resolve_url("163") == "https://cg.163.com"
    assert cloud.resolve_url("netease") == "https://cg.163.com"
    assert cloud.resolve_url("https://example.com/play") == "https://example.com/play"
    assert cloud.resolve_url("http://127.0.0.1:8080/test.html") == "http://127.0.0.1:8080/test.html"
    assert cloud.resolve_url("file:///C:/tmp/test.html") == "file:///C:/tmp/test.html"

    try:
        cloud.resolve_url("not_a_preset")
        raise AssertionError("Expected ValueError for invalid URL preset")
    except ValueError:
        pass


def _test_backend_aliases() -> None:
    assert cloud.normalize_backend_name("pc") == "pc"
    assert cloud.normalize_backend_name("mumu") == "mumu"
    assert cloud.normalize_backend_name("cloud") == "cloud"
    assert cloud.normalize_backend_name("cloud_web") == "cloud"
    assert cloud.normalize_backend_name("cloudweb") == "cloud"
    assert cloud.normalize_backend_name("cloud-web") == "cloud"
    assert cloud.normalize_backend_name("CLOUD_WEB") == "cloud"
    try:
        cloud.normalize_backend_name("invalid_backend")
        raise AssertionError("Expected ValueError for invalid backend")
    except ValueError:
        pass


def _test_exe_discovery_and_cli_args() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        dist_exe = os.path.join(tmpdir, "CloudShell", "dist", "azp-cloud.exe")
        rel_exe = os.path.join(tmpdir, "CloudShell", "build", "bin", "RelWithDebInfo", "azp-cloud.exe")
        bin_exe = os.path.join(tmpdir, "bin", "azp-cloud.exe")
        explicit_exe = os.path.join(tmpdir, "custom", "my-cloud.exe")

        os.makedirs(os.path.dirname(bin_exe), exist_ok=True)
        with open(bin_exe, "wb") as f:
            f.write(b"stub")

        old_env = os.environ.pop("AZP_CLOUD_EXE", None)
        try:
            assert cloud.find_cloud_exe(repo_root=tmpdir) == os.path.abspath(bin_exe)

            os.makedirs(os.path.dirname(rel_exe), exist_ok=True)
            with open(rel_exe, "wb") as f:
                f.write(b"stub")
            assert cloud.find_cloud_exe(repo_root=tmpdir) == os.path.abspath(rel_exe)

            os.makedirs(os.path.dirname(dist_exe), exist_ok=True)
            with open(dist_exe, "wb") as f:
                f.write(b"stub")
            assert cloud.find_cloud_exe(repo_root=tmpdir) == os.path.abspath(dist_exe)

            os.makedirs(os.path.dirname(explicit_exe), exist_ok=True)
            with open(explicit_exe, "wb") as f:
                f.write(b"stub")
            os.environ["AZP_CLOUD_EXE"] = explicit_exe
            assert cloud.find_cloud_exe(repo_root=tmpdir) == os.path.abspath(explicit_exe)
            assert cloud.find_cloud_exe(explicit_path=rel_exe, repo_root=tmpdir) == os.path.abspath(rel_exe)
        finally:
            if old_env is not None:
                os.environ["AZP_CLOUD_EXE"] = old_env
            else:
                os.environ.pop("AZP_CLOUD_EXE", None)

        cfg = cloud.CloudConfig(
            exe=dist_exe,
            url="163",
            width=1280,
            height=720,
            title="Custom Shell",
            user_data_dir=os.path.join(tmpdir, "ud"),
            log_file=os.path.join(tmpdir, "shell.log"),
            inject=(os.path.join(tmpdir, "a.js"), os.path.join(tmpdir, "b.js")),
            devtools=True,
            debug_port=9222,
            disable_webgl2=True,
            repo_root=tmpdir,
        )
        args = cfg.build_cli_args()
        assert args[0] == os.path.abspath(dist_exe), args
        assert args[args.index("--url") + 1] == "https://cg.163.com"
        assert args[args.index("--width") + 1] == "1280"
        assert args[args.index("--height") + 1] == "720"
        assert args[args.index("--title") + 1] == "Custom Shell"
        assert args[args.index("--user-data-dir") + 1] == os.path.abspath(os.path.join(tmpdir, "ud"))
        assert args[args.index("--log-file") + 1] == os.path.abspath(os.path.join(tmpdir, "shell.log"))
        assert args.count("--inject") == 2
        assert "--devtools" in args
        assert args[args.index("--debug-port") + 1] == "9222"
        assert "--disable-webgl2" in args


def _test_vk_and_extended_lparam() -> None:
    extended_keys = (
        "up", "down", "left", "right",
        "insert", "delete", "home", "end",
        "pageup", "pagedown", "ralt", "rctrl", "numpadenter",
    )
    for k in extended_keys:
        vk, ext = cloud.resolve_vk(k)
        assert ext is True, (k, vk, ext)
        lp_down = cloud.make_key_lparam(extended=ext, key_up=False)
        lp_up = cloud.make_key_lparam(extended=ext, key_up=True)
        assert (lp_down & (1 << 24)) != 0, (k, hex(lp_down))
        assert (lp_down & (1 << 31)) == 0
        assert (lp_up & (1 << 24)) != 0
        assert (lp_up & (1 << 31)) != 0

    normal_keys = ("w", "a", "s", "d", "space", "enter", "esc", "f1", "f5", "shift", "lctrl", "lalt", "1", "-")
    for k in normal_keys:
        vk, ext = cloud.resolve_vk(k)
        assert ext is False, (k, vk, ext)
        lp_down = cloud.make_key_lparam(extended=ext, key_up=False)
        assert (lp_down & (1 << 24)) == 0, (k, hex(lp_down))

    assert cloud.resolve_vk("w") == (ord("W"), False)
    assert cloud.resolve_vk("numpadenter") == (0x0D, True)
    assert cloud.resolve_vk("enter") == (0x0D, False)


def _test_utf16_wm_char() -> None:
    # ASCII + CJK + supplementary emoji (surrogate pair)
    units = cloud.encode_utf16_units("A星🚀")
    assert units == [0x0041, ord("星"), 0xD83D, 0xDE80], [hex(u) for u in units]

    posted: List[Tuple[int, int, int, int]] = []
    gw = _make_fake_gw()
    cb = cloud.CloudBackend(
        _post_message_fn=lambda h, m, w, l: (posted.append((h, m, w, l)) or True),
        _find_window_fn=lambda: gw,
        _sleep_fn=lambda _: None,
    )
    assert cb.type_text("Hi🚀") is True
    assert len(posted) == 4
    assert all(m == cloud.WM_CHAR and lp == 0 for _, m, _, lp in posted), posted
    assert [wp for _, _, wp, _ in posted] == [ord("H"), ord("i"), 0xD83D, 0xDE80]


def _test_relative_camera_batching_and_protocol() -> None:
    # Pure batching invariant checks
    for dx, dy in [(572, -195), (-900, 310), (45, 0), (0, -250), (120, -120)]:
        chunks = cloud.batch_relative_deltas(dx, dy, max_step=120)
        assert sum(x for x, _ in chunks) == dx, (dx, chunks)
        assert sum(y for _, y in chunks) == dy, (dy, chunks)
        assert all(-120 <= x <= 120 and -120 <= y <= 120 for x, y in chunks), chunks
    assert cloud.batch_relative_deltas(0, 0) == []

    # PostMessageW MK_XBUTTON2 center-offset verification on 1280x720 window
    posted: List[Tuple[int, int, int, int]] = []
    gw = _make_fake_gw(width=1280, height=720)
    cb = cloud.CloudBackend(
        _post_message_fn=lambda h, m, w, l: (posted.append((h, m, w, l)) or True),
        _find_window_fn=lambda: gw,
        _sleep_fn=lambda _: None,
    )
    assert cb.mouse_move_rel(350, -140, steps=4, step_delay=0.0) is True
    assert len(posted) == 4
    acc_x, acc_y = 0, 0
    for hwnd, msg, wp, lp in posted:
        assert hwnd == gw.hwnd
        assert msg == cloud.WM_MOUSEMOVE
        assert wp == cloud.MK_XBUTTON2
        px, py = cloud.unpack_lparam(lp)
        sx, sy = px - 640, py - 360
        assert -120 <= sx <= 120 and -120 <= sy <= 120
        acc_x += sx
        acc_y += sy
    assert (acc_x, acc_y) == (350, -140), (acc_x, acc_y)


def _test_coord_scaling_and_mouse_messages() -> None:
    posted: List[Tuple[int, int, int, int]] = []
    gw = _make_fake_gw(width=1280, height=720, origin_x=200, origin_y=100)
    cb = cloud.CloudBackend(
        _post_message_fn=lambda h, m, w, l: (posted.append((h, m, w, l)) or True),
        _find_window_fn=lambda: gw,
        _sleep_fn=lambda _: None,
    )

    # 1920x1080 -> 1280x720 scaling
    assert cb.scale_coords(960, 540, gw=gw) == (640, 360)
    assert cb.scale_coords(1245, 660, gw=gw) == (830, 440)
    assert cb.scale_coords(1920, 1080, gw=gw) == (1279, 719)
    assert cb.scale_coords(0.5, 0.25, gw=gw, normalized=True) == (640, 180)

    # move_to without held button passes MK_LBUTTON per InputBridge contract
    cb.move_to(960, 540, gw=gw)
    assert posted[-1] == (gw.hwnd, cloud.WM_MOUSEMOVE, cloud.MK_LBUTTON, cloud.make_lparam(640, 360))

    # click sends WM_LBUTTONDOWN + WM_LBUTTONUP with client coords
    posted.clear()
    cb.click(1245, 660, button="left", hold=0.0, gw=gw)
    assert [m for _, m, _, _ in posted] == [cloud.WM_LBUTTONDOWN, cloud.WM_LBUTTONUP]
    assert cloud.unpack_lparam(posted[0][3]) == (830, 440)

    # drag sends down -> moves (with MK_LBUTTON) -> up at destination
    posted.clear()
    cb.drag(480, 270, 1440, 810, duration=0.05, steps=4, button="left", gw=gw)
    assert posted[0][1] == cloud.WM_LBUTTONDOWN
    assert cloud.unpack_lparam(posted[0][3]) == (320, 180)
    moves = [p for p in posted[1:-1]]
    assert len(moves) == 4
    assert all(m == cloud.WM_MOUSEMOVE and (wp & cloud.MK_LBUTTON) != 0 for _, m, wp, _ in moves)
    assert cloud.unpack_lparam(moves[-1][3]) == (960, 540)
    assert posted[-1][1] == cloud.WM_LBUTTONUP

    # scroll sends WM_MOUSEWHEEL with SCREEN coords in lParam and signed delta*120 in wParam
    posted.clear()
    cb.scroll(-2, x=960, y=540, gw=gw)
    assert len(posted) == 1
    _, msg, wp, lp = posted[0]
    assert msg == cloud.WM_MOUSEWHEEL
    wheel_delta = (wp >> 16) & 0xFFFF
    if wheel_delta >= 0x8000:
        wheel_delta -= 0x10000
    assert wheel_delta == -240, wheel_delta
    assert cloud.unpack_lparam(lp) == (200 + 640, 100 + 360)


def _test_active_backend_dispatch_and_capture() -> None:
    posted: List[Tuple[int, int, int, int]] = []
    gw = _make_fake_gw(width=1280, height=720)
    synthetic = Image.new("RGB", (1280, 720), (40, 90, 160))

    cb = cloud.CloudBackend(
        _post_message_fn=lambda h, m, w, l: (posted.append((h, m, w, l)) or True),
        _find_window_fn=lambda: gw,
        _capture_fn=lambda _gw: synthetic.copy(),
        _sleep_fn=lambda _: None,
    )

    with cloud.use_cloud_backend(cb):
        # win.py hooks
        assert win.find_game_window().hwnd == gw.hwnd
        assert win.focus_game().hwnd == gw.hwnd
        priv = win.check_input_privilege()
        assert priv["can_send_input"] is True and priv["backend"] == "cloud"

        cap = win.capture_client()
        assert cap.size == (1280, 720)
        cap_roi = win.capture_client(roi=(0, 0, 1920, 540))
        assert cap_roi.size == (1280, 360)

        # input_ctl.py transparent dispatch (no SendInput / no focus theft)
        ctl.tap("up", hold=0.0, after=0.0)
        assert [m for _, m, _, _ in posted] == [cloud.WM_KEYDOWN, cloud.WM_KEYUP]
        assert posted[0][2] == 0x26 and (posted[0][3] & (1 << 24)) != 0

        posted.clear()
        ctl.mouse_click_client(960, 540, hold=0.0)
        assert [m for _, m, _, _ in posted] == [cloud.WM_LBUTTONDOWN, cloud.WM_LBUTTONUP]
        assert cloud.unpack_lparam(posted[0][3]) == (640, 360)

        posted.clear()
        ctl.type_text("ok")
        assert [(m, wp, lp) for _, m, wp, lp in posted] == [
            (cloud.WM_CHAR, ord("o"), 0),
            (cloud.WM_CHAR, ord("k"), 0),
        ]

        # FastROIGrabber routes through CloudBackend.capture() + 1920x1080 normalization
        with minigames.FastROIGrabber(0, 0, 400, 200, logical_roi=(100, 50, 500, 250), gw=gw) as grabber:
            rgb = grabber.grab_rgb()
            assert rgb.shape == (200, 400, 3) and isinstance(rgb, np.ndarray)

    # Context exited cleanly
    assert cloud.get_active_cloud_backend() is None


def _test_close_lifecycle() -> None:
    posted: List[Tuple[int, int, int, int]] = []
    terminated: List[int] = []
    gw = _make_fake_gw()
    state = {"alive": True}

    def _find():
        if state["alive"]:
            return gw
        raise RuntimeError("window gone")

    def _post(h, m, w, l):
        posted.append((h, m, w, l))
        if m == cloud.WM_CLOSE:
            state["alive"] = False
        return True

    cb = cloud.CloudBackend(
        _post_message_fn=_post,
        _find_window_fn=_find,
        _terminate_pid_fn=lambda pid: (terminated.append(pid) or True),
        _sleep_fn=lambda _: None,
    )
    res = cb.close(timeout=1.0)
    assert res["ok"] is True and res["closed_gracefully"] is True and not terminated
    assert posted == [(gw.hwnd, cloud.WM_CLOSE, 0, 0)]

    # Already closed -> no-op
    res2 = cb.close(timeout=1.0)
    assert res2["ok"] is True and res2["was_running"] is False


def _test_cli_parsing() -> None:
    parser = azp_cli.build_parser()

    a1 = parser.parse_args(
        [
            "--backend", "cloud_web",
            "--cloud-url", "163",
            "--cloud-width", "1920",
            "--cloud-height", "1080",
            "--cloud-devtools",
            "--cloud-debug-port", "9333",
            "--cloud-disable-webgl2",
            "--cloud-inject", "a.js",
            "--cloud-inject", "b.js",
            "--cloud-auto-launch",
            "status",
        ]
    )
    assert a1.backend == "cloud"
    cfg1 = azp_cli._build_cloud_config(a1)
    assert cfg1.url == "https://cg.163.com"
    assert (cfg1.width, cfg1.height) == (1920, 1080)
    assert cfg1.devtools is True and cfg1.debug_port == 9333 and cfg1.disable_webgl2 is True
    assert cfg1.auto_launch is True and cfg1.inject == ("a.js", "b.js")

    a2 = parser.parse_args(["--backend", "cloudweb", "click", "960", "540"])
    assert a2.backend == "cloud" and a2.cmd == "click"

    a3 = parser.parse_args(["cloud", "launch", "--url", "manjuu", "--width", "1280", "--height", "720", "--wait", "15"])
    assert a3.cmd == "cloud" and a3.cloud_cmd == "launch" and a3.wait == 15.0

    a4 = parser.parse_args(["cloud", "status"])
    assert a4.cmd == "cloud" and a4.cloud_cmd == "status"

    a5 = parser.parse_args(["cloud", "close", "--timeout", "3.0"])
    assert a5.cmd == "cloud" and a5.cloud_cmd == "close" and a5.timeout == 3.0

    a6 = parser.parse_args(["cloud", "build", "--clean"])
    assert a6.cmd == "cloud" and a6.cloud_cmd == "build" and a6.clean is True

    a7 = parser.parse_args(["--backend", "cloud", "hold", "w", "shift", "--sec", "1.5"])
    assert a7.cmd == "hold" and a7.keys == ["w", "shift"] and a7.sec == 1.5

    a8 = parser.parse_args(["--backend", "cloud", "type", "hello 蓝色星原"])
    assert a8.cmd == "type" and a8.text == "hello 蓝色星原"

    a9 = parser.parse_args(["--backend", "cloud", "find", "确认"])
    assert a9.cmd == "find" and a9.text == "确认"

    a10 = parser.parse_args(["--backend", "cloud", "click-text", "开始旅程", "--clicks", "2"])
    assert a10.cmd == "click-text" and a10.text == "开始旅程" and a10.clicks == 2

    a11 = parser.parse_args(["--backend", "cloud", "run", "--dir", "w", "--sec", "2.0"])
    assert a11.cmd == "run" and a11.sprint is True

    a12 = parser.parse_args(["--backend", "cloud", "walk", "--dir", "a", "--sec", "1.0"])
    assert a12.cmd == "walk" and a12.sprint is False


def run_tests() -> None:
    _test_url_presets()
    _test_backend_aliases()
    _test_exe_discovery_and_cli_args()
    _test_vk_and_extended_lparam()
    _test_utf16_wm_char()
    _test_relative_camera_batching_and_protocol()
    _test_coord_scaling_and_mouse_messages()
    _test_active_backend_dispatch_and_capture()
    _test_close_lifecycle()
    _test_cli_parsing()
    print("ALL_CLOUD_TESTS_PASSED")


if __name__ == "__main__":
    run_tests()
