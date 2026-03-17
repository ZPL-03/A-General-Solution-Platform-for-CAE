# ------------------------------------------------------------
# CAE_FEM_Software 根级 CMake 配置
# 作用：
# 1. 配置 C++17 编译环境
# 2. 编译 pybind11 扩展模块 fem_core
# 3. 将编译后的 Python 扩展直接输出到 python 目录
# ------------------------------------------------------------

cmake_minimum_required(VERSION 3.15)

# 项目名称与版本号。
# 这里的项目本体是“有限元求解核心”，前端界面由 Python 单独运行。
project(CAE_FEMCore VERSION 0.2 LANGUAGES CXX)

# 指定统一的 C++ 标准，避免不同编译器默认标准不一致导致语法行为变化。
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# 为单配置生成器（如 Ninja）提供默认构建类型。
# Visual Studio 属于多配置生成器，这个设置不会影响 Release/Debug 下拉选项。
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# 输出基础配置信息，方便初学者确认当前到底用了什么编译器和构建模式。
message(STATUS "C++ Compiler: ${CMAKE_CXX_COMPILER}")
message(STATUS "Build Type: ${CMAKE_BUILD_TYPE}")

# 第三方依赖：
# Python 解释器/开发头文件用于生成正确版本的扩展模块。
# pybind11 负责把 C++ 类导出给 Python。
# Eigen3 负责矩阵、向量与稀疏线性代数。
# OpenMP 可选，用于后续并行装配和并行后处理扩展。
set(PYBIND11_FINDPYTHON ON)
find_package(Python REQUIRED COMPONENTS Interpreter Development)
find_package(pybind11 REQUIRED)
find_package(Eigen3 3.3 REQUIRED NO_MODULE)
find_package(OpenMP)

# 项目内路径统一集中定义，避免后面反复拼接硬编码路径。
set(CAE_CPP_INCLUDE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/cpp/include)
set(CAE_CPP_SRC_DIR ${CMAKE_CURRENT_SOURCE_DIR}/cpp/src)
set(CAE_PYTHON_DIR ${CMAKE_CURRENT_SOURCE_DIR}/python)

# 有限元核心源码列表。
set(CAE_CORE_SOURCES
    ${CAE_CPP_SRC_DIR}/fem_solver.cpp
    ${CAE_CPP_SRC_DIR}/python_bindings.cpp
)

# 生成 Python 扩展模块 fem_core。
# 最终产物在 Windows 下通常是 .pyd，在 Linux/macOS 下通常是 .so。
pybind11_add_module(fem_core ${CAE_CORE_SOURCES})

# 告诉编译器到哪里查找项目头文件。
target_include_directories(fem_core PRIVATE ${CAE_CPP_INCLUDE_DIR})

# 链接 Eigen。
target_link_libraries(fem_core PRIVATE Eigen3::Eigen)

# 如果找到了 OpenMP，就把并行运行时也链接进去。
if(OpenMP_CXX_FOUND)
    target_link_libraries(fem_core PRIVATE OpenMP::OpenMP_CXX)
endif()

# 为不同编译器设置合适的警告级别和优化参数。
if(MSVC)
    target_compile_options(fem_core PRIVATE /W4 /O2 /permissive- /utf-8)
else()
    target_compile_options(fem_core PRIVATE -Wall -Wextra -Wpedantic -O3)
endif()

# 让编译结果直接进入 python 目录，这样 Python 测试和界面可以直接 import fem_core。
set_target_properties(fem_core PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CAE_PYTHON_DIR}
    RUNTIME_OUTPUT_DIRECTORY ${CAE_PYTHON_DIR}
)

# Visual Studio 是多配置生成器，需要分别指定 Release/Debug 等目录。
foreach(cfg RELEASE DEBUG RELWITHDEBINFO MINSIZEREL)
    set_target_properties(fem_core PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY_${cfg} ${CAE_PYTHON_DIR}
        RUNTIME_OUTPUT_DIRECTORY_${cfg} ${CAE_PYTHON_DIR}
    )
endforeach()

# 安装规则。
# 当前阶段主要用于本地开发，保留最简单的模块安装方式即可。
install(TARGETS fem_core
    LIBRARY DESTINATION .
    RUNTIME DESTINATION .
)

# 打开 CTest，便于后续接入自动化测试。
enable_testing()

# 最终摘要输出。
message(STATUS "========================================")
message(STATUS "  CAE FEM Core Configuration Summary")
message(STATUS "========================================")
message(STATUS "  Project: ${PROJECT_NAME}")
message(STATUS "  Version: ${PROJECT_VERSION}")
message(STATUS "  C++ Standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "  Build Type: ${CMAKE_BUILD_TYPE}")
message(STATUS "  Compiler: ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
message(STATUS "  pybind11: ${pybind11_VERSION}")
message(STATUS "  Python: ${Python_EXECUTABLE}")
message(STATUS "  Eigen3: ${EIGEN3_VERSION}")
if(OpenMP_CXX_FOUND)
    message(STATUS "  OpenMP: Enabled")
else()
    message(STATUS "  OpenMP: Disabled")
endif()
message(STATUS "  Python Module Output: ${CAE_PYTHON_DIR}")
message(STATUS "========================================")
