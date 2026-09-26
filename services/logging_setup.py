"""统一日志配置（幂等）：所有入口（`python main.py` / `app.py --server` / `client.py`）调用一次。

- 级别由 `LOG_LEVEL`（DEBUG/INFO/WARNING/ERROR，默认 INFO）控制；
- 输出到 stderr（打包无控制台时 app.py 已把 stdout/stderr 接到 data_dir/server.log）；
- uvicorn 的 access 日志降噪到 WARNING（避免每个请求一行刷屏），error 日志跟随 LOG_LEVEL；
- 只配置一次：重复调用直接返回，不覆盖已有 handler。
"""
import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_CONFIGURED = False


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


__all__ = ["setup_logging"]
