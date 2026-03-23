"""
几何建模与网格划分服务。

本模块负责：
1. 生成参数化几何或导入外部 CAD；
2. 调用 Gmsh 生成三维网格；
3. 把网格转换成 PyVista 数据结构，供界面显示和后处理使用；
4. 导出 msh / vtu，并计算网格质量摘要。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gmsh
import numpy as np
import pyvista as pv

from app.models import MeshSummary, ProjectState
from app.runtime_paths import bootstrap_runtime_environment, resolve_runtime_path

bootstrap_runtime_environment()


@dataclass
class PreviewSceneData:
    """几何预览结果。"""

    surface: pv.PolyData
    feature_edges: pv.PolyData
    surface_patches: dict[str, pv.PolyData]
    surface_labels: dict[str, str]
    point_count: int
    face_count: int
    source_label: str


@dataclass
class MeshBundle:
    """三维体网格数据包。"""

    points: np.ndarray
    cells: np.ndarray
    grid: pv.UnstructuredGrid
    display_grid: pv.UnstructuredGrid
    solver_cells: np.ndarray
    solver_cell_type: str
    surface_patches: dict[str, pv.PolyData]
    surface_node_sets: dict[str, list[int]]
    surface_labels: dict[str, str]
    summary: MeshSummary
    mesh_file: str
    generated_cell_type: str


@dataclass
class MeshQualityBundle:
    """网格质量分析结果。"""

    quality_grid: pv.UnstructuredGrid
    summary: MeshSummary


def _ensure_parent_dir(file_path: str) -> None:
    """确保导出文件的父目录存在。"""

    Path(file_path).parent.mkdir(parents=True, exist_ok=True)


def _estimate_parametric_bounds(state: ProjectState) -> tuple[float, float, float, float, float, float]:
    """估算参数化几何的包围盒，用于局部加密区域设置。"""

    geometry = state.geometry
    primitive = geometry.primitive

    if primitive == "box":
        return 0.0, geometry.length, 0.0, geometry.width, 0.0, geometry.height

    if primitive == "cylinder":
        diameter = geometry.radius * 2.0
        return 0.0, geometry.length, 0.0, diameter, 0.0, diameter

    if primitive == "sphere":
        diameter = geometry.radius * 2.0
        return 0.0, diameter, 0.0, diameter, 0.0, diameter

    if primitive == "plate_with_hole":
        return 0.0, geometry.length, 0.0, geometry.width, 0.0, geometry.height

    raise RuntimeError(f"不支持的参数化几何类型：{primitive}")


def _build_geometry(state: ProjectState) -> str:
    """根据项目状态构建 Gmsh 几何模型。"""

    geometry = state.geometry

    if geometry.mode == "cad" and geometry.cad_file:
        cad_path = Path(geometry.cad_file)
        if not cad_path.exists():
            raise FileNotFoundError(f"未找到 CAD 文件：{cad_path}")

        gmsh.model.occ.importShapes(str(cad_path))
        gmsh.model.occ.synchronize()
        return f"外部 CAD: {cad_path.name}"

    primitive = geometry.primitive

    if primitive == "box":
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, geometry.length, geometry.width, geometry.height)

    elif primitive == "cylinder":
        gmsh.model.occ.addCylinder(
            0.0,
            geometry.radius,
            geometry.radius,
            geometry.length,
            0.0,
            0.0,
            geometry.radius,
        )

    elif primitive == "sphere":
        gmsh.model.occ.addSphere(geometry.radius, geometry.radius, geometry.radius, geometry.radius)

    elif primitive == "plate_with_hole":
        plate = gmsh.model.occ.addBox(0.0, 0.0, 0.0, geometry.length, geometry.width, geometry.height)
        hole = gmsh.model.occ.addCylinder(
            geometry.length * 0.5,
            geometry.width * 0.5,
            0.0,
            0.0,
            0.0,
            geometry.height,
            geometry.hole_radius,
        )
        gmsh.model.occ.cut([(3, plate)], [(3, hole)], removeObject=True, removeTool=True)

    else:
        raise RuntimeError(f"不支持的参数化几何类型：{primitive}")

    gmsh.model.occ.synchronize()

    labels = {
        "box": "参数化长方体",
        "cylinder": "参数化圆柱体",
        "sphere": "参数化球体",
        "plate_with_hole": "参数化带孔板",
    }
    return labels[primitive]


def _apply_mesh_options(state: ProjectState) -> None:
    """把项目中的网格参数写入 Gmsh 选项。"""

    geometry = state.geometry
    min_size = geometry.mesh_size
    if geometry.local_refine_enabled:
        min_size = min(geometry.mesh_size, geometry.local_refine_size)

    # `MeshSizeMin/Max` 是全局尺寸边界。
    # 如果这里把最小值也固定成全局尺寸，后面的局部加密背景场就无法继续压小单元。
    gmsh.option.setNumber("Mesh.MeshSizeMin", min_size)
    gmsh.option.setNumber("Mesh.MeshSizeMax", geometry.mesh_size)
    gmsh.option.setNumber("Mesh.Algorithm", geometry.algorithm_2d)
    gmsh.option.setNumber("Mesh.Algorithm3D", geometry.algorithm_3d)
    gmsh.option.setNumber("Mesh.ElementOrder", geometry.element_order)
    gmsh.option.setNumber("Mesh.Optimize", 1 if geometry.optimize_mesh else 0)
    gmsh.option.setNumber("Mesh.RecombineAll", 1 if geometry.mesh_topology == "hexa" else 0)


def _estimate_transfinite_divisions(length: float, target_size: float) -> int:
    """根据目标网格尺寸估算结构化六面体网格分段数。"""

    return max(2, int(round(max(length, target_size) / max(target_size, 1.0e-6))) + 1)


def _apply_hexahedral_controls(state: ProjectState) -> None:
    """
    对参数化长方体启用结构化六面体网格控制。

    说明：
    1. 当前版本先稳定支持长方体六面体网格；
    2. 其余几何暂时仍建议使用四面体网格；
    3. 六面体显示网格会在求解前自动拆分成 Tet4 进入当前求解内核。
    """

    geometry = state.geometry
    if geometry.mesh_topology != "hexa":
        return

    if geometry.mode == "cad" or geometry.primitive != "box":
        raise RuntimeError("当前版本六面体网格仅支持参数化长方体，请先切换到长方体参数化几何。")

    nx = _estimate_transfinite_divisions(geometry.length, geometry.mesh_size)
    ny = _estimate_transfinite_divisions(geometry.width, geometry.mesh_size)
    nz = _estimate_transfinite_divisions(geometry.height, geometry.mesh_size)

    for _, curve_tag in gmsh.model.getEntities(1):
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(1, curve_tag)
        dx = xmax - xmin
        dy = ymax - ymin
        dz = zmax - zmin
        if dx >= dy and dx >= dz:
            divisions = nx
        elif dy >= dx and dy >= dz:
            divisions = ny
        else:
            divisions = nz
        gmsh.model.mesh.setTransfiniteCurve(curve_tag, divisions)

    for _, surface_tag in gmsh.model.getEntities(2):
        gmsh.model.mesh.setTransfiniteSurface(surface_tag)
        gmsh.model.mesh.setRecombine(2, surface_tag)

    for _, volume_tag in gmsh.model.getEntities(3):
        gmsh.model.mesh.setTransfiniteVolume(volume_tag)
        gmsh.model.mesh.setRecombine(3, volume_tag)


def _apply_local_refinement_field(state: ProjectState) -> None:
    """在指定受载端附近叠加一个盒状局部加密区域。"""

    geometry = state.geometry
    if geometry.mode == "cad" or not geometry.local_refine_enabled or geometry.mesh_topology == "hexa":
        return

    xmin, xmax, ymin, ymax, zmin, zmax = _estimate_parametric_bounds(state)
    radius = geometry.local_refine_radius
    loaded_face = state.load_case.loaded_face

    region = {
        "xmin": (xmin, min(xmin + radius, xmax), ymin - radius, ymax + radius, zmin - radius, zmax + radius),
        "xmax": (max(xmax - radius, xmin), xmax, ymin - radius, ymax + radius, zmin - radius, zmax + radius),
        "ymin": (xmin - radius, xmax + radius, ymin, min(ymin + radius, ymax), zmin - radius, zmax + radius),
        "ymax": (xmin - radius, xmax + radius, max(ymax - radius, ymin), ymax, zmin - radius, zmax + radius),
        "zmin": (xmin - radius, xmax + radius, ymin - radius, ymax + radius, zmin, min(zmin + radius, zmax)),
        "zmax": (xmin - radius, xmax + radius, ymin - radius, ymax + radius, max(zmax - radius, zmin), zmax),
    }

    if loaded_face not in region:
        return

    x_min, x_max, y_min, y_max, z_min, z_max = region[loaded_face]
    field_id = gmsh.model.mesh.field.add("Box")
    gmsh.model.mesh.field.setNumber(field_id, "VIn", geometry.local_refine_size)
    gmsh.model.mesh.field.setNumber(field_id, "VOut", geometry.mesh_size)
    gmsh.model.mesh.field.setNumber(field_id, "XMin", x_min)
    gmsh.model.mesh.field.setNumber(field_id, "XMax", x_max)
    gmsh.model.mesh.field.setNumber(field_id, "YMin", y_min)
    gmsh.model.mesh.field.setNumber(field_id, "YMax", y_max)
    gmsh.model.mesh.field.setNumber(field_id, "ZMin", z_min)
    gmsh.model.mesh.field.setNumber(field_id, "ZMax", z_max)
    gmsh.model.mesh.field.setAsBackgroundMesh(field_id)


def _extract_supported_tetra_cells(
    elem_types: list[int],
    elem_tags: list[np.ndarray],
    elem_node_tags: list[np.ndarray],
    tag_to_index: dict[int, int],
) -> tuple[np.ndarray, int]:
    """
    提取当前求解器支持的四面体网格。

    说明：
    1. `type=4` 是 Tet4；
    2. `type=11` 是 Tet10；
    3. 当前 C++ 求解器仍以 Tet4 为主，因此 Tet10 会先退化成角点 Tet4。
    """

    cells: list[int] = []
    tetra_count = 0
    supported = {4: 4, 11: 10, 5: 8, 17: 20}
    hex_to_tet_pattern = (
        (0, 1, 3, 4),
        (1, 2, 3, 6),
        (1, 3, 4, 6),
        (1, 4, 5, 6),
        (3, 4, 6, 7),
    )

    for array_index, elem_type in enumerate(elem_types):
        if elem_type not in supported:
            continue

        nodes_per_element = supported[elem_type]
        nodes = elem_node_tags[array_index]
        for element_index in range(len(elem_tags[array_index])):
            offset = element_index * nodes_per_element
            if elem_type in (4, 11):
                corner_tags = nodes[offset: offset + 4]
                cell_nodes = [tag_to_index[int(tag)] for tag in corner_tags]
                cells.extend([4, *cell_nodes])
                tetra_count += 1
            else:
                corner_tags = nodes[offset: offset + 8]
                corner_nodes = [tag_to_index[int(tag)] for tag in corner_tags]
                for pattern in hex_to_tet_pattern:
                    tet_nodes = [corner_nodes[index] for index in pattern]
                    cells.extend([4, *tet_nodes])
                    tetra_count += 1

    return np.array(cells, dtype=np.int64), tetra_count


def _extract_display_grid(
    points: np.ndarray,
    elem_types: list[int],
    elem_tags: list[np.ndarray],
    elem_node_tags: list[np.ndarray],
    tag_to_index: dict[int, int],
) -> tuple[pv.UnstructuredGrid, int, str]:
    """提取用于界面显示和导出的原始体网格。"""

    cell_array: list[int] = []
    cell_types: list[int] = []
    generated_cell_type = "tetra"

    for array_index, elem_type in enumerate(elem_types):
        nodes = elem_node_tags[array_index]
        if elem_type in (4, 11):
            generated_cell_type = "tetra"
            nodes_per_element = 4 if elem_type == 4 else 10
            for element_index in range(len(elem_tags[array_index])):
                offset = element_index * nodes_per_element
                corner_tags = nodes[offset: offset + 4]
                cell_nodes = [tag_to_index[int(tag)] for tag in corner_tags]
                cell_array.extend([4, *cell_nodes])
                cell_types.append(int(pv.CellType.TETRA))
        elif elem_type in (5, 17):
            generated_cell_type = "hexa"
            nodes_per_element = 8 if elem_type == 5 else 20
            for element_index in range(len(elem_tags[array_index])):
                offset = element_index * nodes_per_element
                corner_tags = nodes[offset: offset + 8]
                cell_nodes = [tag_to_index[int(tag)] for tag in corner_tags]
                cell_array.extend([8, *cell_nodes])
                cell_types.append(int(pv.CellType.HEXAHEDRON))

    if not cell_types:
        raise RuntimeError("未提取到可显示的体单元。")

    grid = pv.UnstructuredGrid(
        np.array(cell_array, dtype=np.int64),
        np.array(cell_types, dtype=np.uint8),
        points,
    )
    return grid, len(cell_types), generated_cell_type


def _extract_points(node_tags: np.ndarray, node_coords: np.ndarray) -> np.ndarray:
    """把 Gmsh 返回的一维坐标数组整理成 N x 3 点阵。"""

    points = np.zeros((len(node_tags), 3), dtype=float)
    for index in range(len(node_tags)):
        points[index] = [
            node_coords[index * 3 + 0],
            node_coords[index * 3 + 1],
            node_coords[index * 3 + 2],
        ]
    return points


def _extract_surface_patches(
    points: np.ndarray,
    tag_to_index: dict[int, int],
) -> tuple[dict[str, pv.PolyData], dict[str, list[int]], dict[str, str]]:
    """从 Gmsh 的表面实体中提取真实可选表面补丁与节点集合。"""

    surface_patches: dict[str, pv.PolyData] = {}
    surface_node_sets: dict[str, list[int]] = {}
    surface_labels: dict[str, str] = {}
    supported_surface_types = {
        2: 3,   # Tri3
        3: 4,   # Quad4
        9: 6,   # Tri6
        10: 9,  # Quad9
        16: 8,  # Quad8
    }
    face_corner_count = {
        2: 3,
        3: 4,
        9: 3,
        10: 4,
        16: 4,
    }

    for _, surface_tag in gmsh.model.getEntities(2):
        elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements(2, surface_tag)
        face_stream: list[int] = []
        patch_nodes: set[int] = set()
        local_points: list[np.ndarray] = []
        global_to_local: dict[int, int] = {}

        for array_index, elem_type in enumerate(elem_types):
            if elem_type not in supported_surface_types:
                continue
            nodes_per_element = supported_surface_types[elem_type]
            corner_count = face_corner_count[elem_type]
            nodes = elem_node_tags[array_index]
            for element_index in range(len(elem_tags[array_index])):
                offset = element_index * nodes_per_element
                corner_tags = nodes[offset: offset + corner_count]
                global_indices = [tag_to_index[int(tag)] for tag in corner_tags]
                local_cell_nodes: list[int] = []
                for global_index in global_indices:
                    if global_index not in global_to_local:
                        global_to_local[global_index] = len(local_points)
                        local_points.append(points[global_index])
                    local_cell_nodes.append(global_to_local[global_index])
                face_stream.extend([corner_count, *local_cell_nodes])
                patch_nodes.update(global_indices)

        if not face_stream:
            continue

        patch_key = f"surface:{surface_tag}"
        patch_points = np.asarray(local_points, dtype=float)
        surface_patches[patch_key] = pv.PolyData(patch_points, np.array(face_stream, dtype=np.int64))
        surface_node_sets[patch_key] = sorted(patch_nodes)
        surface_labels[patch_key] = f"几何面-{surface_tag}"

    return surface_patches, surface_node_sets, surface_labels


def _extract_solver_cells(
    elem_types: list[int],
    elem_tags: list[np.ndarray],
    elem_node_tags: list[np.ndarray],
    tag_to_index: dict[int, int],
    mesh_topology: str,
) -> tuple[np.ndarray, str, int]:
    """提取真正送入求解器的单元连接关系。"""

    if mesh_topology == "hexa":
        supported = {5: 8, 17: 20}
        cells: list[int] = []
        count = 0
        for array_index, elem_type in enumerate(elem_types):
            if elem_type not in supported:
                continue
            nodes_per_element = supported[elem_type]
            nodes = elem_node_tags[array_index]
            for element_index in range(len(elem_tags[array_index])):
                offset = element_index * nodes_per_element
                corner_tags = nodes[offset: offset + 8]
                cell_nodes = [tag_to_index[int(tag)] for tag in corner_tags]
                cells.extend([8, *cell_nodes])
                count += 1
        if count == 0:
            raise RuntimeError("当前六面体网格没有提取到可供 Hex8 求解器使用的单元。")
        return np.array(cells, dtype=np.int64), "hexa", count

    cells, count = _extract_supported_tetra_cells(elem_types, elem_tags, elem_node_tags, tag_to_index)
    return cells, "tetra", count


def build_geometry_preview(state: ProjectState) -> PreviewSceneData:
    """生成用于几何预览页显示的表面网格。"""

    # `interruptible=False` 可以阻止 Gmsh 在初始化时注册 Python signal 处理器。
    # 这样当几何/网格任务放到后台线程执行时，不会触发
    # “signal only works in main thread of the main interpreter” 异常。
    gmsh.initialize(interruptible=False)
    try:
        # 几何预览属于启动期/前处理的轻量操作，这里关闭 Gmsh 终端输出，
        # 避免用户把正常的预览日志误认为报错。
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("GeometryPreview")
        source_label = _build_geometry(state)
        _apply_mesh_options(state)
        gmsh.model.mesh.generate(2)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements()
        if len(node_tags) == 0:
            raise RuntimeError("几何预览失败：未生成任何节点。")

        points = _extract_points(node_tags, node_coords)
        tag_to_index = {int(tag): index for index, tag in enumerate(node_tags)}
        surface_patches, _surface_node_sets, surface_labels = _extract_surface_patches(points, tag_to_index)

        faces: list[int] = []
        for array_index, elem_type in enumerate(elem_types):
            if elem_type != 2:
                continue

            nodes = elem_node_tags[array_index]
            for element_index in range(len(elem_tags[array_index])):
                n1 = tag_to_index[int(nodes[element_index * 3 + 0])]
                n2 = tag_to_index[int(nodes[element_index * 3 + 1])]
                n3 = tag_to_index[int(nodes[element_index * 3 + 2])]
                faces.extend([3, n1, n2, n3])

        surface = pv.PolyData(points, np.array(faces, dtype=np.int64))
        feature_edges = surface.extract_feature_edges(feature_angle=30.0)
        return PreviewSceneData(
            surface=surface,
            feature_edges=feature_edges,
            surface_patches=surface_patches,
            surface_labels=surface_labels,
            point_count=surface.n_points,
            face_count=surface.n_cells,
            source_label=source_label,
        )
    finally:
        gmsh.finalize()


def generate_volume_mesh(state: ProjectState) -> MeshBundle:
    """生成三维 Tet4 体网格，并导出 msh 文件。"""

    # 同上：关闭 Gmsh 的 signal 注册，保证后台线程里也能安全调用。
    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.model.add("VolumeMesh")
        _build_geometry(state)
        _apply_mesh_options(state)
        _apply_hexahedral_controls(state)
        _apply_local_refinement_field(state)
        gmsh.model.mesh.generate(3)

        if state.geometry.optimize_mesh:
            gmsh.model.mesh.optimize("")

        # 项目状态里可以保存相对路径；
        # 这里统一解析到当前运行目录下，保证打包后的 EXE 也能稳定输出网格文件。
        mesh_file = str(resolve_runtime_path(state.geometry.mesh_file))
        _ensure_parent_dir(mesh_file)
        gmsh.write(mesh_file)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements()
        if len(node_tags) == 0:
            raise RuntimeError("体网格生成失败：未生成任何节点。")

        points = _extract_points(node_tags, node_coords)
        tag_to_index = {int(tag): index for index, tag in enumerate(node_tags)}
        surface_patches, surface_node_sets, surface_labels = _extract_surface_patches(points, tag_to_index)
        display_grid, display_cell_count, display_cell_type = _extract_display_grid(points, elem_types, elem_tags, elem_node_tags, tag_to_index)
        solver_cells, solver_cell_type, solver_cell_count = _extract_solver_cells(
            elem_types,
            elem_tags,
            elem_node_tags,
            tag_to_index,
            state.geometry.mesh_topology,
        )

        solver_cell_types = np.full(
            solver_cell_count,
            pv.CellType.HEXAHEDRON if solver_cell_type == "hexa" else pv.CellType.TETRA,
            dtype=np.uint8,
        )
        grid = pv.UnstructuredGrid(solver_cells, solver_cell_types, points)

        summary = MeshSummary(
            node_count=len(points),
            tetra_count=solver_cell_count,
            display_cell_count=display_cell_count,
            display_cell_type=display_cell_type,
            xmin=float(points[:, 0].min()),
            xmax=float(points[:, 0].max()),
            ymin=float(points[:, 1].min()),
            ymax=float(points[:, 1].max()),
            zmin=float(points[:, 2].min()),
            zmax=float(points[:, 2].max()),
        )

        return MeshBundle(
            points=points,
            cells=solver_cells,
            grid=grid,
            display_grid=display_grid,
            solver_cells=solver_cells,
            solver_cell_type=solver_cell_type,
            surface_patches=surface_patches,
            surface_node_sets=surface_node_sets,
            surface_labels=surface_labels,
            summary=summary,
            mesh_file=mesh_file,
            generated_cell_type=display_cell_type,
        )
    finally:
        gmsh.finalize()


def export_mesh_to_vtk(mesh_bundle: MeshBundle, vtk_file: str) -> str:
    """把当前网格导出为 VTK/VTU 文件。"""

    _ensure_parent_dir(vtk_file)
    mesh_bundle.display_grid.save(vtk_file)
    return vtk_file


def compute_mesh_quality(mesh_bundle: MeshBundle) -> MeshQualityBundle:
    """计算 scaled Jacobian 网格质量指标。"""

    quality_grid = mesh_bundle.display_grid.cell_quality(quality_measure="scaled_jacobian")
    quality_array = quality_grid.cell_data["scaled_jacobian"]
    quality_grid.cell_data["CellQuality"] = quality_array

    summary = MeshSummary(
        node_count=mesh_bundle.summary.node_count,
        tetra_count=mesh_bundle.summary.tetra_count,
        display_cell_count=mesh_bundle.summary.display_cell_count,
        display_cell_type=mesh_bundle.summary.display_cell_type,
        xmin=mesh_bundle.summary.xmin,
        xmax=mesh_bundle.summary.xmax,
        ymin=mesh_bundle.summary.ymin,
        ymax=mesh_bundle.summary.ymax,
        zmin=mesh_bundle.summary.zmin,
        zmax=mesh_bundle.summary.zmax,
        quality_min=float(np.min(quality_array)),
        quality_avg=float(np.mean(quality_array)),
        quality_max=float(np.max(quality_array)),
        quality_bad_count=int(np.sum(quality_array <= 0.2)),
        quality_warning_count=int(np.sum((quality_array > 0.2) & (quality_array <= 0.4))),
    )
    return MeshQualityBundle(quality_grid=quality_grid, summary=summary)
