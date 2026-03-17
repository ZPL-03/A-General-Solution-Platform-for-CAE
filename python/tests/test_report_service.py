"""分析报告服务测试。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from app.models import ProjectState
from app.services.report_service import build_markdown_report


def test_build_markdown_report_contains_core_sections() -> None:
    """验证报告文本包含核心章节和关键结果字段。"""

    state = ProjectState()
    state.project_name = "测试项目"
    state.geometry.mesh_topology = "hexa"
    state.mesh_summary.node_count = 128
    state.mesh_summary.display_cell_count = 64
    state.mesh_summary.tetra_count = 320
    state.result_summary.max_displacement = 1.23e-3
    state.result_summary.max_von_mises = 2.34e8
    state.result_summary.converged = True
    state.solver.analysis_type = "transient_dynamic"
    state.solver.damping_mode = "modal_ratio"
    state.solver.modal_damping_ratio = 0.03
    state.solver.modal_damping_freq1_hz = 20.0
    state.solver.modal_damping_freq2_hz = 80.0

    report = build_markdown_report(state, "线性瞬态动力学", "SparseLU 直接法")

    assert "# 测试项目 分析报告" in report
    assert "## 几何与网格" in report
    assert "## 材料与工况" in report
    assert "## 求解设置" in report
    assert "## 结果摘要" in report
    assert "线性瞬态动力学" in report
    assert "动态阻尼" in report
    assert "最大位移" in report
