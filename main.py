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
import asyncio
import base64
import json
import time
import uuid
from pathlib import Path

import anyio
from fastapi import FastAPI, Request, HTTPException, Depends, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic_ai import Agent, RunContext
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import data_dir
from db import get_db, init_db
from i18n import L, lang_of
from models import Project, Chapter, MicroWork, MicroSession, MicroMessage
from config_store import ConfigStore
from services.agent import build_model, to_message_history, user_prompt, make_httpx_client
from services.pipeline import Pipeline, _now
from services.drawthings import DrawThingsClient

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = Path(data_dir) / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Drawthings Studio")
app.add_middleware(GZipMiddleware, minimum_size=500)  # HTML/CSS/JS 压缩，减少传输体积


def _lang(request: Request) -> str:
    """当前请求的语言（Accept-Language → zh|en），用于本地化用户可见文案。"""
    return lang_of(request.headers.get("accept-language", ""))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def _static_cache(request: Request, call_next):
    """静态资源缓存头：vendor 依赖内容不变（长缓存）；spa 源码常改（no-cache 每次校验）。"""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/vendor/"):
        response.headers["Cache-Control"] = "max-age=3600"
    elif path.startswith("/static/spa/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

init_db()  # 建表（幂等）

pipeline = Pipeline(data_dir)


def _media_url(media_path: str) -> str:
    if not media_path:
        return ""
    return f"/media/{Path(media_path).name}"


def _chapter_view(ch) -> dict:
    return {
        "index": ch.index,
        "title": ch.title,
        "description": ch.description,
        "prompt": ch.prompt,
        "width": ch.width or 0,
        "height": ch.height or 0,
        "media_url": _media_url(ch.media_path),
        "status": ch.status,
        "error": ch.error,
    }


def _llm_view(c) -> dict:
    return {
        "id": c.id, "name": c.name, "base_url": c.base_url, "model": c.model,
        "supports_vision": c.supports_vision or "yes", "created_at": c.created_at,
    }


def _dt_view(c) -> dict:
    return {
        "id": c.id, "name": c.name, "base_url": c.base_url,
        "max_side": c.max_side or 0,
        "max_frames": c.max_frames or 0,
        "created_at": c.created_at,
    }


def _dt_gen_fields(body: dict, lang: str = "zh") -> dict:
    """DrawThings 个性化参数：0/空 = 跟随 app 当前值。非法值直接 400。"""
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
    return {
        "max_side": int(num("max_side", int, 2048)),
        "max_frames": int(num("max_frames", int, 2048)),
    }


def _project_view(p: Project, chapter_count: int = 0) -> dict:
    scope = p.scope or {}
    return {
        "id": p.id, "kind": p.kind, "title": p.title or "", "origin": p.origin,
        "status": p.status, "created_at": p.created_at, "updated_at": p.updated_at,
        "scope": scope, "total_chapters": scope.get("total_chapters"),
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
@app.get("/api/choices")
def choices(db: Session = Depends(get_db)):
    """新建作品/创作时的可选项：全部 LLM + 全部 DrawThings 配置。"""
    cs = ConfigStore(db)
    return {"llm_configs": [_llm_view(c) for c in cs.list_llm()],
            "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()]}


@app.get("/api/configs")
def configs_list(db: Session = Depends(get_db), ctype: str = ""):
    """配置列表：ctype=llm|drawthings。"""
    cs = ConfigStore(db)
    if ctype == "drawthings":
        items = [_dt_view(c) for c in cs.list_drawthing()]
    else:
        items = [_llm_view(c) for c in cs.list_llm()]
    return {"ctype": ctype or "llm", "items": items,
            "llm_count": len(cs.list_llm()), "drawthing_count": len(cs.list_drawthing())}


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
    supports_vision = str(body.get("supports_vision") or "yes").lower()
    if supports_vision not in ("yes", "no"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "图片输入选项无效", "Invalid image-input option"))
    cs = ConfigStore(db)
    try:
        if config_type == "llm":
            model = str(body.get("model") or "").strip()
            if not model:
                raise HTTPException(status_code=400,
                                    detail=L(lang, "LLM 配置需要模型名", "LLM config requires a model name"))
            cs.create_llm(name, base_url, str(body.get("api_key") or ""), model,
                          supports_vision=supports_vision)
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
                                    detail=L(lang, "LLM 配置需要模型名", "LLM config requires a model name"))
            supports_vision = str(body.get("supports_vision") or "yes").lower()
            if supports_vision not in ("yes", "no"):
                raise HTTPException(status_code=400,
                                    detail=L(lang, "图片输入选项无效", "Invalid image-input option"))
            raw_key = body.get("api_key")
            updated = cs.update_llm(config_id, name=name, base_url=base_url,
                                    api_key=None if raw_key in (None, "") else str(raw_key),
                                    model=model, supports_vision=supports_vision)
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
        raise HTTPException(status_code=404, detail=L(lang, "配置不存在", "Config not found"))
    db.commit()
    return {"ok": True}


# ---------------- 微创作（作品 → 多个独立会话 → 消息：历史持久化 + SSE 流式 + function call 出图/出视频）----------------
MC_SYSTEM = ("你是漫画/短剧创作的创意助手，擅长创意构思、角色与剧情设计、分镜和提示词，回答简洁、具体。"
              "用户只是提问时直接文本回答；用户要求生成图片/视频时，先调用 generate_media 工具"
              "（提供详细英文提示词：主体、场景、构图、光线、风格；视频补充运镜与动态），"
              "生成成功后用一两句话说明结果。"
              "始终用用户所用的语言回答（用户用中文提问则答中文，用英文提问则答英文）。")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


MC_PAGE_SIZE = 10  # 微创作作品列表每页条数


def _work_paged(db: Session, page: int) -> tuple[int, int, list[MicroWork]]:
    """分页取作品列表（按最近活跃倒序）：返回 (页码, 总数, 当前页作品)。"""
    total = db.query(func.count(MicroWork.id)).scalar() or 0
    total_pages = max((total + MC_PAGE_SIZE - 1) // MC_PAGE_SIZE, 1)
    page = min(max(page, 1), total_pages)  # 越界页码收敛到有效范围
    rows = (db.query(MicroWork).order_by(MicroWork.updated_at.desc())
            .offset((page - 1) * MC_PAGE_SIZE).limit(MC_PAGE_SIZE).all())
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
def micro_works(db: Session = Depends(get_db), page: int = 1):
    """作品列表（按最近活跃倒序，分页 10/页）+ 新建作品可选配置。"""
    page, total, works = _work_paged(db, page)
    counts = dict(db.query(MicroSession.micro_id, func.count(MicroSession.id))
                  .filter(MicroSession.micro_id.in_([w.id for w in works] or [""]))
                  .group_by(MicroSession.micro_id).all())
    cs = ConfigStore(db)
    return {
        "works": [{
            "id": w.id, "title": w.title, "llm_config_id": w.llm_config_id,
            "drawthings_config_id": w.drawthings_config_id,
            "updated_at": w.updated_at, "session_count": counts.get(w.id, 0),
        } for w in works],
        "total": total, "page": page, "size": MC_PAGE_SIZE,
        "total_pages": max((total + MC_PAGE_SIZE - 1) // MC_PAGE_SIZE, 1),
        "llm_configs": [_llm_view(c) for c in cs.list_llm()],
        "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()],
    }


@app.post("/api/micro")
async def micro_create(request: Request, db: Session = Depends(get_db)):
    """新建微创作作品（含首个会话）。"""
    lang = _lang(request)
    body = await _json_body(request)
    cs = ConfigStore(db)
    llm_config_id = str(body.get("llm_config_id") or "")
    if not cs.get_llm(llm_config_id):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的 LLM 配置", "Please select a valid LLM config"))
    w = MicroWork(
        id=uuid.uuid4().hex[:12],
        title=str(body.get("title") or "").strip()[:200],
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
            "vision": bool(llm_cfg and llm_cfg.supports_vision == "yes"),
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
        msgs.append({"index": m.index, "role": m.role, "content": m.content,
                     "created_at": m.created_at or "",
                     "duration": m.duration or 0,
                     "images": imgs, "media_url": m.media_url or "", "prompt": m.prompt or ""})
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
                            detail=L(lang, "请选择有效的 LLM 配置", "Please select a valid LLM config"))
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
        f = MEDIA_DIR / url.rsplit("/", 1)[-1]
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
    # 产出类型不再手动选择：按 app 当前加载的模型自动判断（视频模型 → 视频）
    media = "image"
    if dt_cfg:
        try:
            media = DrawThingsClient(dt_cfg, data_dir).detect_media_type()
        except Exception:
            media = "image"

    # 用户附图：仅所选 LLM 支持视觉时可用（存 MEDIA_DIR，随消息落库）
    image_urls = []
    image_paths: list[str] = []
    raw_images = body.get("images") or []
    if raw_images:
        if not (llm_cfg and llm_cfg.supports_vision == "yes"):
            raise HTTPException(status_code=400,
                                detail=L(lang, "当前 LLM 配置不支持图片",
                                         "The selected LLM config does not support images"))
        image_urls = _save_user_images(raw_images)
        image_paths = [str(MEDIA_DIR / u.rsplit("/", 1)[-1]) for u in image_urls]
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
                    p = MEDIA_DIR / str(u).rsplit("/", 1)[-1]
                    if p.exists():
                        imgs.append(str(p))
            except (ValueError, TypeError):
                pass
        history.append({"role": r.role, "content": r.content, "images": imgs})
    return StreamingResponse(
        _micro_stream(db, session, llm_cfg, dt_cfg, media, image_paths,
                      message, history, user_idx + 1, lang),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _micro_stream(db: Session, session: MicroSession, llm_cfg, dt_cfg,
                        media: str, img_paths: list[str],
                        user_message: str, history: list[dict], assistant_idx: int,
                        lang: str = "zh"):
    """SSE 事件：token(文本增量) / tool(开始生成) / media(生成结果) /
    tool_error(生成失败) / error(错误) / done(结束)。

    对话成功后把助手消息（文本 + 媒体）持久化到会话。lang = 请求语言（zh|en），本地化用户可见文案。
    """
    t0 = time.monotonic()  # 本条回复耗时起点（流式开始 → 落库完成）
    if not llm_cfg:
        yield _sse("error", {"message": L(lang, "请选择有效的 LLM 配置", "Please select a valid LLM config")})
        yield _sse("done", {})
        return

    # 未选 DrawThings 配置 = 纯对话模式：不注册生成工具，并明确告知模型
    if dt_cfg:
        limit = int(getattr(dt_cfg, "max_side", 0) or 0) or 1024
        instructions = MC_SYSTEM + (
            f"图像比例：用户指定比例或用途（海报 / 手机壁纸 / 横屏 / 竖屏 / 方形等）时，"
            f"换算成具体宽高传给 generate_media 的 width/height（均为 64 的倍数，最长边 ≤ {limit}；"
            f"参考：1:1=768×768、3:4 竖=576×768、4:3 横=768×576、9:16 竖=576×1024、16:9 横=1024×576）；"
            f"用户未指定时 width/height 传 0，跟随 app 当前分辨率。")
    else:
        instructions = MC_SYSTEM + "当前未配置生成服务，无法出图/出视频：用户要求生成时，请说明暂时无法生成，" \
                                   "但可以代为撰写详细的英文提示词供其后续使用。"
    model = build_model(llm_cfg)
    agent = Agent(model, instructions=instructions)

    text_parts: list[str] = []
    media_info: dict = {}

    async def _run(out: asyncio.Queue):
        try:
            async with agent:
                if dt_cfg:
                    dt = DrawThingsClient(dt_cfg, data_dir)

                    @agent.tool
                    async def generate_media(ctx: RunContext, prompt: str,
                                             width: int = 0, height: int = 0) -> str:
                        """生成图片/视频：根据详细英文提示词产出单张图或单个视频。

                        Args:
                            prompt: 详细英文提示词（主体、场景、构图、光线、风格；视频补充运镜与动态）
                            width: 图像宽（64 的倍数；用户未指定比例时传 0 = 跟随 app 当前分辨率）
                            height: 图像高（64 的倍数；用户未指定比例时传 0 = 跟随 app 当前分辨率）
                        """
                        # 提示词随工具事件立即下发：生成期间（可达数十秒）气泡内先展示提示词，图片就绪后同气泡出现
                        if media == "image":
                            label = L(lang, "正在生成图像…", "Generating image…")
                        else:
                            label = L(lang, "正在生成视频…", "Generating video…")
                        await out.put(("tool", {"label": label, "prompt": prompt}))
                        params = {}
                        if width and height:
                            params = {"width": int(width), "height": int(height)}
                        try:
                            if media == "image":
                                path = await anyio.to_thread.run_sync(
                                    lambda: dt.generate_image(prompt, params=params))
                            else:
                                path = await anyio.to_thread.run_sync(
                                    lambda: dt.generate_video(prompt, params=params))
                        except Exception as e:
                            await out.put(("tool_error", {"message": str(e), "prompt": prompt}))
                            return f"生成失败：{e}。请向用户说明原因并建议如何调整。"
                        url = _media_url(path)
                        media_info["url"] = url
                        media_info["prompt"] = prompt
                        await out.put(("media", {"media": media, "url": url, "prompt": prompt}))
                        return f"生成成功，媒体地址：{url}"

                async with agent.run_stream(user_prompt(user_message, img_paths),
                                           message_history=to_message_history(history)) as result:
                    async for text in result.stream_text(delta=True, debounce_by=None):
                        text_parts.append(text)
                        await out.put(("token", {"text": text}))
                    await result.get_output()
            text = "".join(text_parts).strip()
            if text or media_info.get("url"):
                db.add(MicroMessage(session_id=session.id, index=assistant_idx,
                                    role="assistant", content=text, created_at=_now(),
                                    duration=round(time.monotonic() - t0, 1),
                                    media_url=media_info.get("url", ""),
                                    prompt=media_info.get("prompt", "")))
                session.updated_at = _now()
                db.commit()
            await out.put(("done", {}))
        except Exception as e:
            await out.put(("error", {"message": L(lang, f"对话失败：{e}", f"Chat failed: {e}")}))
            await out.put(("done", {}))
        finally:
            await out.put(("__eof__", None))

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_run(queue))
    try:
        while True:
            event, data = await queue.get()
            if event == "__eof__":
                break
            yield _sse(event, data)
    finally:
        if not task.done():
            task.cancel()


# ---------------- 项目（JSON API） ----------------
@app.get("/api/projects")
def projects_list(db: Session = Depends(get_db), page: int = 1, size: int = 10,
                  kind: str = "", status: str = "", sort: str = "desc"):
    """创作列表：类型/状态/排序筛选 + 分页。"""
    page, size = _clamp_page(page, size)
    sort_desc = sort != "asc"
    rows, total = pipeline.list_projects(db, kind or None, status or None, sort_desc,
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
                            detail=L(lang, "请选择有效的 LLM 与 DrawThings 配置",
                                     "Please select valid LLM and DrawThings configs"))
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
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail=L(lang, "标题不能为空", "Title cannot be empty"))
    project.title = title[:200]
    project.updated_at = _now()
    db.commit()
    return {"ok": True, "title": title}


@app.post("/api/projects/{project_id}/delete")
def project_delete(request: Request, project_id: str, db: Session = Depends(get_db)):
    """删除创作（含全部章节与媒体文件）。"""
    project = pipeline.get(db, project_id)
    if not project:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "项目不存在", "Project not found"))
    pipeline.delete_project(db, project)
    return {"ok": True}


@app.get("/api/projects/{project_id}")
def project_view(request: Request, project_id: str, db: Session = Depends(get_db)):
    """项目详情：状态/篇幅/总纲/首图 + 章节 + 可选配置。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
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
            "first_image_url": _media_url(project.first_image or ""),
            "created_at": project.created_at, "updated_at": project.updated_at,
            "llm_config_id": project.llm_config_id,
            "drawthings_config_id": project.drawthings_config_id,
            "llm_name": llm_cfg.name if llm_cfg else unknown,
            "dt_name": dt_cfg.name if dt_cfg else unknown,
        },
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
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(str(body.get("llm_config_id") or ""))
    dt_cfg = cs.get_drawthing(str(body.get("drawthings_config_id") or ""))
    if not llm_cfg or not dt_cfg:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的 LLM 与 DrawThings 配置",
                                     "Please select valid LLM and DrawThings configs"))
    project.llm_config_id = llm_cfg.id
    project.drawthings_config_id = dt_cfg.id
    project.updated_at = _now()
    db.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/action")
async def project_action(request: Request, project_id: str, db: Session = Depends(get_db)):
    """推进流水线：scope/arc/chapters/script/generate（LLM 步骤耗时较长）。"""
    lang = _lang(request)
    body = await _json_body(request)
    step = str(body.get("step") or "")
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    try:
        if step == "scope":
            project = await pipeline.step_scope(db, project, lang)
        elif step == "arc":
            project = await pipeline.step_arc(db, project, lang)
        elif step == "chapters":
            project = await pipeline.step_chapters(db, project, lang)
        elif step == "script":
            project = await pipeline.step_script(db, project, lang)
        elif step == "generate":
            project = pipeline.step_generate(db, project, lang=lang)
        else:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"未知步骤: {step}", f"Unknown step: {step}"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【{step}】失败：{e}", f"[{step}] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/gen/{index}")
def project_gen_single(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """生成指定章节（图/视频）。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    chapters = (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    try:
        project = pipeline.step_generate(db, project, index=index, lang=lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成第{index+1}章】失败：{e}",
                                     f"[Generate chapter {index+1}] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/edit/{index}")
async def project_edit(request: Request, project_id: str, index: int,
                       db: Session = Depends(get_db)):
    """保存指定章节的提示词（修改后需重生成应用）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    chapters = (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    pipeline.edit_prompt(db, project, index, str(body.get("prompt") or ""))
    return {"ok": True}


@app.post("/api/projects/{project_id}/regenerate/{index}")
def project_regenerate(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """重新生成指定章节。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    chapters = (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    try:
        project = pipeline.regenerate(db, project, index, lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【重生成第{index+1}章】失败：{e}",
                                     f"[Regenerate chapter {index+1}] failed: {e}"))
    return {"ok": True}


# ---------------- 首图 / 总纲 ----------------
@app.post("/api/projects/{project_id}/first-image")
def project_first_image_upload(request: Request, project_id: str, file: UploadFile = File(...),
                               db: Session = Depends(get_db)):
    """上传首图（作为第 1 章参考图）。"""
    lang = _lang(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail=L(lang, "文件为空", "File is empty"))
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
    """提示词生成首图（prompt 为空时由 LLM 依据一句话创意+风格自动撰写）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    try:
        project = await pipeline.generate_first_image(db, project, str(body.get("prompt") or ""),
                                                      lang=lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成首图】失败：{e}", f"[Generate first image] failed: {e}"))
    return {"ok": True}


@app.post("/api/projects/{project_id}/arc")
async def project_arc_save(request: Request, project_id: str, db: Session = Depends(get_db)):
    """保存用户手改的整体故事总纲（整体路线控制）。"""
    body = await _json_body(request)
    project = pipeline.get(db, project_id)
    if project is None:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "项目不存在", "Project not found"))
    pipeline.save_arc(db, project, str(body.get("arc") or "").strip())
    return {"ok": True}


# ---------------- SPA 外壳 ----------------
SPA_SHELL = BASE_DIR / "static" / "spa" / "index.html"


@app.get("/{full_path:path}")
def spa_shell(full_path: str):
    """非 /api、/static、/media 的路径一律返回 SPA 外壳（前端路由接管，刷新深链可用）。"""
    return FileResponse(SPA_SHELL, media_type="text/html", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=True)
