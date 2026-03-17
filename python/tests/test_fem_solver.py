"""
fem_core 基础测试。

说明：
1. 这里既可以被 pytest 收集，也可以直接用 `python test_fem_solver.py` 运行；
2. 测试尽量覆盖材料矩阵、单元求解和结果导出三件基础能力；
3. 所有断言都尽量用初学者能看懂的中文注释说明意图。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

import fem_core


def test_material_properties() -> None:
    """验证线弹性本构矩阵的基本形状和对称性。"""

    material = fem_core.Material(1, 210e9, 0.3, 7850.0)
    elasticity_matrix = np.array(material.getElasticityMatrix(), dtype=float)

    assert elasticity_matrix.shape == (6, 6)
    assert np.allclose(elasticity_matrix, elasticity_matrix.T)
    assert np.isclose(elasticity_matrix[0, 0], elasticity_matrix[1, 1])
    assert elasticity_matrix[3, 3] > 0.0


def test_single_tet_workflow() -> None:
    """验证单个 Tet4 单元能完成完整静力学流程。"""

    solver = fem_core.FEMSolver()
    solver.setParallelEnabled(True)
    solver.setNumThreads(0)
    solver.setLinearSolverType("conjugate_gradient")
    solver.addMaterial(fem_core.Material(1, 210e9, 0.3, 7850.0))

    # 使用一个规则四面体，节点编号从 0 连续递增。
    solver.addNode(0, 0.0, 0.0, 0.0)
    solver.addNode(1, 1.0, 0.0, 0.0)
    solver.addNode(2, 0.0, 1.0, 0.0)
    solver.addNode(3, 0.0, 0.0, 1.0)
    solver.addElement(fem_core.create_tet4_element(0, [0, 1, 2, 3], 1))

    # 下面的约束组合用于消除刚体位移与刚体转动。
    solver.addBoundaryCondition(0, 0, 0.0)
    solver.addBoundaryCondition(0, 1, 0.0)
    solver.addBoundaryCondition(0, 2, 0.0)
    solver.addBoundaryCondition(1, 1, 0.0)
    solver.addBoundaryCondition(1, 2, 0.0)
    solver.addBoundaryCondition(2, 2, 0.0)

    # 在节点 3 施加一个 Z 向力。
    solver.addLoad(3, 0.0, 0.0, -1000.0)
    solver.runLinearStaticAnalysis()

    displacements = np.array(solver.getDisplacements(), dtype=float).reshape(-1, 3)
    stresses = np.array(solver.getStresses(), dtype=float)
    strains = np.array(solver.getStrains(), dtype=float)

    assert displacements.shape == (4, 3)
    assert np.all(np.isfinite(displacements))
    assert np.linalg.norm(displacements, axis=1).max() > 0.0
    assert stresses.shape == (1,)
    assert strains.shape == (1,)
    assert stresses[0] > 0.0
    assert strains[0] > 0.0
    assert solver.getAvailableThreads() >= 1
    assert solver.getNumThreads() >= 1
    assert solver.getLinearSolverType() == "conjugate_gradient"

    output_dir = PROJECT_ROOT / "data" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "test_single_tet_results.csv"
    solver.exportResults(str(output_file))
    assert output_file.exists()


def test_single_tet_nonlinear_entry() -> None:
    """验证非线性静力入口在当前线弹性框架下也能走通。"""

    solver = fem_core.FEMSolver()
    solver.setLinearSolverType("bicgstab")
    solver.addMaterial(fem_core.Material(1, 210e9, 0.3, 7850.0, 45.0, 2.5e8, 1.0e9, True))

    solver.addNode(0, 0.0, 0.0, 0.0)
    solver.addNode(1, 1.0, 0.0, 0.0)
    solver.addNode(2, 0.0, 1.0, 0.0)
    solver.addNode(3, 0.0, 0.0, 1.0)
    solver.addElement(fem_core.create_tet4_element(0, [0, 1, 2, 3], 1))

    solver.addBoundaryCondition(0, 0, 0.0)
    solver.addBoundaryCondition(0, 1, 0.0)
    solver.addBoundaryCondition(0, 2, 0.0)
    solver.addBoundaryCondition(1, 1, 0.0)
    solver.addBoundaryCondition(1, 2, 0.0)
    solver.addBoundaryCondition(2, 2, 0.0)
    solver.addLoad(3, 0.0, 0.0, -1000.0)

    solver.runNonlinearStaticAnalysis(3, 10, 1.0e-8)

    displacements = np.array(solver.getDisplacements(), dtype=float).reshape(-1, 3)
    assert np.all(np.isfinite(displacements))
    assert solver.hasConverged()
    assert solver.getLastIterationCount() >= 1
    assert solver.getLastResidualNorm() >= 0.0


def test_material_nonlinear_properties() -> None:
    """验证非线性材料参数可以正确暴露给 Python。"""

    material = fem_core.Material(7, 210e9, 0.3, 7850.0, 45.0, 3.0e8, 2.0e9, True)

    assert material.id == 7
    assert material.k == 45.0
    assert material.yield_strength == 3.0e8
    assert material.hardening_modulus == 2.0e9
    assert material.nonlinear_enabled is True


def test_single_tet_steady_state_thermal_workflow() -> None:
    """验证单个 Tet4 单元可以完成稳态热传导流程。"""

    solver = fem_core.FEMSolver()
    solver.setLinearSolverType("sparse_lu")
    solver.addMaterial(fem_core.Material(1, 210e9, 0.3, 7850.0, 45.0))

    solver.addNode(0, 0.0, 0.0, 0.0)
    solver.addNode(1, 1.0, 0.0, 0.0)
    solver.addNode(2, 0.0, 1.0, 0.0)
    solver.addNode(3, 0.0, 0.0, 1.0)
    solver.addElement(fem_core.create_tet4_element(0, [0, 1, 2, 3], 1))

    solver.addThermalBoundaryCondition(0, 20.0)
    solver.addThermalBoundaryCondition(1, 20.0)
    solver.addThermalBoundaryCondition(2, 20.0)
    solver.addThermalLoad(3, 120.0)

    solver.runSteadyStateThermalAnalysis()

    temperatures = np.array(solver.getTemperatures(), dtype=float)
    heat_flux = np.array(solver.getHeatFluxMagnitudes(), dtype=float)

    assert temperatures.shape == (4,)
    assert np.all(np.isfinite(temperatures))
    assert float(np.max(temperatures)) > 20.0
    assert heat_flux.shape == (1,)
    assert float(np.max(heat_flux)) > 0.0


def test_single_hex8_workflow() -> None:
    """验证单个 Hex8 单元可以完成完整静力学流程。"""

    solver = fem_core.FEMSolver()
    solver.setLinearSolverType("sparse_lu")
    solver.addMaterial(fem_core.Material(1, 210e9, 0.3, 7850.0))

    node_coords = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (1.0, 1.0, 1.0),
        (0.0, 1.0, 1.0),
    ]
    for node_id, coords in enumerate(node_coords):
        solver.addNode(node_id, coords[0], coords[1], coords[2])

    solver.addElement(fem_core.create_hex8_element(0, list(range(8)), 1))

    for node_id in [0, 3, 4, 7]:
        solver.addBoundaryCondition(node_id, 0, 0.0)
        solver.addBoundaryCondition(node_id, 1, 0.0)
        solver.addBoundaryCondition(node_id, 2, 0.0)

    for node_id in [1, 2, 5, 6]:
        solver.addLoad(node_id, 0.0, 0.0, -250.0)

    solver.runLinearStaticAnalysis()

    displacements = np.array(solver.getDisplacements(), dtype=float).reshape(-1, 3)
    stresses = np.array(solver.getStresses(), dtype=float)

    assert np.all(np.isfinite(displacements))
    assert np.max(np.abs(displacements[:, 2])) > 0.0
    assert stresses.shape == (1,)
    assert stresses[0] > 0.0


def test_modal_analysis_workflow() -> None:
    """验证模态分析能够返回固有频率和振型。"""

    solver = fem_core.FEMSolver()
    solver.addMaterial(fem_core.Material(1, 210e9, 0.3, 7850.0))

    node_coords = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (1.0, 1.0, 1.0),
        (0.0, 1.0, 1.0),
    ]
    for node_id, coords in enumerate(node_coords):
        solver.addNode(node_id, coords[0], coords[1], coords[2])

    solver.addElement(fem_core.create_hex8_element(0, list(range(8)), 1))

    for node_id in [0, 3, 4, 7]:
        solver.addBoundaryCondition(node_id, 0, 0.0)
        solver.addBoundaryCondition(node_id, 1, 0.0)
        solver.addBoundaryCondition(node_id, 2, 0.0)

    solver.runModalAnalysis(4)

    frequencies = np.array(solver.getModalFrequenciesHz(), dtype=float)
    mode_shape = np.array(solver.getModeShape(0), dtype=float).reshape(-1, 3)

    assert frequencies.size >= 1
    assert np.all(frequencies > 0.0)
    assert mode_shape.shape == (8, 3)
    assert np.max(np.abs(mode_shape)) > 0.0


def test_transient_analysis_workflow() -> None:
    """验证线性瞬态动力学流程能返回时间历程与最终位移。"""

    solver = fem_core.FEMSolver()
    solver.addMaterial(fem_core.Material(1, 210e9, 0.3, 7850.0))

    node_coords = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ]
    for node_id, coords in enumerate(node_coords):
        solver.addNode(node_id, coords[0], coords[1], coords[2])

    solver.addElement(fem_core.create_tet4_element(0, [0, 1, 2, 3], 1))
    solver.addBoundaryCondition(0, 0, 0.0)
    solver.addBoundaryCondition(0, 1, 0.0)
    solver.addBoundaryCondition(0, 2, 0.0)
    solver.addBoundaryCondition(1, 1, 0.0)
    solver.addBoundaryCondition(1, 2, 0.0)
    solver.addBoundaryCondition(2, 2, 0.0)
    solver.addLoad(3, 0.0, 0.0, -1000.0)

    solver.runTransientAnalysis(0.01, 0.001, 0.25, 0.5, 0.0, 0.0)

    times = np.array(solver.getTransientTimes(), dtype=float)
    history = np.array(solver.getTransientMaxDisplacements(), dtype=float)
    loaded_uz = np.array(solver.getTransientLoadedUzHistory(), dtype=float)
    displacements = np.array(solver.getDisplacements(), dtype=float).reshape(-1, 3)

    assert times.size == history.size
    assert times.size >= 2
    assert np.all(np.diff(times) >= 0.0)
    assert np.max(history) > 0.0
    assert loaded_uz.size == times.size
    assert np.max(np.abs(displacements)) > 0.0


def test_frequency_response_analysis_workflow() -> None:
    """验证频响分析能够返回幅频曲线。"""

    solver = fem_core.FEMSolver()
    solver.addMaterial(fem_core.Material(1, 210e9, 0.3, 7850.0))

    solver.addNode(0, 0.0, 0.0, 0.0)
    solver.addNode(1, 1.0, 0.0, 0.0)
    solver.addNode(2, 0.0, 1.0, 0.0)
    solver.addNode(3, 0.0, 0.0, 1.0)
    solver.addElement(fem_core.create_tet4_element(0, [0, 1, 2, 3], 1))

    solver.addBoundaryCondition(0, 0, 0.0)
    solver.addBoundaryCondition(0, 1, 0.0)
    solver.addBoundaryCondition(0, 2, 0.0)
    solver.addBoundaryCondition(1, 1, 0.0)
    solver.addBoundaryCondition(1, 2, 0.0)
    solver.addBoundaryCondition(2, 2, 0.0)
    solver.addLoad(3, 0.0, 0.0, -1000.0)

    solver.runFrequencyResponseAnalysis(1.0, 100.0, 12, 0.0, 0.0)

    frequencies = np.array(solver.getFrequencyResponseFrequenciesHz(), dtype=float)
    response = np.array(solver.getFrequencyResponseMaxDisplacements(), dtype=float)
    loaded_umag = np.array(solver.getFrequencyResponseLoadedUmagHistory(), dtype=float)

    assert frequencies.size == 12
    assert response.size == 12
    assert loaded_umag.size == 12
    assert np.all(np.diff(frequencies) > 0.0)
    assert np.max(response) > 0.0


def main() -> int:
    """允许直接作为脚本运行。"""

    test_material_properties()
    test_single_tet_workflow()
    test_single_tet_nonlinear_entry()
    test_material_nonlinear_properties()
    test_single_tet_steady_state_thermal_workflow()
    test_single_hex8_workflow()
    test_modal_analysis_workflow()
    test_transient_analysis_workflow()
    test_frequency_response_analysis_workflow()
    print("test_fem_solver.py 运行完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
