"""运行时单例：流水线门面实例（漫画 / 短剧两条独立流水线）。

放在独立模块里，供 main.py 与各 API 路由模块共享同一实例，避免循环导入。
"""
from config import data_dir
from services.pipeline import Pipeline

pipeline = Pipeline(data_dir)

__all__ = ["pipeline"]
