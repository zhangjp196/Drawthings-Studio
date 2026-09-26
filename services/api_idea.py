"""新建创作「AI 生成标题/主题」API（类型无关，漫画/短剧共用）：/api/idea/chat（SSE）。

尚未创建项目，故不落库：用户所选 LLM 配置 + 前端持有的多轮历史随请求带回。
"""
import asyncio
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from config_store import ConfigStore
from db import get_db
from i18n import L
from services.api_common import _json_body, _lang, _sse
from services.idea_agent import run_idea_chat
from services import events as E

router = APIRouter(prefix="/api/idea", tags=["idea"])


@router.post("/chat")
async def idea_chat(request: Request, db: Session = Depends(get_db)):
    """新建创作：与所选 LLM 多轮对话（流式），模型可提问并通过 set_fields 写回标题/主题。

    body: { llm_config_id, message, history:[{role,content}], kind:'comic'|'drama' }
    事件：token / fields / error / done。
    """
    lang = _lang(request)
    body = await _json_body(request)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400,
                            detail=L(lang, "消息不能为空", "Message cannot be empty"))
    hist = body.get("history") or []
    if not isinstance(hist, list):
        hist = []
    # 只保留 user/assistant 文本，防御前端脏数据
    clean_hist = []
    for m in hist[-20:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        c = str(m.get("content") or "")
        if role in ("user", "assistant") and c:
            clean_hist.append({"role": role, "content": c})
    llm_id = str(body.get("llm_config_id") or "")
    llm_cfg = ConfigStore(db).get_llm(llm_id) if llm_id else None
    kind = str(body.get("kind") or "comic")

    def _runner(queue, cancel_event):
        return run_idea_chat(queue, llm_cfg=llm_cfg, kind=kind,
                             user_message=message, history=clean_hist, lang=lang)

    return StreamingResponse(
        _idea_stream(_runner),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _idea_stream(runner):
    """SSE 传输层：驱动会话引擎的事件队列 → SSE 帧（含心跳）。"""
    queue: asyncio.Queue = asyncio.Queue()
    cancel_event = threading.Event()
    task = asyncio.create_task(runner(queue, cancel_event))
    try:
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # 心跳：保持连接
                continue
            if event == E.EOF:
                break
            yield _sse(event, data)
    finally:
        cancel_event.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
