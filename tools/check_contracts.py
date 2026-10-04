#!/usr/bin/env python
"""校验 AGENTS.md 的硬性契约（无需装 pytest 也能跑，CI / 打包前可调用）：

1. SSE 事件契约：services/events.py（后端唯一来源）↔ static/spa/js/events.js（前端镜像）；
2. 漫画 / 短剧双线路由对称（除短剧独有的 /export/video）。

返回非零退出码 = 有漂移。
"""

import re
import sys

sys.path.insert(0, "")

import services.events as E  # noqa: E402
from services import api_comic, api_drama  # noqa: E402

FAILS: list[str] = []


def check_events() -> None:
    backend = {getattr(E, n) for n in E.__all__ if n != "EOF"}
    src = open("static/spa/js/events.js", encoding="utf-8").read()
    frontend = set(re.findall(r":\s*'([^']+)'", src))
    if backend - frontend:
        FAILS.append(f"前端 events.js 缺失后端事件：{backend - frontend}")
    if frontend - backend:
        FAILS.append(f"前端 events.js 多了未定义事件：{frontend - backend}")


def check_routes() -> None:
    def norm(method, path):
        return (method, path.replace("/api/comics", "").replace("/api/dramas", ""))

    def routes(router):
        out = set()
        for r in router.routes:
            p = getattr(r, "path", None)
            if p and getattr(r, "methods", None):
                for m in r.methods:
                    out.add(norm(m.upper(), p))
        return out

    comic, drama = routes(api_comic.router), routes(api_drama.router)
    d_only = {r for r in drama - comic if not r[1].endswith("/export/video")}
    c_only = comic - drama
    if d_only:
        FAILS.append(f"drama 有 comic 缺失的路由：{sorted(d_only)}")
    if c_only:
        FAILS.append(f"comic 有 drama 缺失的路由：{sorted(c_only)}")


def main() -> int:
    check_events()
    check_routes()
    if FAILS:
        print("契约校验失败：")
        for f in FAILS:
            print(f"  ✗ {f}")
        return 1
    print("✅ 契约校验通过（事件 / 双线路由对称）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
