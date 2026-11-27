# Calibrated UI Coordinates, CV Thresholds & Game Mechanics (1920×1080 Client)

All coordinates below are in **game client pixels (`1920×1080`)**, converted to physical desktop pixels automatically by `azp/win.py` (`SetProcessDpiAwareness(2)`).

## 1. HUD & Vision Calibration (`azp/vision.py`)

- **Open-World HP Bar, G-Card Fallback & Flight State (`is_flying`)**:
  - HP bar at `x=830..1090, y=996..1006` (`ratio > 0.14`, vertical contrast `hp_luma - surr_luma > 24.0`, and above/below bright ratio `< 0.42` so bright cream menus like `Y` `星之旅录` / `山谷秘藏` / `建筑管理` never false-trigger `in_world_hud`).
  - Open-world controls check: combat `Q` slot at `x=1600..1696, y=942..1038` (`std > 28.0`) **OR** bottom-center `G` Star-Bond card slot at `x=1158..1202, y=972..1025` (`std > 32.0`), which keeps `in_world_hud == True` during high-altitude flight when `Q` is hidden over smooth sky/water while staying `False` in Home Ranch (`gcard_std ~ 14..16`).
  - **`is_flying` Detection**: Right-hand `[Ctrl]` (`降低高度`) white keycap pill at `x=1812..1864, y=591..619` (white ratio `> 0.18`; empirically `~0.347` on all flying frames vs `0.000` on ground/Wolf/Home Ranch).
- **Loading Screen**: Solid dark/light frames, Azur Promilia's dark-navy night tip banner (`B >= G`, `mean < 42.0`, `std < 22.0`), OR daytime warm-cream tip banner (`mean > 220.0`, `std < 22.0`) at `x=80..1840, y=820..1070`. Requiring `B >= G` for dark banners prevents night-time dark grass (`G > B + 8`) from falsely triggering loading state.
- **Home Ranch (`星临者的家园`), Harvest (`home-harvest`) & Exit Dialog (`H`)**: Detected when not loading, `in_world_hud == False`, and minimap golden arrow heading is visible (`is_home_ranch`). Pressing `H` opens a centered white modal (`x=520..1400, y=420..560`, white ratio `> 0.78`) with `确认` at `(1245, 660)` (`azp.py exit-home`, default timeout `25s`). Inside Home Ranch: `家园中枢` (`G`) → `建筑管理 (180, 730)` → `一键收取 (105, 895)` → close harvest summary `X (1855, 245)` → dismiss level-up popup `(960, 950)` → exit back to Home Ranch HUD (checking `is_home_ranch` before each `Esc` to avoid overshooting into the `Esc` pause menu, where `离开家园` sits at `(778, 995)`) (`azp.py home-harvest`).
- **Bottom-Left UID Mask Box (`mask-uid`)**: Normalized `(80/1440, 778/810, 272/1440, 1.0)` filled with opaque `(18, 22, 26)` (covers `(83, 781, 266, 810)` on 1440×810 and `(107, 1037, 363, 1080)` on 1920×1080).
- **`[F]` Interact Pill**: Active white/light-gray option bar at `x=1145..1420, y=548..590` (bright low-saturation pixel ratio `> 0.35`).
- **Kibo Puzzle `Q` Glow & `[Q]` Hint Badge**: `x=1600..1696, y=942..1038` (bright gold/green aura ratio `> 0.165`, or `> 0.14` together with white `[Q]` badge in `点击 [Q] 使用奇波技能进行解谜` at `x=860..888, y=798..824` `> 0.30`), gated on `in_world_hud == True`.
- **Enemy Combat Red HP Bars**: `x=260..1540, y=150..740`, contiguous horizontal red runs `>= 36px` (`R>205, G<72, B<72, R-G>140`), vertical span `3..32` rows (filters out red village banners and archery targets).
- **Minimap Geometry**: Center `(143, 121)`, radius `86px`, North-Up fixed orientation.
  - **Stuck detection**: Compare minimap ROI `(65, 45, 220, 198)` frame diff (`< 3.2` = stuck). Much cleaner than 3D viewport diff, which gets noisy from mount idle bob and grass sway.
  - **Completed Challenge Filter**: Completed minimap icons carry a small green checkmark immediately bottom-right of the white icon cluster.
- **Top HUD Red Dots**: Monitored at `y=22..48` for `esc, y, f1..f6, p, c`.

## 2. Kibo Puzzle Wheel (`hold Q`)

Holding `Q` for `0.85s` and releasing opens a 3×3 diamond grid (`点击直接释放奇波解谜技能`). Wait `0.45s` for open animation before clicking:

| Element | Key | Client `(x, y)` | Typical Visual Cue |
| --- | --- | --- | --- |
| Fire (火) | `fire` | `(960, 355)` | Dry leaf piles, burnable vines |
| Earth (地) | `earth` | `(805, 435)` | Cracked rock / amber boulders |
| Grass/Wood (木) | `grass` | `(1115, 435)` | Sprouting stumps, green mushroom buds |
| Ice (冰) | `ice` | `(650, 515)` | Water/heat mechanisms |
| Water (水) | `water` | `(960, 515)` | Fire braziers, dry plants |
| Thunder+Earth (雷/地) | `thunder` | `(1270, 515)` | Electric totems |
| Light (光) | `light` | `(805, 600)` | Dim amber crystal monoliths (`暗淡的结晶似乎可以吸收光之力...`) |

*Tip*: Wrong-element hint banners appear at top-center ~1.2s after cast and fade by ~2.5s. Solving a puzzle clears the overhead bunny icon and typically awards `+1` 普通星结卡 (`G` counter).

## 3. World Map, Teleport & Reward Menus

- **Map (`M`)**:
  - `世界地图` button: `(1810, 950)`
  - World overview zones: `夏露露村 (800, 680)`, `新芽山谷 (910, 440)`
  - Region `采集物` button: `(1850, 680)` → `建材 (365, 765)` → `尘石 (980, 760)` (inspect bag count or track wild nodes)
  - Region `可领取 / 探索度` button: `(1850, 815)`
  - `山谷秘藏` gift box icon (inside Exploration panel): `(1200, 795)`
  - `山谷秘藏` Lv.1 claim bar (click left side, not item icon): `(1380, 415)`
  - Waypoint `传送` button (after clicking a blue waypoint): `(1665, 975)` / `(1670, 1000)`
- **Global Reward Popup Dismiss**: Click lower blank area `(960, 920)`.
- **`F1` Events (`活动`)**:
  - `小小侠客养成记` tab: `(165, 330)` — completed rows shift up after claiming; be careful not to click shifted `前往` buttons (which can trigger `奇波对决` map tutorial).
  - `星临旅记` tab: `(165, 890)` — Day 02 `领取` at `(1430, 680)` (`璀璨忆痕`).
  - `来自星海的礼赠` daily check-in popup: Day 02 card `(710, 940)`, close `X` at `(1865, 55)`.
- **`F2` Travelogue Battle Pass (`普罗米利亚游记`)**:
  - `一键领取`: `(1740, 860)`
  - `奖励` tab: `(815, 1030)`
- **`F5` Daily Activity (`日常活跃`)**:
  - Wait ~1.5s for entry animation.
  - Row 1 `领取`: `(1680, 235)` (completed tasks shift up into Row 1).
  - Milestone chests (20 / 40 / 60 / 80 / 100): `y=990`, `x = 800, 1010, 1215, 1425, 1635`.

## 4. Minigame: 搭建祖赞卡之梯 (`azp/minigames.py`)

- **UI Buttons**: Start `开始 (1585, 910)`, Retry `重新开始 (1135, 845)`, Exit `退出 (770, 845)`.
- **Physics & CV Calibration**:
  - Base totem center: `x = 960`.
  - Carrying bird swings on an upper arc across `x = 420..1500, y = 70..330` (golden wings: `R>220, G>165, B<125, R-B>105`).
  - Dropped totems retain horizontal velocity during free-fall (~`0.23s` effective lead time).
  - Using Win32 GDI `BitBlt` (`FastROIGrabber`, ~4ms/frame) with `lead_sec = 0.23`, `tol = 12px`, `settle_sec = 2.05` drops totems dead-center (`11/12`, 3 stars, unlocks Bronze + Silver + Gold chests).

## 5. Key Controls & Movement Notes

- **3D Camera Yaw Calibration**: Ground/Wolf empirically **`6.35 px/deg`** (`dx = 572` ≈ 90° right turn). **Flying mounts have ~2× lower mouse sensitivity (`~12.0–13.5 px/deg`) and banking inertia (~`0.50s W` settle)** — `approach_poi()` automatically switches to 3-pass closed-loop alignment when `is_flying == True`. Keep per-step delta `<= ~38px` (`steps = max(8, abs(dx) // 35)`) so Unity Raw Input never clamps large single-frame turns.
- **Minimap Arrow vs Camera Alignment**: The minimap golden arrow shows the **character/mount body heading** (estimated via center-most connected golden component + PCA major axis + narrow-tip vs flared-wing transverse width, avoiding 180° flips). After `S`/`A`/`D` movement, tap `W` (`~0.24s`) before reading heading so body & camera realign.
- **3D Flight Physics (`T`, `Space`, `Ctrl`, `Shift`)**:
  - `T`: Summon / dismount flying mount (blocked under tight rock overhangs — step back `S` `0.65s` and retry). **Never tap `T` at high altitude** (causes lethal fall damage); use `azp.py land` (`Ctrl` pulses until lower/central viewport diff `< 3.0` twice, then `T`).
  - `W + Space` simultaneously climbs steeply at speed `~730` (the green wing gauge is live flight speed, not stamina; stationary `Space` alone barely rises). **Never press `S` while flying** (turns mount 180° and auto-reverses camera).
  - `Shift` fires a speed-dash impulse (~3s cooldown); default-zoom horizontal flight cruise is `~30 map px/s` (`--no-boost`) / `~45 map px/s` (with `Shift` dash).
- **Mount (`T`) & Mount Wheel (`hold T`)**: Fast ground travel + high jump (`Space`) for floating Star Melodies; auto-dismounts in deep water. Mount-up animation locks camera yaw for ~`0.9s` (`toggle_mount()` waits `0.95s`).
  - Mount wheel slots (`hold T`): Slot 1 Bird (`速 350`, hovers/glides; climbs freely once region flying unlocks) at **`(960, 290)`**, Slot 2 Wolf (`速 800`, fast ground sprint) at **`(1140, 345)`**, Snack Bag (`零食袋`) at **`(560, 720)`** → `一键添加 (1780, 975)`.
- **Minigame: 变身菜鸡 (`solve-cabbage`)**: Advance `呱呱` intro dialog via `dialog` (do **not** click Skip, which cancels). Player transforms into a hovering helicopter Cabbage Chicken (`Space` ascend, `Ctrl` descend, `R` sonar ping reveals ~30×30px electric-blue circular target badges with white 4-point star cores for ~2s; `C` retries; 6/10/18 seeds unlock Bronze/Silver/Gold chests).
- **Hazard Regions**: `雾叶密林` (Mist-Leaf Forest) and fogged **`迹影崖谷` (`0%`)** inflict continuous corruption/poison-mist DoT (`-389 HP/s` + red vignette in `迹影崖谷` until Star-Sense Lv.20+ story unlock); `CHALLENGE FAILED / 复苏` revive button sits at `(960, 800)` (handled automatically by `azp.py recover`).
- **World Map Right-Hand Drawer Guard**: When a right-hand parchment drawer (`标记点` or facility card) is open on the map (`M`), the first click anywhere on the map only dismisses the drawer without selecting a waypoint; `map_teleport()` automatically dismisses open drawers before clicking the target Star Vein.
- **Booster Facilities (`增效设施`)**: Pressing `F` opens either the universal white repair modal (`确认 (1245, 660)`, `取消 (950, 660)`) or the right-hand parchment build panel (`建造 (1570, 885)`, close `Esc` or `X (1865, 50)`). Note: some facilities consume `尘石` (harvested in bulk via `azp.py home-harvest` from `家园-风铜矿场`).
- **Esc Menu & `Y` (`星之旅录`) Panels**: `Esc → 星临图志 (1345, 990)` → `调查奖励 (1835, 995)` → `点击继续 (960, 950)`; `成就 (1728, 990)`; close `X (1865, 50)`. `Y` (`星之旅录`) tabs at `x=230, y=175/290/390/485/575`, completed tasks sort to Row 1 `(680, 195)`, bottom milestone orbs (50/120/190/220/260) at `y=955, x=590/780/975/1170/1365` (`azp.py claim-journal`).
- **Kibo Capture (`G`)**: Press `G` to enter Star-Bond aim mode → `LMB` throws card → wait ~3.5s → press `G` to exit.
- **Combat Rotation**: `Z` summon Kibo → 4× `LMB` → `E` → `Q`/`R` → `Shift` dodge → rotate `1/2/3`. Use `--no-early-exit` for 3-wave rifts so spawn gaps do not terminate combat early.

## 6. Background / Non-Hijacking Input Test Matrix

Empirically verified on Windows 11 with elevated `AzurPromilia.exe` (`UnityWndClass`):

| Mechanism | Focused | Unfocused (Background) | Notes |
| --- | --- | --- | --- |
| `PrintWindow(hwnd, dc, 2)` (`bg-snap`) | ✅ Works | ✅ Works | Full 1920×1080 capture even when covered (window must not be minimized). |
| `SendInput` scan codes (`input_ctl.py`) | ✅ Works (Admin) | ❌ Needs focus | Requires elevated caller (`can_send_input: true`). |
| `PostMessage` `WM_KEYDOWN` | — | ❌ Ignored (`diff=0.01`) | Blocked by Unity Raw Input / `GameInputRawInputProxy.exe`. |
| `ViGEmBus` Virtual Xbox 360 (`gamepad.py`) | ✅ Switches to Xbox UI | ❌ Ignored (`mm_diff=0.25`) | Game mutes XInput when unfocused. |
| Idle-Aware Cooperative (`coop-explore`) | ✅ Yields in ~30ms via `PhysicalInputMonitor` | ✅ Waits until physically idle | Uses read-only `WH_KEYBOARD_LL`/`WH_MOUSE_LL` (`INJECTED` flag filter) to distinguish physical user input from our own `SendInput`. |
| MuMu IPC (`external_renderer_ipc.dll`, `azp/mumu.py`) | ✅ Works (~14ms cap + multi-touch) | ✅ Works (zero focus hijack!) | Tested on MuMu 6.8.8.0 (`>= 6.3.2.0`), Android 15 (`com.manjuu.promilia`, `display_id` bound per package). Note: entering world on Android emulator may trigger ACE emulator safety warning (`ACE安全风险提示`). |

## 7. MuMu Android 1920×1080 Mobile Layout Reference (`azp/mumu.py`)

- **Title Screen `开始旅程`**: `(960, 825)`
- **Left Virtual Joystick / Walk Toggle**: `(298, 795)` (radius `135px`)
- **Minimap Center**: `(200, 175)`; Home exit door `[H]`: `(316, 270)`
- **Right Combat Cluster**: Normal Attack `(1555, 810)`, Jump `(1600, 595)`, Dodge/Sprint `(1535, 985)`, Secondary/Guard `(1750, 795)`, Recall Kibo `(1815, 965)`, Lock-on `(1245, 790)`.
