"""
运行时路径工具。

作用：
1. 统一兼容“源码直接运行”和“PyInstaller 打包运行”两种模式；
2. 为网格、结果、项目文件提供稳定的默认输出目录；
3. 保证 `fem_core`、数据目录等在打包后仍然能被正确找到。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _detect_source_root() -> Path:
    """返回源码仓库根目录。"""

    return Path(__file__).resolve().parents[2]


def _detect_bundle_root() -> Path:
    """
    返回 PyInstaller 运行时的资源根目录。

    说明：
    1. 源码模式下直接返回源码根目录；
    2. 打包模式下优先使用 `_MEIPASS`；
    3. 如果 `_MEIPASS` 不存在，则退回可执行文件所在目录。
    """

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return _detect_source_root()


def _detect_app_root() -> Path:
    """
    返回应用运行目录。

    说明：
    1. 源码模式下，运行目录就是仓库根目录；
    2. 打包模式下，运行目录就是 EXE 所在目录；
    3. 这里的目录默认承担项目文件、网格文件、结果文件的输出位置。
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _detect_source_root()


SOURCE_ROOT = _detect_source_root()
BUNDLE_ROOT = _detect_bundle_root()
APP_ROOT = _detect_app_root()
PYTHON_SOURCE_ROOT = SOURCE_ROOT / "python"

DATA_DIR = APP_ROOT / "data"
MODELS_DIR = DATA_DIR / "models"
RESULTS_DIR = DATA_DIR / "results"


def bootstrap_runtime_environment() -> None:
    """
    初始化运行时导入路径和默认输出目录。

    说明：
    1. `fem_core.pyd` 在源码模式下位于 `python/`，打包后通常位于 EXE 同级目录；
    2. 因此这里把多个候选目录都加到 `sys.path`；
    3. 同时自动创建 `data/models` 与 `data/results`，避免首次运行时报目录不存在。
    """

    candidate_paths = [
        APP_ROOT,
        APP_ROOT / "python",
        BUNDLE_ROOT,
        BUNDLE_ROOT / "python",
        PYTHON_SOURCE_ROOT,
    ]

    for candidate in candidate_paths:
        if candidate.exists():
            candidate_text = str(candidate)
            if candidate_text not in sys.path:
                sys.path.insert(0, candidate_text)

    for folder in [DATA_DIR, MODELS_DIR, RESULTS_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def resolve_runtime_path(path_like: str | Path) -> Path:
    """
    把相对路径解析到应用运行目录下。

    规则：
    1. 绝对路径保持不变；
    2. 相对路径一律相对于 `APP_ROOT` 解析；
    3. 这样项目 JSON 中即使保存了 `data/results/latest_results.csv`，
       打包后的 EXE 也能稳定写到自己的运行目录中。
    """

    path = Path(path_like)
    if path.is_absolute():
        return path
    return APP_ROOT / path


def default_models_path(file_name: str) -> Path:
    """返回默认模型文件输出路径。"""

    return MODELS_DIR / file_name


def default_results_path(file_name: str) -> Path:
    """返回默认结果文件输出路径。"""

    return RESULTS_DIR / file_name

