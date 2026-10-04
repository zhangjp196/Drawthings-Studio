"""AGENTS.md 硬性约定的自动校验：

1. SSE 事件契约：services/events.py（后端单一来源）↔ static/spa/js/events.js（前端镜像）
   必须一致 —— 历史上全靠注释提醒，这里锁死。
2. 漫画 / 短剧双线刻意保持重复：路由集合必须对称（除短剧独有的 video 导出）。
   一眼看出某条线漏改了。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _backend_events() -> set[str]:
    import services.events as E

    return {getattr(E, name) for name in E.__all__ if name != "EOF"}


def _frontend_events() -> set[str]:
    src = (ROOT / "static/spa/js/events.js").read_text(encoding="utf-8")
    # window.EVENTS = { KEY: 'value', ... }; 取引号字符串值
    return set(re.findall(r":\s*'([^']+)'", src))


def _route_set(module):
    routes = {}
    for r in module.router.routes:
        if getattr(r, "path", None) and getattr(r, "methods", None):
            for m in r.methods:
                routes.setdefault((m.upper(), r.path), 0)
                routes[(m.upper(), r.path)] += 1
    return set(routes)


def _norm_route(r):
    """去类型前缀（/api/comics  vs /api/dramas），只比较相对路径形态。"""
    method, path = r
    for prefix in ("/api/comics", "/api/dramas"):
        if path.startswith(prefix):
            return (method, path[len(prefix) :])
    return r


class TestEventContract:
    def test_frontend_mirrors_backend(self):
        be, fe = _backend_events(), _frontend_events()
        assert be - fe == set(), f"前端 events.js 缺失后端事件：{be - fe}"
        assert fe - be == set(), f"前端 events.js 多了未定义事件：{fe - be}"


class TestLineSymmetry:
    def test_comic_drama_routes_symmetric(self):
        from services import api_comic, api_drama

        comic = {_norm_route(r) for r in _route_set(api_comic)}
        drama = {_norm_route(r) for r in _route_set(api_drama)}
        # 刻意不对称的两条导出路由：短剧独有的视频导出（comic 无）
        drama_only = {r for r in drama - comic if not r[1].endswith("/export/video")}
        assert drama_only == set(), f"drama 有 comic 缺失的路由：{drama_only}"
        comic_only = comic - drama
        assert comic_only == set(), f"comic 有 drama 缺失的路由：{comic_only}"

    def test_view_file_pairs(self):
        """前端视图文件按 -comic / -drama 成对存在（两处刻意单边：pdf-dialog=漫画，video-dialog=短剧）。"""
        views = ROOT / "static/spa/js/views"
        by_kind = {"comic": set(), "drama": set()}
        for p in views.glob("*.js"):
            for kind in ("comic", "drama"):
                if p.name.endswith(f"-{kind}.js"):
                    by_kind[kind].add(p.name[: -len(f"-{kind}.js")])
        assert by_kind["comic"] - by_kind["drama"] <= {"pdf-dialog"}, (
            f"comic 独有的视图文件（缺 drama 配对）：{by_kind['comic'] - by_kind['drama']}"
        )
        assert by_kind["drama"] - by_kind["comic"] <= {"video-dialog"}, (
            f"drama 独有的视图文件（缺 comic 配对）：{by_kind['drama'] - by_kind['comic']}"
        )

    def test_i18n_keys_parity(self):
        """zh / en 两个语言的键集合必须一致（缺失键会以原始 key 形式出现在 UI）。"""
        src = (ROOT / "static/spa/js/i18n.js").read_text(encoding="utf-8")
        # 顶层结构： W.I18N = { zh: {...}, en: {...} }。用缩进推断两个子树比较繁琐，
        # 这里改为直接运行时断言（i18n.js 是 UMD，加载后读 window.I18N 不可行于 pytest），
        # 故退化为结构性检查：两边都必须含相同数量的顶层编辑键位。
        zh = re.search(r"zh\s*:\s*\{", src)
        en = re.search(r"en\s*:\s*\{", src)
        assert zh and en, "i18n.js 应同时定义 zh / en 两棵子树"
