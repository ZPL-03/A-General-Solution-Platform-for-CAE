"""分析报告生成服务。

这一层负责把当前项目状态与结果摘要整理成 Markdown 报告，
便于：
1. 导出阶段性分析记录；
2. 后续扩展成 HTML / PDF；
3. 让主窗口保持更清晰，不把大段报告拼装逻辑混在界面代码里。
"""

from __future__ import annotations

from datetime import datetime

from app.models import ProjectState


def _section(title: str, lines: list[str]) -> str:
    """生成 Markdown 二级标题段落。"""

    return "\n".join([f"## {title}", "", *lines, ""])


def build_markdown_report(
    state: ProjectState,
    analysis_label: str,
    solver_label: str,
) -> str:
    """根据当前项目状态生成 Markdown 报告文本。"""

    # 报告导出不是高频启动路径。
    # 这里延迟导入求解服务，避免桌面软件启动阶段就加载 `fem_core` 等原生模块。
    from app.services.solver_service import describe_dynamic_damping

    state.ensure_default_entities()
    mesh = state.mesh_summary
    result = state.result_summary
    geometry = state.geometry
    material = state.material
    load_case = state.load_case
    boundary = state.boundary_condition
    solver = state.solver

    lines: list[str] = [
        f"# {state.project_name} 分析报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 分析类型：{analysis_label}",
        f"- 求解器：{solver_label}",
        "",
    ]

    lines.append(
        _section(
            "几何与网格",
            [
                f"- 几何模式：{'外部 CAD' if geometry.mode == 'cad' and geometry.cad_file else '参数化几何'}",
                f"- 几何类型：{geometry.primitive}",
                f"- 主要尺寸：L={geometry.length:.6f} m, W={geometry.width:.6f} m, H={geometry.height:.6f} m, R={geometry.radius:.6f} m",
                f"- 网格拓扑：{geometry.mesh_topology}",
                f"- 网格尺寸：{geometry.mesh_size:.6f} m",
                f"- 单元阶次：{geometry.element_order}",
                f"- 节点数：{mesh.node_count}",
                f"- 显示单元数：{mesh.display_cell_count} ({mesh.display_cell_type})",
                f"- 求解单元数：{mesh.tetra_count}",
                f"- 网格质量最小/平均/最大：{mesh.quality_min if mesh.quality_min is not None else 'N/A'} / {mesh.quality_avg if mesh.quality_avg is not None else 'N/A'} / {mesh.quality_max if mesh.quality_max is not None else 'N/A'}",
            ],
        )
    )

    lines.append(
        _section(
            "材料与工况",
            [
                f"- 材料名称：{material.name}",
                f"- 杨氏模量：{material.young_modulus:.6e} Pa",
                f"- 泊松比：{material.poisson_ratio:.6f}",
                f"- 密度：{material.density:.6f} kg/m^3",
                f"- 导热系数：{material.thermal_conductivity:.6f} W/(m·K)",
                f"- 材料非线性：{'开启' if material.nonlinear_enabled else '关闭'}",
                f"- 屈服强度：{material.yield_strength:.6e} Pa",
                f"- 硬化模量：{material.hardening_modulus:.6e} Pa",
                f"- 荷载工况：{load_case.name}",
                f"- 总载荷：Fx={load_case.force_x:.6e} N, Fy={load_case.force_y:.6e} N, Fz={load_case.force_z:.6e} N",
                f"- 受载表面：{load_case.loaded_face or '未选择'}",
                f"- 边界条件：{boundary.name}",
                f"- 约束表面：{boundary.target_face or '未选择'}",
                f"- 位移约束：Ux={'开' if boundary.constrain_x else '关'} ({boundary.displacement_x:.6e} m), "
                f"Uy={'开' if boundary.constrain_y else '关'} ({boundary.displacement_y:.6e} m), "
                f"Uz={'开' if boundary.constrain_z else '关'} ({boundary.displacement_z:.6e} m)",
            ],
        )
    )

    solver_lines = [
        f"- 分析步：{analysis_label}",
        f"- 求解器：{solver_label}",
        f"- CPU 并行（OpenMP）：{'开启' if solver.use_parallel else '关闭'}",
        f"- CPU 线程数：{solver.num_threads}",
        f"- 变形放大倍数：{solver.warp_factor:.3f}",
    ]
    if solver.analysis_type == "nonlinear_static":
        solver_lines.extend(
            [
                f"- 载荷步数：{solver.load_steps}",
                f"- 最大迭代数：{solver.max_iterations}",
                f"- 收敛容差：{solver.tolerance:.6e}",
            ]
        )
    if solver.analysis_type == "modal_analysis":
        solver_lines.append(f"- 提取模态阶数：{solver.modal_count}")
    if solver.analysis_type == "transient_dynamic":
        solver_lines.extend(
            [
                f"- 总时长：{solver.total_time:.6e} s",
                f"- 时间步长：{solver.time_step:.6e} s",
                f"- Newmark β：{solver.newmark_beta:.6f}",
                f"- Newmark γ：{solver.newmark_gamma:.6f}",
                f"- 动态阻尼：{describe_dynamic_damping(state)}",
            ]
        )
    if solver.analysis_type == "frequency_response":
        solver_lines.extend(
            [
                f"- 起始频率：{solver.frequency_start_hz:.6f} Hz",
                f"- 终止频率：{solver.frequency_end_hz:.6f} Hz",
                f"- 频率点数：{solver.frequency_point_count}",
                f"- 动态阻尼：{describe_dynamic_damping(state)}",
            ]
        )
    if solver.analysis_type == "steady_state_thermal":
        solver_lines.extend(
            [
                f"- 固定温度表面：{state.thermal_boundary.target_face or '未选择'}",
                f"- 固定温度：{state.thermal_boundary.temperature:.6f} °C",
                f"- 热流表面：{state.thermal_load.target_face or '未选择'}",
                f"- 总热输入：{state.thermal_load.heat_power:.6e} W",
                "- 说明：当前稳态热模块中的“温度边界”表示固定温度边界，不是整个实体的初始温度场。",
            ]
        )
    lines.append(_section("求解设置", solver_lines))

    result_lines = [
        f"- 最大位移：{result.max_displacement:.6e} m",
        f"- 最小 Z 位移：{result.min_z_displacement:.6e} m",
        f"- 最大 Von Mises 应力：{result.max_von_mises:.6e} Pa",
        f"- 平均 Von Mises 应力：{result.mean_von_mises:.6e} Pa",
        f"- 最大等效应变：{result.max_equivalent_strain:.6e}",
        f"- 平均等效应变：{result.mean_equivalent_strain:.6e}",
        f"- 固定节点数：{result.fixed_node_count}",
        f"- 受载节点数：{result.loaded_node_count}",
        f"- 求解耗时：{result.solve_time_seconds:.6f} s",
        f"- 收敛状态：{'收敛' if result.converged else '未收敛/未求解'}",
        f"- 迭代次数：{result.iteration_count}",
        f"- 残差范数：{result.residual_norm:.6e}",
    ]
    if result.modal_frequencies_hz:
        preview = ", ".join(f"{value:.3f}" for value in result.modal_frequencies_hz[:10])
        result_lines.append(f"- 模态频率预览 [Hz]：{preview}")
    if result.transient_step_count > 0:
        result_lines.append(f"- 瞬态步数：{result.transient_step_count}，总时长 {result.transient_total_time:.6f} s")
    if result.frequency_response_count > 0:
        result_lines.append(f"- 频响点数：{result.frequency_response_count}，峰值频率 {result.peak_response_frequency_hz:.6f} Hz")
    if solver.analysis_type == "steady_state_thermal":
        result_lines.extend(
            [
                f"- 最高温度：{result.max_temperature:.6e} °C",
                f"- 最低温度：{result.min_temperature:.6e} °C",
                f"- 平均温度：{result.mean_temperature:.6e} °C",
                f"- 最大热流密度：{result.max_heat_flux:.6e}",
            ]
        )
    if result.export_file:
        result_lines.append(f"- CSV 结果文件：{result.export_file}")
    if result.export_vtk_file:
        result_lines.append(f"- VTU 结果文件：{result.export_vtk_file}")
    lines.append(_section("结果摘要", result_lines))

    return "\n".join(lines).strip() + "\n"
