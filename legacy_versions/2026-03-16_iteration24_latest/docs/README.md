# CAE有限元分析软件

一个基于PyQt6界面和C++/Fortran计算核心的开源CAE有限元分析软件。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)
![C++](https://img.shields.io/badge/C++-17-orange.svg)

---

## 📋 项目简介

本项目旨在开发一套功能完整的CAE有限元分析软件,支持:

- ✅ **前处理**: 几何建模、网格划分(Gmsh集成)、边界条件设置
- ✅ **求解器**: 线性/非线性静力学、模态分析、瞬态动力学
- ✅ **后处理**: 结果可视化(VTK)、应力云图、动画演示

### 技术架构

```
┌─────────────────────────────────┐
│     PyQt6 用户界面层             │
│   (前处理 + 后处理 + 可视化)      │
└──────────────┬──────────────────┘
               │ pybind11
┌──────────────┴──────────────────┐
│   C++/Fortran 计算核心           │
│   (高性能有限元求解器)            │
└──────────────┬──────────────────┘
               │
┌──────────────┴──────────────────┐
│        第三方库                  │
│  Gmsh | Eigen | VTK | MKL       │
└─────────────────────────────────┘
```

---

## 🚀 快速开始

### 环境要求

- **操作系统**: Windows 11 / Windows 10
- **Python**: 3.9+ (推荐使用Anaconda)
- **编译器**: Visual Studio 2022 或 VSCode + MSVC
- **CMake**: 3.15+
- **内存**: 8GB+ (推荐16GB)

### 快速安装

**1. 克隆或下载项目**

```bash
git clone https://github.com/ZPL-03/CAE_FEM_Software.git
cd CAE_FEM_Software
```

**2. 安装Python依赖**

```bash
pip install -r docs/requirements.txt
```

**3. 编译C++求解器**

```bash
# 配置CMake
cmake -S . -B build -G "Visual Studio 17 2022" -A x64

# 编译Release版本
cmake --build build --config Release

# 编译后的fem_core.pyd会自动复制到python/目录
```

**4. 运行测试**

```bash
cd python/tests
python test_fem_solver.py
```

**5. 启动软件**

```bash
cd python/app
python main_window.py
```

详细安装指南请参考 **[docs/快速启动指南.md](docs/快速启动指南.md)**

---

## 📁 项目结构

```
CAE_FEM_SOFTWARE/
├── .vscode/                     # VSCode配置
│   └── settings.json
│
├── build/                       # CMake构建输出目录(git忽略)
│
├── cpp/                         # C++求解器核心代码
│   ├── include/                 # 头文件
│   │   └── fem_solver.h         # 求解器主头文件
│   └── src/                     # 源文件
│       ├── fem_solver.cpp       # 求解器实现
│       └── python_bindings.cpp  # pybind11 Python绑定
│
├── data/                        # 数据文件目录
│   ├── models/                  # 几何模型/网格文件
│   │   └── .gitkeep
│   └── results/                 # 分析结果输出
│       └── .gitkeep
│
├── docs/                        # 项目文档
│   ├── README.md                # 本文件
│   ├── 快速启动指南.md           # 详细安装配置指南
│   ├── CAE软件开发规划.md        # 完整开发规划
│   └── requirements.txt         # Python依赖列表
│
├── python/                      # Python代码
│   ├── app/                     # PyQt6应用程序
│   │   └── main_window.py       # 主窗口
│   ├── tests/                   # 测试代码
│   │   ├── test_fem_solver.py           # 求解器单元测试
│   │   └── test_gmsh_integration.py     # Gmsh集成测试
│   └── fem_core.cp39-win_amd64.pyd      # 编译后的C++模块
│
├── .gitignore                   # Git忽略文件配置
└── CMakeLists.txt               # CMake项目配置文件
```

### 目录说明

| 目录/文件 | 说明 |
|----------|------|
| `cpp/` | C++求解器核心代码,包含有限元计算引擎 |
| `cpp/include/` | C++头文件,定义求解器接口 |
| `cpp/src/` | C++实现文件和Python绑定代码 |
| `python/app/` | PyQt6图形界面应用程序 |
| `python/tests/` | Python单元测试和集成测试 |
| `python/fem_core.*.pyd` | 编译后的C++模块(Windows) |
| `data/models/` | 存放几何模型和网格文件 |
| `data/results/` | 存放分析结果(CSV、VTK等) |
| `docs/` | 所有项目文档和依赖配置 |
| `build/` | CMake编译中间文件(不提交到git) |
| `CMakeLists.txt` | CMake顶层配置文件 |

---

## 🎯 功能特性

### 已实现功能 ✅

- [x] **C++求解器核心**
  - [x] 四面体单元(Tet4)实现
  - [x] 线性静力学分析
  - [x] 全局刚度矩阵组装
  - [x] 稀疏矩阵求解器(Eigen SparseLU)
  - [x] Von Mises应力计算

- [x] **Python接口**
  - [x] pybind11 C++绑定
  - [x] 节点、单元、材料类接口
  - [x] 边界条件和载荷施加
  - [x] 结果导出(CSV格式)

- [x] **测试框架**
  - [x] 单单元测试
  - [x] Gmsh网格集成测试
  - [x] 悬臂梁验证算例

- [x] **PyQt6界面框架**
  - [x] 主窗口基础框架
  - [x] 菜单和工具栏结构

### 开发中功能 🚧

- [ ] **前处理界面**
  - [ ] 交互式几何建模
  - [ ] Gmsh网格可视化
  - [ ] 材料属性编辑器
  - [ ] 边界条件设置面板

- [ ] **后处理可视化**
  - [ ] VTK 3D渲染集成
  - [ ] 位移云图显示
  - [ ] 应力/应变云图
  - [ ] 动画播放功能

- [ ] **更多单元类型**
  - [ ] 六面体单元(Hex8)
  - [ ] 梁单元(Beam2D/3D)
  - [ ] 壳单元(Shell)
  - [ ] 平面单元(Quad4, Tri3)

### 计划功能 📅

- [ ] **高级求解**
  - [ ] 非线性静力学(Newton-Raphson)
  - [ ] 模态分析(特征值求解)
  - [ ] 瞬态动力学(Newmark-β)
  - [ ] 热传导分析
  - [ ] 流固耦合

- [ ] **性能优化**
  - [ ] 多线程并行(OpenMP)
  - [ ] GPU加速(CUDA)
  - [ ] 分布式计算(MPI)
  - [ ] Intel MKL求解器集成

- [ ] **用户体验**
  - [ ] 项目文件管理
  - [ ] 撤销/重做功能
  - [ ] 快捷键系统
  - [ ] 进度显示和日志

---

## 📖 文档索引

| 文档 | 说明 |
|------|------|
| [README.md](docs/README.md) | 项目总览(本文件) |
| [快速启动指南.md](docs/快速启动指南.md) | 环境配置、编译、测试详细步骤 |
| [CAE软件开发规划.md](docs/CAE软件开发规划.md) | 完整开发计划、技术路线、学习资源 |
| [requirements.txt](docs/requirements.txt) | Python依赖包列表 |

---

## 🔧 开发指南

### 开发工作流

#### 1. 修改C++代码

编辑 `cpp/src/fem_solver.cpp` 或 `cpp/include/fem_solver.h`

#### 2. 重新编译

```bash
# 在项目根目录
cmake --build build --config Release
```

编译成功后,`fem_core.pyd` 会自动复制到 `python/` 目录

#### 3. 测试修改

```bash
cd python/tests
python test_fem_solver.py
```

#### 4. 集成到界面

编辑 `python/app/main_window.py`,调用更新后的fem_core模块

### 代码规范

- **Python**: 遵循 PEP 8
- **C++**: 遵循 Google C++ Style Guide
- **提交信息**: 使用清晰的commit message格式

### CMake配置说明

**关键配置项 (CMakeLists.txt)**:

```cmake
# 指定C++标准
set(CMAKE_CXX_STANDARD 17)

# 查找依赖
find_package(pybind11 REQUIRED)
find_package(Eigen3 REQUIRED)

# 编译Python模块
pybind11_add_module(fem_core 
    cpp/src/fem_solver.cpp
    cpp/src/python_bindings.cpp
)

# 自动复制编译结果
add_custom_command(TARGET fem_core POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:fem_core> 
            ${CMAKE_SOURCE_DIR}/python/
)
```

### 调试技巧

**Python调试:**

```python
# 在main_window.py中设置断点
import pdb; pdb.set_trace()

# 或使用VSCode/PyCharm的图形化调试器
```

**C++调试:**

```bash
# 编译Debug版本
cmake --build build --config Debug

# 使用VS2022调试器
# 1. 打开build/CAE_FEM_SOFTWARE.sln
# 2. 设置断点
# 3. 附加到python.exe进程
```

**CMake清理:**

```bash
# 完全清理build目录
rm -rf build

# 重新配置
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
```

---

## 🧪 测试说明

### 运行所有测试

```bash
cd python/tests
python test_fem_solver.py
python test_gmsh_integration.py
```

### 测试内容

**test_fem_solver.py**:
- 材料弹性矩阵计算验证
- 单四面体单元静力学分析
- 边界条件和载荷施加测试
- 位移和应力结果验证

**test_gmsh_integration.py**:
- Gmsh自动建模和网格划分
- 网格数据导入fem_core
- 3D悬臂梁完整分析流程
- 结果导出和后处理

### 性能基准测试

```bash
# 待实现
cd python/tests
python benchmark_solver.py
```

---

## 🎓 技术栈详解

### C++核心库

| 库 | 版本 | 用途 |
|----|------|------|
| **Eigen3** | 3.4+ | 线性代数、矩阵运算、稀疏求解器 |
| **pybind11** | 2.10+ | C++到Python的无缝绑定 |

### Python框架

| 库 | 版本 | 用途 |
|----|------|------|
| **PyQt6** | 6.4+ | 图形界面框架 |
| **gmsh** | 4.11+ | 网格生成和几何建模 |
| **VTK** | 9.2+ | 科学可视化 |
| **NumPy** | 1.23+ | 数值计算 |
| **SciPy** | 1.9+ | 科学计算库 |

### 开发工具

- **CMake**: 跨平台构建系统
- **VS2022**: C++编译和调试
- **VSCode**: 轻量级代码编辑器
- **Git**: 版本控制

---

## 💡 使用示例

### 示例1: 简单立方体受压分析

```python
import sys
sys.path.append('../python')
import fem_core
import gmsh

# 1. Gmsh建模
gmsh.initialize()
gmsh.model.add("cube")
gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
gmsh.model.occ.synchronize()
gmsh.model.mesh.generate(3)

# 2. 创建求解器
solver = fem_core.FEMSolver()
solver.addMaterial(fem_core.Material(1, 200e9, 0.3, 7850))

# 3. 导入网格
node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
for i, tag in enumerate(node_tags):
    solver.addNode(i, 
                   node_coords[i*3], 
                   node_coords[i*3+1], 
                   node_coords[i*3+2])

# 4. 施加边界条件(固定底面)
for i, tag in enumerate(node_tags):
    z = node_coords[i*3 + 2]
    if abs(z) < 1e-6:
        solver.addBoundaryCondition(i, 0, 0.0)  # Ux=0
        solver.addBoundaryCondition(i, 1, 0.0)  # Uy=0
        solver.addBoundaryCondition(i, 2, 0.0)  # Uz=0

# 5. 施加载荷(顶面压力)
for i, tag in enumerate(node_tags):
    z = node_coords[i*3 + 2]
    if abs(z - 1.0) < 1e-6:
        solver.addLoad(i, 0, 0, -1000.0)

# 6. 求解
solver.initialize()
solver.assembleGlobalStiffnessMatrix()
solver.assembleGlobalForceVector()
solver.applyBoundaryConditions()
solver.solve()
solver.updateNodalDisplacements()

# 7. 输出结果
solver.exportResults("results/cube_analysis.csv")
print("分析完成!")

gmsh.finalize()
```

### 示例2: 从Python调用C++求解器

```python
import sys
sys.path.append('../python')
import fem_core

# 创建求解器实例
solver = fem_core.FEMSolver()

# 添加4个节点(手动定义)
solver.addNode(0, 0.0, 0.0, 0.0)
solver.addNode(1, 1.0, 0.0, 0.0)
solver.addNode(2, 0.5, 0.866, 0.0)
solver.addNode(3, 0.5, 0.289, 0.816)

# 定义材料(钢)
steel = fem_core.Material(id=1, E=200e9, nu=0.3, rho=7850.0)
solver.addMaterial(steel)

# 添加四面体单元
elem = fem_core.create_tet4_element(id=0, 
                                    node_ids=[0,1,2,3], 
                                    material_id=1)
solver.addElement(elem)

# 边界条件(节点0全约束)
solver.addBoundaryCondition(node_id=0, dof=0, value=0.0)
solver.addBoundaryCondition(node_id=0, dof=1, value=0.0)
solver.addBoundaryCondition(node_id=0, dof=2, value=0.0)

# 载荷(节点3, Z方向-1000N)
solver.addLoad(node_id=3, fx=0.0, fy=0.0, fz=-1000.0)

# 求解流程
solver.initialize()
solver.assembleGlobalStiffnessMatrix()
solver.assembleGlobalForceVector()
solver.applyBoundaryConditions()
solver.solve()
solver.updateNodalDisplacements()

# 获取结果
displacements = solver.getDisplacements()
stresses = solver.getStresses()

print(f"位移结果: {displacements}")
print(f"应力结果: {stresses}")

# 导出到文件
solver.exportResults("results/simple_test.csv")
```

---

## 🐛 常见问题排查

### 问题1: "无法导入fem_core模块"

**症状**: `ImportError: No module named 'fem_core'`

**解决方案**:
1. 确认编译成功: 检查 `python/fem_core.cp39-win_amd64.pyd` 是否存在
2. 检查Python版本匹配: 编译时的Python版本必须与运行时一致
3. 添加路径:
   ```python
   import sys
   sys.path.append('path/to/python')
   import fem_core
   ```

### 问题2: CMake配置失败

**症状**: `CMake Error: Could not find pybind11`

**解决方案**:
```bash
# 安装pybind11
pip install pybind11

# 或手动指定路径
cmake -S . -B build -Dpybind11_DIR="C:/path/to/pybind11/share/cmake/pybind11"
```

### 问题3: 找不到Eigen3

**症状**: `CMake Error: Could not find Eigen3`

**解决方案**:
```bash
# 方法1: 使用vcpkg
vcpkg install eigen3:x64-windows
cmake -S . -B build -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake

# 方法2: 手动下载
# 下载Eigen3到C:/Libraries/eigen3
cmake -S . -B build -DEigen3_DIR="C:/Libraries/eigen3/share/eigen3/cmake"
```

### 问题4: 编译时出现链接错误

**症状**: `LNK2019: 无法解析的外部符号`

**解决方案**:
1. 确认所有`.cpp`文件都已添加到CMakeLists.txt
2. 检查编译器架构(必须是x64)
3. 清理后重新编译:
   ```bash
   rm -rf build
   cmake -S . -B build -G "Visual Studio 17 2022" -A x64
   cmake --build build --config Release
   ```

### 问题5: PyQt6界面无法启动

**症状**: 界面闪退或无法显示

**解决方案**:
```bash
# 重新安装PyQt6
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip
pip install PyQt6

# 检查Qt插件
python -c "from PyQt6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))"
```

### 问题6: Gmsh集成失败

**症状**: `gmsh.initialize()` 报错

**解决方案**:
```bash
# 重新安装gmsh
pip install --upgrade gmsh

# 验证安装
python -c "import gmsh; gmsh.initialize(); print('Gmsh版本:', gmsh.option.getString('General.Version')); gmsh.finalize()"
```

---

## 🤝 贡献指南

我们欢迎各种形式的贡献!

### 如何贡献

1. **Fork本项目**
2. **创建特性分支** (`git checkout -b feature/AmazingFeature`)
3. **提交更改** (`git commit -m 'Add some AmazingFeature'`)
4. **推送到分支** (`git push origin feature/AmazingFeature`)
5. **开启Pull Request**

### 提交规范

- **功能开发**: `feat: 添加模态分析功能`
- **Bug修复**: `fix: 修复刚度矩阵组装错误`
- **文档更新**: `docs: 更新快速启动指南`
- **性能优化**: `perf: 优化稀疏矩阵求解性能`
- **代码重构**: `refactor: 重构单元类继承结构`

### 开发建议

- 遵循现有代码风格
- 添加必要的注释和文档
- 编写单元测试覆盖新功能
- 更新相关文档
- 保持commits简洁明了

---

## 📄 许可证

本项目采用 **MIT许可证** - 详见 [LICENSE](LICENSE) 文件

**主要条款**:
- ✅ 可自由使用、修改、分发
- ✅ 可用于商业项目
- ⚠️ 需保留版权声明
- ⚠️ 软件按"原样"提供,不提供任何保证

---

## 👥 作者与贡献者

### 项目发起人

- **刘正鹏** - *主要开发者* - [ZPL-03](https://github.com/ZPL-03)
  - 西安交通大学 固体力学硕士研究生
  - 研究方向: 复合材料多尺度建模 + 机器学习

### 特别鸣谢

感谢以下开源项目为本软件提供支持:

- [Eigen](https://eigen.tuxfamily.org/) - 高性能C++线性代数库
- [pybind11](https://pybind11.readthedocs.io/) - Python-C++无缝绑定
- [Gmsh](https://gmsh.info/) - 强大的三维有限元网格生成器
- [VTK](https://vtk.org/) - 可视化工具包
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) - Python Qt绑定框架

---

## 📞 联系方式

- **项目主页**: [https://github.com/ZPL-03/CAE_FEM_Software](https://github.com/ZPL-03/CAE_FEM_Software)
- **问题反馈**: [Issues](https://github.com/ZPL-03/CAE_FEM_Software/issues)
- **讨论交流**: [Discussions](https://github.com/ZPL-03/CAE_FEM_Software/discussions)
- **邮件**: 1370872708@qq.com

---

## 🔖 版本历史

### v0.2.0 (2024-XX-XX) - 当前版本
- ✨ 重构项目结构(cpp/, python/, docs/, data/)
- ✨ 完善CMake构建系统
- ✨ 添加Von Mises应力计算
- ✨ 优化测试框架
- 📝 更新所有文档以适配新结构
- 🐛 修复多个已知问题

### v0.1.0 (2024-03-XX)
- 🎉 初始版本发布
- ✨ 基础C++求解器(四面体单元)
- ✨ Python绑定(pybind11)
- ✨ PyQt6主窗口框架
- ✨ 线性静力学分析
- 📝 基础文档和示例

---

## 🗺️ 路线图

### 2024年度目标

**Q2 (当前)**
- [ ] 完善前处理界面(几何建模、网格可视化)
- [ ] 集成VTK后处理可视化
- [ ] 添加更多单元类型(Hex8, Beam)

**Q3**
- [ ] 实现非线性静力学分析
- [ ] 添加模态分析功能
- [ ] 性能优化(多线程、稀疏求解器)

**Q4**
- [ ] 瞬态动力学分析
- [ ] 热传导分析
- [ ] 用户手册和教程视频

### 长期愿景

- 🎯 成为一款功能完备的开源CAE软件
- 🎯 支持复杂工程问题的快速仿真
- 🎯 为CAE和FEM学习者提供教学平台
- 🎯 构建活跃的开源社区

---

## 📚 学习资源

### 有限元理论

- 📖 《有限元方法》 - 王勖成
- 📖 《The Finite Element Method: Its Basis and Fundamentals》 - Zienkiewicz
- 📖 《Nonlinear Finite Elements for Continua and Structures》 - Belytschko

### 编程实践

- 💻 [PyQt6官方文档](https://www.riverbankcomputing.com/static/Docs/PyQt6/)
- 💻 [pybind11教程](https://pybind11.readthedocs.io/en/stable/basics.html)
- 💻 [Gmsh API指南](https://gmsh.info/doc/texinfo/gmsh.html#Gmsh-API)
- 💻 [Eigen文档](https://eigen.tuxfamily.org/dox/)

### 参考项目

- 🔗 [FEniCS](https://fenicsproject.org/) - Python有限元框架
- 🔗 [deal.II](https://www.dealii.org/) - C++有限元库
- 🔗 [CalculiX](http://www.calculix.de/) - 开源CAE软件
- 🔗 [MOOSE](https://mooseframework.inl.gov/) - 多物理场耦合框架

---

## ⚡ 性能参考

### 测试平台

- **CPU**: Intel Core i7-12700K
- **内存**: 32GB DDR4-3200
- **编译器**: MSVC 19.35 (VS2022)
- **Python**: 3.9.13

### 基准测试结果

| 网格规模 | 节点数 | 单元数 | 自由度 | 求解时间 |
|---------|-------|-------|--------|---------|
| 小型 | 100 | 400 | 300 | < 0.1s |
| 中型 | 1,000 | 4,000 | 3,000 | < 1s |
| 大型 | 10,000 | 40,000 | 30,000 | < 10s |
| 超大型 | 100,000 | 400,000 | 300,000 | 待测试 |

*注: 基于线性静力学分析,使用Eigen SparseLU求解器*

---

## 🎨 界面预览

### 主窗口

```
┌────────────────────────────────────────────────┐
│  文件(F)  编辑(E)  视图(V)  工具(T)  帮助(H)      │
├────────────────────────────────────────────────┤
│  [新建] [打开] [保存] | [网格] [求解] [后处理] │
├──────────┬─────────────────────────────────────┤
│          │                                     │
│  项目树  │         3D视图区域                  │
│          │                                     │
│  □模型   │      (Gmsh/VTK可视化窗口)           │
│  □网格   │                                     │
│  □材料   │                                     │
│  □载荷   │                                     │
│  □边界   │                                     │
│  □结果   │                                     │
│          │                                     │
├──────────┴─────────────────────────────────────┤
│  [状态栏] 就绪 | 节点: 0 | 单元: 0 | 自由度: 0  │
└────────────────────────────────────────────────┘
```

---

**⭐ 如果这个项目对您有帮助,请给个Star! ⭐**

---

<div align="center">

**开源软件 · 学术研究 · 工程实践**

Made with ❤️ by [ZPL-03](https://github.com/ZPL-03)

</div>
