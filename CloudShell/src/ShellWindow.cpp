#include "ShellWindow.h"

#include <algorithm>
#include <cctype>
#include <format>

#include <wrl.h>

#include <WebView2EnvironmentOptions.h>

#include "Log.h"
#include "ResourceIds.h"
#include "Resources.h"

namespace cloudshell
{

using Microsoft::WRL::Callback;
using Microsoft::WRL::ComPtr;

namespace
{

// Non-resizable, no maximize button: keeps physical client rect fixed for capture & coordinate mapping
constexpr DWORD kWindowStyle = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN;

// Prevent occlusion/background throttling and allow cloud stream autoplay without a user gesture
constexpr const wchar_t* kBrowserArguments =
    L"--disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows "
    L"--disable-renderer-backgrounding --disable-background-timer-throttling --autoplay-policy=no-user-gesture-required";

ShellWindow* g_foreground_owner = nullptr;

std::string toLowerAscii(std::string_view src)
{
    std::string out(src);
    std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return out;
}

struct ParsedOrigin
{
    std::string scheme;
    std::string host;
    std::string authority; // host[:port]
};

bool parseOrigin(std::string_view uri, ParsedOrigin& out)
{
    const size_t sep = uri.find("://");
    if (sep == std::string_view::npos) {
        return false;
    }
    out.scheme = toLowerAscii(uri.substr(0, sep));
    std::string_view rest = uri.substr(sep + 3);
    const size_t end = rest.find_first_of("/?#");
    if (end != std::string_view::npos) {
        rest = rest.substr(0, end);
    }
    if (rest.empty()) {
        return false;
    }
    out.authority = toLowerAscii(rest);
    const size_t colon = out.authority.rfind(':');
    out.host = (colon != std::string::npos) ? out.authority.substr(0, colon) : out.authority;
    return !out.host.empty();
}

bool hostMatchesDomain(std::string_view host, std::string_view domain)
{
    if (host == domain) {
        return true;
    }
    return host.size() > domain.size() && host.ends_with(domain) && host[host.size() - domain.size() - 1] == '.';
}

bool isClipboardAllowedOrigin(std::string_view origin_uri, std::string_view initial_url)
{
    ParsedOrigin origin {};
    if (!parseOrigin(origin_uri, origin)) {
        return false;
    }

    if (origin.scheme == "https") {
        if (hostMatchesDomain(origin.host, "manjuu.com") || hostMatchesDomain(origin.host, "163.com")) {
            return true;
        }
    }

    if ((origin.scheme == "http" || origin.scheme == "https") && (origin.host == "127.0.0.1" || origin.host == "localhost")) {
        return true;
    }

    ParsedOrigin initial {};
    if (parseOrigin(initial_url, initial)) {
        if (origin.scheme == initial.scheme && origin.authority == initial.authority) {
            return true;
        }
    }

    return false;
}

} // namespace

ShellWindow::ShellWindow(ShellOptions options)
    : options_(std::move(options))
    , initial_url_utf8_(WideToUtf8(options_.url))
{
}

ShellWindow::~ShellWindow()
{
    if (foreground_hook_) {
        UnhookWinEvent(foreground_hook_);
    }
    g_foreground_owner = nullptr;
}

bool ShellWindow::Create()
{
    WNDCLASSEXW wc { sizeof(WNDCLASSEXW) };
    wc.lpfnWndProc = &ShellWindow::windowProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    wc.hIconSm = LoadIconW(nullptr, IDI_APPLICATION);
    wc.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    wc.lpszClassName = kWindowClassName;
    if (!RegisterClassExW(&wc)) {
        log::Error("RegisterClassExW failed: {}", GetLastError());
        return false;
    }

    hwnd_ = CreateWindowExW(
        WS_EX_APPWINDOW,
        kWindowClassName,
        options_.title.c_str(),
        kWindowStyle,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        options_.width,
        options_.height,
        nullptr,
        nullptr,
        wc.hInstance,
        this);
    if (!hwnd_) {
        log::Error("CreateWindowExW failed: {}", GetLastError());
        return false;
    }

    // Now that hwnd exists on its initial monitor, adjust frame for actual monitor DPI
    applyFixedClientSize(GetDpiForWindow(hwnd_), nullptr);
    input_ = std::make_unique<InputBridge>(hwnd_);

    g_foreground_owner = this;
    foreground_hook_ = SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_FOREGROUND,
        nullptr,
        &ShellWindow::onForegroundChanged,
        0,
        0,
        WINEVENT_OUTOFCONTEXT);
    if (!foreground_hook_) {
        log::Error("SetWinEventHook failed: {}", GetLastError());
    }

    ShowWindow(hwnd_, SW_SHOW);
    UpdateWindow(hwnd_);

    initWebView();
    return true;
}

int ShellWindow::Run()
{
    MSG msg {};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        // Drop queued WM_ACTIVATE posted by background automation; real OS activation is sent synchronously.
        // Passing queued WM_ACTIVATE to DefWindowProc steals focus from WebView2 to the top-level HWND.
        if (msg.message == WM_ACTIVATE) {
            continue;
        }
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return static_cast<int>(msg.wParam);
}

LRESULT CALLBACK ShellWindow::windowProc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam)
{
    if (msg == WM_NCCREATE) {
        auto* self = static_cast<ShellWindow*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        self->hwnd_ = hwnd;
    }

    auto* self = reinterpret_cast<ShellWindow*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (!self) {
        return DefWindowProcW(hwnd, msg, wparam, lparam);
    }
    return self->handleMessage(msg, wparam, lparam);
}

LRESULT ShellWindow::handleMessage(UINT msg, WPARAM wparam, LPARAM lparam)
{
    if (input_) {
        if (auto handled = input_->HandleMessage(msg, wparam, lparam)) {
            return *handled;
        }
    }

    switch (msg) {
    case WM_SETFOCUS:
        if (controller_) {
            controller_->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
            return 0;
        }
        break;

    case WM_DPICHANGED: {
        // Keep physical client width/height unchanged; only adopt suggested origin
        const auto* suggested = reinterpret_cast<const RECT*>(lparam);
        const POINT origin { suggested->left, suggested->top };
        applyFixedClientSize(HIWORD(wparam), &origin);
        return 0;
    }

    case WM_SIZE:
        // Ignore SIZE_MINIMIZED so WebView2 viewport never collapses to 0x0
        if (controller_ && wparam != SIZE_MINIMIZED) {
            RECT bounds {};
            GetClientRect(hwnd_, &bounds);
            controller_->put_Bounds(bounds);
        }
        break;

    case WM_DESTROY:
        if (input_) {
            input_->Detach();
        }
        if (controller_) {
            controller_->Close();
        }
        webview_.Reset();
        controller_.Reset();
        environment_.Reset();
        PostQuitMessage(exit_code_);
        return 0;

    default:
        break;
    }

    return DefWindowProcW(hwnd_, msg, wparam, lparam);
}

void ShellWindow::applyFixedClientSize(UINT dpi, const POINT* origin)
{
    const auto ex_style = static_cast<DWORD>(GetWindowLongPtrW(hwnd_, GWL_EXSTYLE));

    // Per-Monitor v2 DPI aware: client area is physical pixels; only adjust non-client frame for DPI
    RECT rect { 0, 0, options_.width, options_.height };
    if (!AdjustWindowRectExForDpi(&rect, kWindowStyle, FALSE, ex_style, dpi)) {
        log::Error("AdjustWindowRectExForDpi failed: {}", GetLastError());
        return;
    }

    const POINT position = origin ? *origin : POINT {};
    const UINT flags = SWP_NOZORDER | SWP_NOACTIVATE | (origin ? 0 : SWP_NOMOVE);
    SetWindowPos(hwnd_, nullptr, position.x, position.y, rect.right - rect.left, rect.bottom - rect.top, flags);
}

void ShellWindow::initWebView()
{
    // Override inherited WEBVIEW2_* env vars that take precedence over CreateCoreWebView2EnvironmentWithOptions
    std::wstring arguments = kBrowserArguments;
    if (options_.debug_port > 0) {
        arguments += std::format(L" --remote-debugging-port={}", options_.debug_port);
    }
    SetEnvironmentVariableW(L"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", nullptr);
    SetEnvironmentVariableW(L"WEBVIEW2_USER_DATA_FOLDER", options_.user_data_dir.c_str());

    auto environment_options = Microsoft::WRL::Make<CoreWebView2EnvironmentOptions>();
    environment_options->put_AdditionalBrowserArguments(arguments.c_str());

    std::error_code ec;
    std::filesystem::create_directories(options_.user_data_dir, ec);
    log::Info("user data: {}", WideToUtf8(options_.user_data_dir.wstring()));

    const HRESULT hr = CreateCoreWebView2EnvironmentWithOptions(
        nullptr,
        options_.user_data_dir.c_str(),
        environment_options.Get(),
        Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
            [this](HRESULT result, ICoreWebView2Environment* environment) -> HRESULT {
                onEnvironmentCreated(result, environment);
                return S_OK;
            })
            .Get());
    if (FAILED(hr)) {
        MessageBoxW(
            hwnd_,
            L"WebView2 initialization failed. Please install Microsoft Edge WebView2 Runtime.",
            options_.title.c_str(),
            MB_ICONERROR);
        fail("CreateCoreWebView2EnvironmentWithOptions", hr);
    }
}

void ShellWindow::fail(std::string_view what, HRESULT hr)
{
    log::Error("{} failed: 0x{:08x}", what, static_cast<unsigned>(hr));
    exit_code_ = 1;
    DestroyWindow(hwnd_);
}

void ShellWindow::onEnvironmentCreated(HRESULT result, ICoreWebView2Environment* environment)
{
    if (FAILED(result) || !environment) {
        fail("WebView2 environment creation", result);
        return;
    }
    environment_ = environment;

    const HRESULT hr = environment_->CreateCoreWebView2Controller(
        hwnd_,
        Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
            [this](HRESULT controller_result, ICoreWebView2Controller* controller) -> HRESULT {
                onControllerCreated(controller_result, controller);
                return S_OK;
            })
            .Get());
    if (FAILED(hr)) {
        fail("CreateCoreWebView2Controller", hr);
    }
}

void ShellWindow::onControllerCreated(HRESULT result, ICoreWebView2Controller* controller)
{
    if (FAILED(result) || !controller) {
        fail("WebView2 controller creation", result);
        return;
    }

    controller_ = controller;
    if (const HRESULT hr = controller_->get_CoreWebView2(&webview_); FAILED(hr) || !webview_) {
        fail("get_CoreWebView2", hr);
        return;
    }
    webview_->get_BrowserProcessId(&browser_pid_);

    RECT bounds {};
    GetClientRect(hwnd_, &bounds);
    controller_->put_Bounds(bounds);

    applySettings();
    injectScripts();
    subscribeEvents();
    input_->Attach(webview_.Get());
    updateForeground(GetForegroundWindow());

    if (GetFocus() == hwnd_) {
        controller_->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
    }

    if (options_.devtools) {
        webview_->OpenDevToolsWindow();
    }

    if (const HRESULT hr = webview_->Navigate(options_.url.c_str()); FAILED(hr)) {
        fail("Navigate", hr);
        return;
    }
    log::Info("navigate {}", initial_url_utf8_);
}

void ShellWindow::applySettings()
{
    ComPtr<ICoreWebView2Controller3> controller3;
    if (SUCCEEDED(controller_.As(&controller3))) {
        controller3->put_ShouldDetectMonitorScaleChanges(FALSE);
        controller3->put_RasterizationScale(1.0);
    }
    else {
        log::Error("ICoreWebView2Controller3 unavailable, viewport follows system DPI");
    }

    ComPtr<ICoreWebView2Settings> settings;
    if (FAILED(webview_->get_Settings(&settings)) || !settings) {
        log::Error("get_Settings failed");
        return;
    }
    settings->put_AreDefaultContextMenusEnabled(FALSE);
    settings->put_AreDefaultScriptDialogsEnabled(FALSE);
    settings->put_IsZoomControlEnabled(FALSE);
    settings->put_IsStatusBarEnabled(FALSE);

    ComPtr<ICoreWebView2Settings3> settings3;
    if (SUCCEEDED(settings.As(&settings3))) {
        settings3->put_AreBrowserAcceleratorKeysEnabled(FALSE);
    }
    ComPtr<ICoreWebView2Settings5> settings5;
    if (SUCCEEDED(settings.As(&settings5))) {
        settings5->put_IsPinchZoomEnabled(FALSE);
    }
    ComPtr<ICoreWebView2Settings6> settings6;
    if (SUCCEEDED(settings.As(&settings6))) {
        settings6->put_IsSwipeNavigationEnabled(FALSE);
    }
}

void ShellWindow::injectScripts()
{
    std::vector<std::wstring> scripts;
    if (options_.disable_webgl2) {
        scripts.emplace_back(L"window.__azpShellDisableWebGL2 = true;");
    }
    scripts.emplace_back(LoadTextResource(IDR_SCRIPT_CLOUD_POLICY));
    scripts.insert(scripts.end(), options_.extra_scripts.begin(), options_.extra_scripts.end());

    for (const auto& script : scripts) {
        if (script.empty()) {
            log::Error("empty page script, embedded resource missing?");
            continue;
        }
        const HRESULT hr = webview_->AddScriptToExecuteOnDocumentCreated(script.c_str(), nullptr);
        if (FAILED(hr)) {
            log::Error("AddScriptToExecuteOnDocumentCreated failed: 0x{:08x}", static_cast<unsigned>(hr));
        }
    }
}

void ShellWindow::subscribeEvents()
{
    EventRegistrationToken token {};

    // Keep window title fixed to options_.title; sync foreground flag on every navigation
    webview_->add_NavigationCompleted(
        Callback<ICoreWebView2NavigationCompletedEventHandler>([this](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs*) -> HRESULT {
            input_->SyncPageState();
            return S_OK;
        }).Get(),
        &token);

    // Auto-allow clipboard read for official/configured/localhost origins so permission prompts don't occlude capture
    webview_->add_PermissionRequested(
        Callback<ICoreWebView2PermissionRequestedEventHandler>(
            [initial_url = initial_url_utf8_](ICoreWebView2*, ICoreWebView2PermissionRequestedEventArgs* args) -> HRESULT {
                COREWEBVIEW2_PERMISSION_KIND kind = COREWEBVIEW2_PERMISSION_KIND_UNKNOWN_PERMISSION;
                LPWSTR uri = nullptr;
                if (FAILED(args->get_PermissionKind(&kind)) || FAILED(args->get_Uri(&uri)) || !uri) {
                    return S_OK;
                }
                const std::string origin = WideToUtf8(uri);
                CoTaskMemFree(uri);

                if (kind == COREWEBVIEW2_PERMISSION_KIND_CLIPBOARD_READ && isClipboardAllowedOrigin(origin, initial_url)) {
                    args->put_State(COREWEBVIEW2_PERMISSION_STATE_ALLOW);
                    log::Info("clipboard read allowed for {}", origin);
                }
                return S_OK;
            })
            .Get(),
        &token);

    // Block popups/new windows so cloud sessions stay intact
    webview_->add_NewWindowRequested(
        Callback<ICoreWebView2NewWindowRequestedEventHandler>(
            [](ICoreWebView2*, ICoreWebView2NewWindowRequestedEventArgs* args) -> HRESULT {
                args->put_Handled(TRUE);
                LPWSTR uri = nullptr;
                if (SUCCEEDED(args->get_Uri(&uri)) && uri) {
                    log::Warn("new window blocked: {}", WideToUtf8(uri));
                    CoTaskMemFree(uri);
                }
                return S_OK;
            })
            .Get(),
        &token);

    // Prevent modal JS dialogs from deadlocking renderer input
    webview_->add_ScriptDialogOpening(
        Callback<ICoreWebView2ScriptDialogOpeningEventHandler>(
            [](ICoreWebView2*, ICoreWebView2ScriptDialogOpeningEventArgs* args) -> HRESULT {
                COREWEBVIEW2_SCRIPT_DIALOG_KIND kind = COREWEBVIEW2_SCRIPT_DIALOG_KIND_ALERT;
                args->get_Kind(&kind);
                LPWSTR message = nullptr;
                args->get_Message(&message);
                log::Warn("script dialog kind={}: {}", static_cast<int>(kind), message ? WideToUtf8(message) : "");
                CoTaskMemFree(message);
                if (kind == COREWEBVIEW2_SCRIPT_DIALOG_KIND_ALERT || kind == COREWEBVIEW2_SCRIPT_DIALOG_KIND_BEFOREUNLOAD) {
                    args->Accept();
                }
                return S_OK;
            })
            .Get(),
        &token);

    // Recover from renderer crashes/hangs; exit if browser process terminates
    webview_->add_ProcessFailed(
        Callback<ICoreWebView2ProcessFailedEventHandler>(
            [this](ICoreWebView2* sender, ICoreWebView2ProcessFailedEventArgs* args) -> HRESULT {
                COREWEBVIEW2_PROCESS_FAILED_KIND kind = COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED;
                args->get_ProcessFailedKind(&kind);
                switch (kind) {
                case COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED:
                    fail("WebView2 browser process", E_FAIL);
                    break;
                case COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED:
                case COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_UNRESPONSIVE:
                    log::Error("render process failed (kind={}), reloading", static_cast<int>(kind));
                    sender->Reload();
                    break;
                default:
                    log::Warn("WebView2 process failed (kind={})", static_cast<int>(kind));
                    break;
                }
                return S_OK;
            })
            .Get(),
        &token);
}

void CALLBACK ShellWindow::onForegroundChanged(HWINEVENTHOOK, DWORD, HWND hwnd, LONG, LONG, DWORD, DWORD)
{
    if (g_foreground_owner) {
        g_foreground_owner->updateForeground(hwnd);
    }
}

void ShellWindow::updateForeground(HWND foreground)
{
    DWORD pid = 0;
    if (foreground) {
        GetWindowThreadProcessId(foreground, &pid);
    }
    // Pointer-lock bubbles belong to WebView2's browser process; treat both as foreground
    input_->SetForeground(pid != 0 && (pid == GetCurrentProcessId() || pid == browser_pid_));
}

} // namespace cloudshell
