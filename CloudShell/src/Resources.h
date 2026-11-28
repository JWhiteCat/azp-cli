#pragma once

#include <string>
#include <string_view>

namespace cloudshell
{

// Returns empty string if resource does not exist
std::wstring LoadTextResource(int id);

std::string WideToUtf8(std::wstring_view src);
std::wstring Utf8ToWide(std::string_view src);

} // namespace cloudshell
