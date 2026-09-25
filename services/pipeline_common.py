"""流水线通用工具（与作品类型无关的纯工具函数）。

漫画（pipeline_comic.py）与短剧（pipeline_drama.py）是两条完全独立、互不共享的流水线；
本模块只放它们都会用到的「基础设施级」工具（角色设定解析 / 时间 / 线程池 / 字体探测等），
不含任何漫画或短剧的业务逻辑。
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anyio


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_sync(func, *args):
    """把同步阻塞调用放到线程池执行（生图/生视频、ffmpeg、读图编码、HTTP 探测）。

    pipeline 与 main 中所有阻塞型调用统一走这里，避免占用事件循环导致并发请求卡顿。"""
    return await anyio.to_thread.run_sync(func, *args)


# 封面自动模式叠加作品名称用的中文字体：按系统取第一个存在的（结果缓存，避免反复探盘）
_CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)
_cjk_font: str | None = None


def _cjk_font_path() -> str:
    """找一个可渲染中文/日文的字体文件；没有返回 ''（调用方跳过叠加）。"""
    global _cjk_font
    if _cjk_font is None:
        _cjk_font = next((p for p in _CJK_FONT_CANDIDATES if Path(p).is_file()), "")
    return _cjk_font


def hex_to_rgb(s: str) -> tuple[int, int, int]:
    """'#rgb' / '#rrggbb'（也接受 '#rrggbbaa'）→ (r,g,b)；非法输入回退白色。"""
    v = (s or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) not in (6, 8):
        return (255, 255, 255)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return (255, 255, 255)


# ---------------- 角色设定（多个角色：id / 名字 / 形象性格 / 参考图） ----------------
def chars_from_raw(raw: str) -> list[dict]:
    """解析 project.characters（JSON 列表）为 [{id, name, description, image}]。

    兼容旧版纯文本角色设定：整体视为一个未命名角色的描述。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, list):
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append({
                "id": str(item.get("id") or "") or uuid.uuid4().hex[:12],
                "name": str(item.get("name") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "image": str(item.get("image") or "").strip(),
            })
        return out
    return [{"id": uuid.uuid4().hex[:12], "name": "", "description": raw, "image": ""}]


def chars_to_raw(chars: list[dict]) -> str:
    """角色列表 -> JSON 字符串（存 project.characters）。"""
    return json.dumps(chars or [], ensure_ascii=False)


def chars_to_text(chars: list[dict]) -> str:
    """角色列表 -> 文本（注入 LLM 提示词用）：每个角色一行「名字：设定」。"""
    lines = []
    for c in chars or []:
        name = (c.get("name") or "").strip()
        desc = (c.get("description") or "").strip()
        if name and desc:
            lines.append(f"{name}：{desc}")
        elif name or desc:
            lines.append(name or desc)
    return "\n".join(lines)


# ---------------- 章节数量：仅范围模式（min~max） ----------------
DEFAULT_COUNT_MIN, DEFAULT_COUNT_MAX = 6, 12


def count_range(count_min, count_max) -> tuple[int, int]:
    """规范化章节数量范围 (lo, hi)：未设置（0/None）时回退默认 6~12；hi 不小于 lo。"""
    lo = int(count_min or 0) or DEFAULT_COUNT_MIN
    hi = int(count_max or 0) or DEFAULT_COUNT_MAX
    lo = max(1, lo)
    return lo, max(lo, hi)
