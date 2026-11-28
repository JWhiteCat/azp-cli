cmake_minimum_required(VERSION 3.24)

project(azp-cloud-shell LANGUAGES CXX RC)

if(NOT WIN32)
    message(FATAL_ERROR "CloudShell only supports Windows")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
# Static CRT: no VC++ Redistributable required on target machines
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

include(cmake/WebView2Sdk.cmake)

set(CLOUD_SHELL_SCRIPTS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/scripts")
configure_file(src/resources.rc.in "${CMAKE_CURRENT_BINARY_DIR}/resources.rc" @ONLY)

add_executable(cloud-shell WIN32
    src/main.cpp
    src/ShellWindow.cpp
    src/InputBridge.cpp
    src/KeyMap.cpp
    src/Log.cpp
    src/Resources.cpp
    "${CMAKE_CURRENT_BINARY_DIR}/resources.rc"
)

set_target_properties(cloud-shell PROPERTIES OUTPUT_NAME azp-cloud)
target_include_directories(cloud-shell PRIVATE src)
target_link_libraries(cloud-shell PRIVATE WebView2::LoaderStatic)
target_compile_definitions(cloud-shell PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_options(cloud-shell PRIVATE /utf-8 /W4 /WX /permissive-)

# RCDATA dependencies are not tracked automatically by rc.exe
set_property(SOURCE "${CMAKE_CURRENT_BINARY_DIR}/resources.rc" APPEND PROPERTY OBJECT_DEPENDS
    "${CLOUD_SHELL_SCRIPTS_DIR}/cloud_policy.js")

install(TARGETS cloud-shell RUNTIME DESTINATION .)
