"""
有限元求解服务。

这一层统一管理：
1. 如何把 PyVista/Gmsh 网格送入 fem_core；
2. 如何根据项目状态自动生成边界条件和载荷；
3. 如何整理位移、应力、应变结果；
4. 如何导出 CSV 和 VTU。
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import pyvista as pv

from app.models import ProjectState, ResultSummary
from app.runtime_paths import APP_ROOT, bootstrap_runtime_environment, resolve_runtime_path
from app.services.mesh_service import MeshBundle

# 源码模式下，先把仓库内的 `python/` 目录加入搜索路径；
# 打包模式下，再由 `bootstrap_runtime_environment()` 补齐 EXE 目录等候选路径。
SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_PYTHON_ROOT = SOURCE_PROJECT_ROOT / "python"
if str(SOURCE_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PYTHON_ROOT))

bootstrap_runtime_environment()
PROJECT_ROOT = APP_ROOT
PYTHON_ROOT = PROJECT_ROOT / "python"

import fem_core  # noqa: E402


GENERIC_LINEAR_SOLVER_OPTIONS: list[tuple[str, str]] = [
    ("SparseLU 直接法", "sparse_lu"),
    ("Conjugate Gradient", "conjugate_gradient"),
    ("BiCGSTAB", "bicgstab"),
]

# 不同分析步对应的求解算法集合。
# 说明：
# 1. 线性静力、非线性静力、线性瞬态都会在内部反复求解实数稀疏线性方程组；
# 2. 线性模态走广义特征值问题求解，不使用上面的线性方程组选项；
# 3. 线性频响当前走内置复数直接法，也不使用上面的线性方程组选项。
ANALYSIS_SOLVER_OPTIONS: dict[str, list[tuple[str, str]]] = {
    "linear_static": GENERIC_LINEAR_SOLVER_OPTIONS,
    "nonlinear_static": GENERIC_LINEAR_SOLVER_OPTIONS,
    "transient_dynamic": GENERIC_LINEAR_SOLVER_OPTIONS,
    "steady_state_thermal": GENERIC_LINEAR_SOLVER_OPTIONS,
    "modal_analysis": [("内置广义特征值求解器", "modal_eigensolver")],
    "frequency_response": [("内置复数直接频响法", "harmonic_direct")],
}

ANALYSIS_SOLVER_CAPTIONS: dict[str, str] = {
    "linear_static": "线性求解器：",
    "nonlinear_static": "线性求解器：",
    "transient_dynamic": "线性求解器：",
    "steady_state_thermal": "热方程求解器：",
    "modal_analysis": "模态求解器：",
    "frequency_response": "频响求解算法：",
}

ANALYSIS_SOLVER_HINTS: dict[str, str] = {
    "linear_static": "当前分析步会使用所选稀疏线性求解器。",
    "nonlinear_static": "非线性每次迭代都要解线性切线方程组，因此这里的求解器会实际参与计算。",
    "transient_dynamic": "瞬态动力学每个时间步都会组装并求解实数方程组，因此这里的求解器会实际参与计算。",
    "steady_state_thermal": "稳态热传导会组装并求解标量热传导方程，因此这里的求解器会实际参与计算。",
    "modal_analysis": "模态分析使用内置广义特征值求解器，线性方程组求解器不会参与本步计算。",
    "frequency_response": "频响分析当前使用内置复数直接频响法，线性方程组求解器不会参与本步计算。",
}

DAMPING_MODES: dict[str, str] = {
    "none": "无阻尼",
    "rayleigh": "直接 Rayleigh 阻尼",
    "modal_ratio": "按阻尼比换算 Rayleigh",
}


@dataclass
class AnalysisArtifacts:
    """求解完成后返回给界面的结果对象。"""

    solver: fem_core.FEMSolver
    displacement_matrix: np.ndarray
    stress_array: np.ndarray
    strain_array: np.ndarray
    temperature_array: Optional[np.ndarray]
    heat_flux_array: Optional[np.ndarray]
    result_grid: pv.UnstructuredGrid
    deformed_grid: pv.UnstructuredGrid
    summary: ResultSummary
    modal_shapes: Optional[List[np.ndarray]] = None
    transient_times: Optional[np.ndarray] = None
    transient_max_displacements: Optional[np.ndarray] = None
    transient_loaded_ux_history: Optional[np.ndarray] = None
    transient_loaded_uy_history: Optional[np.ndarray] = None
    transient_loaded_uz_history: Optional[np.ndarray] = None
    transient_loaded_umag_history: Optional[np.ndarray] = None
    frequency_response_frequencies_hz: Optional[np.ndarray] = None
    frequency_response_max_displacements: Optional[np.ndarray] = None
    frequency_response_loaded_ux_history: Optional[np.ndarray] = None
    frequency_response_loaded_uy_history: Optional[np.ndarray] = None
    frequency_response_loaded_uz_history: Optional[np.ndarray] = None
    frequency_response_loaded_umag_history: Optional[np.ndarray] = None


def _detect_face_nodes(points: np.ndarray, face_name: str, tolerance_ratio: float) -> list[int]:
    """根据包围盒识别指定极值端面上的节点。"""

    xmin, xmax = float(points[:, 0].min()), float(points[:, 0].max())
    ymin, ymax = float(points[:, 1].min()), float(points[:, 1].max())
    zmin, zmax = float(points[:, 2].min()), float(points[:, 2].max())

    span = max(xmax - xmin, ymax - ymin, zmax - zmin, 1.0)
    tolerance = max(span * tolerance_ratio, 1.0e-9)

    selectors = {
        "xmin": lambda point: abs(point[0] - xmin) <= tolerance,
        "xmax": lambda point: abs(point[0] - xmax) <= tolerance,
        "ymin": lambda point: abs(point[1] - ymin) <= tolerance,
        "ymax": lambda point: abs(point[1] - ymax) <= tolerance,
        "zmin": lambda point: abs(point[2] - zmin) <= tolerance,
        "zmax": lambda point: abs(point[2] - zmax) <= tolerance,
    }

    if face_name not in selectors:
        raise RuntimeError(f"不支持的端面名称：{face_name}")

    selector = selectors[face_name]
    return [node_id for node_id, point in enumerate(points) if selector(point)]


def _resolve_target_nodes(mesh_bundle: MeshBundle, target_name: str, tolerance_ratio: float) -> list[int]:
    """根据真实表面实体或兼容端面名称解析目标节点集合。"""

    if target_name in mesh_bundle.surface_node_sets:
        return mesh_bundle.surface_node_sets[target_name]
    return _detect_face_nodes(mesh_bundle.points, target_name, tolerance_ratio)


def build_result_grid(
    mesh_bundle: MeshBundle,
    displacement_matrix: np.ndarray,
    stress_array: np.ndarray,
    strain_array: np.ndarray,
    warp_factor: float,
    temperature_array: Optional[np.ndarray] = None,
    heat_flux_array: Optional[np.ndarray] = None,
) -> tuple[pv.UnstructuredGrid, pv.UnstructuredGrid]:
    """生成原始结果网格和变形后结果网格。"""

    result_grid = mesh_bundle.display_grid.copy(deep=True) if mesh_bundle.solver_cell_type == "hexa" else mesh_bundle.grid.copy(deep=True)
    result_grid.point_data["Displacement"] = displacement_matrix
    result_grid.point_data["DisplacementMagnitude"] = np.linalg.norm(displacement_matrix, axis=1)

    if len(stress_array) == result_grid.n_cells:
        result_grid.cell_data["VonMisesStress"] = stress_array
    if len(strain_array) == result_grid.n_cells:
        result_grid.cell_data["EquivalentStrain"] = strain_array
    if temperature_array is not None and len(temperature_array) == result_grid.n_points:
        result_grid.point_data["Temperature"] = temperature_array
    if heat_flux_array is not None and len(heat_flux_array) == result_grid.n_cells:
        result_grid.cell_data["HeatFluxMagnitude"] = heat_flux_array

    deformed_grid = result_grid.warp_by_vector("Displacement", factor=warp_factor)
    return result_grid, deformed_grid


def _configure_parallel(solver: fem_core.FEMSolver, state: ProjectState) -> None:
    """把并行配置写入 C++ 求解器。"""

    available_threads = max(
        1,
        int(solver.getAvailableThreads()) if hasattr(solver, "getAvailableThreads") else int(state.solver.num_threads or 1),
    )
    normalized_threads = max(1, int(state.solver.num_threads or available_threads))
    state.solver.num_threads = min(normalized_threads, available_threads) if available_threads > 0 else normalized_threads
    if hasattr(solver, "setParallelEnabled"):
        solver.setParallelEnabled(state.solver.use_parallel)
    if hasattr(solver, "setNumThreads"):
        solver.setNumThreads(state.solver.num_threads)


def get_solver_options_for_analysis(analysis_type: str) -> list[tuple[str, str]]:
    """返回指定分析步允许的求解算法列表。"""

    return list(ANALYSIS_SOLVER_OPTIONS.get(analysis_type, GENERIC_LINEAR_SOLVER_OPTIONS))


def normalize_solver_for_analysis(analysis_type: str, solver_name: str) -> str:
    """把求解器值纠正到当前分析步支持的集合中。"""

    allowed_values = [value for _label, value in get_solver_options_for_analysis(analysis_type)]
    if solver_name in allowed_values:
        return solver_name
    return allowed_values[0]


def get_solver_caption_for_analysis(analysis_type: str) -> str:
    """返回求解器控件左侧的标签文本。"""

    return ANALYSIS_SOLVER_CAPTIONS.get(analysis_type, "线性求解器：")


def get_solver_hint_for_analysis(analysis_type: str) -> str:
    """返回当前分析步对应的求解器提示。"""

    return ANALYSIS_SOLVER_HINTS.get(analysis_type, "当前分析步将使用所选求解器配置。")


def resolve_dynamic_rayleigh_coefficients(state: ProjectState) -> tuple[float, float]:
    """根据当前动态阻尼配置得到实际参与求解的 Rayleigh 系数。"""

    mode = state.solver.damping_mode
    if mode == "none":
        return 0.0, 0.0
    if mode == "rayleigh":
        return state.solver.rayleigh_alpha, state.solver.rayleigh_beta
    if mode == "modal_ratio":
        damping_ratio = max(float(state.solver.modal_damping_ratio), 0.0)
        freq1_hz = float(state.solver.modal_damping_freq1_hz)
        freq2_hz = float(state.solver.modal_damping_freq2_hz)
        if freq1_hz <= 0.0 or freq2_hz <= 0.0:
            raise RuntimeError("阻尼参考频率必须大于 0 Hz。")
        if abs(freq1_hz - freq2_hz) <= 1.0e-12:
            raise RuntimeError("两个阻尼参考频率不能相同，否则无法换算 Rayleigh 系数。")
        omega1 = 2.0 * np.pi * freq1_hz
        omega2 = 2.0 * np.pi * freq2_hz
        alpha = 2.0 * damping_ratio * omega1 * omega2 / (omega1 + omega2)
        beta = 2.0 * damping_ratio / (omega1 + omega2)
        return float(alpha), float(beta)
    raise RuntimeError(f"不支持的阻尼模式：{mode}")


def describe_dynamic_damping(state: ProjectState) -> str:
    """返回当前动态分析阻尼设置的中文摘要。"""

    mode = state.solver.damping_mode
    mode_label = DAMPING_MODES.get(mode, mode)
    if mode == "none":
        return mode_label
    if mode == "rayleigh":
        return f"{mode_label} (α={state.solver.rayleigh_alpha:.3e}, β={state.solver.rayleigh_beta:.3e})"
    if mode == "modal_ratio":
        alpha, beta = resolve_dynamic_rayleigh_coefficients(state)
        return (
            f"{mode_label} (ζ={state.solver.modal_damping_ratio:.4f}, "
            f"f1={state.solver.modal_damping_freq1_hz:.3f} Hz, "
            f"f2={state.solver.modal_damping_freq2_hz:.3f} Hz, "
            f"α={alpha:.3e}, β={beta:.3e})"
        )
    return mode_label


def _configure_linear_solver(solver: fem_core.FEMSolver, state: ProjectState) -> None:
    """把线性方程组求解策略写入 C++ 求解器。"""

    if state.solver.analysis_type not in {"linear_static", "nonlinear_static", "transient_dynamic", "steady_state_thermal"}:
        return
    if hasattr(solver, "setLinearSolverType"):
        solver.setLinearSolverType(state.solver.linear_solver)


def _select_material_id_for_element(points: np.ndarray, node_ids: list[int], state: ProjectState) -> int:
    """
    返回当前激活材料编号。

    说明：
    1. 当前软件先按单一几何体工作流处理；
    2. 当前激活材料会直接赋予整个几何体；
    3. 后续扩展到多零件装配和集合指派时，再升级这里的材料分配策略。
    """
    return state.material.material_id


def _run_modal_analysis(
    solver: fem_core.FEMSolver,
    mesh_bundle: MeshBundle,
    state: ProjectState,
    fixed_nodes: list[int],
) -> AnalysisArtifacts:
    """执行线性模态分析，并返回一阶振型用于当前界面显示。"""

    boundary_condition = state.boundary_condition
    constrained_dofs = (
        (0, boundary_condition.constrain_x),
        (1, boundary_condition.constrain_y),
        (2, boundary_condition.constrain_z),
    )
    if not any(enabled for _dof, enabled in constrained_dofs):
        raise RuntimeError("模态分析至少需要约束一个自由度方向，否则刚体模态无法消除。")

    for node_id in fixed_nodes:
        for dof, enabled in constrained_dofs:
            if enabled:
                solver.addBoundaryCondition(node_id, dof, 0.0)

    solver.runModalAnalysis(state.solver.modal_count)
    modal_frequencies = np.array(solver.getModalFrequenciesHz(), dtype=float)
    modal_shapes = [
        np.array(solver.getModeShape(mode_index), dtype=float).reshape(-1, 3)
        for mode_index in range(len(modal_frequencies))
    ]
    first_mode = modal_shapes[0]
    stress_array = np.zeros(mesh_bundle.display_grid.n_cells if mesh_bundle.solver_cell_type == "hexa" else mesh_bundle.grid.n_cells, dtype=float)
    strain_array = np.zeros_like(stress_array)

    result_grid, deformed_grid = build_result_grid(
        mesh_bundle,
        first_mode,
        stress_array,
        strain_array,
        state.solver.warp_factor,
    )

    export_file = resolve_runtime_path(state.solver.result_file)
    export_file.parent.mkdir(parents=True, exist_ok=True)
    solver.exportResults(str(export_file))

    export_vtk_file = resolve_runtime_path(state.solver.result_vtk_file)
    export_vtk_file.parent.mkdir(parents=True, exist_ok=True)
    result_grid.save(export_vtk_file)

    summary = ResultSummary(
        max_displacement=float(np.linalg.norm(first_mode, axis=1).max(initial=0.0)),
        min_z_displacement=float(first_mode[:, 2].min(initial=0.0)),
        max_von_mises=0.0,
        max_equivalent_strain=0.0,
        mean_von_mises=0.0,
        mean_equivalent_strain=0.0,
        fixed_node_count=len(fixed_nodes),
        loaded_node_count=0,
        solve_time_seconds=0.0,
        converged=True,
        iteration_count=int(modal_frequencies.size),
        residual_norm=0.0,
        modal_frequencies_hz=modal_frequencies.tolist(),
        export_file=str(export_file),
        export_vtk_file=str(export_vtk_file),
    )

    return AnalysisArtifacts(
        solver=solver,
        displacement_matrix=first_mode,
        stress_array=stress_array,
        strain_array=strain_array,
        temperature_array=None,
        heat_flux_array=None,
        result_grid=result_grid,
        deformed_grid=deformed_grid,
        summary=summary,
        modal_shapes=modal_shapes,
    )


def _run_transient_analysis(
    solver: fem_core.FEMSolver,
    mesh_bundle: MeshBundle,
    state: ProjectState,
    fixed_nodes: list[int],
    loaded_nodes: list[int],
) -> AnalysisArtifacts:
    """执行线性瞬态动力学分析，返回最终时刻结果与响应历史。"""

    boundary_condition = state.boundary_condition
    constrained_dofs = (
        (0, boundary_condition.constrain_x, boundary_condition.displacement_x),
        (1, boundary_condition.constrain_y, boundary_condition.displacement_y),
        (2, boundary_condition.constrain_z, boundary_condition.displacement_z),
    )
    if not any(enabled for _dof, enabled, _value in constrained_dofs):
        raise RuntimeError("瞬态分析至少需要一个位移约束方向。")

    for node_id in fixed_nodes:
        for dof, enabled, value in constrained_dofs:
            if enabled:
                solver.addBoundaryCondition(node_id, dof, float(value))

    force_components = np.array([state.load_case.force_x, state.load_case.force_y, state.load_case.force_z], dtype=float)
    nodal_force = force_components / len(loaded_nodes)
    for node_id in loaded_nodes:
        solver.addLoad(node_id, float(nodal_force[0]), float(nodal_force[1]), float(nodal_force[2]))

    solver.runTransientAnalysis(
        state.solver.total_time,
        state.solver.time_step,
        state.solver.newmark_beta,
        state.solver.newmark_gamma,
        *resolve_dynamic_rayleigh_coefficients(state),
    )

    displacement_matrix = np.array(solver.getDisplacements(), dtype=float).reshape(-1, 3)
    stress_array = np.array(solver.getStresses(), dtype=float)
    strain_array = np.array(solver.getStrains(), dtype=float)
    transient_times = np.array(solver.getTransientTimes(), dtype=float)
    transient_max_displacements = np.array(solver.getTransientMaxDisplacements(), dtype=float)
    transient_loaded_ux_history = np.array(solver.getTransientLoadedUxHistory(), dtype=float)
    transient_loaded_uy_history = np.array(solver.getTransientLoadedUyHistory(), dtype=float)
    transient_loaded_uz_history = np.array(solver.getTransientLoadedUzHistory(), dtype=float)
    transient_loaded_umag_history = np.array(solver.getTransientLoadedUmagHistory(), dtype=float)

    result_grid, deformed_grid = build_result_grid(
        mesh_bundle,
        displacement_matrix,
        stress_array,
        strain_array,
        state.solver.warp_factor,
    )

    export_file = resolve_runtime_path(state.solver.result_file)
    export_file.parent.mkdir(parents=True, exist_ok=True)
    solver.exportResults(str(export_file))

    export_vtk_file = resolve_runtime_path(state.solver.result_vtk_file)
    export_vtk_file.parent.mkdir(parents=True, exist_ok=True)
    result_grid.save(export_vtk_file)

    summary = ResultSummary(
        max_displacement=float(np.linalg.norm(displacement_matrix, axis=1).max(initial=0.0)),
        min_z_displacement=float(displacement_matrix[:, 2].min(initial=0.0)),
        max_von_mises=float(stress_array.max(initial=0.0)),
        max_equivalent_strain=float(strain_array.max(initial=0.0)),
        mean_von_mises=float(stress_array.mean() if stress_array.size else 0.0),
        mean_equivalent_strain=float(strain_array.mean() if strain_array.size else 0.0),
        fixed_node_count=len(fixed_nodes),
        loaded_node_count=len(loaded_nodes),
        solve_time_seconds=0.0,
        converged=True,
        iteration_count=max(int(transient_times.size) - 1, 0),
        residual_norm=0.0,
        transient_step_count=max(int(transient_times.size) - 1, 0),
        transient_total_time=float(transient_times[-1] if transient_times.size else 0.0),
        export_file=str(export_file),
        export_vtk_file=str(export_vtk_file),
    )

    return AnalysisArtifacts(
        solver=solver,
        displacement_matrix=displacement_matrix,
        stress_array=stress_array,
        strain_array=strain_array,
        temperature_array=None,
        heat_flux_array=None,
        result_grid=result_grid,
        deformed_grid=deformed_grid,
        summary=summary,
        transient_times=transient_times,
        transient_max_displacements=transient_max_displacements,
        transient_loaded_ux_history=transient_loaded_ux_history,
        transient_loaded_uy_history=transient_loaded_uy_history,
        transient_loaded_uz_history=transient_loaded_uz_history,
        transient_loaded_umag_history=transient_loaded_umag_history,
    )


def _run_frequency_response_analysis(
    solver: fem_core.FEMSolver,
    mesh_bundle: MeshBundle,
    state: ProjectState,
    fixed_nodes: list[int],
    loaded_nodes: list[int],
) -> AnalysisArtifacts:
    """执行线性频响分析，并返回峰值响应工况。"""

    boundary_condition = state.boundary_condition
    constrained_dofs = (
        (0, boundary_condition.constrain_x, boundary_condition.displacement_x),
        (1, boundary_condition.constrain_y, boundary_condition.displacement_y),
        (2, boundary_condition.constrain_z, boundary_condition.displacement_z),
    )
    if not any(enabled for _dof, enabled, _value in constrained_dofs):
        raise RuntimeError("频响分析至少需要一个位移约束方向。")

    for node_id in fixed_nodes:
        for dof, enabled, value in constrained_dofs:
            if enabled:
                solver.addBoundaryCondition(node_id, dof, float(value))

    force_components = np.array([state.load_case.force_x, state.load_case.force_y, state.load_case.force_z], dtype=float)
    nodal_force = force_components / len(loaded_nodes)
    for node_id in loaded_nodes:
        solver.addLoad(node_id, float(nodal_force[0]), float(nodal_force[1]), float(nodal_force[2]))

    solver.runFrequencyResponseAnalysis(
        state.solver.frequency_start_hz,
        state.solver.frequency_end_hz,
        state.solver.frequency_point_count,
        *resolve_dynamic_rayleigh_coefficients(state),
    )

    displacement_matrix = np.array(solver.getDisplacements(), dtype=float).reshape(-1, 3)
    stress_array = np.array(solver.getStresses(), dtype=float)
    strain_array = np.array(solver.getStrains(), dtype=float)
    frequencies = np.array(solver.getFrequencyResponseFrequenciesHz(), dtype=float)
    amplitudes = np.array(solver.getFrequencyResponseMaxDisplacements(), dtype=float)
    loaded_ux = np.array(solver.getFrequencyResponseLoadedUxHistory(), dtype=float)
    loaded_uy = np.array(solver.getFrequencyResponseLoadedUyHistory(), dtype=float)
    loaded_uz = np.array(solver.getFrequencyResponseLoadedUzHistory(), dtype=float)
    loaded_umag = np.array(solver.getFrequencyResponseLoadedUmagHistory(), dtype=float)

    result_grid, deformed_grid = build_result_grid(
        mesh_bundle,
        displacement_matrix,
        stress_array,
        strain_array,
        state.solver.warp_factor,
    )

    export_file = resolve_runtime_path(state.solver.result_file)
    export_file.parent.mkdir(parents=True, exist_ok=True)
    solver.exportResults(str(export_file))

    export_vtk_file = resolve_runtime_path(state.solver.result_vtk_file)
    export_vtk_file.parent.mkdir(parents=True, exist_ok=True)
    result_grid.save(export_vtk_file)

    peak_frequency = 0.0
    if frequencies.size and amplitudes.size:
        peak_index = int(np.argmax(amplitudes))
        peak_frequency = float(frequencies[peak_index])

    summary = ResultSummary(
        max_displacement=float(np.linalg.norm(displacement_matrix, axis=1).max(initial=0.0)),
        min_z_displacement=float(displacement_matrix[:, 2].min(initial=0.0)),
        max_von_mises=float(stress_array.max(initial=0.0)),
        max_equivalent_strain=float(strain_array.max(initial=0.0)),
        mean_von_mises=float(stress_array.mean() if stress_array.size else 0.0),
        mean_equivalent_strain=float(strain_array.mean() if strain_array.size else 0.0),
        fixed_node_count=len(fixed_nodes),
        loaded_node_count=len(loaded_nodes),
        solve_time_seconds=0.0,
        converged=True,
        iteration_count=int(frequencies.size),
        residual_norm=0.0,
        frequency_response_count=int(frequencies.size),
        peak_response_frequency_hz=peak_frequency,
        export_file=str(export_file),
        export_vtk_file=str(export_vtk_file),
    )

    return AnalysisArtifacts(
        solver=solver,
        displacement_matrix=displacement_matrix,
        stress_array=stress_array,
        strain_array=strain_array,
        temperature_array=None,
        heat_flux_array=None,
        result_grid=result_grid,
        deformed_grid=deformed_grid,
        summary=summary,
        frequency_response_frequencies_hz=frequencies,
        frequency_response_max_displacements=amplitudes,
        frequency_response_loaded_ux_history=loaded_ux,
        frequency_response_loaded_uy_history=loaded_uy,
        frequency_response_loaded_uz_history=loaded_uz,
        frequency_response_loaded_umag_history=loaded_umag,
    )


def _run_steady_state_thermal_analysis(
    solver: fem_core.FEMSolver,
    mesh_bundle: MeshBundle,
    state: ProjectState,
) -> AnalysisArtifacts:
    """执行稳态热传导分析。"""

    thermal_boundary = state.thermal_boundary
    thermal_load = state.thermal_load
    boundary_nodes = _resolve_target_nodes(mesh_bundle, thermal_boundary.target_face, state.load_case.boundary_tolerance_ratio)
    load_nodes: list[int] = []

    if not boundary_nodes:
        raise RuntimeError("未识别到温度边界对应的节点，请检查热边界表面选择。")
    if thermal_load.is_applied and thermal_load.heat_power != 0.0:
        if not thermal_load.target_face:
            raise RuntimeError("当前热流载荷已经启用，但还没有选择热流作用表面。")
        load_nodes = _resolve_target_nodes(mesh_bundle, thermal_load.target_face, state.load_case.boundary_tolerance_ratio)
        if not load_nodes:
            raise RuntimeError("未识别到热流边界对应的节点，请检查热载荷表面选择。")

    for node_id in boundary_nodes:
        solver.addThermalBoundaryCondition(node_id, float(thermal_boundary.temperature))

    if thermal_load.is_applied and load_nodes:
        nodal_heat = float(thermal_load.heat_power) / float(len(load_nodes))
        for node_id in load_nodes:
            solver.addThermalLoad(node_id, nodal_heat)

    solver.runSteadyStateThermalAnalysis()

    temperature_array = np.array(solver.getTemperatures(), dtype=float)
    heat_flux_array = np.array(solver.getHeatFluxMagnitudes(), dtype=float)
    zero_displacement = np.zeros((mesh_bundle.points.shape[0], 3), dtype=float)
    zero_stress = np.zeros(mesh_bundle.display_grid.n_cells if mesh_bundle.solver_cell_type == "hexa" else mesh_bundle.grid.n_cells, dtype=float)
    zero_strain = np.zeros_like(zero_stress)

    result_grid, deformed_grid = build_result_grid(
        mesh_bundle,
        zero_displacement,
        zero_stress,
        zero_strain,
        0.0,
        temperature_array=temperature_array,
        heat_flux_array=heat_flux_array,
    )

    export_file = resolve_runtime_path(state.solver.result_file)
    export_file.parent.mkdir(parents=True, exist_ok=True)
    with open(export_file, "w", encoding="utf-8", newline="") as handle:
        handle.write("NodeID,X,Y,Z,Temperature\n")
        for node_id, point in enumerate(mesh_bundle.points):
            handle.write(
                f"{node_id},{point[0]},{point[1]},{point[2]},{temperature_array[node_id]}\n"
            )
        handle.write("\nElementID,HeatFluxMagnitude\n")
        for element_id, value in enumerate(heat_flux_array):
            handle.write(f"{element_id},{value}\n")

    export_vtk_file = resolve_runtime_path(state.solver.result_vtk_file)
    export_vtk_file.parent.mkdir(parents=True, exist_ok=True)
    result_grid.save(export_vtk_file)

    summary = ResultSummary(
        max_temperature=float(temperature_array.max(initial=0.0)),
        min_temperature=float(temperature_array.min(initial=0.0)),
        mean_temperature=float(temperature_array.mean() if temperature_array.size else 0.0),
        max_heat_flux=float(heat_flux_array.max(initial=0.0)),
        fixed_node_count=len(boundary_nodes),
        loaded_node_count=len(load_nodes),
        solve_time_seconds=0.0,
        converged=True,
        iteration_count=1,
        residual_norm=0.0,
        export_file=str(export_file),
        export_vtk_file=str(export_vtk_file),
    )

    return AnalysisArtifacts(
        solver=solver,
        displacement_matrix=zero_displacement,
        stress_array=zero_stress,
        strain_array=zero_strain,
        temperature_array=temperature_array,
        heat_flux_array=heat_flux_array,
        result_grid=result_grid,
        deformed_grid=deformed_grid,
        summary=summary,
    )


def run_linear_static_analysis(mesh_bundle: MeshBundle, state: ProjectState) -> AnalysisArtifacts:
    """
    执行一次完整的有限元分析。

    名称保留为 `run_linear_static_analysis`，是为了兼容现有调用路径。
    但内部已经会根据 `analysis_type` 自动切换到不同分析入口。
    """

    start_time = time.perf_counter()

    solver = fem_core.FEMSolver()
    state.ensure_default_entities()
    state.solver.linear_solver = normalize_solver_for_analysis(
        state.solver.analysis_type,
        state.solver.linear_solver,
    )
    load_case = state.load_case
    boundary_condition = state.boundary_condition

    _configure_parallel(solver, state)
    _configure_linear_solver(solver, state)

    for material_item in state.material_library:
        solver.addMaterial(
            fem_core.Material(
                material_item.material_id,
                material_item.young_modulus,
                material_item.poisson_ratio,
                material_item.density,
                material_item.thermal_conductivity,
                material_item.yield_strength,
                material_item.hardening_modulus,
                material_item.nonlinear_enabled,
            )
        )

    for node_id, point in enumerate(mesh_bundle.points):
        solver.addNode(node_id, float(point[0]), float(point[1]), float(point[2]))

    if mesh_bundle.solver_cell_type == "hexa":
        cell_matrix = mesh_bundle.solver_cells.reshape(-1, 9)
        for element_id, cell in enumerate(cell_matrix):
            node_ids = [int(cell[index]) for index in range(1, 9)]
            material_id = _select_material_id_for_element(mesh_bundle.points, node_ids, state)
            solver.addElement(
                fem_core.create_hex8_element(
                    id=element_id,
                    node_ids=node_ids,
                    material_id=material_id,
                )
            )
    else:
        cell_matrix = mesh_bundle.solver_cells.reshape(-1, 5)
        for element_id, cell in enumerate(cell_matrix):
            node_ids = [int(cell[1]), int(cell[2]), int(cell[3]), int(cell[4])]
            material_id = _select_material_id_for_element(mesh_bundle.points, node_ids, state)
            solver.addElement(
                fem_core.create_tet4_element(
                    id=element_id,
                    node_ids=node_ids,
                    material_id=material_id,
                )
            )

    if state.solver.analysis_type == "steady_state_thermal":
        artifacts = _run_steady_state_thermal_analysis(solver, mesh_bundle, state)
        artifacts.summary.solve_time_seconds = time.perf_counter() - start_time
        return artifacts

    fixed_nodes = _resolve_target_nodes(mesh_bundle, boundary_condition.target_face, load_case.boundary_tolerance_ratio)
    loaded_nodes: list[int] = []
    if state.solver.analysis_type != "modal_analysis":
        loaded_nodes = _resolve_target_nodes(mesh_bundle, load_case.loaded_face, load_case.boundary_tolerance_ratio)

    if not fixed_nodes:
        raise RuntimeError("未识别到固定端节点，请检查固定端面选择和容差。")
    if state.solver.analysis_type != "modal_analysis" and not loaded_nodes:
        raise RuntimeError("未识别到受力端节点，请检查受力端面选择和容差。")

    if state.solver.analysis_type == "modal_analysis":
        artifacts = _run_modal_analysis(solver, mesh_bundle, state, fixed_nodes)
        artifacts.summary.solve_time_seconds = time.perf_counter() - start_time
        return artifacts
    if state.solver.analysis_type == "transient_dynamic":
        artifacts = _run_transient_analysis(solver, mesh_bundle, state, fixed_nodes, loaded_nodes)
        artifacts.summary.solve_time_seconds = time.perf_counter() - start_time
        return artifacts
    if state.solver.analysis_type == "frequency_response":
        artifacts = _run_frequency_response_analysis(solver, mesh_bundle, state, fixed_nodes, loaded_nodes)
        artifacts.summary.solve_time_seconds = time.perf_counter() - start_time
        return artifacts

    constrained_dofs = (
        (0, boundary_condition.constrain_x, boundary_condition.displacement_x),
        (1, boundary_condition.constrain_y, boundary_condition.displacement_y),
        (2, boundary_condition.constrain_z, boundary_condition.displacement_z),
    )
    if not any(enabled for _dof, enabled, _value in constrained_dofs):
        raise RuntimeError("当前边界条件没有勾选任何约束自由度，请至少约束一个方向。")

    for node_id in fixed_nodes:
        for dof, enabled, value in constrained_dofs:
            if enabled:
                solver.addBoundaryCondition(node_id, dof, float(value))

    force_components = np.array([load_case.force_x, load_case.force_y, load_case.force_z], dtype=float)
    nodal_force = force_components / len(loaded_nodes)
    for node_id in loaded_nodes:
        solver.addLoad(node_id, float(nodal_force[0]), float(nodal_force[1]), float(nodal_force[2]))

    if state.solver.analysis_type == "nonlinear_static":
        solver.runNonlinearStaticAnalysis(
            state.solver.load_steps,
            state.solver.max_iterations,
            state.solver.tolerance,
        )
    else:
        solver.runLinearStaticAnalysis()

    displacement_matrix = np.array(solver.getDisplacements(), dtype=float).reshape(-1, 3)
    stress_array = np.array(solver.getStresses(), dtype=float)
    strain_array = np.array(solver.getStrains(), dtype=float)

    result_grid, deformed_grid = build_result_grid(
        mesh_bundle,
        displacement_matrix,
        stress_array,
        strain_array,
        state.solver.warp_factor,
    )

    export_file = resolve_runtime_path(state.solver.result_file)
    export_file.parent.mkdir(parents=True, exist_ok=True)
    solver.exportResults(str(export_file))

    export_vtk_file = resolve_runtime_path(state.solver.result_vtk_file)
    export_vtk_file.parent.mkdir(parents=True, exist_ok=True)
    result_grid.save(export_vtk_file)

    elapsed = time.perf_counter() - start_time
    summary = ResultSummary(
        max_displacement=float(np.linalg.norm(displacement_matrix, axis=1).max(initial=0.0)),
        min_z_displacement=float(displacement_matrix[:, 2].min(initial=0.0)),
        max_von_mises=float(stress_array.max(initial=0.0)),
        max_equivalent_strain=float(strain_array.max(initial=0.0)),
        mean_von_mises=float(stress_array.mean() if stress_array.size else 0.0),
        mean_equivalent_strain=float(strain_array.mean() if strain_array.size else 0.0),
        fixed_node_count=len(fixed_nodes),
        loaded_node_count=len(loaded_nodes),
        solve_time_seconds=elapsed,
        converged=bool(solver.hasConverged()) if hasattr(solver, "hasConverged") else True,
        iteration_count=int(solver.getLastIterationCount()) if hasattr(solver, "getLastIterationCount") else 1,
        residual_norm=float(solver.getLastResidualNorm()) if hasattr(solver, "getLastResidualNorm") else 0.0,
        export_file=str(export_file),
        export_vtk_file=str(export_vtk_file),
    )

    return AnalysisArtifacts(
        solver=solver,
        displacement_matrix=displacement_matrix,
        stress_array=stress_array,
        strain_array=strain_array,
        temperature_array=None,
        heat_flux_array=None,
        result_grid=result_grid,
        deformed_grid=deformed_grid,
        summary=summary,
    )
