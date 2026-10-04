"""长任务取消基础设施（类型无关，供漫画 / 短剧 / 微创作共用）。

- `JobCancelled`：协作式取消时抛出的异常（任务循环在安全点检查并抛出）；
- `JobControl`：一次长任务的取消句柄，持有 `threading.Event`（可赋给 DrawThingsClient.cancel_event，
  让正在跑的生图/生视频尽快停止），并提供 `check()` 供循环在章节边界检查；
- 进程内注册表：job_id → JobControl，供「取消接口」在不依赖客户端连接时置位。

进程重启后残留的 running 任务由启动清理标记 interrupted（见 db._mark_interrupted_streams）。
"""

import threading
from datetime import UTC, datetime, timedelta

_RUNNING: dict = {}
_LOCK = threading.Lock()


class JobCancelled(Exception):
    """任务被调用方主动取消。"""


class JobControl:
    """一次长任务的取消句柄。"""

    def __init__(self):
        self._ev = threading.Event()

    @property
    def event(self) -> threading.Event:
        return self._ev

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def cancel(self) -> None:
        self._ev.set()

    def check(self) -> None:
        """在安全点检查：已取消则抛 JobCancelled。"""
        if self._ev.is_set():
            raise JobCancelled()


def register(job_id: str) -> JobControl:
    ctl = JobControl()
    with _LOCK:
        _RUNNING[job_id] = ctl
    return ctl


def get(job_id: str) -> JobControl | None:
    with _LOCK:
        return _RUNNING.get(job_id)


def pop(job_id: str) -> JobControl | None:
    with _LOCK:
        return _RUNNING.pop(job_id, None)


def cancel(job_id: str) -> bool:
    """置位取消句柄；返回是否有运行中的任务被取消。"""
    with _LOCK:
        ctl = _RUNNING.get(job_id)
    if ctl is None:
        return False
    ctl.cancel()
    return True


def reap_stale(db, model, *, cutoff_minutes: int = 30) -> int:
    """把「running，但进程内已无取消句柄、且超时未更新」的任务标记为 interrupted。

    正常情况下任务结束都会落库。只有两条路径会留下幽灵 running：
    1. SSE 流还没产出第一个 chunk 客户端就断开——StreamingResponse 从不迭代生成器，
       生成器 finally（jobs.pop + 转 interrupted）根本不会执行；
    2. 进程被强杀（这个只能等重启时的 _mark_interrupted_streams 兜底）。
    因此任务列表接口每次顺带收割（updated_at 超时 + 进程内无句柄 → 必死任务），
    避免 UI 出现永远转圈的 running，也不用等重启。
    """
    marker = (datetime.now(UTC) - timedelta(minutes=cutoff_minutes)).isoformat()
    n = 0
    try:
        rows = (
            db.query(model)
            .filter(
                model.status == "running",
                model.updated_at != "",
                model.updated_at < marker,
            )
            .all()
        )
        for r in rows:
            if get(r.id) is not None:
                continue  # 进程内还有活动句柄：可能是超长任务，别动
            try:
                r.status = "interrupted"
                if not r.finished_at:
                    r.finished_at = datetime.now(UTC).isoformat()
            except Exception:
                pass
            n += 1
        if n:
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return n


__all__ = ["JobCancelled", "JobControl", "register", "get", "pop", "cancel", "reap_stale"]
