"""端口自适应回归测试：绑定探测 / 身份识别 / 端口文件（services/port_util.py）。

这些是启动路径上的关键判断，且都容易在改端口逻辑时悄悄退化：
- 只回 200 的「别人家」服务不能被当成自家实例（否则客户端会连错服务、或起第二个写同一 SQLite）；
- 端口顺延必须跳过已占用的端口；
- 端口文件要能发现、能清理，且 pid 失效时自动作废。
"""

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from services import port_util


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(payload: dict) -> tuple[HTTPServer, int]:
    """起一个只回固定 JSON 的本地服务，模拟「自家 / 别人家」实例。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802  # BaseHTTPRequestHandler 的约定方法名
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 测试里不要刷访问日志
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, int(srv.server_address[1])


@pytest.fixture
def own_server():
    srv, port = _serve({"ok": True, "app": port_util.APP_MARKER, "port": 0, "data": "/tmp/own-data"})
    yield port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def foreign_server():
    srv, port = _serve({"ok": True, "app": "some-other-app"})
    yield port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


class TestProbe:
    def test_accepts_our_marker(self, own_server):
        assert port_util.probe_own("127.0.0.1", own_server) is True

    def test_rejects_foreign_200(self, foreign_server):
        # 关键：200 OK 但 app 标记不符 → 不是自家实例（客户端不能连它）
        assert port_util.probe_own("127.0.0.1", foreign_server) is False

    def test_rejects_closed_port(self):
        assert port_util.probe_own("127.0.0.1", _free_port(), timeout=0.2) is False

    def test_requires_matching_data_dir(self, own_server):
        # 同机另一个 checkout / 另一个 DATA_DIR 的实例：不能当成「已就绪」
        assert port_util.probe_own("127.0.0.1", own_server, own_data_dir="/tmp/own-data") is True
        assert port_util.probe_own("127.0.0.1", own_server, own_data_dir="/tmp/other-data") is False


class TestPickPort:
    def test_keeps_preferred_when_free(self):
        port = _free_port()
        assert port_util.pick_port("127.0.0.1", port, span=5) == port

    def test_skips_occupied(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            base = busy.getsockname()[1]
            picked = port_util.pick_port("127.0.0.1", base, span=5)
        assert picked != base
        assert 0 < picked - base < 5

    def test_raises_when_span_exhausted(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            base = busy.getsockname()[1]
            with pytest.raises(OSError):
                port_util.pick_port("127.0.0.1", base, span=1)


class TestPortFile:
    def test_roundtrip(self, data_dir):
        port_util.write_port_file("127.0.0.1", 8123)
        info = port_util.read_port_file()
        assert info is not None
        assert info["port"] == 8123
        assert info["pid"] == os.getpid()
        port_util.clear_port_file()
        assert port_util.read_port_file() is None
        assert not (data_dir / port_util.PORT_FILE).exists()

    def test_stale_pid_is_discarded(self, data_dir):
        p = data_dir / port_util.PORT_FILE
        p.write_text(json.dumps({"host": "127.0.0.1", "port": 8123, "pid": 0}))
        assert port_util.read_port_file() is None
        assert not p.exists()


class TestFindOwn:
    def test_finds_instance_via_port_file(self, own_server, data_dir):
        port_util.write_port_file("127.0.0.1", own_server)
        assert port_util.find_own_port("127.0.0.1", own_server) == own_server

    def test_finds_instance_by_scanning(self, own_server, data_dir):
        # 端口文件缺失/失效时，仍能靠扫描（+ app 标记）发现
        assert port_util.find_own_port("127.0.0.1", own_server) == own_server

    def test_ignores_foreign_instance(self, foreign_server, data_dir):
        assert port_util.find_own_port("127.0.0.1", foreign_server, span=1) is None

    def test_none_when_nothing_listening(self, data_dir):
        assert port_util.find_own_port("127.0.0.1", _free_port(), span=1) is None
