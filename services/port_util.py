"""本地服务端口自适应（CS 桌面端 / BS 浏览器端共用）。

为什么不能「端口被占就随便换一个」：
同一 `data/` 目录下跑两个服务实例会并发写同一个 SQLite（`database is locked`、
写入交错），比启动失败严重得多。所以启动顺序固定为：

1. **先认亲**：读 `data/.server.port` → 再从首选端口起扫 `PORT_SPAN` 个端口，
   逐个探 `/api/health` 并校验 `app` 标记；命中即复用，不再起新进程。
2. **再顺延**：确认没有自家实例后，才从首选端口往后找一个能绑定的端口
   （不随机分配：端口可预期，便于排查、书签和文档）。
3. **记下来**：实际端口写入 `data/.server.port`（进程正常退出时清理；被信号 / `kill -9`
   杀掉时残留的记录由 pid 存活校验在下次启动时作废），客户端据此发现端口，不必每次全量扫描。

身份标记 `APP_MARKER` 与 `main.py` 的 `/api/health` 返回值绑定：只回 200 的
本地服务（别的程序）不会被误当成「服务已就绪」。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import urllib.request
from pathlib import Path

from paths import data_dir

APP_MARKER = "drawthings-studio"
DEFAULT_PORT = 8010
PORT_SPAN = 20  # 顺延上限：本机单用户应用不会出现这么大的端口密度
PORT_FILE = ".server.port"
PROBE_TIMEOUT = 0.8  # 单次健康探测上限（本机 refused 是立即返回，正常不会等满）
SCAN_TIMEOUT = 0.3  # 扫描时缩短超时，避免个别「不响应」端口拖慢启动

log = logging.getLogger("drawthings.port")

# 健康探测必须绕过系统代理：本机 127.0.0.1 走 http_proxy 会直接失败（企业网络常见）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------- 地址工具 ----------------
def port_file() -> Path:
    """端口文件路径（随数据目录走：源码 = ./data，打包 = 应用支持目录）。"""
    return data_dir() / PORT_FILE


def _probe_host(host: str) -> str:
    """通配地址（0.0.0.0 / ::）探测时改走回环，否则连不上。"""
    return "127.0.0.1" if host in ("", "0.0.0.0", "::", "[::]") else host


def _url_host(host: str) -> str:
    h = _probe_host(host)
    return h if ":" not in h or h.startswith("[") else f"[{h}]"


def _family(host: str) -> int:
    return socket.AF_INET6 if ":" in host.strip("[]") else socket.AF_INET


# ---------------- 身份探测 ----------------
def _data_key(value: str | Path) -> str:
    """数据目录比较用：展开 ~ 并解析符号链接（同一目录的不同写法视为同一实例）。"""
    return os.path.realpath(os.path.expanduser(str(value)))


def probe_own(
    host: str,
    port: int,
    timeout: float = PROBE_TIMEOUT,
    own_data_dir: str | Path | None = None,
) -> bool:
    """该端口上是否为**本应用**服务（不只看 HTTP 200，必须带 app 标记）。

    `own_data_dir` 传入时还要求数据目录一致：同一台机器上可能同时跑着别的 checkout
    （`DATA_DIR` 不同），连过去会看到另一套项目数据，必须当成「别人家」。
    """
    if not port or port <= 0:
        return False
    url = f"http://{_url_host(host)}:{port}/api/health"
    try:
        with _OPENER.open(url, timeout=timeout) as r:
            if getattr(r, "status", 0) != 200:
                return False
            payload = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return False
    if not isinstance(payload, dict) or payload.get("app") != APP_MARKER:
        return False
    if own_data_dir is not None:
        recorded = payload.get("data")
        if not isinstance(recorded, str) or _data_key(recorded) != _data_key(own_data_dir):
            return False
    return True


# ---------------- 端口文件 ----------------
def _pid_alive(pid: int) -> bool:
    if pid <= 0:  # 0 / 负数不是有效进程号（os.kill(0, 0) 会探测整个进程组，必须挡掉）
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但无权限探测
    except OSError:
        return False


def read_port_file() -> dict | None:
    """读端口文件；内容损坏或记录进程已退出时清理并返回 None。"""
    try:
        info = json.loads(port_file().read_text() or "{}")
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    port, pid = info.get("port"), info.get("pid")
    if not isinstance(port, int) or not 0 < port < 65536 or not isinstance(pid, int):
        return None
    if not _pid_alive(pid):
        # 记录进程已退出（kill -9 / 崩溃残留）：文件可安全删除，不占用下次启动的发现路径
        try:
            port_file().unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return info


def write_port_file(host: str, port: int) -> None:
    """记录实际监听端口（失败只告警：写不进去不影响服务本身）。"""
    try:
        p = port_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"host": host, "port": port, "pid": os.getpid()}))
    except Exception as e:
        log.warning("写端口文件失败（不影响服务运行）：%s", e)


def clear_port_file() -> None:
    """只清自己写的记录，避免误删并发启动的新实例。"""
    p = port_file()
    try:
        info = json.loads(p.read_text() or "{}")
        if isinstance(info, dict) and info.get("pid") == os.getpid():
            p.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------- 发现 / 选端口 ----------------
def find_own_port(
    host: str = "127.0.0.1",
    preferred: int = DEFAULT_PORT,
    span: int = PORT_SPAN,
    own_data_dir: str | Path | None = None,
) -> int | None:
    """找正在运行的自家实例端口（可按数据目录隔离）；没有返回 None。"""
    info = read_port_file()
    if info:
        port = info["port"]
        if probe_own(host, port, own_data_dir=own_data_dir):
            return port
        clear_port_file()  # 记录失效（实例已退 / 端口被别的程序接手）
    for port in range(preferred, preferred + span):
        if probe_own(host, port, timeout=SCAN_TIMEOUT, own_data_dir=own_data_dir):
            return port
    return None


def _try_bind(host: str, port: int) -> socket.socket | None:
    sock = socket.socket(_family(host), socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return sock
    except OSError:
        sock.close()
        return None


def can_bind(host: str, port: int) -> bool:
    sock = _try_bind(host, port)
    if sock is None:
        return False
    sock.close()
    return True


def pick_port(host: str = "127.0.0.1", preferred: int = DEFAULT_PORT, span: int = PORT_SPAN) -> int:
    """从首选端口顺延，返回第一个可绑定的端口；整段都被占则抛 OSError。"""
    for port in range(preferred, preferred + span):
        if can_bind(host, port):
            if port != preferred:
                log.warning("端口 %s 已被占用，顺延到 %s", preferred, port)
            return port
    raise OSError(
        f"端口 {preferred}–{preferred + span - 1} 全部被占用，请用 PORT 指定其他端口"
        f" / ports {preferred}-{preferred + span - 1} are all in use, set PORT explicitly"
    )


def bind_socket(host: str, port: int, backlog: int = 128) -> socket.socket:
    """预绑定监听 socket：交给 uvicorn 直接使用，消除「探测 → bind」之间的竞态窗口。"""
    sock = _try_bind(host, port)
    if sock is None:
        raise OSError(f"端口 {port} 绑定失败 / cannot bind port {port}")
    sock.listen(backlog)
    return sock


# ---------------- 启动入口 ----------------
def serve(
    app: object,
    host: str = "127.0.0.1",
    preferred: int = DEFAULT_PORT,
    span: int = PORT_SPAN,
    *,
    reload: bool = False,
) -> int:
    """起 uvicorn：复用自家实例 → 顺延选端口 → 预绑定 → 记录端口文件。

    `app` 为 FastAPI 实例；`reload=True` 时必须是 import string（如 "main:app"，
    热重载由 uvicorn 自己建 socket，只剩极小的探测窗口）。
    返回 0：正常退出，或发现已有同数据目录的实例而直接复用（此时不再起新服务）。
    """
    reuse = find_own_port(host, preferred, span, own_data_dir=data_dir())

    if reuse is not None:
        log.info(
            "检测到已运行的实例 http://%s:%s，复用该实例（不再启动新服务）/ "
            "reusing running instance, not starting another server",
            _url_host(host),
            reuse,
        )
        return 0

    port = pick_port(host, preferred, span)
    sock = bind_socket(host, port)
    os.environ["PORT"] = str(port)  # /api/health、启动日志都读真实端口
    write_port_file(host, port)
    log.info(
        "本地服务监听 http://%s:%s（首选 %s）/ serving on http://%s:%s",
        _url_host(host),
        port,
        preferred,
        _url_host(host),
        port,
    )
    try:
        import uvicorn

        if reload:
            sock.close()
            uvicorn.run(app, host=host, port=port, reload=True)
        else:
            uvicorn.Server(uvicorn.Config(app, host=host, port=port, reload=False)).run(sockets=[sock])
    finally:
        clear_port_file()
        try:
            sock.close()
        except Exception:
            pass
    return 0
