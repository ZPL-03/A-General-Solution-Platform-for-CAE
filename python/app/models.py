"""
项目状态与界面数据模型。

这一层负责把界面的输入整理成稳定的数据结构，方便：
1. 项目保存与加载；
2. 网格与求解服务复用同一份配置；
3. 逐步扩展多材料、多工况和非线性分析能力。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class GeometryConfig:
    """几何与网格前处理配置。"""

    mode: str = "box"
    primitive: str = "box"
    length: float = 1.0
    width: float = 0.25
    height: float = 0.25
    radius: float = 0.15
    hole_radius: float = 0.05
    mesh_size: float = 0.08
    cad_file: str = ""

    algorithm_2d: int = 6
    algorithm_3d: int = 1
    mesh_topology: str = "tetra"
    element_order: int = 1
    optimize_mesh: bool = True

    local_refine_enabled: bool = False
    local_refine_size: float = 0.04
    local_refine_radius: float = 0.20
    mesh_file: str = "data/models/latest_mesh.msh"


@dataclass
class MaterialConfig:
    """材料参数与区域指派配置。"""

    material_id: int = 1
    name: str = "结构钢"
    young_modulus: float = 2.10e11
    poisson_ratio: float = 0.30
    density: float = 7850.0
    thermal_conductivity: float = 45.0

    # 非线性材料参数。
    nonlinear_enabled: bool = False
    yield_strength: float = 3.50e8
    hardening_modulus: float = 1.00e9

    # 当前多材料指派先采用“按 X 方向归一化区间分配”的方式。
    region_xmin_ratio: float = 0.0
    region_xmax_ratio: float = 1.0
    is_applied: bool = False


@dataclass
class LoadCaseConfig:
    """载荷工况。"""

    loadcase_id: int = 1
    name: str = "默认荷载"
    force_x: float = 0.0
    force_y: float = 0.0
    force_z: float = -10000.0
    loaded_face: str = ""
    boundary_tolerance_ratio: float = 0.01
    is_applied: bool = False


@dataclass
class BoundaryConditionConfig:
    """位移边界条件工况。"""

    boundary_id: int = 1
    name: str = "默认约束"
    target_face: str = ""
    constrain_x: bool = True
    constrain_y: bool = True
    constrain_z: bool = True
    displacement_x: float = 0.0
    displacement_y: float = 0.0
    displacement_z: float = 0.0
    is_applied: bool = False


@dataclass
class ThermalBoundaryConfig:
    """热边界条件。"""

    name: str = "默认温度边界"
    target_face: str = ""
    temperature: float = 20.0
    is_applied: bool = False


@dataclass
class ThermalLoadConfig:
    """热载荷工况。"""

    name: str = "默认热流"
    target_face: str = ""
    heat_power: float = 0.0
    is_applied: bool = False


def _default_num_threads() -> int:
    """返回当前机器建议使用的默认 CPU 逻辑线程数。"""

    return max(1, int(os.cpu_count() or 1))


@dataclass
class SolverConfig:
    """求解器与后处理相关配置。"""

    analysis_type: str = "linear_static"
    linear_solver: str = "sparse_lu"
    damping_mode: str = "none"
    warp_factor: float = 250.0
    result_file: str = "data/results/latest_results.csv"
    result_vtk_file: str = "data/results/latest_results.vtu"
    load_steps: int = 10
    max_iterations: int = 20
    tolerance: float = 1.0e-8
    modal_count: int = 6
    total_time: float = 0.020
    time_step: float = 0.001
    newmark_beta: float = 0.25
    newmark_gamma: float = 0.50
    rayleigh_alpha: float = 0.0
    rayleigh_beta: float = 0.0
    modal_damping_ratio: float = 0.02
    modal_damping_freq1_hz: float = 10.0
    modal_damping_freq2_hz: float = 100.0
    frequency_start_hz: float = 1.0
    frequency_end_hz: float = 500.0
    frequency_point_count: int = 50
    use_parallel: bool = True
    num_threads: int = field(default_factory=_default_num_threads)


@dataclass
class MeshSummary:
    """网格摘要信息。"""

    node_count: int = 0
    tetra_count: int = 0
    display_cell_count: int = 0
    display_cell_type: str = "tetra"
    xmin: float = 0.0
    xmax: float = 0.0
    ymin: float = 0.0
    ymax: float = 0.0
    zmin: float = 0.0
    zmax: float = 0.0
    quality_min: Optional[float] = None
    quality_avg: Optional[float] = None
    quality_max: Optional[float] = None
    quality_bad_count: int = 0
    quality_warning_count: int = 0


@dataclass
class ResultSummary:
    """分析结果摘要。"""

    max_displacement: float = 0.0
    min_z_displacement: float = 0.0
    max_von_mises: float = 0.0
    max_equivalent_strain: float = 0.0
    mean_von_mises: float = 0.0
    mean_equivalent_strain: float = 0.0
    max_temperature: float = 0.0
    min_temperature: float = 0.0
    mean_temperature: float = 0.0
    max_heat_flux: float = 0.0
    fixed_node_count: int = 0
    loaded_node_count: int = 0
    solve_time_seconds: float = 0.0
    converged: bool = False
    iteration_count: int = 0
    residual_norm: float = 0.0
    modal_frequencies_hz: list[float] = field(default_factory=list)
    transient_step_count: int = 0
    transient_total_time: float = 0.0
    frequency_response_count: int = 0
    peak_response_frequency_hz: float = 0.0
    export_file: str = ""
    export_vtk_file: str = ""


def _default_material_library() -> list[MaterialConfig]:
    return [MaterialConfig()]


def _default_load_cases() -> list[LoadCaseConfig]:
    return [LoadCaseConfig()]


def _default_boundary_conditions() -> list[BoundaryConditionConfig]:
    return [BoundaryConditionConfig()]


@dataclass
class ProjectState:
    """项目级状态对象。"""

    project_name: str = "新建CAE分析项目"
    project_notes: str = "这是一个通用 CAE 分析项目，可用于任意几何的前处理、求解与后处理。"
    geometry: GeometryConfig = field(default_factory=GeometryConfig)
    material: MaterialConfig = field(default_factory=MaterialConfig)
    load_case: LoadCaseConfig = field(default_factory=LoadCaseConfig)
    boundary_condition: BoundaryConditionConfig = field(default_factory=BoundaryConditionConfig)
    thermal_boundary: ThermalBoundaryConfig = field(default_factory=ThermalBoundaryConfig)
    thermal_load: ThermalLoadConfig = field(default_factory=ThermalLoadConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)
    mesh_summary: MeshSummary = field(default_factory=MeshSummary)
    result_summary: ResultSummary = field(default_factory=ResultSummary)
    material_library: list[MaterialConfig] = field(default_factory=_default_material_library)
    load_cases: list[LoadCaseConfig] = field(default_factory=_default_load_cases)
    boundary_conditions: list[BoundaryConditionConfig] = field(default_factory=_default_boundary_conditions)
    active_material_id: int = 1
    active_loadcase_id: int = 1
    active_boundary_id: int = 1

    def __post_init__(self) -> None:
        self.ensure_default_entities()

    def ensure_default_entities(self) -> None:
        """确保材料库和工况库至少各有一项，并同步当前激活对象。"""

        if not self.material_library:
            self.material_library = [self.material]
        if not self.load_cases:
            self.load_cases = [self.load_case]
        if not self.boundary_conditions:
            self.boundary_conditions = [self.boundary_condition]

        active_material = self.get_material_by_id(self.active_material_id)
        if active_material is None:
            active_material = self.material_library[0]
            self.active_material_id = active_material.material_id
        self.material = active_material

        active_loadcase = self.get_loadcase_by_id(self.active_loadcase_id)
        if active_loadcase is None:
            active_loadcase = self.load_cases[0]
            self.active_loadcase_id = active_loadcase.loadcase_id
        self.load_case = active_loadcase

        active_boundary = self.get_boundary_by_id(self.active_boundary_id)
        if active_boundary is None:
            active_boundary = self.boundary_conditions[0]
            self.active_boundary_id = active_boundary.boundary_id
        self.boundary_condition = active_boundary

    def get_material_by_id(self, material_id: int) -> Optional[MaterialConfig]:
        for material in self.material_library:
            if material.material_id == material_id:
                return material
        return None

    def get_loadcase_by_id(self, loadcase_id: int) -> Optional[LoadCaseConfig]:
        for load_case in self.load_cases:
            if load_case.loadcase_id == loadcase_id:
                return load_case
        return None

    def set_active_material(self, material_id: int) -> None:
        material = self.get_material_by_id(material_id)
        if material is not None:
            self.active_material_id = material_id
            self.material = material

    def set_active_loadcase(self, loadcase_id: int) -> None:
        load_case = self.get_loadcase_by_id(loadcase_id)
        if load_case is not None:
            self.active_loadcase_id = loadcase_id
            self.load_case = load_case

    def get_boundary_by_id(self, boundary_id: int) -> Optional[BoundaryConditionConfig]:
        for boundary in self.boundary_conditions:
            if boundary.boundary_id == boundary_id:
                return boundary
        return None

    def set_active_boundary(self, boundary_id: int) -> None:
        boundary = self.get_boundary_by_id(boundary_id)
        if boundary is not None:
            self.active_boundary_id = boundary_id
            self.boundary_condition = boundary

    def to_dict(self) -> Dict[str, Any]:
        """转换成可直接写入 JSON 的字典。"""

        self.ensure_default_entities()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectState":
        """根据 JSON 读取出的字典重建项目状态。"""

        material_library = [MaterialConfig(**item) for item in data.get("material_library", [])]
        load_cases = [LoadCaseConfig(**item) for item in data.get("load_cases", [])]
        boundary_conditions = [BoundaryConditionConfig(**item) for item in data.get("boundary_conditions", [])]

        state = cls(
            project_name=data.get("project_name", "新建CAE分析项目"),
            project_notes=data.get("project_notes", "这是一个通用 CAE 分析项目，可用于任意几何的前处理、求解与后处理。"),
            geometry=GeometryConfig(**data.get("geometry", {})),
            material=MaterialConfig(**data.get("material", {})),
            load_case=LoadCaseConfig(**data.get("load_case", {})),
            boundary_condition=BoundaryConditionConfig(**data.get("boundary_condition", {})),
            thermal_boundary=ThermalBoundaryConfig(**data.get("thermal_boundary", {})),
            thermal_load=ThermalLoadConfig(**data.get("thermal_load", {})),
            solver=SolverConfig(**data.get("solver", {})),
            mesh_summary=MeshSummary(**data.get("mesh_summary", {})),
            result_summary=ResultSummary(**data.get("result_summary", {})),
            material_library=material_library or _default_material_library(),
            load_cases=load_cases or _default_load_cases(),
            boundary_conditions=boundary_conditions or _default_boundary_conditions(),
            active_material_id=data.get("active_material_id", 1),
            active_loadcase_id=data.get("active_loadcase_id", 1),
            active_boundary_id=data.get("active_boundary_id", 1),
        )
        state.ensure_default_entities()
        return state
