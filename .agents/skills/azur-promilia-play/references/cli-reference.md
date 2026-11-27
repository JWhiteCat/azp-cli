# AZP CLI Command Reference (`azp.py`)

Run all live-window commands inside `windows-mcp` `PowerShell` from the AZP project root (pass `timeout: 120` for multi-step loops):

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
| `python azp.py focus` | `win` | Restore & bring `AzurPromilia` to foreground |
| `python azp.py key <key> [--hold 0.06] [--repeat 1]` | `input_ctl` | Hardware scan-code key press |
| `python azp.py click <x> <y> [--button left] [--alt]` | `input_ctl` | Click 1920×1080 client coordinates |
| `python azp.py drag <x1> <y1> <x2> <y2> [--duration 0.35]` | `input_ctl` | Drag between client coordinates (e.g., world map panning) |
| `python azp.py scroll <delta> [--at x y]` | `input_ctl` | Mouse wheel zoom/list scroll |
| `python azp.py move --dir <wasd> --sec <s> [--sprint] [--jump] [--spam-f]` | `actions` | Directional movement with optional sprint, jump, and `F` pickup |
| `python azp.py turn --dx <px> [--dy <px>] [--steps 12]` | `actions` | Relative 3D mouse camera rotation (~`8.4 px/deg`) |

## High-Level Exploration, Combat, Rewards & Minigames
| Command | Module | Purpose |
| --- | --- | --- |
| `python azp.py mount` | `actions` | Toggle Kibo mount (`T`) |
| `python azp.py exit-home [--timeout 18]` | `actions` | Exit Home Ranch (`星临者的家园`) via `H` → `确认 (1258, 676)` and wait for open-world HUD |
| `python azp.py interact [--taps 5] [--interval 0.18]` | `actions` | Rapid `F` tapping for chests, teleporters, pickups |
| `python azp.py goto-bearing <deg> [--sec 2.4] [--no-sprint]` | `nav` | Align to compass bearing via minimap arrow and sprint forward |
| `python azp.py world-map <charlulu\|sprout_valley> [--map-open]` | `actions` | Open `世界地图 (1810, 950)` and enter target region map |
| `python azp.py teleport <x> <y> [--map-open] [--timeout 18]` | `actions` | Click map waypoint → click `传送` → poll until open-world HUD returns |
| `python azp.py kibo-puzzle <element> [--wait 2.8]` | `actions` | Hold `Q` wheel and cast specific element (`fire/earth/grass/ice/water/thunder/light`) |
| `python azp.py solve-puzzle [--elements ...]` | `nav` | Cycle Kibo puzzle elements automatically until `Q` glow clears |
| `python azp.py capture [--throws 1] [--wait 3.5]` | `actions` | Star-Bond Kibo capture (`G` → `LMB` → wait → `G`) |
| `python azp.py combat [--sec 6.0] [--no-switch] [--no-early-exit]` | `actions` | Active combat combo (`Z` + LMB + `E/Q/R` + `Shift` + `1/2/3` swap + post-loot) |
| `python azp.py solve-zuzanka [--max-drops 12] [--lead 0.23]` | `minigames` | 100Hz `BitBlt` real-time CV solver for `搭建祖赞卡之梯` |
| `python azp.py dialog [--max 20]` | `actions` | Advance dialogue/cutscenes until open-world HUD returns |
| `python azp.py dismiss [--clicks 2]` | `actions` | Dismiss full-screen reward popups (`960, 920`) |
| `python azp.py claim-daily [--tasks 4]` | `actions` | Auto-claim `F5` Daily Activity tasks/chests + `F2` Travelogue BP |
| `python azp.py claim-valley [--clicks 2]` | `actions` | Auto-claim `M` → `探索度` → `山谷秘藏` regional exploration rewards |
| `python azp.py claim-events [--clicks 2]` | `actions` | Safely claim `F1` `星临旅记` event rewards |

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
