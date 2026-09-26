"""路径解析：源码运行 与 PyInstaller 打包（frozen）两种模式。

- resource_root()：只读资源根（static/ 等）。源码 = 项目根；打包 = bundle 资源目录（只读，勿写入）。
- support_root() ：可写应用根。源码 = 项目根；打包 = ~/Library/Application Support/Drawthings Studio。
- data_dir()     ：数据库 + 媒体目录（默认 support_root()/data，可被 DATA_DIR 环境变量覆盖）。

启动时加载 `.env`（若存在；不覆盖已有环境变量）：优先当前工作目录，其次项目根 / 应用支持目录。
这样 `cp .env.example .env` 后 DATA_DIR / HOST / PORT / LOG_LEVEL 会真正生效。
"""
import os
import sys
from pathlib import Path

APP_NAME = "Drawthings Studio"


def frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中。"""
    return bool(getattr(sys, "frozen", False))


def _load_dotenv() -> None:
    """加载 `.env`（幂等、不覆盖已有环境变量）。缺 python-dotenv 时静默跳过。"""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
    try:
        candidates.append(support_root() / ".env")
    except Exception:
        pass
    for p in candidates:
        try:
            if p.is_file():
                load_dotenv(p, override=False)
                return
        except Exception:
            continue


_load_dotenv()


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


def env_int(name: str, default: int = 0) -> int:
    """读取整数环境变量：非法值回退默认并告警（避免因一个笔误直接崩启动）。"""
    v = os.getenv(name)
    if v in (None, ""):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        import logging
        logging.getLogger("drawthings").warning(
            "环境变量 %s=%r 不是整数，使用默认值 %s", name, v, default)
        return default
