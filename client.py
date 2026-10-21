"""Drawthings Studio —— CS 桌面客户端（pywebview 原生窗口）。

职责：
1. 启动时探测本地 FastAPI 服务（GET /api/health，校验 `app` 身份标记）；未运行则自动拉起（子进程，RELOAD=0），
   已运行则直接复用（不重复起服务）。端口只用 `PORT` 作首选：被别的程序占用时服务端自动顺延，
   实际端口由服务写入 `data/.server.port`，客户端据此发现（找不到再扫一遍候选端口）。
2. 服务就绪后打开**原生窗口**（macOS = 系统 WKWebView）加载 SPA —— 无需浏览器。
3. 通过 js_api 向前端暴露原生 OS 能力（前端检测 window.pywebview.api 自动切换）：
   - saveBlob(filename, base64) → 系统「另存为」对话框（导出 ZIP/PDF 走系统保存路径）
   - notify(title, message)     → 系统通知
   - openExternal(url)          → 系统默认浏览器打开
   - serverStatus()             → 本地服务状态
4. 单实例锁：防止重复打开多个窗口（锁文件记录 PID，失效自动清理）。

开发运行：
    pip install pywebview
    python client.py            # 自动起服务 + 开原生窗口
    HOST=127.0.0.1 PORT=8010 python client.py   # 可选覆盖
"""

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

from paths import APP_NAME, data_dir, env_int, frozen, resource_root
from services import port_util
from services.logging_setup import setup_logging

setup_logging()

HOST = os.getenv("HOST", "127.0.0.1")
PREFERRED_PORT = env_int("PORT", port_util.DEFAULT_PORT)  # 首选端口；被占用时服务端会顺延
APP_TITLE = APP_NAME

# 单实例锁：记录 PID，供二次启动检测（放数据目录，源码/打包模式位置一致）
LOCK_FILE = data_dir() / ".client.lock"


# ---------------- 单实例锁 ----------------
def acquire_lock() -> bool:
    """获取单实例锁。成功返回 True；已有存活实例返回 False。"""
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOCK_FILE.exists():
            try:
                pid = int(LOCK_FILE.read_text().strip() or 0)
                if pid > 0:
                    os.kill(pid, 0)  # 信号 0 = 仅探测存活
                    return False  # 旧实例还在 → 拒绝
            except (ValueError, ProcessLookupError, PermissionError):
                pass  # 锁文件失效（进程已退出）→ 覆盖
        LOCK_FILE.write_text(str(os.getpid()))
        return True
    except Exception:
        return True  # 锁机制异常不阻断启动


def release_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------- 日志（打包 --windowed 无控制台，落文件便于排查） ----------------
def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        from services.logging_setup import rotating_log_path

        p = data_dir() / "client.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        f = rotating_log_path(p)  # 带大小轮转；临时对象写一次即回收
        f.write(line)
        f.flush()
    except Exception:
        pass
    if not frozen():  # 源码模式同时输出到终端
        try:
            sys.stderr.write(line)
        except Exception:
            pass


# ---------------- 原生能力（暴露给前端 js_api） ----------------
def _apple_quote(s: str) -> str:
    """AppleScript 字符串字面量转义（反斜杠 / 双引号）。"""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _macos_osascript(script: str) -> tuple[int, str]:
    """在 macOS 上执行 AppleScript（子进程，任意线程可调用，绕开 tkinter 主线程限制）。"""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stdout or "").strip()
    except Exception:
        return 1, ""


class NativeApi:
    """暴露给前端的原生桥（前端经 window.pywebview.api.<fn> 调用，返回值自动包装为 Promise）。"""

    def __init__(self, client: "DesktopClient"):
        self._c = client

    # ---- 文件导出：系统「另存为」 ----
    # 注意：pywebview 按「方法原名」注入 window.pywebview.api（不做 snake_case→camelCase 转换），
    # 因此前端 JS 能调到的名字必须与此处一致（saveBlob / notify / openExternal / serverStatus）。
    def saveBlob(self, filename: str, b64: str) -> dict:
        """保存前端传回的 base64 文件：弹系统「另存为」。用户取消返回 canceled；
        对话框失败时兜底写入 ~/Downloads（保证导出不丢失）。"""
        try:
            data = base64.b64decode(b64 or "")
        except Exception as e:
            return {"ok": False, "error": f"invalid base64: {e}"}
        fname = Path(filename or "download").name  # 仅取文件名，防路径注入
        path = ""
        if sys.platform == "darwin":
            script = f'choose file name with prompt "Save As" default name "{_apple_quote(fname)}"'
            code, out = _macos_osascript(script)
            if code == 0 and out:
                path = out
        if not path:
            return {"ok": False, "canceled": True}
        try:
            dest = Path(path).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return {"ok": True, "path": str(dest)}
        except Exception:
            # 兜底：Downloads 目录（重名自动加序号）
            try:
                dl = Path.home() / "Downloads"
                stem, suf = Path(fname).stem, Path(fname).suffix
                fallback, n = dl / fname, 1
                while fallback.exists():
                    fallback = dl / f"{stem}_{n}{suf}"
                    n += 1
                fallback.write_bytes(data)
                return {"ok": True, "path": str(fallback), "fallback": True}
            except Exception as e:
                return {"ok": False, "error": f"save failed: {e}"}

    # ---- 系统通知 ----
    def notify(self, title: str, message: str = "") -> dict:
        if sys.platform == "darwin":
            _macos_osascript(
                f'display notification "{_apple_quote(message)}" with title "{_apple_quote(title)}"'
            )
        return {"ok": True}

    # ---- 外部链接：系统浏览器 ----
    def openExternal(self, url: str) -> dict:
        import webbrowser

        if url.startswith(("http://", "https://")):
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False, "error": "insecure url"}

    # ---- 服务状态 ----
    def serverStatus(self) -> dict:
        return {
            "running": self._c.server_healthy(),
            "base_url": self._c.base_url,
            "started_by_client": self._c.we_started_server,
        }


# ---------------- 客户端主体 ----------------
class DesktopClient:
    def __init__(self):
        self.server_proc: subprocess.Popen | None = None
        self.we_started_server = False
        # 实际端口：发现到已有实例，或自己拉起的服务就绪后更新（首选端口可能被占用而顺延）
        self.port = PREFERRED_PORT

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    # ---- 服务探测 / 生命周期 ----
    def discover(self) -> int | None:
        """找正在运行的**自家**实例（端口文件优先，其次顺延扫描）；找到即记录实际端口。

        除 `app` 标记外还比对数据目录：同一台机器上别的 checkout / 别的 `DATA_DIR`
        的实例不算「已经就绪」，否则窗口会连到另一套数据上。
        """
        port = port_util.find_own_port(HOST, PREFERRED_PORT, port_util.PORT_SPAN, own_data_dir=data_dir())
        if port is not None:
            self.port = port
        return port

    def server_healthy(self) -> bool:
        return port_util.probe_own(HOST, self.port, own_data_dir=data_dir())

    def start_server(self) -> None:
        """拉起本地服务：打包模式复用本 .app 的 --server 服务模式；源码模式 python main.py。"""
        env = dict(os.environ)
        env.update(
            {
                "RELOAD": "0",
                "HOST": HOST,
                "PORT": str(PREFERRED_PORT),
                "PARENT_PID": str(os.getpid()),
            }
        )  # 服务端据此监视本客户端存活
        if frozen():
            cmd, cwd = [sys.executable, "--server"], str(Path.home())
        else:
            # 源码模式同样走 app.py --server（与打包一致：日志重定向 + PARENT_PID 守护）
            cmd, cwd = [sys.executable, str(resource_root() / "app.py"), "--server"], str(resource_root())
        # 服务输出写 server.log（打包 --windowed 无控制台，便于排查启动问题）
        try:
            out = (data_dir() / "server.log").open("ab")
        except Exception:
            out = subprocess.DEVNULL
        self.server_proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        self.we_started_server = True

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """轮询等待**自家**服务就绪；端口可能不是首选（服务端会顺延），以发现结果为准。"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.discover() is not None:
                return True
            if self.we_started_server and self.server_proc and self.server_proc.poll() is not None:
                # 刚拉起的服务进程已退出（启动失败；若它因复用已有实例而退出，上面的 discover 已先命中）
                return False
            time.sleep(0.3)
        return False

    def stop_server(self) -> None:
        """仅停掉本客户端拉起的进程（用户手动启动的服务不动）。"""
        if self.we_started_server and self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()

    def cleanup(self) -> None:
        """退出兜底：停掉本客户端拉起的服务 + 释放单实例锁（幂等，可重复调用）。"""
        try:
            self.stop_server()
        finally:
            release_lock()


def _install_exit_hooks(client: "DesktopClient") -> None:
    """退出兜底：SIGTERM/SIGHUP/SIGINT 或进程结束时清理（避免孤儿 --server 子进程占端口）。
    macOS 上「退出 App / kill」常走 SIGTERM，不注册处理器会跳过 webview.start() 之后的清理。"""
    import atexit
    import signal

    def _handler(signum, _frame):
        _log(f"received signal {signum}, cleaning up")
        client.cleanup()
        os._exit(0)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError, RuntimeError):
            pass  # 非主线程/平台不支持：忽略，至少 atexit 兜底
    atexit.register(client.cleanup)


# ---------------- 入口 ----------------
def main() -> int:
    if not acquire_lock():
        _log("Drawthings Studio is already running.")
        if sys.platform == "darwin":
            _macos_osascript(
                'display notification "Drawthings Studio is already running" with title "Notice"'
            )
        return 0

    try:
        import webview  # Lazy import: if not installed, show a clear message
    except ImportError:
        _log("pywebview is not installed. Please run first: pip install pywebview")
        release_lock()
        return 1

    client = DesktopClient()
    _install_exit_hooks(client)  # SIGTERM/atexit 兜底：防孤儿 --server 子进程
    if client.discover() is None:
        client.start_server()
        if not client.wait_ready():
            last = PREFERRED_PORT + port_util.PORT_SPAN - 1
            _log(
                f"Failed to start local service (no instance on {HOST}:{PREFERRED_PORT}-{last}). "
                "You can try manually first: python main.py"
            )
            if sys.platform == "darwin":
                _macos_osascript(
                    'display notification "Failed to start local service. Please check the terminal log" with title "Drawthings Studio"'
                )
            release_lock()
            return 1
    _log(f"local service ready at {client.base_url}")

    webview.create_window(
        APP_TITLE, client.base_url, js_api=NativeApi(client), width=1280, height=860, min_size=(960, 640)
    )

    webview.start()  # Blocks until the window is closed
    client.cleanup()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
