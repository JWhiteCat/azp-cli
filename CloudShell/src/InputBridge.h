#pragma once

#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <string_view>

#include <windows.h>
#include <wrl/client.h>

#include <WebView2.h>

namespace cloudshell
{

// Translates Win32 mouse/keyboard PostMessage events on the top-level window into CDP Input commands
class InputBridge
{
public:
    explicit InputBridge(HWND hwnd);

    void Attach(ICoreWebView2* webview);
    void Detach();

    std::optional<LRESULT> HandleMessage(UINT msg, WPARAM wparam, LPARAM lparam);

    void SetForeground(bool foreground);
    void SyncPageState();

private:
    struct Command
    {
        std::string method;
        std::string params;
        bool move = false;
        std::optional<POINT> relative {};
    };

    void onMouseButton(int button, bool down, LPARAM lparam);
    void onMouseMove(LPARAM lparam);
    void onRelativeMove(LPARAM lparam);
    void onMouseWheel(bool horizontal, WPARAM wparam, LPARAM lparam);
    void onKey(bool down, WPARAM wparam, LPARAM lparam);
    void onChar(WPARAM wparam, LPARAM lparam);

    void dispatchMouse(std::string_view type, std::string_view button, int click_count, int delta_x = 0, int delta_y = 0);

    // CDP commands run serially so Runtime.evaluate(__azpShellExpect) completes before Input.dispatchMouseEvent
    void enqueue(std::string method, std::string params);
    void evaluate(std::string_view expression);
    void pump();
    void onWatchdog();

private:
    HWND hwnd_ = nullptr;
    Microsoft::WRL::ComPtr<ICoreWebView2> webview_;

    std::deque<Command> queue_;
    bool busy_ = false;
    // Incremented on watchdog timeout to ignore stale completion callbacks
    uint64_t generation_ = 0;

    // Track pressed buttons, pointer coords, and modifiers internally rather than trusting message wParam
    int buttons_ = 0;
    POINT pointer_ {};
    int modifiers_ = 0;
    wchar_t pending_high_surrogate_ = 0;
    bool foreground_ = true;
};

} // namespace cloudshell
