#include "InputBridge.h"

#include <cstdlib>
#include <format>

#include <windowsx.h>
#include <wrl.h>

#include "KeyMap.h"
#include "Log.h"
#include "Resources.h"

namespace cloudshell
{

namespace
{

constexpr int kModifierAlt = 1;
constexpr int kModifierCtrl = 2;
constexpr int kModifierMeta = 4;
constexpr int kModifierShift = 8;

constexpr int kButtonLeft = 1;
constexpr int kButtonRight = 2;
constexpr int kButtonMiddle = 4;
constexpr int kButtonBack = 8;
constexpr int kButtonForward = 16;

constexpr UINT_PTR kWatchdogTimer = 1;
// Drop hung CDP calls after 3s so subsequent automation input is not blocked permanently
constexpr UINT kCommandTimeoutMs = 3000;
// Clamp tail-merged relative camera deltas to stay inside cloud streaming linear range
constexpr LONG kMaxMergedRelativeDelta = 128;

std::string jsonEscape(std::string_view src)
{
    std::string out;
    out.reserve(src.size() + 2);
    for (const char ch : src) {
        if (ch == '"' || ch == '\\') {
            out += '\\';
            out += ch;
        }
        else if (static_cast<unsigned char>(ch) < 0x20) {
            out += std::format("\\u{:04x}", static_cast<unsigned char>(ch));
        }
        else {
            out += ch;
        }
    }
    return out;
}

const char* buttonName(int button)
{
    switch (button) {
    case kButtonLeft:
        return "left";
    case kButtonRight:
        return "right";
    case kButtonMiddle:
        return "middle";
    case kButtonBack:
        return "back";
    case kButtonForward:
        return "forward";
    default:
        return "none";
    }
}

unsigned genericVirtualKey(unsigned vk)
{
    switch (vk) {
    case VK_LSHIFT:
    case VK_RSHIFT:
        return VK_SHIFT;
    case VK_LCONTROL:
    case VK_RCONTROL:
        return VK_CONTROL;
    case VK_LMENU:
    case VK_RMENU:
        return VK_MENU;
    default:
        return vk;
    }
}

int modifierBit(unsigned vk)
{
    switch (genericVirtualKey(vk)) {
    case VK_SHIFT:
        return kModifierShift;
    case VK_CONTROL:
        return kModifierCtrl;
    case VK_MENU:
        return kModifierAlt;
    case VK_LWIN:
    case VK_RWIN:
        return kModifierMeta;
    default:
        return 0;
    }
}

} // namespace

InputBridge::InputBridge(HWND hwnd)
    : hwnd_(hwnd)
{
}

void InputBridge::Attach(ICoreWebView2* webview)
{
    webview_ = webview;
}

void InputBridge::Detach()
{
    KillTimer(hwnd_, kWatchdogTimer);
    webview_.Reset();
    queue_.clear();
}

std::optional<LRESULT> InputBridge::HandleMessage(UINT msg, WPARAM wparam, LPARAM lparam)
{
    switch (msg) {
    case WM_MOUSEMOVE:
        // MK_XBUTTON2 set while XBUTTON2 is not held encodes relative camera delta from client center
        if ((wparam & MK_XBUTTON2) && !(buttons_ & kButtonForward)) {
            onRelativeMove(lparam);
            return 0;
        }
        // Automation moves include a button flag; bare wparam==0 moves are stray physical cursor hits
        if (wparam & (MK_LBUTTON | MK_RBUTTON | MK_MBUTTON | MK_XBUTTON1 | MK_XBUTTON2)) {
            onMouseMove(lparam);
        }
        return 0;

    case WM_TIMER:
        if (wparam != kWatchdogTimer) {
            return std::nullopt;
        }
        onWatchdog();
        return 0;

    case WM_LBUTTONDOWN:
    case WM_LBUTTONUP:
        onMouseButton(kButtonLeft, msg == WM_LBUTTONDOWN, lparam);
        return 0;
    case WM_RBUTTONDOWN:
    case WM_RBUTTONUP:
        onMouseButton(kButtonRight, msg == WM_RBUTTONDOWN, lparam);
        return 0;
    case WM_MBUTTONDOWN:
    case WM_MBUTTONUP:
        onMouseButton(kButtonMiddle, msg == WM_MBUTTONDOWN, lparam);
        return 0;
    case WM_XBUTTONDOWN:
    case WM_XBUTTONUP:
        onMouseButton(GET_XBUTTON_WPARAM(wparam) == XBUTTON1 ? kButtonBack : kButtonForward, msg == WM_XBUTTONDOWN, lparam);
        return TRUE;

    case WM_MOUSEWHEEL:
        onMouseWheel(false, wparam, lparam);
        return 0;
    case WM_MOUSEHWHEEL:
        onMouseWheel(true, wparam, lparam);
        return 0;

    case WM_KEYDOWN:
    case WM_KEYUP:
        onKey(msg == WM_KEYDOWN, wparam, lparam);
        return 0;
    case WM_CHAR:
        onChar(wparam, lparam);
        return 0;

    default:
        return std::nullopt;
    }
}

void InputBridge::SetForeground(bool foreground)
{
    if (foreground_ == foreground) {
        return;
    }
    foreground_ = foreground;
    log::Info("foreground={}", foreground_);
    SyncPageState();
}

void InputBridge::SyncPageState()
{
    evaluate(std::format("window.__azpShellSetForeground&&window.__azpShellSetForeground({})", foreground_));
}

void InputBridge::onMouseButton(int button, bool down, LPARAM lparam)
{
    if (down) {
        pointer_ = { GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam) };
        buttons_ |= button;
    }
    else {
        buttons_ &= ~button;
    }

    log::Info("mouse {} {} ({}, {})", buttonName(button), down ? "down" : "up", pointer_.x, pointer_.y);
    dispatchMouse(down ? "mousePressed" : "mouseReleased", buttonName(button), 1);
}

void InputBridge::onMouseMove(LPARAM lparam)
{
    pointer_ = { GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam) };
    // Report the lowest held button bit, or "none" if no button is pressed
    dispatchMouse("mouseMoved", buttonName(buttons_ & -buttons_), 0);
}

void InputBridge::onRelativeMove(LPARAM lparam)
{
    RECT client {};
    GetClientRect(hwnd_, &client);
    pointer_ = { client.right / 2, client.bottom / 2 };
    POINT delta { GET_X_LPARAM(lparam) - pointer_.x, GET_Y_LPARAM(lparam) - pointer_.y };

    // Coalesce queued tail relative moves up to the safe single-step threshold
    if (queue_.size() >= 2 && queue_.back().relative && queue_[queue_.size() - 2].relative) {
        const POINT merged { delta.x + queue_.back().relative->x, delta.y + queue_.back().relative->y };
        if (std::abs(merged.x) <= kMaxMergedRelativeDelta && std::abs(merged.y) <= kMaxMergedRelativeDelta) {
            delta = merged;
            queue_.pop_back();
            queue_.pop_back();
        }
    }

    const std::string expect =
        std::format("window.__azpShellExpect&&window.__azpShellExpect({},{},{},{})", pointer_.x, pointer_.y, delta.x, delta.y);
    std::string params = std::format(
        R"({{"type":"mouseMoved","x":{},"y":{},"button":"{}","buttons":{},"modifiers":{}}})",
        pointer_.x,
        pointer_.y,
        buttonName(buttons_ & -buttons_),
        buttons_,
        modifiers_);
    queue_.push_back(
        Command { .method = "Runtime.evaluate", .params = std::format(R"({{"expression":"{}"}})", jsonEscape(expect)), .relative = delta });
    queue_.push_back(Command { .method = "Input.dispatchMouseEvent", .params = std::move(params), .relative = delta });
    pump();
}

void InputBridge::onMouseWheel(bool horizontal, WPARAM wparam, LPARAM lparam)
{
    // WM_MOUSEWHEEL / WM_MOUSEHWHEEL carry screen coordinates in lParam
    POINT point { GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam) };
    if (ScreenToClient(hwnd_, &point)) {
        pointer_ = point;
    }

    // Win32 wheel delta > 0 scrolls up; CDP deltaY > 0 scrolls down
    const int delta = GET_WHEEL_DELTA_WPARAM(wparam);
    log::Info("wheel horizontal={} delta={} ({}, {})", horizontal, delta, pointer_.x, pointer_.y);
    dispatchMouse("mouseWheel", "none", 0, horizontal ? delta : 0, horizontal ? 0 : -delta);
}

void InputBridge::onKey(bool down, WPARAM wparam, LPARAM lparam)
{
    const auto vk = static_cast<unsigned>(wparam);
    const bool extended = ((lparam >> 24) & 1) != 0;
    const auto info = LookupKey(vk, extended);
    if (!info) {
        log::Warn("unsupported key vk={}", vk);
        return;
    }

    if (const int bit = modifierBit(vk); bit != 0) {
        modifiers_ = down ? (modifiers_ | bit) : (modifiers_ & ~bit);
    }

    const std::string& key = (modifiers_ & kModifierShift) ? info->shifted_key : info->key;

    // Text goes through WM_CHAR -> Input.insertText; keys use rawKeyDown except Enter (forms expect keypress text)
    std::string type = down ? "rawKeyDown" : "keyUp";
    std::string text_fields;
    if (down && vk == VK_RETURN) {
        type = "keyDown";
        text_fields = R"(,"text":"\r","unmodifiedText":"\r")";
    }

    const unsigned js_vk = genericVirtualKey(vk);
    std::string params = std::format(
        R"({{"type":"{}","modifiers":{},"windowsVirtualKeyCode":{},"nativeVirtualKeyCode":{},"code":"{}","key":"{}","location":{}{}{}}})",
        type,
        modifiers_,
        js_vk,
        js_vk,
        jsonEscape(info->code),
        jsonEscape(key),
        info->location,
        info->location == 3 ? R"(,"isKeypad":true)" : "",
        text_fields);

    log::Info("key vk={} {} {}", vk, down ? "down" : "up", info->code);
    if (down) {
        evaluate("window.__azpShellExpect&&window.__azpShellExpect()");
    }
    enqueue("Input.dispatchKeyEvent", std::move(params));
}

void InputBridge::onChar(WPARAM wparam, LPARAM lparam)
{
    // Ignore TranslateMessage-generated WM_CHAR (lParam != 0); automation posts lParam == 0
    if (lparam != 0) {
        return;
    }

    const auto ch = static_cast<wchar_t>(wparam);
    std::wstring text;
    if (IS_HIGH_SURROGATE(ch)) {
        pending_high_surrogate_ = ch;
        return;
    }
    if (IS_LOW_SURROGATE(ch)) {
        if (pending_high_surrogate_ == 0) {
            return;
        }
        text = { pending_high_surrogate_, ch };
    }
    else if (ch >= 0x20 && ch != 0x7f) {
        text = ch;
    }
    pending_high_surrogate_ = 0;

    if (text.empty()) {
        return;
    }

    enqueue("Input.insertText", std::format(R"({{"text":"{}"}})", jsonEscape(WideToUtf8(text))));
}

void InputBridge::dispatchMouse(std::string_view type, std::string_view button, int click_count, int delta_x, int delta_y)
{
    std::string params = std::format(
        R"({{"type":"{}","x":{},"y":{},"button":"{}","buttons":{},"clickCount":{},"modifiers":{})",
        type,
        pointer_.x,
        pointer_.y,
        button,
        buttons_,
        click_count,
        modifiers_);
    if (type == "mouseWheel") {
        params += std::format(R"(,"deltaX":{},"deltaY":{})", delta_x, delta_y);
    }
    params += '}';

    // Coalesce queued mouseMoved pairs so high-rate drag updates don't back up behind renderer frames
    const bool move = type == "mouseMoved";
    if (move && queue_.size() >= 2 && queue_.back().move && queue_[queue_.size() - 2].move) {
        queue_.pop_back();
        queue_.pop_back();
    }

    const std::string expect = std::format("window.__azpShellExpect&&window.__azpShellExpect({},{})", pointer_.x, pointer_.y);
    queue_.push_back(Command { .method = "Runtime.evaluate", .params = std::format(R"({{"expression":"{}"}})", jsonEscape(expect)), .move = move });
    queue_.push_back(Command { .method = "Input.dispatchMouseEvent", .params = std::move(params), .move = move });
    pump();
}

void InputBridge::evaluate(std::string_view expression)
{
    enqueue("Runtime.evaluate", std::format(R"({{"expression":"{}"}})", jsonEscape(expression)));
}

void InputBridge::enqueue(std::string method, std::string params)
{
    queue_.push_back(Command { .method = std::move(method), .params = std::move(params) });
    pump();
}

void InputBridge::pump()
{
    using Microsoft::WRL::Callback;
    using Handler = ICoreWebView2CallDevToolsProtocolMethodCompletedHandler;

    while (!busy_ && !queue_.empty()) {
        if (!webview_) {
            queue_.clear();
            return;
        }

        Command command = std::move(queue_.front());
        queue_.pop_front();

        const std::wstring method = Utf8ToWide(command.method);
        const std::wstring params = Utf8ToWide(command.params);
        const uint64_t generation = ++generation_;
        busy_ = true;
        SetTimer(hwnd_, kWatchdogTimer, kCommandTimeoutMs, nullptr);
        const HRESULT hr = webview_->CallDevToolsProtocolMethod(
            method.c_str(),
            params.c_str(),
            Callback<Handler>([this, generation, name = command.method](HRESULT error, LPCWSTR) -> HRESULT {
                if (generation != generation_) {
                    return S_OK;
                }
                if (FAILED(error)) {
                    log::Warn("CDP {} failed: 0x{:08x}", name, static_cast<unsigned>(error));
                }
                busy_ = false;
                pump();
                if (!busy_) {
                    KillTimer(hwnd_, kWatchdogTimer);
                }
                return S_OK;
            }).Get());

        if (FAILED(hr)) {
            log::Error("CDP {} dispatch failed: 0x{:08x}", command.method, static_cast<unsigned>(hr));
            busy_ = false;
        }
    }
}

void InputBridge::onWatchdog()
{
    KillTimer(hwnd_, kWatchdogTimer);
    if (!busy_) {
        return;
    }
    log::Warn("CDP command timed out after {}ms, skipped", kCommandTimeoutMs);
    ++generation_;
    busy_ = false;
    pump();
}

} // namespace cloudshell
