"""路径解析：源码运行 与 PyInstaller 打包（frozen）两种模式。

- resource_root()：只读资源根（static/ 等）。源码 = 项目根；打包 = bundle 资源目录（只读，勿写入）。
- support_root() ：可写应用根。源码 = 项目根；打包 = ~/Library/Application Support/Drawthings Studio。
- data_dir()     ：数据库 + 媒体目录（默认 support_root()/data，可被 DATA_DIR 环境变量覆盖）。
"""
import os
import sys
from pathlib import Path

APP_NAME = "Drawthings Studio"


def frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根（static/ 等）。"""
    if frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def support_root() -> Path:
    """可写应用根（数据目录的父目录）。"""
    if frozen():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """数据目录（SQLite + 媒体）：DATA_DIR 环境变量优先，否则按运行模式取默认位置。"""
    v = os.getenv("DATA_DIR")
    if v not in (None, ""):
        return Path(v).expanduser()
    return support_root() / "data"
