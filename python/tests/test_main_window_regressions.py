"""
主窗口回归测试。

说明：
1. 这里重点覆盖已经出现过的 GUI 运行时回归；
2. 测试尽量避开真实 OpenGL 视口，保证在命令行环境里也能稳定运行；
3. 当前先校核后台求解回调与可视化辅助函数的关键路径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyvista as pv
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

# 先设置无界面环境变量，再导入主窗口模块。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CAE_SKIP_VIEWPORT_INIT", "1")
os.environ.setdefault("CAE_SKIP_AUTO_PREVIEW", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from app.main_window import CAEMainWindow, get_analysis_artifacts_type
from app.models import ProjectState, ResultSummary

APP = QApplication.instance() or QApplication([])


def _build_dummy_grid() -> pv.UnstructuredGrid:
    """构造一个最小可用的四面体结果网格。"""

    points = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )
    cells = np.array([4, 0, 1, 2, 3], dtype=np.int64)
    cell_types = np.array([pv.CellType.TETRA], dtype=np.uint8)
    grid = pv.UnstructuredGrid(cells, cell_types, points)
    grid.point_data["Displacement"] = np.zeros((4, 3), dtype=float)
    grid.point_data["DisplacementMagnitude"] = np.zeros(4, dtype=float)
    grid.cell_data["VonMisesStress"] = np.zeros(1, dtype=float)
    grid.cell_data["EquivalentStrain"] = np.zeros(1, dtype=float)
    return grid


def _dummy_analysis_artifacts():
    """构造一个可供主窗口回调接收的最小结果对象。"""

    analysis_type = get_analysis_artifacts_type()
    grid = _build_dummy_grid()
    return analysis_type(
        solver=None,
        displacement_matrix=np.zeros((4, 3), dtype=float),
        stress_array=np.zeros(1, dtype=float),
        strain_array=np.zeros(1, dtype=float),
        temperature_array=None,
        heat_flux_array=None,
        result_grid=grid,
        deformed_grid=grid.copy(deep=True),
        summary=ResultSummary(converged=True, iteration_count=1),
    )


def _make_window() -> CAEMainWindow:
    """创建一个不会触发真实视口初始化的主窗口实例。"""

    window = CAEMainWindow()
    window.restore_full_result = lambda: None
    window._refresh_summary = lambda: None
    window.log = lambda *_args, **_kwargs: None
    return window


def test_on_analysis_finished_accepts_runtime_analysis_artifacts() -> None:
    """验证后台求解回调不会再因 AnalysisArtifacts 未定义而崩溃。"""

    window = _make_window()
    try:
        state_snapshot = ProjectState()
        analysis = _dummy_analysis_artifacts()
        window._on_analysis_finished((state_snapshot, analysis))
        assert window.analysis is analysis
        assert window.state.result_summary.converged is True
    finally:
        window.close()


def test_surface_display_helpers_support_fallback_face_plane() -> None:
    """验证兼容端面回退平面仍能给出稳定法向和标记点。"""

    window = _make_window()
    try:
        bounds = (0.0, 2.0, -1.0, 1.0, -0.5, 0.5)
        plane = window._resolve_visual_surface_patch("xmax", bounds)
        assert plane is not None
        normal = window._surface_display_normal("xmax", plane, bounds)
        marker_points = window._surface_marker_points(plane, max_points=3)
        assert np.allclose(normal, np.array([1.0, 0.0, 0.0]))
        assert marker_points.shape[0] >= 1
    finally:
        window.close()


def test_property_page_change_redraws_current_geometry_scene() -> None:
    """验证切换到其他属性页时会重绘当前几何场景，保证条件标记持续更新。"""

    window = _make_window()
    redraw_calls: list[str] = []
    try:
        window.preview_scene = object()
        window.current_scene = "geometry"
        window._draw_geometry_scene = lambda: redraw_calls.append("geometry")
        window._on_property_page_changed(1)
        assert redraw_calls == ["geometry"]
    finally:
        window.close()


def test_boundary_pick_left_drag_events_are_consumed() -> None:
    """验证选面模式会把左键拖动转发为视口旋转，而轻点仍保留给选面。"""

    window = _make_window()
    try:
        forwarded_events: list[str] = []
        interactor = SimpleNamespace(
            mousePressEvent=lambda _event: forwarded_events.append("press"),
            mouseMoveEvent=lambda _event: forwarded_events.append("move"),
            mouseReleaseEvent=lambda _event: forwarded_events.append("release"),
        )
        window.viewport = SimpleNamespace(interactor=interactor)
        window.boundary_pick_mode = "load"
        window._update_boundary_face_hover = lambda *_args, **_kwargs: None
        window._pick_boundary_face_at_position = lambda *_args, **_kwargs: True

        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(10.0, 12.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(14.0, 16.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(14.0, 16.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        assert window.eventFilter(interactor, press_event) is True
        assert window.eventFilter(interactor, move_event) is True
        assert window.eventFilter(interactor, release_event) is True
        assert forwarded_events == ["press", "move", "release"]
    finally:
        window.close()


def test_structural_load_symbol_uses_face_center_anchor() -> None:
    """验证载荷符号会从选中面中心附近生成基点和箭头几何。"""

    recorded_meshes: list[pv.PolyData] = []
    window = _make_window()
    try:
        window.viewport = SimpleNamespace(
            add_mesh=lambda mesh, **_kwargs: recorded_meshes.append(mesh),
            interactor=object(),
        )
        bounds = (0.0, 2.0, -1.0, 1.0, -0.5, 0.5)
        plane = window._resolve_visual_surface_patch("xmax", bounds)
        assert plane is not None
        window._add_structural_load_symbols(
            plane,
            "xmax",
            np.array([0.0, 0.0, -1000.0], dtype=float),
            bounds,
            size=2.0,
        )
        assert len(recorded_meshes) == 2
        base_marker = recorded_meshes[0]
        arrow_mesh = recorded_meshes[1]
        base_center = np.mean(np.asarray(base_marker.points), axis=0)
        assert base_marker.n_points > 0
        assert arrow_mesh.n_points > 0
        assert np.allclose(base_center, np.array([2.0, 0.0, 0.0]), atol=0.02)
    finally:
        window.close()


def test_condition_visuals_are_hidden_outside_condition_modules() -> None:
    """验证荷载与边界标记不会在其他属性模块误显示。"""

    recorded_meshes: list[pv.PolyData] = []
    window = _make_window()
    try:
        window.viewport = SimpleNamespace(
            add_mesh=lambda mesh, **_kwargs: recorded_meshes.append(mesh),
            interactor=object(),
        )
        window._has_viewport = lambda: True
        window._current_module_index = lambda: 0
        window._current_scene_bounds = lambda: (0.0, 2.0, -1.0, 1.0, -0.5, 0.5)
        window.state.load_case.is_applied = True
        window.state.load_case.loaded_face = "xmax"
        window.state.load_case.force_z = -1000.0
        window._add_condition_visuals()
        assert recorded_meshes == []
    finally:
        window.close()
