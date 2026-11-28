# AZP — 蓝色星原：旅谣 大世界探索与自动化 CLI（PC / MuMu / 内置云游戏 CloudShell）

针对 1920×1080 窗口化 PC 客户端（`AzurPromilia.exe`）、**内置原生 WebView2 云游戏容器（`azp-cloud.exe`，免下载完整客户端、纯后台零抢占前台）** 及 **MuMu 模拟器 12 / NX（v5/v6）** 实测沉淀的可复用 Python CLI 与脚本库（纯标准库 + `pywin32` / `PIL` / `numpy` / `scipy` + 轻量 `opencv` / `onnxruntime` / `rapidocr-onnxruntime`，内置 100Hz Win32 `BitBlt` 视觉追踪、`PrintWindow` 后台截屏、`PostMessageW -> WebView2 CDP` 后台键鼠与 3D 相对视角驱动、`WH_KEYBOARD_LL`/`WH_MOUSE_LL` 物理输入让权监测、`ViGEmBus` 虚拟 Xbox 手柄驱动、**3D 自由飞行与安全迫降（`fly-bearing` / `land`）+ 闭环 POI 自动清扫（`hunt-pois`）**、**2.5D 大地图代价网格 + A\* 寻路 + 在线障碍记忆**、**模板匹配 + RapidOCR 语义感知与异常复苏**、**可选零 PyTorch ONNX YOLO 3D 目标检测插件**，以及参考 MAA / maa-cli 实现的 **MuMu `external_renderer_ipc.dll` 截图增强 + 触控增强**）。

## 模块结构

- [azp.py](azp.py)：统一命令行入口（支持 `--backend pc|mumu|cloud` 及别名 `cloud_web`/`cloudweb`，`AZP_BACKEND`/`AZP_CLOUD_*`/`AZP_MUMU_*` 环境变量，内置 `cloud launch/status/close/build` 生命周期管理）
- [azp/cloud.py](azp/cloud.py)：内置云游戏后端（面向原生 C++20/WebView2 `CloudShell` 窗口 `Azp.CloudShell`，`PrintWindow` 纯后台截屏、`PostMessageW` 后台键鼠/滚轮/`WM_CHAR` 文本与 `MK_XBUTTON2` 3D 相对视角旋转、`1920×1080` 逻辑坐标自动等比缩放至 `1280×720` 物理客户区、支持 `manjuu` / `manjuu-alt` / `163` 预设或自定义 URL、独立持久化的 `cache/cloud-shell/WebView2` 登录态目录）
- [CloudShell/](CloudShell/)：原生 Windows C++20 WebView2 云游戏壳（静态 CRT `/MT` + 静态 `WebView2LoaderStatic.lib`，输出单文件 `CloudShell/dist/azp-cloud.exe`，内置反遮挡休眠参数、`InputBridge` 与 `cloud_policy.js` 指针锁定/前台状态桥接）
- [azp/mumu.py](azp/mumu.py)：MuMu 模拟器后端（参考 MAA `MumuExtras`/`MumuController`/`AdbController`），支持自动发现安装路径、`MuMuManager.exe info -v all` 实例枚举与包名（`com.manjuu.promilia`）display 自动绑定、版本门控（`>= 6.3.2.0`）、**截图增强**（`nemu_capture_display` 纯后台 ~14ms 1920×1080 RGBA 截屏）与**触控增强**（`nemu_input_event_finger_touch_down/up` 多指触控、平滑滑动、虚拟摇杆多指并发、`nemu_input_event_key_down/up` Linux 键码映射、`nemu_input_text` 及 ADB 降级）
- [azp/win.py](azp/win.py)：PC 端 DPI 感知窗口定位（含 `IsIconic` 最小化窗口自动检测与 `SW_RESTORE` 恢复）、焦点管理、前台/后台 `PrintWindow` 客户区截屏、`user_idle_seconds()` 空闲检测、UIPI 管理员权限预检
- [azp/input_ctl.py](azp/input_ctl.py)：基于 `SendInput` 硬件扫描码（`KEYEVENTF_SCANCODE`）的键鼠控制器、3D 相对视角旋转、地图拖拽与滚轮缩放
- [azp/gamepad.py](azp/gamepad.py)：基于本机 `ViGEmBus` / `ViGEmClient.dll` 的虚拟 Xbox 360 手柄驱动
- [azp/vision.py](azp/vision.py)：1920×1080 校准的大世界 HUD（含 `Q` 技能槽 / 高空飞行 `G` 卡槽兜底、字幕防遮挡且带上限防高亮地面误触的飞行态检测 `is_flying`，以及非战斗血条绯红扣血检测 `hazard_vignette`） / 夜间蓝黑与日间浅白加载屏 / `[F]` 交互条 / 奇波解谜发光与 `[Q]` 提示徽标 / 确认弹窗 / 战斗血条 / 顶部红点 / 小地图 POI / 截图 UID 打码（`mask_uid_image`）
- [azp/nav.py](azp/nav.py)：小地图 POI 连通域聚类、玩家朝向角度估计（PCA 窄尖端宽窄比 + 180° 翻转护栏）、地面按罗盘方位导航（`goto-bearing`）、**3D 自由飞行导航（`fly-bearing`，校准 `5.8/5.5 px/deg` 飞行偏航闭环 + `W+Space` 斜爬坡 + 永不按 `S` + 毒雾扣血熔断）与零坠伤安全降落（`land`）**、**闭环小地图 POI 自动清扫（`hunt-pois`，遇崖切飞行、自动解谜/建设施/跳过已建建筑/清怪拾取/人工让权）**，以及奇波解谜全属性自动轮询求解（`solve-puzzle`）
- [azp/navmesh.py](azp/navmesh.py)：纯视觉 **2.5D 大地图代价网格 + 8 邻域 A\* 寻路 + 在线障碍/通道记忆 + 3D 传送/跳跃有向边 + 小地图地形定位**（`nav-bake` / `nav-plan` / `nav-obstacle` / `nav-localize` / `nav-goto`，支持 `ground` / `fly` 双层模式）
- [azp/ocr_tpl.py](azp/ocr_tpl.py)：**模板匹配（OpenCV `TM_CCOEFF_NORMED` + `templates/` 图标库）+ RapidOCR（ONNX）** 语义感知与异常自动恢复（`ocr` / `read-f` / `hud-markers` / `map-pois` / `check-anomaly` / `recover`，覆盖大地图 POI 提取、3D 悬浮距离标解析、`[F]` 文本分类含未建 `facility` vs 已建 `built_facility` 如 `元素祭坛`/`采集小屋`/`伐木场`/`星辉矿场`、死亡复苏 / 教学弹窗 / 维修材料 `0/10` 校验）
- [azp/detector.py](azp/detector.py)：**可选轻量 ONNX YOLO（YOLOv8/YOLO11）3D 目标检测插件 + 混合 CV 降级 + 数据集冷启动/预标注管线**（`yolo-detect` / `yolo-bench` / `yolo-init-dataset` / `yolo-prelabel`，零 PyTorch 运行时，未放权重时自动降级模板 + 抗蓝水晶山/发光种子 CV）
- [azp/actions.py](azp/actions.py)：可中断跑图收集、骑乘（`mount`）、360° 侦察（`scan360`）、奇波轮盘解谜（`kibo-puzzle`）、世界地图切换（`world-map`）、地图传送+边缘自动居中+等待加载（`teleport`）、退出家园（`exit-home`）、星结捕获（`capture`）、自动战斗切人（`combat`）、自动大世界巡逻（`explore`）及日常/游记/山谷秘藏/星临旅记/星临图志领奖（`claim-daily` / `claim-valley` / `claim-events` / `claim-codex` / `build-facility`）
- [azp/minigames.py](azp/minigames.py)：100Hz/25Hz `BitBlt` 实时视觉小游戏求解器（`solve-zuzanka` 11/12 三星通关、`solve-race` 坐骑挑战支持 ONNX YOLO 或抗水晶山白核 CV、`solve-cabbage` 变身菜鸡发光种子追踪）
- [azp/bg_play.py](azp/bg_play.py)：PC 端 `PrintWindow` + `ViGEmBus` 虚拟手柄后台探测模块（`bg-test` / `bg-explore`）
- [azp/coop.py](azp/coop.py)：PC 端空闲协作探索模块（`coop-explore`，`PhysicalInputMonitor` 区分物理操作与注入 `SendInput`，人工动键鼠 ~30ms 内松键让权，支持自动退出家园）
- [tests/test_offline.py](tests/test_offline.py)：基于实机（UID 已打码）截图的离线视觉、导航、家园/弹窗、UID 打码及 MuMu 索引/版本/键码回归测试
- [tests/test_cloud.py](tests/test_cloud.py)：CloudShell URL 预设、CLI 参数构造、VK/扩展键 `lParam`、`WM_CHAR` UTF-16 代理对、`MK_XBUTTON2` 3D 相对视角分步钳位、`1920×1080 -> 1280×720` 坐标缩放及 `--backend cloud` 全链路离线单元测试

## 前置权限与环境说明

- **PC 客户端**：`AzurPromilia.exe` 以管理员权限运行；请确保在**管理员权限终端**下运行，可通过 `python azp.py status` 确认 `privilege.can_send_input == true`。
- **内置云游戏 CloudShell（零抢占前台、无需管理员权限）**：
  - 依赖 Windows 本机已安装的 Microsoft Edge WebView2 Runtime；构建阶段需要 Visual Studio 2022 C++ / CMake（首次运行 `python azp.py cloud build` 会自动下载并校验 WebView2 NuGet SDK，产物输出至 `CloudShell/dist/azp-cloud.exe`）。
  - 默认打开官方《云·蓝色星原》网页端（`manjuu` -> `https://azurpromilia.manjuu.com/cloudgame`），也可切换 `--cloud-url manjuu-alt`（`https://azurpromilia.manjuu.com/cloud/`）、`--cloud-url 163`（`https://cg.163.com`）或任意 `http(s)://` 地址；登录 Cookie 与缓存持久化保存在 `cache/cloud-shell/WebView2`。
  - 所有操作通过 `PrintWindow` 后台截屏与 `PostMessageW -> WebView2 CDP` 投递，窗口被遮挡时依然可完整执行跑图、视角旋转、OCR、解谜与小游戏（逻辑坐标保持 `1920×1080`，自动等比缩放至 `1280×720` 物理客户区）。
- **MuMu 模拟器（零抢占前台）**：通过 `external_renderer_ipc.dll` 直连渲染器 IPC，无需抢占前台焦点即可实现纯后台截图与多指触控；**触控增强**要求 `MuMuManager.exe version >= 6.3.2.0`（低于该版本自动降级 ADB）。注：实测手游端 `com.manjuu.promilia` 进入世界时可能触发腾讯 ACE 模拟器环境安全风险提示弹窗。

## 常用命令速查

```powershell
# 0. 内置云游戏 CloudShell（纯后台零抢占前台，无需管理员权限）
python azp.py cloud build                                   # 编译原生 C++20 WebView2 容器 -> CloudShell/dist/azp-cloud.exe
python azp.py cloud launch                                  # 启动官方《云·蓝色星原》网页端 (默认 1280x720，自动保存登录态)
python azp.py cloud launch --url 163 --width 1280 --height 720  # 切换网易云游戏或自定义 URL
python azp.py cloud status                                  # 查看容器进程、截屏耗时、HUD/红点/小地图状态
python azp.py --backend cloud snap --out snaps/cloud.png    # 纯后台 PrintWindow 截屏
python azp.py --backend cloud click 960 540                 # 1920x1080 逻辑坐标自动缩放到物理客户区后台点击
python azp.py --backend cloud turn --dx 360                 # MK_XBUTTON2 协议后台旋转 3D 视角
python azp.py --backend cloud run --dir w --sec 2.5         # 后台冲刺移动（不抢占桌面焦点/键鼠）
python azp.py --backend cloud type "hello"                  # WM_CHAR UTF-16 后台文本输入
python azp.py --backend cloud click-text "开始旅程"          # RapidOCR 找字并后台点击
python azp.py --backend cloud hunt-pois --steps 6           # 直接复用大世界探索/寻路/领奖/小游戏等全部子命令
python azp.py cloud close                                   # 优雅关闭窗口（保留 cache/cloud-shell/WebView2 登录态）

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
python azp.py fly-bearing 85 --sec 4.0 --climb 3.5 --land   # 3D 自由飞行导航（W+Space 越崖 + 3 轮飞行偏航对齐 + 毒雾熔断 + Ctrl 零坠伤迫降）
python azp.py land                                          # 从高空安全按 Ctrl 降落至触地后再按 T 下坐骑（零坠伤）
python azp.py hunt-pois --steps 12 --idle-sec 15            # 闭环小地图 POI 自动清扫（遇崖切飞行、自动解谜/建设施/战斗拾取，动键鼠 ~30ms 让权）
python azp.py nav-goto --name wolf_central --goal 1386 241 --mount  # 2.5D A* 寻路导航（支持 --mount 或 --mode fly）
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
