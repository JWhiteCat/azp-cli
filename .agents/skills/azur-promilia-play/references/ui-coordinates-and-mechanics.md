# Calibrated UI Coordinates, CV Thresholds & Game Mechanics (1920×1080 Client)

All coordinates below are in **game client pixels (`1920×1080`)**, converted to physical desktop pixels automatically by `azp/win.py` (`SetProcessDpiAwareness(2)`).

## 1. HUD & Vision Calibration (`azp/vision.py`)

- **Open-World HP Bar**: `x=830..1090, y=996..1006` (calibrated bright white rows at `y=998..1003`, ratio `> 0.14` indicates live HUD).
- **Loading Screen**: Solid dark/light frames OR Azur Promilia's dark-navy bottom tip banner at `x=80..1840, y=820..1070` (`mean < 42.0` and `std < 24.0`).
- **`[F]` Interact Pill**: Active white/light-gray option bar at `x=1145..1420, y=548..590` (bright low-saturation pixel ratio `> 0.35`).
- **Kibo Puzzle `Q` Glow**: `x=1600..1696, y=942..1038` (bright gold/green aura ratio `> 0.20`).
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
  - Region `可领取 / 探索度` button: `(1850, 805)`
  - `山谷秘藏` gift box icon (inside Exploration panel): `(1195, 800)`
  - `山谷秘藏` Lv.1 claim bar (click left side, not item icon): `(1380, 415)`
  - Waypoint `传送` button (after clicking a blue waypoint): `(1670, 1000)`
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

- **3D Camera Yaw Calibration**: Roughly `8.4 px/deg` (`dx = 750` ≈ 90° right turn).
- **Mount (`T`)**: Fast ground travel + high jump (`Space`) for floating Star Melodies; auto-dismounts in deep water.
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
| Idle-Aware Cooperative (`coop-explore`) | ✅ Yields on user input | ✅ Waits until idle | Safe way to share one desktop without fighting the user. |
