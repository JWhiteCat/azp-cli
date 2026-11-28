#!/usr/bin/env python3
"""Build helper for AZP CloudShell (Windows MSVC + CMake + Ninja)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def find_vs_dev_cmd() -> Path | None:
    """Locate Visual Studio 2022 VsDevCmd.bat via vswhere or common install paths."""
    env_vs = os.environ.get("VSINSTALLDIR")
    if env_vs:
        cand = Path(env_vs) / "Common7" / "Tools" / "VsDevCmd.bat"
        if cand.is_file():
            return cand

    vswhere_candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio"
        / "Installer"
        / "vswhere.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft Visual Studio"
        / "Installer"
        / "vswhere.exe",
    ]
    for vswhere in vswhere_candidates:
        if vswhere.is_file():
            try:
                out = subprocess.check_output(
                    [
                        str(vswhere),
                        "-latest",
                        "-products",
                        "*",
                        "-requires",
                        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                        "-property",
                        "installationPath",
                    ],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                ).strip()
                if out:
                    cand = Path(out) / "Common7" / "Tools" / "VsDevCmd.bat"
                    if cand.is_file():
                        return cand
            except subprocess.SubprocessError:
                pass

    # Fallback search across standard and custom VS 2022 locations
    search_roots = [
        Path(r"C:\Program Files\Microsoft Visual Studio\2022"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022"),
        Path(r"D:\software\Microsoft Visual Studio\2022"),
        Path(r"D:\Program Files\Microsoft Visual Studio\2022"),
    ]
    for base in search_roots:
        for edition in ("Community", "Professional", "Enterprise", "BuildTools"):
            cand = base / edition / "Common7" / "Tools" / "VsDevCmd.bat"
            if cand.is_file():
                return cand
    return None


def find_extra_tool_dirs(vs_dev_cmd: Path) -> list[str]:
    """Return extra PATH directories for cmake / ninja if not already on PATH."""
    dirs: list[str] = []
    vs_root = vs_dev_cmd.parents[2]
    vs_cmake_bin = vs_root / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "CMake" / "bin"
    vs_ninja_bin = vs_root / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "Ninja"
    for d in (vs_cmake_bin, vs_ninja_bin):
        if d.is_dir():
            dirs.append(str(d))
    return dirs


def load_vs_env(vs_dev_cmd: Path, arch: str, base_env: dict[str, str]) -> dict[str, str]:
    """Run VsDevCmd.bat and capture the resulting environment variables."""
    vs_arch = "arm64" if arch == "arm64" else "amd64"
    # Use a single command string with cmd.exe /d /s /c so cmd strips outer quotes cleanly
    cmd_line = f'cmd.exe /d /s /c ""{vs_dev_cmd}" -no_logo -host_arch=amd64 -arch={vs_arch} >nul && set"'
    proc = subprocess.run(
        cmd_line,
        cwd=str(ROOT),
        env=base_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)

    captured: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key:
            captured[key] = value
    if not captured:
        print("Error: Failed to capture environment from VsDevCmd.bat.", file=sys.stderr)
        raise SystemExit(1)
    return captured


def run_step(cmd: list[str], env: dict[str, str]) -> None:
    print(f"[build] {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        print("Error: CloudShell only supports Windows.", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description="Build AZP CloudShell (azp-cloud.exe)")
    parser.add_argument("--arch", choices=("x64", "arm64"), default="x64", help="Target architecture (default: x64)")
    parser.add_argument(
        "--config",
        choices=("RelWithDebInfo", "Debug"),
        default="RelWithDebInfo",
        help="CMake build configuration (default: RelWithDebInfo)",
    )
    parser.add_argument(
        "--webview2-sdk-dir",
        type=Path,
        default=None,
        help="Optional path to extracted Microsoft.Web.WebView2 NuGet package root",
    )
    parser.add_argument("--clean", action="store_true", help="Remove build directory before configuring")
    args = parser.parse_args(argv)

    vs_dev_cmd = find_vs_dev_cmd()
    if not vs_dev_cmd:
        print(
            "Error: Visual Studio 2022 with C++ Desktop tools not found. "
            "Install VS 2022 C++ Build Tools or set VSINSTALLDIR.",
            file=sys.stderr,
        )
        return 1

    arm64 = args.arch == "arm64"
    configure_preset = "NinjaMulti Win32 ARM64" if arm64 else "NinjaMulti Win32"
    build_dir = ROOT / ("build-arm64" if arm64 else "build")

    if args.clean and build_dir.exists():
        print(f"[build] Cleaning {build_dir}")
        shutil.rmtree(build_dir)

    env = load_vs_env(vs_dev_cmd, args.arch, os.environ.copy())
    extra_dirs = find_extra_tool_dirs(vs_dev_cmd)
    if extra_dirs:
        path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
        env[path_key] = os.pathsep.join(extra_dirs + [env.get(path_key, "")])

    configure_cmd = ["cmake", "--preset", configure_preset]
    if args.webview2_sdk_dir:
        sdk_dir = str(args.webview2_sdk_dir.resolve()).replace("\\", "/")
        configure_cmd.append(f"-DWEBVIEW2_SDK_DIR={sdk_dir}")

    build_cmd = ["cmake", "--build", str(build_dir), "--config", args.config]

    run_step(configure_cmd, env)
    run_step(build_cmd, env)

    built_exe = build_dir / "bin" / args.config / "azp-cloud.exe"
    if not built_exe.is_file():
        print(f"Error: Expected binary not found at {built_exe}", file=sys.stderr)
        return 1

    dist_dir = ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    dist_exe = dist_dir / "azp-cloud.exe"
    shutil.copy2(built_exe, dist_exe)

    size = dist_exe.stat().st_size
    print(f"[build] Installed {dist_exe} ({size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
