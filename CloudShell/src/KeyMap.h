#pragma once

#include <optional>
#include <string>

namespace cloudshell
{

struct KeyInfo
{
    std::string code;
    std::string key;
    std::string shifted_key;
    int location = 0; // KeyboardEvent.location: 0 standard, 1 left, 2 right, 3 numpad
};

// extended comes from lParam bit 24, used to distinguish left/right modifiers and NumpadEnter
std::optional<KeyInfo> LookupKey(unsigned vk, bool extended);

} // namespace cloudshell
