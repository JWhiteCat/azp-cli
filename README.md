# AZP — 蓝色星原：旅谣 大世界探索与自动化 CLI（PC & MuMu 模拟器）

针对 1920×1080 窗口化 PC 客户端（`AzurPromilia.exe`）及 **MuMu 模拟器 12 / NX（v5/v6）** 实测沉淀的可复用 Python CLI 与脚本库（纯标准库 + `pywin32` / `PIL` / `numpy` / `scipy` + 轻量 `opencv` / `onnxruntime` / `rapidocr-onnxruntime`，共 62 个子命令，内置 100Hz Win32 `BitBlt` 视觉追踪、`PrintWindow` 后台截屏、`WH_KEYBOARD_LL`/`WH_MOUSE_LL` 物理输入让权监测、`ViGEmBus` 虚拟 Xbox 手柄驱动、**2.5D 大地图代价网格 + A\* 寻路 + 在线障碍记忆**、**模板匹配 + RapidOCR 语义感知与异常复苏**、**可选零 PyTorch ONNX YOLO 3D 目标检测插件**，以及参考 MAA / maa-cli 实现的 **MuMu `external_renderer_ipc.dll` 截图增强 + 触控增强**）。

## 模块结构

- [azp.py](azp.py)：统一命令行入口（62 个子命令，支持 `--backend pc|mumu` 及 `AZP_BACKEND`/`AZP_MUMU_*` 环境变量）
- [azp/mumu.py](azp/mumu.py)：MuMu 模拟器后端（参考 MAA `MumuExtras`/`MumuController`/`AdbController`），支持自动发现安装路径、`MuMuManager.exe info -v all` 实例枚举与包名（`com.manjuu.promilia`）display 自动绑定、版本门控（`>= 6.3.2.0`）、**截图增强**（`nemu_capture_display` 纯后台 ~14ms 1920×1080 RGBA 截屏）与**触控增强**（`nemu_input_event_finger_touch_down/up` 多指触控、平滑滑动、虚拟摇杆多指并发、`nemu_input_event_key_down/up` Linux 键码映射、`nemu_input_text` 及 ADB 降级）
- [azp/win.py](azp/win.py)：PC 端 DPI 感知窗口定位、焦点管理、前台/后台 `PrintWindow` 客户区截屏、`user_idle_seconds()` 空闲检测、UIPI 管理员权限预检
- [azp/input_ctl.py](azp/input_ctl.py)：基于 `SendInput` 硬件扫描码（`KEYEVENTF_SCANCODE`）的键鼠控制器、3D 相对视角旋转、地图拖拽与滚轮缩放
- [azp/gamepad.py](azp/gamepad.py)：基于本机 `ViGEmBus` / `ViGEmClient.dll` 的虚拟 Xbox 360 手柄驱动
- [azp/vision.py](azp/vision.py)：1920×1080 校准的大世界 HUD（含 `Q` 技能槽区分家园） / 夜间蓝黑与日间浅白加载屏 / `[F]` 交互条 / 奇波解谜发光 / 确认弹窗 / 战斗血条 / 顶部红点 / 小地图 POI / 截图 UID 打码（`mask_uid_image`）
- [azp/nav.py](azp/nav.py)：小地图 POI 连通域聚类、玩家朝向角度估计（PCA 窄尖端宽窄比防 180° 翻转）、按罗盘方位导航（`goto-bearing`，内置小地图差分防卡死绕障）与奇波解谜全属性自动轮询求解（`solve-puzzle`）
- [azp/navmesh.py](azp/navmesh.py)：纯视觉 **2.5D 大地图代价网格 + 8 邻域 A\* 寻路 + 在线障碍/通道记忆 + 3D 传送/跳跃有向边 + 小地图地形定位**（`nav-bake` / `nav-plan` / `nav-obstacle` / `nav-localize` / `nav-goto`，支持 `ground` / `fly` 双层模式）
- [azp/ocr_tpl.py](azp/ocr_tpl.py)：**模板匹配（OpenCV `TM_CCOEFF_NORMED` + `templates/` 图标库）+ RapidOCR（ONNX）** 语义感知与异常自动恢复（`ocr` / `read-f` / `hud-markers` / `map-pois` / `check-anomaly` / `recover`，覆盖大地图 POI 提取、3D 悬浮距离标解析、`[F]` 文本分类、死亡复苏 / 教学弹窗 / 维修材料 `0/10` 校验）
- [azp/detector.py](azp/detector.py)：**可选轻量 ONNX YOLO（YOLOv8/YOLO11）3D 目标检测插件 + 混合 CV 降级 + 数据集冷启动/预标注管线**（`yolo-detect` / `yolo-bench` / `yolo-init-dataset` / `yolo-prelabel`，零 PyTorch 运行时，未放权重时自动降级模板 + 抗蓝水晶山/发光种子 CV）
- [azp/actions.py](azp/actions.py)：可中断跑图收集、骑乘（`mount`）、360° 侦察（`scan360`）、奇波轮盘解谜（`kibo-puzzle`）、世界地图切换（`world-map`）、地图传送+边缘自动居中+等待加载（`teleport`）、退出家园（`exit-home`）、星结捕获（`capture`）、自动战斗切人（`combat`）、自动大世界巡逻（`explore`）及日常/游记/山谷秘藏/星临旅记/星临图志领奖（`claim-daily` / `claim-valley` / `claim-events` / `claim-codex` / `build-facility`）
- [azp/minigames.py](azp/minigames.py)：100Hz/25Hz `BitBlt` 实时视觉小游戏求解器（`solve-zuzanka` 11/12 三星通关、`solve-race` 坐骑挑战支持 ONNX YOLO 或抗水晶山白核 CV、`solve-cabbage` 变身菜鸡发光种子追踪）
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

# 3. 移动、罗盘导航、家园收取与地图传送
python azp.py mount --select wolf                           # 切换指定奇波坐骑 (wolf 速800 / bird 速350)
python azp.py home-harvest                                  # 进家园一键收取尘石/矿/木/结晶并自动返回大世界
python azp.py exit-home                                     # 退出星临者的家园 (H -> 确认 -> 等待大世界 HUD)
python azp.py goto-bearing 273.3 --sec 3.0                  # 对齐罗盘方位并冲刺拾取
python azp.py nav-goto --name wolf_central --goal 1386 241 --mount  # 2.5D A* 寻路导航（支持坐骑加速与遇阻自动重规划）
python azp.py world-map sprout_valley                       # 切换世界地图至新芽山谷 / charlulu
python azp.py teleport 890 610                              # 打开地图→点击锚点→点击传送→等待加载完成

# 4. 解谜、捕获、战斗与小游戏
python azp.py kibo-puzzle light                             # 释放指定属性奇波解谜
python azp.py solve-puzzle                                  # 自动轮询奇波轮盘属性直至解谜成功
python azp.py capture --throws 1                            # 星结捕获奇波 (G + LMB)
python azp.py combat --sec 16.0 --no-early-exit             # 多波次裂隙战斗轮换
python azp.py solve-zuzanka --max-drops 12 --lead 0.23      # 搭建祖赞卡之梯 100Hz 自动叠塔
python azp.py solve-race --max-sec 65 --gain 0.30           # 坐骑挑战 25Hz 实时转向（支持 --model ONNX YOLO 或抗蓝水晶 CV）
python azp.py solve-cabbage --max-sec 45                    # 变身菜鸡 25Hz 发光绿种子追踪（支持 --model ONNX YOLO）

# 5. 模板匹配 + RapidOCR 语义感知、异常复苏 & ONNX YOLO 检测管线
python azp.py map-pois --frame snaps/g3_map_now.png         # 提取大地图星脉/增效设施/头目坐标（直接喂给 nav-plan/nav-goto）
python azp.py hud-markers                                   # 识别 3D 视野悬浮图标（宝箱/挑战/乌咪/奇波蛋）+ OCR 距离(m)
python azp.py read-f                                        # OCR 读取当前 [F] 交互提示并分类
python azp.py check-anomaly                                 # 检测死亡复苏/教学弹窗/修复材料不足/建造卡片/冻结气泡
python azp.py recover --auto-confirm                        # 一键自动恢复异常界面回到大世界 HUD
python azp.py yolo-detect                                   # 运行 ONNX YOLO（models/azp_yolo.onnx）或混合 CV/模板降级检测
python azp.py yolo-init-dataset --max-images 120            # 从 snaps/ 自动采样并打码 UID 生成 Ultralytics 训练集框架
python azp.py yolo-prelabel                                 # 使用模板+CV 自动生成初始 YOLO .txt 标注

# 6. 空闲协作、一键领奖与提交前工具
python azp.py coop-explore --idle-sec 15 --steps 20         # PC 端空闲协作探索（物理动键鼠 ~30ms 立即让权）
python azp.py claim-daily                                   # 一键领取 F5 日常活跃 + F2 游记奖励
python azp.py claim-journal                                 # 一键领取 Y 星之旅录各页签任务与积分里程碑
python azp.py claim-valley                                  # 一键领取 M -> 探索度 -> 山谷秘藏奖励
python azp.py claim-events                                  # 安全领取 F1 星临旅记活动奖励
python azp.py claim-codex                                   # 一键领取 Esc -> 星临图志 -> 调查奖励
python azp.py build-facility                                # 自动修复/建造附近增效设施 (F -> 确认/建造)
python azp.py mask-uid snaps/a.png --out snaps/a_masked.png # 提交截图前对左下角 UID 进行纯色打码
```
