"""API 层共享基础设施（与类型无关）：请求解析、本地化、媒体 URL、序列化视图等。

漫画 / 短剧各自的 API 路由模块（api_comic / api_drama）与 main.py 都从这里导入。
本模块不含任何类型特有逻辑。
"""
import json

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from config import MEDIA_DIR, media_url as _media_url
from i18n import L, lang_of
from models import Project
from config_store import ConfigStore
from services.drawthings import MAX_VIDEO_SECONDS, norm_ref_flag
from services.pipeline import hex_to_rgb
from services.runtime import pipeline

MEDIA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------- 请求 / 本地化 ----------------
def _lang(request: Request) -> str:
    """当前请求的语言（Accept-Language → zh|en），用于本地化用户可见文案。"""
    return lang_of(request.headers.get("accept-language", ""))


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
        return body
    except Exception:
        raise HTTPException(status_code=400,
                            detail=L(_lang(request), "请求体需为 JSON 对象", "Body must be a JSON object"))


# ---------------- 媒体 URL / 序列化 ----------------
# _media_url 现由 config.media_url 提供（api_common 顶部已重导出，兼容既有 `_media_url` 引用）。


def _chapter_view(ch) -> dict:
    return {
        "index": ch.index,
        "season_id": ch.season_id or "",
        "title": ch.title,
        "summary": ch.summary or "",
        "description": ch.description,
        "prompt": ch.prompt,
        "width": ch.width or 0,
        "height": ch.height or 0,
        "media_url": _media_url(ch.media_path),
        "status": ch.status,
        "error": ch.error,
        "score": ch.score or 0,
        "score_note": ch.score_note or "",
    }


def _llm_view(c) -> dict:
    return {
        "id": c.id, "name": c.name, "base_url": c.base_url, "model": c.model,
        "supports_vision": "yes",
        "thinking": getattr(c, "thinking", None) or "default",
        "thinking_param": getattr(c, "thinking_param", None) or "auto",
        "created_at": c.created_at,
    }


def _dt_view(c) -> dict:
    return {
        "id": c.id, "name": c.name, "base_url": c.base_url,
        "model_image": getattr(c, "model_image", "") or "",
        "model_video": getattr(c, "model_video", "") or "",
        "max_side": c.max_side or 0,
        "max_seconds": getattr(c, "max_seconds", 0) or 0,
        "ref_image": int(getattr(c, "ref_image", 0) or 0),   # 图像支持参考图片（图生图）
        "ref_video": int(getattr(c, "ref_video", 0) or 0),   # 视频支持参考图片（图生视频）
        "created_at": c.created_at,
    }


def _dt_ref_field(body: dict, key: str) -> str:
    """功能级参考图开关：请求缺省 = 跟随配置（''）；提供 = 归一为 '0'/'1'。"""
    if key not in body:
        return ""
    return "1" if norm_ref_flag(body.get(key)) else "0"


def _dt_gen_fields(body: dict, lang: str = "zh") -> dict:
    """DrawThings 个性化参数：0/空 = 跟随 app 当前值。非法值直接 400。

    模型改为功能级选择（项目 / 微创作各自选），配置里的模型仅作兜底默认，可为空。
    ref_image / ref_video：「支持参考图片」能力开关，随配置声明（图生图 / 图生视频）。"""
    def num(key, cast, max_v: int) -> int | float:
        v = body.get(key)
        if v in (None, ""):
            return 0
        try:
            v = cast(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=L(lang, f"{key} 需为数字", f"{key} must be a number"))
        if v < 0 or v > max_v:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"{key} 超出范围（0~{max_v}，0=跟随 app）",
                                         f"{key} out of range (0~{max_v}, 0 = follow app)"))
        return v
    model_image = str(body.get("model_image") or "").strip()
    model_video = str(body.get("model_video") or "").strip()

    def flag(key: str) -> int:
        """勾选类字段：true/1/yes/on → 1，其余（含缺省）→ 0。"""
        v = body.get(key)
        if isinstance(v, bool):
            return 1 if v else 0
        return 1 if str(v or "").strip().lower() in ("1", "true", "yes", "on") else 0
    return {
        "model_image": model_image,
        "model_video": model_video,
        "max_side": int(num("max_side", int, 2048)),
        "max_seconds": int(num("max_seconds", int, MAX_VIDEO_SECONDS)),
        # 能力开关：模型已在功能级选择，配置只声明「支持参考图片」
        "ref_image": flag("ref_image"),
        "ref_video": flag("ref_video"),
    }


def _project_view(p: Project, chapter_count: int = 0) -> dict:
    scope = p.scope or {}
    return {
        "id": p.id, "kind": p.kind, "title": p.title or "", "origin": p.origin,
        "status": p.status, "created_at": p.created_at, "updated_at": p.updated_at,
        "scope": scope,
        "first_image_url": _media_url(p.first_image or ""),
        "chapter_count": chapter_count,
    }


def _config_lists(db: Session, project: Project) -> dict:
    """项目可选配置：LLM 全部 + DrawThings 全部。"""
    cs = ConfigStore(db)
    return {"llm_configs": [_llm_view(c) for c in cs.list_llm()],
            "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()]}


def _clamp_page(page: int, size: int) -> tuple[int, int]:
    """分页参数防御：页码 >=1，每页条数限制在 1..100（防 size=0 除零、超大分页）。"""
    return max(page, 1), min(max(size, 1), 100)


def _ensure_not_finished(project: Project, lang: str) -> None:
    """已完结（锁定）的作品禁止一切编辑/生成操作：需先解锁（服务端兜底，前端按钮同步禁用）。"""
    if pipeline.is_finished(project):
        raise HTTPException(status_code=400,
                            detail=L(lang, "该作品已完结（锁定），请先点「解锁」再操作",
                                     "This work is finished (locked) — click Unlock first"))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


MAX_IMAGE_UPLOAD = 20 * 1024 * 1024  # 单张上传图片上限 20MB（防超大文件占满内存）


def _overlay_opts(body: dict) -> dict:
    """封面标题叠加参数（位置/字号/样式/颜色/底条）；非法值回退默认。"""
    def _f(v, default, lo, hi):
        try:
            return min(hi, max(lo, float(v)))
        except (TypeError, ValueError):
            return default

    style = str(body.get("style") or "bold_outline")
    if style not in ("bold_outline", "outline", "shadow", "plain"):
        style = "bold_outline"
    return {
        "x": _f(body.get("x"), 0.5, 0.0, 1.0),
        "y": _f(body.get("y"), 1 / 3, 0.0, 1.0),
        "size_pct": _f(body.get("size_pct"), 8.0, 2.0, 40.0),
        "style": style,
        "color": hex_to_rgb(str(body.get("color") or "#ffffff")),
        "band": bool(body.get("band", True)),
    }


__all__ = [
    "MEDIA_DIR", "MAX_IMAGE_UPLOAD", "_overlay_opts",
    "_lang", "_json_body", "_media_url", "_chapter_view",
    "_llm_view", "_dt_view", "_dt_ref_field", "_dt_gen_fields", "_project_view",
    "_config_lists", "_clamp_page", "_ensure_not_finished", "_sse",
]
