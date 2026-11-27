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
3. **Respect foreground exclusivity & user activity (`PhysicalInputMonitor`)**:
   - Empirically verified: `AzurPromilia.exe` supports background `PrintWindow` capture (`azp.py bg-snap`), **but ignores both `PostMessage` and `ViGEmBus` Xbox 360 gamepad input when unfocused**.
   - Before taking foreground control, run `python azp.py bg-snap --out snaps/check.png` to inspect `focused` and `idle_sec`.
   - If the user is actively using the PC or game (`idle_sec < 15`), do **not** hijack focus; either wait passively (e.g., via `schedule`) or run `azp.py coop-explore --idle-sec 15` (uses read-only `WH_KEYBOARD_LL`/`WH_MOUSE_LL` hooks to distinguish physical input from injected `SendInput` and aborts walks/combat within ~30ms on user takeover).
   - Whenever pausing, asking the user, or finishing a run, always call `windows-mcp` `ControlStatus(release=true)`.
4. **Exit Home Ranch before open-world runs**:
   - If `azp.py status` reports `in_home_ranch: true` (or `hud.confirm_dialog: true` for `是否要离开家园？`), run `python azp.py exit-home` (or let `coop-explore` auto-exit Home Ranch; pass `--no-exit-home` to opt out).
5. **MuMu Emulator Backend (Zero-Focus-Hijack IPC 截图增强 & 触控增强)**:
   - When targeting MuMu Player 12 / NX (`com.manjuu.promilia`), use `azp.py mumu-*` commands or pass `--backend mumu` (optionally `--mumu-index <n>`, `--mumu-addr <host:port|emulator-xxxx>`, or `--mumu-path <dir>`).
   - `azp/mumu.py` automatically discovers the MuMu install root, enumerates instances via `MuMuManager.exe info -v all`, binds `nemu_get_display_id` to `com.manjuu.promilia` (avoiding other emulator instances like Arknights), checks `MuMuManager.exe version >= 6.3.2.0` before enabling IPC touch/key input (`nemu_input_event_finger_touch_down/up`, `nemu_input_event_key_down/up`, `nemu_input_text`), and captures 1920×1080 background RGBA frames via `nemu_capture_display` (~14ms/frame) with zero foreground focus hijack.
   - **Anti-cheat note**: On Android MuMu, `com.manjuu.promilia` may display a Tencent ACE emulator warning (`ACE安全风险提示：检测到您运行在模拟器内...`) upon entering the game world. Never attempt to bypass ACE; report it clearly to the user if encountered.

## 2. Core Open-World Exploration & Reward Workflow

Work in tight **observe → act → verify** batches (chain multiple short CLI calls per PowerShell turn to minimize latency).

### Step A — Claim Ready Rewards & Check Goals
1. Check `red_dots` from `azp.py status`.
2. Claim ready rewards via one-line commands:
   ```powershell
   python azp.py claim-daily --tasks 4   # F5 Daily Activity + F2 Battle Pass
   python azp.py claim-valley            # M -> 探索度 -> 山谷秘藏 milestones
   python azp.py claim-events            # F1 星临旅记 (safe against shifting 前往 rows)
   ```
3. Track regional Exploration Progress milestones (e.g., Sprout Valley Lv.2 `550` EXP unlocks flying).

### Step B — Scout Before Moving
Avoid blind wandering. Combine minimap POI clustering with 360° scouting:
```powershell
python azp.py pois
python azp.py scan360 --out-dir snaps/scout --prefix s1
```
- `pois` returns player heading (`0=N, 90=E, 180=S, 270=W`) and clusters bright white minimap icons (`completed: false` filters out already-finished challenges with green checkmarks).
- Use `python azp.py goto-bearing <deg> --sec <sec>` to turn directly toward a POI and sprint there while spamming `F`.
- Use `python azp.py mount` (`T`) on wide open roads/meadows for faster travel (and `T + Space` to reach elevated Star Melodies). Avoid `--jump` while swimming.

### Step C — Execute High-Value Reward Targets (Priority Order)
1. **Unactivated Star Vein Teleporters (`星脉节点`)**: High Exploration EXP + `90 红晶` + new fast-travel anchor. Approach and run `azp.py interact --taps 6` + `azp.py dismiss`.
2. **Kibo Puzzles (`奇波解谜`)**: When near a puzzle (`near_kibo_puzzle: true` or bunny icon overhead):
   - If the element is obvious (amber crystal → `light`, sprout/stump → `grass`, dry leaf pile → `fire`), run `python azp.py kibo-puzzle <element>`.
   - Otherwise run `python azp.py solve-puzzle` to cycle wheel elements automatically until the `Q` puzzle glow clears, then loot drops.
3. **Exploration Challenges (`探索挑战`)**:
   - **Zuzanka Ladder (`搭建祖赞卡之梯`)**: Start challenge (`F` → click `开始 (1585, 910)`), then run the 100Hz BitBlt CV solver:
     ```powershell
     python azp.py solve-zuzanka --max-drops 12 --lead 0.23 --settle 2.15
     ```
     Exit (`click 770 845`) and loot all 3 spawned chests (Bronze/Silver/Gold). Note: scroll or sidestep past `拆除祖赞卡之梯` if it sits at slot #1 in the `[F]` list.
   - **Multi-wave Rifts / Guarded Chests**: Use `python azp.py combat --sec 18 --no-early-exit` (prevents early exit during wave spawn gaps), then `azp.py interact --taps 6`.
4. **Star Melodies (`星律`), Chests, Wumi (`乌咪`) & New Kibo**:
   - Jump / mount-jump into floating golden Saturn-ring orbs (`星律`).
   - Aim at uncaptured Kibo (species with white card icon next to nameplate) and run `python azp.py capture --throws 1`.
5. **Fast Travel, Region Switching & Cooperative Patrol**:
   - Switch region maps via `python azp.py world-map sprout_valley` (or `charlulu`), snap with `--grid --grid-step 160` if needed, then run `python azp.py teleport <x> <y> --map-open`.
   - Run `python azp.py coop-explore --idle-sec 15 --steps 10 --snap-dir snaps/coop_run` (with `PowerShell(timeout=120)`) for safe idle-aware patrol, or `python azp.py explore --steps 10 --step-sec 2.6` when foreground play is explicitly requested.

## 3. Distilling New Mechanics & Committing Safely

Whenever encountering a new repeatable UI flow, puzzle, or minigame:
1. Capture a 1920×1080 reference frame with `--grid --grid-step 160` to measure exact client coordinates and color signatures.
2. For latency-sensitive minigames, use `FastROIGrabber` (Win32 GDI `BitBlt`, ~4ms/frame) from `azp/minigames.py` instead of `PIL.ImageGrab` (~110ms on multi-monitor setups).
3. Add the reusable function under `azp/` and expose a clean subcommand in `azp.py`.
4. **Mask UID before committing ANY screenshot**:
   - Never commit raw screenshots with visible bottom-left UID. Always run:
     ```powershell
     python azp.py mask-uid snaps/<raw>.png --out snaps/<fixture>.png
     ```
   - Whitelist only masked offline test fixtures in `.gitignore` and add assertions in `tests/test_offline.py` (which enforces masked UID boxes on all committed fixtures).
5. Update `README.md` and this skill's references when adding subcommands or calibrated coordinates.

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
- [UI Coordinates, CV Thresholds & Game Mechanics](./references/ui-coordinates-and-mechanics.md) — Calibrated 1920×1080 coordinates, Kibo wheel slots, menu buttons, Home Ranch exit dialog, Zuzanka physics, and background input test matrix.
- [AZP CLI Command Reference](./references/cli-reference.md) — Complete catalog of all 41 `azp.py` subcommands (PC & MuMu) and module responsibilities.
