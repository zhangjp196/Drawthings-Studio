"""Drawthings Studio —— CS 桌面客户端（pywebview 原生窗口）。

职责：
1. 启动时探测本地 FastAPI 服务（GET /api/health）；未运行则自动拉起（子进程，RELOAD=0），
   已运行则直接复用（不重复起服务）。
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
import urllib.request
from pathlib import Path

from paths import frozen, resource_root, data_dir

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8010"))
BASE_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{BASE_URL}/api/health"
APP_TITLE = "Drawthings Studio"

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
                    return False     # 旧实例还在 → 拒绝
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
        p = data_dir() / "client.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
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
                f'display notification "{_apple_quote(message)}" with title "{_apple_quote(title)}"')
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
        return {"running": self._c.server_healthy(),
                "base_url": BASE_URL,
                "started_by_client": self._c.we_started_server}


# ---------------- 客户端主体 ----------------
class DesktopClient:
    def __init__(self):
        self.server_proc: subprocess.Popen | None = None
        self.we_started_server = False

    # ---- 服务探测 / 生命周期 ----
    def server_healthy(self) -> bool:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1) as r:
                return r.status == 200
        except Exception:
            return False

    def start_server(self) -> None:
        """拉起本地服务：打包模式复用本 .app 的 --server 服务模式；源码模式 python main.py。"""
        env = dict(os.environ)
        env.update({"RELOAD": "0", "HOST": HOST, "PORT": str(PORT),
                    "PARENT_PID": str(os.getpid())})  # 服务端据此监视本客户端存活
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
            cmd, env=env, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
        )
        self.we_started_server = True

    def wait_ready(self, timeout: float = 30.0) -> bool:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.server_healthy():
                return True
            if self.we_started_server and self.server_proc and self.server_proc.poll() is not None:
                return False  # 刚拉起的服务进程已退出（启动失败）
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
            _macos_osascript('display notification "Drawthings Studio is already running" with title "Notice"')
        return 0

    try:
        import webview  # Lazy import: if not installed, show a clear message
    except ImportError:
        _log("pywebview is not installed. Please run first: pip install pywebview")
        release_lock()
        return 1

    client = DesktopClient()
    _install_exit_hooks(client)  # SIGTERM/atexit 兜底：防孤儿 --server 子进程
    if not client.server_healthy():
        client.start_server()
        if not client.wait_ready():
            _log(f"Failed to start local service ({HEALTH_URL} not ready). You can try manually first: python main.py")
            if sys.platform == "darwin":
                _macos_osascript('display notification "Failed to start local service. Please check the terminal log" with title "Drawthings Studio"')
            release_lock()
            return 1

    webview.create_window(APP_TITLE, BASE_URL, js_api=NativeApi(client),
                          width=1280, height=860, min_size=(960, 640))

    webview.start()  # Blocks until the window is closed
    client.cleanup()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
