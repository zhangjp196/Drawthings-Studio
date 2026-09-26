"""一体化应用：FastAPI JSON API + 本地 vendor 的 Vue3 / Element Plus SPA（无构建、离线可用）。

品牌：Drawthings Studio。
两个功能：
- 漫画走向（连续生图）
- 短剧走向（连续出视频，参考上一视频末帧）
统一走一条流水线：一句话 -> 设定篇幅 -> 章节设定 -> 剧本编写 -> 单任务进行。

配置（LLM / DrawThings）由用户在页面管理，存 SQLite；项目创建时选择所用配置。
前端：static/spa（Vue3 + Element Plus UMD 本地引用），/api/* 为 JSON 接口，
其余路径返回 SPA 外壳（前端路由接管）。
界面支持中文 / English（前端 I18N 切换 + 后端按 Accept-Language 本地化错误文案）。
"""
import os

# 关闭 pydantic 的第三方插件加载：logfire 的 pydantic 插件会调用 inspect.getsource()，
# 在 PyInstaller 冻结环境中取不到源码会抛 OSError 导致启动崩溃；本应用不使用 pydantic 插件。
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")

import asyncio
import base64
import itertools
import json
import logging
import re
import time
import uuid
import zipfile
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, File, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic_ai import Agent, RunContext
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from config import data_dir
from paths import resource_root
from db import get_db, init_db
from i18n import L, lang_of
from models import Project, Chapter, Season, MicroWork, MicroSession, MicroMessage
from config_store import ConfigStore
from services.agent import build_model, to_message_history, user_prompt, make_httpx_client
from services.pipeline import Pipeline, _now, chars_from_raw, hex_to_rgb, run_sync
from services.drawthings import build_drawthings_client, MAX_VIDEO_SECONDS

STATIC_ROOT = resource_root() / "static"
MEDIA_DIR = Path(data_dir) / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

MAX_IMAGE_UPLOAD = 20 * 1024 * 1024  # 单张上传图片上限 20MB（防超大文件占满内存）
MAX_REQUEST_BODY = 64 * 1024 * 1024  # 请求体上限 64MB（容纳多张 base64 附图；超限直接拒绝）

app = FastAPI(title="Drawthings Studio")
app.add_middleware(GZipMiddleware, minimum_size=500)  # HTML/CSS/JS 压缩，减少传输体积

logger = logging.getLogger("drawthings")


def _lang(request: Request) -> str:
    """当前请求的语言（Accept-Language → zh|en），用于本地化用户可见文案。"""
    return lang_of(request.headers.get("accept-language", ""))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """参数校验失败：统一返回本地化的 JSON，避免前端拿到结构化 detail 数组无法展示。"""
    return JSONResponse(status_code=422,
                        content={"detail": L(_lang(request), "请求参数不合法",
                                             "Invalid request parameters")})


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    """兜底：任何未捕获异常都返回 JSON（而非 HTML 纯文本），并记录堆栈便于排查。"""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"detail": L(_lang(request), "服务器内部错误，请查看服务端日志",
                                             "Internal server error — check the server logs")})

app.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")


@app.middleware("http")
async def _static_cache(request: Request, call_next):
    """静态资源缓存头：
    - vendor 依赖文件名固定、内容不变 → 一年强缓存 + immutable（不再发校验请求）
    - spa 源码常改 → no-cache（仍发条件请求，命中 304）
    - /media 媒体 URL 已带 ?v=mtime 版本号（覆盖同名文件即换 URL）→ 一年强缓存
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/vendor/") or path.startswith("/media/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/static/spa/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def _limit_request_body(request: Request, call_next):
    """请求体上限保护：Content-Length 超限直接 413，避免解析超大 JSON 撑爆内存。"""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_REQUEST_BODY:
        return JSONResponse(
            status_code=413,
            content={"detail": L(_lang(request), "请求体过大（上限 64MB）",
                                 "Request body too large (64MB max)")})
    return await call_next(request)

init_db()  # 建表（幂等）

pipeline = Pipeline(data_dir)


def _media_url(media_path: str) -> str:
    if not media_path:
        return ""
    name = Path(media_path).name
    # 附带文件修改时间作为缓存破坏参数：文件被重新生成（覆盖同名文件）后 URL 变化，
    # 强制浏览器重新拉取，避免命中旧缓存（如封面重新生成不刷新）。
    try:
        mtime = (MEDIA_DIR / name).stat().st_mtime_ns
        return f"/media/{name}?v={mtime}"
    except OSError:
        return f"/media/{name}"


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


def _dt_gen_fields(body: dict, lang: str = "zh") -> dict:
    """DrawThings 个性化参数：0/空 = 跟随 app 当前值。非法值直接 400。
    ref_image / ref_video：「支持参考图片」（图生图 / 图生视频）勾选，仅在配了对应模型时才生效。"""
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
    if not (model_image or model_video):
        raise HTTPException(status_code=400,
                            detail=L(lang, "至少需要指定一个模型（图像或视频）",
                                     "At least one model (image or video) is required"))
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
        # 勾选仅在配了对应模型时才生效：无该类型模型 → 开关一并归零（避免残留）
        "ref_image": flag("ref_image") if model_image else 0,
        "ref_video": flag("ref_video") if model_video else 0,
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


# ---------------- 静态媒体 ----------------
@app.get("/media/{filename}")
def serve_media(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith((".", "%")):
        raise HTTPException(status_code=404, detail="not found")
    path = MEDIA_DIR / filename
    if not path.is_file() or not path.resolve().is_relative_to(MEDIA_DIR.resolve()):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


def _clamp_page(page: int, size: int) -> tuple[int, int]:
    """分页参数防御：页码 >=1，每页条数限制在 1..100（防 size=0 除零、超大分页）。"""
    return max(page, 1), min(max(size, 1), 100)


# ---------------- 配置管理（JSON API） ----------------
CFG_LIMITS = {"llm": 3, "drawthings": 9}  # 配置数量上限：LLM 最多 3 个，DrawThings 最多 9 个


@app.get("/api/choices")
def choices(db: Session = Depends(get_db)):
    """新建作品/创作时的可选项：全部 LLM + 全部 DrawThings 配置。"""
    cs = ConfigStore(db)
    return {"llm_configs": [_llm_view(c) for c in cs.list_llm()],
            "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()]}


@app.get("/api/configs")
def configs_list(db: Session = Depends(get_db)):
    """配置列表：一次返回 LLM + DrawThings 两组（页面无 tab，分区展示），附数量与上限。"""
    cs = ConfigStore(db)
    llms = cs.list_llm()
    dts = cs.list_drawthing()
    return {
        "llm_items": [_llm_view(c) for c in llms],
        "dt_items": [_dt_view(c) for c in dts],
        "llm_count": len(llms), "drawthing_count": len(dts),
        "llm_max": CFG_LIMITS["llm"], "dt_max": CFG_LIMITS["drawthings"],
    }


_DT_PRESETS_CACHE: list[dict] | None = None


@app.get("/api/dt-presets")
def dt_presets():
    """gRPC 可选预设（drawthings-py）：名称 + 默认模型 + 是否视频模型。未安装返回空列表。"""
    global _DT_PRESETS_CACHE
    if _DT_PRESETS_CACHE is not None:
        return {"presets": _DT_PRESETS_CACHE, "available": True}
    try:
        from drawthings_py import Configs
        from drawthings_py.configs.presets import Presets
        from services.drawthings import is_video_model
    except Exception:
        return {"presets": [], "available": False}
    items: list[dict] = []
    for p in Presets:
        name = str(p.value)
        model = ""
        try:
            model = str(Configs.from_preset(name)["model"] or "")
        except Exception:
            pass
        items.append({"name": name, "model": model, "video": is_video_model(model or name)})
    _DT_PRESETS_CACHE = items
    return {"presets": items, "available": True}


@app.get("/api/dt-models")
def dt_models(request: Request, base_url: str = "", refresh: int = 1):
    """连接 Draw Things gRPC 返回已下载的基座模型清单（供配置页下拉选择）。"""
    lang = _lang(request)
    if not base_url.strip():
        raise HTTPException(status_code=400,
                            detail=L(lang, "请先填写 gRPC 端点（host:port）",
                                     "Enter the gRPC endpoint (host:port) first"))
    try:
        from services.drawthings import parse_endpoint, fetch_models
        host, port = parse_endpoint(base_url)
        models = fetch_models(host, port, refresh=bool(refresh))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"获取模型列表失败：{e}", f"Failed to fetch models: {e}"))
    return {"models": models}


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
        return body
    except Exception:
        raise HTTPException(status_code=400,
                            detail=L(_lang(request), "请求体需为 JSON 对象", "Body must be a JSON object"))


@app.post("/api/configs")
async def config_create(request: Request, db: Session = Depends(get_db)):
    lang = _lang(request)
    body = await _json_body(request)
    config_type = str(body.get("config_type") or "")
    name = str(body.get("name") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    if not name or not base_url:
        raise HTTPException(status_code=400,
                            detail=L(lang, "名称和端点地址不能为空", "Name and endpoint URL are required"))
    thinking = str(body.get("thinking") or "default").lower()
    if thinking not in ("default", "yes", "no"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "深度思考选项无效", "Invalid thinking option"))
    thinking_param = str(body.get("thinking_param") or "auto").lower()
    if thinking_param not in ("auto", "reasoning_effort", "enable_thinking"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "思考参数选项无效", "Invalid thinking parameter option"))
    cs = ConfigStore(db)
    limit = CFG_LIMITS.get(config_type)
    if limit is not None:
        cur = len(cs.list_llm()) if config_type == "llm" else len(cs.list_drawthing())
        if cur >= limit:
            if config_type == "llm":
                msg = L(lang, f"VLM 配置已达上限（最多 {limit} 个），请先删除不再使用的配置",
                        f"VLM config limit reached ({limit} max) — delete an unused one first")
            else:
                msg = L(lang, f"DrawThings 配置最多 {limit} 个，请先删除现有配置",
                        f"Only {limit} DrawThings config allowed — delete the existing one first")
            raise HTTPException(status_code=400, detail=msg)
    try:
        if config_type == "llm":
            model = str(body.get("model") or "").strip()
            if not model:
                raise HTTPException(status_code=400,
                                    detail=L(lang, "VLM 配置需要模型名", "VLM config requires a model name"))
            cs.create_llm(name, base_url, str(body.get("api_key") or ""), model,
                          thinking=thinking, thinking_param=thinking_param)
        elif config_type == "drawthings":
            cs.create_drawthing(name, base_url, **_dt_gen_fields(body, lang))
        else:
            raise HTTPException(status_code=400,
                                detail=L(lang, "未知配置类型", "Unknown config type"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=L(lang, f"保存失败：{e}", f"Save failed: {e}"))
    db.commit()
    return {"ok": True}


@app.put("/api/configs/{config_type}/{config_id}")
async def config_update(request: Request, config_type: str, config_id: str, db: Session = Depends(get_db)):
    """编辑配置。API Key 留空 = 保持原值不变（列表接口不返回 Key）。"""
    lang = _lang(request)
    body = await _json_body(request)
    name = str(body.get("name") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    if not name or not base_url:
        raise HTTPException(status_code=400,
                            detail=L(lang, "名称和端点地址不能为空", "Name and endpoint URL are required"))
    cs = ConfigStore(db)
    try:
        if config_type == "llm":
            model = str(body.get("model") or "").strip()
            if not model:
                raise HTTPException(status_code=400,
                                    detail=L(lang, "VLM 配置需要模型名", "VLM config requires a model name"))
            thinking = str(body.get("thinking") or "default").lower()
            if thinking not in ("default", "yes", "no"):
                raise HTTPException(status_code=400,
                                    detail=L(lang, "深度思考选项无效", "Invalid thinking option"))
            thinking_param = str(body.get("thinking_param") or "auto").lower()
            if thinking_param not in ("auto", "reasoning_effort", "enable_thinking"):
                raise HTTPException(status_code=400,
                                    detail=L(lang, "思考参数选项无效", "Invalid thinking parameter option"))
            raw_key = body.get("api_key")
            updated = cs.update_llm(config_id, name=name, base_url=base_url,
                                    api_key=None if raw_key in (None, "") else str(raw_key),
                                    model=model,
                                    thinking=thinking, thinking_param=thinking_param)
        elif config_type == "drawthings":
            updated = cs.update_drawthing(config_id, name=name, base_url=base_url,
                                          **_dt_gen_fields(body, lang))
        else:
            raise HTTPException(status_code=400,
                                detail=L(lang, "未知配置类型", "Unknown config type"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=L(lang, f"保存失败：{e}", f"Save failed: {e}"))
    if not updated:
        raise HTTPException(status_code=404,
                            detail=L(lang, "配置不存在", "Config not found"))
    db.commit()
    return {"ok": True}


@app.get("/api/llm/models")
def llm_models(request: Request, base_url: str = "", api_key: str = "", config_id: str = "",
               db: Session = Depends(get_db)):
    """从 OpenAI 兼容端点的 /models 接口拉取可用模型 id 列表（新建/编辑 LLM 配置时自动填充）。

    编辑已有配置时前端传 config_id：表单未填地址/Key 时回退用库里已存值
    （密钥不在列表接口回传，编辑弹窗里是空的，须由服务端代填）。
    """
    lang = _lang(request)
    cfg = ConfigStore(db).get_llm(config_id) if config_id else None
    base_url = (base_url or "").strip() or (cfg.base_url if cfg else "")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400,
                            detail=L(lang, "端点地址需为 http(s) URL", "Endpoint must be an http(s) URL"))
    key = (api_key or "").strip() or (cfg.api_key if cfg else "")
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with make_httpx_client(base_url, timeout=15.0) as client:
            r = client.get(url, headers=headers)
        if r.status_code in (401, 403):
            detail = L(lang, "端点鉴权失败：请检查 API Key" if key else "端点需要鉴权（401）：请先填写 API Key",
                       "Endpoint authentication failed: check the API Key" if key
                       else "Endpoint requires auth (401): please fill in an API Key")
            raise HTTPException(status_code=400, detail=detail)
        if r.status_code != 200:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"端点返回 {r.status_code}，请检查地址与 API Key",
                                         f"Endpoint returned {r.status_code}; check the URL and API Key"))
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"获取模型失败：{e}", f"Failed to fetch models: {e}"))
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    models: list[str] = []
    for it in items:
        mid = str(it.get("id") or it.get("name") or "").strip() if isinstance(it, dict) else str(it).strip()
        if mid and mid not in models:
            models.append(mid)
    return {"models": sorted(models)}


@app.post("/api/configs/{config_type}/{config_id}/delete")
def config_delete(request: Request, config_type: str, config_id: str, db: Session = Depends(get_db)):
    lang = _lang(request)
    cs = ConfigStore(db)
    if config_type in ("llm", "drawthings"):
        if cs.is_referenced(config_id):
            raise HTTPException(status_code=400,
                                detail=L(lang, "该配置仍被项目使用，无法删除",
                                         "This config is still used by a project and cannot be deleted"))
        ok = cs.delete_llm(config_id) if config_type == "llm" else cs.delete_drawthing(config_id)
    else:
        raise HTTPException(status_code=400, detail=L(lang, "未知配置类型", "Unknown config type"))
    if not ok:
        raise HTTPException(status_code=404,
                            detail=L(lang, "配置不存在", "Config not found"))
    cs.clear_default_ref(config_type, config_id)  # 删掉的配置若被设为默认 → 清空引用
    db.commit()
    return {"ok": True}


@app.get("/api/settings")
def settings_get(db: Session = Depends(get_db)):
    """基础配置：新建创作的默认 LLM / DrawThings 配置。"""
    return ConfigStore(db).get_settings()


@app.put("/api/settings")
async def settings_update(request: Request, db: Session = Depends(get_db)):
    """保存基础配置（白名单字段；引用不存在的配置 id 直接 400）。"""
    lang = _lang(request)
    body = await _json_body(request)
    cs = ConfigStore(db)
    llm_id = str(body.get("default_llm_config_id") or "").strip()
    dt_id = str(body.get("default_dt_config_id") or "").strip()
    if llm_id and not cs.get_llm(llm_id):
        raise HTTPException(status_code=400,
                            detail=L(lang, "默认 VLM 配置不存在", "Default VLM config not found"))
    if dt_id and not cs.get_drawthing(dt_id):
        raise HTTPException(status_code=400,
                            detail=L(lang, "默认 DrawThings 配置不存在", "Default DrawThings config not found"))
    return cs.update_settings({
        "default_llm_config_id": llm_id,
        "default_dt_config_id": dt_id,
    })


# ---------------- 微创作（作品 → 多个独立会话 → 消息：历史持久化 + SSE 流式 + function call 出图/出视频）----------------
MC_SYSTEM = ("你是漫画/短剧创作的创意助手，擅长创意构思、角色与剧情设计、分镜和提示词，回答简洁、具体。"
              "用户只是提问时直接文本回答；用户要求生成图片/视频时，先调用 generate_media 工具"
              "（提供详细英文提示词：主体、场景、构图、光线、风格；视频补充运镜与动态），"
              "生成成功后用一两句话说明结果。"
              "始终用用户所用的语言回答（用户用中文提问则答中文，用英文提问则答英文）。")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


MC_PAGE_SIZE = 10  # 微创作作品列表每页条数


def _work_paged(db: Session, page: int, size: int = MC_PAGE_SIZE, q: str = "",
                kind: str = "", sort_desc: bool = True) -> tuple[int, int, list[MicroWork]]:
    """按条件（标题关键词 / 类型）+ 排序取作品列表，分页：返回 (页码, 总数, 当前页作品)。"""
    query = db.query(MicroWork)
    if q:
        query = query.filter(MicroWork.title.ilike(f"%{q}%"))
    if kind == "gen":
        query = query.filter(MicroWork.drawthings_config_id != "")
    elif kind == "chat":
        query = query.filter(or_(MicroWork.drawthings_config_id.is_(None),
                                 MicroWork.drawthings_config_id == ""))
    total = query.count()
    total_pages = max((total + size - 1) // size, 1)
    page = min(max(page, 1), total_pages)  # 越界页码收敛到有效范围
    rows = (query.order_by(MicroWork.updated_at.desc() if sort_desc else MicroWork.updated_at.asc())
            .offset((page - 1) * size).limit(size).all())
    return page, total, rows


def _session_counts(db: Session, session_ids: list[str]) -> dict:
    """各会话的消息数 {session_id: n}。"""
    if not session_ids:
        return {}
    return dict(db.query(MicroSession.id, func.count(MicroMessage.id))
                .join(MicroMessage, MicroMessage.session_id == MicroSession.id)
                .filter(MicroSession.id.in_(session_ids))
                .group_by(MicroSession.id).all())


@app.get("/api/micro")
def micro_works(db: Session = Depends(get_db), page: int = 1, size: int = MC_PAGE_SIZE,
                q: str = "", kind: str = "", sort: str = "desc"):
    """作品列表：标题关键词 / 类型（生成 / 纯对话）/ 排序筛选 + 分页 + 新建作品可选配置。"""
    page, size = _clamp_page(page, size)
    page, total, works = _work_paged(db, page, size, q.strip(), kind, sort != "asc")
    counts = dict(db.query(MicroSession.micro_id, func.count(MicroSession.id))
                  .filter(MicroSession.micro_id.in_([w.id for w in works] or [""]))
                  .group_by(MicroSession.micro_id).all())
    # 作品集预览：每作品最近 3 条生成媒体（列表页卡片缩略图）
    wid_list = [w.id for w in works]
    previews: dict[str, list[str]] = {wid: [] for wid in wid_list}
    if wid_list:
        rows = (db.query(MicroSession.micro_id, MicroMessage.media_url, MicroMessage.id)
                .join(MicroMessage, MicroMessage.session_id == MicroSession.id)
                .filter(MicroSession.micro_id.in_(wid_list),
                        MicroMessage.media_url.isnot(None),
                        MicroMessage.media_url != "")
                .order_by(MicroMessage.id.desc()).all())
        for mid, url, _ in rows:
            lst = previews.get(mid)
            if lst is not None and len(lst) < 3:
                lst.append(url)
    cs = ConfigStore(db)
    return {
        "works": [{
            "id": w.id, "title": w.title, "llm_config_id": w.llm_config_id,
            "drawthings_config_id": w.drawthings_config_id,
            "updated_at": w.updated_at, "session_count": counts.get(w.id, 0),
            "media_preview": previews.get(w.id, []),
        } for w in works],
        "total": total, "page": page, "size": size,
        "total_pages": max((total + size - 1) // size, 1),
        "llm_configs": [_llm_view(c) for c in cs.list_llm()],
        "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()],
    }


@app.post("/api/micro")
async def micro_create(request: Request, db: Session = Depends(get_db)):
    """新建微创作作品（含首个会话）。"""
    lang = _lang(request)
    body = await _json_body(request)
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400,
                            detail=L(lang, "名称不能为空", "Name is required"))
    cs = ConfigStore(db)
    llm_config_id = str(body.get("llm_config_id") or "")
    if not cs.get_llm(llm_config_id):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的 VLM 配置", "Please select a valid VLM config"))
    w = MicroWork(
        id=uuid.uuid4().hex[:12],
        title=title[:200],
        llm_config_id=llm_config_id,
        drawthings_config_id=str(body.get("drawthings_config_id") or "").strip(),
        created_at=_now(), updated_at=_now(),
    )
    db.add(w)
    db.flush()  # 先落库作品，会话挂在作品下
    db.add(MicroSession(id=uuid.uuid4().hex[:12], micro_id=w.id,
                        title="", created_at=_now(), updated_at=_now()))
    db.commit()
    return {"id": w.id}


def _micro_work_view(db: Session, work: MicroWork) -> dict:
    """作品视图：作品信息 + 会话列表 + 名称 + 视觉标记 + 可选配置。

    dt_name 为空 = 未选 DrawThings（纯对话）：文案由前端按语言渲染。
    """
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(work.llm_config_id or "")
    dt_cfg = cs.get_drawthing(work.drawthings_config_id) if work.drawthings_config_id else None
    counts = _session_counts(db, [s.id for s in work.sessions])
    return {
        "work": {
            "id": work.id, "title": work.title,
            "llm_config_id": work.llm_config_id,
            "drawthings_config_id": work.drawthings_config_id,
            "created_at": work.created_at,
            "llm_name": llm_cfg.name if llm_cfg else "",
            "dt_name": dt_cfg.name if dt_cfg else "",
            "vision": bool(llm_cfg),
        },
        "sessions": [
            {"id": s.id, "title": s.title, "created_at": s.created_at,
             "updated_at": s.updated_at, "msg_count": counts.get(s.id, 0)}
            for s in work.sessions
        ],
        "llm_configs": [_llm_view(c) for c in cs.list_llm()],
        "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()],
    }


@app.get("/api/micro/{work_id}")
def micro_work_page(request: Request, work_id: str, db: Session = Depends(get_db)):
    """作品（不含消息）：前端自动选中最近更新的会话。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "作品不存在", "Work not found"))
    return _micro_work_view(db, work)


# 注意：以下两个 /works 路由必须注册在 {session_id} 系列路由之前，
# 否则「works」会被当成 session_id 抢先匹配（FastAPI 按注册顺序匹配）。
@app.get("/api/micro/{work_id}/works")
def micro_works_gallery(request: Request, work_id: str, db: Session = Depends(get_db)):
    """作品（= 该作品下所有会话里助手生成的媒体：图/视频）。按生成时间倒序。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "作品不存在", "Work not found"))
    sess_title = {s.id: s.title for s in work.sessions}
    msgs = (db.query(MicroMessage)
            .join(MicroSession, MicroMessage.session_id == MicroSession.id)
            .filter(MicroSession.micro_id == work_id,
                    MicroMessage.role == "assistant",
                    MicroMessage.media_url.isnot(None),
                    MicroMessage.media_url != "")
            .order_by(MicroMessage.created_at.desc(), MicroMessage.id.desc()).all())
    items = [{
        "id": m.id, "session_id": m.session_id,
        "session_title": sess_title.get(m.session_id, ""),
        "media_url": m.media_url, "prompt": m.prompt or "",
        "content": m.content or "", "created_at": m.created_at or "",
    } for m in msgs]
    return {"works": items, "total": len(items)}


@app.post("/api/micro/{work_id}/works/delete")
async def micro_works_delete(request: Request, work_id: str, db: Session = Depends(get_db)):
    """批量删除作品（= 对应的助手媒体消息 + 媒体文件），可跨该作品多个会话。"""
    lang = _lang(request)
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(lang, "作品不存在", "Work not found"))
    body = await _json_body(request)
    ids = []
    for x in (body.get("ids") or []):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择要删除的作品", "Please select works to delete"))
    msgs = (db.query(MicroMessage)
            .join(MicroSession, MicroMessage.session_id == MicroSession.id)
            .filter(MicroSession.micro_id == work_id, MicroMessage.id.in_(ids)).all())
    if not msgs:
        raise HTTPException(status_code=404, detail=L(lang, "作品不存在", "Work not found"))
    _cleanup_message_media(msgs)
    for m in msgs:
        db.delete(m)
    work.updated_at = _now()
    db.commit()
    return {"ok": True, "deleted": len(msgs)}


@app.get("/api/micro/{work_id}/works/export")
def micro_works_export(request: Request, work_id: str, ids: str = "", format: str = "zip",
                       db: Session = Depends(get_db)):
    """导出所选作品媒体（按 ids 顺序）：format=zip（图 + 视频）/ pdf（仅图片）。
    前端按「图片 / 视频」分区各自支持 单个 / 勾选 / 全部 导出。"""
    lang = _lang(request)
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(lang, "作品不存在", "Work not found"))
    id_list: list[int] = []
    for x in (ids or "").split(","):
        x = x.strip()
        if x.isdigit() and int(x) not in id_list:
            id_list.append(int(x))
    if not id_list:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择要导出的作品", "Please select works to export"))
    msgs = (db.query(MicroMessage)
            .join(MicroSession, MicroMessage.session_id == MicroSession.id)
            .filter(MicroSession.micro_id == work_id, MicroMessage.id.in_(id_list)).all())
    by_id = {m.id: m for m in msgs}
    files: list[tuple[Path, bool]] = []
    for mid in id_list:                       # 保持前端选择顺序
        m = by_id.get(mid)
        if not m:
            continue
        p = _media_path_from_url(m.media_url)
        if p is not None:
            files.append((p, _is_video_url(m.media_url)))
    if not files:
        raise HTTPException(status_code=400,
                            detail=L(lang, "没有可导出的媒体", "No media to export"))
    if format == "pdf":
        if any(is_video for _, is_video in files):
            raise HTTPException(status_code=400,
                                detail=L(lang, "PDF 仅支持图片，视频请导出 ZIP",
                                         "PDF supports images only — export videos as ZIP"))
        try:
            path, fname = _micro_export_pdf(work, files)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
        except Exception as e:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"导出 PDF 失败：{e}", f"Export PDF failed: {e}"))
        return FileResponse(path, filename=fname, media_type="application/pdf")
    try:
        path, fname = _micro_export_zip(work, files)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"导出 ZIP 失败：{e}", f"Export ZIP failed: {e}"))
    return FileResponse(path, filename=fname, media_type="application/zip")


@app.get("/api/micro/{work_id}/{session_id}")
def micro_session_page(request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)):
    """作品 + 选中会话的消息历史。"""
    work = db.get(MicroWork, work_id)
    session = db.get(MicroSession, session_id)
    if not work or not session or session.micro_id != work_id:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "会话不存在", "Session not found"))
    view = _micro_work_view(db, work)
    msgs = []
    for m in session.messages:
        imgs = []
        if m.images:
            try:
                imgs = [str(u) for u in json.loads(m.images) if u]
            except (ValueError, TypeError):
                pass
        parts = []
        if m.parts:
            try:
                parsed = json.loads(m.parts)
                if isinstance(parsed, list):
                    parts = parsed
            except (ValueError, TypeError):
                parts = []
        msgs.append({"index": m.index, "role": m.role, "content": m.content,
                     "created_at": m.created_at or "",
                     "duration": m.duration or 0,
                     "images": imgs, "media_url": m.media_url or "", "prompt": m.prompt or "",
                     "parts": parts})
    view["session_id"] = session.id
    view["messages"] = msgs
    return view


@app.post("/api/micro/{work_id}/settings")
async def micro_work_settings(request: Request, work_id: str, db: Session = Depends(get_db)):
    """修改作品选项（标题/LLM/DrawThings），作用于其下全部会话。"""
    lang = _lang(request)
    body = await _json_body(request)
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(lang, "作品不存在", "Work not found"))
    cs = ConfigStore(db)
    llm_config_id = str(body.get("llm_config_id") or "")
    if not cs.get_llm(llm_config_id):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的 VLM 配置", "Please select a valid VLM config"))
    work.title = str(body.get("title") or "").strip()[:200]
    work.llm_config_id = llm_config_id
    work.drawthings_config_id = str(body.get("drawthings_config_id") or "").strip()
    work.updated_at = _now()
    db.commit()
    return {"ok": True}


@app.post("/api/micro/{work_id}/sessions")
async def micro_session_create(request: Request, work_id: str, db: Session = Depends(get_db)):
    """在作品下新建一个独立会话。"""
    body = await _json_body(request)
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "作品不存在", "Work not found"))
    s = MicroSession(id=uuid.uuid4().hex[:12], micro_id=work_id,
                     title=str(body.get("title") or "").strip()[:200],
                     created_at=_now(), updated_at=_now())
    db.add(s)
    work.updated_at = _now()
    db.commit()
    return {"id": s.id}


@app.post("/api/micro/{work_id}/delete")
def micro_work_delete(request: Request, work_id: str, db: Session = Depends(get_db)):
    """删除作品（级联删除全部会话与消息），顺带清理已生成的媒体文件。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "作品不存在", "Work not found"))
    _cleanup_message_media(db.query(MicroMessage).join(MicroSession, MicroMessage.session_id == MicroSession.id)
                           .filter(MicroSession.micro_id == work_id).all())
    db.delete(work)  # 级联删除会话与消息
    db.commit()
    return {"ok": True}


@app.post("/api/micro/{work_id}/{session_id}/rename")
async def micro_session_rename(request: Request, work_id: str, session_id: str,
                               db: Session = Depends(get_db)):
    """重命名会话。"""
    body = await _json_body(request)
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "会话不存在", "Session not found"))
    session.title = str(body.get("title") or "").strip()[:200]
    session.updated_at = _now()
    db.get(MicroWork, work_id).updated_at = _now()
    db.commit()
    return {"ok": True}


@app.post("/api/micro/{work_id}/{session_id}/delete")
def micro_session_delete(request: Request, work_id: str, session_id: str,
                         db: Session = Depends(get_db)):
    """删除作品下的一个会话（含全部消息），顺带清理媒体文件。"""
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "会话不存在", "Session not found"))
    _cleanup_message_media(session.messages)
    db.delete(session)  # 级联删除消息
    db.get(MicroWork, work_id).updated_at = _now()
    db.commit()
    return {"ok": True}


def _cleanup_message_media(msgs) -> None:
    """删除消息关联的媒体文件：助手生成媒体（media_url）+ 用户附图（images JSON 列表）。"""
    def _unlink(url: str) -> None:
        f = MEDIA_DIR / url.rsplit("/", 1)[-1].split("?")[0]
        if f.is_file() and f.resolve().is_relative_to(MEDIA_DIR.resolve()):
            f.unlink()
    for m in msgs:
        if m.media_url:
            _unlink(m.media_url)
        if m.images:
            try:
                for u in json.loads(m.images):
                    _unlink(str(u))
            except (ValueError, TypeError):
                pass


def _is_video_url(url: str) -> bool:
    """按扩展名判断媒体是否为视频（媒体落盘时按实际内容定扩展名）。"""
    return bool(re.search(r"\.(mp4|mov|webm|gif)(?:\?|$)", url or "", re.I))


def _media_path_from_url(url: str) -> Path | None:
    """媒体 URL（/media/xxx）-> 磁盘路径；越界或不存在返回 None。"""
    name = (url or "").rsplit("/", 1)[-1].split("?")[0]
    if not name:
        return None
    p = MEDIA_DIR / name
    try:
        if p.is_file() and p.resolve().is_relative_to(MEDIA_DIR.resolve()):
            return p
    except OSError:
        return None
    return None


def _export_dir() -> Path:
    d = Path(data_dir) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_file_base(text: str, fallback: str) -> str:
    """标题 -> 安全文件名（去掉路径/非法字符，限长）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (text or "").strip())[:40].strip(" ._")
    return s or fallback


def _micro_export_zip(work: MicroWork, files: list[tuple[Path, bool]]) -> tuple[str, str]:
    """把所选媒体（图/视频）打包为 ZIP：按导出顺序命名，分 images/ 与 videos/ 两个子目录。"""
    base = _safe_file_base(work.title or work.id, work.id)
    fname = f"{base}_media.zip"
    zpath = _export_dir() / fname
    n_img = sum(1 for _, v in files if not v)
    n_vid = len(files) - n_img
    readme = (f"作品：{work.title or work.id}\n"
              f"图片：{n_img} 张 · 视频：{n_vid} 个\n")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme.encode("utf-8"))
        for i, (p, is_video) in enumerate(files):
            sub = "videos" if is_video else "images"
            z.write(str(p), f"{sub}/{i:02d}_{p.name}")
    return str(zpath), fname


def _micro_export_pdf(work: MicroWork, files: list[tuple[Path, bool]]) -> tuple[str, str]:
    """把所选图片按顺序拼成多页 PDF（仅图片；视频需先导出 ZIP）。"""
    base = _safe_file_base(work.title or work.id, work.id)
    fname = f"{base}_images.pdf"
    ppath = _export_dir() / fname
    imgs: list[Image.Image] = []
    for p, _ in files:
        try:
            with Image.open(p) as im:      # 及时关闭文件句柄；convert 产生独立图像
                imgs.append(im.convert("RGB"))
        except Exception:
            continue
    if not imgs:
        raise ValueError("没有可导出的图片")
    try:
        imgs[0].save(ppath, save_all=True, append_images=imgs[1:], resolution=96.0)
    finally:
        for im in imgs:
            im.close()
    return str(ppath), fname


def _save_user_images(items: list) -> list[str]:
    """把请求里的图片（data URI 列表）存到 MEDIA_DIR，返回媒体 URL 列表（最多 4 张）。"""
    urls: list[str] = []
    for data in [str(x) for x in (items or [])][:4]:
        if not data.startswith("data:image/"):
            continue
        try:
            head, b64 = data.split(",", 1)
            ext = head.split("/")[-1].split(";")[0] or "png"
            if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
                ext = "png"
            if len(b64) > 8 * 1024 * 1024:  # 单张限 8MB（base64 后）
                continue
            p = MEDIA_DIR / f"mc_{uuid.uuid4().hex[:10]}.{ext}"
            p.write_bytes(base64.b64decode(b64))
            urls.append(_media_url(str(p)))
        except Exception:
            continue
    return urls


@app.post("/api/micro/{work_id}/{session_id}/chat")
async def micro_chat(request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)):
    """微创作对话：SSE 流式回复（生成配置取自所属作品）。

    用户消息（可附图，仅视觉模型）先落库；需要出图/出视频时由模型 function call
    自动调用 DrawThings；对话结束后助手消息（文本 + 生成媒体）落库。历史取自数据库（最近 20 条）。
    """
    lang = _lang(request)
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404,
                            detail=L(lang, "会话不存在", "Session not found"))
    work = session.work
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请求体需为 JSON", "Body must be JSON"))
    message = str(body.get("message") or "").strip()
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(work.llm_config_id or "")
    dt_cfg = cs.get_drawthing(work.drawthings_config_id) if work.drawthings_config_id else None

    # 用户附图：VLM 一律支持图片输入（存 MEDIA_DIR，随消息落库）
    image_urls = []
    image_paths: list[str] = []
    raw_images = body.get("images") or []
    if raw_images:
        image_urls = _save_user_images(raw_images)
        image_paths = [str(MEDIA_DIR / u.rsplit("/", 1)[-1].split("?")[0]) for u in image_urls]
    if not message and not image_urls:
        raise HTTPException(status_code=400,
                            detail=L(lang, "消息不能为空", "Message cannot be empty"))

    # 用户消息先落库；空标题会话（及空标题作品）用首条用户消息命名
    last = db.query(MicroMessage).filter_by(session_id=session_id)\
        .order_by(MicroMessage.index.desc()).first()
    user_idx = (last.index + 1) if last else 0
    db.add(MicroMessage(session_id=session_id, index=user_idx, role="user", content=message,
                        created_at=_now(),
                        images=json.dumps(image_urls, ensure_ascii=False) if image_urls else None))
    if not session.title.strip():
        session.title = message[:50]
        if not work.title.strip():
            work.title = message[:50]
    session.updated_at = _now()
    work.updated_at = _now()
    db.commit()

    rows = db.query(MicroMessage).filter(MicroMessage.session_id == session_id,
                                         MicroMessage.index < user_idx)\
        .order_by(MicroMessage.index.desc()).limit(20).all()
    history = []
    for r in reversed(rows):
        imgs = []
        if r.images:
            try:
                for u in json.loads(r.images):
                    p = MEDIA_DIR / str(u).rsplit("/", 1)[-1].split("?")[0]
                    if p.exists():
                        imgs.append(str(p))
            except (ValueError, TypeError):
                pass
        history.append({"role": r.role, "content": r.content, "images": imgs})
    return StreamingResponse(
        _micro_stream(db, session, llm_cfg, dt_cfg, image_paths,
                      message, history, user_idx + 1, lang),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _micro_stream(db: Session, session: MicroSession, llm_cfg, dt_cfg,
                        img_paths: list[str],
                        user_message: str, history: list[dict], assistant_idx: int,
                        lang: str = "zh"):
    """SSE 事件（有序内容块）：token(文本增量) / tool(开始生成) / media(生成结果) /
    tool_error(生成失败) / error(错误) / done(结束)。

    tool / media / tool_error 携带同一 id：前端按 id 把「生成中 → 已生成 / 失败」归到同一块，
    不会出现「提示词/媒体重复、spinner 永不停」的错乱。助手回复按「文本 / 生成」实际发生的
    先后顺序持久化为 parts（JSON 内容块数组），刷新后顺序与流式过程完全一致。
    lang = 请求语言（zh|en），本地化用户可见文案。
    """
    t0 = time.monotonic()  # 本条回复耗时起点（流式开始 → 落库完成）
    if not llm_cfg:
        yield _sse("error", {"message": L(lang, "请选择有效的 VLM 配置", "Please select a valid VLM config")})
        yield _sse("done", {})
        return

    # DrawThings 客户端（gRPC）：按配置的「图像模型 / 视频模型」决定可用产出类型
    dt = build_drawthings_client(dt_cfg, data_dir) if dt_cfg else None
    can_image = bool(dt and dt.supports_image())
    can_video = bool(dt and dt.supports_video())

    if dt and (can_image or can_video):
        kinds = []
        if can_image:
            kinds.append("图片")
        if can_video:
            kinds.append("视频")
        avail = "、".join(kinds)
        ratio = ""
        if can_image:
            limit = int(getattr(dt_cfg, "max_side", 0) or 0) or 1024
            ratio = (f"生成图片时：用户指定比例或用途（海报 / 手机壁纸 / 横屏 / 竖屏 / 方形等）时，"
                     f"换算成具体宽高传给 generate_media 的 width/height（均为 64 的倍数，最长边 ≤ {limit}；"
                     f"参考：1:1=768×768、3:4 竖=576×768、4:3 横=768×576、9:16 竖=576×1024、16:9 横=1024×576）；"
                     f"用户未指定时 width/height 传 0。")
        default_media = "video" if can_video else "image"
        sec_hint = ""
        if can_video:
            cap = int(getattr(dt_cfg, "max_seconds", 0) or 0) or MAX_VIDEO_SECONDS
            cap = min(cap, MAX_VIDEO_SECONDS)
            sec_hint = (f"生成视频时用 seconds 参数指定时长（秒，1~{cap}；用户未指定时传 0 = 用 {cap} 秒），"
                        f"单段视频最长 {cap} 秒。")
        instructions = MC_SYSTEM + (
            f"当前只能生成：{avail}。调用 generate_media 时必须用 media 参数指明类型"
            f"（图片传 media=\"image\"，视频传 media=\"video\"）；用户未明确时默认用 {default_media}。"
            + sec_hint + ratio)
    else:
        instructions = MC_SYSTEM + "当前未配置生成服务，无法出图/出视频：用户要求生成时，请说明暂时无法生成，" \
                                   "但可以代为撰写详细的英文提示词供其后续使用。"
    model = build_model(llm_cfg)
    agent = Agent(model, instructions=instructions)

    # 有序内容块：text / tool（含生成结果与提示词）/ error，按发生顺序保存
    parts: list[dict] = []
    tool_ids = itertools.count(1)
    last_media: dict = {}

    def _add_text(delta: str) -> None:
        """追加文本增量：与上一块同为文本则合并，否则新起一个文本块（保证与生成块的相对顺序）。"""
        if parts and parts[-1].get("type") == "text":
            parts[-1]["text"] = parts[-1].get("text", "") + delta
        else:
            parts.append({"type": "text", "text": delta})

    async def _run(out: asyncio.Queue):
        try:
            async with agent:
                if dt:
                    @agent.tool
                    async def generate_media(ctx: RunContext, prompt: str, media: str = "",
                                             width: int = 0, height: int = 0, seconds: int = 0) -> str:
                        """生成图片或视频：根据详细英文提示词产出单张图或单个视频。

                        Args:
                            prompt: 详细英文提示词（主体、场景、构图、光线、风格；视频补充运镜与动态）
                            media: 产出类型："image" 生成图片 / "video" 生成视频（用户未明确时可留空）
                            width: 图片宽（64 的倍数；用户未指定比例时传 0）
                            height: 图片高（64 的倍数；用户未指定比例时传 0）
                            seconds: 视频时长（秒，1~上限；用户未指定时传 0 = 用配置上限）
                        """
                        kind = (media or "").strip().lower()
                        if kind not in ("image", "video"):
                            kind = "video" if can_video else "image"
                        if (kind == "video" and not can_video) or (kind == "image" and not can_image):
                            name = "视频" if kind == "video" else "图像"
                            msg = L(lang, f"未配置{name}模型：请在 DrawThings 配置里填写{name}模型。",
                                    f"No {'video' if kind == 'video' else 'image'} model configured — set one in the DrawThings config.")
                            await out.put(("tool_error", {"id": f"t{next(tool_ids)}",
                                                          "message": msg, "prompt": prompt}))
                            return f"生成失败：{msg}"
                        # 提示词随工具事件立即下发：生成期间（可达数十秒）气泡内先展示提示词，媒体就绪后同块出现
                        tid = f"t{next(tool_ids)}"
                        if kind == "image":
                            label = L(lang, "正在生成图像…", "Generating image…")
                        elif seconds:
                            label = L(lang, f"正在生成视频（约 {int(seconds)} 秒）…",
                                      f"Generating video (~{int(seconds)}s)…")
                        else:
                            label = L(lang, "正在生成视频…", "Generating video…")
                        block = {"type": "tool", "id": tid, "label": label, "prompt": prompt,
                                 "status": "running", "media": kind, "url": "", "message": ""}
                        parts.append(block)
                        await out.put(("tool", {"id": tid, "label": label, "prompt": prompt, "media": kind}))
                        params = {}
                        if width and height:
                            params = {"width": int(width), "height": int(height)}
                        if kind == "video" and seconds:
                            params["seconds"] = int(seconds)
                        try:
                            if kind == "image":
                                path = await run_sync(partial(dt.generate_image, prompt, params=params))
                            else:
                                path = await run_sync(partial(dt.generate_video, prompt, params=params))
                        except Exception as e:
                            block["status"] = "error"
                            block["message"] = str(e)
                            await out.put(("tool_error", {"id": tid, "message": str(e), "prompt": prompt}))
                            return f"生成失败：{e}。请向用户说明原因并建议如何调整。"
                        url = _media_url(path)
                        block["status"] = "ok"
                        block["url"] = url
                        last_media["url"] = url
                        last_media["prompt"] = prompt
                        await out.put(("media", {"id": tid, "media": kind, "url": url, "prompt": prompt}))
                        return f"生成成功，媒体地址：{url}"

                async with agent.run_stream(user_prompt(user_message, img_paths),
                                           message_history=to_message_history(history)) as result:
                    async for text in result.stream_text(delta=True, debounce_by=None):
                        _add_text(text)
                        await out.put(("token", {"text": text}))
                    await result.get_output()
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
            if text or last_media.get("url"):
                db.add(MicroMessage(session_id=session.id, index=assistant_idx,
                                    role="assistant", content=text, created_at=_now(),
                                    duration=round(time.monotonic() - t0, 1),
                                    media_url=last_media.get("url", ""),
                                    prompt=last_media.get("prompt", ""),
                                    parts=json.dumps(parts, ensure_ascii=False) if parts else None))
                session.updated_at = _now()
                db.commit()
            await out.put(("done", {}))
        except Exception as e:
            msg = L(lang, f"对话失败：{e}", f"Chat failed: {e}")
            parts.append({"type": "error", "message": msg})
            await out.put(("error", {"message": msg}))
            await out.put(("done", {}))
        finally:
            await out.put(("__eof__", None))

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_run(queue))
    try:
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # 心跳：出图/出视频耗时长，保持 SSE 连接不被空闲断开
                continue
            if event == "__eof__":
                break
            yield _sse(event, data)
    finally:
        if not task.done():
            task.cancel()


# ---------------- 项目（JSON API） ----------------
def _ensure_not_finished(project: Project, lang: str) -> None:
    """已完结（锁定）的作品禁止一切编辑/生成操作：需先解锁（服务端兜底，前端按钮同步禁用）。"""
    if pipeline.is_finished(project):
        raise HTTPException(status_code=400,
                            detail=L(lang, "该作品已完结（锁定），请先点「解锁」再操作",
                                     "This work is finished (locked) — click Unlock first"))


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


@app.get("/api/projects")
def projects_list(db: Session = Depends(get_db), page: int = 1, size: int = 10,
                  kind: str = "", status: str = "", q: str = "", sort: str = "desc"):
    """创作列表：类型/状态/关键词筛选 + 排序（创建时间 / 最近活跃）+ 分页。"""
    page, size = _clamp_page(page, size)
    rows, total = pipeline.list_projects(db, kind or None, status or None, q.strip(), sort,
                                         size, (page - 1) * size)
    counts = dict(db.query(Chapter.project_id, func.count(Chapter.id))
                  .group_by(Chapter.project_id).all())
    return {
        "projects": [_project_view(p, counts.get(p.id, 0)) for p in rows],
        "total": total, "page": page, "size": size,
        "total_pages": (total + size - 1) // size if total else 1,
    }


@app.post("/api/projects")
async def project_create(request: Request, db: Session = Depends(get_db)):
    """新建创作：标题 + 主题（一句话）→ 流水线。"""
    lang = _lang(request)
    body = await _json_body(request)
    kind = str(body.get("kind") or "")
    if kind not in ("comic", "drama"):
        raise HTTPException(status_code=400, detail="invalid kind")
    origin = str(body.get("origin") or "").strip()
    if not origin:
        raise HTTPException(status_code=400,
                            detail=L(lang, "主题不能为空", "The idea (origin) cannot be empty"))
    style = str(body.get("style_custom") or "").strip() or str(body.get("style") or "").strip()
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(str(body.get("llm_config_id") or ""))
    dt_cfg = cs.get_drawthing(str(body.get("drawthings_config_id") or ""))
    if not llm_cfg or not dt_cfg:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的 VLM 与 DrawThings 配置",
                                     "Please select valid VLM and DrawThings configs"))
    project = pipeline.create(db, kind, origin, llm_cfg.id, dt_cfg.id,
                               style=style, title=str(body.get("title") or "").strip()[:200])
    return {"id": project.id}


@app.post("/api/projects/{project_id}/rename")
async def project_rename(request: Request, project_id: str, db: Session = Depends(get_db)):
    """重命名创作（修改标题）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail=L(lang, "标题不能为空", "Title cannot be empty"))
    project.title = title[:200]
    project.updated_at = _now()
    db.commit()
    return {"ok": True, "title": title}


@app.post("/api/projects/{project_id}/reset")
async def project_reset(request: Request, project_id: str, db: Session = Depends(get_db)):
    """重新设定：修改标题 / 一句话创意（主题）/ 风格。
    默认不清空下游（保留大纲/章节/已生成媒体）；clear_downstream=true 时清空大纲/章节/媒体并回到 planning。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        pipeline.reset_settings(db, project,
                                title=str(body.get("title") or ""),
                                origin=str(body.get("origin") or ""),
                                style=str(body.get("style") or ""),
                                clear_downstream=bool(body.get("clear_downstream") or False),
                                lang=lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"【重新设定】失败：{e}", f"[Re-set] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/delete")
def project_delete(request: Request, project_id: str, db: Session = Depends(get_db)):
    """删除创作（含全部章节与媒体文件）。"""
    project = pipeline.get(db, project_id)
    if not project:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "项目不存在", "Project not found"))
    pipeline.delete_project(db, project)
    return {"ok": True}


@app.post("/api/projects/{project_id}/complete")
def project_complete(request: Request, project_id: str, db: Session = Depends(get_db)):
    """完结整部作品：要求全部季的章节都已完成（done），完结后作品锁定（只读），需解锁才能继续操作。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)  # 已完结 → 防重复
    try:
        pipeline.complete_project(db, project, lang=lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    return {"ok": True, "status": project.status}


@app.post("/api/projects/{project_id}/unlock")
def project_unlock(request: Request, project_id: str, db: Session = Depends(get_db)):
    """解锁已完结（锁定）的作品：回到「章节已定」状态，可继续编辑 / 生成。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    if not pipeline.is_finished(project):
        raise HTTPException(status_code=400,
                            detail=L(lang, "该作品未处于完结（锁定）状态，无需解锁",
                                     "This project is not finished/locked — nothing to unlock"))
    pipeline.unlock_project(db, project, lang=lang)
    return {"ok": True, "status": project.status}


@app.get("/api/projects/{project_id}")
def project_view(request: Request, project_id: str, db: Session = Depends(get_db)):
    """项目详情：状态/篇幅/总纲/首图 + 章节 + 可选配置。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    pipeline.ensure_first_season(db, project)  # 保证存在第一季（旧项目懒迁移：自动建第一季并入孤儿章节）
    unknown = L(lang, "未知配置", "Unknown config")
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(project.llm_config_id)
    dt_cfg = cs.get_drawthing(project.drawthings_config_id)
    chapters = (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())
    data = {
        "project": {
            "id": project.id, "kind": project.kind, "title": project.title or "",
            "origin": project.origin, "status": project.status,
            "scope": project.scope or {}, "arc": project.arc or "",
            "characters": [{"id": c["id"], "name": c["name"], "description": c["description"],
                             "image_url": _media_url(c["image"])}
                            for c in chars_from_raw(project.characters)],
            "global_prompt": project.global_prompt or "",
            "res_width": project.res_width or 0, "res_height": project.res_height or 0,
            "auto_score": project.auto_score or 0, "score_min": project.score_min or 60,
            "auto_redo": project.auto_redo or 0,
            "count_mode": project.count_mode or "range",
            "count_min": project.count_min or 0, "count_max": project.count_max or 0,
            "first_image_url": _media_url(project.first_image or ""),
            "created_at": project.created_at, "updated_at": project.updated_at,
            "llm_config_id": project.llm_config_id,
            "drawthings_config_id": project.drawthings_config_id,
            "llm_name": llm_cfg.name if llm_cfg else unknown,
            "dt_name": dt_cfg.name if dt_cfg else unknown,
        },
        "seasons": [
            {
                "id": s.id, "number": s.number, "title": s.title or "",
                "arc": s.arc or "",
                "first_image_url": _media_url(s.first_image or ""),
                "cover_as_first_ref": bool(s.cover_as_first_ref),
                "characters": [{"id": c["id"], "name": c["name"], "description": c["description"],
                                 "image_url": _media_url(c["image"])}
                                for c in chars_from_raw(s.characters)],
                "count_mode": s.count_mode or "range",
                "count_min": s.count_min or 0, "count_max": s.count_max or 0,
            }
            for s in (db.query(Season).filter(Season.project_id == project.id)
                      .order_by(Season.number).all())
        ],
        "chapters": [_chapter_view(c) for c in chapters],
    }
    data.update(_config_lists(db, project))
    return data


@app.post("/api/projects/{project_id}/config")
async def project_config_update(request: Request, project_id: str, db: Session = Depends(get_db)):
    """随时调整项目使用的 LLM / DrawThings 配置（下一步起生效）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(str(body.get("llm_config_id") or ""))
    dt_cfg = cs.get_drawthing(str(body.get("drawthings_config_id") or ""))
    if not llm_cfg or not dt_cfg:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的 VLM 与 DrawThings 配置",
                                     "Please select valid VLM and DrawThings configs"))
    project.llm_config_id = llm_cfg.id
    project.drawthings_config_id = dt_cfg.id
    project.updated_at = _now()
    db.commit()
    return {"ok": True}


# ---------------- 季（篇章）CRUD ----------------
@app.post("/api/projects/{project_id}/seasons")
async def project_season_create(request: Request, project_id: str, db: Session = Depends(get_db)):
    """新增一季（body: {title}）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    season = pipeline.add_season(db, project, title=str(body.get("title") or ""))
    return {"id": season.id, "number": season.number, "title": season.title or ""}


@app.patch("/api/projects/{project_id}/seasons/{season_id}")
async def project_season_update(request: Request, project_id: str, season_id: str,
                                 db: Session = Depends(get_db)):
    """保存季（篇章）级编辑：季名 / 季大纲 / 季角色 / 章节数量设定 / 每章(标题+摘要)。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    season = pipeline._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    raw = body.get("chapters")
    chapters = None
    if isinstance(raw, list):
        chapters = [{"title": str(c.get("title") or ""), "summary": str(c.get("summary") or "")}
                    for c in raw if isinstance(c, dict)]
    raw_chars = body.get("characters")
    characters = None
    if isinstance(raw_chars, list):
        characters = [{"id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
                        "description": str(c.get("description") or "")}
                       for c in raw_chars if isinstance(c, dict)]
    try:
        pipeline.save_season(
            db, project, season,
            title=body.get("title"), arc=body.get("arc"),
            characters=characters,
            count_mode=body.get("count_mode"), count_min=body.get("count_min"),
            count_max=body.get("count_max"), chapters=chapters)
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"保存季失败：{e}", f"Save season failed: {e}"))
    return {"ok": True}


@app.delete("/api/projects/{project_id}/seasons/{season_id}")
def project_season_delete(request: Request, project_id: str, season_id: str,
                           db: Session = Depends(get_db)):
    """删除一季（连同其章节/媒体/季角色参考图），剩余季重排。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        pipeline.delete_season(db, project, season_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@app.post("/api/projects/{project_id}/action")
async def project_action(request: Request, project_id: str, db: Session = Depends(get_db)):
    """推进流水线：arc（故事大纲）/ season_arc（本季大纲）/ season_chars（季角色）/ chars（角色设定）/ chapters（拆章）/ generate（生成未完成章节）。
    season_arc / season_chars / chapters / generate 需 body 传 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    step = str(body.get("step") or "")
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    season = None
    if step in ("chapters", "generate", "season_arc", "season_chars"):
        sid = str(body.get("season_id") or "")
        season = pipeline._get_season(db, project, sid) if sid else None
        if season is None:
            raise HTTPException(status_code=400,
                                detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    try:
        if step == "arc":
            project = await pipeline.step_arc(
                db, project, lang,
                res_width=int(body.get("res_width") or 0),
                res_height=int(body.get("res_height") or 0),
                extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "season_arc":
            project = await pipeline.step_season_arc(db, project, season, lang,
                                                     extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "season_chars":
            project = await pipeline.step_season_chars(db, project, season, lang,
                                                       extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "chars":
            project = await pipeline.step_chars(db, project, lang,
                                                extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "chapters":
            project = await pipeline.step_chapters(
                db, project, season, lang,
                count_min=int(body.get("count_min") or 0),
                count_max=int(body.get("count_max") or 0))
        elif step == "generate":
            project = await pipeline.step_generate(db, project, season, lang=lang)
        else:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"未知步骤: {step}", f"Unknown step: {step}"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"【{step}】失败：{e}", f"[{step}] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/action-stream")
async def project_action_stream(request: Request, project_id: str, db: Session = Depends(get_db)):
    """逐章推进的 SSE 进度流：body 传 { step:'generate', season_id, indices:[...] }（省略/空=全部）
    或 { step:'chapters', season_id, count_min, count_max, indices:[...], mode }（逐章规划，数量固定值：
    mode=replan（默认，重做）indices 省略/空=全季重规划、非空=只重规划选中的章节；
    mode=append（新增）在现有章节之后新增 count 章，保留已有章节与媒体）。
    事件：progress(i/total+标题) / chapter(单章完成) / done(附新状态) / error(失败)。"""
    lang = _lang(request)
    body = await _json_body(request)
    step = str(body.get("step") or "generate")
    if step not in ("generate", "chapters", "score"):
        raise HTTPException(status_code=400,
                             detail=L(lang, f"该步骤不支持流式进度：{step}",
                                      f"Streaming progress not supported for step: {step}"))
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    return StreamingResponse(
        _project_action_stream(db, project, season, lang, step, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _project_action_stream(db: Session, project: Project, season: Season,
                                  lang: str, step: str, body: dict):
    """后台执行逐章推进（生成画面 / 章节规划）并逐事件下发：进度/单章回调入队 → SSE 帧；结束发 done，异常发 error。"""
    queue: asyncio.Queue = asyncio.Queue()

    async def progress_cb(current: int, total: int, title: str):
        await queue.put(("progress", {"current": current, "total": total, "title": title or ""}))

    async def chapter_cb(ch):
        # 生成画面：单章两步完成 → 回传提示词/描述/分辨率/媒体/状态，前端逐个刷新（后续章节仍参考它）
        await queue.put(("chapter", {"index": ch.index,
                                     "description": ch.description or "",
                                     "prompt": ch.prompt or "",
                                     "width": ch.width or 0, "height": ch.height or 0,
                                     "media_url": _media_url(ch.media_path or ""),
                                     "status": ch.status, "error": ch.error or "",
                                     "score": ch.score or 0, "score_note": ch.score_note or ""}))

    async def score_cb(ch, score, note, rd):
        # 自动评分事件：score 为 None 时 note 携带阶段（scoring/redo），为数字时是评分结果
        phase = "result" if score is not None else str(note or "")
        await queue.put(("score", {"index": ch.index, "title": ch.title or "",
                                    "score": score,
                                    "note": (note or "") if score is not None else "",
                                    "phase": phase, "redo": rd}))

    async def plan_cb(ch):
        # 章节规划：单章规划完成 → 回传标题/摘要/状态，前端逐个补入
        await queue.put(("chapter", {"index": ch.index, "title": ch.title or "",
                                     "summary": ch.summary or "", "status": ch.status}))

    async def _run():
        try:
            if step == "chapters":
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全季重规划
                await pipeline.step_chapters_stream(
                    db, project, season, lang=lang,
                    count_min=int(body.get("count_min") or 0),
                    count_max=int(body.get("count_max") or 0),
                    indices=indices,
                    mode=str(body.get("mode") or "replan"),  # replan=重做 / append=新增
                    progress_cb=progress_cb, chapter_done_cb=plan_cb)
            elif step == "score":
                # VLM 批量评分：逐章评分（季内），单章失败不阻塞后续章节（error 阶段事件单独提示）
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全季
                chapters = pipeline._season_chapters(db, project, season)
                targets = list(enumerate(chapters)) if indices is None \
                    else [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
                for pos, (i, ch) in enumerate(targets, start=1):
                    await progress_cb(pos, len(targets), ch.title)
                    await score_cb(ch, None, "scoring", 0)
                    try:
                        score, note = await pipeline.vlm_score_chapter(db, project, season, i, lang)
                    except ValueError as e:
                        await queue.put(("score", {"index": ch.index, "title": ch.title or "",
                                                    "score": None, "note": str(e), "phase": "error", "redo": 0}))
                        continue
                    await score_cb(ch, score, note, 0)
                    await chapter_cb(ch)
            else:
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全部
                await pipeline.step_generate(db, project, season, indices=indices, lang=lang,
                                             progress_cb=progress_cb, chapter_done_cb=chapter_cb,
                                             score_cb=score_cb)
            fresh = db.get(Project, project.id)
            await queue.put(("done", {"status": fresh.status if fresh else ""}))
        except Exception as e:
            if step == "chapters":
                msg, msg_en = "规划章节失败：", "Planning chapters failed: "
            elif step == "score":
                msg, msg_en = "批量评分失败：", "Batch scoring failed: "
            else:
                msg, msg_en = "生成画面失败：", "Generation failed: "
            await queue.put(("error", {"message": L(lang, msg + str(e), msg_en + str(e))}))
        finally:
            await queue.put(("__eof__", None))

    task = asyncio.create_task(_run())
    try:
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # 心跳：逐章生成耗时长，保持 SSE 连接不被空闲断开
                continue
            if event == "__eof__":
                break
            yield _sse(event, data)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task  # 等任务清理（逐章提交进度）完成再结束请求（数据库会话此时才关闭）
            except asyncio.CancelledError:
                pass


@app.post("/api/projects/{project_id}/gen/{index}")
async def project_gen_single(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """生成指定章节（季内）：body 需 season_id。两步连贯——① (重新)生成出图提示词 ② 生图/生视频。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    try:
        project = await pipeline.step_generate(db, project, season, indices=[index], lang=lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"【生成第{index+1}章】失败：{e}",
                                      f"[Generate chapter {index+1}] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/chapters/{index}/score")
async def project_chapter_score(request: Request, project_id: str, index: int,
                                db: Session = Depends(get_db)):
    """VLM 自动评分：调用 VLM 对指定章节（季内）重新评分（与流水线自动评分同款）；
    body 需 season_id（无需分值）。评分不涉及生成，已完结作品也允许。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    try:
        score, note = await pipeline.vlm_score_chapter(db, project, season, index, lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    return {"ok": True, "score": score, "note": note}


@app.post("/api/projects/{project_id}/edit/{index}")
async def project_edit(request: Request, project_id: str, index: int,
                       db: Session = Depends(get_db)):
    """保存指定章节（季内）的出图提示词。body 需 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    pipeline.save_chapter_fields(db, project, season, index, str(body.get("prompt") or ""))
    return {"ok": True}


# ---------------- 章节：增删排序（季内） / 手动完成 ----------------
@app.post("/api/projects/{project_id}/chapters")
async def project_chapter_add(request: Request, project_id: str, db: Session = Depends(get_db)):
    """在季末尾新增一章。body 需 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    pipeline.add_chapter(db, project, season)
    return {"ok": True}


@app.delete("/api/projects/{project_id}/chapters/{index}")
async def project_chapter_delete(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """删除季内第 index 章（连同清理媒体文件）。body/query 需 season_id。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(request.query_params.get("season_id") or "")
    if not sid:
        try:
            body = await _json_body(request)
            sid = str(body.get("season_id") or "")
        except Exception:
            pass
    season = pipeline._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    pipeline.delete_chapter(db, project, season, index)
    return {"ok": True}


@app.post("/api/projects/{project_id}/chapters/{index}/move")
async def project_chapter_move(request: Request, project_id: str, index: int,
                                db: Session = Depends(get_db)):
    """上移 / 下移季内第 index 章（body: {season_id, direction}）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    pipeline.move_chapter(db, project, season, index, str(body.get("direction") or ""))
    return {"ok": True}


# ---------------- 封面 / 大纲 ----------------
@app.post("/api/projects/{project_id}/first-image")
def project_first_image_upload(request: Request, project_id: str, file: UploadFile = File(...),
                               db: Session = Depends(get_db)):
    """上传封面（作品封面；可选作为第 1 章参考图）。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    data = file.file.read(MAX_IMAGE_UPLOAD + 1)
    if not data:
        raise HTTPException(status_code=400, detail=L(lang, "文件为空", "File is empty"))
    if len(data) > MAX_IMAGE_UPLOAD:
        raise HTTPException(status_code=400,
                            detail=L(lang, "文件过大（上限 20MB）", "File too large (20MB max)"))
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请上传图片文件（png/jpg/webp/gif）",
                                     "Please upload an image file (png/jpg/webp/gif)"))
    dest = MEDIA_DIR / f"first_{project.id}{ext}"
    dest.write_bytes(data)
    pipeline.set_first_image_path(db, project, str(dest))
    return {"ok": True, "url": _media_url(str(dest))}


@app.post("/api/projects/{project_id}/first-image/generate")
async def project_first_image_generate(request: Request, project_id: str,
                                       db: Session = Depends(get_db)):
    """提示词生成封面（prompt 为空时由 LLM 依据一句话创意+风格自动撰写）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        project = await pipeline.generate_first_image(db, project, str(body.get("prompt") or ""),
                                                       lang=lang,
                                                       include_title=bool(body.get("include_title", True)))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成封面】失败：{e}", f"[Generate cover] failed: {e}"))
    return {"ok": True}


# ---------------- 季封面 ----------------
@app.post("/api/projects/{project_id}/seasons/{season_id}/first-image")
def season_first_image_upload(request: Request, project_id: str, season_id: str,
                              file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传季封面。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    _ensure_not_finished(project, lang)
    data = file.file.read(MAX_IMAGE_UPLOAD + 1)
    if not data:
        raise HTTPException(status_code=400, detail=L(lang, "文件为空", "File is empty"))
    if len(data) > MAX_IMAGE_UPLOAD:
        raise HTTPException(status_code=400,
                            detail=L(lang, "文件过大（上限 20MB）", "File too large (20MB max)"))
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请上传图片文件（png/jpg/webp/gif）",
                                     "Please upload an image file (png/jpg/webp/gif)"))
    dest = MEDIA_DIR / f"seasonfirst_{season.id}{ext}"
    dest.write_bytes(data)
    pipeline.set_season_first_image_path(db, project, season, str(dest))
    return {"ok": True, "url": _media_url(str(dest))}


@app.post("/api/projects/{project_id}/seasons/{season_id}/first-image/generate")
async def season_first_image_generate(request: Request, project_id: str, season_id: str,
                                      db: Session = Depends(get_db)):
    """提示词生成季封面（prompt 为空时由 LLM 依据季大纲/季角色自动撰写并叠加季名）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    _ensure_not_finished(project, lang)
    try:
        season = await pipeline.generate_season_first_image(db, project, season,
                                                            str(body.get("prompt") or ""),
                                                            lang=lang,
                                                            include_title=bool(body.get("include_title", True)))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成季封面】失败：{e}",
                                     f"[Generate season cover] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/first-image/overlay-title")
async def project_first_image_overlay_title(request: Request, project_id: str,
                                            db: Session = Depends(get_db)):
    """把作品标题叠加到现有封面上（PIL 合成；可传位置/字号/样式/颜色；不重新生图）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        project = pipeline.overlay_first_image_title(db, project, lang, _overlay_opts(body))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【叠加标题】失败：{e}", f"[Overlay title] failed: {e}"))
    return {"ok": True, "url": _media_url(project.first_image or "")}


@app.post("/api/projects/{project_id}/seasons/{season_id}/first-image/ref")
async def season_cover_ref(request: Request, project_id: str, season_id: str,
                           db: Session = Depends(get_db)):
    """设置是否把季封面作为本季第 1 章参考（漫画 img2img / 短剧首帧）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    _ensure_not_finished(project, lang)
    enabled = bool(body.get("enabled"))
    pipeline.set_season_cover_ref(db, project, season, enabled)
    return {"ok": True, "enabled": enabled}


@app.post("/api/projects/{project_id}/seasons/{season_id}/first-image/overlay-title")
async def season_first_image_overlay_title(request: Request, project_id: str, season_id: str,
                                           db: Session = Depends(get_db)):
    """把季名叠加到现有季封面上（PIL 合成；可传位置/字号/样式/颜色；不重新生图）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    _ensure_not_finished(project, lang)
    try:
        season = pipeline.overlay_season_first_image_title(db, project, season, lang, _overlay_opts(body))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【叠加季名】失败：{e}", f"[Overlay season name] failed: {e}"))
    return {"ok": True, "url": _media_url(season.first_image or "")}


@app.post("/api/projects/{project_id}/characters/{char_id}/image")
def project_char_image_upload(request: Request, project_id: str, char_id: str,
                              file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传角色参考图（角色设定页：供各章保持角色形象一致）。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    data = file.file.read(MAX_IMAGE_UPLOAD + 1)
    if not data:
        raise HTTPException(status_code=400, detail=L(lang, "文件为空", "File is empty"))
    if len(data) > MAX_IMAGE_UPLOAD:
        raise HTTPException(status_code=400,
                            detail=L(lang, "文件过大（上限 20MB）", "File too large (20MB max)"))
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请上传图片文件（png/jpg/webp/gif）",
                                     "Please upload an image file (png/jpg/webp/gif)"))
    dest = MEDIA_DIR / f"char_{project.id}_{char_id}{ext}"
    dest.write_bytes(data)
    try:
        pipeline.set_char_image(db, project, char_id, str(dest))
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【上传角色参考图】失败：{e}",
                                     f"[Upload character image] failed: {e}"))
    return {"ok": True, "url": _media_url(str(dest))}


@app.delete("/api/projects/{project_id}/characters/{char_id}/image")
def project_char_image_delete(request: Request, project_id: str, char_id: str,
                              db: Session = Depends(get_db)):
    """清除角色参考图（连同删除文件）。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        pipeline.set_char_image(db, project, char_id, "")
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【清除角色参考图】失败：{e}",
                                     f"[Remove character image] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/characters/{char_id}/gen-desc")
async def project_char_gen_desc(request: Request, project_id: str, char_id: str,
                                db: Session = Depends(get_db)):
    """AI 生成单个角色的形象/性格描述（该角色有参考图时以图为准，VLM 一律支持图片输入）。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        desc = await pipeline.gen_char_description(db, project, char_id, lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成角色描述】失败：{e}",
                                     f"[Generate character description] failed: {e}"))
    return {"ok": True, "description": desc}


@app.post("/api/projects/{project_id}/outline")
async def project_outline_save(request: Request, project_id: str, db: Session = Depends(get_db)):
    """保存大纲页手动编辑：大纲 / 角色设定 / 风格 / 默认分辨率（章节按季编辑，见 /seasons）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    raw_chars = body.get("characters")
    characters = None
    if isinstance(raw_chars, list):
        # 参考图不随保存传（按 id 由后端保留）；这里只同步 名字 / 描述
        characters = [{"id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
                        "description": str(c.get("description") or "")}
                       for c in raw_chars if isinstance(c, dict)]
    try:
        pipeline.save_outline(
            db, project,
            arc=body.get("arc"), characters=body.get("characters"), style=body.get("style"),
            global_prompt=body.get("global_prompt"),
            res_width=body.get("res_width"), res_height=body.get("res_height"),
            count_mode=body.get("count_mode"), count_min=body.get("count_min"),
            count_max=body.get("count_max"),
            auto_score=body.get("auto_score"), score_min=body.get("score_min"),
            auto_redo=body.get("auto_redo"))
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"保存大纲失败：{e}", f"Save outline failed: {e}"))
    return {"ok": True}


# ---------------- 导出（ZIP / PDF） ----------------
@app.get("/api/projects/{project_id}/export/zip")
def project_export_zip(request: Request, project_id: str, season_id: str | None = None,
                        db: Session = Depends(get_db)):
    """导出 ZIP：媒体（图/视频）+ 首图 + 大纲/角色/各章剧本文本；season_id 非空时仅导出该季章节。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = None
    if season_id:
        season = pipeline._get_season(db, project, season_id)
        if season is None:
            raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    try:
        path, fname = pipeline.export_zip(db, project, season)
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"导出 ZIP 失败：{e}", f"Export ZIP failed: {e}"))
    return FileResponse(path, filename=fname, media_type="application/zip")


@app.get("/api/projects/{project_id}/export/pdf")
def project_export_pdf(request: Request, project_id: str, season_id: str | None = None,
                        preview: int = 0, db: Session = Depends(get_db)):
    """导出 PDF（漫画：各章图片按序拼成多页；短剧/无图 → 400）；season_id 非空时仅导出该季章节。
    preview=1：以 inline 返回（浏览器新标签直接预览，不触发下载）。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = None
    if season_id:
        season = pipeline._get_season(db, project, season_id)
        if season is None:
            raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    try:
        path, fname = pipeline.export_pdf(db, project, season)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"导出 PDF 失败：{e}", f"Export PDF failed: {e}"))
    # preview=1 用 inline（浏览器直接渲染）；统一走 Starlette 的 filename 处理
    #（非 ASCII 自动 UTF-8 百分号编码 filename*=utf-8''，中文文件名不会 500）
    return FileResponse(path, media_type="application/pdf", filename=fname,
                        content_disposition_type="inline" if preview else "attachment")


# ---------------- 应用健康检查（CS 桌面客户端探测用） ----------------
@app.get("/api/health")
def health():
    """健康检查：桌面客户端（CS）启动时轮询此端点判断本地服务是否就绪；亦可用于存活监控。"""
    return {"ok": True, "app": "drawthings-studio"}


# ---------------- SPA 外壳 ----------------
SPA_SHELL = STATIC_ROOT / "spa" / "index.html"


@app.get("/{full_path:path}")
def spa_shell(full_path: str):
    """非 /api、/static、/media 的路径一律返回 SPA 外壳（前端路由接管，刷新深链可用）。"""
    return FileResponse(SPA_SHELL, media_type="text/html", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import os as _os
    import uvicorn
    # CS 桌面客户端以 RELOAD=0 拉起本进程（生产模式）；开发时可用 RELOAD=1 python main.py。
    # 默认只绑 127.0.0.1（本地单用户，不对外；如需局域网访问可显式设 HOST=0.0.0.0）。
    uvicorn.run("main:app",
                host=_os.getenv("HOST", "127.0.0.1"),
                port=int(_os.getenv("PORT", "8010")),
                reload=_os.getenv("RELOAD", "0") == "1")
