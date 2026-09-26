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
from services.pipeline import _now, chars_from_raw, hex_to_rgb, run_sync
from services.drawthings import build_drawthings_client, MAX_VIDEO_SECONDS, norm_ref_flag
from services.runtime import pipeline
from services.api_common import (
    MEDIA_DIR, _lang, _json_body, _media_url, _chapter_view, _llm_view, _dt_view,
    _dt_ref_field, _dt_gen_fields, _project_view, _config_lists, _clamp_page,
    _ensure_not_finished, _sse,
)
from services import api_comic, api_drama

STATIC_ROOT = resource_root() / "static"

MAX_REQUEST_BODY = 64 * 1024 * 1024  # 请求体上限 64MB（容纳多张 base64 附图；超限直接拒绝）
MC_PAGE_SIZE = 10  # 微创作作品列表每页条数

app = FastAPI(title="Drawthings Studio")
app.add_middleware(GZipMiddleware, minimum_size=500)  # HTML/CSS/JS 压缩，减少传输体积
app.include_router(api_comic.router)   # 漫画 API（/api/comics/*）
app.include_router(api_drama.router)   # 短剧 API（/api/dramas/*）

logger = logging.getLogger("drawthings")




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
















# ---------------- 静态媒体 ----------------
@app.get("/media/{filename}")
def serve_media(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith((".", "%")):
        raise HTTPException(status_code=404, detail="not found")
    path = MEDIA_DIR / filename
    if not path.is_file() or not path.resolve().is_relative_to(MEDIA_DIR.resolve()):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)




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
        dt_model_image=str(body.get("dt_model_image") or "").strip()[:200],
        dt_model_video=str(body.get("dt_model_video") or "").strip()[:200],
        dt_ref_image=_dt_ref_field(body, "dt_ref_image"),
        dt_ref_video=_dt_ref_field(body, "dt_ref_video"),
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
            "dt_model_image": work.dt_model_image or "",
            "dt_model_video": work.dt_model_video or "",
            "dt_ref_image": work.dt_ref_image or "",
            "dt_ref_video": work.dt_ref_video or "",
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
    work.dt_model_image = str(body.get("dt_model_image") or "").strip()[:200]
    work.dt_model_video = str(body.get("dt_model_video") or "").strip()[:200]
    work.dt_ref_image = _dt_ref_field(body, "dt_ref_image")
    work.dt_ref_video = _dt_ref_field(body, "dt_ref_video")
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
        _micro_stream(db, session, llm_cfg, dt_cfg, work, image_paths,
                      message, history, user_idx + 1, lang),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _micro_stream(db: Session, session: MicroSession, llm_cfg, dt_cfg,
                        work: "MicroWork",
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

    # DrawThings 客户端（gRPC）：功能级模型/参考图开关优先（作品自选），空 = 跟随配置
    dt = (build_drawthings_client(
        dt_cfg, data_dir,
        model_image=getattr(work, "dt_model_image", "") or "",
        model_video=getattr(work, "dt_model_video", "") or "",
        ref_image=norm_ref_flag(getattr(work, "dt_ref_image", "")),
        ref_video=norm_ref_flag(getattr(work, "dt_ref_video", "")))
        if dt_cfg else None)
    can_image = bool(dt and dt.supports_image())
    can_video = bool(dt and dt.supports_video())
    # 生效的「支持参考图片」（功能级优先、配置兜底）：决定提示词是否写成参考图修改指令
    _r = norm_ref_flag(getattr(work, "dt_ref_image", ""))
    eff_ref_img = bool(_r) if _r is not None else bool(getattr(dt_cfg, "ref_image", 0))
    _r = norm_ref_flag(getattr(work, "dt_ref_video", ""))
    eff_ref_vid = bool(_r) if _r is not None else bool(getattr(dt_cfg, "ref_video", 0))

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
        # 参考图模式：勾选「支持参考图片」后，附图 / 最近生成的媒体会作为参考图 →
        # 提示词写成基于参考图的修改指令，而不是从头完整描述
        if eff_ref_img or eff_ref_vid:
            instructions += (
                "参考图模式：附图（无附图时为本会话最近一次生成的媒体）将作为图生图 / 图生视频的参考图。"
                "此时 prompt 必须写成针对参考图的修改指令：先用一句话点明需与参考图保持一致的元素"
                "（角色、服装、画风、光照、构图），再具体描述用户本次要求的改动；不要从头重新描述整个画面。")
    else:
        instructions = MC_SYSTEM + "当前未配置生成服务，无法出图/出视频：用户要求生成时，请说明暂时无法生成，" \
                                   "但可以代为撰写详细的英文提示词供其后续使用。"
    model = build_model(llm_cfg)
    agent = Agent(model, instructions=instructions)

    # 有序内容块：text / tool（含生成结果与提示词）/ error，按发生顺序保存
    parts: list[dict] = []
    tool_ids = itertools.count(1)
    last_media: dict = {}  # 上次成功生成的媒体：{url, prompt, path}（path 供后续生成作参考图回退）

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

                        若用户当前消息附带了图片，会自动作为参考图做图生图 / 图生视频（受配置「支持参考图片」开关
                        控制，未开启时按文生图 / 文生视频）；无附图时回退使用本会话最近一次生成的媒体作参考。

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
                        # 参考图：当前消息附图优先，回退会话内上次生成的媒体（用不用由客户端按「支持参考图片」勾选决定）
                        ref = img_paths[-1] if img_paths else last_media.get("path")
                        # 生成状态实时透传（如「正在等待 Draw Things 恢复…」）→ 前端工具气泡
                        _loop = asyncio.get_running_loop()
                        dt.on_status = lambda msg: _loop.call_soon_threadsafe(
                            out.put_nowait, ("tool_status", {"id": tid, "message": msg}))
                        try:
                            if kind == "image":
                                path = await run_sync(partial(dt.generate_image, prompt,
                                                             ref_path=ref, params=params))
                            else:
                                path = await run_sync(partial(dt.generate_video, prompt,
                                                              ref_video_path=ref, params=params))
                        except Exception as e:
                            block["status"] = "error"
                            block["message"] = str(e)
                            await out.put(("tool_error", {"id": tid, "message": str(e), "prompt": prompt}))
                            return f"生成失败：{e}。请向用户说明原因并建议如何调整。"
                        finally:
                            dt.on_status = None
                        url = _media_url(path)
                        block["status"] = "ok"
                        block["url"] = url
                        last_media["url"] = url
                        last_media["prompt"] = prompt
                        last_media["path"] = path
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
