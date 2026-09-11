"""最小配置：仅保留数据目录位置（数据库与媒体文件存放处）。

LLM / DrawThings 等外部端点配置已迁到页面（数据库存储），见 /configs。
此处仅通过 DATA_DIR 环境变量覆盖数据存储位置（可选）。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"


def _env(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val not in (None, "") else default


data_dir: Path = Path(_env("DATA_DIR", str(DEFAULT_DATA_DIR)))
