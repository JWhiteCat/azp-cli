"""CLI entrypoint for AzurPromilia (蓝色星原) exploration & reusable automation."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from azp import actions, bg_play, coop, gamepad, input_ctl as ctl, minigames, mumu, nav, vision
from azp.win import capture_background, capture_client, check_input_privilege, find_game_window, focus_game, is_focused, user_idle_seconds


def _is_mumu(args: argparse.Namespace) -> bool:
    return getattr(args, "backend", "pc") == "mumu"


def _open_mumu(args: argparse.Namespace) -> mumu.MumuExtras:
    return mumu.open_mumu(
        mumu_path=getattr(args, "mumu_path", None),
        index=getattr(args, "mumu_index", None),
        adb_address=getattr(args, "mumu_addr", None),
        package_name=getattr(args, "mumu_pkg", mumu.DEFAULT_PACKAGE),
    )


def cmd_status(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        cmd_mumu_status(args)
        return
    gw = find_game_window()
    img = capture_client(gw=gw)
    hud = vision.check_hud_state(img)
    mm = vision.analyze_minimap(img)
    priv = check_input_privilege(gw)
    dots = vision.detect_red_dots(img)
    print(
        json.dumps(
            {
                "hwnd": gw.hwnd,
                "pid": gw.pid,
                "title": gw.title,
                "focused": is_focused(gw),
                "client_rect": gw.client_rect,
                "size": [gw.width, gw.height],
                "privilege": priv,
                "hud": hud,
                "in_home_ranch": coop.is_home_ranch(img),
                "minimap": mm,
                "red_dots": dots,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_focus(args: argparse.Namespace) -> None:
    gw = focus_game()
    print(json.dumps({"focused": is_focused(gw), "hwnd": gw.hwnd, "client_rect": gw.client_rect}))


def cmd_snap(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        cmd_mumu_snap(args)
        return
    gw = find_game_window()
    roi = tuple(args.roi) if args.roi else None
    img = capture_client(gw=gw, roi=roi, scale=args.scale, grid=args.grid, grid_step=args.grid_step, save_path=args.out)
    print(json.dumps({"saved": args.out, "size": list(img.size), "hud": vision.check_hud_state(img) if roi is None and args.scale == 1.0 else None}))


def cmd_key(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.press_key(args.key, hold=args.hold, repeat=args.repeat, interval=args.interval)
            print(json.dumps({"ok": ok, "backend": "mumu", "touch_enhanced": mm.input_available, "key": args.key, "repeat": args.repeat}))
        return
    focus_game()
    for i in range(args.repeat):
        ctl.tap(args.key, hold=args.hold, after=args.interval if i + 1 < args.repeat else 0.05)
    print(json.dumps({"ok": True, "key": args.key, "repeat": args.repeat}))


def cmd_click(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.tap(args.x, args.y, hold=args.hold, repeat=args.clicks)
            print(json.dumps({"ok": ok, "backend": "mumu", "touch_enhanced": mm.input_available, "x": args.x, "y": args.y, "clicks": args.clicks}))
        return
    gw = focus_game()
    ctl.mouse_click_client(
        args.x,
        args.y,
        button=args.button,
        clicks=args.clicks,
        hold=args.hold,
        hold_alt=args.alt,
        gw=gw,
    )
    print(json.dumps({"ok": True, "x": args.x, "y": args.y, "button": args.button, "clicks": args.clicks}))


def cmd_drag(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.swipe(args.x1, args.y1, args.x2, args.y2, duration=args.duration, steps=args.steps)
            print(json.dumps({"ok": ok, "backend": "mumu", "touch_enhanced": mm.input_available, "from": [args.x1, args.y1], "to": [args.x2, args.y2]}))
        return
    gw = focus_game()
    ctl.mouse_drag_client(args.x1, args.y1, args.x2, args.y2, duration=args.duration, steps=args.steps, button=args.button, gw=gw)
    print(json.dumps({"ok": True, "from": [args.x1, args.y1], "to": [args.x2, args.y2]}))


def cmd_scroll(args: argparse.Namespace) -> None:
    gw = focus_game()
    x, y = (args.at[0], args.at[1]) if args.at else (None, None)
    ctl.mouse_scroll(args.delta, x=x, y=y, gw=gw)
    print(json.dumps({"ok": True, "delta": args.delta, "at": args.at}))


def cmd_move(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.joystick_walk(direction=args.dir, duration=args.sec, sprint=args.sprint, jump=args.jump, spam_f=args.spam_f)
            print(json.dumps({"ok": ok, "backend": "mumu", "dir": args.dir, "sec": args.sec, "sprint": args.sprint, "spam_f": args.spam_f}))
        return
    actions.walk(direction=args.dir, duration=args.sec, sprint=args.sprint, jump=args.jump, spam_f=args.spam_f)
    print(json.dumps({"ok": True, "dir": args.dir, "sec": args.sec, "sprint": args.sprint, "spam_f": args.spam_f}))


def cmd_turn(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.turn_camera(args.dx, args.dy, steps=args.steps)
            print(json.dumps({"ok": ok, "backend": "mumu", "dx": args.dx, "dy": args.dy}))
        return
    actions.turn_camera(args.dx, args.dy, steps=args.steps)
    print(json.dumps({"ok": True, "dx": args.dx, "dy": args.dy}))


def cmd_interact(args: argparse.Namespace) -> None:
    actions.interact_spam(taps=args.taps, interval=args.interval)
    print(json.dumps({"ok": True, "taps": args.taps}))


def cmd_kibo_puzzle(args: argparse.Namespace) -> None:
    res = actions.kibo_puzzle(args.element, wait_after=args.wait)
    print(json.dumps(res, ensure_ascii=False))


def cmd_mount(args: argparse.Namespace) -> None:
    print(json.dumps(actions.toggle_mount(), ensure_ascii=False))


def cmd_dismiss(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.tap(960, 920, repeat=args.clicks, interval=args.interval)
            print(json.dumps({"ok": ok, "backend": "mumu", "clicks": args.clicks}, ensure_ascii=False))
        return
    print(json.dumps(actions.dismiss_reward(clicks=args.clicks, interval=args.interval), ensure_ascii=False))


def cmd_teleport(args: argparse.Namespace) -> None:
    res = actions.map_teleport(args.x, args.y, open_map_first=not args.map_open, timeout=args.timeout)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_scan360(args: argparse.Namespace) -> None:
    res = actions.scan360(args.out_dir, prefix=args.prefix, yaw_step=args.yaw_step, scale=args.scale)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_solve_zuzanka(args: argparse.Namespace) -> None:
    res = minigames.solve_zuzanka_ladder(
        target_x=args.target_x,
        tolerance_px=args.tol,
        lead_sec=args.lead,
        max_drops=args.max_drops,
        settle_sec=args.settle,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_capture(args: argparse.Namespace) -> None:
    print(json.dumps(actions.capture_kibo(throws=args.throws, wait_sec=args.wait), ensure_ascii=False))


def cmd_claim_daily(args: argparse.Namespace) -> None:
    r1 = actions.claim_daily_activity(task_clicks=args.tasks)
    r2 = actions.claim_travelogue_bp()
    print(json.dumps({"ok": True, "daily": r1, "bp": r2}, ensure_ascii=False, indent=2))


def cmd_claim_valley(args: argparse.Namespace) -> None:
    res = actions.claim_valley_treasure(lv_clicks=args.clicks)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_claim_events(args: argparse.Namespace) -> None:
    res = actions.claim_event_rewards(journal_clicks=args.clicks)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_world_map(args: argparse.Namespace) -> None:
    res = actions.switch_world_zone(args.zone, open_map_first=not args.map_open)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_exit_home(args: argparse.Namespace) -> None:
    res = actions.exit_home_ranch(timeout=args.timeout)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_mask_uid(args: argparse.Namespace) -> None:
    color = tuple(args.color)
    if args.out and len(args.paths) != 1:
        raise SystemExit("--out can only be used with a single input path")
    results = [vision.mask_uid_image(p, out_path=args.out, color=color) for p in args.paths]
    print(json.dumps({"ok": True, "count": len(results), "results": results}, ensure_ascii=False, indent=2))


def cmd_pois(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            img = mm.screencap()
    else:
        gw = find_game_window()
        img = capture_client(gw=gw)
    print(
        json.dumps(
            {
                "backend": "mumu" if _is_mumu(args) else "pc",
                "heading_deg": nav.estimate_player_heading(img),
                "hud": vision.check_hud_state(img),
                "pois": nav.detect_minimap_pois(img),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_solve_puzzle(args: argparse.Namespace) -> None:
    els = args.elements if args.elements else ("grass", "fire", "light", "water", "earth", "ice", "thunder")
    res = nav.solve_puzzle_auto(elements=els, wait_after=args.wait)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_goto_bearing(args: argparse.Namespace) -> None:
    res = nav.approach_poi(target_bearing=args.deg, walk_sec=args.sec, sprint=not args.no_sprint)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_bg_snap(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            t0 = time.perf_counter()
            img = mm.screencap(scale=args.scale, save_path=args.out)
            ms = round((time.perf_counter() - t0) * 1000.0, 2)
            print(json.dumps({"backend": "mumu", "saved": args.out, "size": list(img.size), "display_id": mm.get_display_id(), "capture_ms": ms}))
        return
    gw = find_game_window()
    img = capture_background(gw=gw, scale=args.scale, save_path=args.out)
    print(json.dumps({"saved": args.out, "size": list(img.size), "focused": is_focused(gw), "idle_sec": user_idle_seconds()}))


def cmd_mumu_list(args: argparse.Namespace) -> None:
    root = mumu.discover_mumu_root(args.mumu_path)
    ver = mumu.query_mumu_version(root)
    dll = mumu.get_mumu_ipc_dll_path(root)
    insts = mumu.list_mumu_instances(root)
    print(
        json.dumps(
            {
                "mumu_root": root,
                "version": ver,
                "input_version_ok": bool(ver and mumu.is_input_version_supported(ver)),
                "dll_path": dll,
                "instances": insts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_mumu_status(args: argparse.Namespace) -> None:
    with _open_mumu(args) as mm:
        info = mm.info()
        img = mm.screencap()
        info["hud"] = vision.check_hud_state(img)
        info["in_home_ranch"] = coop.is_home_ranch(img)
        info["minimap"] = vision.analyze_minimap(img)
        info["red_dots"] = vision.detect_red_dots(img)
        print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_mumu_snap(args: argparse.Namespace) -> None:
    with _open_mumu(args) as mm:
        roi = tuple(args.roi) if getattr(args, "roi", None) else None
        t0 = time.perf_counter()
        img = mm.screencap(
            roi=roi,
            scale=args.scale,
            grid=getattr(args, "grid", False),
            grid_step=getattr(args, "grid_step", 160),
            save_path=args.out,
        )
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        print(
            json.dumps(
                {
                    "backend": "mumu",
                    "saved": args.out,
                    "size": list(img.size),
                    "display_id": mm.get_display_id(),
                    "target_display_matched": mm.has_target_display,
                    "capture_ms": ms,
                    "hud": vision.check_hud_state(img) if roi is None and args.scale == 1.0 else None,
                },
                ensure_ascii=False,
            )
        )


def cmd_mumu_tap(args: argparse.Namespace) -> None:
    with _open_mumu(args) as mm:
        ok = mm.tap(args.x, args.y, hold=args.hold, repeat=args.repeat, interval=args.interval, contact=args.contact)
        print(
            json.dumps(
                {
                    "ok": ok,
                    "backend": "mumu",
                    "touch_enhanced": mm.input_available,
                    "display_id": mm.get_display_id(),
                    "x": args.x,
                    "y": args.y,
                    "contact": args.contact,
                    "repeat": args.repeat,
                }
            )
        )


def cmd_mumu_swipe(args: argparse.Namespace) -> None:
    with _open_mumu(args) as mm:
        ok = mm.swipe(
            args.x1,
            args.y1,
            args.x2,
            args.y2,
            duration=args.duration,
            steps=args.steps,
            contact=args.contact,
        )
        print(
            json.dumps(
                {
                    "ok": ok,
                    "backend": "mumu",
                    "touch_enhanced": mm.input_available,
                    "from": [args.x1, args.y1],
                    "to": [args.x2, args.y2],
                    "duration": args.duration,
                    "contact": args.contact,
                }
            )
        )


def cmd_mumu_key(args: argparse.Namespace) -> None:
    with _open_mumu(args) as mm:
        ok = mm.press_key(args.key, hold=args.hold, repeat=args.repeat, interval=args.interval)
        print(
            json.dumps(
                {
                    "ok": ok,
                    "backend": "mumu",
                    "touch_enhanced": mm.input_available,
                    "key": args.key,
                    "linux_code": mumu.resolve_linux_keycode(args.key),
                    "repeat": args.repeat,
                }
            )
        )


def cmd_mumu_text(args: argparse.Namespace) -> None:
    with _open_mumu(args) as mm:
        ok = mm.input_text(args.text)
        print(json.dumps({"ok": ok, "backend": "mumu", "touch_enhanced": mm.input_available, "length": len(args.text)}))


def cmd_mumu_test(args: argparse.Namespace) -> None:
    with _open_mumu(args) as mm:
        latencies = []
        for _ in range(args.frames):
            t0 = time.perf_counter()
            im = mm.screencap()
            latencies.append(round((time.perf_counter() - t0) * 1000.0, 2))
        info = mm.info()
        info["benchmark_frames"] = args.frames
        info["capture_latencies_ms"] = latencies
        info["avg_capture_ms"] = round(sum(latencies) / max(1, len(latencies)), 2)
        if args.out:
            im.save(args.out)
            info["saved"] = args.out
        print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_bg_test(args: argparse.Namespace) -> None:
    print(json.dumps(bg_play.probe_background_gamepad(), ensure_ascii=False, indent=2))


def cmd_bg_explore(args: argparse.Namespace) -> None:
    res = bg_play.bg_explore(
        steps=args.steps,
        step_sec=args.step_sec,
        snap_dir=args.snap_dir,
        require_unfocused=args.require_unfocused,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_coop_explore(args: argparse.Namespace) -> None:
    res = coop.coop_explore(
        steps=args.steps,
        idle_threshold=args.idle_sec,
        max_idle_wait=args.max_wait,
        step_sec=args.step_sec,
        snap_dir=args.snap_dir,
        auto_exit_home=not args.no_exit_home,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_combat(args: argparse.Namespace) -> None:
    res = actions.combat_burst(
        duration=args.sec,
        switch_chars=not args.no_switch,
        summon_kibo=not args.no_kibo,
        early_exit=not args.no_early_exit,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_dialog(args: argparse.Namespace) -> None:
    res = actions.dialog_advance(max_steps=args.max, interval=args.interval)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_explore(args: argparse.Namespace) -> None:
    res = actions.explore_loop(
        steps=args.steps,
        step_sec=args.step_sec,
        sprint=not args.no_sprint,
        snap_dir=args.snap_dir,
        yaw_bias=args.yaw_bias,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    env_idx = os.environ.get("AZP_MUMU_INDEX")
    p = argparse.ArgumentParser(prog="azp", description="AzurPromilia (蓝色星原) Open-World Automation CLI (PC & MuMu)")
    p.add_argument("--backend", choices=["pc", "mumu"], default=os.environ.get("AZP_BACKEND", "pc"), help="Execution backend: pc (Windows client) or mumu (MuMu IPC)")
    p.add_argument("--mumu-path", default=os.environ.get("AZP_MUMU_PATH"), help="MuMu installation root (auto-detected if omitted)")
    p.add_argument("--mumu-index", type=int, default=int(env_idx) if env_idx and env_idx.isdigit() else None, help="MuMu instance index (auto-detects com.manjuu.promilia if omitted)")
    p.add_argument("--mumu-addr", default=os.environ.get("AZP_MUMU_ADDR"), help="Optional MuMu ADB address (e.g. 127.0.0.1:16384 or emulator-5554)")
    p.add_argument("--mumu-pkg", default=os.environ.get("AZP_MUMU_PKG", mumu.DEFAULT_PACKAGE), help="Target Android package for MuMu display binding")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="Inspect game window, focus, HUD state, and minimap")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("focus", help="Focus AzurPromilia window")
    s.set_defaults(func=cmd_focus)

    s = sub.add_parser("snap", help="Capture 1920x1080 client screenshot")
    s.add_argument("--out", required=True, help="Output PNG path")
    s.add_argument("--roi", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--grid", action="store_true")
    s.add_argument("--grid-step", type=int, default=160)
    s.set_defaults(func=cmd_snap)

    s = sub.add_parser("key", help="Tap a hardware scan-code key")
    s.add_argument("key")
    s.add_argument("--hold", type=float, default=0.06)
    s.add_argument("--repeat", type=int, default=1)
    s.add_argument("--interval", type=float, default=0.12)
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("click", help="Click at 1920x1080 client coordinates")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s.add_argument("--button", default="left", choices=["left", "right", "middle"])
    s.add_argument("--clicks", type=int, default=1)
    s.add_argument("--hold", type=float, default=0.05)
    s.add_argument("--alt", action="store_true", help="Hold Alt to show cursor first")
    s.set_defaults(func=cmd_click)

    s = sub.add_parser("drag", help="Drag between client coordinates")
    s.add_argument("x1", type=float)
    s.add_argument("y1", type=float)
    s.add_argument("x2", type=float)
    s.add_argument("y2", type=float)
    s.add_argument("--duration", type=float, default=0.35)
    s.add_argument("--steps", type=int, default=18)
    s.add_argument("--button", default="left", choices=["left", "right", "middle"])
    s.set_defaults(func=cmd_drag)

    s = sub.add_parser("scroll", help="Mouse wheel scroll (+up/-down)")
    s.add_argument("delta", type=int)
    s.add_argument("--at", nargs=2, type=float, metavar=("X", "Y"))
    s.set_defaults(func=cmd_scroll)

    s = sub.add_parser("move", help="Move character via WASD")
    s.add_argument("--dir", default="w", help="Any combination of w/a/s/d")
    s.add_argument("--sec", type=float, default=2.0)
    s.add_argument("--sprint", action="store_true")
    s.add_argument("--jump", action="store_true")
    s.add_argument("--spam-f", action="store_true")
    s.set_defaults(func=cmd_move)

    s = sub.add_parser("turn", help="Rotate 3D camera via relative mouse movement")
    s.add_argument("--dx", type=int, required=True)
    s.add_argument("--dy", type=int, default=0)
    s.add_argument("--steps", type=int, default=12)
    s.set_defaults(func=cmd_turn)

    s = sub.add_parser("interact", help="Spam F to loot/open/interact")
    s.add_argument("--taps", type=int, default=5)
    s.add_argument("--interval", type=float, default=0.18)
    s.set_defaults(func=cmd_interact)

    s = sub.add_parser("kibo-puzzle", help="Open Kibo wheel (hold Q) and cast element puzzle skill")
    s.add_argument("element", choices=list(actions.KIBO_WHEEL_SLOTS.keys()))
    s.add_argument("--wait", type=float, default=2.8)
    s.set_defaults(func=cmd_kibo_puzzle)

    s = sub.add_parser("mount", help="Toggle Kibo mount (T)")
    s.set_defaults(func=cmd_mount)

    s = sub.add_parser("dismiss", help="Dismiss reward popups")
    s.add_argument("--clicks", type=int, default=2)
    s.add_argument("--interval", type=float, default=0.7)
    s.set_defaults(func=cmd_dismiss)

    s = sub.add_parser("teleport", help="Open map, click waypoint (x, y), click 传送, wait for HUD")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s.add_argument("--map-open", action="store_true", help="Skip pressing M if map is already open")
    s.add_argument("--timeout", type=float, default=18.0)
    s.set_defaults(func=cmd_teleport)

    s = sub.add_parser("scan360", help="Capture 4-direction 360-degree panorama")
    s.add_argument("--out-dir", required=True)
    s.add_argument("--prefix", default="scan")
    s.add_argument("--yaw-step", type=int, default=750)
    s.add_argument("--scale", type=float, default=0.75)
    s.set_defaults(func=cmd_scan360)

    s = sub.add_parser("solve-zuzanka", help="Real-time CV solver for 搭建祖赞卡之梯 totem stacking")
    s.add_argument("--target-x", type=float, default=960.0)
    s.add_argument("--tol", type=float, default=12.0)
    s.add_argument("--lead", type=float, default=0.23)
    s.add_argument("--max-drops", type=int, default=12)
    s.add_argument("--settle", type=float, default=2.05)
    s.set_defaults(func=cmd_solve_zuzanka)

    s = sub.add_parser("capture", help="Star-Bond capture Kibo (G -> LMB -> wait -> G)")
    s.add_argument("--throws", type=int, default=1)
    s.add_argument("--wait", type=float, default=3.5)
    s.set_defaults(func=cmd_capture)

    s = sub.add_parser("claim-daily", help="Auto-claim F5 Daily Activity tasks/chests and F2 Battle Pass")
    s.add_argument("--tasks", type=int, default=4)
    s.set_defaults(func=cmd_claim_daily)

    s = sub.add_parser("claim-valley", help="Auto-claim regional Exploration / 山谷秘藏 milestone rewards")
    s.add_argument("--clicks", type=int, default=2)
    s.set_defaults(func=cmd_claim_valley)

    s = sub.add_parser("claim-events", help="Safely claim F1 星临旅记 event rewards")
    s.add_argument("--clicks", type=int, default=2)
    s.set_defaults(func=cmd_claim_events)

    s = sub.add_parser("world-map", help="Switch region map via 世界地图 (charlulu / sprout_valley)")
    s.add_argument("zone", choices=list(actions.WORLD_ZONES.keys()))
    s.add_argument("--map-open", action="store_true", help="Skip pressing M if map is already open")
    s.set_defaults(func=cmd_world_map)

    s = sub.add_parser("exit-home", help="Exit Home Ranch (星临者的家园) via H -> 确认 and wait for open-world HUD")
    s.add_argument("--timeout", type=float, default=18.0)
    s.set_defaults(func=cmd_exit_home)

    s = sub.add_parser("mask-uid", help="Mask bottom-left UID region on screenshots with opaque solid box before git commit")
    s.add_argument("paths", nargs="+", help="One or more PNG/JPG screenshot paths")
    s.add_argument("--out", default=None, help="Optional output path (only valid for single input)")
    s.add_argument("--color", nargs=3, type=int, default=[18, 22, 26], metavar=("R", "G", "B"))
    s.set_defaults(func=cmd_mask_uid)

    s = sub.add_parser("pois", help="Detect player heading and clustered minimap POI bearings")
    s.set_defaults(func=cmd_pois)

    s = sub.add_parser("solve-puzzle", help="Auto-solve nearby Kibo puzzle by cycling elements until Q glow clears")
    s.add_argument("--elements", nargs="+", choices=list(actions.KIBO_WHEEL_SLOTS.keys()))
    s.add_argument("--wait", type=float, default=2.6)
    s.set_defaults(func=cmd_solve_puzzle)

    s = sub.add_parser("goto-bearing", help="Align to compass bearing (0=N,90=E,180=S,270=W) and sprint forward")
    s.add_argument("deg", type=float)
    s.add_argument("--sec", type=float, default=2.4)
    s.add_argument("--no-sprint", action="store_true")
    s.set_defaults(func=cmd_goto_bearing)

    s = sub.add_parser("bg-snap", help="Capture game screenshot in background via PrintWindow (PC) or MuMu IPC")
    s.add_argument("--out", required=True)
    s.add_argument("--scale", type=float, default=0.75)
    s.set_defaults(func=cmd_bg_snap)

    s = sub.add_parser("mumu-list", help="List MuMu emulator instances, version, and IPC DLL path")
    s.set_defaults(func=cmd_mumu_list)

    s = sub.add_parser("mumu-status", help="Inspect MuMu IPC screencap/touch enhancements, display_id, and HUD state")
    s.set_defaults(func=cmd_mumu_status)

    s = sub.add_parser("mumu-snap", help="Zero-focus-hijack MuMu IPC enhanced screenshot (nemu_capture_display)")
    s.add_argument("--out", required=True, help="Output PNG path")
    s.add_argument("--roi", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--grid", action="store_true")
    s.add_argument("--grid-step", type=int, default=160)
    s.set_defaults(func=cmd_mumu_snap)

    s = sub.add_parser("mumu-tap", help="Zero-focus-hijack MuMu IPC enhanced touch tap")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s.add_argument("--hold", type=float, default=mumu.TOUCH_HOLD_SEC)
    s.add_argument("--repeat", type=int, default=1)
    s.add_argument("--interval", type=float, default=0.08)
    s.add_argument("--contact", type=int, default=0, help="0-based finger slot (mapped to MuMu 1-based finger_id)")
    s.set_defaults(func=cmd_mumu_tap)

    s = sub.add_parser("mumu-swipe", help="Zero-focus-hijack MuMu IPC enhanced smooth swipe")
    s.add_argument("x1", type=float)
    s.add_argument("y1", type=float)
    s.add_argument("x2", type=float)
    s.add_argument("y2", type=float)
    s.add_argument("--duration", type=float, default=0.30)
    s.add_argument("--steps", type=int, default=None)
    s.add_argument("--contact", type=int, default=0)
    s.set_defaults(func=cmd_mumu_swipe)

    s = sub.add_parser("mumu-key", help="Zero-focus-hijack MuMu IPC enhanced Linux key press")
    s.add_argument("key", help="Key name (e.g. esc, back, space, w) or Android KeyEvent code")
    s.add_argument("--hold", type=float, default=0.05)
    s.add_argument("--repeat", type=int, default=1)
    s.add_argument("--interval", type=float, default=0.10)
    s.set_defaults(func=cmd_mumu_key)

    s = sub.add_parser("mumu-text", help="Zero-focus-hijack MuMu IPC text input (nemu_input_text)")
    s.add_argument("text")
    s.set_defaults(func=cmd_mumu_text)

    s = sub.add_parser("mumu-test", help="Benchmark MuMu IPC 截图增强 and verify 触控增强 capabilities")
    s.add_argument("--frames", type=int, default=5)
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_mumu_test)

    s = sub.add_parser("bg-test", help="Test whether ViGEmBus virtual Xbox controller moves camera while unfocused")
    s.set_defaults(func=cmd_bg_test)

    s = sub.add_parser("bg-explore", help="Zero-focus-hijack background exploration via PrintWindow + ViGEmBus")
    s.add_argument("--steps", type=int, default=10)
    s.add_argument("--step-sec", type=float, default=2.4)
    s.add_argument("--snap-dir", default=None)
    s.add_argument("--require-unfocused", action="store_true")
    s.set_defaults(func=cmd_bg_explore)

    s = sub.add_parser("coop-explore", help="Idle-aware cooperative exploration (runs only when user is physically idle)")
    s.add_argument("--steps", type=int, default=12)
    s.add_argument("--idle-sec", type=float, default=15.0)
    s.add_argument("--max-wait", type=float, default=90.0)
    s.add_argument("--step-sec", type=float, default=2.4)
    s.add_argument("--snap-dir", default=None)
    s.add_argument("--no-exit-home", action="store_true", help="Do not auto-exit Home Ranch when detected")
    s.set_defaults(func=cmd_coop_explore)

    s = sub.add_parser("combat", help="Run active combat rotation")
    s.add_argument("--sec", type=float, default=6.0)
    s.add_argument("--no-switch", action="store_true")
    s.add_argument("--no-kibo", action="store_true")
    s.add_argument("--no-early-exit", action="store_true", help="Keep fighting for full duration (good for multi-wave rifts)")
    s.set_defaults(func=cmd_combat)

    s = sub.add_parser("dialog", help="Advance dialogue/popups back to open-world HUD")
    s.add_argument("--max", type=int, default=20)
    s.add_argument("--interval", type=float, default=0.45)
    s.set_defaults(func=cmd_dialog)

    s = sub.add_parser("explore", help="Autonomous open-world patrol, loot, and combat loop")
    s.add_argument("--steps", type=int, default=12)
    s.add_argument("--step-sec", type=float, default=2.6)
    s.add_argument("--no-sprint", action="store_true")
    s.add_argument("--snap-dir", default=None)
    s.add_argument("--yaw-bias", type=int, default=0)
    s.set_defaults(func=cmd_explore)

    return p


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
