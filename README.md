# AZP — 蓝色星原：旅谣 大世界探索与自动化 CLI（PC & MuMu 模拟器）

针对 1920×1080 窗口化 PC 客户端（`AzurPromilia.exe`）及 **MuMu 模拟器 12 / NX（v5/v6）** 实测沉淀的可复用 Python CLI 与脚本库（纯标准库 + `pywin32` / `PIL` / `numpy` / `scipy`，共 41 个子命令，内置 100Hz Win32 `BitBlt` 视觉追踪、`PrintWindow` 后台截屏、`WH_KEYBOARD_LL`/`WH_MOUSE_LL` 物理输入让权监测、`ViGEmBus` 虚拟 Xbox 手柄驱动，以及参考 MAA / maa-cli 实现的 **MuMu `external_renderer_ipc.dll` 截图增强 + 触控增强**）。

## 模块结构

- [azp.py](azp.py)：统一命令行入口（41 个子命令，支持 `--backend pc|mumu` 及 `AZP_BACKEND`/`AZP_MUMU_*` 环境变量）
- [azp/mumu.py](azp/mumu.py)：MuMu 模拟器后端（参考 MAA `MumuExtras`/`MumuController`/`AdbController`），支持自动发现安装路径、`MuMuManager.exe info -v all` 实例枚举与包名（`com.manjuu.promilia`）display 自动绑定、版本门控（`>= 6.3.2.0`）、**截图增强**（`nemu_capture_display` 纯后台 ~14ms 1920×1080 RGBA 截屏）与**触控增强**（`nemu_input_event_finger_touch_down/up` 多指触控、平滑滑动、虚拟摇杆多指并发、`nemu_input_event_key_down/up` Linux 键码映射、`nemu_input_text` 及 ADB 降级）
- [azp/win.py](azp/win.py)：PC 端 DPI 感知窗口定位、焦点管理、前台/后台 `PrintWindow` 客户区截屏、`user_idle_seconds()` 空闲检测、UIPI 管理员权限预检
- [azp/input_ctl.py](azp/input_ctl.py)：基于 `SendInput` 硬件扫描码（`KEYEVENTF_SCANCODE`）的键鼠控制器、3D 相对视角旋转、地图拖拽与滚轮缩放
- [azp/gamepad.py](azp/gamepad.py)：基于本机 `ViGEmBus` / `ViGEmClient.dll` 的虚拟 Xbox 360 手柄驱动
- [azp/vision.py](azp/vision.py)：1920×1080 校准的大世界 HUD（含 `Q` 技能槽区分家园） / 夜间蓝黑与日间浅白加载屏 / `[F]` 交互条 / 奇波解谜发光 / 确认弹窗 / 战斗血条 / 顶部红点 / 小地图 POI / 截图 UID 打码（`mask_uid_image`）
- [azp/nav.py](azp/nav.py)：小地图 POI 连通域聚类、玩家朝向角度估计、按罗盘方位导航（`goto-bearing`，支持物理输入即时中断）与奇波解谜全属性自动轮询求解（`solve-puzzle`）
- [azp/actions.py](azp/actions.py)：可中断跑图收集、骑乘（`mount`）、360° 侦察（`scan360`）、奇波轮盘解谜（`kibo-puzzle`）、世界地图切换（`world-map`）、地图传送+等待加载（`teleport`）、退出家园（`exit-home`）、星结捕获（`capture`）、自动战斗切人（`combat`）、自动大世界巡逻（`explore`）及日常/游记/山谷秘藏/星临旅记一键领奖（`claim-daily` / `claim-valley` / `claim-events`）
- [azp/minigames.py](azp/minigames.py)：100Hz `BitBlt` 实时视觉小游戏求解器（`solve-zuzanka`，实测 11/12 三星全宝箱通关）
- [azp/bg_play.py](azp/bg_play.py)：PC 端 `PrintWindow` + `ViGEmBus` 虚拟手柄后台探测模块（`bg-test` / `bg-explore`）
- [azp/coop.py](azp/coop.py)：PC 端空闲协作探索模块（`coop-explore`，`PhysicalInputMonitor` 区分物理操作与注入 `SendInput`，人工动键鼠 ~30ms 内松键让权，支持自动退出家园）
- [tests/test_offline.py](tests/test_offline.py)：基于实机（UID 已打码）截图的离线视觉、导航、家园/弹窗、UID 打码及 MuMu 索引/版本/键码回归测试

## 前置权限与环境说明

- **PC 客户端**：`AzurPromilia.exe` 以管理员权限运行；请确保在**管理员权限终端**下运行，可通过 `python azp.py status` 确认 `privilege.can_send_input == true`。
- **MuMu 模拟器（零抢占前台）**：通过 `external_renderer_ipc.dll` 直连渲染器 IPC，无需抢占前台焦点即可实现纯后台截图与多指触控；**触控增强**要求 `MuMuManager.exe version >= 6.3.2.0`（低于该版本自动降级 ADB）。注：实测手游端 `com.manjuu.promilia` 进入世界时可能触发腾讯 ACE 模拟器环境安全风险提示弹窗。

## 常用命令速查

```powershell
# 1. PC 端状态、后台截屏与小地图 POI 侦察
python azp.py status                                        # 窗口/UIPI权限/HUD/家园/红点/小地图
python azp.py bg-snap --out snaps/bg.png                    # PrintWindow 后台截屏（不抢前台焦点）
python azp.py pois                                          # 小地图玩家朝向与未完成 POI 方位聚类
python azp.py scan360 --out-dir snaps/scout --prefix s1     # 4 方向 360° 全景侦察

# 2. MuMu 模拟器（截图增强 + 触控增强，纯后台不抢前台）
python azp.py mumu-list                                     # 枚举所有 MuMu 实例、版本与 IPC DLL 路径
python azp.py mumu-status                                   # 查看 MuMu 截图/触控增强状态、display_id 与 HUD
python azp.py mumu-test --frames 5 --out snaps/mumu.png     # 实测 5 帧 IPC 截图增强耗时并校验触控增强能力
python azp.py mumu-snap --out snaps/m.png --grid            # 纯后台 MuMu IPC 1920x1080 截图
python azp.py mumu-tap 960 825                              # 纯后台 MuMu IPC 多指触控点击
python azp.py mumu-swipe 1180 480 780 480 --duration 0.25   # 纯后台 MuMu IPC 平滑滑动
python azp.py mumu-key back                                 # 纯后台 MuMu IPC 按键（Android -> Linux 键码）
python azp.py mumu-text "hello"                             # 纯后台 MuMu IPC 文本输入
python azp.py --backend mumu --mumu-index 0 status          # 通过全局 --backend mumu 复用通用子命令

# 3. 移动、罗盘导航、退出家园与地图传送
python azp.py mount                                         # 切换奇波骑乘 (T)
python azp.py exit-home                                     # 退出星临者的家园 (H -> 确认 -> 等待大世界 HUD)
python azp.py goto-bearing 273.3 --sec 3.0                  # 对齐罗盘方位并冲刺拾取
python azp.py world-map sprout_valley                       # 切换世界地图至新芽山谷 / charlulu
python azp.py teleport 890 610                              # 打开地图→点击锚点→点击传送→等待加载完成

# 4. 解谜、捕获、战斗与小游戏
python azp.py kibo-puzzle light                             # 释放指定属性奇波解谜
python azp.py solve-puzzle                                  # 自动轮询奇波轮盘属性直至解谜成功
python azp.py capture --throws 1                            # 星结捕获奇波 (G + LMB)
python azp.py combat --sec 16.0 --no-early-exit             # 多波次裂隙战斗轮换
python azp.py solve-zuzanka --max-drops 12 --lead 0.23      # 搭建祖赞卡之梯 100Hz 自动叠塔

# 5. 空闲协作、一键领奖与提交前工具
python azp.py coop-explore --idle-sec 15 --steps 20         # PC 端空闲协作探索（物理动键鼠 ~30ms 立即让权）
python azp.py claim-daily                                   # 一键领取 F5 日常活跃 + F2 游记奖励
python azp.py claim-valley                                  # 一键领取 M -> 探索度 -> 山谷秘藏奖励
python azp.py claim-events                                  # 安全领取 F1 星临旅记活动奖励
python azp.py mask-uid snaps/a.png --out snaps/a_masked.png # 提交截图前对左下角 UID 进行纯色打码
```
