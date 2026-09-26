"""最小配置：数据目录位置 + 媒体路径/URL 基础工具（低层，无 services 依赖）。

LLM / DrawThings 等外部端点配置已迁到页面（数据库存储），见 /configs。
位置规则集中在 paths.py：源码 = <项目根>/data；打包（PyInstaller）=
~/Library/Application Support/Drawthings Studio/data；可用 DATA_DIR 环境变量覆盖。

`MEDIA_DIR` / `media_url()` 放这里（而不是 services/api_common），使 `services/media_files`
等底层工具无需依赖 API 层，避免循环导入。
"""
from pathlib import Path

from paths import data_dir as _data_dir

data_dir: Path = _data_dir()

MEDIA_DIR: Path = Path(data_dir) / "media"


def media_url(media_path: str) -> str:
    """媒体磁盘路径 -> /media/xxx URL（附 mtime 版本号，覆盖同名文件后强制刷新缓存）。"""
    if not media_path:
        return ""
    name = Path(media_path).name
    try:
        mtime = (MEDIA_DIR / name).stat().st_mtime_ns
        return f"/media/{name}?v={mtime}"
    except OSError:
        return f"/media/{name}"
