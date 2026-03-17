"""
Gmsh 与前后处理服务集成测试。

测试目标：
1. 验证参数化几何可以生成三维体网格；
2. 验证二阶网格、局部加密、网格导出都能走通；
3. 验证非线性静力入口可以返回有效结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from app.models import ProjectState
from app.services.mesh_service import compute_mesh_quality, export_mesh_to_vtk, generate_volume_mesh
from app.services.solver_service import describe_dynamic_damping, get_solver_options_for_analysis, normalize_solver_for_analysis, resolve_dynamic_rayleigh_coefficients, run_linear_static_analysis


def _make_state() -> ProjectState:
    """构造一个通用测试项目状态。"""

    state = ProjectState()
    state.geometry.primitive = "box"
    state.geometry.mode = "box"
    state.geometry.length = 1.0
    state.geometry.width = 0.2
    state.geometry.height = 0.2
    state.geometry.mesh_size = 0.18
    state.geometry.mesh_file = str(PROJECT_ROOT / "data" / "models" / "test_mesh.msh")
    state.load_case.loaded_face = "xmax"
    state.boundary_condition.target_face = "xmin"
    state.solver.result_file = str(PROJECT_ROOT / "data" / "results" / "test_results.csv")
    state.solver.result_vtk_file = str(PROJECT_ROOT / "data" / "results" / "test_results.vtu")
    return state


def test_second_order_mesh_and_export() -> None:
    """验证二阶网格选项、质量检测和 VTU 导出。"""

    state = _make_state()
    state.geometry.element_order = 2
    mesh_bundle = generate_volume_mesh(state)
    quality_bundle = compute_mesh_quality(mesh_bundle)

    assert mesh_bundle.summary.node_count > 0
    assert mesh_bundle.summary.tetra_count > 0
    assert len(mesh_bundle.surface_patches) > 0
    assert quality_bundle.summary.quality_min is not None

    export_file = PROJECT_ROOT / "data" / "models" / "test_mesh_order2.vtu"
    export_mesh_to_vtk(mesh_bundle, str(export_file))
    assert export_file.exists()


def test_local_refinement_changes_mesh_density() -> None:
    """验证局部加密会让网格规模发生变化。"""

    coarse_state = _make_state()
    refined_state = _make_state()
    refined_state.geometry.local_refine_enabled = True
    refined_state.geometry.local_refine_size = 0.05
    refined_state.geometry.local_refine_radius = 0.25

    coarse_mesh = generate_volume_mesh(coarse_state)
    refined_mesh = generate_volume_mesh(refined_state)

    assert refined_mesh.summary.node_count > coarse_mesh.summary.node_count
    assert refined_mesh.summary.tetra_count > coarse_mesh.summary.tetra_count


def test_nonlinear_static_service_entry() -> None:
    """验证非线性静力入口能完成一次有效分析。"""

    state = _make_state()
    state.geometry.mesh_size = 0.2
    state.solver.analysis_type = "nonlinear_static"
    state.solver.linear_solver = "sparse_lu"
    state.solver.load_steps = 4
    state.solver.max_iterations = 12
    state.solver.tolerance = 1.0e-8

    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    assert artifacts.summary.converged
    assert artifacts.summary.iteration_count >= 1
    assert artifacts.summary.max_displacement > 0.0
    assert Path(artifacts.summary.export_file).exists()
    assert Path(artifacts.summary.export_vtk_file).exists()


def test_active_material_assignment() -> None:
    """验证当前激活材料会赋予整个几何体并参与求解。"""

    state = _make_state()
    from app.models import MaterialConfig

    state.material_library.append(
        MaterialConfig(
            material_id=2,
            name="高刚度钢",
            young_modulus=2.80e11,
            poisson_ratio=0.29,
            density=7900.0,
            nonlinear_enabled=True,
            yield_strength=4.5e8,
            hardening_modulus=2.0e9,
        )
    )
    state.set_active_material(2)

    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    assert artifacts.summary.max_displacement > 0.0
    assert artifacts.stress_array.size == mesh_bundle.summary.tetra_count


def test_hex_mesh_generation_for_box() -> None:
    """验证参数化长方体可以生成 Hex8 显示网格，并进入六面体求解链路。"""

    state = _make_state()
    state.geometry.mesh_topology = "hexa"
    state.geometry.mesh_size = 0.25
    state.material.is_applied = True
    state.load_case.is_applied = True
    state.boundary_condition.is_applied = True

    mesh_bundle = generate_volume_mesh(state)

    assert mesh_bundle.generated_cell_type == "hexa"
    assert mesh_bundle.summary.display_cell_type == "hexa"
    assert mesh_bundle.solver_cell_type == "hexa"
    assert mesh_bundle.summary.display_cell_count > 0
    assert mesh_bundle.summary.tetra_count > 0

    artifacts = run_linear_static_analysis(mesh_bundle, state)
    assert artifacts.summary.max_displacement > 0.0
    assert artifacts.stress_array.size == mesh_bundle.summary.tetra_count


def test_result_grid_supports_contours() -> None:
    """验证结果网格可以转换为点标量并提取等值面。"""

    state = _make_state()
    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    contour_source = artifacts.result_grid.cell_data_to_point_data(pass_cell_data=True)
    contours = contour_source.contour(isosurfaces=6, scalars="VonMisesStress")

    assert contours.n_points > 0


def test_force_components_work_in_x_and_y_directions() -> None:
    """验证荷载在 X/Y 方向施加后也能产生对应方向位移。"""

    state = _make_state()
    state.load_case.force_x = 5000.0
    state.load_case.force_y = -3000.0
    state.load_case.force_z = 0.0
    state.material.is_applied = True
    state.load_case.is_applied = True
    state.boundary_condition.is_applied = True

    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    assert float(np.max(np.abs(artifacts.displacement_matrix[:, 0]))) > 0.0
    assert float(np.max(np.abs(artifacts.displacement_matrix[:, 1]))) > 0.0


def test_modal_analysis_service_entry() -> None:
    """验证服务层模态分析入口可返回频率与一阶振型。"""

    state = _make_state()
    state.solver.analysis_type = "modal_analysis"
    state.solver.linear_solver = "bicgstab"
    state.solver.modal_count = 5
    state.material.is_applied = True
    state.boundary_condition.is_applied = True

    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    assert artifacts.summary.converged
    assert state.solver.linear_solver == "modal_eigensolver"
    assert len(artifacts.summary.modal_frequencies_hz) >= 1
    assert artifacts.summary.modal_frequencies_hz[0] > 0.0
    assert float(np.max(np.abs(artifacts.displacement_matrix))) > 0.0


def test_transient_dynamic_service_entry() -> None:
    """验证服务层瞬态动力学入口可返回时间历程。"""

    state = _make_state()
    state.solver.analysis_type = "transient_dynamic"
    state.solver.total_time = 0.01
    state.solver.time_step = 0.001
    state.solver.newmark_beta = 0.25
    state.solver.newmark_gamma = 0.50
    state.material.is_applied = True
    state.load_case.is_applied = True
    state.boundary_condition.is_applied = True

    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    assert artifacts.summary.converged
    assert artifacts.summary.transient_step_count > 0
    assert artifacts.transient_times is not None
    assert artifacts.transient_max_displacements is not None
    assert float(np.max(artifacts.transient_max_displacements)) > 0.0


def test_frequency_response_service_entry() -> None:
    """验证服务层频响分析入口可返回幅频曲线。"""

    state = _make_state()
    state.solver.analysis_type = "frequency_response"
    state.solver.linear_solver = "conjugate_gradient"
    state.solver.frequency_start_hz = 1.0
    state.solver.frequency_end_hz = 120.0
    state.solver.frequency_point_count = 15
    state.material.is_applied = True
    state.load_case.is_applied = True
    state.boundary_condition.is_applied = True

    mesh_bundle = generate_volume_mesh(state)
    artifacts = run_linear_static_analysis(mesh_bundle, state)

    assert artifacts.summary.converged
    assert state.solver.linear_solver == "harmonic_direct"
    assert artifacts.summary.frequency_response_count == 15
    assert artifacts.frequency_response_frequencies_hz is not None
    assert artifacts.frequency_response_max_displacements is not None
    assert float(np.max(artifacts.frequency_response_max_displacements)) > 0.0


def test_solver_option_matching_helpers() -> None:
    """验证分析步与求解器选项映射保持一致。"""

    assert normalize_solver_for_analysis("linear_static", "bicgstab") == "bicgstab"
    assert normalize_solver_for_analysis("steady_state_thermal", "bicgstab") == "bicgstab"
    assert normalize_solver_for_analysis("modal_analysis", "bicgstab") == "modal_eigensolver"
    assert normalize_solver_for_analysis("frequency_response", "sparse_lu") == "harmonic_direct"
    assert [value for _label, value in get_solver_options_for_analysis("transient_dynamic")] == [
        "sparse_lu",
        "conjugate_gradient",
        "bicgstab",
    ]
    assert [value for _label, value in get_solver_options_for_analysis("steady_state_thermal")] == [
        "sparse_lu",
        "conjugate_gradient",
        "bicgstab",
    ]


def test_modal_ratio_damping_conversion() -> None:
    """验证按阻尼比换算 Rayleigh 系数的辅助函数。"""

    state = _make_state()
    state.solver.damping_mode = "modal_ratio"
    state.solver.modal_damping_ratio = 0.03
    state.solver.modal_damping_freq1_hz = 20.0
    state.solver.modal_damping_freq2_hz = 120.0

    alpha, beta = resolve_dynamic_rayleigh_coefficients(state)

    assert alpha > 0.0
    assert beta > 0.0
    assert "ζ=0.0300" in describe_dynamic_damping(state)
