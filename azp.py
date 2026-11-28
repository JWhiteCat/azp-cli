"""CLI entrypoint for AzurPromilia (蓝色星原) exploration & reusable automation."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from PIL import Image

from azp import actions, bg_play, cloud, coop, detector, gamepad, input_ctl as ctl, minigames, mumu, nav, navmesh, ocr_tpl, vision
from azp.win import capture_background, capture_client, check_input_privilege, find_game_window, focus_game, is_focused, user_idle_seconds


def _is_mumu(args: argparse.Namespace) -> bool:
    return getattr(args, "backend", "pc") == "mumu"


def _is_cloud(args: argparse.Namespace) -> bool:
    return getattr(args, "backend", "pc") == "cloud"


def _build_cloud_config(args: argparse.Namespace) -> cloud.CloudConfig:
    def _pick(sub_attr: str, global_attr: str):
        val = getattr(args, sub_attr, None)
        if val is not None and val is not False:
            return val
        return getattr(args, global_attr, None)

    inject_list = list(getattr(args, "cloud_inject", None) or [])
    sub_inject = getattr(args, "inject", None) or []
    inject_list.extend(sub_inject)

    overrides = {
        "exe": _pick("exe", "cloud_exe"),
        "url": _pick("url", "cloud_url"),
        "width": _pick("width", "cloud_width"),
        "height": _pick("height", "cloud_height"),
        "title": _pick("title", "cloud_title"),
        "user_data_dir": _pick("user_data", "cloud_user_data"),
        "log_file": _pick("log_file", "cloud_log_file"),
        "debug_port": _pick("debug_port", "cloud_debug_port"),
        "camera_scale": getattr(args, "cloud_camera_scale", None),
    }
    if getattr(args, "devtools", False) or getattr(args, "cloud_devtools", False):
        overrides["devtools"] = True
    if getattr(args, "disable_webgl2", False) or getattr(args, "cloud_disable_webgl2", False):
        overrides["disable_webgl2"] = True
    if getattr(args, "cloud_auto_launch", False):
        overrides["auto_launch"] = True
    if inject_list:
        overrides["inject"] = tuple(inject_list)
    return cloud.CloudConfig.from_env(**overrides)


def _open_cloud(args: argparse.Namespace) -> cloud.CloudBackend:
    return cloud.CloudBackend(_build_cloud_config(args))


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
    t0 = time.perf_counter()
    img = capture_client(gw=gw)
    cap_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    hud = vision.check_hud_state(img)
    mm = vision.analyze_minimap(img)
    priv = check_input_privilege(gw)
    dots = vision.detect_red_dots(img)
    payload = {
        "backend": getattr(args, "backend", "pc"),
        "hwnd": gw.hwnd,
        "pid": gw.pid,
        "title": gw.title,
        "focused": is_focused(gw),
        "client_rect": gw.client_rect,
        "size": [gw.width, gw.height],
        "capture_ms": cap_ms,
        "privilege": priv,
        "hud": hud,
        "in_home_ranch": coop.is_home_ranch(img),
        "minimap": mm,
        "red_dots": dots,
    }
    if _is_cloud(args):
        cb = cloud.get_active_cloud_backend()
        if cb is not None:
            payload["url"] = cb.config.url
            payload["exe"] = cb.config.resolve_exe()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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
    print(json.dumps({"ok": True, "backend": args.backend, "key": args.key, "repeat": args.repeat}))


def cmd_hold(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            try:
                for k in args.keys:
                    mm.key_down(k)
                time.sleep(max(0.0, args.sec))
            finally:
                for k in reversed(args.keys):
                    mm.key_up(k)
        print(json.dumps({"ok": True, "backend": "mumu", "keys": args.keys, "sec": args.sec}))
        return
    focus_game()
    ctl.hold_keys(args.keys, args.sec)
    print(json.dumps({"ok": True, "backend": args.backend, "keys": args.keys, "sec": args.sec}))


def cmd_type(args: argparse.Namespace) -> None:
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.input_text(args.text)
            print(json.dumps({"ok": ok, "backend": "mumu", "length": len(args.text)}))
        return
    focus_game()
    ctl.type_text(args.text, char_delay=args.char_delay)
    print(json.dumps({"ok": True, "backend": args.backend, "length": len(args.text)}))


def _match_ocr_text(img: Image.Image, query: str, roi=None, min_score: float = 0.48, exact: bool = False):
    res = ocr_tpl.ocr_roi(img, roi=roi, min_score=min_score)
    q = query.strip().lower()
    matches = []
    for item in res["items"]:
        txt = str(item["text"]).strip()
        low = txt.lower()
        if (low == q) if exact else (q in low):
            matches.append(item)
    return res, matches


def cmd_find(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    roi = tuple(args.roi) if args.roi else None
    res, matches = _match_ocr_text(img, args.text, roi=roi, min_score=args.min_score, exact=args.exact)
    print(
        json.dumps(
            {
                "ok": len(matches) > 0,
                "query": args.text,
                "count": len(matches),
                "matches": matches,
                "elapsed_ms": res["elapsed_ms"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_click_text(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    roi = tuple(args.roi) if args.roi else None
    res, matches = _match_ocr_text(img, args.text, roi=roi, min_score=args.min_score, exact=args.exact)
    if not matches or args.index >= len(matches):
        print(
            json.dumps(
                {
                    "ok": False,
                    "query": args.text,
                    "count": len(matches),
                    "matches": matches,
                    "elapsed_ms": res["elapsed_ms"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    target = matches[args.index]
    cx, cy = float(target["center"][0]), float(target["center"][1])
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            ok = mm.tap(cx, cy, hold=args.hold, repeat=args.clicks)
        print(json.dumps({"ok": ok, "backend": "mumu", "matched": target, "clicked": [cx, cy]}, ensure_ascii=False, indent=2))
        return
    gw = focus_game()
    ctl.mouse_click_client(cx, cy, button=args.button, clicks=args.clicks, hold=args.hold, gw=gw)
    print(json.dumps({"ok": True, "backend": args.backend, "matched": target, "clicked": [cx, cy]}, ensure_ascii=False, indent=2))


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
    if args.select:
        print(json.dumps(actions.select_mount(args.select), ensure_ascii=False))
    else:
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


def cmd_solve_race(args: argparse.Namespace) -> None:
    res = minigames.solve_mount_race(
        max_sec=args.max_sec,
        gain=args.gain,
        restart=args.restart,
        sprint=not args.no_sprint,
        model_path=args.model,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_solve_cabbage(args: argparse.Namespace) -> None:
    res = minigames.solve_cabbage_run(
        max_sec=args.max_sec,
        gain=args.gain,
        model_path=args.model,
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


def cmd_claim_codex(args: argparse.Namespace) -> None:
    res = actions.claim_codex_rewards(clicks=args.clicks)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_claim_journal(args: argparse.Namespace) -> None:
    tabs = tuple(args.tabs) if args.tabs else ("home", "adventure", "companion", "trial")
    res = actions.claim_journal_rewards(
        tabs=tabs,
        clicks_per_tab=args.clicks,
        claim_milestones=not args.no_milestones,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_build_facility(args: argparse.Namespace) -> None:
    res = actions.build_booster_facility(wait_after=args.wait)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_world_map(args: argparse.Namespace) -> None:
    res = actions.switch_world_zone(args.zone, open_map_first=not args.map_open)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_exit_home(args: argparse.Namespace) -> None:
    res = actions.exit_home_ranch(timeout=args.timeout)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_home_harvest(args: argparse.Namespace) -> None:
    res = actions.home_harvest(return_to_world=not args.stay, timeout=args.timeout)
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
    res = nav.approach_poi(
        target_bearing=args.deg,
        walk_sec=args.sec,
        sprint=not args.no_sprint,
        spam_f=not args.no_spam_f,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_fly_bearing(args: argparse.Namespace) -> None:
    res = nav.fly_to_bearing(
        target_bearing=args.deg,
        fly_sec=args.sec,
        climb_sec=args.climb,
        boost=not args.no_boost,
        land=args.land,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_land(args: argparse.Namespace) -> None:
    res = nav.safe_land(max_sec=args.max_sec, dismount=not args.hover)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_hunt_pois(args: argparse.Namespace) -> None:
    res = nav.hunt_pois(
        steps=args.steps,
        max_dist=args.max_dist,
        allow_fly=not args.no_fly,
        idle_sec=args.idle_sec,
        snap_dir=args.snap_dir,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_nav_bake(args: argparse.Namespace) -> None:
    im = Image.open(args.map)
    cmap = navmesh.CostMap25D.bake_from_image(
        im,
        name=args.name,
        cell_size=args.cell,
        source_image=args.map,
        px_per_sec=args.px_per_sec,
    )
    saved = cmap.save(cache_dir=args.cache_dir)
    if args.preview:
        cmap.render_debug_overlay(base_image=im, mode="ground", out_path=args.preview)
    print(
        json.dumps(
            {
                "ok": True,
                "name": cmap.name,
                "grid_shape": [cmap.gh, cmap.gw],
                "cell_size": cmap.cell_size,
                "stats": cmap.terrain_stats(),
                "saved": saved,
                "preview": args.preview,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_nav_plan(args: argparse.Namespace) -> None:
    cmap = navmesh.CostMap25D.load(args.name, cache_dir=args.cache_dir)
    plan = cmap.find_path(
        (args.start[0], args.start[1]),
        (args.goal[0], args.goal[1]),
        mode=args.mode,
        smooth=not args.no_smooth,
    )
    if args.preview:
        base_im = Image.open(cmap.source_image) if (cmap.source_image and os.path.isfile(cmap.source_image)) else None
        cmap.render_debug_overlay(base_image=base_im, path_result=plan, mode=args.mode, out_path=args.preview)
        plan["preview"] = args.preview
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def cmd_nav_obstacle(args: argparse.Namespace) -> None:
    cmap = navmesh.CostMap25D.load(args.name, cache_dir=args.cache_dir)
    mem_path = os.path.join(args.cache_dir, f"{cmap.name}_memory.json")
    if args.op == "add":
        if args.xy is None:
            raise SystemExit("nav-obstacle add requires --xy X Y")
        rec = cmap.memory.record_obstacle(
            args.xy[0],
            args.xy[1],
            radius=args.radius,
            penalty=args.penalty,
            reason=args.reason,
        )
        cmap.memory.save(mem_path)
        print(json.dumps({"ok": True, "op": "add", "record": rec.__dict__, "total": len(cmap.memory.obstacles)}, ensure_ascii=False, indent=2))
    elif args.op == "portal":
        if args.xy is None or args.dst is None:
            raise SystemExit("nav-obstacle portal requires --xy X1 Y1 --dst X2 Y2")
        edge = cmap.memory.add_portal(
            args.xy[0], args.xy[1], args.dst[0], args.dst[1], action=args.action, cost=args.cost, label=args.reason
        )
        cmap.memory.save(mem_path)
        print(json.dumps({"ok": True, "op": "portal", "edge": edge.__dict__, "total": len(cmap.memory.portals)}, ensure_ascii=False, indent=2))
    elif args.op == "clear":
        cmap.memory.clear()
        cmap.memory.save(mem_path)
        print(json.dumps({"ok": True, "op": "clear"}, ensure_ascii=False))
    else:
        print(json.dumps({"ok": True, "op": "list", "memory": cmap.memory.to_dict()}, ensure_ascii=False, indent=2))


def cmd_nav_localize(args: argparse.Namespace) -> None:
    cmap = navmesh.CostMap25D.load(args.name, cache_dir=args.cache_dir)
    if args.frame:
        frame = Image.open(args.frame)
    elif _is_mumu(args):
        with _open_mumu(args) as mm:
            frame = mm.screencap()
    else:
        gw = find_game_window()
        frame = capture_client(gw=gw)
    hint = (args.hint[0], args.hint[1]) if args.hint else None
    res = cmap.localize_minimap(frame, hint_xy=hint, search_radius_px=args.radius)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_nav_goto(args: argparse.Namespace) -> None:
    cmap = navmesh.CostMap25D.load(args.name, cache_dir=args.cache_dir)
    start_xy = (args.start[0], args.start[1]) if args.start else None
    res = navmesh.navigate_with_costmap(
        cmap,
        goal_xy=(args.goal[0], args.goal[1]),
        start_xy=start_xy,
        mode=args.mode,
        max_legs=args.max_legs,
        arrive_px=args.arrive_px,
        sprint=not args.no_sprint,
        spam_f=not args.no_spam_f,
        mount=args.mount,
        cache_dir=args.cache_dir,
    )
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
    t0 = time.perf_counter()
    img = capture_background(gw=gw, scale=args.scale, save_path=args.out)
    ms = round((time.perf_counter() - t0) * 1000.0, 2)
    print(
        json.dumps(
            {
                "backend": getattr(args, "backend", "pc"),
                "saved": args.out,
                "size": list(img.size),
                "focused": is_focused(gw),
                "capture_ms": ms,
                "idle_sec": user_idle_seconds(),
            }
        )
    )


def cmd_cloud(args: argparse.Namespace) -> None:
    action = args.cloud_cmd
    if action == "build":
        build_script = os.path.join(cloud.ROOT_DIR, "CloudShell", "build.py")
        if not os.path.isfile(build_script):
            raise SystemExit(f"CloudShell build script not found: {build_script}")
        cmd = [sys.executable, build_script]
        if getattr(args, "arch", None):
            cmd.extend(["--arch", args.arch])
        if getattr(args, "debug", False):
            cmd.extend(["--config", "Debug"])
        elif getattr(args, "config", None):
            cmd.extend(["--config", args.config])
        if getattr(args, "webview2_sdk_dir", None):
            cmd.extend(["--webview2-sdk-dir", args.webview2_sdk_dir])
        if getattr(args, "clean", False):
            cmd.append("--clean")
        if getattr(args, "extra", None):
            cmd.extend(args.extra)
        proc = subprocess.run(cmd, cwd=cloud.ROOT_DIR)
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)
        exe = cloud.find_cloud_exe(explicit_path=getattr(args, "cloud_exe", None))
        print(json.dumps({"ok": True, "action": "build", "exe": exe, "exe_exists": bool(exe)}, ensure_ascii=False, indent=2))
        return

    cb = _open_cloud(args)
    if action == "launch":
        gw = cb.launch(wait_timeout=args.wait, reuse_existing=not args.no_reuse)
        print(
            json.dumps(
                {
                    "ok": True,
                    "action": "launch",
                    "hwnd": gw.hwnd,
                    "pid": gw.pid,
                    "title": gw.title,
                    "size": [gw.width, gw.height],
                    "client_rect": list(gw.client_rect),
                    "url": cb.config.url,
                    "exe": cb.config.resolve_exe(),
                    "user_data_dir": os.path.abspath(str(cb.config.user_data_dir)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if action == "close":
        res = cb.close(timeout=args.timeout)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    # action == "status"
    info = cb.info()
    if info.get("running"):
        gw = cb.find_window(auto_launch=False)
        img = cb.capture(gw=gw)
        info["focused"] = is_focused(gw)
        info["hud"] = vision.check_hud_state(img)
        info["in_home_ranch"] = coop.is_home_ranch(img)
        info["minimap"] = vision.analyze_minimap(img)
        info["red_dots"] = vision.detect_red_dots(img)
    print(json.dumps(info, ensure_ascii=False, indent=2))


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


def _load_frame_or_capture(args: argparse.Namespace) -> Image.Image:
    frame = getattr(args, "frame", None)
    if frame:
        return Image.open(frame).convert("RGB")
    if _is_mumu(args):
        with _open_mumu(args) as mm:
            return mm.screencap()
    gw = find_game_window()
    return capture_client(gw=gw)


def cmd_ocr(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    roi = tuple(args.roi) if args.roi else None
    res = ocr_tpl.ocr_roi(img, roi=roi, min_score=args.min_score)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_read_f(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    res = ocr_tpl.read_interact_prompt(img)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_hud_markers(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    kinds = args.kinds if args.kinds else ("chest", "challenge", "wumi", "egg")
    res = ocr_tpl.detect_floating_markers(img, kinds=kinds, use_ocr=not args.no_ocr, threshold=args.threshold)
    print(json.dumps({"ok": True, "count": len(res), "markers": res}, ensure_ascii=False, indent=2))


def cmd_map_pois(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    res = ocr_tpl.detect_world_map_pois(img, use_ocr=args.ocr)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_check_anomaly(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    res = ocr_tpl.detect_screen_anomaly(img, use_ocr=not args.no_ocr)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_recover(args: argparse.Namespace) -> None:
    res = ocr_tpl.recover_anomaly(
        auto_confirm_repair=args.auto_confirm,
        dry_run=args.dry_run,
        frame_path=args.frame,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_yolo_detect(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    det = detector.HybridDetector(model_path=args.model, conf_thres=args.conf)
    res = det.detect(img, classes=args.classes, use_ocr=args.ocr)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_yolo_bench(args: argparse.Namespace) -> None:
    img = _load_frame_or_capture(args)
    if args.model and os.path.isfile(args.model):
        yd = detector.YoloOnnxDetector(model_path=args.model, conf_thres=args.conf)
        res = yd.benchmark(img, runs=args.runs)
    else:
        det = detector.HybridDetector(model_path=args.model, conf_thres=args.conf)
        rgb = ocr_tpl._to_rgb_1080(img)
        times = []
        last_cnt = 0
        for _ in range(max(1, args.runs)):
            dets, ms = det.detect_rgb(rgb)
            times.append(ms)
            last_cnt = len(dets)
        res = {
            "ok": True,
            "backend": det.backend_name,
            "model_found": False,
            "model_path": args.model,
            "ort_providers": detector.available_providers(),
            "runs": len(times),
            "avg_ms": round(sum(times) / len(times), 2),
            "est_fps": round(1000.0 / max(0.1, sum(times) / len(times)), 1),
            "last_count": last_cnt,
        }
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_yolo_init_dataset(args: argparse.Namespace) -> None:
    res = detector.init_yolo_dataset(
        out_dir=args.out,
        snaps_dir=args.snaps,
        val_ratio=args.val_ratio,
        max_images=args.max_images,
        mask_uid=not args.no_mask_uid,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_yolo_prelabel(args: argparse.Namespace) -> None:
    res = detector.prelabel_yolo_dataset(
        dataset_dir=args.dataset,
        min_score=args.min_score,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    env_idx = os.environ.get("AZP_MUMU_INDEX")
    env_backend = cloud.normalize_backend_name(os.environ.get("AZP_BACKEND", "pc"))
    p = argparse.ArgumentParser(
        prog="azp",
        description="AzurPromilia (蓝色星原) Open-World Automation CLI (PC, MuMu & Built-in CloudShell)",
    )
    p.add_argument(
        "--backend",
        type=cloud.normalize_backend_name,
        choices=["pc", "mumu", "cloud"],
        default=env_backend,
        help="Execution backend: pc (Windows client), mumu (MuMu IPC), or cloud (built-in WebView2 CloudShell; aliases: cloud_web, cloudweb)",
    )
    p.add_argument("--mumu-path", default=os.environ.get("AZP_MUMU_PATH"), help="MuMu installation root (auto-detected if omitted)")
    p.add_argument("--mumu-index", type=int, default=int(env_idx) if env_idx and env_idx.isdigit() else None, help="MuMu instance index (auto-detects com.manjuu.promilia if omitted)")
    p.add_argument("--mumu-addr", default=os.environ.get("AZP_MUMU_ADDR"), help="Optional MuMu ADB address (e.g. 127.0.0.1:16384 or emulator-5554)")
    p.add_argument("--mumu-pkg", default=os.environ.get("AZP_MUMU_PKG", mumu.DEFAULT_PACKAGE), help="Target Android package for MuMu display binding")
    p.add_argument("--cloud-exe", default=None, help="Path to azp-cloud.exe (auto-detected or AZP_CLOUD_EXE)")
    p.add_argument("--cloud-url", default=None, help="Cloud game URL or preset (manjuu, manjuu-alt, 163, or http(s)://...)")
    p.add_argument("--cloud-width", type=int, default=None, help="CloudShell physical client width (default 1280)")
    p.add_argument("--cloud-height", type=int, default=None, help="CloudShell physical client height (default 720)")
    p.add_argument("--cloud-title", default=None, help="CloudShell window title (default AZP CloudShell)")
    p.add_argument("--cloud-user-data", default=None, help="CloudShell WebView2 user-data directory")
    p.add_argument("--cloud-log-file", default=None, help="CloudShell log file path")
    p.add_argument("--cloud-devtools", action="store_true", help="Open WebView2 DevTools when launching CloudShell")
    p.add_argument("--cloud-debug-port", type=int, default=None, help="Enable WebView2 remote debugging port")
    p.add_argument("--cloud-disable-webgl2", action="store_true", help="Disable WebGL2 inside CloudShell (optional compatibility switch)")
    p.add_argument("--cloud-inject", action="append", default=None, help="Repeatable JS file injected into CloudShell on document creation")
    p.add_argument("--cloud-auto-launch", action="store_true", help="Automatically launch azp-cloud.exe if window is not running")
    p.add_argument("--cloud-camera-scale", type=float, default=None, help="Multiplier for relative 3D camera movement in cloud mode (default 1.0)")
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

    s = sub.add_parser("key", help="Tap a hardware scan-code / CloudShell VK key")
    s.add_argument("key")
    s.add_argument("--hold", type=float, default=0.06)
    s.add_argument("--repeat", type=int, default=1)
    s.add_argument("--interval", type=float, default=0.12)
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("hold", help="Hold one or more keys simultaneously for a duration")
    s.add_argument("keys", nargs="+", help="One or more keys to hold (e.g. w shift)")
    s.add_argument("--sec", type=float, default=1.0)
    s.set_defaults(func=cmd_hold)

    s = sub.add_parser("type", help="Type text into active backend (WM_CHAR on CloudShell, Unicode SendInput on PC, IPC on MuMu)")
    s.add_argument("text")
    s.add_argument("--char-delay", type=float, default=0.005)
    s.set_defaults(func=cmd_type)

    s = sub.add_parser("find", help="Find text on screen via RapidOCR and return 1920x1080 boxes/centers")
    s.add_argument("text", help="Substring (or exact text with --exact) to search for")
    s.add_argument("--frame", default=None, help="Optional offline screenshot path")
    s.add_argument("--roi", nargs=4, type=int, default=None, metavar=("X1", "Y1", "X2", "Y2"))
    s.add_argument("--min-score", type=float, default=0.48)
    s.add_argument("--exact", action="store_true")
    s.set_defaults(func=cmd_find)

    s = sub.add_parser("click-text", help="Find text via RapidOCR and click its center")
    s.add_argument("text", help="Substring (or exact text with --exact) to click")
    s.add_argument("--frame", default=None, help="Optional offline screenshot path")
    s.add_argument("--roi", nargs=4, type=int, default=None, metavar=("X1", "Y1", "X2", "Y2"))
    s.add_argument("--min-score", type=float, default=0.48)
    s.add_argument("--exact", action="store_true")
    s.add_argument("--index", type=int, default=0, help="Match index to click (default 0)")
    s.add_argument("--button", default="left", choices=["left", "right", "middle"])
    s.add_argument("--clicks", type=int, default=1)
    s.add_argument("--hold", type=float, default=0.05)
    s.set_defaults(func=cmd_click_text)

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

    s = sub.add_parser("walk", help="Walk character via WASD (alias of move without sprint)")
    s.add_argument("--dir", default="w", help="Any combination of w/a/s/d")
    s.add_argument("--sec", type=float, default=2.0)
    s.add_argument("--jump", action="store_true")
    s.add_argument("--spam-f", action="store_true")
    s.set_defaults(func=cmd_move, sprint=False)

    s = sub.add_parser("run", help="Sprint character via WASD + Shift (alias of move --sprint)")
    s.add_argument("--dir", default="w", help="Any combination of w/a/s/d")
    s.add_argument("--sec", type=float, default=2.0)
    s.add_argument("--jump", action="store_true")
    s.add_argument("--spam-f", action="store_true")
    s.set_defaults(func=cmd_move, sprint=True)

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

    s = sub.add_parser("mount", help="Toggle Kibo mount (T) or select a specific mount from hold-T wheel")
    s.add_argument("--select", choices=list(actions.MOUNT_WHEEL_SLOTS.keys()), default=None, help="Select specific mount (bird/wolf) via hold-T wheel")
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

    s = sub.add_parser("solve-race", help="Real-time 25Hz BitBlt steering solver for 坐骑挑战 (ONNX YOLO or anti-crystal CV)")
    s.add_argument("--max-sec", type=float, default=65.0)
    s.add_argument("--gain", type=float, default=0.28)
    s.add_argument("--restart", action="store_true", help="Press P to restart race before steering")
    s.add_argument("--no-sprint", action="store_true")
    s.add_argument("--model", default=None, help="Optional ONNX YOLO model path (auto-uses models/azp_yolo.onnx if present)")
    s.set_defaults(func=cmd_solve_race)

    s = sub.add_parser("solve-cabbage", help="Real-time 25Hz BitBlt seed-chasing solver for 变身菜鸡")
    s.add_argument("--max-sec", type=float, default=45.0)
    s.add_argument("--gain", type=float, default=0.25)
    s.add_argument("--model", default=None, help="Optional ONNX YOLO model path")
    s.set_defaults(func=cmd_solve_cabbage)

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

    s = sub.add_parser("claim-codex", help="Claim Esc -> 星临图志 -> 调查奖励 star-level Codex rewards")
    s.add_argument("--clicks", type=int, default=3)
    s.set_defaults(func=cmd_claim_codex)

    s = sub.add_parser("claim-journal", help="Open Y (星之旅录), claim completed tasks across tabs & unlocked milestone orbs")
    s.add_argument("--tabs", nargs="+", choices=list(actions.JOURNAL_TABS.keys()), default=None)
    s.add_argument("--clicks", type=int, default=2)
    s.add_argument("--no-milestones", action="store_true")
    s.set_defaults(func=cmd_claim_journal)

    s = sub.add_parser("build-facility", help="Repair or build a nearby Booster Facility (增效设施) via F -> 确认/建造")
    s.add_argument("--wait", type=float, default=4.2)
    s.set_defaults(func=cmd_build_facility)

    s = sub.add_parser("world-map", help="Switch region map via 世界地图 (charlulu / sprout_valley)")
    s.add_argument("zone", choices=list(actions.WORLD_ZONES.keys()))
    s.add_argument("--map-open", action="store_true", help="Skip pressing M if map is already open")
    s.set_defaults(func=cmd_world_map)

    s = sub.add_parser("exit-home", help="Exit Home Ranch (星临者的家园) via H -> 确认 and wait for open-world HUD")
    s.add_argument("--timeout", type=float, default=25.0)
    s.set_defaults(func=cmd_exit_home)

    s = sub.add_parser("home-harvest", help="Enter Home Ranch (H), one-click harvest all production buildings (尘石/矿/木/结晶), and return")
    s.add_argument("--stay", action="store_true", help="Stay inside Home Ranch instead of returning to open world")
    s.add_argument("--timeout", type=float, default=25.0)
    s.set_defaults(func=cmd_home_harvest)

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
    s.add_argument("--no-spam-f", action="store_true", help="Disable F tapping while walking (e.g., walking away from a modal building)")
    s.set_defaults(func=cmd_goto_bearing)

    s = sub.add_parser("fly-bearing", help="3D flight navigation: mount (T), climb (Space), align compass, cruise, and optional safe Ctrl landing")
    s.add_argument("deg", type=float)
    s.add_argument("--sec", type=float, default=2.4)
    s.add_argument("--climb", type=float, default=2.5, help="Initial vertical Space climb duration (sec)")
    s.add_argument("--no-boost", action="store_true", help="Cruise without Shift speed boost for short precision hops")
    s.add_argument("--land", action="store_true", help="Safely descend with Ctrl until ground touchdown and dismount (T)")
    s.set_defaults(func=cmd_fly_bearing)

    s = sub.add_parser("land", help="Safely descend from flight with Ctrl until touchdown and dismount (T) with zero fall damage")
    s.add_argument("--max-sec", type=float, default=6.5)
    s.add_argument("--hover", action="store_true", help="Descend to ground without dismounting")
    s.set_defaults(func=cmd_land)

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

    s = sub.add_parser("nav-bake", help="Bake a 1920x1080 world-map screenshot into a cached 2.5D costmap")
    s.add_argument("--map", required=True, help="World map screenshot PNG path")
    s.add_argument("--name", default="sprout_valley", help="Cache map ID")
    s.add_argument("--cell", type=int, default=6, help="Grid cell size in map pixels (default 6 -> 320x180)")
    s.add_argument("--px-per-sec", type=float, default=34.0, help="Calibrated sprint speed in map pixels/sec")
    s.add_argument("--cache-dir", default=navmesh.DEFAULT_CACHE_DIR)
    s.add_argument("--preview", default=None, help="Optional PNG path to save color-coded costmap preview")
    s.set_defaults(func=cmd_nav_bake)

    s = sub.add_parser("nav-plan", help="Plan an A* path on a baked 2.5D costmap (with obstacle memory)")
    s.add_argument("--name", default="sprout_valley")
    s.add_argument("--start", nargs=2, type=float, required=True, metavar=("X", "Y"))
    s.add_argument("--goal", nargs=2, type=float, required=True, metavar=("X", "Y"))
    s.add_argument("--mode", choices=["ground", "fly"], default="ground")
    s.add_argument("--no-smooth", action="store_true")
    s.add_argument("--cache-dir", default=navmesh.DEFAULT_CACHE_DIR)
    s.add_argument("--preview", default=None, help="Optional PNG path to render path overlay")
    s.set_defaults(func=cmd_nav_plan)

    s = sub.add_parser("nav-obstacle", help="Manage online obstacle memory & 3D portal edges for a costmap")
    s.add_argument("op", choices=["list", "add", "portal", "clear"])
    s.add_argument("--name", default="sprout_valley")
    s.add_argument("--xy", nargs=2, type=float, default=None, metavar=("X", "Y"))
    s.add_argument("--dst", nargs=2, type=float, default=None, metavar=("X2", "Y2"))
    s.add_argument("--radius", type=float, default=24.0)
    s.add_argument("--penalty", type=float, default=18.0)
    s.add_argument("--action", default="jump", choices=["jump", "mushroom", "teleport", "fly"])
    s.add_argument("--cost", type=float, default=4.0)
    s.add_argument("--reason", default="manual")
    s.add_argument("--cache-dir", default=navmesh.DEFAULT_CACHE_DIR)
    s.set_defaults(func=cmd_nav_obstacle)

    s = sub.add_parser("nav-localize", help="Localize player (x, y) on a baked world map from minimap terrain")
    s.add_argument("--name", default="sprout_valley")
    s.add_argument("--frame", default=None, help="Optional offline screenshot path (captures live window if omitted)")
    s.add_argument("--hint", nargs=2, type=float, default=None, metavar=("X", "Y"))
    s.add_argument("--radius", type=float, default=240.0)
    s.add_argument("--cache-dir", default=navmesh.DEFAULT_CACHE_DIR)
    s.set_defaults(func=cmd_nav_localize)

    s = sub.add_parser("nav-goto", help="Closed-loop 2.5D A* navigation with online obstacle learning & replanning")
    s.add_argument("--name", default="sprout_valley")
    s.add_argument("--goal", nargs=2, type=float, required=True, metavar=("X", "Y"))
    s.add_argument("--start", nargs=2, type=float, default=None, metavar=("X", "Y"))
    s.add_argument("--mode", choices=["ground", "fly"], default="ground")
    s.add_argument("--max-legs", type=int, default=12)
    s.add_argument("--arrive-px", type=float, default=28.0)
    s.add_argument("--mount", action="store_true", help="Summon fast ground mount (~1.75x speed) during nav-goto and dismount on arrival")
    s.add_argument("--no-sprint", action="store_true")
    s.add_argument("--no-spam-f", action="store_true")
    s.add_argument("--cache-dir", default=navmesh.DEFAULT_CACHE_DIR)
    s.set_defaults(func=cmd_nav_goto)

    s = sub.add_parser("ocr", help="Run RapidOCR (ONNX) on live game window or offline screenshot ROI")
    s.add_argument("--frame", default=None, help="Optional offline screenshot path (captures live window if omitted)")
    s.add_argument("--roi", nargs=4, type=int, default=None, metavar=("X1", "Y1", "X2", "Y2"))
    s.add_argument("--min-score", type=float, default=0.48)
    s.set_defaults(func=cmd_ocr)

    s = sub.add_parser("read-f", help="Read active [F] interaction prompt text and classify category via RapidOCR")
    s.add_argument("--frame", default=None)
    s.set_defaults(func=cmd_read_f)

    s = sub.add_parser("hud-markers", help="Detect 3D floating distance icons (chest/challenge/wumi/egg) via templates + OCR")
    s.add_argument("--frame", default=None)
    s.add_argument("--kinds", nargs="+", choices=["chest", "challenge", "wumi", "egg"])
    s.add_argument("--threshold", type=float, default=0.74)
    s.add_argument("--no-ocr", action="store_true")
    s.set_defaults(func=cmd_hud_markers)

    s = sub.add_parser("map-pois", help="Extract world-map POIs (Star Veins, locked facilities, shops, bosses) for navmesh")
    s.add_argument("--frame", default=None)
    s.add_argument("--ocr", action="store_true", help="Also OCR World Boss labels below red markers")
    s.set_defaults(func=cmd_map_pois)

    s = sub.add_parser("check-anomaly", help="Detect screen anomalies (revive, tutorial, confirm modal + materials, build panel, tip)")
    s.add_argument("--frame", default=None)
    s.add_argument("--no-ocr", action="store_true")
    s.set_defaults(func=cmd_check_anomaly)

    s = sub.add_parser("recover", help="Automatically recover from revive/tutorial/modal/tip screens back to open-world HUD")
    s.add_argument("--auto-confirm", action="store_true", help="Confirm repair/build dialogs when materials are sufficient")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--frame", default=None)
    s.set_defaults(func=cmd_recover)

    s = sub.add_parser("yolo-detect", help="Run optional ONNX YOLO 3D detector (or hybrid CV/template fallback)")
    s.add_argument("--frame", default=None)
    s.add_argument("--model", default=detector.DEFAULT_MODEL_PATH)
    s.add_argument("--classes", nargs="+", choices=list(detector.DEFAULT_CLASSES))
    s.add_argument("--conf", type=float, default=0.35)
    s.add_argument("--ocr", action="store_true")
    s.set_defaults(func=cmd_yolo_detect)

    s = sub.add_parser("yolo-bench", help="Benchmark ONNX YOLO (or hybrid fallback) inference latency and FPS")
    s.add_argument("--frame", default=None)
    s.add_argument("--model", default=detector.DEFAULT_MODEL_PATH)
    s.add_argument("--conf", type=float, default=0.35)
    s.add_argument("--runs", type=int, default=10)
    s.set_defaults(func=cmd_yolo_bench)

    s = sub.add_parser("yolo-init-dataset", help="Bootstrap UID-masked Ultralytics YOLO dataset from snaps/")
    s.add_argument("--out", default=detector.DEFAULT_DATASET_DIR)
    s.add_argument("--snaps", default=os.path.join(detector.ROOT_DIR, "snaps"))
    s.add_argument("--val-ratio", type=float, default=0.15)
    s.add_argument("--max-images", type=int, default=120)
    s.add_argument("--no-mask-uid", action="store_true")
    s.set_defaults(func=cmd_yolo_init_dataset)

    s = sub.add_parser("yolo-prelabel", help="Auto-generate initial YOLO .txt labels via templates + CV")
    s.add_argument("--dataset", default=detector.DEFAULT_DATASET_DIR)
    s.add_argument("--min-score", type=float, default=0.76)
    s.set_defaults(func=cmd_yolo_prelabel)

    s = sub.add_parser("hunt-pois", help="Autonomous closed-loop minimap POI hunter (approach, flight climb, puzzle, facility, combat)")
    s.add_argument("--steps", type=int, default=12)
    s.add_argument("--max-dist", type=float, default=72.0)
    s.add_argument("--no-fly", action="store_true", help="Disable automatic 3D flight escalation when blocked by cliffs")
    s.add_argument("--idle-sec", type=float, default=0.0, help="When >0, wait for user physical idle and yield within ~30ms on takeover")
    s.add_argument("--snap-dir", default=None)
    s.set_defaults(func=cmd_hunt_pois)

    # Built-in WebView2 CloudShell lifecycle commands
    sc = sub.add_parser("cloud", help="Manage built-in WebView2 CloudShell (azp-cloud.exe): launch, status, close, build")
    csub = sc.add_subparsers(dest="cloud_cmd", required=True)

    cl = csub.add_parser("launch", help="Launch azp-cloud.exe detached and wait for Azp.CloudShell window")
    cl.add_argument("--exe", default=None, help="Explicit path to azp-cloud.exe")
    cl.add_argument("--url", default=None, help="Cloud URL or preset (manjuu, manjuu-alt, 163, or http(s)://...)")
    cl.add_argument("--width", type=int, default=None, help="Client width in physical px (default 1280)")
    cl.add_argument("--height", type=int, default=None, help="Client height in physical px (default 720)")
    cl.add_argument("--title", default=None, help="Window title (default AZP CloudShell)")
    cl.add_argument("--user-data", default=None, help="Persistent WebView2 user-data directory")
    cl.add_argument("--log-file", default=None, help="Log file path")
    cl.add_argument("--inject", action="append", default=None, help="Repeatable JS file to inject on document created")
    cl.add_argument("--devtools", action="store_true", help="Open WebView2 DevTools")
    cl.add_argument("--debug-port", type=int, default=None, help="Remote debugging port")
    cl.add_argument("--disable-webgl2", action="store_true", help="Disable WebGL2 inside CloudShell")
    cl.add_argument("--wait", type=float, default=30.0, help="Seconds to wait for window creation")
    cl.add_argument("--no-reuse", action="store_true", help="Spawn a new instance instead of reusing existing window")
    cl.set_defaults(func=cmd_cloud)

    cs = csub.add_parser("status", help="Inspect CloudShell executable, running window, capture speed, and HUD state")
    cs.add_argument("--title", default=None, help="Window title to query")
    cs.set_defaults(func=cmd_cloud)

    cc = csub.add_parser("close", help="Gracefully close CloudShell via WM_CLOSE (preserving WebView2 login profile)")
    cc.add_argument("--title", default=None, help="Window title to close")
    cc.add_argument("--timeout", type=float, default=5.0, help="Seconds to wait before force termination")
    cc.set_defaults(func=cmd_cloud)

    cb = csub.add_parser("build", help="Compile native C++20 WebView2 CloudShell (CloudShell/dist/azp-cloud.exe)")
    cb.add_argument("--arch", choices=["x64", "arm64"], default=None, help="Target architecture (default: x64)")
    cb.add_argument("--config", choices=["RelWithDebInfo", "Debug"], default=None, help="CMake build configuration")
    cb.add_argument("--webview2-sdk-dir", default=None, help="Optional extracted Microsoft.Web.WebView2 NuGet root")
    cb.add_argument("--clean", action="store_true", help="Clean build directory before compiling")
    cb.add_argument("--debug", action="store_true", help="Shorthand for --config Debug")
    cb.add_argument("extra", nargs=argparse.REMAINDER, help="Extra arguments forwarded to CloudShell/build.py")
    cb.set_defaults(func=cmd_cloud)

    return p


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args()
    if _is_cloud(args) and args.cmd != "cloud":
        cloud.set_active_cloud_backend(_open_cloud(args))
        try:
            args.func(args)
        finally:
            cloud.set_active_cloud_backend(None)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
