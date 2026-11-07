"""全局任务中心 API（/api/tasks/*）：跨漫画 / 短剧 / 微创作的任务视图与取消。

- `GET  /api/tasks`：合并两类任务（ProjectJob / GenerationJob），按创建时间倒序；
- `GET  /api/tasks/{id}`：单个任务（含 result，供文档导入预览等场景取结果）；
- `POST /api/tasks/{id}/cancel`：统一取消入口（内部仍走 services/jobs 的取消句柄）。

任务事件与状态广播由 `services/task_manager` 负责；本模块只做查询、快照与取消编排。
`project_job_snapshot` / `micro_job_snapshot` 也被各任务运行器用来广播状态变化。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db
from i18n import L
from models import GenerationJob, MicroWork, Project, ProjectJob
from services import jobs, task_manager
from services.api_common import _lang

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

ACTIVE = ("running",)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def project_job_snapshot(job: ProjectJob, project: Project | None = None) -> dict:
    """项目任务快照（scope=comic/drama，按项目 kind 区分）。"""
    title = ""
    if project is not None:
        title = project.title or project.origin or ""
    return {
        "id": job.id,
        "scope": (project.kind if project is not None else "project") or "project",
        "target_id": job.project_id,
        "title": title,
        "kind": job.kind or "",
        "status": job.status or "",
        "note": job.note or "",
        "error": job.error or "",
        "media": "",
        "session_id": "",
        "created_at": job.created_at or "",
        "updated_at": job.updated_at or "",
        "finished_at": job.finished_at or "",
    }


def micro_job_snapshot(job: GenerationJob, work: MicroWork | None = None) -> dict:
    """微创作任务快照（scope=micro）。"""
    return {
        "id": job.id,
        "scope": "micro",
        "target_id": job.micro_id,
        "title": (work.title or "") if work is not None else "",
        "kind": job.kind or "",
        "status": job.status or "",
        "note": job.note or "",
        "error": job.error or "",
        "media": job.media or "",
        "session_id": job.session_id or "",
        "created_at": job.created_at or "",
        "updated_at": job.updated_at or "",
        "finished_at": job.finished_at or "",
    }


def _load_snapshots(db: Session, project_jobs: list[ProjectJob], micro_jobs: list[GenerationJob]) -> list[dict]:
    """批量补齐标题后生成快照（避免逐条查询）。"""
    pids = {j.project_id for j in project_jobs}
    mids = {j.micro_id for j in micro_jobs}
    projects = {p.id: p for p in db.query(Project).filter(Project.id.in_(pids)).all()} if pids else {}
    works = {w.id: w for w in db.query(MicroWork).filter(MicroWork.id.in_(mids)).all()} if mids else {}
    out = [project_job_snapshot(j, projects.get(j.project_id)) for j in project_jobs]
    out += [micro_job_snapshot(j, works.get(j.micro_id)) for j in micro_jobs]
    out.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return out


def snapshots_for(db: Session, ids: set[str]) -> list[dict]:
    """按 id 集合取快照（WS 订阅时回推当前状态）。"""
    if not ids:
        return []
    ids = set(ids)
    return _load_snapshots(
        db,
        db.query(ProjectJob).filter(ProjectJob.id.in_(ids)).all(),
        db.query(GenerationJob).filter(GenerationJob.id.in_(ids)).all(),
    )


def active_snapshots(db: Session, limit: int = 50) -> list[dict]:
    """最近的活动任务（任务中心订阅「*」时的初始快照）。"""
    limit = min(max(int(limit or 50), 1), 200)
    return _load_snapshots(
        db,
        db.query(ProjectJob).order_by(ProjectJob.created_at.desc()).limit(limit).all(),
        db.query(GenerationJob).order_by(GenerationJob.created_at.desc()).limit(limit).all(),
    )[:limit]


@router.get("")
def list_tasks(request: Request, active: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """全局任务列表：默认最近 50 条；active=1 只返回运行中。"""
    limit = min(max(int(limit or 50), 1), 200)
    pq = db.query(ProjectJob)
    mq = db.query(GenerationJob)
    if active:
        pq = pq.filter(ProjectJob.status.in_(ACTIVE))
        mq = mq.filter(GenerationJob.status.in_(ACTIVE))
    jobs_list = _load_snapshots(
        db,
        pq.order_by(ProjectJob.created_at.desc()).limit(limit).all(),
        mq.order_by(GenerationJob.created_at.desc()).limit(limit).all(),
    )[:limit]
    return {"jobs": jobs_list, "total": len(jobs_list)}


@router.get("/{job_id}")
def get_task(request: Request, job_id: str, db: Session = Depends(get_db)):
    """单个任务详情（含 result；供文档导入预览等场景读取）。"""
    job = db.get(ProjectJob, job_id)
    if job is not None:
        snap = _load_snapshots(db, [job], [])[0]
        snap["result"] = job.result or ""
        return snap
    mjob = db.get(GenerationJob, job_id)
    if mjob is not None:
        return _load_snapshots(db, [], [mjob])[0]
    raise HTTPException(status_code=404, detail=L(_lang(request), "任务不存在", "Task not found"))


@router.post("/{job_id}/cancel")
def cancel_task(request: Request, job_id: str, db: Session = Depends(get_db)):
    """统一取消：置位取消句柄（协作式停止）；无运行句柄的残留任务直接标记 cancelled。"""
    job = db.get(ProjectJob, job_id)
    mjob = None
    if job is None:
        mjob = db.get(GenerationJob, job_id)
        job = mjob
    if job is None:
        raise HTTPException(status_code=404, detail=L(_lang(request), "任务不存在", "Task not found"))
    if job.status != "running":
        return {"ok": True, "status": job.status}
    if jobs.cancel(job_id):
        return {"ok": True, "status": "cancelling"}
    job.status = "cancelled"
    job.updated_at = _now()
    job.finished_at = _now()
    db.commit()
    snap = (
        project_job_snapshot(job, db.get(Project, job.project_id))
        if mjob is None
        else micro_job_snapshot(job, db.get(MicroWork, job.micro_id))
    )
    task_manager.publish_job(snap)
    return {"ok": True, "status": "cancelled"}


__all__ = [
    "active_snapshots",
    "micro_job_snapshot",
    "project_job_snapshot",
    "router",
    "snapshots_for",
]
