"""微创作 API 路由（/api/micro/*）：作品 → 多个独立会话 → 消息。

与项目侧 `api_comic / api_drama` 对称：本模块只做 HTTP/SSE 与持久化编排，
会话推理与生成在 `services/micro_agent.py`；共享基础设施取 `services/api_common.py`。
"""
import asyncio
import json
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from config_store import ConfigStore
from db import get_db
from i18n import L
from models import MicroMessage, MicroSession, MicroWork
from services.api_common import (
    MEDIA_DIR, _clamp_page, _dt_ref_field, _dt_view, _json_body, _lang,
    _llm_view, _sse,
)
from services import events as E
from services.media_files import (
    cleanup_message_media, export_pdf, export_zip, is_video_url,
    media_path_from_url, save_data_uri_images,
)
from services.micro_agent import run_micro_chat
from services.micro_parts import load_parts
from services.pipeline import _now

router = APIRouter(prefix="/api/micro", tags=["micro"])

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


@router.get("")
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


@router.post("")
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


@router.get("/{work_id}")
def micro_work_page(request: Request, work_id: str, db: Session = Depends(get_db)):
    """作品（不含消息）：前端自动选中最近更新的会话。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "作品不存在", "Work not found"))
    return _micro_work_view(db, work)


# 注意：以下两个 /works 路由必须注册在 {session_id} 系列路由之前，
# 否则「works」会被当成 session_id 抢先匹配（FastAPI 按注册顺序匹配）。
@router.get("/{work_id}/works")
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


@router.post("/{work_id}/works/delete")
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
    cleanup_message_media(msgs)
    for m in msgs:
        db.delete(m)
    work.updated_at = _now()
    db.commit()
    return {"ok": True, "deleted": len(msgs)}


@router.get("/{work_id}/works/export")
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
        p = media_path_from_url(m.media_url)
        if p is not None:
            files.append((p, is_video_url(m.media_url)))
    if not files:
        raise HTTPException(status_code=400,
                            detail=L(lang, "没有可导出的媒体", "No media to export"))
    if format == "pdf":
        if any(is_video for _, is_video in files):
            raise HTTPException(status_code=400,
                                detail=L(lang, "PDF 仅支持图片，视频请导出 ZIP",
                                         "PDF supports images only — export videos as ZIP"))
        try:
            path, fname = export_pdf(work.title or work.id, work.id, files)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
        except Exception as e:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"导出 PDF 失败：{e}", f"Export PDF failed: {e}"))
        return FileResponse(path, filename=fname, media_type="application/pdf")
    try:
        path, fname = export_zip(work.title or work.id, work.id, files)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"导出 ZIP 失败：{e}", f"Export ZIP failed: {e}"))
    return FileResponse(path, filename=fname, media_type="application/zip")


@router.post("/{work_id}/settings")
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


@router.post("/{work_id}/sessions")
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


@router.post("/{work_id}/delete")
def micro_work_delete(request: Request, work_id: str, db: Session = Depends(get_db)):
    """删除作品（级联删除全部会话与消息），顺带清理已生成的媒体文件。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "作品不存在", "Work not found"))
    cleanup_message_media(db.query(MicroMessage).join(MicroSession, MicroMessage.session_id == MicroSession.id)
                           .filter(MicroSession.micro_id == work_id).all())
    db.delete(work)  # 级联删除会话与消息
    db.commit()
    return {"ok": True}


@router.get("/{work_id}/{session_id}")
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
                     "images": imgs, "media_url": m.media_url or "", "prompt": m.prompt or "",
                     "parts": load_parts(m.parts)})
    view["session_id"] = session.id
    view["messages"] = msgs
    return view


@router.post("/{work_id}/{session_id}/rename")
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


@router.post("/{work_id}/{session_id}/delete")
def micro_session_delete(request: Request, work_id: str, session_id: str,
                         db: Session = Depends(get_db)):
    """删除作品下的一个会话（含全部消息），顺带清理媒体文件。"""
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "会话不存在", "Session not found"))
    cleanup_message_media(session.messages)
    db.delete(session)  # 级联删除消息
    db.get(MicroWork, work_id).updated_at = _now()
    db.commit()
    return {"ok": True}


# ---------------- 对话（SSE 流式） ----------------
@router.post("/{work_id}/{session_id}/chat")
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
    dt_cfg = cs.get_drawthings(work.drawthings_config_id) if work.drawthings_config_id else None

    # 用户附图：VLM 一律支持图片输入（存 MEDIA_DIR，随消息落库）
    image_urls = []
    image_paths: list[str] = []
    raw_images = body.get("images") or []
    if raw_images:
        image_urls = save_data_uri_images(raw_images)
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
                        work: MicroWork, img_paths: list[str],
                        user_message: str, history: list[dict], assistant_idx: int,
                        lang: str = "zh"):
    """SSE 传输层：驱动 `run_micro_chat` 的事件队列，转成 SSE 帧（含心跳）。

    事件语义见 `services/events.py`。出图/出视频耗时长，15s 无事件发心跳保持连接。
    """
    queue: asyncio.Queue = asyncio.Queue()
    cancel_event = threading.Event()  # 客户端断开（切换会话 / 关闭页面）时协作式停止正在进行的生成
    task = asyncio.create_task(run_micro_chat(
        queue, db=db, session=session, work=work, llm_cfg=llm_cfg, dt_cfg=dt_cfg,
        img_paths=img_paths, user_message=user_message, history=history,
        assistant_idx=assistant_idx, lang=lang, cancel_event=cancel_event))
    try:
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # 心跳：出图/出视频耗时长，保持 SSE 连接不被空闲断开
                continue
            if event == E.EOF:
                break
            yield _sse(event, data)
    finally:
        cancel_event.set()  # 先请求取消（线程内的 Draw Things 生成尽快停止）
        if not task.done():
            task.cancel()
            try:
                await task  # 等引擎清理（落库）完成再结束请求（数据库会话此时才关闭）
            except asyncio.CancelledError:
                pass
