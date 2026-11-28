#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#include <windows.h>

#include <objbase.h>
#include <shellapi.h>

#include "Log.h"
#include "Resources.h"
#include "ShellWindow.h"

namespace
{

constexpr const wchar_t* kDefaultUrl = L"https://azurpromilia.manjuu.com/cloudgame";
constexpr const wchar_t* kDefaultTitle = L"AZP CloudShell";

std::optional<std::wstring> readFile(const std::filesystem::path& path)
{
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        return std::nullopt;
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    return cloudshell::Utf8ToWide(buffer.str());
}

bool parseOptions(
    cloudshell::ShellOptions& options,
    std::vector<std::filesystem::path>& inject_paths,
    std::vector<std::string>& errors)
{
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) {
        errors.emplace_back("CommandLineToArgvW failed");
        return false;
    }

    bool ok = true;
    for (int i = 1; i < argc && ok; ++i) {
        const std::wstring arg = argv[i];
        const bool has_value = i + 1 < argc;
        if (arg == L"--devtools") {
            options.devtools = true;
        }
        else if (arg == L"--disable-webgl2") {
            options.disable_webgl2 = true;
        }
        else if (arg == L"--url" && has_value) {
            options.url = argv[++i];
        }
        else if (arg == L"--title" && has_value) {
            options.title = argv[++i];
        }
        else if (arg == L"--width" && has_value) {
            options.width = _wtoi(argv[++i]);
        }
        else if (arg == L"--height" && has_value) {
            options.height = _wtoi(argv[++i]);
        }
        else if (arg == L"--user-data-dir" && has_value) {
            options.user_data_dir = std::filesystem::absolute(argv[++i]);
        }
        else if (arg == L"--log-file" && has_value) {
            options.log_file = std::filesystem::absolute(argv[++i]);
        }
        else if (arg == L"--debug-port" && has_value) {
            options.debug_port = _wtoi(argv[++i]);
        }
        else if (arg == L"--inject" && has_value) {
            inject_paths.emplace_back(argv[++i]);
        }
        else {
            errors.emplace_back("unknown or incomplete argument: " + cloudshell::WideToUtf8(arg));
            ok = false;
        }
    }

    LocalFree(argv);

    if (ok && (options.width <= 0 || options.height <= 0)) {
        errors.emplace_back("width and height must be positive integers");
        ok = false;
    }
    if (ok && (options.debug_port < 0 || options.debug_port > 65535)) {
        errors.emplace_back("debug-port must be between 0 and 65535");
        ok = false;
    }
    return ok;
}

} // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int)
{
    const std::filesystem::path working_dir = std::filesystem::current_path();

    cloudshell::ShellOptions options {
        .url = kDefaultUrl,
        .title = kDefaultTitle,
        .user_data_dir = working_dir / "cache" / "cloud-shell" / "WebView2",
        .log_file = working_dir / "cache" / "cloud-shell" / "cloud-shell.log",
    };

    std::vector<std::filesystem::path> inject_paths;
    std::vector<std::string> parse_errors;
    const bool parsed = parseOptions(options, inject_paths, parse_errors);

    cloudshell::log::Init(options.log_file);
    for (const auto& err : parse_errors) {
        cloudshell::log::Error("{}", err);
    }
    if (!parsed) {
        return 1;
    }

    for (const auto& path : inject_paths) {
        if (auto script = readFile(path)) {
            options.extra_scripts.emplace_back(std::move(*script));
        }
        else {
            cloudshell::log::Error("failed to read inject script {}", cloudshell::WideToUtf8(path.wstring()));
            return 1;
        }
    }

    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE))) {
        cloudshell::log::Error("CoInitializeEx failed");
        return 1;
    }

    int exit_code = 1;
    {
        cloudshell::ShellWindow window(std::move(options));
        if (window.Create()) {
            cloudshell::log::Info("started");
            exit_code = window.Run();
            cloudshell::log::Info("exited with {}", exit_code);
        }
    }

    CoUninitialize();
    return exit_code;
}
