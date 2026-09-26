"""漫画项目 API 路由（/api/comics/*）：仅漫画走向。

- 只调用漫画流水线（pipeline.comic），无 kind 分支
- 与另一类型完全独立：services/api_drama.py
- 设计约定：漫画 / 短剧刻意分成两条重复的独立线（便于分开开发维护），请勿合并 / 勿抽共享基座（见 AGENTS.md）"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from db import get_db
from i18n import L
from models import Chapter, Project, Season
from config_store import ConfigStore
from services.drawthings import norm_ref_flag
from services.pipeline import _now, chars_from_raw
from services.runtime import pipeline
from services import events as E
from services.api_common import (
    MEDIA_DIR, MAX_IMAGE_UPLOAD, _overlay_opts, _lang, _json_body, _media_url,
    _chapter_view, _dt_ref_field, _project_view, _config_lists, _clamp_page,
    _ensure_not_finished, _sse,
)

router = APIRouter(prefix="/api/comics", tags=["comic"])

def _comic_project(db: Session, project_id: str, lang: str) -> Project:
    """取漫画项目：不存在或类型不符（跨命名空间访问）→ 404。"""
    project = pipeline.comic.get(db, project_id)
    if project is None or (project.kind or "") != "comic":
        raise HTTPException(status_code=404,
                            detail=L(lang, "项目不存在", "Project not found"))
    return project


@router.post("")
async def comic_create(request: Request, db: Session = Depends(get_db)):
    """新建漫画创作：标题 + 主题（一句话）→ 漫画流水线。"""
    lang = _lang(request)
    body = await _json_body(request)
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
    # 功能级模型：项目侧自选；留空 = 跟随 DrawThings 配置里的模型（两边都空才拒绝）
    dt_model_image = str(body.get("dt_model_image") or "").strip()[:200]
    # 功能级参考图开关：缺省 = 跟随配置；显式 0/1 = 覆盖
    dt_ref_image = norm_ref_flag(body.get("dt_ref_image"))
    if not dt_model_image and not (dt_cfg.model_image or ""):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择出图模型（DrawThings 配置里也未设置模型）",
                                     "Please pick an image model (none set in the Draw Things config)"))
    project = pipeline.comic.create(db, "comic", origin, llm_cfg.id, dt_cfg.id,
                                    style=style, title=str(body.get("title") or "").strip()[:200])
    if dt_model_image:
        project.dt_model_image = dt_model_image
    if dt_ref_image is not None:
        project.dt_ref_image = "1" if dt_ref_image else "0"
    if dt_model_image or dt_ref_image is not None:
        db.commit()
    return {"id": project.id}


@router.get("")
def projects_list(db: Session = Depends(get_db), page: int = 1, size: int = 10,
                  status: str = "", q: str = "", sort: str = "desc"):
    """创作列表：类型/状态/关键词筛选 + 排序（创建时间 / 最近活跃）+ 分页。"""
    page, size = _clamp_page(page, size)
    rows, total = pipeline.comic.list_projects(db, "comic", status or None, q.strip(), sort,
                                         size, (page - 1) * size)
    counts = dict(db.query(Chapter.project_id, func.count(Chapter.id))
                  .group_by(Chapter.project_id).all())
    return {
        "projects": [_project_view(p, counts.get(p.id, 0)) for p in rows],
        "total": total, "page": page, "size": size,
        "total_pages": (total + size - 1) // size if total else 1,
    }




@router.post("/{project_id}/rename")
async def project_rename(request: Request, project_id: str, db: Session = Depends(get_db)):
    """重命名创作（修改标题）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
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


@router.post("/{project_id}/reset")
async def project_reset(request: Request, project_id: str, db: Session = Depends(get_db)):
    """重新设定：修改标题 / 一句话创意（主题）/ 风格。
    默认不清空下游（保留大纲/章节/已生成媒体）；clear_downstream=true 时清空大纲/章节/媒体并回到 planning。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        pipeline.comic.reset_settings(db, project,
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


@router.post("/{project_id}/delete")
def project_delete(request: Request, project_id: str, db: Session = Depends(get_db)):
    """删除创作（含全部章节与媒体文件）。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if not project:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "项目不存在", "Project not found"))
    pipeline.comic.delete_project(db, project)
    return {"ok": True}


@router.post("/{project_id}/complete")
def project_complete(request: Request, project_id: str, db: Session = Depends(get_db)):
    """完结整部作品：要求全部季的章节都已完成（done），完结后作品锁定（只读），需解锁才能继续操作。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)  # 已完结 → 防重复
    try:
        pipeline.comic.complete_project(db, project, lang=lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    return {"ok": True, "status": project.status}


@router.post("/{project_id}/unlock")
def project_unlock(request: Request, project_id: str, db: Session = Depends(get_db)):
    """解锁已完结（锁定）的作品：回到「章节已定」状态，可继续编辑 / 生成。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    if not pipeline.comic.is_finished(project):
        raise HTTPException(status_code=400,
                            detail=L(lang, "该作品未处于完结（锁定）状态，无需解锁",
                                     "This project is not finished/locked — nothing to unlock"))
    pipeline.comic.unlock_project(db, project, lang=lang)
    return {"ok": True, "status": project.status}


@router.get("/{project_id}")
def project_view(request: Request, project_id: str, db: Session = Depends(get_db)):
    """项目详情：状态/篇幅/总纲/首图 + 章节 + 可选配置。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    pipeline.comic.ensure_first_season(db, project)  # 保证存在第一季（旧项目懒迁移：自动建第一季并入孤儿章节）
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
            "dt_model_image": project.dt_model_image or "",
            "dt_model_video": project.dt_model_video or "",
            "dt_ref_image": project.dt_ref_image or "",
            "dt_ref_video": project.dt_ref_video or "",
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


@router.post("/{project_id}/config")
async def project_config_update(request: Request, project_id: str, db: Session = Depends(get_db)):
    """随时调整项目使用的 LLM / DrawThings 配置（下一步起生效）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
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
    # 功能级模型 / 参考图开关（可随时调整；留空 / 未传 = 跟随配置默认值）
    project.dt_model_image = str(body.get("dt_model_image") or "").strip()[:200]
    project.dt_model_video = str(body.get("dt_model_video") or "").strip()[:200]
    project.dt_ref_image = _dt_ref_field(body, "dt_ref_image")
    project.dt_ref_video = _dt_ref_field(body, "dt_ref_video")
    project.updated_at = _now()
    db.commit()
    return {"ok": True}


# ---------------- 季（篇章）CRUD ----------------
@router.post("/{project_id}/seasons")
async def project_season_create(request: Request, project_id: str, db: Session = Depends(get_db)):
    """新增一季（body: {title}）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    season = pipeline.comic.add_season(db, project, title=str(body.get("title") or ""))
    return {"id": season.id, "number": season.number, "title": season.title or ""}


@router.patch("/{project_id}/seasons/{season_id}")
async def project_season_update(request: Request, project_id: str, season_id: str,
                                 db: Session = Depends(get_db)):
    """保存季（篇章）级编辑：季名 / 季大纲 / 季角色 / 章节数量设定 / 每章(标题+摘要)。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    season = pipeline.comic._get_season(db, project, season_id)
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
        pipeline.comic.save_season(
            db, project, season,
            title=body.get("title"), arc=body.get("arc"),
            characters=characters,
            count_mode=body.get("count_mode"), count_min=body.get("count_min"),
            count_max=body.get("count_max"), chapters=chapters)
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"保存季失败：{e}", f"Save season failed: {e}"))
    return {"ok": True}


@router.delete("/{project_id}/seasons/{season_id}")
def project_season_delete(request: Request, project_id: str, season_id: str,
                           db: Session = Depends(get_db)):
    """删除一季（连同其章节/媒体/季角色参考图），剩余季重排。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        pipeline.comic.delete_season(db, project, season_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.post("/{project_id}/action")
async def project_action(request: Request, project_id: str, db: Session = Depends(get_db)):
    """推进流水线：arc（故事大纲）/ season_arc（本季大纲）/ season_chars（季角色）/ chars（角色设定）/ chapters（拆章）/ generate（生成未完成章节）。
    season_arc / season_chars / chapters / generate 需 body 传 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    step = str(body.get("step") or "")
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    season = None
    if step in ("chapters", "generate", "season_arc", "season_chars"):
        sid = str(body.get("season_id") or "")
        season = pipeline.comic._get_season(db, project, sid) if sid else None
        if season is None:
            raise HTTPException(status_code=400,
                                detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    try:
        if step == "arc":
            project = await pipeline.comic.step_arc(
                db, project, lang,
                res_width=int(body.get("res_width") or 0),
                res_height=int(body.get("res_height") or 0),
                extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "season_arc":
            project = await pipeline.comic.step_season_arc(db, project, season, lang,
                                                     extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "season_chars":
            project = await pipeline.comic.step_season_chars(db, project, season, lang,
                                                       extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "chars":
            project = await pipeline.comic.step_chars(db, project, lang,
                                                extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "chapters":
            project = await pipeline.comic.step_chapters(
                db, project, season, lang,
                count_min=int(body.get("count_min") or 0),
                count_max=int(body.get("count_max") or 0))
        elif step == "generate":
            project = await pipeline.comic.step_generate(db, project, season, lang=lang)
        else:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"未知步骤: {step}", f"Unknown step: {step}"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"【{step}】失败：{e}", f"[{step}] failed: {e}"))
    return {"ok": True}


@router.post("/{project_id}/action-stream")
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
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline.comic._get_season(db, project, sid) if sid else None
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
        await queue.put((E.PROGRESS, {"current": current, "total": total, "title": title or ""}))

    async def chapter_cb(ch):
        # 生成画面：单章两步完成 → 回传提示词/描述/分辨率/媒体/状态，前端逐个刷新（后续章节仍参考它）
        await queue.put((E.CHAPTER, {"index": ch.index,
                                     "description": ch.description or "",
                                     "prompt": ch.prompt or "",
                                     "width": ch.width or 0, "height": ch.height or 0,
                                     "media_url": _media_url(ch.media_path or ""),
                                     "status": ch.status, "error": ch.error or "",
                                     "score": ch.score or 0, "score_note": ch.score_note or ""}))

    async def score_cb(ch, score, note, rd):
        # 自动评分事件：score 为 None 时 note 携带阶段（scoring/redo），为数字时是评分结果
        phase = "result" if score is not None else str(note or "")
        await queue.put((E.SCORE, {"index": ch.index, "title": ch.title or "",
                                    "score": score,
                                    "note": (note or "") if score is not None else "",
                                    "phase": phase, "redo": rd}))

    async def plan_cb(ch):
        # 章节规划：单章规划完成 → 回传标题/摘要/状态，前端逐个补入
        await queue.put((E.CHAPTER, {"index": ch.index, "title": ch.title or "",
                                     "summary": ch.summary or "", "status": ch.status}))

    async def _run():
        try:
            if step == "chapters":
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全季重规划
                await pipeline.comic.step_chapters_stream(
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
                chapters = pipeline.comic._season_chapters(db, project, season)
                targets = list(enumerate(chapters)) if indices is None \
                    else [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
                for pos, (i, ch) in enumerate(targets, start=1):
                    await progress_cb(pos, len(targets), ch.title)
                    await score_cb(ch, None, "scoring", 0)
                    try:
                        score, note = await pipeline.comic.vlm_score_chapter(db, project, season, i, lang)
                    except ValueError as e:
                        await queue.put((E.SCORE, {"index": ch.index, "title": ch.title or "",
                                                    "score": None, "note": str(e), "phase": "error", "redo": 0}))
                        continue
                    await score_cb(ch, score, note, 0)
                    await chapter_cb(ch)
            else:
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全部
                await pipeline.comic.step_generate(db, project, season, indices=indices, lang=lang,
                                             progress_cb=progress_cb, chapter_done_cb=chapter_cb,
                                             score_cb=score_cb)
            fresh = db.get(Project, project.id)
            await queue.put((E.DONE, {"status": fresh.status if fresh else ""}))
        except Exception as e:
            if step == "chapters":
                msg, msg_en = "规划章节失败：", "Planning chapters failed: "
            elif step == "score":
                msg, msg_en = "批量评分失败：", "Batch scoring failed: "
            else:
                msg, msg_en = "生成画面失败：", "Generation failed: "
            await queue.put((E.ERROR, {"message": L(lang, msg + str(e), msg_en + str(e))}))
        finally:
            await queue.put((E.EOF, None))

    task = asyncio.create_task(_run())
    try:
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # 心跳：逐章生成耗时长，保持 SSE 连接不被空闲断开
                continue
            if event == E.EOF:
                break
            yield _sse(event, data)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task  # 等任务清理（逐章提交进度）完成再结束请求（数据库会话此时才关闭）
            except asyncio.CancelledError:
                pass


@router.post("/{project_id}/gen/{index}")
async def project_gen_single(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """生成指定章节（季内）：body 需 season_id。两步连贯——① (重新)生成出图提示词 ② 生图/生视频。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline.comic._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.comic._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    try:
        project = await pipeline.comic.step_generate(db, project, season, indices=[index], lang=lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"【生成第{index+1}章】失败：{e}",
                                      f"[Generate chapter {index+1}] failed: {e}"))
    return {"ok": True}


@router.post("/{project_id}/chapters/{index}/score")
async def project_chapter_score(request: Request, project_id: str, index: int,
                                db: Session = Depends(get_db)):
    """VLM 自动评分：调用 VLM 对指定章节（季内）重新评分（与流水线自动评分同款）；
    body 需 season_id（无需分值）。评分不涉及生成，已完结作品也允许。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.comic._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.comic._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    try:
        score, note = await pipeline.comic.vlm_score_chapter(db, project, season, index, lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    return {"ok": True, "score": score, "note": note}


@router.post("/{project_id}/edit/{index}")
async def project_edit(request: Request, project_id: str, index: int,
                       db: Session = Depends(get_db)):
    """保存指定章节（季内）的出图提示词。body 需 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline.comic._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.comic._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    pipeline.comic.save_chapter_fields(db, project, season, index, str(body.get("prompt") or ""))
    return {"ok": True}


# ---------------- 章节：增删排序（季内） / 手动完成 ----------------
@router.post("/{project_id}/chapters")
async def project_chapter_add(request: Request, project_id: str, db: Session = Depends(get_db)):
    """在季末尾新增一章。body 需 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline.comic._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    pipeline.comic.add_chapter(db, project, season)
    return {"ok": True}


@router.delete("/{project_id}/chapters/{index}")
async def project_chapter_delete(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """删除季内第 index 章（连同清理媒体文件）。body/query 需 season_id。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
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
    season = pipeline.comic._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.comic._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    pipeline.comic.delete_chapter(db, project, season, index)
    return {"ok": True}


@router.post("/{project_id}/chapters/{index}/move")
async def project_chapter_move(request: Request, project_id: str, index: int,
                                db: Session = Depends(get_db)):
    """上移 / 下移季内第 index 章（body: {season_id, direction}）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    sid = str(body.get("season_id") or "")
    season = pipeline.comic._get_season(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.comic._season_chapters(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "章节不存在", "Chapter not found"))
    pipeline.comic.move_chapter(db, project, season, index, str(body.get("direction") or ""))
    return {"ok": True}


# ---------------- 封面 / 大纲 ----------------
@router.post("/{project_id}/first-image")
def project_first_image_upload(request: Request, project_id: str, file: UploadFile = File(...),
                               db: Session = Depends(get_db)):
    """上传封面（作品封面；可选作为第 1 章参考图）。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
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
    pipeline.comic.set_first_image_path(db, project, str(dest))
    return {"ok": True, "url": _media_url(str(dest))}


@router.post("/{project_id}/first-image/generate")
async def project_first_image_generate(request: Request, project_id: str,
                                       db: Session = Depends(get_db)):
    """提示词生成封面（prompt 为空时由 LLM 依据一句话创意+风格自动撰写）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        project = await pipeline.comic.generate_first_image(db, project, str(body.get("prompt") or ""),
                                                       lang=lang,
                                                       include_title=bool(body.get("include_title", True)))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成封面】失败：{e}", f"[Generate cover] failed: {e}"))
    return {"ok": True}


# ---------------- 季封面 ----------------
@router.post("/{project_id}/seasons/{season_id}/first-image")
def season_first_image_upload(request: Request, project_id: str, season_id: str,
                              file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传季封面。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.comic._get_season(db, project, season_id)
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
    pipeline.comic.set_season_first_image_path(db, project, season, str(dest))
    return {"ok": True, "url": _media_url(str(dest))}


@router.post("/{project_id}/seasons/{season_id}/first-image/generate")
async def season_first_image_generate(request: Request, project_id: str, season_id: str,
                                      db: Session = Depends(get_db)):
    """提示词生成季封面（prompt 为空时由 LLM 依据季大纲/季角色自动撰写并叠加季名）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.comic._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    _ensure_not_finished(project, lang)
    try:
        season = await pipeline.comic.generate_season_first_image(db, project, season,
                                                            str(body.get("prompt") or ""),
                                                            lang=lang,
                                                            include_title=bool(body.get("include_title", True)))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成季封面】失败：{e}",
                                     f"[Generate season cover] failed: {e}"))
    return {"ok": True}


@router.post("/{project_id}/first-image/overlay-title")
async def project_first_image_overlay_title(request: Request, project_id: str,
                                            db: Session = Depends(get_db)):
    """把作品标题叠加到现有封面上（PIL 合成；可传位置/字号/样式/颜色；不重新生图）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        project = pipeline.comic.overlay_first_image_title(db, project, lang, _overlay_opts(body))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【叠加标题】失败：{e}", f"[Overlay title] failed: {e}"))
    return {"ok": True, "url": _media_url(project.first_image or "")}


@router.post("/{project_id}/seasons/{season_id}/first-image/ref")
async def season_cover_ref(request: Request, project_id: str, season_id: str,
                           db: Session = Depends(get_db)):
    """设置是否把季封面作为本季第 1 章参考（漫画 img2img / 短剧首帧）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.comic._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    _ensure_not_finished(project, lang)
    enabled = bool(body.get("enabled"))
    pipeline.comic.set_season_cover_ref(db, project, season, enabled)
    return {"ok": True, "enabled": enabled}


@router.post("/{project_id}/seasons/{season_id}/first-image/overlay-title")
async def season_first_image_overlay_title(request: Request, project_id: str, season_id: str,
                                           db: Session = Depends(get_db)):
    """把季名叠加到现有季封面上（PIL 合成；可传位置/字号/样式/颜色；不重新生图）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.comic._get_season(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    _ensure_not_finished(project, lang)
    try:
        season = pipeline.comic.overlay_season_first_image_title(db, project, season, lang, _overlay_opts(body))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【叠加季名】失败：{e}", f"[Overlay season name] failed: {e}"))
    return {"ok": True, "url": _media_url(season.first_image or "")}


@router.post("/{project_id}/characters/{char_id}/image")
def project_char_image_upload(request: Request, project_id: str, char_id: str,
                              file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传角色参考图（角色设定页：供各章保持角色形象一致）。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
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
        pipeline.comic.set_char_image(db, project, char_id, str(dest))
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【上传角色参考图】失败：{e}",
                                     f"[Upload character image] failed: {e}"))
    return {"ok": True, "url": _media_url(str(dest))}


@router.delete("/{project_id}/characters/{char_id}/image")
def project_char_image_delete(request: Request, project_id: str, char_id: str,
                              db: Session = Depends(get_db)):
    """清除角色参考图（连同删除文件）。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        pipeline.comic.set_char_image(db, project, char_id, "")
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【清除角色参考图】失败：{e}",
                                     f"[Remove character image] failed: {e}"))
    return {"ok": True}


@router.post("/{project_id}/characters/{char_id}/gen-desc")
async def project_char_gen_desc(request: Request, project_id: str, char_id: str,
                                db: Session = Depends(get_db)):
    """AI 生成单个角色的形象/性格描述（该角色有参考图时以图为准，VLM 一律支持图片输入）。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    _ensure_not_finished(project, lang)
    try:
        desc = await pipeline.comic.gen_char_description(db, project, char_id, lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成角色描述】失败：{e}",
                                     f"[Generate character description] failed: {e}"))
    return {"ok": True, "description": desc}


@router.post("/{project_id}/outline")
async def project_outline_save(request: Request, project_id: str, db: Session = Depends(get_db)):
    """保存大纲页手动编辑：大纲 / 角色设定 / 风格 / 默认分辨率（章节按季编辑，见 /seasons）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _comic_project(db, project_id, lang)
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
        pipeline.comic.save_outline(
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
@router.get("/{project_id}/export/zip")
def project_export_zip(request: Request, project_id: str, season_id: str | None = None,
                        db: Session = Depends(get_db)):
    """导出 ZIP：媒体（图/视频）+ 首图 + 大纲/角色/各章剧本文本；season_id 非空时仅导出该季章节。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = None
    if season_id:
        season = pipeline.comic._get_season(db, project, season_id)
        if season is None:
            raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    try:
        path, fname = pipeline.comic.export_zip(db, project, season)
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"导出 ZIP 失败：{e}", f"Export ZIP failed: {e}"))
    return FileResponse(path, filename=fname, media_type="application/zip")


@router.get("/{project_id}/export/pdf")
def project_export_pdf(request: Request, project_id: str, season_id: str | None = None,
                        preview: int = 0, db: Session = Depends(get_db)):
    """导出 PDF（漫画：各章图片按序拼成多页；短剧/无图 → 400）；season_id 非空时仅导出该季章节。
    preview=1：以 inline 返回（浏览器新标签直接预览，不触发下载）。"""
    lang = _lang(request)
    project = _comic_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = None
    if season_id:
        season = pipeline.comic._get_season(db, project, season_id)
        if season is None:
            raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    try:
        path, fname = pipeline.comic.export_pdf(db, project, season)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"导出 PDF 失败：{e}", f"Export PDF failed: {e}"))
    # preview=1 用 inline（浏览器直接渲染）；统一走 Starlette 的 filename 处理
    #（非 ASCII 自动 UTF-8 百分号编码 filename*=utf-8''，中文文件名不会 500）
    return FileResponse(path, media_type="application/pdf", filename=fname,
                        content_disposition_type="inline" if preview else "attachment")
