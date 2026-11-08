"""微创作 API 路由（/api/micro/*）：作品 → 多个独立会话 → 消息。

与项目侧 `api_comic / api_drama` 对称：本模块只做 HTTP/SSE 与持久化编排，
会话推理与生成在 `services/micro_agent.py`；共享基础设施取 `services/api_common.py`。
"""

import asyncio
import json
import logging
import uuid
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from config_store import ConfigStore
from db import SessionLocal, get_db
from i18n import L
from models import Asset, GenerationJob, MicroMessage, MicroSession, MicroWork
from services import events as E
from services import jobs, task_manager
from services.api_common import (
    MEDIA_DIR,
    _clamp_page,
    _dt_limit_field,
    _dt_ref_field,
    _dt_steps_field,
    _dt_view,
    _json_body,
    _lang,
    _llm_view,
)
from services.api_tasks import micro_job_snapshot
from services.drawthings import MAX_SIDE_LIMIT, MAX_VIDEO_SECONDS, extract_last_frame
from services.media_files import (
    cleanup_message_media,
    export_pdf,
    export_zip,
    is_video_url,
    media_path_from_url,
    save_data_uri_images,
)
from services.micro_agent import (
    is_video_path,
    llm_score_media,
    norm_score_mode,
    run_micro_chat,
    run_regenerate,
)
from services.micro_parts import dump_parts, load_parts
from services.pipeline import _now
from services.pipeline_common import run_sync

router = APIRouter(prefix="/api/micro", tags=["micro"])

MC_PAGE_SIZE = 10  # 微创作作品列表每页条数


def _new_job(
    db: Session, *, micro_id: str, session_id: str, message_id, kind: str, prompt: str, media: str = ""
) -> GenerationJob:
    """创建生成任务（running）并登记取消句柄。"""
    job = GenerationJob(
        id=uuid.uuid4().hex[:12],
        micro_id=micro_id,
        session_id=session_id,
        message_id=message_id,
        kind=kind,
        status="running",
        media=media,
        prompt=(prompt or "")[:2000],
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(job)
    db.commit()
    jobs.register(job.id)
    return job


def _work_paged(
    db: Session, page: int, size: int = MC_PAGE_SIZE, q: str = "", kind: str = "", sort_desc: bool = True
) -> tuple[int, int, list[MicroWork]]:
    """按条件（标题关键词 / 类型）+ 排序取作品列表，分页：返回 (页码, 总数, 当前页作品)。"""
    query = db.query(MicroWork)
    if q:
        query = query.filter(MicroWork.title.ilike(f"%{q}%"))
    if kind == "gen":
        query = query.filter(MicroWork.drawthings_config_id != "")
    elif kind == "chat":
        query = query.filter(
            or_(MicroWork.drawthings_config_id.is_(None), MicroWork.drawthings_config_id == "")
        )
    total = query.count()
    total_pages = max((total + size - 1) // size, 1)
    page = min(max(page, 1), total_pages)  # 越界页码收敛到有效范围
    rows = (
        query.order_by(MicroWork.updated_at.desc() if sort_desc else MicroWork.updated_at.asc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return page, total, rows


def _session_counts(db: Session, session_ids: list[str]) -> dict:
    """各会话的消息数 {session_id: n}。"""
    if not session_ids:
        return {}
    return dict(
        db.query(MicroSession.id, func.count(MicroMessage.id))
        .join(MicroMessage, MicroMessage.session_id == MicroSession.id)
        .filter(MicroSession.id.in_(session_ids))
        .group_by(MicroSession.id)
        .all()
    )


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
            "id": work.id,
            "title": work.title,
            "llm_config_id": work.llm_config_id,
            "drawthings_config_id": work.drawthings_config_id,
            "dt_model_image": work.dt_model_image or "",
            "dt_model_video": work.dt_model_video or "",
            "dt_ref_image": work.dt_ref_image or "",
            "dt_ref_video": work.dt_ref_video or "",
            "dt_max_steps_image": work.dt_max_steps_image or 0,
            "dt_max_steps_video": work.dt_max_steps_video or 0,
            "dt_max_side": work.dt_max_side or 0,
            "dt_max_seconds": work.dt_max_seconds or 0,
            "score_mode": work.score_mode or "image",
            "auto_score": int(work.auto_score or 0),
            "created_at": work.created_at,
            "llm_name": llm_cfg.name if llm_cfg else "",
            "dt_name": dt_cfg.name if dt_cfg else "",
            "vision": bool(llm_cfg) and str(getattr(llm_cfg, "supports_vision", "") or "").lower() == "yes",
        },
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "msg_count": counts.get(s.id, 0),
            }
            for s in work.sessions
        ],
        "llm_configs": [_llm_view(c) for c in cs.list_llm()],
        "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()],
    }


@router.get("")
def micro_works(
    db: Session = Depends(get_db),
    page: int = 1,
    size: int = MC_PAGE_SIZE,
    q: str = "",
    kind: str = "",
    sort: str = "desc",
):
    """作品列表：标题关键词 / 类型（生成 / 纯对话）/ 排序筛选 + 分页 + 新建作品可选配置。"""
    page, size = _clamp_page(page, size)
    page, total, works = _work_paged(db, page, size, q.strip(), kind, sort != "asc")
    counts = dict(
        db.query(MicroSession.micro_id, func.count(MicroSession.id))
        .filter(MicroSession.micro_id.in_([w.id for w in works] or [""]))
        .group_by(MicroSession.micro_id)
        .all()
    )
    # 作品集预览：每作品最近 3 条生成媒体（列表页卡片缩略图）
    wid_list = [w.id for w in works]
    previews: dict[str, list[str]] = {wid: [] for wid in wid_list}
    if wid_list:
        rows = (
            db.query(MicroSession.micro_id, MicroMessage.media_url, MicroMessage.id)
            .join(MicroMessage, MicroMessage.session_id == MicroSession.id)
            .filter(
                MicroSession.micro_id.in_(wid_list),
                MicroMessage.media_url.isnot(None),
                MicroMessage.media_url != "",
            )
            .order_by(MicroMessage.id.desc())
            .all()
        )
        for mid, url, _ in rows:
            lst = previews.get(mid)
            if lst is not None and len(lst) < 3:
                lst.append(url)
    cs = ConfigStore(db)
    return {
        "works": [
            {
                "id": w.id,
                "title": w.title,
                "llm_config_id": w.llm_config_id,
                "drawthings_config_id": w.drawthings_config_id,
                "updated_at": w.updated_at,
                "session_count": counts.get(w.id, 0),
                "media_preview": previews.get(w.id, []),
            }
            for w in works
        ],
        "total": total,
        "page": page,
        "size": size,
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
        raise HTTPException(status_code=400, detail=L(lang, "名称不能为空", "Name is required"))
    cs = ConfigStore(db)
    llm_config_id = str(body.get("llm_config_id") or "")
    if not cs.get_llm(llm_config_id):
        raise HTTPException(
            status_code=400, detail=L(lang, "请选择有效的 LLM 配置", "Please select a valid LLM config")
        )
    w = MicroWork(
        id=uuid.uuid4().hex[:12],
        title=title[:200],
        llm_config_id=llm_config_id,
        drawthings_config_id=str(body.get("drawthings_config_id") or "").strip(),
        dt_model_image=str(body.get("dt_model_image") or "").strip()[:200],
        dt_model_video=str(body.get("dt_model_video") or "").strip()[:200],
        dt_ref_image=_dt_ref_field(body, "dt_ref_image"),
        dt_ref_video=_dt_ref_field(body, "dt_ref_video"),
        dt_max_steps_image=_dt_steps_field(body, "dt_max_steps_image", lang),
        dt_max_steps_video=_dt_steps_field(body, "dt_max_steps_video", lang),
        dt_max_side=_dt_limit_field(body, "dt_max_side", MAX_SIDE_LIMIT, lang),
        dt_max_seconds=_dt_limit_field(body, "dt_max_seconds", MAX_VIDEO_SECONDS, lang),
        score_mode=norm_score_mode(body.get("score_mode")),
        auto_score=(1 if body.get("auto_score") else 0),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(w)
    db.flush()  # 先落库作品，会话挂在作品下
    db.add(
        MicroSession(id=uuid.uuid4().hex[:12], micro_id=w.id, title="", created_at=_now(), updated_at=_now())
    )
    db.commit()
    return {"id": w.id}


@router.get("/{work_id}")
def micro_work_page(request: Request, work_id: str, db: Session = Depends(get_db)):
    """作品（不含消息）：前端自动选中最近更新的会话。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(_lang(request), "作品不存在", "Work not found"))
    return _micro_work_view(db, work)


# 注意：以下两个 /works 路由必须注册在 {session_id} 系列路由之前，
# 否则「works」会被当成 session_id 抢先匹配（FastAPI 按注册顺序匹配）。
@router.get("/{work_id}/works")
def micro_works_gallery(request: Request, work_id: str, db: Session = Depends(get_db)):
    """作品（= 该作品下所有会话里助手生成的媒体：图/视频）。按生成时间倒序。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(_lang(request), "作品不存在", "Work not found"))
    sess_title = {s.id: s.title for s in work.sessions}
    msgs = (
        db.query(MicroMessage)
        .join(MicroSession, MicroMessage.session_id == MicroSession.id)
        .filter(
            MicroSession.micro_id == work_id,
            MicroMessage.role == "assistant",
            MicroMessage.media_url.isnot(None),
            MicroMessage.media_url != "",
        )
        .order_by(MicroMessage.created_at.desc(), MicroMessage.id.desc())
        .all()
    )
    # 附加资产元数据（Asset Graph）：模型 / 尺寸 / 时长 / 参考图（旧数据可能没有）
    meta: dict = {}
    mids = [m.id for m in msgs]
    if mids:
        for a in db.query(Asset).filter(Asset.message_id.in_(mids)).order_by(Asset.id.asc()).all():
            meta[(a.message_id, a.url)] = a
    items = []
    for m in msgs:
        a = meta.get((m.id, m.media_url))
        items.append(
            {
                "id": m.id,
                "session_id": m.session_id,
                "session_title": sess_title.get(m.session_id, ""),
                "media_url": m.media_url,
                "prompt": m.prompt or "",
                "content": m.content or "",
                "created_at": m.created_at or "",
                "asset_id": a.id if a else None,
                "kind": (a.kind if a else ("video" if is_video_url(m.media_url) else "image")),
                "model": (a.model or "") if a else "",
                "width": (a.width or 0) if a else 0,
                "height": (a.height or 0) if a else 0,
                "seconds": (a.seconds or 0) if a else 0,
                "ref_url": (a.ref_url or "") if a else "",
            }
        )
    return {"works": items, "total": len(items)}


@router.get("/{work_id}/assets")
def micro_assets(
    request: Request, work_id: str, kind: str = "", session_id: str = "", db: Session = Depends(get_db)
):
    """资产图（Asset Graph）：该作品的全部生成资产（图/视频），可按类型 / 会话过滤。

    比「作品画廊」粒度更细（同一消息内多次生成 = 多条资产），并带参数快照，
    供跨轮/跨会话复用为参考图、按资产维度导出或统计。
    """
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(_lang(request), "作品不存在", "Work not found"))
    q = db.query(Asset).filter(Asset.micro_id == work_id)
    if kind in ("image", "video"):
        q = q.filter(Asset.kind == kind)
    if session_id:
        q = q.filter(Asset.session_id == session_id)
    assets = q.order_by(Asset.id.desc()).all()
    return {
        "assets": [
            {
                "id": a.id,
                "session_id": a.session_id,
                "message_id": a.message_id,
                "kind": a.kind or "image",
                "url": a.url or "",
                "prompt": a.prompt or "",
                "model": a.model or "",
                "width": a.width or 0,
                "height": a.height or 0,
                "seconds": a.seconds or 0,
                "ref_url": a.ref_url or "",
                "created_at": a.created_at or "",
            }
            for a in assets
        ],
        "total": len(assets),
    }


@router.get("/{work_id}/jobs")
def micro_jobs(
    request: Request, work_id: str, active: int = 0, limit: int = 20, db: Session = Depends(get_db)
):
    """生成任务列表（Job）：可按 active=1 只看运行中；用于可观测与「停止生成」。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(_lang(request), "作品不存在", "Work not found"))
    q = db.query(GenerationJob).filter(GenerationJob.micro_id == work_id)
    if active:
        q = q.filter(GenerationJob.status == "running")
    jobs = q.order_by(GenerationJob.created_at.desc()).limit(min(max(limit, 1), 100)).all()
    return {
        "jobs": [
            {
                "id": j.id,
                "session_id": j.session_id,
                "message_id": j.message_id,
                "kind": j.kind,
                "status": j.status,
                "media": j.media or "",
                "prompt": j.prompt or "",
                "note": j.note or "",
                "error": j.error or "",
                "created_at": j.created_at or "",
                "updated_at": j.updated_at or "",
                "finished_at": j.finished_at or "",
            }
            for j in jobs
        ],
        "total": len(jobs),
    }


@router.post("/{work_id}/jobs/{job_id}/cancel")
def micro_job_cancel(request: Request, work_id: str, job_id: str, db: Session = Depends(get_db)):
    """取消运行中的生成任务：置位其取消句柄（引擎协作式停止；不依赖客户端连接）。"""
    lang = _lang(request)
    job = db.get(GenerationJob, job_id)
    if not job or job.micro_id != work_id:
        raise HTTPException(status_code=404, detail=L(lang, "任务不存在", "Job not found"))
    if job.status != "running":
        return {"ok": True, "status": job.status}
    if jobs.cancel(job_id):
        return {"ok": True, "status": "cancelling"}
    # 无运行句柄（进程重启后的残留 running）：直接标记为已取消
    job.status = "cancelled"
    job.updated_at = _now()
    job.finished_at = _now()
    db.commit()
    task_manager.publish_job(micro_job_snapshot(job, db.get(MicroWork, job.micro_id)))
    return {"ok": True, "status": "cancelled"}


@router.post("/{work_id}/works/delete")
async def micro_works_delete(request: Request, work_id: str, db: Session = Depends(get_db)):
    """批量删除作品（= 对应的助手媒体消息 + 媒体文件），可跨该作品多个会话。"""
    lang = _lang(request)
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(lang, "作品不存在", "Work not found"))
    body = await _json_body(request)
    ids = []
    for x in body.get("ids") or []:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(
            status_code=400, detail=L(lang, "请选择要删除的作品", "Please select works to delete")
        )
    msgs = (
        db.query(MicroMessage)
        .join(MicroSession, MicroMessage.session_id == MicroSession.id)
        .filter(MicroSession.micro_id == work_id, MicroMessage.id.in_(ids))
        .all()
    )
    if not msgs:
        raise HTTPException(status_code=404, detail=L(lang, "作品不存在", "Work not found"))
    cleanup_message_media(msgs)
    for m in msgs:
        db.delete(m)
    work.updated_at = _now()
    db.commit()
    return {"ok": True, "deleted": len(msgs)}


@router.get("/{work_id}/works/export")
def micro_works_export(
    request: Request, work_id: str, ids: str = "", format: str = "zip", db: Session = Depends(get_db)
):
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
        raise HTTPException(
            status_code=400, detail=L(lang, "请选择要导出的作品", "Please select works to export")
        )
    msgs = (
        db.query(MicroMessage)
        .join(MicroSession, MicroMessage.session_id == MicroSession.id)
        .filter(MicroSession.micro_id == work_id, MicroMessage.id.in_(id_list))
        .all()
    )
    by_id = {m.id: m for m in msgs}
    files: list[tuple[Path, bool]] = []
    for mid in id_list:  # 保持前端选择顺序
        m = by_id.get(mid)
        if not m:
            continue
        p = media_path_from_url(m.media_url)
        if p is not None:
            files.append((p, is_video_url(m.media_url)))
    if not files:
        raise HTTPException(status_code=400, detail=L(lang, "没有可导出的媒体", "No media to export"))
    if format == "pdf":
        if any(is_video for _, is_video in files):
            raise HTTPException(
                status_code=400,
                detail=L(
                    lang, "PDF 仅支持图片，视频请导出 ZIP", "PDF supports images only — export videos as ZIP"
                ),
            )
        try:
            path, fname = export_pdf(work.title or work.id, work.id, files)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=L(lang, f"导出 PDF 失败：{e}", f"Export PDF failed: {e}")
            )
        return FileResponse(path, filename=fname, media_type="application/pdf")
    try:
        path, fname = export_zip(work.title or work.id, work.id, files)
    except Exception as e:
        raise HTTPException(status_code=400, detail=L(lang, f"导出 ZIP 失败：{e}", f"Export ZIP failed: {e}"))
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
        raise HTTPException(
            status_code=400, detail=L(lang, "请选择有效的 LLM 配置", "Please select a valid LLM config")
        )
    work.title = str(body.get("title") or "").strip()[:200]
    work.llm_config_id = llm_config_id
    work.drawthings_config_id = str(body.get("drawthings_config_id") or "").strip()
    work.dt_model_image = str(body.get("dt_model_image") or "").strip()[:200]
    work.dt_model_video = str(body.get("dt_model_video") or "").strip()[:200]
    work.dt_ref_image = _dt_ref_field(body, "dt_ref_image")
    work.dt_ref_video = _dt_ref_field(body, "dt_ref_video")
    work.dt_max_steps_image = _dt_steps_field(body, "dt_max_steps_image", lang)
    work.dt_max_steps_video = _dt_steps_field(body, "dt_max_steps_video", lang)
    work.dt_max_side = _dt_limit_field(body, "dt_max_side", MAX_SIDE_LIMIT, lang)
    work.dt_max_seconds = _dt_limit_field(body, "dt_max_seconds", MAX_VIDEO_SECONDS, lang)
    work.score_mode = norm_score_mode(body.get("score_mode"))
    work.auto_score = 1 if body.get("auto_score") else 0
    work.updated_at = _now()
    db.commit()
    return {"ok": True}


@router.post("/{work_id}/sessions")
async def micro_session_create(request: Request, work_id: str, db: Session = Depends(get_db)):
    """在作品下新建一个独立会话。"""
    body = await _json_body(request)
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(_lang(request), "作品不存在", "Work not found"))
    s = MicroSession(
        id=uuid.uuid4().hex[:12],
        micro_id=work_id,
        title=str(body.get("title") or "").strip()[:200],
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(s)
    work.updated_at = _now()
    db.commit()
    return {"id": s.id}


@router.post("/{work_id}/delete")
def micro_work_delete(request: Request, work_id: str, db: Session = Depends(get_db)):
    """删除作品（级联删除全部会话与消息），顺带清理已生成的媒体文件。"""
    work = db.get(MicroWork, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=L(_lang(request), "作品不存在", "Work not found"))
    cleanup_message_media(
        db.query(MicroMessage)
        .join(MicroSession, MicroMessage.session_id == MicroSession.id)
        .filter(MicroSession.micro_id == work_id)
        .all()
    )
    db.delete(work)  # 级联删除会话与消息
    db.commit()
    return {"ok": True}


@router.get("/{work_id}/{session_id}")
def micro_session_page(request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)):
    """作品 + 选中会话的消息历史。"""
    work = db.get(MicroWork, work_id)
    session = db.get(MicroSession, session_id)
    if not work or not session or session.micro_id != work_id:
        raise HTTPException(status_code=404, detail=L(_lang(request), "会话不存在", "Session not found"))
    view = _micro_work_view(db, work)
    msgs = []
    for m in session.messages:
        imgs = []
        if m.images:
            try:
                imgs = [str(u) for u in json.loads(m.images) if u]
            except (ValueError, TypeError):
                pass
        msgs.append(
            {
                "index": m.index,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at or "",
                "duration": m.duration or 0,
                "images": imgs,
                "media_url": m.media_url or "",
                "prompt": m.prompt or "",
                "status": m.status or "done",
                "parts": load_parts(m.parts),
            }
        )
    view["session_id"] = session.id
    view["messages"] = msgs
    return view


@router.post("/{work_id}/{session_id}/rename")
async def micro_session_rename(
    request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)
):
    """重命名会话。"""
    body = await _json_body(request)
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404, detail=L(_lang(request), "会话不存在", "Session not found"))
    session.title = str(body.get("title") or "").strip()[:200]
    session.updated_at = _now()
    db.get(MicroWork, work_id).updated_at = _now()
    db.commit()
    return {"ok": True}


@router.post("/{work_id}/{session_id}/delete")
def micro_session_delete(request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)):
    """删除作品下的一个会话（含全部消息），顺带清理媒体文件。"""
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404, detail=L(_lang(request), "会话不存在", "Session not found"))
    cleanup_message_media(session.messages)
    db.delete(session)  # 级联删除消息
    db.get(MicroWork, work_id).updated_at = _now()
    db.commit()
    return {"ok": True}


# ---------------- 对话 / 重跑 / 评分（后台任务 + WS 事件） ----------------
@router.post("/{work_id}/{session_id}/chat")
async def micro_chat(request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)):
    """微创作对话 —— 提交后台任务，立即返回 {job_id}；文本/生成事件经 /ws 推送。

    用户消息（可附图，仅视觉模型）先落库；需要出图/出视频时由模型 function call
    自动调用 DrawThings；对话结束后助手消息（文本 + 生成媒体）落库。历史取自数据库（最近 20 条）。
    任务脱离 HTTP 连接运行：切页 / 刷新 / 断网都不会中断本轮生成。
    """
    lang = _lang(request)
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404, detail=L(lang, "会话不存在", "Session not found"))
    work = session.work
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=L(lang, "请求体需为 JSON", "Body must be JSON"))
    message = str(body.get("message") or "").strip()
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(work.llm_config_id or "")

    # 用户附图：需作品所选 LLM 勾选「支持视觉」（存 MEDIA_DIR，随消息落库；未勾选则拒绝）
    image_urls = []
    image_paths: list[str] = []
    raw_images = body.get("images") or []
    if raw_images and llm_cfg and str(getattr(llm_cfg, "supports_vision", "") or "").lower() != "yes":
        raise HTTPException(
            status_code=400,
            detail=L(
                lang,
                "当前 LLM 配置未勾选「支持视觉」，无法附带图片",
                "The selected LLM config has no vision support — image attachments are disabled",
            ),
        )
    if raw_images:
        image_urls = save_data_uri_images(raw_images)
        image_paths = [str(MEDIA_DIR / u.rsplit("/", 1)[-1].split("?")[0]) for u in image_urls]
    if not message and not image_urls:
        raise HTTPException(status_code=400, detail=L(lang, "消息不能为空", "Message cannot be empty"))

    # 右键「引用」的媒体（图/视频均可）：本轮生成的最高优先级参考
    quoted_ref = ""
    ref_url = str(body.get("ref_url") or "").strip()
    if ref_url:
        p = media_path_from_url(ref_url)
        quoted_ref = str(p) if p else ""

    # 用户消息先落库；空标题会话（及空标题作品）用首条用户消息命名
    last = db.query(MicroMessage).filter_by(session_id=session_id).order_by(MicroMessage.index.desc()).first()
    user_idx = (last.index + 1) if last else 0
    db.add(
        MicroMessage(
            session_id=session_id,
            index=user_idx,
            role="user",
            content=message,
            created_at=_now(),
            images=json.dumps(image_urls, ensure_ascii=False) if image_urls else None,
        )
    )
    # 助手「草稿」先落库（可恢复流）：生成过程中增量写入，断连/崩溃也保留部分输出
    draft = MicroMessage(
        session_id=session_id,
        index=user_idx + 1,
        role="assistant",
        content="",
        created_at=_now(),
        status="streaming",
    )
    db.add(draft)
    if not session.title.strip():
        session.title = message[:50]
        if not work.title.strip():
            work.title = message[:50]
    session.updated_at = _now()
    work.updated_at = _now()
    db.commit()
    assistant_id = draft.id
    job = _new_job(
        db, micro_id=work_id, session_id=session_id, message_id=assistant_id, kind="chat", prompt=message
    )
    task_manager.spawn(
        job.id,
        _run_micro_chat_job(
            job.id,
            work_id,
            session_id,
            assistant_id,
            user_idx,
            message,
            image_paths,
            quoted_ref,
            lang,
        ),
    )
    return {"job_id": job.id, "session_id": session_id, "assistant_id": assistant_id, "user_index": user_idx}


def _history_for(db: Session, session_id: str, before_index: int, limit: int = 20) -> list[dict]:
    """取某条消息之前最近 N 条历史（附图转回磁盘路径，供多模态入图）。"""
    rows = (
        db.query(MicroMessage)
        .filter(MicroMessage.session_id == session_id, MicroMessage.index < before_index)
        .order_by(MicroMessage.index.desc())
        .limit(limit)
        .all()
    )
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
    return history


def _make_job_updater(db: Session, job: GenerationJob, work: MicroWork):
    """任务状态更新器：写库 + 广播快照（任务中心实时刷新）。"""

    def _job(status=None, note=None, error=None, media=None, finished=False):
        try:
            if status:
                job.status = status
            if note is not None:
                job.note = str(note)[:500]
            if error is not None:
                job.error = str(error)[:2000]
            if media:
                job.media = str(media)[:10]
            job.updated_at = _now()
            if finished:
                job.finished_at = _now()
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        task_manager.publish_job(micro_job_snapshot(job, work))

    return _job


def _finish_job(_job, control, sink) -> None:
    """结束任务：取消优先，其次错误，否则完成。"""
    if control.cancelled:
        _job(status="cancelled", note="已取消", finished=True)
    elif sink is not None and sink.error:
        _job(status="error", error=sink.error, finished=True)
    else:
        _job(status="done", note="完成", finished=True)


async def _run_micro_chat_job(
    job_id: str,
    work_id: str,
    session_id: str,
    assistant_id: int,
    user_idx: int,
    message: str,
    image_paths: list[str],
    quoted_ref: str,
    lang: str,
) -> None:
    """后台执行一轮微创作对话（独立会话；事件经 WS 广播）。"""
    db = SessionLocal()
    control = jobs.get(job_id)
    job = db.get(GenerationJob, job_id)
    session = db.get(MicroSession, session_id)
    work = db.get(MicroWork, work_id)
    if job is None or control is None or session is None or work is None:
        jobs.pop(job_id)
        db.close()
        return
    _job = _make_job_updater(db, job, work)
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(work.llm_config_id or "")
    dt_cfg = cs.get_drawthing(work.drawthings_config_id) if work.drawthings_config_id else None

    def _on_event(event, data) -> None:
        d = data or {}
        if event == E.TOOL:
            _job(note=d.get("label", ""), media=d.get("media", ""))
        elif event == E.TOOL_STATUS:
            _job(note=d.get("message", ""))
        elif event == E.MEDIA:
            _job(note="已生成", media=d.get("media", ""))

    sink = task_manager.EventSink(job_id, on_event=_on_event)
    try:
        await run_micro_chat(
            sink,
            db=db,
            session=session,
            work=work,
            llm_cfg=llm_cfg,
            dt_cfg=dt_cfg,
            img_paths=image_paths,
            user_message=message,
            history=_history_for(db, session_id, user_idx),
            assistant_idx=user_idx + 1,
            lang=lang,
            cancel_event=control.event,
            assistant_id=assistant_id,
            quoted_ref=quoted_ref,
        )
        _finish_job(_job, control, sink)
        task_manager.publish(job_id, E.DONE, {"cancelled": control.cancelled})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _job(status="error", error=str(e), finished=True)
        task_manager.publish(job_id, E.ERROR, {"message": L(lang, f"对话失败：{e}", f"Chat failed: {e}")})
    finally:
        jobs.pop(job_id)
        db.close()


@router.post("/{work_id}/{session_id}/regenerate")
async def micro_regenerate(request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)):
    """一键重跑 —— 提交后台任务，立即返回 {job_id}。

    跳过 LLM，按内容块保存的参数直接重生成（可复现）。
    body: {prompt, media, width, height, seconds, ref_url}（来自 tool 内容块的参数快照）。
    """
    lang = _lang(request)
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404, detail=L(lang, "会话不存在", "Session not found"))
    work = session.work
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=L(lang, "请求体需为 JSON", "Body must be JSON"))
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(
            status_code=400, detail=L(lang, "缺少提示词，无法重跑", "Missing prompt — cannot re-generate")
        )
    kind = str(body.get("media") or "image").strip().lower()
    if kind not in ("image", "video"):
        kind = "image"

    def _int(v) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    width, height, seconds = _int(body.get("width")), _int(body.get("height")), _int(body.get("seconds"))
    ref_url = str(body.get("ref_url") or "")
    improve = bool(body.get("improve"))
    score = _int(body.get("score"))
    note = str(body.get("note") or "")

    last = db.query(MicroMessage).filter_by(session_id=session_id).order_by(MicroMessage.index.desc()).first()
    draft = MicroMessage(
        session_id=session_id,
        index=(last.index + 1) if last else 0,
        role="assistant",
        content="",
        created_at=_now(),
        status="streaming",
    )
    db.add(draft)
    session.updated_at = _now()
    work.updated_at = _now()
    db.commit()
    assistant_id = draft.id
    job = _new_job(
        db,
        micro_id=work_id,
        session_id=session_id,
        message_id=assistant_id,
        kind="regenerate",
        prompt=prompt,
        media=kind,
    )
    task_manager.spawn(
        job.id,
        _run_micro_regenerate_job(
            job.id,
            work_id,
            session_id,
            assistant_id,
            kind,
            prompt,
            width,
            height,
            seconds,
            ref_url,
            improve,
            score,
            note,
            lang,
        ),
    )
    return {"job_id": job.id, "session_id": session_id, "assistant_id": assistant_id}


async def _run_micro_regenerate_job(
    job_id: str,
    work_id: str,
    session_id: str,
    assistant_id: int,
    kind: str,
    prompt: str,
    width: int,
    height: int,
    seconds: int,
    ref_url: str,
    improve: bool,
    score: int,
    note: str,
    lang: str,
) -> None:
    """后台执行一次重跑（跳过 LLM，按参数直接生成）。"""
    db = SessionLocal()
    control = jobs.get(job_id)
    job = db.get(GenerationJob, job_id)
    session = db.get(MicroSession, session_id)
    work = db.get(MicroWork, work_id)
    if job is None or control is None or session is None or work is None:
        jobs.pop(job_id)
        db.close()
        return
    _job = _make_job_updater(db, job, work)
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(work.llm_config_id or "")
    dt_cfg = cs.get_drawthing(work.drawthings_config_id) if work.drawthings_config_id else None

    def _on_event(event, data) -> None:
        d = data or {}
        if event == E.TOOL:
            _job(note=d.get("label", ""), media=d.get("media", ""))
        elif event == E.TOOL_STATUS:
            _job(note=d.get("message", ""))
        elif event == E.MEDIA:
            _job(note="已生成", media=d.get("media", ""))

    sink = task_manager.EventSink(job_id, on_event=_on_event)
    try:
        await run_regenerate(
            sink,
            db=db,
            session=session,
            work=work,
            dt_cfg=dt_cfg,
            assistant_id=assistant_id,
            kind=kind,
            prompt=prompt,
            width=width,
            height=height,
            seconds=seconds,
            ref_url=ref_url,
            lang=lang,
            cancel_event=control.event,
            llm_cfg=llm_cfg,
            improve=improve,
            score=score,
            note=note,
        )
        _finish_job(_job, control, sink)
        task_manager.publish(job_id, E.DONE, {"cancelled": control.cancelled})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _job(status="error", error=str(e), finished=True)
        task_manager.publish(
            job_id, E.ERROR, {"message": L(lang, f"重跑失败：{e}", f"Re-generate failed: {e}")}
        )
    finally:
        jobs.pop(job_id)
        db.close()


@router.post("/{work_id}/{session_id}/score")
async def micro_score(request: Request, work_id: str, session_id: str, db: Session = Depends(get_db)):
    """LLM 评分 —— 提交后台任务，立即返回 {job_id}；结果经 media_score 事件推送并写回内容块。"""
    lang = _lang(request)
    session = db.get(MicroSession, session_id)
    if not session or session.micro_id != work_id:
        raise HTTPException(status_code=404, detail=L(lang, "会话不存在", "Session not found"))
    work = session.work
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=L(lang, "请求体需为 JSON", "Body must be JSON"))
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail=L(lang, "缺少媒体地址", "Missing media URL"))
    kind = str(body.get("media") or "image").strip().lower()
    if not ConfigStore(db).get_llm(work.llm_config_id or ""):
        raise HTTPException(
            status_code=400, detail=L(lang, "请选择有效的 LLM 配置", "Please select a valid LLM config")
        )
    if media_path_from_url(url) is None:
        raise HTTPException(status_code=400, detail=L(lang, "媒体不存在", "Media not found"))
    job = _new_job(
        db,
        micro_id=work_id,
        session_id=session_id,
        message_id=None,
        kind="score",
        prompt=str(body.get("prompt") or "")[:2000],
        media=kind,
    )
    task_manager.spawn(
        job.id,
        _run_micro_score_job(job.id, work_id, session_id, url, kind, str(body.get("prompt") or ""), lang),
    )
    return {"job_id": job.id, "url": url}


async def _run_micro_score_job(
    job_id: str, work_id: str, session_id: str, url: str, kind: str, prompt: str, lang: str
) -> None:
    """后台评分：抽末帧（视频）→ LLM 打分 → 写回内容块 → 广播 media_score。"""
    db = SessionLocal()
    control = jobs.get(job_id)
    job = db.get(GenerationJob, job_id)
    work = db.get(MicroWork, work_id)
    if job is None or control is None or work is None:
        jobs.pop(job_id)
        db.close()
        return
    _job = _make_job_updater(db, job, work)
    try:
        llm_cfg = ConfigStore(db).get_llm(work.llm_config_id or "")
        p = media_path_from_url(url)
        if p is None or llm_cfg is None:
            raise ValueError(L(lang, "媒体或 LLM 配置已失效", "Media or LLM config no longer available"))
        img = str(p)
        if kind == "video" or is_video_path(img):
            # 抽帧要跑 OpenCV 解码 / ffmpeg（自身最长 60s 超时），放线程池，避免阻塞事件循环
            frame = await run_sync(partial(extract_last_frame, img))
            if not frame:
                raise ValueError(
                    L(
                        lang,
                        "无法提取视频末帧用于评分（需安装 ffmpeg）",
                        "Cannot extract a video frame to score (ffmpeg required)",
                    )
                )
            img = frame
        score, note = await llm_score_media(
            llm_cfg, img, lang, mode=getattr(work, "score_mode", "image"), prompt=prompt
        )
        # 写回该会话内对应内容块（按媒体 url 匹配）
        changed = False
        for m in db.query(MicroMessage).filter(MicroMessage.session_id == session_id).all():
            if not m.parts:
                continue
            blocks = load_parts(m.parts)
            hit = False
            for b in blocks:
                if b.get("type") == "tool" and b.get("url") == url:
                    b["score"] = score
                    b["score_note"] = note
                    hit = True
            if hit:
                m.parts = dump_parts(blocks)
                changed = True
        if changed:
            db.commit()
        _job(status="done", note=f"{score}", finished=True)
        task_manager.publish(job_id, E.MEDIA_SCORE, {"url": url, "media": kind, "score": score, "note": note})
        task_manager.publish(job_id, E.DONE, {"score": score, "note": note})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logging.getLogger("drawthings").warning("微创作评分失败：%s", e, exc_info=True)
        _job(status="error", error=str(e), finished=True)
        task_manager.publish(job_id, E.ERROR, {"message": L(lang, f"评分失败：{e}", f"Scoring failed: {e}")})
    finally:
        jobs.pop(job_id)
        db.close()
