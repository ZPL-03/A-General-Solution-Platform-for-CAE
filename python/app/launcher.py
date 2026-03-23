"""
桌面软件启动入口。
作用：
1. 统一源码运行和 PyInstaller 打包运行的入口脚本；
2. 捕获启动阶段异常，写入软件目录下的 `startup_error.log`；
3. 在 Windows 上弹出明确的错误提示，避免出现“双击无反应”的静默失败。
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


STARTUP_TRACE_ENABLED = os.environ.get("CAE_STARTUP_TRACE", "0") == "1"


def _source_python_root() -> Path:
    """返回源码模式下的 `python/` 目录。"""

    return Path(__file__).resolve().parents[1]


def _app_root() -> Path:
    """
    返回软件运行目录。
    说明：
    1. 源码模式下使用仓库根目录；
    2. 打包模式下使用 EXE 所在目录；
    3. `startup_error.log` 会统一写到这个目录中，方便用户查找。
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _bootstrap_import_path() -> None:
    """
    补齐源码模式下的导入路径。
    说明：
    1. 直接运行本文件时，Python 默认只会把 `python/app/` 放入 `sys.path`；
    2. 这里显式补上 `python/`，确保 `import app.xxx` 稳定可用；
    3. 打包模式下这一步无害，不会影响 PyInstaller 的导入逻辑。
    """

    python_root = _source_python_root()
    python_root_text = str(python_root)
    if python_root_text not in sys.path:
        sys.path.insert(0, python_root_text)


def _startup_log_path() -> Path:
    """返回启动异常日志路径。"""

    return _app_root() / "startup_error.log"


def _startup_trace_path() -> Path:
    """返回启动轨迹日志路径。"""

    return _app_root() / "startup_trace.log"


def _write_startup_log() -> Path:
    """把当前异常写入软件目录，便于后续定位问题。"""

    log_path = _startup_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    traceback_text = traceback.format_exc()
    log_text = (
        "CAE Analysis Workbench startup failure\n"
        f"time={datetime.now().isoformat(timespec='seconds')}\n"
        f"python_executable={sys.executable}\n"
        f"frozen={getattr(sys, 'frozen', False)}\n"
        f"cwd={os.getcwd()}\n"
        f"app_root={_app_root()}\n"
        "\n"
        "Traceback:\n"
        f"{traceback_text}"
    )
    log_path.write_text(log_text, encoding="utf-8")
    return log_path


def _write_startup_trace(message: str) -> None:
    """
    记录启动轨迹。
    说明：
    1. 只在 `CAE_STARTUP_TRACE=1` 时启用；
    2. 由启动器负责写入，便于区分“还没进入主程序”就崩溃的场景；
    3. 每次写入都立即刷新，尽量保留崩溃前最后一个检查点。
    """

    if not STARTUP_TRACE_ENABLED:
        return

    trace_path = _startup_trace_path()
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} | {message}\n")
        handle.flush()


def _show_startup_error(log_path: Path) -> None:
    """在 Windows 上弹出错误提示，同时把信息写到标准错误输出。"""

    message = (
        "软件启动失败，详细错误已写入：\n"
        f"{log_path}\n\n"
        "请把该日志提供给开发者继续定位。"
    )

    try:
        if sys.platform.startswith("win"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "CAE Analysis Workbench", 0x10)
    except Exception:
        pass

    sys.__stderr__.write(message + "\n")


def _run_diagnostic_imports() -> None:
    """
    按步骤导入关键模块，仅用于定位打包版启动崩溃。
    说明：
    1. 只有 `CAE_STARTUP_TRACE=1` 时才会执行；
    2. 正常用户启动不走这条路径，避免影响正式版启动时序；
    3. 每一步前后都记录轨迹，便于判断崩溃停在哪个模块。
    """

    _write_startup_trace("import numpy begin")
    import numpy  # noqa: F401
    _write_startup_trace("import numpy finished")

    _write_startup_trace("import PyQt6 widgets begin")
    from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401
    _write_startup_trace("import PyQt6 widgets finished")

    _write_startup_trace("import matplotlib qtagg begin")
    from matplotlib.backends import backend_qtagg  # noqa: F401
    _write_startup_trace("import matplotlib qtagg finished")

    _write_startup_trace("import vtk begin")
    import vtk  # noqa: F401
    _write_startup_trace("import vtk finished")

    _write_startup_trace("import pyvista begin")
    import pyvista  # noqa: F401
    _write_startup_trace("import pyvista finished")

    _write_startup_trace("import pyvistaqt begin")
    import pyvistaqt  # noqa: F401
    _write_startup_trace("import pyvistaqt finished")

    _write_startup_trace("import app.models begin")
    from app import models  # noqa: F401
    _write_startup_trace("import app.models finished")

    _write_startup_trace("import app.runtime_paths begin")
    from app import runtime_paths  # noqa: F401
    _write_startup_trace("import app.runtime_paths finished")

    _write_startup_trace("import app.services.mesh_service begin")
    from app.services import mesh_service  # noqa: F401
    _write_startup_trace("import app.services.mesh_service finished")

    _write_startup_trace("import app.services.report_service begin")
    from app.services import report_service  # noqa: F401
    _write_startup_trace("import app.services.report_service finished")

    _write_startup_trace("import app.services.solver_service begin")
    from app.services import solver_service  # noqa: F401
    _write_startup_trace("import app.services.solver_service finished")


def main() -> None:
    """启动图形界面。"""

    _write_startup_trace("launcher main begin")
    _bootstrap_import_path()
    _write_startup_trace("launcher import path bootstrapped")
    if STARTUP_TRACE_ENABLED:
        _run_diagnostic_imports()

    _write_startup_trace("import main_window begin")
    from app.main_window import main as run_main
    _write_startup_trace("import main_window finished")

    _write_startup_trace("call main_window.main begin")
    run_main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_file = _write_startup_log()
        _show_startup_error(log_file)
        raise
