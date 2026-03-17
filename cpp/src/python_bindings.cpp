/**
 * @file python_bindings.cpp
 * @brief 使用 pybind11 将 C++ 有限元求解器导出给 Python
 */

#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include <pybind11/eigen.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "fem_solver.h"

namespace py = pybind11;

namespace {

/**
 * @brief 检查 Python 传入的三维向量长度是否正确
 */
void ensureThreeComponents(const std::vector<double>& values, const std::string& name) {
    if (values.size() != 3) {
        throw std::runtime_error(name + " 必须包含 3 个分量。");
    }
}

}  // namespace

PYBIND11_MODULE(fem_core, module) {
    module.doc() = "CAE 有限元求解核心 Python 绑定";

    py::class_<Node>(module, "Node")
        .def(py::init<int, double, double, double>(),
             py::arg("id"), py::arg("x"), py::arg("y"), py::arg("z"))
        .def_readwrite("id", &Node::id)
        .def_property(
            "coords",
            [](const Node& node) {
                return std::vector<double>{node.coords(0), node.coords(1), node.coords(2)};
            },
            [](Node& node, const std::vector<double>& values) {
                ensureThreeComponents(values, "coords");
                node.coords << values[0], values[1], values[2];
            },
            "节点坐标 [x, y, z]"
        )
        .def_property(
            "displacement",
            [](const Node& node) {
                return std::vector<double>{node.displacement(0), node.displacement(1), node.displacement(2)};
            },
            [](Node& node, const std::vector<double>& values) {
                ensureThreeComponents(values, "displacement");
                node.displacement << values[0], values[1], values[2];
            },
            "节点位移 [ux, uy, uz]"
        );

    py::class_<Material>(module, "Material")
        .def(py::init<int, double, double, double, double, double, double, bool>(),
             py::arg("id"),
             py::arg("E"),
             py::arg("nu"),
             py::arg("rho") = 0.0,
             py::arg("thermal_conductivity") = 45.0,
             py::arg("yield_strength") = 0.0,
             py::arg("hardening_modulus") = 0.0,
             py::arg("nonlinear_enabled") = false)
        .def_readwrite("id", &Material::id)
        .def_readwrite("E", &Material::E)
        .def_readwrite("nu", &Material::nu)
        .def_readwrite("rho", &Material::rho)
        .def_readwrite("k", &Material::k)
        .def_readwrite("yield_strength", &Material::yield_strength)
        .def_readwrite("hardening_modulus", &Material::hardening_modulus)
        .def_readwrite("nonlinear_enabled", &Material::nonlinear_enabled)
        .def("getElasticityMatrix", &Material::getElasticityMatrix,
             "返回 3D 线弹性本构矩阵 D。");

    py::class_<Element, std::shared_ptr<Element>>(module, "Element");

    py::class_<Tet4Element, Element, std::shared_ptr<Tet4Element>>(module, "Tet4Element")
        .def(py::init<int, const std::vector<int>&, int>(),
             py::arg("id"), py::arg("node_ids"), py::arg("material_id"))
        .def_readwrite("id", &Tet4Element::id)
        .def_readwrite("node_ids", &Tet4Element::node_ids)
        .def_readwrite("material_id", &Tet4Element::material_id)
        .def("computeVolume", &Tet4Element::computeVolume,
             py::arg("nodes"),
             "计算四面体单元体积。");

    py::class_<Hex8Element, Element, std::shared_ptr<Hex8Element>>(module, "Hex8Element")
        .def(py::init<int, const std::vector<int>&, int>(),
             py::arg("id"), py::arg("node_ids"), py::arg("material_id"))
        .def_readwrite("id", &Hex8Element::id)
        .def_readwrite("node_ids", &Hex8Element::node_ids)
        .def_readwrite("material_id", &Hex8Element::material_id)
        .def("computeVolume", &Hex8Element::computeVolume,
             py::arg("nodes"),
             "计算六面体单元体积。");

    py::class_<BoundaryCondition>(module, "BoundaryCondition")
        .def(py::init<int, int, double>(),
             py::arg("node_id"), py::arg("dof"), py::arg("value"))
        .def_readwrite("node_id", &BoundaryCondition::node_id)
        .def_readwrite("dof", &BoundaryCondition::dof)
        .def_readwrite("value", &BoundaryCondition::value);

    py::class_<Load>(module, "Load")
        .def(py::init<int, double, double, double>(),
             py::arg("node_id"), py::arg("fx"), py::arg("fy"), py::arg("fz"))
        .def_readwrite("node_id", &Load::node_id)
        .def_property(
            "force",
            [](const Load& load) {
                return std::vector<double>{load.force(0), load.force(1), load.force(2)};
            },
            [](Load& load, const std::vector<double>& values) {
                ensureThreeComponents(values, "force");
                load.force << values[0], values[1], values[2];
            },
            "节点力向量 [fx, fy, fz]"
        );

    py::class_<FEMSolver>(module, "FEMSolver")
        .def(py::init<>())
        .def("addNode", &FEMSolver::addNode,
             py::arg("id"), py::arg("x"), py::arg("y"), py::arg("z"),
             "添加节点。当前版本要求节点编号从 0 开始连续递增。")
        .def("addElement", &FEMSolver::addElement,
             py::arg("element"),
             "添加有限元单元。")
        .def("addMaterial", &FEMSolver::addMaterial,
             py::arg("material"),
             "添加材料。")
        .def("addBoundaryCondition", &FEMSolver::addBoundaryCondition,
             py::arg("node_id"), py::arg("dof"), py::arg("value"),
             "添加位移边界条件。")
        .def("addLoad", &FEMSolver::addLoad,
             py::arg("node_id"), py::arg("fx"), py::arg("fy"), py::arg("fz"),
             "添加节点载荷。")
        .def("addThermalBoundaryCondition", &FEMSolver::addThermalBoundaryCondition,
             py::arg("node_id"), py::arg("temperature"),
             "添加节点温度边界条件。")
        .def("addThermalLoad", &FEMSolver::addThermalLoad,
             py::arg("node_id"), py::arg("value"),
             "添加节点热载荷。")
        .def("initialize", &FEMSolver::initialize,
             "初始化全局矩阵与向量。")
        .def("assembleGlobalStiffnessMatrix", &FEMSolver::assembleGlobalStiffnessMatrix,
             "组装全局刚度矩阵。")
        .def("assembleGlobalForceVector", &FEMSolver::assembleGlobalForceVector,
             "组装全局载荷向量。")
        .def("applyBoundaryConditions", &FEMSolver::applyBoundaryConditions,
             "通过罚函数法施加位移边界条件。")
        .def("solve", &FEMSolver::solve,
             "求解线弹性静力学方程组。")
        .def("updateNodalDisplacements", &FEMSolver::updateNodalDisplacements,
             "将全局位移写回各节点对象。")
        .def("runLinearStaticAnalysis", &FEMSolver::runLinearStaticAnalysis,
             "一键执行完整线弹性静力学分析。")
        .def("runNonlinearStaticAnalysis", &FEMSolver::runNonlinearStaticAnalysis,
             py::arg("load_steps"),
             py::arg("max_iterations"),
             py::arg("tolerance"),
             "执行增量-Newton 静力学分析流程。")
        .def("runModalAnalysis", &FEMSolver::runModalAnalysis,
             py::arg("mode_count"),
             "执行线性模态分析，提取指定阶数的固有频率与振型。")
        .def("runTransientAnalysis", &FEMSolver::runTransientAnalysis,
             py::arg("total_time"),
             py::arg("time_step"),
             py::arg("beta"),
             py::arg("gamma"),
             py::arg("damping_alpha"),
             py::arg("damping_beta"),
             "执行线性瞬态动力学分析，使用 Newmark-beta 时间积分。")
        .def("runFrequencyResponseAnalysis", &FEMSolver::runFrequencyResponseAnalysis,
             py::arg("start_frequency_hz"),
             py::arg("end_frequency_hz"),
             py::arg("point_count"),
             py::arg("damping_alpha"),
             py::arg("damping_beta"),
             "执行线性频响分析，逐频率点求解稳态谐响应幅值。")
        .def("runSteadyStateThermalAnalysis", &FEMSolver::runSteadyStateThermalAnalysis,
             "执行稳态热传导分析。")
        .def("setLinearSolverType", &FEMSolver::setLinearSolverType,
             py::arg("solver_type"),
             "设置线性方程组求解器类型。支持 sparse_lu / conjugate_gradient / bicgstab。")
        .def("getLinearSolverType", &FEMSolver::getLinearSolverType,
             "返回当前线性方程组求解器类型。")
        .def("setParallelEnabled", &FEMSolver::setParallelEnabled,
             py::arg("enabled"),
             "启用或关闭 OpenMP 并行路径。")
        .def("isParallelEnabled", &FEMSolver::isParallelEnabled,
             "返回当前是否启用了并行路径。")
        .def("setNumThreads", &FEMSolver::setNumThreads,
             py::arg("threads"),
             "设置线程数。0 表示自动使用系统可用线程数。")
        .def("getNumThreads", &FEMSolver::getNumThreads,
             "返回当前求解器准备使用的线程数。")
        .def("getAvailableThreads", &FEMSolver::getAvailableThreads,
             "返回当前编译环境下可用的最大线程数。")
        .def("hasConverged", &FEMSolver::hasConverged,
             "返回最近一次分析是否收敛。")
        .def("getLastIterationCount", &FEMSolver::getLastIterationCount,
             "返回最近一次分析的最后迭代次数。")
        .def("getLastResidualNorm", &FEMSolver::getLastResidualNorm,
             "返回最近一次分析的最终残差范数。")
        .def("getDisplacements", &FEMSolver::getDisplacements,
             "返回所有节点位移，展平为 [ux0, uy0, uz0, ux1, ...]。")
        .def("getStresses", &FEMSolver::getStresses,
             "返回每个单元的 Von Mises 等效应力。")
        .def("getStrains", &FEMSolver::getStrains,
             "返回每个单元的等效应变。")
        .def("getMeshBounds", &FEMSolver::getMeshBounds,
             "返回模型包围盒 [xmin, xmax, ymin, ymax, zmin, zmax]。")
        .def("getModalFrequenciesHz", &FEMSolver::getModalFrequenciesHz,
             "返回最近一次模态分析的固有频率列表，单位 Hz。")
        .def("getModeShape", &FEMSolver::getModeShape,
             py::arg("mode_index"),
             "返回指定模态阶次的振型向量，展平为 [ux0, uy0, uz0, ...]。")
        .def("getTransientTimes", &FEMSolver::getTransientTimes,
             "返回最近一次瞬态分析的时间序列。")
        .def("getTransientMaxDisplacements", &FEMSolver::getTransientMaxDisplacements,
             "返回最近一次瞬态分析每个时刻的最大位移幅值。")
        .def("getTransientLoadedUxHistory", &FEMSolver::getTransientLoadedUxHistory,
             "返回最近一次瞬态分析中受载面平均 Ux 响应历史。")
        .def("getTransientLoadedUyHistory", &FEMSolver::getTransientLoadedUyHistory,
             "返回最近一次瞬态分析中受载面平均 Uy 响应历史。")
        .def("getTransientLoadedUzHistory", &FEMSolver::getTransientLoadedUzHistory,
             "返回最近一次瞬态分析中受载面平均 Uz 响应历史。")
        .def("getTransientLoadedUmagHistory", &FEMSolver::getTransientLoadedUmagHistory,
             "返回最近一次瞬态分析中受载面平均位移幅值历史。")
        .def("getFrequencyResponseFrequenciesHz", &FEMSolver::getFrequencyResponseFrequenciesHz,
             "返回最近一次频响分析的频率序列，单位 Hz。")
        .def("getFrequencyResponseMaxDisplacements", &FEMSolver::getFrequencyResponseMaxDisplacements,
             "返回最近一次频响分析每个频率点的最大位移响应幅值。")
        .def("getFrequencyResponseLoadedUxHistory", &FEMSolver::getFrequencyResponseLoadedUxHistory,
             "返回最近一次频响分析中受载面平均 Ux 幅值曲线。")
        .def("getFrequencyResponseLoadedUyHistory", &FEMSolver::getFrequencyResponseLoadedUyHistory,
             "返回最近一次频响分析中受载面平均 Uy 幅值曲线。")
        .def("getFrequencyResponseLoadedUzHistory", &FEMSolver::getFrequencyResponseLoadedUzHistory,
             "返回最近一次频响分析中受载面平均 Uz 幅值曲线。")
        .def("getFrequencyResponseLoadedUmagHistory", &FEMSolver::getFrequencyResponseLoadedUmagHistory,
             "返回最近一次频响分析中受载面平均位移幅值曲线。")
        .def("getTemperatures", &FEMSolver::getTemperatures,
             "返回最近一次稳态热分析的节点温度。")
        .def("getHeatFluxMagnitudes", &FEMSolver::getHeatFluxMagnitudes,
             "返回最近一次稳态热分析的单元热流密度幅值。")
        .def("exportResults", &FEMSolver::exportResults,
             py::arg("filename"),
             "将节点位移和单元结果导出为 CSV 文件。")
        .def("printInfo", &FEMSolver::printInfo,
             "在控制台打印模型统计信息。")
        .def("getNodeCount", &FEMSolver::getNodeCount)
        .def("getElementCount", &FEMSolver::getElementCount)
        .def("getMaterialCount", &FEMSolver::getMaterialCount)
        .def("getBoundaryConditionCount", &FEMSolver::getBoundaryConditionCount)
        .def("getLoadCount", &FEMSolver::getLoadCount)
        .def("getNumDofs", &FEMSolver::getNumDofs);

    module.def(
        "create_tet4_element",
        [](int id, const std::vector<int>& node_ids, int material_id) {
            return std::make_shared<Tet4Element>(id, node_ids, material_id);
        },
        py::arg("id"),
        py::arg("node_ids"),
        py::arg("material_id"),
        "创建一个 Tet4 四面体单元对象。"
    );

    module.def(
        "create_hex8_element",
        [](int id, const std::vector<int>& node_ids, int material_id) {
            return std::make_shared<Hex8Element>(id, node_ids, material_id);
        },
        py::arg("id"),
        py::arg("node_ids"),
        py::arg("material_id"),
        "创建一个 Hex8 六面体单元对象。"
    );
}
