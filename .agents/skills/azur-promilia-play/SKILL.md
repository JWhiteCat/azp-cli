---
name: azur-promilia-play
description: >-
  Guides the agent to autonomously play and explore the running Windows PC game
  Azur Promilia (蓝色星原：旅谣 / AzurPromilia.exe), collect open-world rewards
  (chests, Kibo puzzles, Star Melodies, Star Vein teleporters, exploration
  challenges, daily/event rewards), and distill new reusable operations into the
  AZP (`azp.py`) CLI toolkit. Use whenever the user asks to play, explore,
  farm rewards, solve minigames/puzzles, claim dailies, or extend automation
  scripts for Azur Promilia / 蓝色星原.
---

# Azur Promilia (蓝色星原) Open-World Play & Automation Runbook

Use the existing Python CLI (`azp.py` in the AZP project root) to observe, navigate, fight, solve puzzles/minigames, claim rewards, and continuously expand reusable automation.

## 1. Execution Environment & Preflight Gates

Always follow these four hard platform rules before sending any input:

1. **Use `windows-mcp` `PowerShell` for live game commands (set `timeout` for long batches)**:
   - Standard `run_command` executes in an isolated sandbox desktop (`exebox-*`) where `EnumWindows` sees 0 windows.
   - Always run `python azp.py ...` that inspects or controls `AzurPromilia.exe` via `windows-mcp` `PowerShell` from the AZP workspace root.
   - `windows-mcp` `PowerShell` defaults to a **30s timeout**; pass `timeout: 120` (or keep batches to 6–10 steps) when running `coop-explore`, `explore`, or `solve-zuzanka`.
   - Use `run_command` only for offline file work, analyzing already-saved PNGs, `mask-uid`, and running `tests/test_offline.py`.
2. **Verify Windows UIPI elevation first**:
   - `AzurPromilia.exe` runs elevated (High integrity). Non-elevated `SendInput` is silently dropped by Windows UIPI.
   - Run preflight via `windows-mcp` `PowerShell` from the AZP project root:
     ```powershell
     python azp.py status
     ```
   - Confirm `privilege.can_send_input == true` and `size == [1920, 1080]`.
   - If `can_send_input == false` (`open_process_err == 5`), **stop immediately** and ask the user to restart the `windows-mcp` daemon (`C:\Users\wanj\.windows-mcp\start-server.cmd`) from an Administrator PowerShell. Never attempt to bypass UIPI.
3. **Respect foreground exclusivity & user activity (`PhysicalInputMonitor` & `IsIconic` recovery)**:
   - Empirically verified: `AzurPromilia.exe` supports background `PrintWindow` capture (`azp.py bg-snap`), **but ignores both `PostMessage` and `ViGEmBus` Xbox 360 gamepad input when unfocused**.
   - `azp/win.py` automatically detects minimized (`IsIconic`) game windows and restores them (`SW_RESTORE` + `0.35s`) when focusing (e.g., if Steam or a background app minimized the client).
   - **Important**: Some background apps (e.g., Steam / idle MMOs) continuously reset Windows `GetLastInputInfo()` so `user_idle_seconds()` reads `0.0` even when the user is AFK. Always trust `coop.PhysicalInputMonitor` (read-only `WH_KEYBOARD_LL`/`WH_MOUSE_LL` filtering `INJECTED` flags, used inside `coop-explore` and `hunt-pois --idle-sec`) as the source of truth for real physical user input.
   - If the user is physically active, do **not** hijack focus; either wait passively (e.g., via `schedule`) or run `azp.py coop-explore --idle-sec 15` / `azp.py hunt-pois --steps 6 --idle-sec 15` (aborts within ~30ms on physical user takeover).
   - Whenever pausing, asking the user, or finishing a run, always call `windows-mcp` `ControlStatus(release=true)`.
4. **Exit Home Ranch before open-world runs**:
   - If `azp.py status` reports `in_home_ranch: true` (or `hud.confirm_dialog: true` for `是否要离开家园？`), run `python azp.py exit-home` (or let `coop-explore` auto-exit Home Ranch; pass `--no-exit-home` to opt out).
5. **MuMu Emulator Backend (Zero-Focus-Hijack IPC 截图增强 & 触控增强)**:
   - When targeting MuMu Player 12 / NX (`com.manjuu.promilia`), use `azp.py mumu-*` commands or pass `--backend mumu` (optionally `--mumu-index <n>`, `--mumu-addr <host:port|emulator-xxxx>`, or `--mumu-path <dir>`).
   - `azp/mumu.py` automatically discovers the MuMu install root, enumerates instances via `MuMuManager.exe info -v all`, binds `nemu_get_display_id` to `com.manjuu.promilia` (avoiding other emulator instances like Arknights), checks `MuMuManager.exe version >= 6.3.2.0` before enabling IPC touch/key input (`nemu_input_event_finger_touch_down/up`, `nemu_input_event_key_down/up`, `nemu_input_text`), and captures 1920×1080 background RGBA frames via `nemu_capture_display` (~14ms/frame) with zero foreground focus hijack.
   - **Anti-cheat note**: On Android MuMu, `com.manjuu.promilia` may display a Tencent ACE emulator warning (`ACE安全风险提示：检测到您运行在模拟器内...`) upon entering the game world. Never attempt to bypass ACE; report it clearly to the user if encountered.

## 2. Core Open-World Exploration & Reward Workflow

Work in tight **observe → act → verify** batches (chain multiple short CLI calls per PowerShell turn to minimize latency).

### Step A — Anomaly Guard, Claim Ready Rewards & Check Goals
1. Before or after long runs, check screen anomalies (`revive`, `tutorial_modal`, `confirm_dialog` with `have/need` material check, `build_panel`, `floating_tip`) and recover cleanly:
   ```powershell
   python azp.py check-anomaly
   python azp.py recover --auto-confirm   # auto-revives, dismisses tutorials, or confirms repairs when materials suffice
   ```
2. Check `red_dots` from `azp.py status` and claim ready rewards via one-line commands:
   ```powershell
   python azp.py claim-daily --tasks 4   # F5 Daily Activity + F2 Battle Pass
   python azp.py claim-journal           # Y 星之旅录 completed tasks + point milestone orbs
   python azp.py claim-valley            # M -> 探索度 -> 山谷秘藏 milestones
   python azp.py claim-events            # F1 星临旅记 (safe against shifting 前往 rows)
   python azp.py claim-codex             # Esc -> 星临图志 -> 调查奖励
   python azp.py home-harvest            # H -> 家园中枢 -> 建筑管理 -> 一键收取 (300+ 尘石/矿/木/结晶) -> 自动返回大世界
   ```
3. Track regional Exploration Progress milestones (e.g., Sprout Valley Lv.2 `550` EXP unlocks free-altitude flying).

### Step B — Perception & 2.5D A* Pathfinding Navigation
Avoid blind wandering or walking straight into cliffs/lakes/World Bosses. Combine **world-map POI extraction + 2.5D A* navmesh** with **3D floating marker & OCR perception**:
```powershell
# 1. Extract map POIs (Star Veins, locked facilities, shops, World Bosses) with safe_click zoom-bar guards
python azp.py map-pois --frame snaps/map.png --ocr

# 2. Bake a 1920x1080 world-map screenshot into a cached 2.5D semantic costmap (roads, slopes, water, ruins, bosses)
python azp.py nav-bake --map snaps/map.png --name wolf_north --preview snaps/wolf_north_debug.png

# 3. Plan & execute closed-loop 2.5D A* navigation with online obstacle memory & automatic replanning
python azp.py nav-plan --name wolf_north --start 352 585 --goal 630 530 --mode ground
python azp.py nav-goto --name wolf_north --start 352 585 --goal 630 530 --max-legs 8

# 4. Live 3D viewport perception: floating distance icons, [F] prompt OCR, and optional ONNX YOLO / hybrid CV
python azp.py hud-markers             # detects chest/challenge/wumi/egg icons + OCRs distance (m) & steer_dx
python azp.py read-f                  # OCRs active [F] prompt bar and classifies chest/facility/star_vein/challenge/gather
python azp.py yolo-detect             # runs optional models/azp_yolo.onnx (zero PyTorch) or hybrid CV/template fallback
python azp.py pois                    # minimap player heading (PCA narrow-tip) & clustered uncompleted POI bearings
```
- Prefer `nav-goto` (passing `--start <x> <y>` from your last Star Vein teleport or `nav-localize`; add `--mount` for ~1.75× Wolf speed or `--mode fly` once regional flying is unlocked) for multi-hundred-meter routing around lakes, cliffs, and red World Boss danger bubbles.
- For **3D free flight** (unlocked at Sprout Valley Lv.2+, `T` summons flying mount, `hud.is_flying` detected via right-hand `[Ctrl]` pill or bounded subtitle-proof `"Space"`+`"Shift"` captions):
  - Use `python azp.py fly-bearing <deg> --sec <sec> --climb <sec> [--no-boost] [--land]` and `python azp.py land`.
  - **3D Map Tracking Trick (`追踪`)**: Click any locked Booster Facility on the map (`M`) → click `追踪 (1665, 975)` to pin a live 3D HUD marker with distance (`m`) and elevation arrow (`^`/`v`), then fly directly to it with `fly-bearing`.
  - **CRITICAL FLIGHT RULES**:
    1. **NEVER press `S` while flying** — holding `S` turns the flying mount 180° and the flight camera auto-follows, silently reversing heading. Always clear cliffs/canopies with `W + Space` simultaneous climb (`~730` speed; stationary `Space` alone barely rises).
    2. **NEVER tap `T` at high altitude** — causes lethal fall damage. Always descend with `Ctrl` until touchdown (`python azp.py land` or `--land`) before dismounting.
    3. **Story Barriers & Hazard Zones**: Avoid crossing the golden fog barrier into **`迹影崖谷` (`0%`)** before Star-Sense Lv.20+ (`-389 HP/s` DoT; `fly-bearing` auto-aborts on `hazard_vignette`), and note **`常青之庭`** (`雾叶密林`) is wrapped in a prismatic story barrier (`露米` pushes the character 180° back) — route **around via `河间廊桥` (`20° → 285°`)** to reach `屹湖湾地` cleanly.
- For short-range sprints (<80m), use `python azp.py goto-bearing <deg> --sec <sec>` (2-pass closed-loop yaw alignment + minimap-diff unstuck recovery; pass `--no-spam-f` when walking away from an interactive modal building).
- Mount (`T`) uses the last selected mount from `hold T` wheel. Switch explicitly via `python azp.py mount --select wolf` (Slot 2 `(1140, 345)`, `速 800`, fast ground travel) or `python azp.py mount --select bird` (Slot 1 `(960, 290)`, `速 350`, hovers/glides; climbs freely once region flying unlocks). Avoid `--jump` while swimming.

### Step C — Execute High-Value Reward Targets (Priority Order)
1. **Unactivated Star Vein Teleporters (`星脉节点`)**: High Exploration EXP + `90 红晶` + new fast-travel anchor. Approach and run `azp.py interact --taps 6` + `azp.py dismiss`. Note: `雾叶密林` (Mist-Leaf Forest) has continuous poison-mist DoT until story/antidote progression.
2. **Booster Facilities (`增效设施`)**: Use `python azp.py build-facility` (waits `1.15s` after `F` for panel slide-in; handles both white repair modals `确认 (1245, 660)` and right-hand parchment build panels `建造 (1570, 885)`, whether already opened by `goto-bearing` spam-F or still in open-world). Already-built facilities (`采集小屋`/`伐木场`/`星辉矿场`/`元素祭坛` etc.) are classified as `built_facility` by `read-f` and automatically skipped by `hunt-pois`. If short on `尘石` or `风铜矿`, run `python azp.py home-harvest` first to one-click harvest Home Ranch production buildings (live-verified `0 → 311 尘石`!).
3. **Kibo Puzzles (`奇波解谜`)**: When near a puzzle (`near_kibo_puzzle: true` or bunny icon overhead):
   - If the element is obvious (amber crystal → `light`, sprout/stump → `grass`, dry leaf pile → `fire`, cracked cairn → `thunder` Earth+Thunder slot `(1270, 515)`), run `python azp.py kibo-puzzle <element>`.
   - Otherwise run `python azp.py solve-puzzle` (cycles wheel elements automatically with empty-slot `Esc` guard until `Q` glow clears, then loots drops).
4. **Exploration Challenges (`探索挑战`)**:
   - **Zuzanka Ladder (`搭建祖赞卡之梯`)**: Start challenge (`F` → click `开始 (1585, 910)`), then run the 100Hz BitBlt CV solver:
     ```powershell
     python azp.py solve-zuzanka --max-drops 12 --lead 0.23 --settle 2.15
     ```
     Exit (`click 770 845`) and loot all 3 spawned chests (Bronze/Silver/Gold). Use `read-f` or sidestep past `拆除祖赞卡之梯` if it sits at slot #1 in the `[F]` list.
   - **Mount Race (`坐骑挑战`)**: Run `python azp.py solve-race --max-sec 65 --gain 0.28 [--restart]` (25Hz BitBlt with ONNX YOLO or anti-crystal-mountain CV).
   - **Cabbage Chicken (`变身菜鸡`)**: Advance `呱呱` intro dialog via `dialog` (do **not** skip, which cancels), then run `python azp.py solve-cabbage --max-sec 48 --gain 0.34` (25Hz BitBlt dual `R`-sonar blue-badge + lime-seed detector with `Space`/`Ctrl` helicopter altitude control).
   - **Multi-wave Rifts / Guarded Chests**: Use `python azp.py combat --sec 18 --no-early-exit` (prevents early exit during wave spawn gaps), then `azp.py interact --taps 6`.
5. **Star Melodies (`星律`), Chests, Wumi (`乌咪`) & New Kibo**:
   - Jump / mount-jump into floating golden Saturn-ring orbs (`星律`).
   - Aim at uncaptured / Shiny Kibo (species with white card icon next to nameplate) and run `python azp.py capture --throws 1`.
6. **Fast Travel, Region Switching & Cooperative Patrol**:
   - Switch region maps via `python azp.py world-map sprout_valley` (or `charlulu`), run `map-pois` to locate targets, then run `python azp.py teleport <x> <y> --map-open` (automatically drags bottom-edge targets above the zoom bar before clicking).
   - For rapid local clearing of clusters (Kibo puzzles + Booster Facilities + guarded chests + camps), use `python azp.py hunt-pois --steps 6 --max-dist 76 [--idle-sec 15]` (keep `--steps <= 6` per `PowerShell(timeout=115)` turn so JSON returns cleanly) — automatically approaches nearest uncompleted minimap POIs, escalates to `W+Space` 3D flight when blocked by cliffs at `dist_px >= 28`, fast-solves glowing `Q` puzzles (or falls back to wheel cycling), builds locked facilities via `read-f` + `build-facility` while skipping already-built `built_facility` prompts, clears aggroed combat, aborts on lethal `hazard_vignette`, and yields within ~30ms on physical user input when `--idle-sec > 0`.
- Run `python azp.py coop-explore --idle-sec 15 --steps 10 --snap-dir snaps/coop_run` (with `PowerShell(timeout=120)`) for safe idle-aware patrol, or `python azp.py explore --steps 10 --step-sec 2.6` when foreground play is explicitly requested.

## 3. Distilling New Mechanics, YOLO Dataset & Committing Safely

Whenever encountering a new repeatable UI flow, puzzle, or minigame:
1. Capture a 1920×1080 reference frame with `--grid --grid-step 160` to measure exact client coordinates and color signatures.
2. For latency-sensitive minigames, use `FastROIGrabber` (Win32 GDI `BitBlt`, ~4ms/frame) from `azp/minigames.py` instead of `PIL.ImageGrab` (~110ms on multi-monitor setups).
3. To train/upgrade the optional zero-PyTorch ONNX YOLO 3D detector (`models/azp_yolo.onnx`), run `python azp.py yolo-init-dataset` (automatically masks bottom-left UID) → `python azp.py yolo-prelabel` → train/export `.onnx` → benchmark via `python azp.py yolo-bench`.
4. Add the reusable function under `azp/` and expose a clean subcommand in `azp.py`.
5. **Mask UID before committing ANY screenshot**:
   - Never commit raw screenshots with visible bottom-left UID. Always run:
     ```powershell
     python azp.py mask-uid snaps/<raw>.png --out snaps/<fixture>.png
     ```
   - Whitelist only masked offline test fixtures in `.gitignore` and add assertions in `tests/test_offline.py` (which enforces masked UID boxes on all committed fixtures).
6. Update `README.md` and this skill's references when adding subcommands or calibrated coordinates.

## 4. Verification & Clean Handoff

Before concluding or committing:
1. Run offline regression tests and CLI sanity check via `run_command` from the AZP project root:
   ```powershell
   python tests/test_offline.py; python azp.py --help
   ```
2. Verify live game is cleanly back in open-world HUD (`python azp.py status` or `bg-snap`).
3. Release desktop control via `windows-mcp` `ControlStatus(release=true)`.

## References

Load these only when needed:
- [UI Coordinates, CV Thresholds & Game Mechanics](./references/ui-coordinates-and-mechanics.md) — Calibrated 1920×1080 coordinates, Kibo wheel slots, mount wheel, menu buttons, Home Ranch exit dialog, Zuzanka/Cabbage/Race physics, and background input test matrix.
- [AZP CLI Command Reference](./references/cli-reference.md) — Complete catalog of all 65 `azp.py` subcommands (PC, MuMu, 3D flight `fly-bearing`/`land`, autonomous `hunt-pois`, 2.5D A* navmesh, RapidOCR/templates, anomaly recovery, Home Ranch harvest, Y-Journal claim, and ONNX YOLO) and module responsibilities.
