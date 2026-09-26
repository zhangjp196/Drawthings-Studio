"""Drawthings Studio —— 统一入口（供 PyInstaller 打包；源码下也可直接用）。

- 无参数      → 桌面客户端：自动拉起本地服务 + 原生窗口（CS 形态）
- --server   → 运行 FastAPI 服务（客户端据此拉子进程；也可单独运行做 BS/调试）

打包后（dist/Drawthings Studio.app）客户端会以「本程序 --server」起服务，
同一份二进制两种模式，无需外置 python。
"""
import os
import sys

# 关闭 pydantic 第三方插件（logfire 的 pydantic 插件在冻结环境下 inspect.getsource() 失败会崩溃）；
# 客户端进程据此起 --server 子进程时，环境变量经 os.environ 一并传给服务进程。
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")


def _redirect_logs() -> None:
    """打包（--windowed 无控制台）时把 stdout/stderr 接到数据目录 server.log，便于排查启动错误。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        from paths import data_dir
        p = data_dir() / "server.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        f = open(p, "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass


def _watch_parent() -> None:
    """服务模式守护：客户端拉起本进程时传入 PARENT_PID，父进程消失（被 kill / 崩溃）即自杀，
    防止孤儿 --server 占着端口与数据目录。仅在显式传入时生效（单独起的服务不受影响）。
    注：不能依赖客户端侧的信号处理器——pywebview 主线程阻塞在 Cocoa 事件循环里，信号不被处理。"""
    import os
    import threading
    import time
    pid = int(os.getenv("PARENT_PID", "0") or 0)
    if pid <= 0:
        return

    def _loop() -> None:
        while True:
            time.sleep(3)
            try:
                os.kill(pid, 0)          # 信号 0：仅探测存活
            except ProcessLookupError:
                os._exit(0)               # 父进程已消失 → 自动退出
            except PermissionError:
                continue                  # 进程还在（仅无权限），继续监视

    threading.Thread(target=_loop, daemon=True, name="parent-watch").start()


def run_server() -> None:
    """服务模式：进程内直接跑 uvicorn（生产模式，无热重载）。"""
    _redirect_logs()
    try:
        import os
        from paths import env_int
        from services.logging_setup import setup_logging
        setup_logging()
        _watch_parent()
        import uvicorn
        from main import app as fastapi_app
        uvicorn.run(fastapi_app,
                    host=os.getenv("HOST", "127.0.0.1"),
                    port=env_int("PORT", 8010),
                    reload=False)
    except BaseException:
        import traceback
        traceback.print_exc()
        raise


def main() -> int:
    if "--server" in sys.argv:
        run_server()
        return 0
    from client import main as client_main
    return client_main()


if __name__ == "__main__":
    sys.exit(main())
