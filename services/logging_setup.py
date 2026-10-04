"""统一日志配置（幂等）：所有入口（`python main.py` / `app.py --server` / `client.py`）调用一次。

- 级别由 `LOG_LEVEL`（DEBUG/INFO/WARNING/ERROR，默认 INFO）控制；
- 输出到 stderr（打包无控制台时 app.py 已把 stdout/stderr 接到 data_dir/server.log）；
- uvicorn 的 access 日志降噪到 WARNING（避免每个请求一行刷屏），error 日志跟随 LOG_LEVEL；
- 只配置一次：重复调用直接返回，不覆盖已有 handler。
"""

import logging
import os
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_CONFIGURED = False

LOG_MAX_BYTES = 5 * 1024 * 1024  # 单个日志文件上限
LOG_BACKUPS = 3  # 保留的旧日志份数（b.log → b.log.1 → b.log.2）


class RotatingLogFile:
    """简单的大小轮转文件（server.log / client.log 用）。

    `--windowed` 打包时 stdout/stderr 被重定向到该文件，日志会经过它；
    logging 的 RotatingFileHandler 帮不上这个场景（那要改 handler 归属），
    这里直接在文件层轮转：超过 LOG_MAX_BYTES 就顺移 *.1 / *.2，保留 LOG_BACKUPS 份。
    """

    def __init__(self, path):
        self.path = Path(path)
        self._f: object | None = None
        self._size = 0  # 自己跟踪实际字节数（追加模式下 tell() 不可靠）

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", encoding="utf-8", buffering=1)
        try:
            self._size = self.path.stat().st_size
        except OSError:
            self._size = 0

    def _rotate(self):
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass
        for i in range(LOG_BACKUPS, 0, -1):  # 顺移：.2 ← .1 ← 当前
            src = Path(f"{self.path}.{i - 1}") if i > 1 else self.path
            dst = Path(f"{self.path}.{i}")
            try:
                if src.exists():
                    src.replace(dst)
            except OSError:
                pass
        try:
            Path(f"{self.path}.{LOG_BACKUPS}").unlink(missing_ok=True)
        except OSError:
            pass
        self._open()

    def write(self, s: str) -> int:
        if self._f is None:
            self._open()
        try:
            if self._size > LOG_MAX_BYTES:
                self._rotate()
            n = self._f.write(s)
            self._size += n
            return n
        except OSError:
            return 0

    def flush(self):
        try:
            if self._f is not None:
                self._f.flush()
        except OSError:
            pass

    def isatty(self) -> bool:
        return False


def rotating_log_path(path: Path | str) -> RotatingLogFile:
    """把日志文件路径包成带轮转的文件对象（供 stdout/stderr 重定向与 client._log 共用）。"""
    return RotatingLogFile(path)


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("drawthings").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    # access 日志固定降噪（即使 LOG_LEVEL=DEBUG，也不逐请求刷屏）
    logging.getLogger("uvicorn.access").setLevel(max(level, logging.WARNING))
    # 第三方库降噪：HTTP/底层库按 WARNING 起，避免逐请求/连接刷屏（LOG_LEVEL=DEBUG 时仍可调低）
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
    _CONFIGURED = True


__all__ = ["setup_logging", "rotating_log_path"]
