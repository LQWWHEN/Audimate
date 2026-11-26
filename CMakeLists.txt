cmake_minimum_required(VERSION 3.20)
project(Audimate LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "Audimate process capture requires Windows")
endif()

add_library(audimate_capture SHARED
    host/capture/capture.cpp
    host/capture/capture.h)
target_compile_features(audimate_capture PRIVATE cxx_std_17)
target_compile_definitions(audimate_capture PRIVATE
    AUDIMATE_CAPTURE_EXPORTS NOMINMAX WIN32_LEAN_AND_MEAN
    _WIN32_WINNT=0x0A00 NTDDI_VERSION=0x0A00000A)
target_include_directories(audimate_capture PUBLIC host/capture)
target_link_libraries(audimate_capture PRIVATE mmdevapi ole32)
set_target_properties(audimate_capture PROPERTIES PREFIX "")
if(MSVC)
    target_compile_options(audimate_capture PRIVATE /W4 /permissive-)
endif()
