# AZP CLI Command Reference (`azp.py`)

Run all live-window or MuMu IPC commands inside `windows-mcp` `PowerShell` from the AZP project root (pass `timeout: 120` for multi-step loops). Global backend options: `--backend pc|mumu`, `--mumu-path <dir>`, `--mumu-index <n>`, `--mumu-addr <addr>`, `--mumu-pkg <pkg>` (or env `AZP_BACKEND`, `AZP_MUMU_PATH`, `AZP_MUMU_INDEX`, `AZP_MUMU_ADDR`, `AZP_MUMU_PKG`).

## MuMu Emulator Backend (Zero-Focus-Hijack 截图增强 & 触控增强)
| Command | Module | Purpose |
| --- | --- | --- |
| `python azp.py mumu-list` | `mumu` | Discover MuMu root, version (`>= 6.3.2.0` input gate), IPC DLL path, and all instances (`MuMuManager info -v all`) |
| `python azp.py mumu-status` | `mumu`, `vision` | Inspect MuMu IPC capture/touch enhancements, `display_id`, latency, HUD, Home Ranch, and minimap |
| `python azp.py mumu-test [--frames 5] [--out <path>]` | `mumu` | Benchmark MuMu IPC `nemu_capture_display` latency and verify multi-touch/key/text enhancement support |
| `python azp.py mumu-snap --out <path> [--grid] [--roi ...] [--scale 1.0]` | `mumu` | Pure-background MuMu IPC screenshot (~14ms, works covered/minimized) |
| `python azp.py mumu-tap <x> <y> [--hold 0.035] [--repeat 1] [--contact 0]` | `mumu` | Pure-background MuMu IPC multi-finger touch tap (`contact` 0-based → `finger_id` 1-based) |
| `python azp.py mumu-swipe <x1> <y1> <x2> <y2> [--duration 0.30] [--contact 0]` | `mumu` | Pure-background MuMu IPC smoothstep-interpolated swipe (always lifts finger in `finally`) |
| `python azp.py mumu-key <key> [--hold 0.05] [--repeat 1]` | `mumu` | Pure-background MuMu IPC Linux key press (`esc/back/space/w/...` or Android KeyEvent code) |
| `python azp.py mumu-text <text>` | `mumu` | Pure-background MuMu IPC text input (`nemu_input_text`) |
| `python azp.py --backend mumu <status\|snap\|bg-snap\|click\|drag\|key\|move\|turn\|dismiss\|pois>` | `mumu` | Route standard AZP subcommands transparently through MuMu IPC |

## Inspection & Reconnaissance
| Command | Module | Purpose |
| --- | --- | --- |
| `python azp.py status` | `win`, `vision`, `coop` | Window rect, focus, UIPI `can_send_input`, HUD/Home Ranch/confirm-dialog state, minimap summary, `red_dots` |
| `python azp.py bg-snap --out <path> [--scale 0.75]` | `win` | Non-intrusive `PrintWindow` background capture + `focused` & `idle_sec` |
| `python azp.py snap --out <path> [--grid] [--roi x1 y1 x2 y2]` | `win` | Foreground client capture with optional 160px coordinate grid |
| `python azp.py pois` | `nav` | Player compass heading (`0=N`) and clustered minimap POI bearings |
| `python azp.py scan360 --out-dir <dir> --prefix <p>` | `actions` | 4-direction 360° panoramic scout (front/right/back/left) |

## Low-Level Controls
| Command | Module | Purpose |
| --- | --- | --- |
| `python azp.py focus` | `win` | Auto-restore minimized (`IsIconic`) `AzurPromilia` window and bring to foreground |
| `python azp.py key <key> [--hold 0.06] [--repeat 1]` | `input_ctl` | Hardware scan-code key press |
| `python azp.py click <x> <y> [--button left] [--alt]` | `input_ctl` | Click 1920×1080 client coordinates |
| `python azp.py drag <x1> <y1> <x2> <y2> [--duration 0.35]` | `input_ctl` | Drag between client coordinates (e.g., world map panning) |
| `python azp.py scroll <delta> [--at x y]` | `input_ctl` | Mouse wheel zoom/list scroll |
| `python azp.py move --dir <wasd> --sec <s> [--sprint] [--jump] [--spam-f]` | `actions` | Directional movement with optional sprint, jump, and `F` pickup |
| `python azp.py turn --dx <px> [--dy <px>] [--steps 12]` | `actions` | Relative 3D mouse camera rotation (~`6.35 px/deg`; keep `|dx|/steps <= 38` to avoid Unity Raw Input clamp) |

## High-Level Exploration, Combat, Rewards & Minigames
| Command | Module | Purpose |
| --- | --- | --- |
| `python azp.py mount [--select bird\|wolf]` | `actions` | Toggle Kibo mount (`T`) or explicitly pick Bird/Wolf via hold-`T` wheel |
| `python azp.py exit-home [--timeout 25]` | `actions` | Exit Home Ranch (`星临者的家园`) via `H` → `确认 (1245, 660)` and wait for open-world HUD |
| `python azp.py home-harvest [--stay] [--timeout 25]` | `actions` | Enter Home Ranch (`H`), one-click harvest `建筑管理 → 一键收取` (`尘石`/矿/木/结晶), and return to open world |
| `python azp.py interact [--taps 5] [--interval 0.18]` | `actions` | Rapid `F` tapping for chests, teleporters, pickups |
| `python azp.py goto-bearing <deg> [--sec 2.4] [--no-sprint] [--no-spam-f]` | `nav` | Align to compass bearing via minimap arrow (PCA narrow-tip detector + 180° flip guard, 2-pass ground / 3-pass flight closed-loop yaw) and sprint forward with minimap-diff unstuck |
| `python azp.py fly-bearing <deg> [--sec 2.4] [--climb 2.5] [--no-boost] [--land]` | `nav` | 3D flight navigation: auto-summons flying mount (`T`, retries after step-back if blocked by overhang), calibrated 3-pass flight yaw (`5.8/5.5 px/deg` + 180° flip guard), `W+Space` diagonal climb (never presses `S`), `hazard_vignette` DoT abort, cliff climb unstuck, optional `safe_land()` |
| `python azp.py land [--max-sec 6.5] [--hover]` | `nav` | Safe zero-fall-damage landing: pulses `Ctrl` until lower/central viewport stops shifting for 2 consecutive checks, then dismounts with `T` (unless `--hover`) |
| `python azp.py hunt-pois [--steps 12] [--max-dist 72] [--no-fly] [--idle-sec 0] [--snap-dir <dir>]` | `nav`, `ocr_tpl`, `coop` | Autonomous closed-loop POI hunter: approaches nearest uncompleted minimap POI, escalates to 3D flight over cliffs (`dist_px >= 28`), auto-solves puzzles (`Q`/wheel), builds locked facilities (skipping `built_facility`), clears combat, aborts on `hazard_vignette`, and yields on physical user input when `--idle-sec > 0` |
| `python azp.py nav-bake --map <png> --name <id> [--cell 6] [--preview <png>]` | `navmesh` | Bake 1920×1080 world map into cached 2.5D costmap (roads/grass/slope/ruins/water/boss/fog) |
| `python azp.py nav-plan --name <id> --start X Y --goal X Y [--mode ground\|fly] [--preview <png>]` | `navmesh` | Plan smoothed 8-connected A* path using 2.5D costmap + online obstacle memory (with micro-leg merging) |
| `python azp.py nav-obstacle <add\|portal\|list\|clear> --name <id> [--xy X Y]` | `navmesh` | Inspect or mutate persisted online obstacle stamps and 3D portal edges (`jump`/`mushroom`/`fly`) |
| `python azp.py nav-localize --name <id> [--frame <png>] [--hint X Y]` | `navmesh` | Localize player `(x, y)` on baked world map from minimap terrain correlation (masks UI & fog) |
| `python azp.py nav-goto --name <id> --goal X Y [--start X Y] [--mode ground\|fly] [--mount]` | `navmesh` | Closed-loop A* navigation that auto-records stuck snags and replans around them (`--mount` uses ~1.75× Wolf speed) |
| `python azp.py world-map <charlulu\|sprout_valley> [--map-open]` | `actions` | Open `世界地图 (1810, 950)` and enter target region map |
| `python azp.py teleport <x> <y> [--map-open] [--timeout 18]` | `actions` | Click map waypoint → click `传送` → poll until open-world HUD returns |
| `python azp.py kibo-puzzle <element> [--wait 2.8]` | `actions` | Hold `Q` wheel and cast specific element (`fire/earth/grass/ice/water/thunder/light`) |
| `python azp.py solve-puzzle [--elements ...]` | `nav` | Cycle Kibo puzzle elements automatically until `Q` glow clears |
| `python azp.py capture [--throws 1] [--wait 3.5]` | `actions` | Star-Bond Kibo capture (`G` → `LMB` → wait → `G`) |
| `python azp.py combat [--sec 6.0] [--no-switch] [--no-early-exit]` | `actions` | Active combat combo (`Z` + LMB + `E/Q/R` + `Shift` + `1/2/3` swap + post-loot) |
| `python azp.py solve-zuzanka [--max-drops 12] [--lead 0.23]` | `minigames` | 100Hz `BitBlt` real-time CV solver for `搭建祖赞卡之梯` |
| `python azp.py solve-race [--max-sec 65] [--gain 0.28] [--restart] [--model <onnx>]` | `minigames`, `detector` | 25Hz `BitBlt` steering solver for `坐骑挑战` (ONNX YOLO or upgraded anti-crystal CV) |
| `python azp.py solve-cabbage [--max-sec 45] [--gain 0.25] [--model <onnx>]` | `minigames`, `detector` | 25Hz `BitBlt` glowing-seed chaser for `变身菜鸡` (ONNX YOLO or lime-glow CV) |

## Template Matching, RapidOCR & ONNX YOLO Perception
| Command | Module | Purpose |
| --- | --- | --- |
| `python azp.py ocr [--frame <png>] [--roi x1 y1 x2 y2]` | `ocr_tpl` | Run RapidOCR (ONNX) on live client or offline screenshot ROI |
| `python azp.py read-f [--frame <png>]` | `ocr_tpl` | Read active `[F]` interact prompt text and classify (`chest/facility/built_facility/star_vein/gather/...`) |
| `python azp.py hud-markers [--frame <png>] [--kinds ...] [--no-ocr]` | `ocr_tpl` | Detect 3D floating distance icons (`chest/challenge/wumi/egg`) + OCR distance (`m`) |
| `python azp.py map-pois [--frame <png>] [--ocr]` | `ocr_tpl` | Extract world-map POIs (`star_vein_active/locked`, `facility_locked`, `shop_bag`, `boss`) with `safe_click` guard for `navmesh` |
| `python azp.py check-anomaly [--frame <png>] [--no-ocr]` | `ocr_tpl` | Classify screen anomalies (`revive`, `tutorial_modal`, `confirm_dialog` + `have/need` check, `build_panel`, `floating_tip`) |
| `python azp.py recover [--auto-confirm] [--dry-run] [--frame <png>]` | `ocr_tpl` | Automatically recover from death/tutorial/modal/tip screens back to open-world HUD |
| `python azp.py yolo-detect [--frame <png>] [--model <onnx>] [--classes ...]` | `detector` | Run optional zero-PyTorch ONNX YOLO (`models/azp_yolo.onnx`) or hybrid CV/template fallback |
| `python azp.py yolo-bench [--model <onnx>] [--frame <png>] [--runs 10]` | `detector` | Benchmark ONNXRuntime / hybrid detector inference latency and FPS |
| `python azp.py yolo-init-dataset [--out <dir>] [--max-images 120]` | `detector` | Bootstrap UID-masked Ultralytics dataset (`data.yaml`, `train/val`) from `snaps/` |
| `python azp.py yolo-prelabel [--dataset <dir>] [--min-score 0.76]` | `detector` | Auto-generate initial YOLO `.txt` labels using Part-1 templates + CV |
| `python azp.py dialog [--max 20]` | `actions` | Advance dialogue/cutscenes until open-world HUD returns |
| `python azp.py dismiss [--clicks 2]` | `actions` | Dismiss full-screen reward popups (`960, 920`) |
| `python azp.py claim-daily [--tasks 4]` | `actions` | Auto-claim `F5` Daily Activity tasks/chests + `F2` Travelogue BP |
| `python azp.py claim-valley [--clicks 2]` | `actions` | Auto-claim `M` → `探索度` → `山谷秘藏` regional exploration rewards |
| `python azp.py claim-events [--clicks 2]` | `actions` | Safely claim `F1` `星临旅记` event rewards |
| `python azp.py claim-codex [--clicks 3]` | `actions` | Claim `Esc` → `星临图志` → `调查奖励` star-level Codex rewards |
| `python azp.py claim-journal [--tabs ...] [--clicks 2]` | `actions` | Claim `Y` (`星之旅录`) completed top-row tasks across tabs + unlocked milestone orbs |
| `python azp.py build-facility [--wait 4.2]` | `actions` | Repair or build nearby Booster Facility (`增效设施`) via `F` → `确认`/`建造 (1570, 885)` |

## Autonomous, Cooperative & Offline Utilities
| Command | Module | Purpose |
| --- | --- | --- |
| `python azp.py explore --steps 12 --step-sec 2.6 [--snap-dir <dir>]` | `actions` | Autonomous patrol, F-loot, combat, and minimap-diff unstuck loop |
| `python azp.py coop-explore --idle-sec 15 --steps 12 [--no-exit-home]` | `coop` | Idle-aware cooperative loop (`PhysicalInputMonitor` distinguishes physical vs injected input; ~30ms abort on takeover; auto-exits Home Ranch) |
| `python azp.py bg-test` / `bg-explore` | `bg_play`, `gamepad` | `PrintWindow` + `ViGEmBus` virtual Xbox 360 background probe/loop |
| `python azp.py mask-uid <paths...> [--out <path>] [--color 18 22 26]` | `vision` | Solid opaque mask over bottom-left UID region before committing screenshots |

## Offline Verification
```powershell
python tests/test_offline.py
```
Validates `vision.py` (HUD, blue-navy loading vs dark night grass, `[F]` pill, `Q` puzzle glow, confirm dialog, UID masking), `nav.py` (minimap heading & POI clustering), and `coop.py` (Home Ranch detection) against UID-masked real frames without touching the desktop.
