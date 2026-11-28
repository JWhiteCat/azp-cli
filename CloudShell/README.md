# AZP CloudShell

Native Windows C++20 Win32 + WebView2 host window for playing **Azur Promilia (蓝色星原：旅谣)** cloud gaming in the background with `azp --backend cloud`.

## Contract with `azp`

| Property | Value |
| --- | --- |
| Window class | `Azp.CloudShell` |
| Window title | `AZP CloudShell` (fixed; does not follow `document.title`, override via `--title`) |
| Client area | `1280×720` physical pixels (DPI Per-Monitor v2, `RasterizationScale = 1.0`, non-resizable) |
| Screenshot | `PrintWindow` (`PW_RENDERFULLCONTENT`, works when occluded/backgrounded) |
| Input | Win32 `PostMessageW` bridged to WebView2 CDP `Input.*` |
| Relative camera | `WM_MOUSEMOVE` with `wParam = MK_XBUTTON2` (`0x0040`, when XBUTTON2 is not held), `lParam = MAKELPARAM(W/2 + dx, H/2 + dy)` |

## Prerequisites

- **Runtime**: Microsoft Edge WebView2 Runtime (preinstalled on Windows 11 and most Windows 10 systems). No VC++ Redistributable or external `WebView2Loader.dll` required (statically linked).
- **Build**: Visual Studio 2022 (MSVC C++20), Windows 10/11 SDK, CMake 3.24+, Ninja. WebView2 SDK (`1.0.2210.55`) is automatically downloaded & SHA256-verified from NuGet during CMake configure (or pass `--webview2-sdk-dir` for offline builds).

## Build

```powershell
python CloudShell/build.py
```

The standalone binary is copied to `CloudShell/dist/azp-cloud.exe`.

## CLI Usage

```powershell
CloudShell\dist\azp-cloud.exe `
  [--url <url>] `
  [--width 1280] [--height 720] `
  [--title "AZP CloudShell"] `
  [--user-data-dir <dir>] `
  [--log-file <file>] `
  [--inject <script.js>]... `
  [--devtools] `
  [--debug-port <port>] `
  [--disable-webgl2]
```

- Default URL: `https://azurpromilia.manjuu.com/cloudgame`
- Default user data dir (persists cookies/login): `<cwd>/cache/cloud-shell/WebView2`
- Default log file: `<cwd>/cache/cloud-shell/cloud-shell.log`
