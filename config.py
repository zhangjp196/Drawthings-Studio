"""最小配置：仅保留数据目录位置（数据库与媒体文件存放处）。

LLM / DrawThings 等外部端点配置已迁到页面（数据库存储），见 /configs。
位置规则集中在 paths.py：源码 = <项目根>/data；打包（PyInstaller）=
~/Library/Application Support/Drawthings Studio/data；可用 DATA_DIR 环境变量覆盖。
"""
from pathlib import Path

from paths import data_dir as _data_dir

data_dir: Path = _data_dir()
