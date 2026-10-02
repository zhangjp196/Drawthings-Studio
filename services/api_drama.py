"""短剧项目 API 路由（/api/dramas/*）：仅短剧走向。

- 只调用短剧流水线（pipeline.drama），无 kind 分支
- 与另一类型完全独立：services/api_comic.py
- 设计约定：漫画 / 短剧刻意分成两条重复的独立线（便于分开开发维护），请勿合并 / 勿抽共享基座（见 AGENTS.md）"""
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from db import get_db
from i18n import L
from models import Chapter, Project, ProjectJob, Season
from config_store import ConfigStore
from services.pipeline import _now, chars_from_raw
from services.runtime import pipeline
from services import events as E
from services import jobs
from services.jobs import JobCancelled
from services.api_common import (
    MEDIA_DIR, MAX_IMAGE_UPLOAD, _overlay_opts, _lang, _json_body, _media_url,
    _chapter_view, _dt_ref_field, _dt_steps_field, _project_view, _config_lists, _clamp_page, _sse,
)

router = APIRouter(prefix="/api/dramas", tags=["drama"])

def _drama_project(db: Session, project_id: str, lang: str) -> Project:
    """取短剧项目：不存在或类型不符（跨命名空间访问）→ 404。"""
    project = pipeline.drama.get(db, project_id)
    if project is None or (project.kind or "") != "drama":
        raise HTTPException(status_code=404,
                            detail=L(lang, "项目不存在", "Project not found"))
    return project


def _get_season_any(db: Session, project: Project, season_id: str, lang: str) -> Season | None:
    """按 id 取本项目的季（漫画/短剧共用：两者都用同一张 seasons 表与 Season 模型）。"""
    return db.get(Season, season_id) if season_id else None


@router.post("")
async def drama_create(request: Request, db: Session = Depends(get_db)):
    """新建短剧创作（简化）：仅需 **模型（LLM 配置）** 与 **项目名称**。
    名称同时作为初始标题与创作主题（seed）；正式标题由「生成大纲」一并产出；
    DrawThings 配置取全局默认（未设则留空，可在项目「设置」中补选）。"""
    lang = _lang(request)
    body = await _json_body(request)
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400,
                            detail=L(lang, "项目名称不能为空", "Project name cannot be empty"))
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(str(body.get("llm_config_id") or ""))
    if not llm_cfg:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的模型配置",
                                     "Please select a valid model (LLM) config"))
    # DrawThings 配置：显式指定 > 全局默认 > 首个可用配置（新建表单不再手选，项目可在「设置」中更换）
    dt_id = str(body.get("drawthings_config_id") or "").strip()
    if not dt_id:
        dt_id = str(cs.get_settings().get("default_dt_config_id") or "").strip()
    dt_cfg = cs.get_drawthing(dt_id) if dt_id else None
    if dt_cfg is None:
        dt_items = cs.list_drawthing()
        if dt_items:
            dt_cfg = dt_items[0]
    if dt_cfg is None:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请先在「配置管理」创建 DrawThings 配置，并在系统设置中设为默认",
                                     "Create a DrawThings config in Config Management first (optionally set it as the default)"))
    project = pipeline.drama.create(db, "drama", name, llm_cfg.id,
                                    dt_cfg.id if dt_cfg else "",
                                    title=name)
    return {"id": project.id}


@router.get("")
def projects_list(db: Session = Depends(get_db), page: int = 1, size: int = 10,
                  status: str = "", q: str = "", sort: str = "desc"):
    """创作列表：类型/状态/关键词筛选 + 排序（创建时间 / 最近活跃）+ 分页。"""
    page, size = _clamp_page(page, size)
    rows, total = pipeline.drama.list_projects(db, "drama", status or None, q.strip(), sort,
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
    project = _drama_project(db, project_id, lang)
    if not project:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail=L(lang, "标题不能为空", "Title cannot be empty"))
    project.title = title[:200]
    project.updated_at = _now()
    db.commit()
    return {"ok": True, "title": title}


@router.post("/{project_id}/delete")
def project_delete(request: Request, project_id: str, db: Session = Depends(get_db)):
    """删除创作（含全部章节与媒体文件）。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if not project:
        raise HTTPException(status_code=404,
                            detail=L(_lang(request), "项目不存在", "Project not found"))
    pipeline.drama.delete_project(db, project)
    return {"ok": True}


@router.get("/{project_id}")
def project_view(request: Request, project_id: str, db: Session = Depends(get_db)):
    """项目详情：状态/篇幅/首图 + 各季（大纲/角色）+ 章节 + 可选配置。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    pipeline.drama.ensure_first_episode(db, project)  # 保证存在第一季（旧项目懒迁移：自动建第一季并入孤儿章节）
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
            "global_prompt": project.global_prompt or "",
            "res_width": project.res_width or 0, "res_height": project.res_height or 0,
            "auto_score": project.auto_score or 0, "score_min": project.score_min or 60,
            "auto_redo": project.auto_redo or 0, "stop_on_low": project.stop_on_low or 0,
            "first_image_url": _media_url(project.first_image or ""),
            "created_at": project.created_at, "updated_at": project.updated_at,
            "llm_config_id": project.llm_config_id,
            "drawthings_config_id": project.drawthings_config_id,
            "dt_model_image": project.dt_model_image or "",
            "dt_max_steps_image": project.dt_max_steps_image or 0,
            "dt_max_steps_video": project.dt_max_steps_video or 0,
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
    """随时调整项目设置：所用 LLM / DrawThings 配置 + 自动评分开关（下一步起生效）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
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
    # 最大 Step 数（图像 / 视频分开，随所选模型一起配；未传 = 不改）
    if "dt_max_steps_image" in body:
        project.dt_max_steps_image = _dt_steps_field(body, "dt_max_steps_image", lang)
    if "dt_max_steps_video" in body:
        project.dt_max_steps_video = _dt_steps_field(body, "dt_max_steps_video", lang)
    # 自动评分（设置弹框内调整；未传 = 不改）
    for key in ("auto_score", "auto_redo", "stop_on_low"):
        if key in body:
            setattr(project, key, 1 if body.get(key) else 0)
    if "score_min" in body:
        project.score_min = max(0, min(100, int(body.get("score_min") or 0)))
    project.updated_at = _now()
    db.commit()
    return {"ok": True}


# ---------------- 季（篇章）CRUD ----------------
@router.post("/{project_id}/seasons")
async def project_season_create(request: Request, project_id: str, db: Session = Depends(get_db)):
    """新增一季（body: {title}）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.drama.add_episode(db, project, title=str(body.get("title") or ""))
    return {"id": season.id, "number": season.number, "title": season.title or ""}


@router.patch("/{project_id}/seasons/{season_id}")
async def project_season_update(request: Request, project_id: str, season_id: str,
                                 db: Session = Depends(get_db)):
    """保存季（篇章）级编辑：季名 / 季大纲 / 季角色 / 章节数量设定 / 每章(标题+摘要)。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.drama._get_episode(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "集不存在", "Episode not found"))
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
        pipeline.drama.save_episode(
            db, project, season,
            title=body.get("title"), arc=body.get("arc"),
            characters=characters,
            count_min=body.get("count_min"),
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
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    try:
        pipeline.drama.delete_episode(db, project, season_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.post("/{project_id}/action")
async def project_action(request: Request, project_id: str, db: Session = Depends(get_db)):
    """推进流水线：season_arc（本集大纲，同时产出作品标题）/ season_chars（本集角色）/ chars（角色设定）/ chapters（拆章）/ generate（生成未完成章节）。
    除 chars 外均需 body 传 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    step = str(body.get("step") or "")
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = None
    if step in ("chapters", "generate", "season_arc", "season_chars"):
        sid = str(body.get("season_id") or "")
        season = pipeline.drama._get_episode(db, project, sid) if sid else None
        if season is None:
            raise HTTPException(status_code=400,
                                detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    try:
        if step == "season_arc":
            project = await pipeline.drama.step_episode_arc(db, project, season, lang,
                                                     extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "season_chars":
            project = await pipeline.drama.step_episode_chars(db, project, season, lang,
                                                       extra_prompt=str(body.get("extra_prompt") or ""))
        elif step == "chapters":
            project = await pipeline.drama.step_clips(
                db, project, season, lang,
                count_min=int(body.get("count_min") or 0),
                count_max=int(body.get("count_max") or 0))
        elif step == "generate":
            project = await pipeline.drama.step_generate(db, project, season, lang=lang)
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
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    return StreamingResponse(
        _project_action_stream(db, project, season, lang, step, body, _new_project_job(db, project, step)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _new_project_job(db: Session, project: Project, kind: str) -> tuple[ProjectJob, "jobs.JobControl"]:
    """创建项目生成任务（running）并登记取消句柄。"""
    job = ProjectJob(id=uuid.uuid4().hex[:12], project_id=project.id, kind=kind,
                     status="running", created_at=_now(), updated_at=_now())
    db.add(job)
    db.commit()
    return job, jobs.register(job.id)


def _finish_project_job(db: Session, job: ProjectJob, status: str, error: str = "") -> None:
    job.status = status
    if error:
        job.error = error[:2000]
    job.updated_at = _now()
    job.finished_at = _now()
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


async def _project_action_stream(db: Session, project: Project, season: Season,
                                  lang: str, step: str, body: dict,
                                  job_and_control: tuple):
    """后台执行逐章推进（生成画面 / 章节规划）并逐事件下发：进度/单章回调入队 → SSE 帧；结束发 done，异常发 error。

    同步更新项目生成任务（note/状态）；客户端断开时协作式取消正在跑的生成。
    """
    queue: asyncio.Queue = asyncio.Queue()
    job, control = job_and_control
    cancel_event = control.event

    def _job(status=None, note=None, error=None, finished=False):
        try:
            if status:
                job.status = status
            if note is not None:
                job.note = str(note)[:500]
            if error is not None:
                job.error = str(error)[:2000]
            job.updated_at = _now()
            if finished:
                job.finished_at = _now()
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass

    async def progress_cb(current: int, total: int, title: str):
        _job(note=f"{current}/{total} {title or ''}".strip())
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
        # 自动评分事件：score 为 None 时 note 携带阶段（scoring/redo）；为数字时是评分结果
        note = note or ""
        phase = str(note) if score is None else "result"
        if note == "stopped":
            phase = "stopped"  # 低于阈值停止生成（score 仍为该章分数）
        await queue.put((E.SCORE, {"index": ch.index, "title": ch.title or "",
                                    "score": score,
                                    "note": "" if (score is None or note == "stopped") else note,
                                    "phase": phase, "redo": rd}))

    async def plan_cb(ch):
        # 章节规划：单章规划完成 → 回传标题/摘要/时长/状态，前端逐个补入
        await queue.put((E.CHAPTER, {"index": ch.index, "title": ch.title or "",
                                     "summary": ch.summary or "",
                                     "seconds": int(getattr(ch, "seconds", 0) or 0),
                                     "status": ch.status}))

    async def _run():
        try:
            if step == "chapters":
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全季重规划
                await pipeline.drama.step_clips_stream(
                    db, project, season, lang=lang,
                    count_min=int(body.get("count_min") or 0),
                    count_max=int(body.get("count_max") or 0),
                    indices=indices,
                    mode=str(body.get("mode") or "replan"),  # replan=重做 / append=新增
                    progress_cb=progress_cb, chapter_done_cb=plan_cb,
                    cancel_event=cancel_event)
            elif step == "score":
                # VLM 批量评分：逐章评分（季内），单章失败不阻塞后续章节（error 阶段事件单独提示）
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全季
                chapters = pipeline.drama._episode_clips(db, project, season)
                targets = list(enumerate(chapters)) if indices is None \
                    else [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
                for pos, (i, ch) in enumerate(targets, start=1):
                    control.check()  # 取消则抛 JobCancelled
                    await progress_cb(pos, len(targets), ch.title)
                    await score_cb(ch, None, "scoring", 0)
                    try:
                        score, note = await pipeline.drama.vlm_score_clip(db, project, season, i, lang)
                    except ValueError as e:
                        await queue.put((E.SCORE, {"index": ch.index, "title": ch.title or "",
                                                    "score": None, "note": str(e), "phase": "error", "redo": 0}))
                        continue
                    await score_cb(ch, score, note, 0)
                    await chapter_cb(ch)
            else:
                raw = body.get("indices")
                indices = [int(x) for x in raw] if raw else None  # 省略/空 = 全部
                await pipeline.drama.step_generate(db, project, season, indices=indices, lang=lang,
                                             progress_cb=progress_cb, chapter_done_cb=chapter_cb,
                                             score_cb=score_cb, cancel_event=cancel_event)
            fresh = db.get(Project, project.id)
            _job(status="done", note="完成", finished=True)
            await queue.put((E.DONE, {"status": fresh.status if fresh else ""}))
        except JobCancelled:
            _job(status="cancelled", note="已取消", finished=True)
            await queue.put((E.DONE, {"status": "", "cancelled": True}))
        except Exception as e:
            if step == "chapters":
                msg, msg_en = "规划章节失败：", "Planning chapters failed: "
            elif step == "score":
                msg, msg_en = "批量评分失败：", "Batch scoring failed: "
            else:
                msg, msg_en = "生成画面失败：", "Generation failed: "
            _job(status="error", error=str(e), finished=True)
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
        control.cancel()  # 客户端断开：请求取消（正在跑的生图尽快停止）
        if not task.done():
            task.cancel()
            try:
                await task  # 等任务清理（逐章提交进度）完成再结束请求（数据库会话此时才关闭）
            except asyncio.CancelledError:
                pass
        jobs.pop(job.id)
        if job.status == "running":
            _job(status="interrupted", note="中断", finished=True)


@router.get("/{project_id}/jobs")
def project_jobs(request: Request, project_id: str, active: int = 0, limit: int = 20,
                 db: Session = Depends(get_db)):
    """项目生成任务列表（Job）：可按 active=1 只看运行中；用于可观测与「停止生成」。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    q = db.query(ProjectJob).filter(ProjectJob.project_id == project.id)
    if active:
        q = q.filter(ProjectJob.status == "running")
    rows = q.order_by(ProjectJob.created_at.desc()).limit(min(max(limit, 1), 100)).all()
    return {"jobs": [{
        "id": j.id, "kind": j.kind, "status": j.status, "note": j.note or "",
        "error": j.error or "", "created_at": j.created_at or "",
        "updated_at": j.updated_at or "", "finished_at": j.finished_at or "",
    } for j in rows], "total": len(rows)}


@router.post("/{project_id}/jobs/{job_id}/cancel")
def project_job_cancel(request: Request, project_id: str, job_id: str,
                       db: Session = Depends(get_db)):
    """取消运行中的生成任务：置位取消句柄（协作式停止，不依赖客户端连接）。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    job = db.get(ProjectJob, job_id)
    if not job or job.project_id != project.id:
        raise HTTPException(status_code=404, detail=L(lang, "任务不存在", "Job not found"))
    if job.status != "running":
        return {"ok": True, "status": job.status}
    if not jobs.cancel(job_id):
        job.status = "cancelled"
        job.updated_at = _now()
        job.finished_at = _now()
        db.commit()
        return {"ok": True, "status": "cancelled"}
    return {"ok": True, "status": "cancelling"}


@router.post("/{project_id}/gen/{index}")
async def project_gen_single(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """生成指定章节（季内）：body 需 season_id。两步连贯——① (重新)生成出图提示词 ② 生图/生视频。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.drama._episode_clips(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "片段不存在", "Clip not found"))
    job, control = _new_project_job(db, project, "single")
    try:
        project = await pipeline.drama.step_generate(db, project, season, indices=[index], lang=lang,
                                                     cancel_event=control.event,
                                                     extra_prompt=str(body.get("extra_prompt") or ""))
        _finish_project_job(db, job, "done")
    except JobCancelled:
        _finish_project_job(db, job, "cancelled")
        raise HTTPException(status_code=400, detail=L(lang, "已取消", "Cancelled"))
    except Exception as e:
        _finish_project_job(db, job, "error", str(e))
        raise HTTPException(status_code=400,
                             detail=L(lang, f"【生成本集第{index+1}段】失败：{e}",
                                      f"[Generate clip {index+1}] failed: {e}"))
    finally:
        jobs.pop(job.id)
    return {"ok": True}


@router.post("/{project_id}/chapters/{index}/score")
async def project_chapter_score(request: Request, project_id: str, index: int,
                                db: Session = Depends(get_db)):
    """VLM 自动评分：调用 VLM 对指定章节（季内）重新评分（与流水线自动评分同款）；
    body 需 season_id（无需分值）。评分不涉及生成，可随时调用。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.drama._episode_clips(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "片段不存在", "Clip not found"))
    try:
        score, note = await pipeline.drama.vlm_score_clip(db, project, season, index, lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    return {"ok": True, "score": score, "note": note}


@router.post("/{project_id}/edit/{index}")
async def project_edit(request: Request, project_id: str, index: int,
                       db: Session = Depends(get_db)):
    """保存指定章节（季内）的出图提示词。body 需 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.drama._episode_clips(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "片段不存在", "Clip not found"))
    def _int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    pipeline.drama.save_clip_fields(db, project, season, index,
                                       str(body.get("prompt") or ""),
                                       seconds=_int_or_none(body.get("seconds")))
    return {"ok": True}


# ---------------- 章节：增删排序（季内） / 手动完成 ----------------
@router.post("/{project_id}/chapters")
async def project_chapter_add(request: Request, project_id: str, db: Session = Depends(get_db)):
    """在季末尾新增一章。body 需 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    pipeline.drama.add_clip(db, project, season)
    return {"ok": True}


@router.delete("/{project_id}/chapters/{index}")
async def project_chapter_delete(request: Request, project_id: str, index: int, db: Session = Depends(get_db)):
    """删除季内第 index 章（连同清理媒体文件）。body/query 需 season_id。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(request.query_params.get("season_id") or "")
    if not sid:
        try:
            body = await _json_body(request)
            sid = str(body.get("season_id") or "")
        except Exception:
            pass
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.drama._episode_clips(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "片段不存在", "Clip not found"))
    pipeline.drama.delete_clip(db, project, season, index)
    return {"ok": True}


@router.post("/{project_id}/chapters/delete-batch")
async def project_chapters_delete_batch(request: Request, project_id: str,
                                        db: Session = Depends(get_db)):
    """批量删除季内片段（连同清理媒体文件），其余片段重新编号。body 需 season_id；
    indices 可选（季内序号列表，缺省=删除该季全部片段）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    raw = body.get("indices")
    indices = None if raw is None else [int(i) for i in raw]
    pipeline.drama.delete_clips(db, project, season, indices)
    return {"ok": True}


@router.post("/{project_id}/chapters/clear")
async def project_chapters_clear(request: Request, project_id: str, db: Session = Depends(get_db)):
    """批量清空季内章节产物（产物/出图提示词/评分）。body 需 season_id；
    indices 可选（季内序号列表，缺省=该季全部章节）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    raw = body.get("indices")
    indices = None if raw is None else [int(i) for i in raw]
    pipeline.drama.clear_clips(db, project, season, indices)
    return {"ok": True}


@router.post("/{project_id}/chapters/{index}/clear")
async def project_chapter_clear(request: Request, project_id: str, index: int,
                                db: Session = Depends(get_db)):
    """清空季内第 index 章的产物（产物/出图提示词/评分）。body 需 season_id。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.drama._episode_clips(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "片段不存在", "Clip not found"))
    pipeline.drama.clear_clips(db, project, season, [index])
    return {"ok": True}


@router.post("/{project_id}/chapters/{index}/move")
async def project_chapter_move(request: Request, project_id: str, index: int,
                                db: Session = Depends(get_db)):
    """上移 / 下移季内第 index 章（body: {season_id, direction}）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    sid = str(body.get("season_id") or "")
    season = pipeline.drama._get_episode(db, project, sid) if sid else None
    if season is None:
        raise HTTPException(status_code=400,
                             detail=L(lang, "请指定有效的季 (season_id)", "Please provide a valid season_id"))
    chapters = pipeline.drama._episode_clips(db, project, season)
    if not (0 <= index < len(chapters)):
        raise HTTPException(status_code=404, detail=L(lang, "片段不存在", "Clip not found"))
    pipeline.drama.move_clip(db, project, season, index, str(body.get("direction") or ""))
    return {"ok": True}


# ---------------- 封面 / 大纲 ----------------
@router.post("/{project_id}/first-image")
def project_first_image_upload(request: Request, project_id: str, file: UploadFile = File(...),
                               db: Session = Depends(get_db)):
    """上传封面（作品封面；可选作为第 1 章参考图）。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
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
    pipeline.drama.set_first_image_path(db, project, str(dest))
    return {"ok": True, "url": _media_url(str(dest))}


@router.post("/{project_id}/first-image/generate")
async def project_first_image_generate(request: Request, project_id: str,
                                       db: Session = Depends(get_db)):
    """提示词生成封面（prompt 为空时由 LLM 依据一句话创意+风格自动撰写）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    try:
        project = await pipeline.drama.generate_first_image(db, project, str(body.get("prompt") or ""),
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
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.drama._get_episode(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "集不存在", "Episode not found"))
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
    pipeline.drama.set_episode_first_image_path(db, project, season, str(dest))
    return {"ok": True, "url": _media_url(str(dest))}


@router.post("/{project_id}/seasons/{season_id}/first-image/generate")
async def season_first_image_generate(request: Request, project_id: str, season_id: str,
                                      db: Session = Depends(get_db)):
    """提示词生成季封面（prompt 为空时由 LLM 依据季大纲/季角色自动撰写并叠加季名）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.drama._get_episode(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "集不存在", "Episode not found"))
    try:
        season = await pipeline.drama.generate_episode_first_image(db, project, season,
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
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    try:
        project = pipeline.drama.overlay_first_image_title(db, project, lang, _overlay_opts(body))
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
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.drama._get_episode(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "集不存在", "Episode not found"))
    enabled = bool(body.get("enabled"))
    pipeline.drama.set_episode_cover_ref(db, project, season, enabled)
    return {"ok": True, "enabled": enabled}


@router.post("/{project_id}/seasons/{season_id}/first-image/overlay-title")
async def season_first_image_overlay_title(request: Request, project_id: str, season_id: str,
                                           db: Session = Depends(get_db)):
    """把季名叠加到现有季封面上（PIL 合成；可传位置/字号/样式/颜色；不重新生图）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = pipeline.drama._get_episode(db, project, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "集不存在", "Episode not found"))
    try:
        season = pipeline.drama.overlay_episode_first_image_title(db, project, season, lang, _overlay_opts(body))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【叠加季名】失败：{e}", f"[Overlay season name] failed: {e}"))
    return {"ok": True, "url": _media_url(season.first_image or "")}


@router.post("/{project_id}/seasons/{season_id}/characters/{char_id}/image")
def season_char_image_upload(request: Request, project_id: str, season_id: str, char_id: str,
                              file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传季角色参考图（季角色页：供各章保持角色形象一致）。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = _get_season_any(db, project, season_id, lang)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
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
    dest = MEDIA_DIR / f"char_{season.id}_{char_id}{ext}"
    dest.write_bytes(data)
    try:
        pipeline.drama.set_char_image(db, project, season, char_id, str(dest))
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【上传角色参考图】失败：{e}",
                                     f"[Upload character image] failed: {e}"))
    return {"ok": True, "url": _media_url(str(dest))}


@router.delete("/{project_id}/seasons/{season_id}/characters/{char_id}/image")
def season_char_image_delete(request: Request, project_id: str, season_id: str, char_id: str,
                              db: Session = Depends(get_db)):
    """清除季角色参考图（连同删除文件）。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = _get_season_any(db, project, season_id, lang)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    try:
        pipeline.drama.set_char_image(db, project, season, char_id, "")
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【清除角色参考图】失败：{e}",
                                     f"[Remove character image] failed: {e}"))
    return {"ok": True}


@router.post("/{project_id}/seasons/{season_id}/characters/{char_id}/gen-desc")
async def season_char_gen_desc(request: Request, project_id: str, season_id: str, char_id: str,
                                db: Session = Depends(get_db)):
    """AI 生成单个季角色的形象/性格描述（有参考图时以图为准，VLM 一律支持图片输入）。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = _get_season_any(db, project, season_id, lang)
    if season is None:
        raise HTTPException(status_code=404, detail=L(lang, "季不存在", "Season not found"))
    try:
        desc = await pipeline.drama.gen_char_description(db, project, season, char_id, lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"【生成角色描述】失败：{e}",
                                     f"[Generate character description] failed: {e}"))
    return {"ok": True, "description": desc}


@router.post("/{project_id}/outline")
async def project_outline_save(request: Request, project_id: str, db: Session = Depends(get_db)):
    """保存总体页手动编辑：全局要求（风格 + 要点）/ 默认分辨率 / 评分设置（章节与角色按季编辑，见 /seasons）。"""
    lang = _lang(request)
    body = await _json_body(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    try:
        pipeline.drama.save_outline(
            db, project,
            global_prompt=body.get("global_prompt"),
            res_width=body.get("res_width"), res_height=body.get("res_height"),
            auto_score=body.get("auto_score"), score_min=body.get("score_min"),
            auto_redo=body.get("auto_redo"), stop_on_low=body.get("stop_on_low"))
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
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = None
    if season_id:
        season = pipeline.drama._get_episode(db, project, season_id)
        if season is None:
            raise HTTPException(status_code=404, detail=L(lang, "集不存在", "Episode not found"))
    try:
        path, fname = pipeline.drama.export_zip(db, project, season)
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"导出 ZIP 失败：{e}", f"Export ZIP failed: {e}"))
    return FileResponse(path, filename=fname, media_type="application/zip")


@router.get("/{project_id}/export/video")
def project_export_video(request: Request, project_id: str, season_id: str | None = None,
                          preview: int = 0, db: Session = Depends(get_db)):
    """合成视频导出（短剧）：把该季各章视频按章序首尾相接为一段 mp4。
    preview=1：以 inline 返回（浏览器新标签直接预览，不触发下载）。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    season = None
    if season_id:
        season = pipeline.drama._get_episode(db, project, season_id)
        if season is None:
            raise HTTPException(status_code=404, detail=L(lang, "集不存在", "Episode not found"))
    try:
        path, fname = pipeline.drama.export_video(db, project, season)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=L(lang, str(e), str(e)))
    except Exception as e:
        raise HTTPException(status_code=400,
                             detail=L(lang, f"合成视频失败：{e}", f"Compose video failed: {e}"))
    return FileResponse(path, media_type="video/mp4", filename=fname,
                        content_disposition_type="inline" if preview else "attachment")


@router.get("/{project_id}/export/pdf")
def project_export_pdf(request: Request, project_id: str, season_id: str | None = None,
                        preview: int = 0, db: Session = Depends(get_db)):
    """短剧不再支持 PDF：一律 400，提示改用「合成视频」。保留路由以兼容旧调用。"""
    lang = _lang(request)
    project = _drama_project(db, project_id, lang)
    if project is None:
        raise HTTPException(status_code=404, detail=L(lang, "项目不存在", "Project not found"))
    msg = L(lang, "短剧为视频，请使用「合成视频」导出（不再支持 PDF）",
            "Drama is video; use \"Compose video\" to export (PDF is no longer supported).")
    raise HTTPException(status_code=400, detail=msg)
