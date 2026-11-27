# AZP — 蓝色星原：旅谣 大世界探索与自动化 CLI

针对 1920×1080 窗口化 `AzurPromilia.exe` 实测沉淀的可复用 Python CLI 与脚本库（纯标准库 + `pywin32` / `PIL` / `numpy` / `scipy`，共 33 个子命令，内置 100Hz Win32 `BitBlt` 视觉追踪、`PrintWindow` 后台截屏、`WH_KEYBOARD_LL`/`WH_MOUSE_LL` 物理输入让权监测与 `ViGEmBus` 虚拟 Xbox 手柄驱动）。

## 模块结构

- [azp.py](azp.py)：统一命令行入口（33 个子命令）
- [azp/win.py](azp/win.py)：DPI 感知窗口定位、焦点管理、前台/后台 `PrintWindow` 客户区截屏、`user_idle_seconds()` 空闲检测、UIPI 管理员权限预检
- [azp/input_ctl.py](azp/input_ctl.py)：基于 `SendInput` 硬件扫描码（`KEYEVENTF_SCANCODE`）的键鼠控制器、3D 相对视角旋转、地图拖拽与滚轮缩放
- [azp/gamepad.py](azp/gamepad.py)：基于本机 `ViGEmBus` / `ViGEmClient.dll` 的虚拟 Xbox 360 手柄驱动（无键鼠冲突）
- [azp/vision.py](azp/vision.py)：1920×1080 校准的大世界 HUD（含 `Q` 技能槽区分家园） / 蓝黑加载屏（防夜间草地误判） / `[F]` 交互条 / 奇波解谜发光 / 确认弹窗 / 战斗血条 / 顶部红点 / 小地图 POI / 截图 UID 打码（`mask_uid_image`）
- [azp/nav.py](azp/nav.py)：小地图 POI 连通域聚类、玩家朝向角度估计、按罗盘方位导航（`goto-bearing`，支持物理输入即时中断）与奇波解谜全属性自动轮询求解（`solve-puzzle`）
- [azp/actions.py](azp/actions.py)：可中断跑图收集、骑乘（`mount`）、360° 侦察（`scan360`）、奇波轮盘解谜（`kibo-puzzle`）、世界地图切换（`world-map`）、地图传送+等待加载（`teleport`）、退出家园（`exit-home`）、星结捕获（`capture`）、自动战斗切人（`combat`）、自动大世界巡逻（`explore`）及日常/游记/山谷秘藏/星临旅记一键领奖（`claim-daily` / `claim-valley` / `claim-events`）
- [azp/minigames.py](azp/minigames.py)：100Hz `BitBlt` 实时视觉小游戏求解器（`solve-zuzanka`，实测 11/12 三星全宝箱通关）
- [azp/bg_play.py](azp/bg_play.py)：`PrintWindow` + `ViGEmBus` 虚拟手柄纯后台探测/探索模块（`bg-test` / `bg-explore`）
- [azp/coop.py](azp/coop.py)：空闲协作探索模块（`coop-explore`，内置 `PhysicalInputMonitor` 低级键鼠钩子区分物理操作与注入 `SendInput`，检测到人工动键鼠在 ~30ms 内松键让权，支持自动识别并退出家园）
- [tests/test_offline.py](tests/test_offline.py)：基于实机（UID 已打码）截图的离线视觉、导航、家园/弹窗与 UID 打码回归测试

## 前置权限要求（Windows UIPI）

`AzurPromilia.exe` 以管理员权限运行；请确保在**管理员权限终端**下运行，可通过 `python azp.py status` 确认 `privilege.can_send_input == true`。

## 常用命令速查

```powershell
# 1. 状态、后台截屏与小地图 POI 侦察
python azp.py status                                        # 窗口/UIPI权限/HUD/家园/红点/小地图
python azp.py bg-snap --out snaps/bg.png                    # PrintWindow 后台截屏（不抢前台焦点）
python azp.py pois                                          # 小地图玩家朝向与未完成 POI 方位聚类
python azp.py scan360 --out-dir snaps/scout --prefix s1     # 4 方向 360° 全景侦察

# 2. 移动、罗盘导航、退出家园与地图传送
python azp.py mount                                         # 切换奇波骑乘 (T)
python azp.py exit-home                                     # 退出星临者的家园 (H -> 确认 -> 等待大世界 HUD)
python azp.py goto-bearing 273.3 --sec 3.0                  # 对齐罗盘方位并冲刺拾取
python azp.py world-map sprout_valley                       # 切换世界地图至新芽山谷 / charlulu
python azp.py teleport 890 610                              # 打开地图→点击锚点→点击传送→等待加载完成

# 3. 解谜、捕获、战斗与小游戏
python azp.py kibo-puzzle light                             # 释放指定属性奇波解谜
python azp.py solve-puzzle                                  # 自动轮询奇波轮盘属性直至解谜成功
python azp.py capture --throws 1                            # 星结捕获奇波 (G + LMB)
python azp.py combat --sec 16.0 --no-early-exit             # 多波次裂隙战斗轮换
python azp.py solve-zuzanka --max-drops 12 --lead 0.23      # 搭建祖赞卡之梯 100Hz 自动叠塔

# 4. 空闲协作、一键领奖与提交前工具
python azp.py coop-explore --idle-sec 15 --steps 20         # 空闲协作探索（物理动键鼠 ~30ms 立即让权）
python azp.py claim-daily                                   # 一键领取 F5 日常活跃 + F2 游记奖励
python azp.py claim-valley                                  # 一键领取 M -> 探索度 -> 山谷秘藏奖励
python azp.py claim-events                                  # 安全领取 F1 星临旅记活动奖励
python azp.py mask-uid snaps/a.png --out snaps/a_masked.png # 提交截图前对左下角 UID 进行纯色打码
python azp.py bg-test                                       # 测试失焦状态下 ViGEmBus 虚拟手柄是否有效
```
