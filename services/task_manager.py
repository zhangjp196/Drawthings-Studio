"""进程内后台任务运行器 + 事件广播（与业务类型无关）。

Phase 2 起，长任务（逐章生成/规划/批量评分/单章生成、微创作对话/重跑/评分）不再绑定 HTTP 连接：

- `spawn(job_id, coro)`：把协程放到事件循环后台执行，请求立即返回 `{job_id}`；
  切页 / 刷新 / 断网都不再取消任务。
- `publish(job_id, event, data)`：任务事件广播给所有 `/ws` 订阅者（`services/events.py` 契约不变）；
  没有订阅者时是空操作，任务照跑、状态照常落库。
- `publish_job(snapshot)`：任务状态变化广播（任务中心实时刷新）。
- `EventSink`：适配旧引擎的 `out.put((event, data))` 协议，把事件转成广播（微创作会话流使用）。

取消仍由 `services/jobs.py` 的 `JobControl`（threading.Event）负责；本模块只负责调度与广播。
进程退出时 `shutdown()` 取消所有后台任务（状态由各自的 finally 落库）。
"""

import asyncio
import logging

from services import events as E

log = logging.getLogger("drawthings.tasks")

_subscribers: set[asyncio.Queue] = set()
_tasks: dict[str, asyncio.Task] = {}


def spawn(job_id: str, coro) -> asyncio.Task:
    """后台启动一个任务协程（不等待结果）。"""
    task = asyncio.get_running_loop().create_task(coro, name=f"job-{job_id}")
    _tasks[job_id] = task

    def _done(t: asyncio.Task) -> None:
        _tasks.pop(job_id, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:  # 任务自身已落库 error；这里只记录，避免静默丢失
            log.error("后台任务 %s 异常退出", job_id, exc_info=exc)

    task.add_done_callback(_done)
    return task


def running(job_id: str) -> bool:
    return job_id in _tasks


def active_ids() -> list[str]:
    return list(_tasks)


# ---------------- 订阅与广播 ----------------


def subscribe() -> asyncio.Queue:
    """注册一个事件队列（每个 /ws 连接一个）。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _broadcast(msg: dict) -> None:
    for q in tuple(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            # 慢客户端：丢弃事件而不是阻塞任务（任务中心随后仍能拿到状态快照）
            pass


def publish(job_id: str, event: str, data: dict | None = None) -> None:
    """广播一条任务事件（event 名见 services/events.py）。"""
    _broadcast({"type": "event", "job": job_id, "event": event, "data": data or {}})


def publish_job(snapshot: dict | None) -> None:
    """广播任务快照（状态/进度变化时调用）。"""
    if snapshot:
        _broadcast({"type": "job", "job": snapshot})


async def shutdown() -> None:
    """进程退出：取消所有后台任务并等待其 finally 落库。"""
    tasks = list(_tasks.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _subscribers.clear()


class EventSink:
    """把 `put((event, data))` 协议适配到 WS 广播（供微创作引擎复用，不改变其签名）。"""

    def __init__(self, job_id: str, on_event=None):
        self.job_id = job_id
        self.error = ""  # 最近一次错误文案（任务结束时用于落库）
        self.done = False
        self._on_event = on_event  # 可选：同步回调（用于把事件摘要写进任务 note）

    async def put(self, item) -> None:
        event, data = item if isinstance(item, tuple) else (item, None)
        if event == E.EOF:
            return
        d = data or {}
        if event in (E.ERROR, E.TOOL_ERROR):
            msg = str(d.get("message") or "")
            if msg:
                self.error = msg
        elif event == E.DONE:
            self.done = True
        publish(self.job_id, event, d)
        if self._on_event is not None:
            try:
                self._on_event(event, d)
            except Exception:  # 回调只用于展示，不影响主流程
                pass


__all__ = [
    "EventSink",
    "active_ids",
    "publish",
    "publish_job",
    "running",
    "shutdown",
    "spawn",
    "subscribe",
    "unsubscribe",
]
