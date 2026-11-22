cmake_minimum_required(VERSION 3.22.1)
project(probe LANGUAGES CXX)

add_library(probe SHARED native_probe.cpp)
target_link_libraries(probe PRIVATE log)
