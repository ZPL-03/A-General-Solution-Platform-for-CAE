"""通用 CAE 图形主窗口。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import traceback
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

# Windows 下嵌入 VTK 视口时，先关闭 MSAA，降低部分显卡驱动在首帧创建时卡死的概率。
if sys.platform.startswith("win"):
    os.environ.setdefault("VTK_FORCE_MSAA", "0")
warnings.filterwarnings("ignore", message="sipPyTypeDict\\(\\) is deprecated", category=DeprecationWarning)

import numpy as np
from PyQt6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QThread, QTimer, pyqtSignal, qInstallMessageHandler
from PyQt6.QtGui import QAction, QMouseEvent
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QStatusBar, QStyle, QTableWidget, QTableWidgetItem, QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor

# 先把源码模式下的 `python/` 目录加入搜索路径，
# 这样当前文件既可以直接作为脚本运行，也可以在打包后继续复用同一套导入逻辑。
SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PYTHON_ROOT = SOURCE_PROJECT_ROOT / "python"
if str(SOURCE_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PYTHON_ROOT))

from app.models import MeshSummary, ProjectState, ResultSummary
from app.runtime_paths import APP_ROOT, DATA_DIR, MODELS_DIR, RESULTS_DIR, bootstrap_runtime_environment
from app.services.mesh_service import MeshBundle, MeshQualityBundle, PreviewSceneData, build_geometry_preview, compute_mesh_quality, export_mesh_to_vtk, generate_volume_mesh

bootstrap_runtime_environment()
PROJECT_ROOT = APP_ROOT
PYTHON_ROOT = PROJECT_ROOT / "python"
STARTUP_TRACE_ENABLED = os.environ.get("CAE_STARTUP_TRACE", "0") == "1"
SKIP_VIEWPORT_INIT = os.environ.get("CAE_SKIP_VIEWPORT_INIT", "0") == "1"
SKIP_AUTO_PREVIEW = os.environ.get("CAE_SKIP_AUTO_PREVIEW", "0") == "1"


def write_startup_trace(message: str) -> None:
    """
    在软件目录中记录启动轨迹。
    说明：
    1. 只在显式设置 `CAE_STARTUP_TRACE=1` 时启用；
    2. 用于定位打包版的原生崩溃发生在启动流程的哪一步；
    3. 每次写入都立即刷新到磁盘，避免进程崩溃后轨迹丢失。
    """

    if not STARTUP_TRACE_ENABLED:
        return

    trace_file = APP_ROOT / "startup_trace.log"
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    with trace_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}\n")
        handle.flush()


if TYPE_CHECKING:
    from app.services.solver_service import AnalysisArtifacts


def build_markdown_report(state: ProjectState, analysis_label: str, solver_label: str) -> str:
    """
    延迟导入报告服务。
    说明：
    1. 报告导出不是启动关键路径；
    2. 这样可以避免主窗口导入时顺带触发 `solver_service`；
    3. 有助于降低打包版启动阶段的原生模块加载压力。
    """

    from app.services.report_service import build_markdown_report as _build_markdown_report

    return _build_markdown_report(state, analysis_label, solver_label)


def _solver_service():
    """延迟导入求解服务模块，避免主窗口启动时立刻加载 `fem_core`。"""

    from app.services import solver_service

    return solver_service


def describe_dynamic_damping(state: ProjectState) -> str:
    """延迟转发到求解服务中的阻尼描述函数。"""

    return _solver_service().describe_dynamic_damping(state)

def get_analysis_artifacts_type():
    """延迟获取求解结果对象类型，避免主窗口导入时过早加载求解模块。"""

    return _solver_service().AnalysisArtifacts

GENERIC_LINEAR_SOLVER_OPTIONS = [
    ("SparseLU 直接法", "sparse_lu"),
    ("Conjugate Gradient", "conjugate_gradient"),
    ("BiCGSTAB", "bicgstab"),
]

ANALYSIS_SOLVER_OPTIONS = {
    "linear_static": GENERIC_LINEAR_SOLVER_OPTIONS,
    "nonlinear_static": GENERIC_LINEAR_SOLVER_OPTIONS,
    "transient_dynamic": GENERIC_LINEAR_SOLVER_OPTIONS,
    "steady_state_thermal": GENERIC_LINEAR_SOLVER_OPTIONS,
    "modal_analysis": [("内置广义特征值求解器", "modal_eigensolver")],
    "frequency_response": [("内置复数直接频响法", "harmonic_direct")],
}

ANALYSIS_SOLVER_CAPTIONS = {
    "linear_static": "线性求解器：",
    "nonlinear_static": "线性求解器：",
    "transient_dynamic": "线性求解器：",
    "steady_state_thermal": "热方程求解器：",
    "modal_analysis": "模态求解器：",
    "frequency_response": "频响求解算法：",
}

ANALYSIS_SOLVER_HINTS = {
    "linear_static": "当前分析步会使用所选稀疏线性求解器。",
    "nonlinear_static": "非线性每次迭代都要求解线性切线方程组，因此这里的求解器会实际参与计算。",
    "transient_dynamic": "瞬态动力学每个时间步都会组装并求解实数方程组，因此这里的求解器会实际参与计算。",
    "steady_state_thermal": "稳态热传导会组装并求解标量热传导方程，因此这里的求解器会实际参与计算。",
    "modal_analysis": "模态分析使用内置广义特征值求解器，线性方程组求解器不会参与本步计算。",
    "frequency_response": "频响分析当前使用内置复数直接频响法，线性方程组求解器不会参与本步计算。",
}


def get_solver_caption_for_analysis(analysis_type: str) -> str:
    """返回指定分析步对应的求解器标题。"""

    return ANALYSIS_SOLVER_CAPTIONS.get(analysis_type, "线性求解器：")


def get_solver_hint_for_analysis(analysis_type: str) -> str:
    """返回指定分析步对应的求解器提示。"""

    return ANALYSIS_SOLVER_HINTS.get(analysis_type, "当前分析步将使用所选求解器配置。")


def get_solver_options_for_analysis(analysis_type: str) -> list[tuple[str, str]]:
    """返回指定分析步允许的求解器选项。"""

    return list(ANALYSIS_SOLVER_OPTIONS.get(analysis_type, GENERIC_LINEAR_SOLVER_OPTIONS))


def normalize_solver_for_analysis(analysis_type: str, solver_name: str) -> str:
    """把求解器值约束到当前分析步允许的集合中。"""

    allowed_values = [value for _label, value in get_solver_options_for_analysis(analysis_type)]
    if solver_name in allowed_values:
        return solver_name
    return allowed_values[0]


def run_linear_static_analysis(mesh_bundle: MeshBundle, state: ProjectState):
    """延迟转发到求解服务中的分析执行函数。"""

    return _solver_service().run_linear_static_analysis(mesh_bundle, state)

FACE_ITEMS = [("X 最小端面", "xmin"), ("X 最大端面", "xmax"), ("Y 最小端面", "ymin"), ("Y 最大端面", "ymax"), ("Z 最小端面", "zmin"), ("Z 最大端面", "zmax")]
FACE_LABELS = {value: label for label, value in FACE_ITEMS}
ALG2D = [("Delaunay", 5), ("Frontal-Delaunay", 6), ("BAMG", 7)]
ALG3D = [("Delaunay", 1), ("Frontal", 4), ("MMG3D", 7), ("HXT", 10)]
MESH_TOPOLOGIES = [("四面体", "tetra"), ("六面体（仅长方体）", "hexa")]
PRIMITIVES = [("长方体", "box"), ("圆柱体", "cylinder"), ("球体", "sphere"), ("带孔板", "plate_with_hole")]
ANALYSIS_TYPES = [("线性静力学", "linear_static"), ("非线性静力学", "nonlinear_static"), ("稳态热传导", "steady_state_thermal"), ("线性模态分析", "modal_analysis"), ("线性瞬态动力学", "transient_dynamic"), ("线性频响分析", "frequency_response")]
SLICE_AXES = [("X 方向切片", "x"), ("Y 方向切片", "y"), ("Z 方向切片", "z")]
RESULT_SCALARS = {"stress": "VonMisesStress", "strain": "EquivalentStrain", "displacement": "DisplacementMagnitude", "temperature": "Temperature", "heat_flux": "HeatFluxMagnitude"}
CURVE_RESPONSE_ITEMS = [
    ("全局最大位移", "global_max"),
    ("受载面平均 Ux", "loaded_ux"),
    ("受载面平均 Uy", "loaded_uy"),
    ("受载面平均 Uz", "loaded_uz"),
    ("受载面平均位移幅值", "loaded_umag"),
]
DAMPING_MODE_ITEMS = [
    ("无阻尼", "none"),
    ("直接 Rayleigh 阻尼", "rayleigh"),
    ("按阻尼比换算 Rayleigh", "modal_ratio"),
]


def qt_message_handler(_msg_type, _context, message: str) -> None:
    """屏蔽无害的 Qt Windows 边框提示。"""

    if "setDarkBorderToWindow" in message:
        return
    sys.__stderr__.write(f"{message}\n")


class SignalEmitter(QObject):
    """把标准输出转发到界面日志。"""

    text_written = pyqtSignal(str)


class StdoutRedirector:
    """把 `print` 输出写入日志窗口。"""

    def __init__(self, emitter: SignalEmitter) -> None:
        self.emitter = emitter

    def write(self, text: str) -> None:
        self.emitter.text_written.emit(text)

    def flush(self) -> None:
        return None


class BackgroundTaskWorker(QObject):
    """把耗时任务放到后台线程执行，避免阻塞界面主线程。"""

    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, task_name: str, task_callable: Callable[[], object]) -> None:
        super().__init__()
        self.task_name = task_name
        self.task_callable = task_callable

    def run(self) -> None:
        try:
            result = self.task_callable()
            self.finished.emit(self.task_name, result)
        except Exception:
            self.failed.emit(self.task_name, traceback.format_exc())
        try:
            result = self.task_callable()
            self.finished.emit(self.task_name, result)
        except Exception:
            self.failed.emit(self.task_name, traceback.format_exc())


class AsyncTaskWorker(QObject):
    """真正用于后台线程执行的任务 worker。"""

    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, task_name: str, task_callable: Callable[[], object]) -> None:
        super().__init__()
        self.task_name = task_name
        self.task_callable = task_callable

    def run(self) -> None:
        try:
            result = self.task_callable()
            self.finished.emit(self.task_name, result)
        except Exception:
            self.failed.emit(self.task_name, traceback.format_exc())


class MetricCard(QFrame):
    """摘要卡片。"""

    def __init__(self, title: str, accent: str) -> None:
        super().__init__()
        self.setStyleSheet(f"QFrame{{background:white;border:1px solid #D9E2EC;border-left:5px solid {accent};border-radius:10px;}}")
        layout = QVBoxLayout(self)
        self.title = QLabel(title)
        self.title.setStyleSheet("color:#52606D;font-size:12px;")
        self.value = QLabel("--")
        self.value.setStyleSheet("color:#102A43;font-size:20px;font-weight:700;")
        self.note = QLabel("等待更新")
        self.note.setStyleSheet("color:#7B8794;font-size:12px;")
        self.note.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.note)

    def update_card(self, value: str, note: str) -> None:
        self.value.setText(value)
        self.note.setText(note)


class SummaryDialog(QDialog):
    """单独弹出的模型摘要窗口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("模型摘要")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        layout.addWidget(self.browser)

    def set_summary(self, text: str) -> None:
        self.browser.setText(text)


class PlotDialog(QDialog):
    """用于显示动力学响应曲线的独立绘图窗口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("响应曲线")
        self.resize(880, 560)
        layout = QVBoxLayout(self)
        self.figure = Figure(figsize=(8, 5), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

    def plot_curve(
        self,
        x_values: np.ndarray,
        y_values: np.ndarray,
        title: str,
        x_label: str,
        y_label: str,
        color: str,
    ) -> None:
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.plot(x_values, y_values, color=color, linewidth=2.0)
        axes.set_title(title)
        axes.set_xlabel(x_label)
        axes.set_ylabel(y_label)
        axes.grid(True, alpha=0.3)
        self.canvas.draw_idle()


class ResultTableDialog(QDialog):
    """用于浏览结果表格的独立窗口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("结果数据表")
        self.resize(1080, 700)
        self.sections: dict[str, tuple[list[str], list[list[str]], str]] = {}
        self.current_section_name = ""
        layout = QVBoxLayout(self)
        top_row = QHBoxLayout()
        self.combo_section = QComboBox()
        self.combo_section.currentTextChanged.connect(self._switch_section)
        self.button_export = QPushButton("导出当前表格 CSV")
        self.button_export.clicked.connect(self._export_current_section)
        top_row.addWidget(QLabel("数据表："))
        top_row.addWidget(self.combo_section, 1)
        top_row.addWidget(self.button_export)
        layout.addLayout(top_row)
        self.note_label = QLabel("等待加载结果表格。")
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color:#52606D;")
        layout.addWidget(self.note_label)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

    def set_sections(self, sections: dict[str, tuple[list[str], list[list[str]], str]]) -> None:
        """更新当前可浏览的数据表集合。"""

        self.sections = sections
        self.combo_section.blockSignals(True)
        self.combo_section.clear()
        for name in sections:
            self.combo_section.addItem(name)
        self.combo_section.blockSignals(False)
        if sections:
            first_name = next(iter(sections))
            self.combo_section.setCurrentText(first_name)
            self._switch_section(first_name)

    def _switch_section(self, section_name: str) -> None:
        """切换当前显示的数据表。"""

        if section_name not in self.sections:
            return
        self.current_section_name = section_name
        headers, rows, note = self.sections[section_name]
        self.note_label.setText(note)
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for row_index, row_values in enumerate(rows):
            for column_index, value in enumerate(row_values):
                self.table.setItem(row_index, column_index, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def _export_current_section(self) -> None:
        """把当前显示的数据表导出为 CSV。"""

        if not self.current_section_name or self.current_section_name not in self.sections:
            QMessageBox.warning(self, "没有数据", "当前没有可导出的结果表格。")
            return
        default_name = self.current_section_name.replace(" ", "_")
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "导出结果表格 CSV",
            str(RESULTS_DIR / f"{default_name}.csv"),
            "CSV File (*.csv)",
        )
        if not file_name:
            return
        headers, rows, _note = self.sections[self.current_section_name]
        with open(file_name, "w", encoding="utf-8", newline="") as handle:
            handle.write(",".join(headers) + "\n")
            for row in rows:
                handle.write(",".join(row) + "\n")
        QMessageBox.information(self, "导出完成", f"结果表格已导出到：\n{file_name}")


class ProbeDialog(QDialog):
    """显示结果探针查询信息的独立窗口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("结果探针")
        self.resize(500, 420)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        layout.addWidget(self.browser)

    def set_probe_text(self, text: str) -> None:
        self.browser.setMarkdown(text)


class CAEMainWindow(QMainWindow):
    """通用 CAE 工作台主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.state = ProjectState()
        self.preview_scene: Optional[PreviewSceneData] = None
        self.mesh_bundle: Optional[MeshBundle] = None
        self.quality_bundle: Optional[MeshQualityBundle] = None
        self.analysis: Optional[AnalysisArtifacts] = None
        self.current_scene = "geometry"
        self.current_result_mode = "stress"
        self.current_result_view = "full"
        self.current_modal_mode_index = 0
        self.geometry_rows: dict[str, tuple[QLabel, QWidget]] = {}
        self.thermal_pick_mode: Optional[str] = None
        self.boundary_pick_mode: Optional[str] = None
        self.boundary_pick_actor_map: dict[str, str] = {}
        self.boundary_pick_actor_refs: dict[str, object] = {}
        self.boundary_pick_hover_face: str = ""
        self.pick_press_position: Optional[tuple[float, float]] = None
        self.pick_press_modifiers = Qt.KeyboardModifier.NoModifier
        self.pick_drag_active = False
        self.result_probe_enabled = False
        self.result_probe_marker_actor = None
        self.result_probe_hover_actor = None
        self.result_probe_hover_node_id = -1
        self.current_display_grid: Optional[pv.DataSet] = None
        self.current_display_scalar = ""
        self.current_display_title = ""
        self.summary_dialog: Optional[SummaryDialog] = None
        self.response_dialog: Optional[SummaryDialog] = None
        self.plot_dialog: Optional[PlotDialog] = None
        self.result_table_dialog: Optional[ResultTableDialog] = None
        self.probe_dialog: Optional[ProbeDialog] = None
        self._stdout = sys.stdout
        self._is_syncing_widgets = False
        self._startup_initialized = False
        self._busy_task_name = ""
        self._busy_thread: Optional[QThread] = None
        self._busy_worker: Optional[QObject] = None
        self.main_toolbar: Optional[QToolBar] = None
        self.view_toolbar: Optional[QToolBar] = None
        self.viewport_page: Optional[QWidget] = None
        self.viewport_layout: Optional[QVBoxLayout] = None
        self.viewport_placeholder: Optional[QLabel] = None
        self.viewport: Optional[QtInteractor] = None

        self.setWindowTitle("CAE 通用分析工作台 v1.0")
        self.resize(1820, 1040)
        self.setMinimumSize(1500, 920)
        self._build_ui()
        self._install_stdout_redirector()
        self._sync_widgets_from_state()
        self._refresh_summary()

    def _make_double(self, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        return spin

    def _set_combo_by_data(self, combo: QComboBox, value: str | int) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _icon(self, icon_type: QStyle.StandardPixmap):
        return self.style().standardIcon(icon_type)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QMainWindow{background:#E8EDF3;}"
            "QGroupBox{border:1px solid #C9D2DC;border-radius:8px;margin-top:12px;background:white;font-weight:700;color:#1F2933;}"
            "QGroupBox::title{subcontrol-origin:margin;left:12px;}"
            "QTreeWidget,QPlainTextEdit,QTextBrowser,QComboBox,QDoubleSpinBox,QSpinBox{background:white;border:1px solid #C5CDD5;border-radius:6px;padding:4px;}"
            "QPushButton{background:#2563EB;color:white;border:none;border-radius:6px;padding:7px 12px;font-weight:600;}"
            "QPushButton:hover{background:#1D4ED8;}"
        )

        self._build_menu_bar()
        self._build_main_toolbar()
        self._build_view_toolbar()
        self._build_center_view()
        self._build_tree_dock()
        self._build_property_dock()
        self._build_log_dock()
        self.setStatusBar(QStatusBar(self))

    def _build_menu_bar(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("文件")
        view_menu = menu.addMenu("视图")
        mesh_menu = menu.addMenu("网格")
        solve_menu = menu.addMenu("求解")

        for text, icon, slot in [("导入 CAD", QStyle.StandardPixmap.SP_DialogOpenButton, self.import_cad), ("打开项目", QStyle.StandardPixmap.SP_DialogOpenButton, self.load_project), ("保存项目", QStyle.StandardPixmap.SP_DialogSaveButton, self.save_project), ("模型摘要", QStyle.StandardPixmap.SP_FileDialogInfoView, self.open_summary_window)]:
            action = QAction(self._icon(icon), text, self)
            action.triggered.connect(slot)
            file_menu.addAction(action)

        for text, slot in [("等轴测", self.view_isometric), ("前视", self.view_front), ("右视", self.view_right), ("俯视", self.view_top), ("适配视图", self.reset_active_camera)]:
            action = QAction(text, self)
            action.triggered.connect(slot)
            view_menu.addAction(action)

        for text, icon, slot in [("生成网格", QStyle.StandardPixmap.SP_BrowserReload, self.generate_mesh), ("检查质量", QStyle.StandardPixmap.SP_FileDialogContentsView, self.check_mesh_quality), ("导出网格", QStyle.StandardPixmap.SP_DriveFDIcon, self.export_mesh)]:
            action = QAction(self._icon(icon), text, self)
            action.triggered.connect(slot)
            mesh_menu.addAction(action)

        for text, icon, slot in [("运行分析", QStyle.StandardPixmap.SP_MediaPlay, self.run_analysis_async), ("结果探针", QStyle.StandardPixmap.SP_FileDialogInfoView, self.toggle_result_probe), ("结果数据表", QStyle.StandardPixmap.SP_FileDialogDetailedView, self.open_result_table_window), ("导出分析报告", QStyle.StandardPixmap.SP_DialogSaveButton, self.export_analysis_report), ("导出结果 CSV", QStyle.StandardPixmap.SP_DialogSaveButton, self.export_results_csv), ("导出结果 VTU", QStyle.StandardPixmap.SP_DialogSaveButton, self.export_results_vtu)]:
            action = QAction(self._icon(icon), text, self)
            action.triggered.connect(slot)
            solve_menu.addAction(action)

    def _build_main_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏", self)
        toolbar.setIconSize(QSize(22, 22))
        self.addToolBar(toolbar)
        self.main_toolbar = toolbar
        actions = [("导入CAD", QStyle.StandardPixmap.SP_DialogOpenButton, self.import_cad), ("预览几何", QStyle.StandardPixmap.SP_BrowserReload, self.refresh_geometry_preview), ("生成网格", QStyle.StandardPixmap.SP_FileDialogDetailedView, self.generate_mesh), ("检查质量", QStyle.StandardPixmap.SP_FileDialogContentsView, self.check_mesh_quality), ("运行分析", QStyle.StandardPixmap.SP_MediaPlay, self.run_analysis_async), ("结果探针", QStyle.StandardPixmap.SP_FileDialogInfoView, self.toggle_result_probe), ("结果表格", QStyle.StandardPixmap.SP_FileDialogDetailedView, self.open_result_table_window), ("导出报告", QStyle.StandardPixmap.SP_DialogSaveButton, self.export_analysis_report), ("模型摘要", QStyle.StandardPixmap.SP_FileDialogInfoView, self.open_summary_window)]
        for text, icon, slot in actions:
            action = QAction(self._icon(icon), text, self)
            action.triggered.connect(slot)
            toolbar.addAction(action)

    def _build_view_toolbar(self) -> None:
        toolbar = QToolBar("视图工具栏", self)
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.view_toolbar = toolbar

        for text, slot in [("等轴", self.view_isometric), ("前", self.view_front), ("右", self.view_right), ("上", self.view_top), ("适配", self.reset_active_camera)]:
            toolbar.addAction(text, slot)

        toolbar.addSeparator()
        self.action_toggle_axes = QAction("坐标轴", self)
        self.action_toggle_axes.setCheckable(True)
        self.action_toggle_axes.setChecked(True)
        self.action_toggle_axes.triggered.connect(self._apply_view_preferences)
        toolbar.addAction(self.action_toggle_axes)

    def _build_center_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        placeholder = QLabel("三维视口初始化中，请稍候…")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("QLabel{color:#52606D;font-size:14px;background:#D9E2EC;}")
        layout.addWidget(placeholder)
        self.viewport_page = page
        self.viewport_layout = layout
        self.viewport_placeholder = placeholder
        self.setCentralWidget(page)

    def _initialize_viewport(self) -> None:
        """在事件循环启动后再创建 VTK 视口，避免主窗口构造阶段过早触发原生 OpenGL 初始化。"""

        if self.viewport is not None or self.viewport_page is None or self.viewport_layout is None:
            return
        if SKIP_VIEWPORT_INIT:
            write_startup_trace("skip viewport initialization by environment flag")
            return

        # 关闭 pyvistaqt 的自动刷新定时器，避免首屏阶段后台 render 抢先触发。
        # 当前界面在 add_mesh / clear / reset_camera 等操作后会主动刷新，足够满足需求。
        write_startup_trace("begin viewport initialization")
        viewport = QtInteractor(self.viewport_page, auto_update=False, multi_samples=0)
        viewport.set_background("#D9E2EC", top="#243B53")
        viewport.add_axes()
        viewport.interactor.installEventFilter(self)

        if self.viewport_placeholder is not None:
            self.viewport_layout.removeWidget(self.viewport_placeholder)
            self.viewport_placeholder.deleteLater()
            self.viewport_placeholder = None

        self.viewport_layout.addWidget(viewport.interactor)
        self.viewport = viewport
        write_startup_trace("viewport initialization finished")

    def _has_viewport(self) -> bool:
        return self.viewport is not None

    def _build_tree_dock(self) -> None:
        dock = QDockWidget("模型数据库", self)
        self.project_tree = QTreeWidget()
        self.project_tree.setHeaderLabel("模型")
        root = QTreeWidgetItem(self.project_tree, ["Model-1"])
        for text in ["几何", "材料", "载荷与边界", "热模块", "网格", "分析步", "结果", "视图"]:
            QTreeWidgetItem(root, [text])
        self.project_tree.expandAll()
        self.project_tree.itemClicked.connect(self._on_tree_clicked)
        dock.setWidget(self.project_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_property_dock(self) -> None:
        dock = QDockWidget("属性编辑器", self)
        self.property_stack = QStackedWidget()
        self.property_stack.addWidget(self._make_geometry_page())
        self.property_stack.addWidget(self._make_material_page())
        self.property_stack.addWidget(self._make_load_page())
        self.property_stack.addWidget(self._make_thermal_page())
        self.property_stack.addWidget(self._make_mesh_page())
        self.property_stack.addWidget(self._make_solver_page())
        self.property_stack.addWidget(self._make_result_page())
        self.property_stack.addWidget(self._make_view_page())
        self.property_stack.currentChanged.connect(self._on_property_page_changed)
        dock.setWidget(self.property_stack)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _build_log_dock(self) -> None:
        dock = QDockWidget("消息日志", self)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("QPlainTextEdit{background:#0F172A;color:#E2E8F0;font-family:Consolas;}")
        dock.setWidget(self.log_text)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _deferred_startup_initialize(self) -> None:
        """执行首次几何预览。"""

        if self._startup_initialized:
            return
        # 首次初始化必须等主窗口真正显示后再做，避免在原生窗口句柄尚未稳定时触发 VTK 渲染。
        if not self.isVisible():
            QTimer.singleShot(120, self._deferred_startup_initialize)
            return
        self._startup_initialized = True
        write_startup_trace("deferred startup initialization begin")
        try:
            self._initialize_viewport()
            if SKIP_AUTO_PREVIEW:
                write_startup_trace("skip automatic geometry preview by environment flag")
            else:
                write_startup_trace("begin automatic geometry preview")
                self.refresh_geometry_preview()
                write_startup_trace("automatic geometry preview finished")
        except Exception as exc:
            write_startup_trace(f"deferred startup initialization failed: {exc}")
            self.log(f"启动阶段几何预览初始化失败：{exc}")

    def _task_running(self) -> bool:
        return self._busy_thread is not None and self._busy_thread.isRunning()

    def _set_busy_state(self, busy: bool, message: str = "") -> None:
        """统一切换界面的忙碌状态。"""

        if self.main_toolbar is not None:
            self.main_toolbar.setEnabled(not busy)
        if self.view_toolbar is not None:
            self.view_toolbar.setEnabled(not busy)
        self.project_tree.setEnabled(not busy)
        self.property_stack.setEnabled(not busy)
        self.menuBar().setEnabled(not busy)
        if busy:
            self.statusBar().showMessage(message)
            self.setCursor(Qt.CursorShape.WaitCursor)
        else:
            self.statusBar().clearMessage()
            self.unsetCursor()

    def _start_background_task(
        self,
        task_name: str,
        busy_message: str,
        task_callable: Callable[[], object],
        success_handler: Callable[[object], None],
    ) -> bool:
        """启动一个后台线程任务。"""

        if self._task_running():
            QMessageBox.information(self, "任务仍在执行", f"当前正在执行“{self._busy_task_name}”，请等待完成后再继续。")
            return False

        self._busy_task_name = task_name
        self._set_busy_state(True, busy_message)
        thread = QThread(self)
        worker = AsyncTaskWorker(task_name, task_callable)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda _name, result: self._on_background_task_finished(success_handler, result))
        worker.failed.connect(self._on_background_task_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(lambda _name, _trace: thread.quit())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._busy_thread = thread
        self._busy_worker = worker
        thread.start()
        return True

    def _on_background_task_finished(self, success_handler: Callable[[object], None], result: object) -> None:
        """后台任务成功完成后的统一收尾。"""

        try:
            success_handler(result)
        finally:
            self._busy_task_name = ""
            self._busy_thread = None
            self._busy_worker = None
            self._set_busy_state(False)

    def _on_background_task_failed(self, task_name: str, traceback_text: str) -> None:
        """后台任务失败后的统一收尾与报错展示。"""

        self._busy_task_name = ""
        self._busy_thread = None
        self._busy_worker = None
        self._set_busy_state(False)
        self.log(f"{task_name}失败：\n{traceback_text}")
        QMessageBox.critical(self, f"{task_name}失败", traceback_text.splitlines()[-1] if traceback_text.strip() else "未知错误")

    def _make_geometry_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        geo = QGroupBox("几何建模")
        form = QFormLayout(geo)

        self.lbl_cad_status = QLabel("当前使用：参数化几何")
        self.combo_primitive = QComboBox()
        for label, value in PRIMITIVES:
            self.combo_primitive.addItem(label, value)

        self.spin_length = self._make_double(0.01, 100.0, 4)
        self.spin_width = self._make_double(0.01, 100.0, 4)
        self.spin_height = self._make_double(0.001, 100.0, 4)
        self.spin_radius = self._make_double(0.001, 100.0, 4)
        self.spin_hole_radius = self._make_double(0.001, 100.0, 4)

        self.label_length = QLabel("长度 X [m]：")
        self.label_width = QLabel("宽度 Y [m]：")
        self.label_height = QLabel("高度 Z [m]：")
        self.label_radius = QLabel("半径 [m]：")
        self.label_hole_radius = QLabel("孔半径 [m]：")

        form.addRow("几何来源：", self.lbl_cad_status)
        form.addRow("参数化类型：", self.combo_primitive)
        form.addRow(self.label_length, self.spin_length)
        form.addRow(self.label_width, self.spin_width)
        form.addRow(self.label_height, self.spin_height)
        form.addRow(self.label_radius, self.spin_radius)
        form.addRow(self.label_hole_radius, self.spin_hole_radius)

        self.geometry_rows = {"length": (self.label_length, self.spin_length), "width": (self.label_width, self.spin_width), "height": (self.label_height, self.spin_height), "radius": (self.label_radius, self.spin_radius), "hole_radius": (self.label_hole_radius, self.spin_hole_radius)}
        self.combo_primitive.currentIndexChanged.connect(self._update_geometry_form_state)

        for text, slot in [("导入 CAD", self.import_cad), ("恢复参数化", self.reset_to_parametric), ("刷新几何预览", self.refresh_geometry_preview)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            layout.addWidget(button)
        layout.insertWidget(0, geo)
        layout.addStretch()
        return page

    def _make_material_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("材料参数")
        form = QFormLayout(group)
        self.combo_active_material = QComboBox()
        self.combo_active_material.currentIndexChanged.connect(self._material_selection_changed)
        self.combo_material = QComboBox()
        self.combo_material.addItems(["结构钢", "铝合金", "混凝土"])
        self.combo_material.currentTextChanged.connect(self.apply_material_preset)
        self.combo_material_name = QComboBox()
        self.combo_material_name.setEditable(True)
        self.spin_E = self._make_double(1.0e6, 1.0e13, 0)
        self.spin_nu = self._make_double(0.01, 0.49, 4)
        self.spin_density = self._make_double(1.0, 50000.0, 2)
        self.check_material_nonlinear = QCheckBox("启用材料非线性")
        self.spin_yield_strength = self._make_double(1.0e5, 1.0e10, 0)
        self.spin_hardening_modulus = self._make_double(0.0, 1.0e12, 0)
        self.check_material_nonlinear.toggled.connect(self._material_nonlinear_toggled)
        form.addRow("当前材料：", self.combo_active_material)
        form.addRow("材料模板：", self.combo_material)
        form.addRow("材料名称：", self.combo_material_name)
        form.addRow("杨氏模量 E [Pa]：", self.spin_E)
        form.addRow("泊松比 ν：", self.spin_nu)
        form.addRow("密度 ρ [kg/m³]：", self.spin_density)
        form.addRow(self.check_material_nonlinear)
        form.addRow("屈服强度 [Pa]：", self.spin_yield_strength)
        form.addRow("硬化模量 [Pa]：", self.spin_hardening_modulus)
        layout.addWidget(group)
        row = QHBoxLayout()
        for text, slot in [("新增材料", self.add_material_item), ("删除材料", self.remove_material_item), ("应用材料设置", self.apply_material_settings)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        layout.addStretch()
        self._connect_material_dirty_signals()
        return page

    def _make_load_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        load_group = QGroupBox("荷载工况")
        load_form = QFormLayout(load_group)
        self.combo_active_loadcase = QComboBox()
        self.combo_active_loadcase.currentIndexChanged.connect(self._loadcase_selection_changed)
        self.lbl_loaded_face = QLabel("未选择")
        self.lbl_loaded_face.setWordWrap(True)
        self.spin_force_x = self._make_double(-1.0e9, 1.0e9, 2)
        self.spin_force_y = self._make_double(-1.0e9, 1.0e9, 2)
        self.spin_force_z = self._make_double(-1.0e9, 1.0e9, 2)
        self.spin_tolerance_ratio = self._make_double(0.001, 0.2, 4)
        load_form.addRow("当前荷载：", self.combo_active_loadcase)
        load_form.addRow("受载端面：", self.lbl_loaded_face)
        load_form.addRow("总载荷 Fx [N]：", self.spin_force_x)
        load_form.addRow("总载荷 Fy [N]：", self.spin_force_y)
        load_form.addRow("总载荷 Fz [N]：", self.spin_force_z)
        load_form.addRow("端面识别容差：", self.spin_tolerance_ratio)
        layout.addWidget(load_group)
        row_manage = QHBoxLayout()
        for text, slot in [("新增荷载工况", self.add_loadcase_item), ("删除荷载工况", self.remove_loadcase_item), ("应用荷载设置", self.apply_loadcase_settings)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            row_manage.addWidget(button)
        layout.addLayout(row_manage)
        row_pick = QHBoxLayout()
        for text, slot in [("图形选择受载面", lambda: self.start_boundary_face_pick("load"))]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            row_pick.addWidget(button)
        layout.addLayout(row_pick)

        boundary_group = QGroupBox("边界条件")
        boundary_form = QFormLayout(boundary_group)
        self.combo_active_boundary = QComboBox()
        self.combo_active_boundary.currentIndexChanged.connect(self._boundary_selection_changed)
        self.lbl_boundary_face = QLabel("未选择")
        self.lbl_boundary_face.setWordWrap(True)
        self.check_boundary_x = QCheckBox("约束 X 位移")
        self.check_boundary_y = QCheckBox("约束 Y 位移")
        self.check_boundary_z = QCheckBox("约束 Z 位移")
        self.spin_boundary_dx = self._make_double(-1.0, 1.0, 6)
        self.spin_boundary_dy = self._make_double(-1.0, 1.0, 6)
        self.spin_boundary_dz = self._make_double(-1.0, 1.0, 6)
        boundary_form.addRow("当前边界：", self.combo_active_boundary)
        boundary_form.addRow("约束端面：", self.lbl_boundary_face)
        boundary_form.addRow(self.check_boundary_x)
        boundary_form.addRow("Ux [m]：", self.spin_boundary_dx)
        boundary_form.addRow(self.check_boundary_y)
        boundary_form.addRow("Uy [m]：", self.spin_boundary_dy)
        boundary_form.addRow(self.check_boundary_z)
        boundary_form.addRow("Uz [m]：", self.spin_boundary_dz)
        layout.addWidget(boundary_group)

        row_boundary_manage = QHBoxLayout()
        for text, slot in [("新增边界条件", self.add_boundary_item), ("删除边界条件", self.remove_boundary_item), ("应用边界设置", self.apply_boundary_settings)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            row_boundary_manage.addWidget(button)
        layout.addLayout(row_boundary_manage)

        row_boundary_pick = QHBoxLayout()
        button_pick_boundary = QPushButton("图形选择约束面")
        button_pick_boundary.clicked.connect(lambda: self.start_boundary_face_pick("boundary"))
        row_boundary_pick.addWidget(button_pick_boundary)
        layout.addLayout(row_boundary_pick)
        layout.addStretch()
        self._connect_load_dirty_signals()
        self._connect_boundary_dirty_signals()
        return page

    def _make_thermal_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        material_group = QGroupBox("热材料参数")
        material_form = QFormLayout(material_group)
        self.spin_thermal_conductivity = self._make_double(0.001, 1.0e6, 4)
        material_form.addRow("导热系数 k [W/(m·K)]：", self.spin_thermal_conductivity)
        layout.addWidget(material_group)

        boundary_group = QGroupBox("固定温度边界")
        boundary_form = QFormLayout(boundary_group)
        self.lbl_thermal_boundary_face = QLabel("未选择")
        self.lbl_thermal_boundary_face.setWordWrap(True)
        self.spin_boundary_temperature = self._make_double(-273.15, 5000.0, 3)
        boundary_form.addRow("固定温度表面：", self.lbl_thermal_boundary_face)
        boundary_form.addRow("固定温度 [°C]：", self.spin_boundary_temperature)
        layout.addWidget(boundary_group)
        boundary_note = QLabel("说明：稳态热传导中的温度边界表示该表面温度被固定，不是整个实体的初始温度场。当前版本暂不单独设置初始温度场。")
        boundary_note.setWordWrap(True)
        boundary_note.setStyleSheet("color:#5b6b7a;")
        layout.addWidget(boundary_note)

        boundary_row = QHBoxLayout()
        for text, slot in [("图形选择温度边界", lambda: self.start_boundary_face_pick("thermal_boundary")), ("应用温度边界", self.apply_thermal_boundary_settings)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            boundary_row.addWidget(button)
        layout.addLayout(boundary_row)

        load_group = QGroupBox("热流载荷")
        load_form = QFormLayout(load_group)
        self.lbl_thermal_load_face = QLabel("未选择")
        self.lbl_thermal_load_face.setWordWrap(True)
        self.spin_heat_power = self._make_double(-1.0e9, 1.0e9, 3)
        load_form.addRow("热流表面：", self.lbl_thermal_load_face)
        load_form.addRow("总热流 [W]：", self.spin_heat_power)
        layout.addWidget(load_group)

        load_row = QHBoxLayout()
        for text, slot in [("图形选择热流面", lambda: self.start_boundary_face_pick("thermal_load")), ("应用热流载荷", self.apply_thermal_load_settings)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            load_row.addWidget(button)
        layout.addLayout(load_row)
        layout.addStretch()
        self._connect_thermal_dirty_signals()
        return page

    def _make_mesh_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("网格控制")
        form = QFormLayout(group)
        self.spin_mesh_size = self._make_double(0.001, 5.0, 4)
        self.combo_mesh_topology = QComboBox()
        self.combo_algorithm_2d = QComboBox()
        self.combo_algorithm_3d = QComboBox()
        for label, value in MESH_TOPOLOGIES:
            self.combo_mesh_topology.addItem(label, value)
        for label, value in ALG2D:
            self.combo_algorithm_2d.addItem(label, value)
        for label, value in ALG3D:
            self.combo_algorithm_3d.addItem(label, value)
        self.spin_element_order = QSpinBox()
        self.spin_element_order.setRange(1, 2)
        self.check_optimize_mesh = QCheckBox("启用 Gmsh 网格优化")
        self.check_local_refine = QCheckBox("启用局部加密")
        self.spin_local_refine_size = self._make_double(0.001, 5.0, 4)
        self.spin_local_refine_radius = self._make_double(0.001, 5.0, 4)
        form.addRow("网格拓扑：", self.combo_mesh_topology)
        form.addRow("全局网格尺寸：", self.spin_mesh_size)
        form.addRow("2D 网格算法：", self.combo_algorithm_2d)
        form.addRow("3D 网格算法：", self.combo_algorithm_3d)
        form.addRow("单元阶次：", self.spin_element_order)
        form.addRow(self.check_optimize_mesh)
        form.addRow(self.check_local_refine)
        form.addRow("局部加密尺寸：", self.spin_local_refine_size)
        form.addRow("局部加密半径：", self.spin_local_refine_radius)
        layout.addWidget(group)
        for text, slot in [("生成体网格", self.generate_mesh), ("检查网格质量", self.check_mesh_quality), ("导出网格", self.export_mesh)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            layout.addWidget(button)
        layout.addStretch()
        return page

    def _make_solver_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("分析步与求解器")
        form = QFormLayout(group)
        self.combo_analysis_type = QComboBox()
        self.combo_linear_solver = QComboBox()
        for label, value in ANALYSIS_TYPES:
            self.combo_analysis_type.addItem(label, value)
        self.spin_load_steps = QSpinBox()
        self.spin_load_steps.setRange(1, 500)
        self.spin_max_iterations = QSpinBox()
        self.spin_max_iterations.setRange(1, 1000)
        self.spin_tolerance = self._make_double(1.0e-12, 1.0, 10)
        self.spin_modal_count = QSpinBox()
        self.spin_modal_count.setRange(1, 50)
        self.spin_total_time = self._make_double(1.0e-5, 100.0, 5)
        self.spin_time_step = self._make_double(1.0e-6, 10.0, 6)
        self.spin_newmark_beta = self._make_double(0.01, 1.0, 4)
        self.spin_newmark_gamma = self._make_double(0.01, 1.0, 4)
        self.spin_rayleigh_alpha = self._make_double(0.0, 1.0e4, 6)
        self.spin_rayleigh_beta = self._make_double(0.0, 1.0, 8)
        self.spin_frequency_start = self._make_double(0.01, 1.0e6, 3)
        self.spin_frequency_end = self._make_double(0.01, 1.0e6, 3)
        self.spin_frequency_points = QSpinBox()
        self.spin_frequency_points.setRange(2, 2000)
        self.combo_damping_mode = QComboBox()
        for label, value in DAMPING_MODE_ITEMS:
            self.combo_damping_mode.addItem(label, value)
        self.spin_modal_damping_ratio = self._make_double(0.0, 1.0, 5)
        self.spin_modal_damping_freq1 = self._make_double(0.01, 1.0e6, 3)
        self.spin_modal_damping_freq2 = self._make_double(0.01, 1.0e6, 3)
        self.spin_warp = self._make_double(1.0, 100000.0, 1)
        self.spin_warp.valueChanged.connect(lambda value: self._sync_warp_controls(value, source="solver"))
        self.check_parallel = QCheckBox("启用 CPU 并行装配与后处理（OpenMP）")
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 256)
        self.spin_threads.setToolTip("这里填写 CPU 逻辑线程数。当前版本只支持 CPU OpenMP 并行，不使用 CUDA GPU 或 MPI。")
        self.label_load_steps = QLabel("载荷步数：")
        self.label_max_iterations = QLabel("最大迭代数：")
        self.label_tolerance = QLabel("收敛容差：")
        self.label_modal_count = QLabel("模态阶数：")
        self.label_total_time = QLabel("总时长 [s]：")
        self.label_time_step = QLabel("时间步长 [s]：")
        self.label_newmark_beta = QLabel("Newmark β：")
        self.label_newmark_gamma = QLabel("Newmark γ：")
        self.label_rayleigh_alpha = QLabel("Rayleigh α：")
        self.label_rayleigh_beta = QLabel("Rayleigh β：")
        self.label_frequency_start = QLabel("起始频率 [Hz]：")
        self.label_frequency_end = QLabel("终止频率 [Hz]：")
        self.label_frequency_points = QLabel("频率点数：")
        self.label_damping_mode = QLabel("阻尼模式：")
        self.label_modal_damping_ratio = QLabel("目标阻尼比 ζ：")
        self.label_modal_damping_freq1 = QLabel("参考频率 f1 [Hz]：")
        self.label_modal_damping_freq2 = QLabel("参考频率 f2 [Hz]：")
        self.label_rayleigh_alpha.setText("Rayleigh α：")
        self.label_rayleigh_beta.setText("Rayleigh β：")
        self.label_linear_solver = QLabel("线性求解器：")
        self.label_solver_hint = QLabel()
        self.label_solver_hint.setWordWrap(True)
        self.label_solver_hint.setStyleSheet("color: #5b6b7a;")
        self.combo_analysis_type.currentIndexChanged.connect(self._update_solver_form_state)
        self.combo_damping_mode.currentIndexChanged.connect(self._update_solver_form_state)
        form.addRow("分析类型：", self.combo_analysis_type)
        form.addRow(self.label_linear_solver, self.combo_linear_solver)
        form.addRow(self.label_load_steps, self.spin_load_steps)
        form.addRow(self.label_max_iterations, self.spin_max_iterations)
        form.addRow(self.label_tolerance, self.spin_tolerance)
        form.addRow(self.label_modal_count, self.spin_modal_count)
        form.addRow(self.label_total_time, self.spin_total_time)
        form.addRow(self.label_time_step, self.spin_time_step)
        form.addRow(self.label_newmark_beta, self.spin_newmark_beta)
        form.addRow(self.label_newmark_gamma, self.spin_newmark_gamma)
        form.addRow(self.label_frequency_start, self.spin_frequency_start)
        form.addRow(self.label_frequency_end, self.spin_frequency_end)
        form.addRow(self.label_frequency_points, self.spin_frequency_points)
        form.addRow(self.label_damping_mode, self.combo_damping_mode)
        form.addRow(self.label_modal_damping_ratio, self.spin_modal_damping_ratio)
        form.addRow(self.label_modal_damping_freq1, self.spin_modal_damping_freq1)
        form.addRow(self.label_modal_damping_freq2, self.spin_modal_damping_freq2)
        form.addRow(self.label_rayleigh_alpha, self.spin_rayleigh_alpha)
        form.addRow(self.label_rayleigh_beta, self.spin_rayleigh_beta)
        form.addRow("变形放大倍数：", self.spin_warp)
        form.addRow(self.check_parallel)
        form.addRow("CPU线程数（逻辑线程）：", self.spin_threads)
        form.addRow("求解说明：", self.label_solver_hint)
        layout.addWidget(group)
        button = QPushButton("运行分析")
        button.clicked.connect(self.run_analysis_async)
        layout.addWidget(button)
        layout.addStretch()
        self._connect_solver_dirty_signals()
        self._refresh_solver_options_for_analysis("linear_static")
        return page

    def _make_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("结果显示")
        form = QFormLayout(group)
        self.combo_result_mode = QComboBox()
        self.combo_result_mode.currentIndexChanged.connect(self._result_mode_changed)
        self._refresh_result_modes_for_analysis("linear_static")
        self.combo_slice_axis = QComboBox()
        for label, value in SLICE_AXES:
            self.combo_slice_axis.addItem(label, value)
        self.combo_curve_response = QComboBox()
        for label, value in CURVE_RESPONSE_ITEMS:
            self.combo_curve_response.addItem(label, value)
        self.spin_result_warp = self._make_double(1.0, 100000.0, 1)
        self.spin_result_warp.valueChanged.connect(lambda value: self._sync_warp_controls(value, source="result"))
        self.spin_contour_count = QSpinBox()
        self.spin_contour_count.setRange(2, 200)
        self.spin_contour_count.setValue(8)
        self.spin_contour_count.setToolTip("等值面数量可在 2 到 200 之间调整。切片不是数量控制，切片始终是单一平面，通过下方百分比连续调节位置。")
        self.spin_mode_index = QSpinBox()
        self.spin_mode_index.setRange(1, 1)
        self.spin_slice_ratio = self._make_double(0.0, 100.0, 1)
        self.spin_slice_ratio.setSuffix(" %")
        self.check_slice_deformed = QCheckBox("在变形结果上切片")
        self.check_slice_deformed.setChecked(True)
        form.addRow("云图类型：", self.combo_result_mode)
        form.addRow("结果变形倍数：", self.spin_result_warp)
        form.addRow("等值面数量（2-200）：", self.spin_contour_count)
        self.label_mode_index = QLabel("模态阶次：")
        form.addRow(self.label_mode_index, self.spin_mode_index)
        self.label_curve_response = QLabel("曲线响应量：")
        form.addRow(self.label_curve_response, self.combo_curve_response)
        form.addRow("切片方向：", self.combo_slice_axis)
        form.addRow("切片位置：", self.spin_slice_ratio)
        form.addRow(self.check_slice_deformed)
        layout.addWidget(group)
        self.button_show_mode = QPushButton("显示选定模态")
        self.button_show_mode.clicked.connect(self.show_selected_mode)
        layout.addWidget(self.button_show_mode)
        self.button_dynamic_history = QPushButton("查看动力响应摘要")
        self.button_dynamic_history.clicked.connect(self.open_response_history_window)
        layout.addWidget(self.button_dynamic_history)
        self.button_export_history = QPushButton("导出动力曲线 CSV")
        self.button_export_history.clicked.connect(self.export_response_history_csv)
        layout.addWidget(self.button_export_history)
        self.button_frequency_plot = QPushButton("查看频响曲线")
        self.button_frequency_plot.clicked.connect(self.open_frequency_response_plot)
        layout.addWidget(self.button_frequency_plot)
        self.button_export_frequency = QPushButton("导出频响曲线 CSV")
        self.button_export_frequency.clicked.connect(self.export_frequency_response_csv)
        layout.addWidget(self.button_export_frequency)
        self.button_toggle_probe = QPushButton("开启结果探针")
        self.button_toggle_probe.clicked.connect(self.toggle_result_probe)
        layout.addWidget(self.button_toggle_probe)
        self.button_result_table = QPushButton("查看结果数据表")
        self.button_result_table.clicked.connect(self.open_result_table_window)
        layout.addWidget(self.button_result_table)
        self.button_export_report = QPushButton("导出分析报告")
        self.button_export_report.clicked.connect(self.export_analysis_report)
        layout.addWidget(self.button_export_report)
        for text, slot in [("显示整体结果", self.restore_full_result), ("显示切片", self.show_result_slice), ("显示等值面", self.show_result_contours), ("更新变形显示", self.restore_full_result), ("导出 CSV", self.export_results_csv), ("导出 VTU", self.export_results_vtu)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            layout.addWidget(button)
        layout.addStretch()
        self._update_result_form_state()
        return page

    def _make_view_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("视图控制")
        form = QFormLayout(group)
        self.check_show_axes = QCheckBox("显示坐标轴")
        self.check_show_axes.setChecked(True)
        self.check_show_axes.toggled.connect(self._sync_axes_check)
        self.combo_background = QComboBox()
        self.combo_background.addItems(["工程蓝灰", "浅色背景", "深色背景"])
        self.combo_background.currentTextChanged.connect(self._apply_view_preferences)
        form.addRow(self.check_show_axes)
        form.addRow("背景主题：", self.combo_background)
        layout.addWidget(group)
        row = QHBoxLayout()
        for text, slot in [("等轴", self.view_isometric), ("前视", self.view_front), ("右视", self.view_right), ("俯视", self.view_top), ("适配", self.reset_active_camera)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        layout.addStretch()
        return page

    def _install_stdout_redirector(self) -> None:
        self.emitter = SignalEmitter()
        self.emitter.text_written.connect(self.log_text.insertPlainText)
        sys.stdout = StdoutRedirector(self.emitter)

    def _sync_axes_check(self) -> None:
        self.action_toggle_axes.setChecked(self.check_show_axes.isChecked())
        self._apply_view_preferences()

    def _update_geometry_form_state(self) -> None:
        if not self.geometry_rows:
            return
        visible = {"box": {"length", "width", "height"}, "cylinder": {"length", "radius"}, "sphere": {"radius"}, "plate_with_hole": {"length", "width", "height", "hole_radius"}}.get(str(self.combo_primitive.currentData()), {"length", "width", "height"})
        for key, (label, widget) in self.geometry_rows.items():
            label.setVisible(key in visible)
            widget.setVisible(key in visible)

    def _update_solver_form_state(self) -> None:
        analysis_type = str(self.combo_analysis_type.currentData())
        self._refresh_solver_options_for_analysis(analysis_type)
        nonlinear = analysis_type == "nonlinear_static"
        thermal = analysis_type == "steady_state_thermal"
        modal = analysis_type == "modal_analysis"
        transient = analysis_type == "transient_dynamic"
        frequency = analysis_type == "frequency_response"
        dynamic = transient or frequency
        damping_mode = str(self.combo_damping_mode.currentData())
        use_direct_rayleigh = dynamic and damping_mode == "rayleigh"
        use_modal_ratio = dynamic and damping_mode == "modal_ratio"
        for label, widget in [(self.label_load_steps, self.spin_load_steps), (self.label_max_iterations, self.spin_max_iterations), (self.label_tolerance, self.spin_tolerance)]:
            label.setVisible(nonlinear)
            widget.setVisible(nonlinear)
        self.label_modal_count.setVisible(modal)
        self.spin_modal_count.setVisible(modal)
        for label, widget in [
            (self.label_total_time, self.spin_total_time),
            (self.label_time_step, self.spin_time_step),
            (self.label_newmark_beta, self.spin_newmark_beta),
            (self.label_newmark_gamma, self.spin_newmark_gamma),
        ]:
            label.setVisible(transient)
            widget.setVisible(transient)
        for label, widget in [
            (self.label_frequency_start, self.spin_frequency_start),
            (self.label_frequency_end, self.spin_frequency_end),
            (self.label_frequency_points, self.spin_frequency_points),
        ]:
            label.setVisible(frequency)
            widget.setVisible(frequency)
        for label, widget in [
            (self.label_damping_mode, self.combo_damping_mode),
            (self.label_modal_damping_ratio, self.spin_modal_damping_ratio),
            (self.label_modal_damping_freq1, self.spin_modal_damping_freq1),
            (self.label_modal_damping_freq2, self.spin_modal_damping_freq2),
            (self.label_rayleigh_alpha, self.spin_rayleigh_alpha),
            (self.label_rayleigh_beta, self.spin_rayleigh_beta),
        ]:
            label.setVisible(dynamic)
            widget.setVisible(dynamic)
        self.label_modal_damping_ratio.setVisible(use_modal_ratio)
        self.spin_modal_damping_ratio.setVisible(use_modal_ratio)
        self.label_modal_damping_freq1.setVisible(use_modal_ratio)
        self.spin_modal_damping_freq1.setVisible(use_modal_ratio)
        self.label_modal_damping_freq2.setVisible(use_modal_ratio)
        self.spin_modal_damping_freq2.setVisible(use_modal_ratio)
        self.label_rayleigh_alpha.setVisible(use_direct_rayleigh)
        self.spin_rayleigh_alpha.setVisible(use_direct_rayleigh)
        self.label_rayleigh_beta.setVisible(use_direct_rayleigh)
        self.spin_rayleigh_beta.setVisible(use_direct_rayleigh)
        self.spin_warp.setEnabled(not thermal)

    def _refresh_solver_options_for_analysis(self, analysis_type: str, preferred_value: Optional[str] = None) -> None:
        """按分析步刷新求解器选项，避免出现无法求解的组合。"""

        current_value = preferred_value or str(self.combo_linear_solver.currentData() or "")
        normalized_value = normalize_solver_for_analysis(analysis_type, current_value)
        options = get_solver_options_for_analysis(analysis_type)
        self.label_linear_solver.setText(get_solver_caption_for_analysis(analysis_type))
        self.label_solver_hint.setText(
            get_solver_hint_for_analysis(analysis_type)
            + "\n当前并行仅支持 CPU OpenMP 装配与后处理；尚未接入 CUDA GPU 求解或 MPI 分布式求解。"
        )
        self.combo_linear_solver.blockSignals(True)
        self.combo_linear_solver.clear()
        for label, value in options:
            self.combo_linear_solver.addItem(label, value)
        self._set_combo_by_data(self.combo_linear_solver, normalized_value)
        self.combo_linear_solver.setEnabled(len(options) > 1)
        self.combo_linear_solver.blockSignals(False)

    def _refresh_result_modes_for_analysis(self, analysis_type: str, preferred_value: Optional[str] = None) -> None:
        """按分析步刷新结果模式选项。"""

        if analysis_type == "steady_state_thermal":
            options = [("温度", "temperature"), ("热流密度", "heat_flux")]
            fallback = "temperature"
        else:
            options = [("位移", "displacement"), ("应力", "stress"), ("应变", "strain")]
            fallback = "stress"
        current_value = preferred_value or str(self.combo_result_mode.currentData() or fallback)
        allowed_values = [value for _label, value in options]
        if current_value not in allowed_values:
            current_value = fallback
        self.combo_result_mode.blockSignals(True)
        self.combo_result_mode.clear()
        for label, value in options:
            self.combo_result_mode.addItem(label, value)
        self._set_combo_by_data(self.combo_result_mode, current_value)
        self.combo_result_mode.blockSignals(False)

    def _update_result_form_state(self) -> None:
        """按当前分析类型刷新结果页控件可见性。"""

        self._refresh_result_modes_for_analysis(self.state.solver.analysis_type, self.current_result_mode)
        modal_available = bool(self.analysis and self.analysis.modal_shapes)
        transient_available = bool(
            self.analysis and
            self.analysis.transient_times is not None and
            self.analysis.transient_max_displacements is not None and
            len(self.analysis.transient_times) > 0
        )
        frequency_available = bool(
            self.analysis and
            self.analysis.frequency_response_frequencies_hz is not None and
            self.analysis.frequency_response_max_displacements is not None and
            len(self.analysis.frequency_response_frequencies_hz) > 0
        )
        curve_available = transient_available or frequency_available
        result_available = bool(self.analysis)
        self.label_mode_index.setVisible(modal_available)
        self.spin_mode_index.setVisible(modal_available)
        self.button_show_mode.setVisible(modal_available)
        self.label_curve_response.setVisible(curve_available)
        self.combo_curve_response.setVisible(curve_available)
        self.button_dynamic_history.setVisible(transient_available)
        self.button_export_history.setVisible(transient_available)
        self.button_frequency_plot.setVisible(frequency_available)
        self.button_export_frequency.setVisible(frequency_available)
        self.button_result_table.setVisible(result_available)
        self.button_export_report.setVisible(result_available)
        self.button_toggle_probe.setVisible(result_available)
        self.button_toggle_probe.setText("关闭结果探针" if self.result_probe_enabled else "开启结果探针")

    def _material_nonlinear_toggled(self) -> None:
        enabled = self.check_material_nonlinear.isChecked()
        self.spin_yield_strength.setEnabled(enabled)
        self.spin_hardening_modulus.setEnabled(enabled)

    def _sync_warp_controls(self, value: float, source: str) -> None:
        """保持求解页和结果页的变形倍数输入一致。"""

        if source == "solver" and hasattr(self, "spin_result_warp"):
            self.spin_result_warp.blockSignals(True)
            self.spin_result_warp.setValue(value)
            self.spin_result_warp.blockSignals(False)
        elif source == "result":
            self.spin_warp.blockSignals(True)
            self.spin_warp.setValue(value)
            self.spin_warp.blockSignals(False)

    def _surface_label(self, surface_key: str) -> str:
        """返回当前表面键对应的人类可读名称。"""

        if self.mesh_bundle and surface_key in self.mesh_bundle.surface_labels:
            return self.mesh_bundle.surface_labels[surface_key]
        if self.preview_scene and surface_key in self.preview_scene.surface_labels:
            return self.preview_scene.surface_labels[surface_key]
        return FACE_LABELS.get(surface_key, "未选择")

    def _get_current_surface_patches(self) -> tuple[dict[str, pv.PolyData], dict[str, str]]:
        """返回当前场景可用的真实表面补丁。"""

        if self.mesh_bundle is not None:
            return self.mesh_bundle.surface_patches, self.mesh_bundle.surface_labels
        if self.preview_scene is not None:
            return self.preview_scene.surface_patches, self.preview_scene.surface_labels
        return {}, {}

    def _prompt_name(self, title: str, label: str, default_value: str) -> Optional[str]:
        """弹出命名对话框，返回去除空白后的名称。"""

        text, accepted = QInputDialog.getText(self, title, label, text=default_value)
        cleaned = text.strip()
        if not accepted:
            return None
        if not cleaned:
            QMessageBox.warning(self, "名称无效", "名称不能为空。")
            return None
        return cleaned

    def _refresh_material_combo(self) -> None:
        self.combo_active_material.blockSignals(True)
        self.combo_active_material.clear()
        for material in self.state.material_library:
            self.combo_active_material.addItem(material.name, material.material_id)
        self._set_combo_by_data(self.combo_active_material, self.state.active_material_id)
        self.combo_active_material.blockSignals(False)

    def _refresh_loadcase_combo(self) -> None:
        self.combo_active_loadcase.blockSignals(True)
        self.combo_active_loadcase.clear()
        for load_case in self.state.load_cases:
            self.combo_active_loadcase.addItem(load_case.name, load_case.loadcase_id)
        self._set_combo_by_data(self.combo_active_loadcase, self.state.active_loadcase_id)
        self.combo_active_loadcase.blockSignals(False)

    def _refresh_boundary_combo(self) -> None:
        self.combo_active_boundary.blockSignals(True)
        self.combo_active_boundary.clear()
        for boundary in self.state.boundary_conditions:
            self.combo_active_boundary.addItem(boundary.name, boundary.boundary_id)
        self._set_combo_by_data(self.combo_active_boundary, self.state.active_boundary_id)
        self.combo_active_boundary.blockSignals(False)

    def _material_selection_changed(self) -> None:
        if self.combo_active_material.currentData() is None:
            return
        material_id = int(self.combo_active_material.currentData())
        self._sync_state_from_widgets()
        self.state.set_active_material(material_id)
        self._sync_widgets_from_state()

    def _loadcase_selection_changed(self) -> None:
        if self.combo_active_loadcase.currentData() is None:
            return
        loadcase_id = int(self.combo_active_loadcase.currentData())
        self._sync_state_from_widgets()
        self.state.set_active_loadcase(loadcase_id)
        self._sync_widgets_from_state()

    def _boundary_selection_changed(self) -> None:
        if self.combo_active_boundary.currentData() is None:
            return
        boundary_id = int(self.combo_active_boundary.currentData())
        self._sync_state_from_widgets()
        self.state.set_active_boundary(boundary_id)
        self._sync_widgets_from_state()

    def _connect_material_dirty_signals(self) -> None:
        """材料参数改动后，要求用户重新确认应用。"""

        self.combo_material_name.currentTextChanged.connect(self._mark_material_dirty)
        self.spin_E.valueChanged.connect(self._mark_material_dirty)
        self.spin_nu.valueChanged.connect(self._mark_material_dirty)
        self.spin_density.valueChanged.connect(self._mark_material_dirty)
        if hasattr(self, "spin_thermal_conductivity"):
            self.spin_thermal_conductivity.valueChanged.connect(self._mark_material_dirty)
        self.check_material_nonlinear.toggled.connect(self._mark_material_dirty)
        self.spin_yield_strength.valueChanged.connect(self._mark_material_dirty)
        self.spin_hardening_modulus.valueChanged.connect(self._mark_material_dirty)

    def _connect_load_dirty_signals(self) -> None:
        """荷载参数改动后，要求用户重新确认应用。"""

        self.spin_force_x.valueChanged.connect(self._mark_load_dirty)
        self.spin_force_y.valueChanged.connect(self._mark_load_dirty)
        self.spin_force_z.valueChanged.connect(self._mark_load_dirty)
        self.spin_tolerance_ratio.valueChanged.connect(self._mark_load_dirty)

    def _connect_boundary_dirty_signals(self) -> None:
        """边界参数改动后，要求用户重新确认应用。"""

        self.check_boundary_x.toggled.connect(self._mark_boundary_dirty)
        self.check_boundary_y.toggled.connect(self._mark_boundary_dirty)
        self.check_boundary_z.toggled.connect(self._mark_boundary_dirty)
        self.spin_boundary_dx.valueChanged.connect(self._mark_boundary_dirty)
        self.spin_boundary_dy.valueChanged.connect(self._mark_boundary_dirty)
        self.spin_boundary_dz.valueChanged.connect(self._mark_boundary_dirty)

    def _connect_thermal_dirty_signals(self) -> None:
        """热参数修改后，要求用户重新确认应用。"""

        self.spin_thermal_conductivity.valueChanged.connect(self._mark_thermal_material_dirty)
        self.spin_boundary_temperature.valueChanged.connect(self._mark_thermal_boundary_dirty)
        self.spin_heat_power.valueChanged.connect(self._mark_thermal_load_dirty)

    def _connect_solver_dirty_signals(self) -> None:
        """求解器页参数修改时只同步求解设置。"""

        self.combo_analysis_type.currentIndexChanged.connect(self._sync_state_from_widgets)
        self.combo_linear_solver.currentIndexChanged.connect(self._sync_state_from_widgets)
        self.spin_load_steps.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_max_iterations.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_tolerance.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_modal_count.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_total_time.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_time_step.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_newmark_beta.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_newmark_gamma.valueChanged.connect(self._sync_state_from_widgets)
        self.combo_damping_mode.currentIndexChanged.connect(self._sync_state_from_widgets)
        self.spin_rayleigh_alpha.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_rayleigh_beta.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_modal_damping_ratio.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_modal_damping_freq1.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_modal_damping_freq2.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_frequency_start.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_frequency_end.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_frequency_points.valueChanged.connect(self._sync_state_from_widgets)
        self.spin_warp.valueChanged.connect(self._sync_state_from_widgets)
        self.check_parallel.toggled.connect(self._sync_state_from_widgets)
        self.spin_threads.valueChanged.connect(self._sync_state_from_widgets)

    def _mark_material_dirty(self) -> None:
        if self._is_syncing_widgets:
            return
        self.state.material.is_applied = False
        self._sync_state_from_widgets()
        if self._current_module_index() == 1:
            self._draw_geometry_scene()

    def _mark_load_dirty(self) -> None:
        if self._is_syncing_widgets:
            return
        self.state.load_case.is_applied = False
        self._sync_state_from_widgets()
        if self._current_module_index() == 2:
            self._redraw_current_scene()

    def _mark_boundary_dirty(self) -> None:
        if self._is_syncing_widgets:
            return
        self.state.boundary_condition.is_applied = False
        self._sync_state_from_widgets()
        if self._current_module_index() == 2:
            self._redraw_current_scene()

    def _mark_thermal_material_dirty(self) -> None:
        if self._is_syncing_widgets:
            return
        self.state.material.is_applied = False
        self._sync_state_from_widgets()
        if self._current_module_index() == 1:
            self._draw_geometry_scene()

    def _mark_thermal_boundary_dirty(self) -> None:
        if self._is_syncing_widgets:
            return
        self.state.thermal_boundary.is_applied = False
        self._sync_state_from_widgets()
        if self._current_module_index() == 3:
            self._redraw_current_scene()

    def _mark_thermal_load_dirty(self) -> None:
        if self._is_syncing_widgets:
            return
        self.state.thermal_load.is_applied = False
        self._sync_state_from_widgets()
        if self._current_module_index() == 3:
            self._redraw_current_scene()

    def add_material_item(self) -> None:
        self._sync_state_from_widgets()
        next_id = max((material.material_id for material in self.state.material_library), default=0) + 1
        clone = self.state.material
        from app.models import MaterialConfig
        name = self._prompt_name("新增材料", "请输入材料名称：", clone.name if clone.name else "新材料")
        if name is None:
            return
        new_material = MaterialConfig(
            material_id=next_id,
            name=name,
            young_modulus=clone.young_modulus,
            poisson_ratio=clone.poisson_ratio,
            density=clone.density,
            thermal_conductivity=clone.thermal_conductivity,
            nonlinear_enabled=clone.nonlinear_enabled,
            yield_strength=clone.yield_strength,
            hardening_modulus=clone.hardening_modulus,
            region_xmin_ratio=0.0,
            region_xmax_ratio=1.0,
        )
        self.state.material_library.append(new_material)
        self.state.set_active_material(new_material.material_id)
        self._sync_widgets_from_state()

    def remove_material_item(self) -> None:
        if len(self.state.material_library) <= 1:
            QMessageBox.warning(self, "无法删除", "至少需要保留一个材料。")
            return
        active_id = self.state.active_material_id
        self.state.material_library = [material for material in self.state.material_library if material.material_id != active_id]
        self.state.set_active_material(self.state.material_library[0].material_id)
        self._sync_widgets_from_state()

    def add_loadcase_item(self) -> None:
        self._sync_state_from_widgets()
        next_id = max((load_case.loadcase_id for load_case in self.state.load_cases), default=0) + 1
        clone = self.state.load_case
        from app.models import LoadCaseConfig
        name = self._prompt_name("新增荷载工况", "请输入荷载工况名称：", clone.name if clone.name else "新荷载")
        if name is None:
            return
        new_loadcase = LoadCaseConfig(
            loadcase_id=next_id,
            name=name,
            force_x=clone.force_x,
            force_y=clone.force_y,
            force_z=clone.force_z,
            loaded_face=clone.loaded_face,
            boundary_tolerance_ratio=clone.boundary_tolerance_ratio,
        )
        self.state.load_cases.append(new_loadcase)
        self.state.set_active_loadcase(new_loadcase.loadcase_id)
        self._sync_widgets_from_state()

    def remove_loadcase_item(self) -> None:
        if len(self.state.load_cases) <= 1:
            QMessageBox.warning(self, "无法删除", "至少需要保留一个工况。")
            return
        active_id = self.state.active_loadcase_id
        self.state.load_cases = [load_case for load_case in self.state.load_cases if load_case.loadcase_id != active_id]
        self.state.set_active_loadcase(self.state.load_cases[0].loadcase_id)
        self._sync_widgets_from_state()

    def add_boundary_item(self) -> None:
        self._sync_state_from_widgets()
        next_id = max((boundary.boundary_id for boundary in self.state.boundary_conditions), default=0) + 1
        clone = self.state.boundary_condition
        from app.models import BoundaryConditionConfig

        name = self._prompt_name("新增边界条件", "请输入边界条件名称：", clone.name if clone.name else "新边界")
        if name is None:
            return
        new_boundary = BoundaryConditionConfig(
            boundary_id=next_id,
            name=name,
            target_face=clone.target_face,
            constrain_x=clone.constrain_x,
            constrain_y=clone.constrain_y,
            constrain_z=clone.constrain_z,
            displacement_x=clone.displacement_x,
            displacement_y=clone.displacement_y,
            displacement_z=clone.displacement_z,
        )
        self.state.boundary_conditions.append(new_boundary)
        self.state.set_active_boundary(new_boundary.boundary_id)
        self._sync_widgets_from_state()

    def remove_boundary_item(self) -> None:
        if len(self.state.boundary_conditions) <= 1:
            QMessageBox.warning(self, "无法删除", "至少需要保留一个边界条件。")
            return
        active_id = self.state.active_boundary_id
        self.state.boundary_conditions = [boundary for boundary in self.state.boundary_conditions if boundary.boundary_id != active_id]
        self.state.set_active_boundary(self.state.boundary_conditions[0].boundary_id)
        self._sync_widgets_from_state()

    def apply_material_settings(self) -> None:
        self._sync_state_from_widgets()
        self.state.material.is_applied = True
        self._sync_widgets_from_state()
        self._refresh_summary()
        if self._current_module_index() == 1:
            self._draw_geometry_scene()
        self.log(f"已应用材料设置：{self.state.material.name}")

    def apply_loadcase_settings(self) -> None:
        self._sync_state_from_widgets()
        if not self.state.load_case.loaded_face:
            QMessageBox.warning(self, "缺少受载面", "请先在图形界面中选择一个受载面。")
            return
        self.state.load_case.is_applied = True
        self._sync_widgets_from_state()
        self._refresh_summary()
        if self._current_module_index() == 2:
            self._redraw_current_scene()
        self.log(f"已应用荷载设置：{self.state.load_case.name}")

    def apply_boundary_settings(self) -> None:
        self._sync_state_from_widgets()
        if not self.state.boundary_condition.target_face:
            QMessageBox.warning(self, "缺少约束面", "请先在图形界面中选择一个约束面。")
            return
        self.state.boundary_condition.is_applied = True
        self._sync_widgets_from_state()
        self._refresh_summary()
        if self._current_module_index() == 2:
            self._redraw_current_scene()
        self.log(f"已应用边界条件：{self.state.boundary_condition.name}")

    def apply_thermal_boundary_settings(self) -> None:
        self._sync_state_from_widgets()
        if not self.state.thermal_boundary.target_face:
            QMessageBox.warning(self, "缺少固定温度边界", "请先在图形界面中选择一个固定温度表面。")
            return
        self.state.thermal_boundary.is_applied = True
        self._sync_widgets_from_state()
        self._refresh_summary()
        if self._current_module_index() == 3:
            self._redraw_current_scene()
        self.log(f"已应用温度边界：{self._surface_label(self.state.thermal_boundary.target_face)}")

    def apply_thermal_load_settings(self) -> None:
        self._sync_state_from_widgets()
        if self.state.thermal_load.heat_power != 0.0 and not self.state.thermal_load.target_face:
            QMessageBox.warning(self, "缺少热流表面", "请先在图形界面中选择一个热流作用表面。")
            return
        self.state.thermal_load.is_applied = True
        self._sync_widgets_from_state()
        self._refresh_summary()
        if self._current_module_index() == 3:
            self._redraw_current_scene()
        if self.state.thermal_load.target_face:
            self.log(f"已应用热流载荷：{self._surface_label(self.state.thermal_load.target_face)}")
        else:
            self.log("已应用热流载荷：当前为零热流输入。")

    def _sync_widgets_from_state(self) -> None:
        self.state.ensure_default_entities()
        self._is_syncing_widgets = True
        g, m, l, b, tb, tl, s = self.state.geometry, self.state.material, self.state.load_case, self.state.boundary_condition, self.state.thermal_boundary, self.state.thermal_load, self.state.solver
        try:
            self._refresh_material_combo()
            self._refresh_loadcase_combo()
            self._refresh_boundary_combo()
            self._set_combo_by_data(self.combo_primitive, g.primitive)
            self.spin_length.setValue(g.length)
            self.spin_width.setValue(g.width)
            self.spin_height.setValue(g.height)
            self.spin_radius.setValue(g.radius)
            self.spin_hole_radius.setValue(g.hole_radius)
            self._set_combo_by_data(self.combo_mesh_topology, g.mesh_topology)
            self.spin_mesh_size.setValue(g.mesh_size)
            self._set_combo_by_data(self.combo_algorithm_2d, g.algorithm_2d)
            self._set_combo_by_data(self.combo_algorithm_3d, g.algorithm_3d)
            self.spin_element_order.setValue(g.element_order)
            self.check_optimize_mesh.setChecked(g.optimize_mesh)
            self.check_local_refine.setChecked(g.local_refine_enabled)
            self.spin_local_refine_size.setValue(g.local_refine_size)
            self.spin_local_refine_radius.setValue(g.local_refine_radius)

            self.combo_material_name.setCurrentText(m.name)
            self.spin_E.setValue(m.young_modulus)
            self.spin_nu.setValue(m.poisson_ratio)
            self.spin_density.setValue(m.density)
            self.spin_thermal_conductivity.setValue(m.thermal_conductivity)
            self.check_material_nonlinear.setChecked(m.nonlinear_enabled)
            self.spin_yield_strength.setValue(m.yield_strength)
            self.spin_hardening_modulus.setValue(m.hardening_modulus)
            self.lbl_loaded_face.setText(self._surface_label(l.loaded_face) if l.loaded_face else "未选择")
            self.spin_force_x.setValue(l.force_x)
            self.spin_force_y.setValue(l.force_y)
            self.spin_force_z.setValue(l.force_z)
            self.spin_tolerance_ratio.setValue(l.boundary_tolerance_ratio)
            self.lbl_boundary_face.setText(self._surface_label(b.target_face) if b.target_face else "未选择")
            self.check_boundary_x.setChecked(b.constrain_x)
            self.check_boundary_y.setChecked(b.constrain_y)
            self.check_boundary_z.setChecked(b.constrain_z)
            self.spin_boundary_dx.setValue(b.displacement_x)
            self.spin_boundary_dy.setValue(b.displacement_y)
            self.spin_boundary_dz.setValue(b.displacement_z)
            self.lbl_thermal_boundary_face.setText(self._surface_label(tb.target_face) if tb.target_face else "未选择")
            self.spin_boundary_temperature.setValue(tb.temperature)
            self.lbl_thermal_load_face.setText(self._surface_label(tl.target_face) if tl.target_face else "未选择")
            self.spin_heat_power.setValue(tl.heat_power)

            self._set_combo_by_data(self.combo_analysis_type, s.analysis_type)
            self._refresh_solver_options_for_analysis(s.analysis_type, s.linear_solver)
            self.spin_load_steps.setValue(s.load_steps)
            self.spin_max_iterations.setValue(s.max_iterations)
            self.spin_tolerance.setValue(s.tolerance)
            self.spin_modal_count.setValue(s.modal_count)
            self.spin_total_time.setValue(s.total_time)
            self.spin_time_step.setValue(s.time_step)
            self.spin_newmark_beta.setValue(s.newmark_beta)
            self.spin_newmark_gamma.setValue(s.newmark_gamma)
            self._set_combo_by_data(self.combo_damping_mode, s.damping_mode)
            self.spin_rayleigh_alpha.setValue(s.rayleigh_alpha)
            self.spin_rayleigh_beta.setValue(s.rayleigh_beta)
            self.spin_modal_damping_ratio.setValue(s.modal_damping_ratio)
            self.spin_modal_damping_freq1.setValue(s.modal_damping_freq1_hz)
            self.spin_modal_damping_freq2.setValue(s.modal_damping_freq2_hz)
            self.spin_frequency_start.setValue(s.frequency_start_hz)
            self.spin_frequency_end.setValue(s.frequency_end_hz)
            self.spin_frequency_points.setValue(s.frequency_point_count)
            self.spin_warp.setValue(s.warp_factor)
            self.spin_result_warp.setValue(s.warp_factor)
            normalized_threads = max(1, int(s.num_threads or os.cpu_count() or 1))
            s.num_threads = normalized_threads
            self.check_parallel.setChecked(s.use_parallel)
            self.spin_threads.setValue(normalized_threads)

            if g.mode == "cad" and g.cad_file:
                self.lbl_cad_status.setText(f"当前使用：外部 CAD - {Path(g.cad_file).name}")
            else:
                self.lbl_cad_status.setText(f"当前使用：参数化几何 - {self.combo_primitive.currentText()}")

            self._update_geometry_form_state()
            self._update_solver_form_state()
            self._update_result_form_state()
            self._material_nonlinear_toggled()
        finally:
            self._is_syncing_widgets = False

    def _sync_state_from_widgets(self) -> None:
        if self._is_syncing_widgets:
            return
        self.state.ensure_default_entities()
        g, m, l, b, tb, tl, s = self.state.geometry, self.state.material, self.state.load_case, self.state.boundary_condition, self.state.thermal_boundary, self.state.thermal_load, self.state.solver
        g.primitive = str(self.combo_primitive.currentData())
        g.length = self.spin_length.value()
        g.width = self.spin_width.value()
        g.height = self.spin_height.value()
        g.radius = self.spin_radius.value()
        g.hole_radius = self.spin_hole_radius.value()
        g.mesh_topology = str(self.combo_mesh_topology.currentData())
        g.mesh_size = self.spin_mesh_size.value()
        g.algorithm_2d = int(self.combo_algorithm_2d.currentData())
        g.algorithm_3d = int(self.combo_algorithm_3d.currentData())
        g.element_order = self.spin_element_order.value()
        g.optimize_mesh = self.check_optimize_mesh.isChecked()
        g.local_refine_enabled = self.check_local_refine.isChecked()
        g.local_refine_size = self.spin_local_refine_size.value()
        g.local_refine_radius = self.spin_local_refine_radius.value()

        m.name = self.combo_material_name.currentText().strip() or "未命名材料"
        m.young_modulus = self.spin_E.value()
        m.poisson_ratio = self.spin_nu.value()
        m.density = self.spin_density.value()
        m.thermal_conductivity = self.spin_thermal_conductivity.value()
        m.nonlinear_enabled = self.check_material_nonlinear.isChecked()
        m.yield_strength = self.spin_yield_strength.value()
        m.hardening_modulus = self.spin_hardening_modulus.value()
        l.force_x = self.spin_force_x.value()
        l.force_y = self.spin_force_y.value()
        l.force_z = self.spin_force_z.value()
        l.boundary_tolerance_ratio = self.spin_tolerance_ratio.value()
        b.constrain_x = self.check_boundary_x.isChecked()
        b.constrain_y = self.check_boundary_y.isChecked()
        b.constrain_z = self.check_boundary_z.isChecked()
        b.displacement_x = self.spin_boundary_dx.value()
        b.displacement_y = self.spin_boundary_dy.value()
        b.displacement_z = self.spin_boundary_dz.value()
        tb.temperature = self.spin_boundary_temperature.value()
        tl.heat_power = self.spin_heat_power.value()

        s.analysis_type = str(self.combo_analysis_type.currentData())
        s.linear_solver = normalize_solver_for_analysis(
            s.analysis_type,
            str(self.combo_linear_solver.currentData()),
        )
        s.load_steps = self.spin_load_steps.value()
        s.max_iterations = self.spin_max_iterations.value()
        s.tolerance = self.spin_tolerance.value()
        s.modal_count = self.spin_modal_count.value()
        s.total_time = self.spin_total_time.value()
        s.time_step = self.spin_time_step.value()
        s.newmark_beta = self.spin_newmark_beta.value()
        s.newmark_gamma = self.spin_newmark_gamma.value()
        s.damping_mode = str(self.combo_damping_mode.currentData())
        s.rayleigh_alpha = self.spin_rayleigh_alpha.value()
        s.rayleigh_beta = self.spin_rayleigh_beta.value()
        s.modal_damping_ratio = self.spin_modal_damping_ratio.value()
        s.modal_damping_freq1_hz = self.spin_modal_damping_freq1.value()
        s.modal_damping_freq2_hz = self.spin_modal_damping_freq2.value()
        s.frequency_start_hz = self.spin_frequency_start.value()
        s.frequency_end_hz = self.spin_frequency_end.value()
        s.frequency_point_count = self.spin_frequency_points.value()
        s.warp_factor = self.spin_warp.value()
        s.use_parallel = self.check_parallel.isChecked()
        s.num_threads = max(1, self.spin_threads.value())

    def _summary_text(self) -> str:
        g, mesh, res = self.state.geometry, self.state.mesh_summary, self.state.result_summary
        geo_value = "外部 CAD" if g.mode == "cad" and g.cad_file else "参数化几何"
        geo_note = Path(g.cad_file).name if g.mode == "cad" and g.cad_file else self.combo_primitive.currentText()
        modal_text = ""
        if res.modal_frequencies_hz:
            preview = ", ".join(f"{value:.2f}" for value in res.modal_frequencies_hz[:5])
            modal_text = f"\n模态频率 [Hz]：{preview}"
        thermal_text = ""
        if self.state.solver.analysis_type == "steady_state_thermal":
            thermal_text = (
                f"\n固定温度边界：{self._surface_label(self.state.thermal_boundary.target_face) if self.state.thermal_boundary.target_face else '未选择'} = {self.state.thermal_boundary.temperature:.3f} °C"
                f"\n热流载荷：{self._surface_label(self.state.thermal_load.target_face) if self.state.thermal_load.target_face else '未选择'} / {self.state.thermal_load.heat_power:.3f} W"
                f"\n最高温度：{res.max_temperature:.6e} °C，最低温度：{res.min_temperature:.6e} °C，最大热流密度：{res.max_heat_flux:.6e}"
            )
        transient_text = ""
        if res.transient_step_count > 0:
            transient_text = (
                f"\n瞬态步数：{res.transient_step_count}，总时长 {res.transient_total_time:.6f} s"
                f"\n动态阻尼：{describe_dynamic_damping(self.state)}"
            )
        frequency_text = ""
        if res.frequency_response_count > 0:
            frequency_text = (
                f"\n频响点数：{res.frequency_response_count}，峰值频率 {res.peak_response_frequency_hz:.3f} Hz"
                f"\n动态阻尼：{describe_dynamic_damping(self.state)}"
            )
        solver_caption = self.label_linear_solver.text().rstrip("：")
        return (
            f"几何模式：{geo_value}\n"
            f"几何类型：{geo_note}\n"
            f"网格类型：{self.combo_mesh_topology.currentText()}，2D={self.combo_algorithm_2d.currentText()} / 3D={self.combo_algorithm_3d.currentText()}\n"
            f"材料：{self.state.material.name}（非线性={'开' if self.state.material.nonlinear_enabled else '关'}，导热系数={self.state.material.thermal_conductivity:.3f} W/(m·K)，赋予整个当前几何体）\n"
            f"网格摘要：节点 {mesh.node_count}，显示单元 {mesh.display_cell_count}（{mesh.display_cell_type}），求解单元 {mesh.tetra_count}\n"
            f"荷载工况：{self.state.load_case.name}，受载端={self._surface_label(self.state.load_case.loaded_face) if self.state.load_case.loaded_face else '未选择'}\n"
            f"总载荷：Fx={self.state.load_case.force_x:.2f} N，Fy={self.state.load_case.force_y:.2f} N，Fz={self.state.load_case.force_z:.2f} N\n"
            f"边界条件：{self.state.boundary_condition.name}，约束端={self._surface_label(self.state.boundary_condition.target_face) if self.state.boundary_condition.target_face else '未选择'}\n"
            f"求解设置：{self.combo_analysis_type.currentText()} / {solver_caption}{self.combo_linear_solver.currentText()}\n"
            f"收敛信息：{'收敛' if res.converged else '未求解或未收敛'}，迭代 {res.iteration_count} 次，残差 {res.residual_norm:.3e}\n"
            f"最大位移：{res.max_displacement:.6e} m\n"
            f"最大应力：{res.max_von_mises:.6e} Pa\n"
            f"最大应变：{res.max_equivalent_strain:.6e}"
            f"{thermal_text}"
            f"{modal_text}"
            f"{transient_text}"
            f"{frequency_text}"
        )

    def _refresh_summary(self) -> None:
        if self.summary_dialog is not None:
            self.summary_dialog.set_summary(self._summary_text())

    def _set_view_header(self, _title: str, _hint: str) -> None:
        return None

    def _apply_plotter_background(self) -> None:
        if not self._has_viewport():
            return
        theme = self.combo_background.currentText()
        if theme == "浅色背景":
            self.viewport.set_background("#F8FAFC", top="#E5E7EB")
        elif theme == "深色背景":
            self.viewport.set_background("#111827", top="#334155")
        else:
            self.viewport.set_background("#D9E2EC", top="#243B53")

    def _apply_view_preferences(self) -> None:
        self.check_show_axes.blockSignals(True)
        self.check_show_axes.setChecked(self.action_toggle_axes.isChecked())
        self.check_show_axes.blockSignals(False)
        if not self._has_viewport():
            return
        self._redraw_current_scene()

    def _current_scene_bounds(self) -> Optional[tuple[float, float, float, float, float, float]]:
        if self.analysis is not None:
            return self.analysis.result_grid.bounds
        if self.mesh_bundle is not None:
            return self.mesh_bundle.display_grid.bounds
        if self.preview_scene is not None:
            return self.preview_scene.surface.bounds
        return None

    def _current_module_index(self) -> int:
        """返回当前右侧属性编辑器所在模块索引。"""

        return self.property_stack.currentIndex()

    def _face_center_and_normal(self, face_name: str, bounds: tuple[float, float, float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
        """根据包围盒端面名称返回端面中心和外法向。"""

        xmin, xmax, ymin, ymax, zmin, zmax = bounds
        center = {
            "xmin": np.array([xmin, 0.5 * (ymin + ymax), 0.5 * (zmin + zmax)], dtype=float),
            "xmax": np.array([xmax, 0.5 * (ymin + ymax), 0.5 * (zmin + zmax)], dtype=float),
            "ymin": np.array([0.5 * (xmin + xmax), ymin, 0.5 * (zmin + zmax)], dtype=float),
            "ymax": np.array([0.5 * (xmin + xmax), ymax, 0.5 * (zmin + zmax)], dtype=float),
            "zmin": np.array([0.5 * (xmin + xmax), 0.5 * (ymin + ymax), zmin], dtype=float),
            "zmax": np.array([0.5 * (xmin + xmax), 0.5 * (ymin + ymax), zmax], dtype=float),
        }[face_name]
        normal = {
            "xmin": np.array([-1.0, 0.0, 0.0], dtype=float),
            "xmax": np.array([1.0, 0.0, 0.0], dtype=float),
            "ymin": np.array([0.0, -1.0, 0.0], dtype=float),
            "ymax": np.array([0.0, 1.0, 0.0], dtype=float),
            "zmin": np.array([0.0, 0.0, -1.0], dtype=float),
            "zmax": np.array([0.0, 0.0, 1.0], dtype=float),
        }[face_name]
        return center, normal

    def _build_face_plane(self, face_name: str, bounds: tuple[float, float, float, float, float, float], offset_ratio: float = 0.0) -> pv.PolyData:
        """创建指定端面的矩形平面，用于高亮和标注。"""

        xmin, xmax, ymin, ymax, zmin, zmax = bounds
        center, normal = self._face_center_and_normal(face_name, bounds)
        dx = max(xmax - xmin, 1.0e-6)
        dy = max(ymax - ymin, 1.0e-6)
        dz = max(zmax - zmin, 1.0e-6)
        center = center + normal * (offset_ratio * max(dx, dy, dz))

        plane_sizes = {
            "xmin": (dy, dz),
            "xmax": (dy, dz),
            "ymin": (dx, dz),
            "ymax": (dx, dz),
            "zmin": (dx, dy),
            "zmax": (dx, dy),
        }
        i_size, j_size = plane_sizes[face_name]
        return pv.Plane(center=center, direction=tuple(normal.tolist()), i_size=i_size, j_size=j_size, i_resolution=1, j_resolution=1)

    def _resolve_visual_surface_patch(
        self,
        face_name: str,
        bounds: tuple[float, float, float, float, float, float],
        offset_ratio: float = 0.004,
    ) -> Optional[pv.PolyData]:
        """
        返回当前需要显示的表面补丁。

        说明：
        1. 优先使用真实几何面 `surface:*`；
        2. 若当前状态里保存的是兼容端面名称，则退回到包围盒平面；
        3. 这样旧项目或未启用真实选面时也能正常显示标注。
        """

        surface_patches, _surface_labels = self._get_current_surface_patches()
        if face_name in surface_patches:
            return surface_patches[face_name]
        if face_name in FACE_LABELS:
            return self._build_face_plane(face_name, bounds, offset_ratio=offset_ratio)
        return None

    def _surface_display_normal(
        self,
        face_name: str,
        surface_patch: pv.PolyData,
        bounds: tuple[float, float, float, float, float, float],
    ) -> np.ndarray:
        """
        估算当前显示补丁的外法向。

        说明：
        1. 对标准端面名称，直接使用包围盒法向；
        2. 对真实 CAD/网格面，使用平均单元法向并按模型中心修正朝向；
        3. 这样载荷箭头和约束符号都能尽量贴合真实选中面。
        """

        if face_name in FACE_LABELS:
            _center, normal = self._face_center_and_normal(face_name, bounds)
            return normal

        normal_patch = surface_patch.compute_normals(
            cell_normals=True,
            point_normals=False,
            auto_orient_normals=True,
            consistent_normals=True,
        )
        normals = np.asarray(normal_patch.cell_normals, dtype=float)
        if normals.size == 0:
            normals = np.asarray([[0.0, 0.0, 1.0]], dtype=float)
        normal = np.mean(normals, axis=0)

        patch_points = np.asarray(surface_patch.points, dtype=float)
        patch_center = np.mean(patch_points, axis=0) if patch_points.size else np.zeros(3, dtype=float)
        model_center = np.array(
            [
                0.5 * (bounds[0] + bounds[1]),
                0.5 * (bounds[2] + bounds[3]),
                0.5 * (bounds[4] + bounds[5]),
            ],
            dtype=float,
        )
        outward_hint = patch_center - model_center
        if np.linalg.norm(normal) <= 1.0e-12:
            normal = outward_hint if np.linalg.norm(outward_hint) > 1.0e-12 else np.array([0.0, 0.0, 1.0], dtype=float)
        if np.linalg.norm(outward_hint) > 1.0e-12 and float(np.dot(normal, outward_hint)) < 0.0:
            normal = -normal

        length = float(np.linalg.norm(normal))
        if length <= 1.0e-12:
            return np.array([0.0, 0.0, 1.0], dtype=float)
        return normal / length

    def _surface_anchor_point(self, surface_patch: pv.PolyData) -> np.ndarray:
        """返回当前表面用于放置主标记的中心点。"""

        centers = np.asarray(surface_patch.cell_centers().points, dtype=float)
        if centers.ndim != 2 or centers.shape[0] == 0:
            centers = np.asarray(surface_patch.points, dtype=float)
        if centers.ndim != 2 or centers.shape[0] == 0:
            return np.zeros(3, dtype=float)
        return np.mean(centers, axis=0)

    def _surface_symbol_anchor(
        self,
        face_name: str,
        surface_patch: pv.PolyData,
        bounds: tuple[float, float, float, float, float, float],
    ) -> np.ndarray:
        """
        返回用于绘制边界/荷载符号的真实锚点。

        说明：
        1. 对标准端面，直接返回实体真实端面的中心，而不是外移后的高亮平面中心；
        2. 对真实选中的几何面，优先使用面包围盒中心，避免单元中心平均导致视觉偏移；
        3. 这样可把“高亮显示层”和“符号起点”解耦，减少箭头与所选面中心不重合的问题。
        """

        if face_name in FACE_LABELS:
            center, _normal = self._face_center_and_normal(face_name, bounds)
            return center
        patch_bounds = surface_patch.bounds
        return np.array(
            [
                0.5 * (patch_bounds[0] + patch_bounds[1]),
                0.5 * (patch_bounds[2] + patch_bounds[3]),
                0.5 * (patch_bounds[4] + patch_bounds[5]),
            ],
            dtype=float,
        )

    def _surface_tangent_basis(self, normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """根据表面法向构造两个互相正交的面内切向基。"""

        direction = np.asarray(normal, dtype=float)
        length = float(np.linalg.norm(direction))
        if length <= 1.0e-12:
            direction = np.array([0.0, 0.0, 1.0], dtype=float)
        else:
            direction = direction / length

        reference = np.array([0.0, 0.0, 1.0], dtype=float)
        if abs(float(np.dot(direction, reference))) > 0.9:
            reference = np.array([0.0, 1.0, 0.0], dtype=float)

        tangent_1 = np.cross(direction, reference)
        tangent_1_norm = float(np.linalg.norm(tangent_1))
        if tangent_1_norm <= 1.0e-12:
            tangent_1 = np.array([1.0, 0.0, 0.0], dtype=float)
        else:
            tangent_1 = tangent_1 / tangent_1_norm

        tangent_2 = np.cross(direction, tangent_1)
        tangent_2_norm = float(np.linalg.norm(tangent_2))
        if tangent_2_norm <= 1.0e-12:
            tangent_2 = np.array([0.0, 1.0, 0.0], dtype=float)
        else:
            tangent_2 = tangent_2 / tangent_2_norm
        return tangent_1, tangent_2

    def _surface_marker_points(self, surface_patch: pv.PolyData, max_points: int = 4) -> np.ndarray:
        """返回一个用于放置箭头/约束符号的代表性点集。"""

        centers = np.asarray(surface_patch.cell_centers().points, dtype=float)
        if centers.ndim != 2 or centers.shape[0] == 0:
            centers = np.asarray(surface_patch.points, dtype=float)
        if centers.ndim != 2 or centers.shape[0] == 0:
            return np.empty((0, 3), dtype=float)
        if centers.shape[0] <= max_points:
            return centers
        pick_indices = np.linspace(0, centers.shape[0] - 1, num=max_points, dtype=int)
        return centers[pick_indices]

    def _add_slim_arrow(
        self,
        start: np.ndarray,
        direction: np.ndarray,
        length: float,
        color: str,
        shaft_radius: float,
        tip_radius: float,
        tip_length: float,
        opacity: float = 0.95,
    ) -> None:
        """绘制一根更细、更克制的箭头标记。"""

        if not self._has_viewport():
            return
        vector = np.asarray(direction, dtype=float)
        vector_norm = float(np.linalg.norm(vector))
        if vector_norm <= 1.0e-12 or length <= 1.0e-12:
            return
        vector = vector / vector_norm

        arrow = pv.Arrow(
            start=np.asarray(start, dtype=float),
            direction=vector,
            tip_length=min(max(tip_length / length, 0.18), 0.45),
            tip_radius=max(tip_radius / length, 0.04),
            shaft_radius=max(shaft_radius / length, 0.015),
            scale=length,
        )
        self.viewport.add_mesh(arrow, color=color, opacity=opacity, smooth_shading=True)

    def _add_structural_boundary_symbols(
        self,
        boundary_patch: pv.PolyData,
        face_name: str,
        bounds: tuple[float, float, float, float, float, float],
        size: float,
    ) -> None:
        """在约束面上添加更轻量、更清晰的边界条件符号。"""

        normal = self._surface_display_normal(face_name, boundary_patch, bounds)
        anchor = self._surface_symbol_anchor(face_name, boundary_patch, bounds)
        tangent_1, tangent_2 = self._surface_tangent_basis(normal)

        cone_height = 0.060 * size
        cone_radius = 0.014 * size
        support_offsets = [(-0.040 * size) * tangent_1, (0.040 * size) * tangent_1]
        for offset in support_offsets:
            cone_center = anchor + offset + normal * (0.5 * cone_height)
            support_cone = pv.Cone(
                center=cone_center,
                direction=-normal,
                height=cone_height,
                radius=cone_radius,
                resolution=18,
            )
            self.viewport.add_mesh(support_cone, color="#2563EB", opacity=0.90, smooth_shading=True)

        axis_markers = [
            (
                self.state.boundary_condition.constrain_x,
                np.array([1.0, 0.0, 0.0], dtype=float),
                "#DC2626",
                -0.026 * size * tangent_2,
            ),
            (
                self.state.boundary_condition.constrain_y,
                np.array([0.0, 1.0, 0.0], dtype=float),
                "#16A34A",
                np.zeros(3, dtype=float),
            ),
            (
                self.state.boundary_condition.constrain_z,
                np.array([0.0, 0.0, 1.0], dtype=float),
                "#2563EB",
                0.026 * size * tangent_2,
            ),
        ]
        axis_length = 0.075 * size
        for enabled, axis, color, offset in axis_markers:
            if not enabled:
                continue
            arrow_start = anchor + offset + normal * (0.040 * size) - 0.5 * axis_length * axis
            self._add_slim_arrow(
                arrow_start,
                axis,
                axis_length,
                color,
                shaft_radius=0.0035 * size,
                tip_radius=0.0085 * size,
                tip_length=0.020 * size,
                opacity=0.92,
            )

    def _add_structural_load_symbols(
        self,
        load_patch: pv.PolyData,
        face_name: str,
        load_vector: np.ndarray,
        bounds: tuple[float, float, float, float, float, float],
        size: float,
    ) -> None:
        """把载荷箭头简化为单根细箭头，并稳定贴在选中的面附近。"""

        anchor = self._surface_symbol_anchor(face_name, load_patch, bounds)
        normal = self._surface_display_normal(face_name, load_patch, bounds)
        load_magnitude = float(np.linalg.norm(load_vector))
        if load_magnitude <= 0.0:
            marker_center = anchor
            marker = pv.Sphere(radius=0.018 * size, center=marker_center)
            self.viewport.add_mesh(marker, color="#F59E0B", opacity=0.88, smooth_shading=True)
            return

        direction = load_vector / load_magnitude
        arrow_length = 0.220 * size
        arrow_start = anchor
        base_marker = pv.Sphere(radius=0.010 * size, center=arrow_start)
        self.viewport.add_mesh(base_marker, color="#DC2626", opacity=0.96, smooth_shading=True)
        self._add_slim_arrow(
            arrow_start,
            direction,
            arrow_length,
            "#DC2626",
            shaft_radius=0.0052 * size,
            tip_radius=0.013 * size,
            tip_length=0.040 * size,
            opacity=0.96,
        )

    def _add_condition_visuals(self) -> None:
        """在视口中显示当前边界条件和载荷的可视化标注。"""

        if not self._has_viewport():
            return
        if self.boundary_pick_mode is not None:
            return
        module_index = self._current_module_index()
        if module_index not in {2, 3}:
            return

        bounds = self._current_scene_bounds()
        if bounds is None:
            return

        size = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4], 1.0e-6)
        show_structural = module_index == 2 and (self.state.boundary_condition.is_applied or self.state.load_case.is_applied)
        show_thermal = module_index == 3 and (self.state.thermal_boundary.is_applied or self.state.thermal_load.is_applied)

        if show_structural:
            boundary_face = self.state.boundary_condition.target_face
            boundary_patch = (
                self._resolve_visual_surface_patch(boundary_face, bounds)
                if self.state.boundary_condition.is_applied and boundary_face
                else None
            )
            if boundary_patch is not None:
                self.viewport.add_mesh(
                    boundary_patch,
                    color="#60A5FA",
                    opacity=0.20,
                    show_edges=False,
                    smooth_shading=True,
                )
                self.viewport.add_mesh(
                    boundary_patch.extract_feature_edges(boundary_edges=True, feature_edges=False, manifold_edges=False),
                    color="#2563EB",
                    line_width=2.0,
                )
                self._add_structural_boundary_symbols(boundary_patch, boundary_face, bounds, size)

            load_face = self.state.load_case.loaded_face
            load_vector = np.array(
                [self.state.load_case.force_x, self.state.load_case.force_y, self.state.load_case.force_z],
                dtype=float,
            )
            load_patch = self._resolve_visual_surface_patch(load_face, bounds) if self.state.load_case.is_applied and load_face else None
            if load_patch is not None:
                self.viewport.add_mesh(
                    load_patch,
                    color="#FBBF24",
                    opacity=0.18,
                    show_edges=False,
                    smooth_shading=True,
                )
                self.viewport.add_mesh(
                    load_patch.extract_feature_edges(boundary_edges=True, feature_edges=False, manifold_edges=False),
                    color="#EA580C",
                    line_width=2.0,
                )
                self._add_structural_load_symbols(load_patch, load_face, load_vector, bounds, size)

        if show_thermal:
            thermal_boundary_face = self.state.thermal_boundary.target_face
            boundary_patch = (
                self._resolve_visual_surface_patch(thermal_boundary_face, bounds)
                if self.state.thermal_boundary.is_applied and thermal_boundary_face
                else None
            )
            if boundary_patch is not None:
                self.viewport.add_mesh(
                    boundary_patch,
                    color="#C084FC",
                    opacity=0.22,
                    show_edges=False,
                    smooth_shading=True,
                )
                self.viewport.add_mesh(
                    boundary_patch.extract_feature_edges(boundary_edges=True, feature_edges=False, manifold_edges=False),
                    color="#7C3AED",
                    line_width=2.0,
                )

            thermal_load_face = self.state.thermal_load.target_face
            load_patch = (
                self._resolve_visual_surface_patch(thermal_load_face, bounds)
                if self.state.thermal_load.is_applied and thermal_load_face
                else None
            )
            if load_patch is not None:
                self.viewport.add_mesh(
                    load_patch,
                    color="#F87171",
                    opacity=0.18,
                    show_edges=False,
                    smooth_shading=True,
                )
                self.viewport.add_mesh(
                    load_patch.extract_feature_edges(boundary_edges=True, feature_edges=False, manifold_edges=False),
                    color="#B91C1C",
                    line_width=2.0,
                )

    def _current_result_grid(self, deformed: bool = True, point_data: bool = False) -> pv.DataSet:
        """根据当前变形倍数返回用于显示的结果网格。"""

        if self.analysis is None:
            raise RuntimeError("当前没有可显示的分析结果。")

        source = self.analysis.result_grid.copy(deep=True)
        if self.analysis.modal_shapes:
            mode_index = min(max(self.current_modal_mode_index, 0), len(self.analysis.modal_shapes) - 1)
            modal_displacement = self.analysis.modal_shapes[mode_index]
            source.point_data["Displacement"] = modal_displacement
            source.point_data["DisplacementMagnitude"] = np.linalg.norm(modal_displacement, axis=1)
        if point_data:
            source = source.cell_data_to_point_data(pass_cell_data=True)
        if deformed:
            source = source.warp_by_vector("Displacement", factor=self.spin_result_warp.value())
        return source

    def _result_metadata(self, mode: str) -> tuple[str, str]:
        """返回当前结果模式对应的标题和配色。"""

        title = {
            "stress": "Von Mises Stress (Pa)",
            "strain": "Equivalent Strain",
            "displacement": "Displacement (m)",
            "temperature": "Temperature (degC)",
            "heat_flux": "Heat Flux Magnitude",
        }[mode]
        cmap = {
            "stress": "turbo",
            "strain": "plasma",
            "displacement": "viridis",
            "temperature": "coolwarm",
            "heat_flux": "inferno",
        }[mode]
        return title, cmap

    def _build_mouse_event(
        self,
        event_type: QEvent.Type,
        position: tuple[float, float],
        button: Qt.MouseButton,
        buttons: Qt.MouseButton,
        modifiers: Qt.KeyboardModifier,
    ) -> QMouseEvent:
        """根据记录的鼠标状态重建一个 Qt 鼠标事件，用于转发给视口。"""

        return QMouseEvent(event_type, QPointF(float(position[0]), float(position[1])), button, buttons, modifiers)

    def _dispatch_viewport_mouse_event(self, event: QMouseEvent) -> None:
        """把延迟判定后的鼠标事件手动转发给视口控件。"""

        if not self._has_viewport():
            return
        if event.type() == QEvent.Type.MouseButtonPress:
            self.viewport.interactor.mousePressEvent(event)
        elif event.type() == QEvent.Type.MouseMove:
            self.viewport.interactor.mouseMoveEvent(event)
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self.viewport.interactor.mouseReleaseEvent(event)

    def _current_curve_response_key(self) -> str:
        """返回当前曲线窗口选中的响应量类型。"""

        return str(self.combo_curve_response.currentData())

    def _transient_curve_data(self) -> tuple[np.ndarray, np.ndarray, str]:
        """返回瞬态分析当前选中响应量的曲线数据。"""

        if not self.analysis or self.analysis.transient_times is None or self.analysis.transient_max_displacements is None:
            raise RuntimeError("当前没有可用的瞬态响应数据。")
        response_key = self._current_curve_response_key()
        mapping = {
            "global_max": (self.analysis.transient_max_displacements, "全局最大位移 [m]"),
            "loaded_ux": (self.analysis.transient_loaded_ux_history, "受载面平均 Ux [m]"),
            "loaded_uy": (self.analysis.transient_loaded_uy_history, "受载面平均 Uy [m]"),
            "loaded_uz": (self.analysis.transient_loaded_uz_history, "受载面平均 Uz [m]"),
            "loaded_umag": (self.analysis.transient_loaded_umag_history, "受载面平均位移幅值 [m]"),
        }
        values, label = mapping.get(response_key, (self.analysis.transient_max_displacements, "全局最大位移 [m]"))
        if values is None:
            values = self.analysis.transient_max_displacements
        return self.analysis.transient_times, values, label

    def _frequency_curve_data(self) -> tuple[np.ndarray, np.ndarray, str]:
        """返回频响分析当前选中响应量的曲线数据。"""

        if (
            not self.analysis or
            self.analysis.frequency_response_frequencies_hz is None or
            self.analysis.frequency_response_max_displacements is None
        ):
            raise RuntimeError("当前没有可用的频响响应数据。")
        response_key = self._current_curve_response_key()
        mapping = {
            "global_max": (self.analysis.frequency_response_max_displacements, "全局最大位移幅值 [m]"),
            "loaded_ux": (self.analysis.frequency_response_loaded_ux_history, "受载面平均 Ux 幅值 [m]"),
            "loaded_uy": (self.analysis.frequency_response_loaded_uy_history, "受载面平均 Uy 幅值 [m]"),
            "loaded_uz": (self.analysis.frequency_response_loaded_uz_history, "受载面平均 Uz 幅值 [m]"),
            "loaded_umag": (self.analysis.frequency_response_loaded_umag_history, "受载面平均位移幅值 [m]"),
        }
        values, label = mapping.get(response_key, (self.analysis.frequency_response_max_displacements, "全局最大位移幅值 [m]"))
        if values is None:
            values = self.analysis.frequency_response_max_displacements
        return self.analysis.frequency_response_frequencies_hz, values, label

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        """把选面操作限定为“左键单击释放”，拖动视图时不触发选择。"""

        if not self._has_viewport():
            return super().eventFilter(watched, event)
        if watched is self.viewport.interactor and self.boundary_pick_mode is not None:
            if event.type() == QEvent.Type.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
                position = event.position()
                self.pick_press_position = (position.x(), position.y())
                self.pick_press_modifiers = event.modifiers()
                self.pick_drag_active = False
                return True
            elif event.type() == QEvent.Type.MouseMove:
                position = event.position()
                self._update_boundary_face_hover(position.x(), position.y())
                buttons = getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)()
                if self.pick_press_position is not None and bool(buttons & Qt.MouseButton.LeftButton):
                    dx = position.x() - self.pick_press_position[0]
                    dy = position.y() - self.pick_press_position[1]
                    if not self.pick_drag_active and dx * dx + dy * dy > 25.0:
                        press_event = self._build_mouse_event(
                            QEvent.Type.MouseButtonPress,
                            self.pick_press_position,
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            self.pick_press_modifiers,
                        )
                        self._dispatch_viewport_mouse_event(press_event)
                        self.pick_drag_active = True
                    if self.pick_drag_active:
                        move_event = self._build_mouse_event(
                            QEvent.Type.MouseMove,
                            (position.x(), position.y()),
                            Qt.MouseButton.NoButton,
                            buttons,
                            event.modifiers(),
                        )
                        self._dispatch_viewport_mouse_event(move_event)
                        return True
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
                position = event.position()
                press = self.pick_press_position
                drag_active = self.pick_drag_active
                self.pick_press_position = None
                self.pick_drag_active = False
                if drag_active:
                    release_event = self._build_mouse_event(
                        QEvent.Type.MouseButtonRelease,
                        (position.x(), position.y()),
                        Qt.MouseButton.LeftButton,
                        getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)(),
                        event.modifiers(),
                    )
                    self._dispatch_viewport_mouse_event(release_event)
                elif press is not None:
                    dx = position.x() - press[0]
                    dy = position.y() - press[1]
                    if dx * dx + dy * dy <= 25.0:
                        if self._pick_boundary_face_at_position(position.x(), position.y()):
                            return True
                return True
            elif event.type() == QEvent.Type.Leave:
                self.pick_press_position = None
                self.pick_drag_active = False
                self.boundary_pick_hover_face = ""
                self._refresh_boundary_pick_overlay_styles()
        elif watched is self.viewport.interactor and self.result_probe_enabled and self.current_scene == "result":
            if event.type() == QEvent.Type.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
                position = event.position()
                self.pick_press_position = (position.x(), position.y())
            elif event.type() == QEvent.Type.MouseMove:
                position = event.position()
                self._update_result_probe_hover(position.x(), position.y())
            elif event.type() == QEvent.Type.MouseButtonRelease and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
                position = event.position()
                press = self.pick_press_position
                self.pick_press_position = None
                if press is not None:
                    dx = position.x() - press[0]
                    dy = position.y() - press[1]
                    if dx * dx + dy * dy <= 25.0:
                        world_position = self._pick_result_world_position(position.x(), position.y())
                        if world_position is not None and self._show_result_probe_at_position(world_position):
                            return True
            elif event.type() == QEvent.Type.Leave:
                self.pick_press_position = None
                self._clear_result_probe_hover()
        return super().eventFilter(watched, event)

    def _actor_key(self, actor) -> str:
        """生成 VTK Actor 的稳定键值，用于拾取映射。"""

        return actor.GetAddressAsString("")

    def _viewport_pick_coordinates(self, x_pos: float, y_pos: float) -> tuple[float, float]:
        """把 Qt 逻辑坐标换算成 VTK 拾取使用的物理像素坐标。"""

        if not self._has_viewport():
            return float(x_pos), float(y_pos)
        scale = float(self.viewport.interactor.devicePixelRatioF())
        width = float(self.viewport.interactor.width()) * scale
        height = float(self.viewport.interactor.height()) * scale
        vtk_x = float(x_pos) * scale
        vtk_y = height - float(y_pos) * scale
        vtk_x = max(0.0, min(vtk_x, max(width - 1.0, 0.0)))
        vtk_y = max(0.0, min(vtk_y, max(height - 1.0, 0.0)))
        return vtk_x, vtk_y

    def _pick_boundary_face_at_position(self, x_pos: float, y_pos: float) -> bool:
        """在当前鼠标释放位置执行一次端面拾取。"""

        if self.boundary_pick_mode is None:
            return False
        actor = self._pick_boundary_actor_at_position(x_pos, y_pos)
        if actor is None:
            return False
        return self._handle_boundary_face_pick(actor)

    def _pick_boundary_actor_at_position(self, x_pos: float, y_pos: float):
        """在指定视口位置拾取当前表面覆盖层上的 Actor。"""

        if not self._has_viewport():
            return None
        picker = vtk.vtkPropPicker()
        vtk_x, vtk_y = self._viewport_pick_coordinates(x_pos, y_pos)
        picker.Pick(vtk_x, vtk_y, 0.0, self.viewport.renderer)
        return picker.GetActor()

    def _update_boundary_face_hover(self, x_pos: float, y_pos: float) -> None:
        """根据当前鼠标悬停位置刷新端面高亮。"""

        actor = self._pick_boundary_actor_at_position(x_pos, y_pos)
        hovered_face = ""
        if actor is not None:
            hovered_face = self.boundary_pick_actor_map.get(self._actor_key(actor), "")
        if hovered_face == self.boundary_pick_hover_face:
            return
        self.boundary_pick_hover_face = hovered_face
        self._refresh_boundary_pick_overlay_styles()

    def _refresh_boundary_pick_overlay_styles(self) -> None:
        """统一更新选面覆盖层的颜色与透明度。"""

        if self.boundary_pick_mode is None or not self._has_viewport():
            return

        selected_face = ""
        if self.boundary_pick_mode == "boundary":
            selected_face = self.state.boundary_condition.target_face
        elif self.boundary_pick_mode == "load":
            selected_face = self.state.load_case.loaded_face
        elif self.boundary_pick_mode == "thermal_boundary":
            selected_face = self.state.thermal_boundary.target_face
        elif self.boundary_pick_mode == "thermal_load":
            selected_face = self.state.thermal_load.target_face

        for face_name, actor in self.boundary_pick_actor_refs.items():
            prop = actor.GetProperty()
            color = (0.0627, 0.7255, 0.5059)
            opacity = 0.10
            if face_name == selected_face:
                color = (0.9804, 0.6902, 0.0902)
                opacity = 0.38
            if face_name == self.boundary_pick_hover_face:
                color = (0.1294, 0.5882, 0.9529)
                opacity = 0.52
            prop.SetColor(*color)
            prop.SetOpacity(opacity)
        self.viewport.render()

    def _add_boundary_face_overlay(self) -> None:
        if self.boundary_pick_mode is None:
            self.boundary_pick_actor_map = {}
            self.boundary_pick_actor_refs = {}
            self.boundary_pick_hover_face = ""
            return
        if not self._has_viewport():
            return

        surface_patches, _surface_labels = self._get_current_surface_patches()
        if not surface_patches:
            return

        self.boundary_pick_actor_map = {}
        self.boundary_pick_actor_refs = {}
        for face_name, patch in surface_patches.items():
            actor = self.viewport.add_mesh(
                patch,
                color="#10B981",
                opacity=0.10,
                pickable=True,
                show_edges=False,
                smooth_shading=True,
            )
            self.boundary_pick_actor_map[self._actor_key(actor)] = face_name
            self.boundary_pick_actor_refs[face_name] = actor
        self._refresh_boundary_pick_overlay_styles()

    def start_boundary_face_pick(self, target: str) -> None:
        surface_patches, _surface_labels = self._get_current_surface_patches()
        if self._current_scene_bounds() is None or not surface_patches:
            QMessageBox.warning(self, "无法选择", "请先生成几何预览或网格。")
            return
        self.boundary_pick_mode = target
        self.boundary_pick_hover_face = ""
        self.pick_press_position = None
        self.pick_drag_active = False
        self._redraw_current_scene()
        prompt_map = {
            "load": "受载面",
            "boundary": "约束面",
            "thermal_boundary": "固定温度表面",
            "thermal_load": "热流作用表面",
        }
        prompt = prompt_map.get(target, "目标表面")
        self.log(f"请在主视口中左键单击{prompt}完成选择；左键拖动可旋转视图，轻点会选面。")

    def _handle_boundary_face_pick(self, actor) -> bool:
        face_name = self.boundary_pick_actor_map.get(self._actor_key(actor))
        if face_name is None:
            return False
        if self.boundary_pick_mode == "boundary":
            self.state.boundary_condition.target_face = face_name
            self.state.boundary_condition.is_applied = False
        elif self.boundary_pick_mode == "load":
            self.state.load_case.loaded_face = face_name
            self.state.load_case.is_applied = False
        elif self.boundary_pick_mode == "thermal_boundary":
            self.state.thermal_boundary.target_face = face_name
            self.state.thermal_boundary.is_applied = False
        elif self.boundary_pick_mode == "thermal_load":
            self.state.thermal_load.target_face = face_name
            self.state.thermal_load.is_applied = False
        self.boundary_pick_mode = None
        self.boundary_pick_hover_face = ""
        self.pick_press_position = None
        self.pick_drag_active = False
        self._sync_widgets_from_state()
        self._redraw_current_scene()
        self.log(f"已通过图形方式选择表面：{self._surface_label(face_name)}")
        return True

    def _draw_geometry_scene(self) -> None:
        if not self.preview_scene:
            return
        self._initialize_viewport()
        if not self._has_viewport():
            return
        self.current_scene = "geometry"
        self.current_display_grid = None
        self.current_display_scalar = ""
        self.current_display_title = ""
        self._clear_result_probe_marker()
        self.result_probe_hover_actor = None
        self.result_probe_hover_node_id = -1
        self.viewport.clear()
        self._apply_plotter_background()
        # 几何预览只显示光滑外形和几何特征边，不显示三角面片网格边。
        geometry_color = "#B8C6D6"
        if self._current_module_index() == 1 and self.state.material.is_applied:
            geometry_color = "#D6C28A"
        self.viewport.add_mesh(self.preview_scene.surface, color=geometry_color, smooth_shading=True, show_edges=False)
        self.viewport.add_mesh(self.preview_scene.feature_edges, color="#1F2937", line_width=2.2)
        self._add_boundary_face_overlay()
        self._add_condition_visuals()
        if self.action_toggle_axes.isChecked():
            self.viewport.show_axes()
        else:
            self.viewport.hide_axes()
        self.viewport.reset_camera()
        self._set_view_header("几何预览", "显示参数化几何或导入 CAD 的几何外形，不显示面网格边。")

    def _draw_mesh_scene(self) -> None:
        if not self.mesh_bundle:
            return
        self._initialize_viewport()
        if not self._has_viewport():
            return
        self.current_scene = "mesh"
        self.current_display_grid = None
        self.current_display_scalar = ""
        self.current_display_title = ""
        self._clear_result_probe_marker()
        self.result_probe_hover_actor = None
        self.result_probe_hover_node_id = -1
        self.viewport.clear()
        self._apply_plotter_background()
        self.viewport.add_mesh(self.mesh_bundle.display_grid, color="#BCE3DB", show_edges=True, edge_color="#0F766E")
        self._add_boundary_face_overlay()
        self._add_condition_visuals()
        if self.action_toggle_axes.isChecked():
            self.viewport.show_axes()
        else:
            self.viewport.hide_axes()
        self.viewport.reset_camera()
        self._set_view_header("体网格显示", "显示三维体网格，可通过右侧属性调整网格算法、阶次和局部加密。")

    def _draw_quality_scene(self) -> None:
        if not self.quality_bundle:
            return
        self._initialize_viewport()
        if not self._has_viewport():
            return
        self.current_scene = "mesh"
        self.current_display_grid = None
        self.current_display_scalar = ""
        self.current_display_title = ""
        self._clear_result_probe_marker()
        self.result_probe_hover_actor = None
        self.result_probe_hover_node_id = -1
        self.viewport.clear()
        self._apply_plotter_background()
        self.viewport.add_mesh(self.quality_bundle.quality_grid, scalars="CellQuality", cmap="RdYlGn", show_edges=True, scalar_bar_args={"title": "Scaled Jacobian", "fmt": "%.3f", "color": "black"})
        self._add_boundary_face_overlay()
        self._add_condition_visuals()
        if self.action_toggle_axes.isChecked():
            self.viewport.show_axes()
        else:
            self.viewport.hide_axes()
        self.viewport.reset_camera()
        self._set_view_header("网格质量", "显示网格质量着色结果。")

    def _draw_result_scene(self, grid: pv.DataSet, scalar: str, title: str, cmap: str, outline_deformed: bool = True) -> None:
        self._initialize_viewport()
        if not self._has_viewport():
            return
        self.current_scene = "result"
        self.current_display_grid = grid.copy(deep=True)
        self.current_display_scalar = scalar
        self.current_display_title = title
        self.result_probe_marker_actor = None
        self.result_probe_hover_actor = None
        self.result_probe_hover_node_id = -1
        self.viewport.clear()
        self._apply_plotter_background()
        self.viewport.add_mesh(self._current_result_grid(deformed=outline_deformed).outline(), color="#475569")
        self.viewport.add_mesh(grid, scalars=scalar, cmap=cmap, show_edges=True, edge_color="#64748B", scalar_bar_args={"title": title, "fmt": "%.2e", "color": "black"})
        self._add_condition_visuals()
        if self.action_toggle_axes.isChecked():
            self.viewport.show_axes()
        else:
            self.viewport.hide_axes()
        self.viewport.reset_camera()
        self._set_view_header("结果后处理", "结果类型、切片和导出在右侧属性编辑器中设置。")

    def _on_property_page_changed(self, index: int) -> None:
        """右侧模块切换后，强制显示该模块对应的主视图。"""

        if index in (0, 1, 2, 3):
            if self.preview_scene is not None:
                self._draw_geometry_scene()
            return
        if index == 4:
            if self.mesh_bundle is not None:
                self._draw_quality_scene() if self.quality_bundle else self._draw_mesh_scene()
            elif self.preview_scene is not None:
                self._draw_geometry_scene()
            return
        if index == 5:
            if self.mesh_bundle is not None:
                self._draw_quality_scene() if self.quality_bundle else self._draw_mesh_scene()
            elif self.preview_scene is not None:
                self._draw_geometry_scene()
            return
        if index == 6:
            if self.analysis is not None:
                if self.current_scene == "result":
                    self._redraw_current_scene()
                else:
                    self.restore_full_result()
            elif self.mesh_bundle is not None:
                self._draw_quality_scene() if self.quality_bundle else self._draw_mesh_scene()
            elif self.preview_scene is not None:
                self._draw_geometry_scene()
            return
        if index == 7:
            self._redraw_current_scene()
            return
        self._redraw_current_scene()

    def _redraw_current_scene(self) -> None:
        if self.current_scene == "geometry":
            self._draw_geometry_scene()
        elif self.current_scene == "mesh":
            if self.quality_bundle:
                self._draw_quality_scene()
            else:
                self._draw_mesh_scene()
        elif self.current_scene == "result" and self.analysis:
            if self.current_result_view == "slice":
                self.show_result_slice()
            elif self.current_result_view == "contour":
                self.show_result_contours()
            else:
                self.show_result_mode(self.current_result_mode)

    def log(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}")
        self.statusBar().showMessage(message, 5000)

    def open_summary_window(self) -> None:
        if self.summary_dialog is None:
            self.summary_dialog = SummaryDialog(self)
        self.summary_dialog.set_summary(self._summary_text())
        self.summary_dialog.show()
        self.summary_dialog.raise_()
        self.summary_dialog.activateWindow()

    def export_analysis_report(self) -> None:
        """导出当前分析项目的 Markdown 报告。"""

        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析后再导出报告。")
            return
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "导出分析报告",
            str(RESULTS_DIR / "analysis_report.md"),
            "Markdown File (*.md)",
        )
        if not file_name:
            return
        report_text = build_markdown_report(
            self.state,
            self.combo_analysis_type.currentText(),
            self.combo_linear_solver.currentText(),
        )
        Path(file_name).write_text(report_text, encoding="utf-8")
        self.log(f"分析报告已导出：{file_name}")

    def toggle_result_probe(self) -> None:
        """切换结果探针模式。"""

        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析。")
            return
        if not self._has_viewport():
            QMessageBox.warning(self, "视口未就绪", "当前可视化视口尚未初始化，请稍后再试。")
            return
        self.result_probe_enabled = not self.result_probe_enabled
        if not self.result_probe_enabled:
            self._clear_result_probe_marker()
            self._clear_result_probe_hover()
        self._update_result_form_state()
        if self.result_probe_enabled:
            self.log("结果探针已开启：在主视口中左键单击结果表面即可查询，拖动画面不会误触发。")
        else:
            self.log("结果探针已关闭。")

    def _clear_result_probe_marker(self) -> None:
        """移除结果探针的高亮标记。"""

        if self.result_probe_marker_actor is not None and self._has_viewport():
            try:
                self.viewport.remove_actor(self.result_probe_marker_actor)
            except Exception:
                pass
            self.result_probe_marker_actor = None
            self.viewport.render()

    def _clear_result_probe_hover(self) -> None:
        """清除探针悬停预高亮。"""

        self.result_probe_hover_node_id = -1
        if self.result_probe_hover_actor is not None and self._has_viewport():
            try:
                self.viewport.remove_actor(self.result_probe_hover_actor)
            except Exception:
                pass
            self.result_probe_hover_actor = None
            self.viewport.render()

    def _probe_reference_grid(self) -> pv.DataSet:
        """返回结果探针用于节点吸附的参考网格。"""

        return self._current_result_grid(deformed=True, point_data=True)

    def _pick_result_world_position(self, x_pos: float, y_pos: float) -> Optional[np.ndarray]:
        """在当前结果视图中拾取世界坐标。"""

        if not self._has_viewport() or not self.result_probe_enabled or self.current_scene != "result" or self.current_display_grid is None:
            return None
        # 这里优先拾取结果表面的任意位置，再吸附到最近节点。
        # 这样用户不需要非常精确地点到顶点上，探针会更接近商业软件的体验。
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.0005)
        vtk_x, vtk_y = self._viewport_pick_coordinates(x_pos, y_pos)
        picker.Pick(vtk_x, vtk_y, 0.0, self.viewport.renderer)
        if picker.GetCellId() < 0:
            return None
        return np.array(picker.GetPickPosition(), dtype=float)

    def _update_result_probe_hover(self, x_pos: float, y_pos: float) -> None:
        """鼠标悬停时预高亮最近节点，帮助用户判断当前会选中哪里。"""

        if not self._has_viewport() or not self.result_probe_enabled or self.current_scene != "result":
            return
        world_position = self._pick_result_world_position(x_pos, y_pos)
        if world_position is None:
            self._clear_result_probe_hover()
            return
        reference_grid = self._probe_reference_grid()
        node_id = int(reference_grid.find_closest_point(world_position))
        if node_id < 0:
            self._clear_result_probe_hover()
            return
        if node_id == self.result_probe_hover_node_id and self.result_probe_hover_actor is not None:
            return
        self._clear_result_probe_hover()
        self.result_probe_hover_node_id = node_id
        hover_point = np.asarray(reference_grid.points[node_id], dtype=float)
        bounds = reference_grid.bounds
        size = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4], 1.0e-6)
        hover_marker = pv.Sphere(radius=0.008 * size, center=hover_point)
        self.result_probe_hover_actor = self.viewport.add_mesh(
            hover_marker,
            color="#38BDF8",
            smooth_shading=True,
            opacity=0.95,
        )
        self.viewport.render()

    def _show_result_probe_at_position(self, world_position: np.ndarray) -> bool:
        """根据拾取到的位置显示结果探针信息。"""

        if not self._has_viewport() or self.current_display_grid is None or self.analysis is None:
            return False
        reference_grid = self._probe_reference_grid()
        reference_node_id = int(reference_grid.find_closest_point(world_position))
        if reference_node_id < 0:
            return False
        reference_point = np.asarray(reference_grid.points[reference_node_id], dtype=float)

        self._clear_result_probe_marker()
        self._clear_result_probe_hover()
        bounds = reference_grid.bounds
        size = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4], 1.0e-6)
        marker = pv.Sphere(radius=0.013 * size, center=reference_point)
        self.result_probe_marker_actor = self.viewport.add_mesh(
            marker,
            color="#FACC15",
            smooth_shading=True,
            opacity=1.0,
        )
        point_data = reference_grid.point_data
        displacement = point_data["Displacement"][reference_node_id] if "Displacement" in point_data else np.zeros(3, dtype=float)
        displacement_mag = point_data["DisplacementMagnitude"][reference_node_id] if "DisplacementMagnitude" in point_data else float(np.linalg.norm(displacement))
        stress_value = point_data["VonMisesStress"][reference_node_id] if "VonMisesStress" in point_data else 0.0
        strain_value = point_data["EquivalentStrain"][reference_node_id] if "EquivalentStrain" in point_data else 0.0
        temperature_value = point_data["Temperature"][reference_node_id] if "Temperature" in point_data else 0.0
        heat_flux_value = point_data["HeatFluxMagnitude"][reference_node_id] if "HeatFluxMagnitude" in point_data else 0.0

        probe_text = "\n".join(
            [
                "# 结果探针",
                "",
                f"- 当前显示结果：{self.current_display_title or self.combo_result_mode.currentText()}",
                f"- 最近原始节点 ID：{reference_node_id}",
                f"- 鼠标捕捉点坐标：[ {world_position[0]:.6e}, {world_position[1]:.6e}, {world_position[2]:.6e} ]",
                f"- 最近节点坐标：[ {reference_point[0]:.6e}, {reference_point[1]:.6e}, {reference_point[2]:.6e} ]",
                f"- 位移 Ux：{float(displacement[0]):.6e} m",
                f"- 位移 Uy：{float(displacement[1]):.6e} m",
                f"- 位移 Uz：{float(displacement[2]):.6e} m",
                f"- 位移幅值：{float(displacement_mag):.6e} m",
                f"- Von Mises 应力：{float(stress_value):.6e} Pa",
                f"- 等效应变：{float(strain_value):.6e}",
                f"- 温度：{float(temperature_value):.6e} °C",
                f"- 热流密度幅值：{float(heat_flux_value):.6e}",
            ]
        )
        if self.probe_dialog is None:
            self.probe_dialog = ProbeDialog(self)
        self.probe_dialog.set_probe_text(probe_text)
        self.probe_dialog.show()
        self.probe_dialog.raise_()
        self.probe_dialog.activateWindow()
        self.viewport.render()
        self.log(f"结果探针已更新：节点 {reference_node_id}，位移 {float(displacement_mag):.3e} m")
        return True

    def _format_table_value(self, value: float) -> str:
        """统一格式化表格中的浮点数。"""

        return f"{float(value):.6e}"

    def _build_result_table_sections(self) -> dict[str, tuple[list[str], list[list[str]], str]]:
        """把当前结果整理成可浏览的数据表集合。"""

        if not self.analysis:
            raise RuntimeError("当前没有可用分析结果。")

        sections: dict[str, tuple[list[str], list[list[str]], str]] = {}
        summary = self.analysis.summary
        sections["结果摘要"] = (
            ["项目", "数值"],
            [
                ["分析类型", self.combo_analysis_type.currentText()],
                ["求解器", self.combo_linear_solver.currentText()],
                ["最大位移 [m]", self._format_table_value(summary.max_displacement)],
                ["最大应力 [Pa]", self._format_table_value(summary.max_von_mises)],
                ["最大应变", self._format_table_value(summary.max_equivalent_strain)],
                ["平均应力 [Pa]", self._format_table_value(summary.mean_von_mises)],
                ["平均应变", self._format_table_value(summary.mean_equivalent_strain)],
                ["最高温度 [°C]", self._format_table_value(summary.max_temperature)],
                ["最低温度 [°C]", self._format_table_value(summary.min_temperature)],
                ["平均温度 [°C]", self._format_table_value(summary.mean_temperature)],
                ["最大热流密度", self._format_table_value(summary.max_heat_flux)],
                ["固定节点数", str(summary.fixed_node_count)],
                ["受载节点数", str(summary.loaded_node_count)],
                ["收敛", "是" if summary.converged else "否"],
                ["迭代次数", str(summary.iteration_count)],
                ["残差范数", self._format_table_value(summary.residual_norm)],
                ["求解耗时 [s]", self._format_table_value(summary.solve_time_seconds)],
            ],
            "汇总当前分析步的关键结果指标。",
        )

        max_preview_rows = 3000
        points = np.asarray(self.analysis.result_grid.points)
        displacements = np.asarray(self.analysis.displacement_matrix)
        temperatures = np.asarray(self.analysis.temperature_array) if self.analysis.temperature_array is not None else None
        node_rows: list[list[str]] = []
        preview_node_count = min(len(points), max_preview_rows)
        for node_id in range(preview_node_count):
            point = points[node_id]
            displacement = displacements[node_id]
            temperature_text = self._format_table_value(temperatures[node_id]) if temperatures is not None else ""
            node_rows.append(
                [
                    str(node_id),
                    self._format_table_value(point[0]),
                    self._format_table_value(point[1]),
                    self._format_table_value(point[2]),
                    self._format_table_value(displacement[0]),
                    self._format_table_value(displacement[1]),
                    self._format_table_value(displacement[2]),
                    self._format_table_value(np.linalg.norm(displacement)),
                    temperature_text,
                ]
            )
        sections["节点结果预览"] = (
            ["节点ID", "X", "Y", "Z", "Ux", "Uy", "Uz", "|U|", "Temperature"],
            node_rows,
            f"显示前 {preview_node_count} / {len(points)} 个节点结果，便于快速检查位移分布。",
        )

        element_rows: list[list[str]] = []
        heat_fluxes = np.asarray(self.analysis.heat_flux_array) if self.analysis.heat_flux_array is not None else None
        preview_element_count = min(len(self.analysis.stress_array), max_preview_rows)
        for element_id in range(preview_element_count):
            element_rows.append(
                [
                    str(element_id),
                    self._format_table_value(self.analysis.stress_array[element_id]),
                    self._format_table_value(self.analysis.strain_array[element_id]),
                    self._format_table_value(heat_fluxes[element_id]) if heat_fluxes is not None and element_id < len(heat_fluxes) else "",
                ]
            )
        sections["单元结果预览"] = (
            ["单元ID", "VonMises [Pa]", "EquivalentStrain", "HeatFluxMagnitude"],
            element_rows,
            f"显示前 {preview_element_count} / {len(self.analysis.stress_array)} 个单元结果。",
        )

        if summary.modal_frequencies_hz:
            mode_rows = [
                [str(index + 1), self._format_table_value(frequency)]
                for index, frequency in enumerate(summary.modal_frequencies_hz)
            ]
            sections["模态频率"] = (
                ["阶次", "频率 [Hz]"],
                mode_rows,
                "列出当前模态分析已经提取出的各阶固有频率。",
            )

        if self.analysis.transient_times is not None and self.analysis.transient_max_displacements is not None:
            transient_rows: list[list[str]] = []
            loaded_ux = self.analysis.transient_loaded_ux_history
            loaded_uy = self.analysis.transient_loaded_uy_history
            loaded_uz = self.analysis.transient_loaded_uz_history
            loaded_umag = self.analysis.transient_loaded_umag_history
            for index, time_value in enumerate(self.analysis.transient_times):
                transient_rows.append(
                    [
                        str(index),
                        self._format_table_value(time_value),
                        self._format_table_value(self.analysis.transient_max_displacements[index]),
                        self._format_table_value(loaded_ux[index] if loaded_ux is not None else 0.0),
                        self._format_table_value(loaded_uy[index] if loaded_uy is not None else 0.0),
                        self._format_table_value(loaded_uz[index] if loaded_uz is not None else 0.0),
                        self._format_table_value(loaded_umag[index] if loaded_umag is not None else 0.0),
                    ]
                )
            sections["瞬态响应时程"] = (
                ["步号", "时间 [s]", "全局最大位移", "受载面平均 Ux", "受载面平均 Uy", "受载面平均 Uz", "受载面平均 |U|"],
                transient_rows,
                f"瞬态动力学完整时程表，阻尼设置：{describe_dynamic_damping(self.state)}。",
            )

        if self.analysis.frequency_response_frequencies_hz is not None and self.analysis.frequency_response_max_displacements is not None:
            frequency_rows: list[list[str]] = []
            loaded_ux = self.analysis.frequency_response_loaded_ux_history
            loaded_uy = self.analysis.frequency_response_loaded_uy_history
            loaded_uz = self.analysis.frequency_response_loaded_uz_history
            loaded_umag = self.analysis.frequency_response_loaded_umag_history
            for index, frequency_hz in enumerate(self.analysis.frequency_response_frequencies_hz):
                frequency_rows.append(
                    [
                        str(index + 1),
                        self._format_table_value(frequency_hz),
                        self._format_table_value(self.analysis.frequency_response_max_displacements[index]),
                        self._format_table_value(loaded_ux[index] if loaded_ux is not None else 0.0),
                        self._format_table_value(loaded_uy[index] if loaded_uy is not None else 0.0),
                        self._format_table_value(loaded_uz[index] if loaded_uz is not None else 0.0),
                        self._format_table_value(loaded_umag[index] if loaded_umag is not None else 0.0),
                    ]
                )
            sections["频响响应表"] = (
                ["序号", "频率 [Hz]", "全局最大位移幅值", "受载面平均 Ux 幅值", "受载面平均 Uy 幅值", "受载面平均 Uz 幅值", "受载面平均 |U| 幅值"],
                frequency_rows,
                f"频响分析完整幅频表，阻尼设置：{describe_dynamic_damping(self.state)}。",
            )

        return sections

    def open_result_table_window(self) -> None:
        """打开结果数据表窗口。"""

        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析。")
            return
        if self.result_table_dialog is None:
            self.result_table_dialog = ResultTableDialog(self)
        self.result_table_dialog.set_sections(self._build_result_table_sections())
        self.result_table_dialog.show()
        self.result_table_dialog.raise_()
        self.result_table_dialog.activateWindow()

    def refresh_geometry_preview(self) -> None:
        if self._task_running():
            self.log(f"当前正在执行“{self._busy_task_name}”，几何预览刷新已跳过。")
            return
        self._sync_state_from_widgets()
        try:
            self.preview_scene = build_geometry_preview(self.state)
            self.lbl_cad_status.setText(f"当前使用：{self.preview_scene.source_label}")
            self._draw_geometry_scene()
            self._refresh_summary()
            self.log(f"几何预览已更新：{self.preview_scene.source_label}")
        except Exception as exc:
            QMessageBox.critical(self, "几何预览失败", str(exc))
            self.log(f"几何预览失败：{exc}")

    def _on_mesh_generated(self, result: object) -> None:
        mesh_bundle = result
        if not isinstance(mesh_bundle, MeshBundle):
            raise RuntimeError("后台网格生成返回了无法识别的结果对象。")
        self.mesh_bundle = mesh_bundle
        self.quality_bundle = None
        self.analysis = None
        self.state.mesh_summary = self.mesh_bundle.summary
        self.state.result_summary = ResultSummary()
        self._draw_mesh_scene()
        self._refresh_summary()
        self.log(
            f"网格生成完成：节点 {self.state.mesh_summary.node_count}，"
            f"显示单元 {self.state.mesh_summary.display_cell_count}（{self.state.mesh_summary.display_cell_type}），"
            f"求解单元 {self.state.mesh_summary.tetra_count}，"
            f"单元阶次 {self.state.geometry.element_order}"
        )

    def generate_mesh(self) -> None:
        if self._task_running():
            QMessageBox.information(self, "任务仍在执行", f"当前正在执行“{self._busy_task_name}”，请等待完成后再生成网格。")
            return
        self._sync_state_from_widgets()
        if self.state.geometry.mesh_topology == "hexa" and self.state.geometry.local_refine_enabled:
            self.log("提示：结构化六面体网格当前不支持局部加密，已按规则六面体网格生成。")
        state_snapshot = ProjectState.from_dict(self.state.to_dict())
        self._start_background_task(
            "网格生成",
            "正在生成网格，请稍候……",
            lambda: generate_volume_mesh(state_snapshot),
            self._on_mesh_generated,
        )

    def export_mesh(self) -> None:
        if not self.mesh_bundle:
            QMessageBox.warning(self, "没有网格", "请先生成网格。")
            return
        file_name, _ = QFileDialog.getSaveFileName(self, "导出网格 VTU", str(MODELS_DIR / "latest_mesh.vtu"), "VTU File (*.vtu)")
        if file_name:
            export_mesh_to_vtk(self.mesh_bundle, file_name)
            self.log(f"网格已导出：{file_name}")

    def check_mesh_quality(self) -> None:
        if not self.mesh_bundle:
            QMessageBox.warning(self, "没有网格", "请先生成三维网格。")
            return
        if self._task_running():
            QMessageBox.information(self, "任务仍在执行", f"当前正在执行“{self._busy_task_name}”，请等待完成后再检查网格质量。")
            return
        self._start_background_task(
            "网格质量检测",
            "正在计算网格质量，请稍候……",
            lambda: compute_mesh_quality(self.mesh_bundle),
            self._on_mesh_quality_ready,
        )

    def _on_mesh_quality_ready(self, result: object) -> None:
        quality_bundle = result
        if not isinstance(quality_bundle, MeshQualityBundle):
            raise RuntimeError("后台网格质量检测返回了无法识别的结果对象。")
        self.quality_bundle = quality_bundle
        self.state.mesh_summary = self.quality_bundle.summary
        self._draw_quality_scene()
        self._refresh_summary()
        self.log("网格质量检测完成。")

    def run_analysis_async(self) -> None:
        """以后台线程方式启动求解，避免主界面长时间无响应。"""

        if not self.mesh_bundle:
            QMessageBox.warning(self, "没有网格", "请先生成网格。")
            return
        if self._task_running():
            QMessageBox.information(self, "任务仍在执行", f"当前正在执行“{self._busy_task_name}”，请等待完成后再启动求解。")
            return
        self._sync_state_from_widgets()
        analysis_type = str(self.combo_analysis_type.currentData())
        if not self.state.material.is_applied:
            QMessageBox.warning(self, "材料未确认", "请先在材料模块点击“应用材料设置”。")
            return
        if analysis_type == "steady_state_thermal":
            if not self.state.thermal_boundary.is_applied:
                QMessageBox.warning(self, "热边界未确认", "请先在热模块点击“应用温度边界”。")
                return
            if self.state.thermal_load.heat_power != 0.0 and not self.state.thermal_load.is_applied:
                QMessageBox.warning(self, "热载荷未确认", "请先在热模块点击“应用热流载荷”。")
                return
        if analysis_type not in {"modal_analysis", "steady_state_thermal"} and not self.state.load_case.is_applied:
            QMessageBox.warning(self, "载荷未确认", "请先在载荷与边界模块点击“应用载荷设置”。")
            return
        if analysis_type != "steady_state_thermal" and not self.state.boundary_condition.is_applied:
            QMessageBox.warning(self, "边界未确认", "请先在载荷与边界模块点击“应用边界设置”。")
            return
        state_snapshot = ProjectState.from_dict(self.state.to_dict())
        self._start_background_task(
            "求解分析",
            "正在求解，请稍候……",
            lambda: (state_snapshot, run_linear_static_analysis(self.mesh_bundle, state_snapshot)),
            self._on_analysis_finished,
        )

    def run_analysis(self) -> None:
        if not self.mesh_bundle:
            QMessageBox.warning(self, "没有网格", "请先生成网格。")
            return
        if self._task_running():
            QMessageBox.information(self, "任务仍在执行", f"当前正在执行“{self._busy_task_name}”，请等待完成后再启动求解。")
            return
        self._sync_state_from_widgets()
        analysis_type = str(self.combo_analysis_type.currentData())
        if not self.state.material.is_applied:
            QMessageBox.warning(self, "材料未确认", "请先在材料模块点击“应用材料设置”。")
            return
        if analysis_type == "steady_state_thermal":
            if not self.state.thermal_boundary.is_applied:
                QMessageBox.warning(self, "热边界未确认", "请先在热模块点击“应用温度边界”。")
                return
            if self.state.thermal_load.heat_power != 0.0 and not self.state.thermal_load.is_applied:
                QMessageBox.warning(self, "热载荷未确认", "请先在热模块点击“应用热流载荷”。")
                return
        if analysis_type not in {"modal_analysis", "steady_state_thermal"} and not self.state.load_case.is_applied:
            QMessageBox.warning(self, "荷载未确认", "请先在载荷与边界模块点击“应用荷载设置”。")
            return
        if analysis_type != "steady_state_thermal" and not self.state.boundary_condition.is_applied:
            QMessageBox.warning(self, "边界未确认", "请先在载荷与边界模块点击“应用边界设置”。")
            return
        try:
            self.analysis = run_linear_static_analysis(self.mesh_bundle, self.state)
            self.state.result_summary = self.analysis.summary
            self.current_modal_mode_index = 0
            self.current_result_mode = "temperature" if analysis_type == "steady_state_thermal" else self.current_result_mode
            modal_count = len(self.analysis.modal_shapes) if self.analysis.modal_shapes else 1
            self.spin_mode_index.setRange(1, max(modal_count, 1))
            self.spin_mode_index.setValue(1)
            self._update_result_form_state()
            self.restore_full_result()
            self._refresh_summary()
            if analysis_type == "modal_analysis" and self.analysis.summary.modal_frequencies_hz:
                first_frequency = self.analysis.summary.modal_frequencies_hz[0]
                self.log(f"{self.combo_analysis_type.currentText()}完成：已提取 {len(self.analysis.summary.modal_frequencies_hz)} 阶模态，一阶频率 {first_frequency:.3f} Hz")
            elif analysis_type == "transient_dynamic" and self.analysis.summary.transient_step_count > 0:
                self.log(f"{self.combo_analysis_type.currentText()}完成：{self.analysis.summary.transient_step_count} 个时间步，总时长 {self.analysis.summary.transient_total_time:.6f} s，峰值位移 {self.analysis.summary.max_displacement:.3e} m，阻尼={describe_dynamic_damping(self.state)}")
            elif analysis_type == "frequency_response" and self.analysis.summary.frequency_response_count > 0:
                self.log(f"{self.combo_analysis_type.currentText()}完成：{self.analysis.summary.frequency_response_count} 个频率点，峰值频率 {self.analysis.summary.peak_response_frequency_hz:.3f} Hz，峰值位移 {self.analysis.summary.max_displacement:.3e} m，阻尼={describe_dynamic_damping(self.state)}")
            elif analysis_type == "steady_state_thermal":
                self.log(f"{self.combo_analysis_type.currentText()}完成：最高温度 {self.analysis.summary.max_temperature:.3f} °C，最低温度 {self.analysis.summary.min_temperature:.3f} °C，最大热流密度 {self.analysis.summary.max_heat_flux:.3e}")
            else:
                self.log(f"{self.combo_analysis_type.currentText()}完成：最大位移 {self.analysis.summary.max_displacement:.3e} m，最大应力 {self.analysis.summary.max_von_mises:.3e} Pa，迭代 {self.analysis.summary.iteration_count} 次")
        except Exception as exc:
            QMessageBox.critical(self, "分析失败", str(exc))
            self.log(f"分析失败：{exc}")

    def _on_analysis_finished(self, result: object) -> None:
        """接收后台求解结果并刷新界面。"""

        if not isinstance(result, tuple) or len(result) != 2:
            raise RuntimeError("后台求解返回了无法识别的结果对象。")
        state_snapshot, analysis = result
        analysis_artifacts_type = get_analysis_artifacts_type()
        if not isinstance(state_snapshot, ProjectState) or not isinstance(analysis, analysis_artifacts_type):
            raise RuntimeError("后台求解返回对象类型不正确。")

        self.analysis = analysis
        self.state.solver.linear_solver = state_snapshot.solver.linear_solver
        self.state.solver.num_threads = state_snapshot.solver.num_threads
        self.state.result_summary = self.analysis.summary
        self.current_modal_mode_index = 0
        analysis_type = self.state.solver.analysis_type
        self.current_result_mode = "temperature" if analysis_type == "steady_state_thermal" else self.current_result_mode
        modal_count = len(self.analysis.modal_shapes) if self.analysis.modal_shapes else 1
        self.spin_mode_index.setRange(1, max(modal_count, 1))
        self.spin_mode_index.setValue(1)
        self._update_result_form_state()
        self.restore_full_result()
        self._refresh_summary()
        if analysis_type == "modal_analysis" and self.analysis.summary.modal_frequencies_hz:
            first_frequency = self.analysis.summary.modal_frequencies_hz[0]
            self.log(f"{self.combo_analysis_type.currentText()}完成：已提取 {len(self.analysis.summary.modal_frequencies_hz)} 阶模态，一阶频率 {first_frequency:.3f} Hz")
        elif analysis_type == "transient_dynamic" and self.analysis.summary.transient_step_count > 0:
            self.log(f"{self.combo_analysis_type.currentText()}完成：{self.analysis.summary.transient_step_count} 个时间步，总时长 {self.analysis.summary.transient_total_time:.6f} s，峰值位移 {self.analysis.summary.max_displacement:.3e} m，阻尼 {describe_dynamic_damping(self.state)}")
        elif analysis_type == "frequency_response" and self.analysis.summary.frequency_response_count > 0:
            self.log(f"{self.combo_analysis_type.currentText()}完成：{self.analysis.summary.frequency_response_count} 个频率点，峰值频率 {self.analysis.summary.peak_response_frequency_hz:.3f} Hz，峰值位移 {self.analysis.summary.max_displacement:.3e} m，阻尼 {describe_dynamic_damping(self.state)}")
        elif analysis_type == "steady_state_thermal":
            self.log(f"{self.combo_analysis_type.currentText()}完成：最高温度 {self.analysis.summary.max_temperature:.3f} °C，最低温度 {self.analysis.summary.min_temperature:.3f} °C，最大热流密度 {self.analysis.summary.max_heat_flux:.3e}")
        else:
            self.log(f"{self.combo_analysis_type.currentText()}完成：最大位移 {self.analysis.summary.max_displacement:.3e} m，最大应力 {self.analysis.summary.max_von_mises:.3e} Pa，迭代 {self.analysis.summary.iteration_count} 次")

    def _result_mode_changed(self) -> None:
        self.current_result_mode = str(self.combo_result_mode.currentData())
        if self.analysis:
            self.show_result_mode(self.current_result_mode)

    def show_result_mode(self, mode: str) -> None:
        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析。")
            return
        thermal = self.state.solver.analysis_type == "steady_state_thermal"
        if thermal and mode not in {"temperature", "heat_flux"}:
            QMessageBox.information(self, "热分析结果限制", "稳态热分析当前仅支持温度和热流密度显示。")
            mode = "temperature"
        if self.analysis.modal_shapes and mode != "displacement":
            QMessageBox.information(self, "模态结果限制", "模态分析当前仅支持位移振型显示。")
            mode = "displacement"
        self.current_result_mode = mode
        self.current_result_view = "full"
        self._set_combo_by_data(self.combo_result_mode, mode)
        scalar = RESULT_SCALARS[mode]
        title, cmap = self._result_metadata(mode)
        result_grid = self._current_result_grid(deformed=True, point_data=False)
        self._draw_result_scene(result_grid, scalar, title, cmap, outline_deformed=True)

    def show_result_slice(self) -> None:
        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析。")
            return
        self.current_result_view = "slice"
        source = self._current_result_grid(deformed=self.check_slice_deformed.isChecked(), point_data=False)
        axis = str(self.combo_slice_axis.currentData())
        ratio = self.spin_slice_ratio.value() / 100.0
        bounds = source.bounds
        axis_index = {"x": 0, "y": 1, "z": 2}[axis]
        origin = [0.5 * (bounds[0] + bounds[1]), 0.5 * (bounds[2] + bounds[3]), 0.5 * (bounds[4] + bounds[5])]
        origin[axis_index] = bounds[axis_index * 2] + (bounds[axis_index * 2 + 1] - bounds[axis_index * 2]) * ratio
        sliced = source.slice(normal={"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis], origin=origin)
        if sliced.n_points == 0:
            QMessageBox.warning(self, "切片失败", "当前切片位置没有截取到有效结果。")
            return
        scalar = RESULT_SCALARS[self.current_result_mode]
        title, cmap = self._result_metadata(self.current_result_mode)
        self._draw_result_scene(sliced, scalar, title, cmap, outline_deformed=self.check_slice_deformed.isChecked())
        self.log(f"结果切片已更新：方向 {self.combo_slice_axis.currentText()}，位置 {self.spin_slice_ratio.value():.1f}%")

    def show_result_contours(self) -> None:
        """显示当前结果量的等值面。"""

        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析。")
            return
        self.current_result_view = "contour"
        mode = str(self.combo_result_mode.currentData())
        if self.analysis.modal_shapes and mode != "displacement":
            QMessageBox.information(self, "模态结果限制", "模态分析当前仅支持位移振型显示。")
            mode = "displacement"
            self._set_combo_by_data(self.combo_result_mode, mode)
        scalar = RESULT_SCALARS[mode]
        title, cmap = self._result_metadata(mode)
        contour_source = self._current_result_grid(deformed=True, point_data=True)
        scalar_values = np.asarray(contour_source.point_data[scalar], dtype=float)
        scalar_values = scalar_values[np.isfinite(scalar_values)]
        if scalar_values.size == 0:
            QMessageBox.warning(self, "等值面失败", "当前结果没有可用的标量数据。")
            return
        scalar_min = float(np.min(scalar_values))
        scalar_max = float(np.max(scalar_values))
        if abs(scalar_max - scalar_min) <= 1.0e-12:
            QMessageBox.warning(self, "等值面失败", "当前结果标量几乎没有变化，无法提取有效等值面。")
            return
        requested_count = self.spin_contour_count.value()
        contour_levels = np.linspace(scalar_min, scalar_max, requested_count + 2, dtype=float)[1:-1]
        contours = contour_source.contour(isosurfaces=contour_levels.tolist(), scalars=scalar)
        if contours.n_points == 0:
            QMessageBox.warning(self, "等值面失败", "当前结果无法提取有效等值面。")
            return
        self._draw_result_scene(contours, scalar, title, cmap, outline_deformed=True)
        self.log(f"已请求显示 {requested_count} 层等值面：{self.combo_result_mode.currentText()}")

    def restore_full_result(self) -> None:
        self.current_result_view = "full"
        self.show_result_mode(str(self.combo_result_mode.currentData()))

    def show_selected_mode(self) -> None:
        """显示右侧结果页指定的模态阶次。"""

        if not self.analysis or not self.analysis.modal_shapes:
            QMessageBox.warning(self, "没有模态结果", "请先运行模态分析。")
            return
        requested_index = self.spin_mode_index.value() - 1
        if requested_index < 0 or requested_index >= len(self.analysis.modal_shapes):
            QMessageBox.warning(self, "阶次无效", "所选模态阶次超出当前结果范围。")
            return
        self.current_modal_mode_index = requested_index
        self.current_result_mode = "displacement"
        self.restore_full_result()
        if requested_index < len(self.analysis.summary.modal_frequencies_hz):
            frequency = self.analysis.summary.modal_frequencies_hz[requested_index]
            self.log(f"已切换到第 {requested_index + 1} 阶模态，频率 {frequency:.3f} Hz")

    def open_response_history_window(self) -> None:
        """弹出瞬态分析的时间历程曲线窗口。"""

        if not self.analysis or self.analysis.transient_times is None or self.analysis.transient_max_displacements is None:
            QMessageBox.warning(self, "没有动力响应", "请先运行瞬态动力学分析。")
            return
        if self.plot_dialog is None:
            self.plot_dialog = PlotDialog(self)
        times, values, y_label = self._transient_curve_data()
        self.plot_dialog.setWindowTitle("动力响应曲线")
        self.plot_dialog.plot_curve(
            times,
            values,
            f"瞬态动力学曲线 - {self.combo_curve_response.currentText()}",
            "时间 [s]",
            y_label,
            "#2563EB",
        )
        self.plot_dialog.show()
        self.plot_dialog.raise_()
        self.plot_dialog.activateWindow()

    def open_frequency_response_plot(self) -> None:
        """弹出频响曲线图窗口。"""

        if (
            not self.analysis or
            self.analysis.frequency_response_frequencies_hz is None or
            self.analysis.frequency_response_max_displacements is None
        ):
            QMessageBox.warning(self, "没有频响结果", "请先运行频响分析。")
            return
        if self.plot_dialog is None:
            self.plot_dialog = PlotDialog(self)
        frequencies, values, y_label = self._frequency_curve_data()
        self.plot_dialog.setWindowTitle("频响曲线")
        self.plot_dialog.plot_curve(
            frequencies,
            values,
            f"频响曲线 - {self.combo_curve_response.currentText()}",
            "频率 [Hz]",
            y_label,
            "#DC2626",
        )
        self.plot_dialog.show()
        self.plot_dialog.raise_()
        self.plot_dialog.activateWindow()

    def export_response_history_csv(self) -> None:
        """导出瞬态分析的时间历程曲线。"""

        if not self.analysis or self.analysis.transient_times is None or self.analysis.transient_max_displacements is None:
            QMessageBox.warning(self, "没有动力响应", "请先运行瞬态动力学分析。")
            return
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "导出动力曲线 CSV",
            str(RESULTS_DIR / "transient_history.csv"),
            "CSV File (*.csv)",
        )
        if not file_name:
            return
        times, values, _label = self._transient_curve_data()
        history = np.column_stack((times, values))
        np.savetxt(file_name, history, delimiter=",", header="Time,ResponseValue", comments="")
        self.log(f"动力曲线 CSV 已导出：{file_name}")

    def export_frequency_response_csv(self) -> None:
        """导出频响分析的幅频曲线。"""

        if (
            not self.analysis or
            self.analysis.frequency_response_frequencies_hz is None or
            self.analysis.frequency_response_max_displacements is None
        ):
            QMessageBox.warning(self, "没有频响结果", "请先运行频响分析。")
            return
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "导出频响曲线 CSV",
            str(RESULTS_DIR / "frequency_response.csv"),
            "CSV File (*.csv)",
        )
        if not file_name:
            return
        frequencies, values, _label = self._frequency_curve_data()
        history = np.column_stack((frequencies, values))
        np.savetxt(file_name, history, delimiter=",", header="FrequencyHz,ResponseValue", comments="")
        self.log(f"频响曲线 CSV 已导出：{file_name}")

    def save_project(self) -> None:
        self._sync_state_from_widgets()
        file_name, _ = QFileDialog.getSaveFileName(self, "保存项目", str(DATA_DIR / "project_state.json"), "CAE Project (*.json)")
        if file_name:
            path = Path(file_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"项目已保存：{path}")

    def load_project(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "打开项目", str(DATA_DIR), "CAE Project (*.json)")
        if file_name:
            path = Path(file_name)
            self.state = ProjectState.from_dict(json.loads(path.read_text(encoding="utf-8")))
            self.mesh_bundle = None
            self.quality_bundle = None
            self.analysis = None
            self._sync_widgets_from_state()
            self.refresh_geometry_preview()
            self._refresh_summary()
            self.log(f"项目已加载：{path}")

    def export_results_csv(self) -> None:
        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析。")
            return
        file_name, _ = QFileDialog.getSaveFileName(self, "导出结果 CSV", self.state.result_summary.export_file or str(RESULTS_DIR / "export_results.csv"), "CSV File (*.csv)")
        if file_name:
            self.analysis.solver.exportResults(file_name)
            self.state.result_summary.export_file = file_name
            self._refresh_summary()
            self.log(f"结果 CSV 已导出：{file_name}")

    def export_results_vtu(self) -> None:
        if not self.analysis:
            QMessageBox.warning(self, "没有结果", "请先运行分析。")
            return
        file_name, _ = QFileDialog.getSaveFileName(self, "导出结果 VTU", self.state.result_summary.export_vtk_file or str(RESULTS_DIR / "export_results.vtu"), "VTU File (*.vtu)")
        if file_name:
            self._current_result_grid(deformed=False, point_data=False).save(file_name)
            self.state.result_summary.export_vtk_file = file_name
            self._refresh_summary()
            self.log(f"结果 VTU 已导出：{file_name}")

    def import_cad(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "导入 CAD", "", "CAD Files (*.step *.stp *.iges *.igs)")
        if file_name:
            self.state.geometry.mode = "cad"
            self.state.geometry.cad_file = file_name
            self._clear_mesh_and_results()
            self._sync_widgets_from_state()
            self.refresh_geometry_preview()
            self.log(f"已导入 CAD 文件：{file_name}")

    def reset_to_parametric(self) -> None:
        self.state.geometry.mode = "box"
        self.state.geometry.cad_file = ""
        self._clear_mesh_and_results()
        self._sync_widgets_from_state()
        self.refresh_geometry_preview()
        self.log("已恢复为参数化几何模式。")

    def _clear_mesh_and_results(self) -> None:
        self.mesh_bundle = None
        self.quality_bundle = None
        self.analysis = None
        self.current_modal_mode_index = 0
        self.state.mesh_summary = MeshSummary()
        self.state.result_summary = ResultSummary()
        if hasattr(self, "spin_mode_index"):
            self.spin_mode_index.setRange(1, 1)
            self.spin_mode_index.setValue(1)
            self._update_result_form_state()

    def apply_material_preset(self, name: str) -> None:
        presets = {
            "结构钢": ("结构钢", 2.10e11, 0.30, 7850.0, 3.50e8, 1.00e9),
            "铝合金": ("铝合金", 7.00e10, 0.33, 2700.0, 2.40e8, 8.00e8),
            "混凝土": ("混凝土", 3.00e10, 0.20, 2500.0, 4.00e7, 2.00e8),
        }
        if name in presets:
            mat, young, nu, density, yield_strength, hardening = presets[name]
            self.combo_material_name.setCurrentText(mat)
            self.spin_E.setValue(young)
            self.spin_nu.setValue(nu)
            self.spin_density.setValue(density)
            self.spin_yield_strength.setValue(yield_strength)
            self.spin_hardening_modulus.setValue(hardening)
            self._sync_state_from_widgets()

    def reset_active_camera(self) -> None:
        if not self._has_viewport():
            return
        self.viewport.reset_camera()

    def view_isometric(self) -> None:
        if not self._has_viewport():
            return
        self.viewport.view_isometric()

    def view_front(self) -> None:
        if not self._has_viewport():
            return
        self.viewport.view_yz()

    def view_right(self) -> None:
        if not self._has_viewport():
            return
        self.viewport.view_xz()

    def view_top(self) -> None:
        if not self._has_viewport():
            return
        self.viewport.view_xy()

    def _on_tree_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """左侧模型树只负责切换右侧属性页，避免同一次点击重复刷新三维场景。"""

        mapping = {
            "几何": 0,
            "材料": 1,
            "载荷与边界": 2,
            "热模块": 3,
            "网格": 4,
            "分析步": 5,
            "结果": 6,
            "视图": 7,
        }
        name = item.text(0)
        if name in mapping:
            self.property_stack.setCurrentIndex(mapping[name])

    def showEvent(self, event) -> None:  # type: ignore[override]
        """主窗口显示后再调度首屏三维视口初始化，避免首帧渲染过早发生。"""

        super().showEvent(event)
        if not self._startup_initialized:
            QTimer.singleShot(120, self._deferred_startup_initialize)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._task_running():
            QMessageBox.information(self, "任务仍在执行", f"当前正在执行“{self._busy_task_name}”，请等待完成后再关闭软件。")
            event.ignore()
            return
        try:
            if self._has_viewport():
                self.viewport.close()
        finally:
            sys.stdout = self._stdout
        event.accept()


def main() -> None:
    write_startup_trace("main entry")
    qInstallMessageHandler(qt_message_handler)
    pv.global_theme.allow_empty_mesh = True
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    write_startup_trace("create QApplication")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 让 Ctrl+C / VSCode 停止按钮优雅退出 Qt 事件循环，避免控制台留下 KeyboardInterrupt traceback。
    signal.signal(signal.SIGINT, lambda *_args: app.quit())
    signal_timer = QTimer()
    signal_timer.setInterval(200)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start()
    app._signal_timer = signal_timer  # type: ignore[attr-defined]
    write_startup_trace("create main window")
    window = CAEMainWindow()
    write_startup_trace("show main window")
    window.showMaximized()
    write_startup_trace("enter Qt event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
