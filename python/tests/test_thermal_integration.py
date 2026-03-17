"""
稳态热传导集成测试。
说明：
1. 这里专门覆盖热模块，避免和结构求解测试混在一起难以定位问题。
2. 测试既校核 fem_core 热接口，也校核 Python 服务层的热分析工作流。
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
from app.models import ProjectState
from app.services.mesh_service import generate_volume_mesh
from app.services.solver_service import run_linear_static_analysis


def test_fem_core_steady_state_thermal_entry() -> None:
    """验证 C++ 热分析入口可以求出温度场和热流结果。"""

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


def test_steady_state_thermal_service_entry() -> None:
    """验证服务层稳态热传导流程可以生成温度场和热流结果。"""

    state = ProjectState()
    state.geometry.primitive = "box"
    state.geometry.mode = "box"
    state.geometry.length = 1.0
    state.geometry.width = 0.2
    state.geometry.height = 0.2
    state.geometry.mesh_size = 0.18
    state.geometry.mesh_file = str(PROJECT_ROOT / "data" / "models" / "test_thermal_mesh.msh")
    state.solver.analysis_type = "steady_state_thermal"
    state.solver.linear_solver = "conjugate_gradient"
    state.solver.result_file = str(PROJECT_ROOT / "data" / "results" / "test_thermal_results.csv")
    state.solver.result_vtk_file = str(PROJECT_ROOT / "data" / "results" / "test_thermal_results.vtu")
    state.material.is_applied = True
    state.thermal_boundary.target_face = "xmin"
    state.thermal_boundary.temperature = 20.0
    state.thermal_boundary.is_applied = True
    state.thermal_load.target_face = "xmax"
    state.thermal_load.heat_power = 5000.0
    state.thermal_load.is_applied = True

    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    assert artifacts.temperature_array is not None
    assert artifacts.heat_flux_array is not None
    assert float(np.max(artifacts.temperature_array)) >= 20.0
    assert float(np.max(artifacts.temperature_array)) > float(np.min(artifacts.temperature_array))
    assert float(np.max(artifacts.heat_flux_array)) > 0.0
    assert state.solver.linear_solver == "conjugate_gradient"
    assert artifacts.summary.max_temperature >= artifacts.summary.min_temperature
    assert Path(artifacts.summary.export_file).exists()
    assert Path(artifacts.summary.export_vtk_file).exists()
