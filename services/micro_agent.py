"""微创作会话引擎：系统提示词拼装 + generate_media 工具 + 有序内容块 + 落库。

从 main.py 的 `_micro_stream` 抽出，与项目侧 `pipeline_comic/drama` 形成对称结构：
本模块只负责「一次对话的推理与生成」，传输（SSE 帧 / 心跳）由 `services/api_micro.py` 负责。

- `build_instructions(...)`：按生效能力（图片/视频/参考图）拼装系统提示词；
- `run_micro_chat(...)`：多轮对话（LLM 流式 + generate_media 工具调用）；
- `run_regenerate(...)`：一键重跑（跳过 LLM，按已保存参数直接重生成，结果可复现）；
- 两者都把事件写入 `asyncio.Queue`，并**增量落库**（可恢复流）：断连/崩溃时保留已产出的部分。

可恢复流：助手消息先建「草稿」（status=streaming），生成过程中增量写入文本与内容块，
完成置 done、中断置 interrupted；下次进入会话即可看到（可能不完整的）结果。
"""
import asyncio
import itertools
import time
from functools import partial

from pydantic_ai import Agent, RunContext

from config import data_dir
from i18n import L
from models import MicroMessage
from services.agent import build_model, to_message_history, user_prompt
from services.api_common import _media_url
from services.capabilities import caps, dt_client
from services.drawthings import MAX_VIDEO_SECONDS
from services.media_files import media_path_from_url
from services.micro_parts import dump_parts
from services.pipeline import _now, run_sync
from services import events as E

MC_SYSTEM = ("你是漫画/短剧创作的创意助手，擅长创意构思、角色与剧情设计、分镜和提示词，回答简洁、具体。"
             "用户只是提问时直接文本回答；用户要求生成图片/视频时，先调用 generate_media 工具"
             "（提供详细英文提示词：主体、场景、构图、光线、风格；视频补充运镜与动态），"
             "生成成功后用一两句话说明结果。"
             "始终用用户所用的语言回答（用户用中文提问则答中文，用英文提问则答英文）。")


def build_instructions(can_image: bool, can_video: bool, dt_cfg,
                       eff_ref_img: bool, eff_ref_vid: bool, lang: str = "zh") -> str:
    """按生效能力拼装系统提示词（生成类型 / 比例 / 时长 / 参考图模式 / 无生成服务）。"""
    if not (can_image or can_video):
        return (MC_SYSTEM + "当前未配置生成服务，无法出图/出视频：用户要求生成时，请说明暂时无法生成，"
                            "但可以代为撰写详细的英文提示词供其后续使用。")
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
    return instructions


def latest_session_media_path(db, session_id: str) -> str | None:
    """本会话最近一次生成的媒体磁盘路径（无参考图时的回退参考；跨轮次也生效）。"""
    try:
        m = (db.query(MicroMessage)
             .filter(MicroMessage.session_id == session_id,
                     MicroMessage.role == "assistant",
                     MicroMessage.media_url.isnot(None),
                     MicroMessage.media_url != "")
             .order_by(MicroMessage.index.desc()).first())
    except Exception:
        return None
    if not m:
        return None
    p = media_path_from_url(m.media_url or "")
    return str(p) if p else None


class _MsgPersister:
    """助手消息的增量落库（可恢复流）：草稿 → 流式更新 → done / interrupted。"""

    def __init__(self, db, session, parts, last_media, t0, assistant_idx, assistant_id=None):
        self.db = db
        self.session = session
        self.parts = parts
        self.last_media = last_media
        self.t0 = t0
        self.idx = assistant_idx
        self.id = assistant_id
        self.final = False
        self._last_save = 0.0

    def _text(self) -> str:
        return "".join(p.get("text", "") for p in self.parts if p.get("type") == "text").strip()

    def _write(self, status: str) -> None:
        text = self._text()
        m = self.db.get(MicroMessage, self.id) if self.id else None
        if m is None:
            if not (text or self.last_media.get("url") or status == "interrupted"):
                return
            m = MicroMessage(session_id=self.session.id, index=self.idx,
                             role="assistant", created_at=_now())
            self.db.add(m)
            self.db.flush()  # 取 id，供后续增量更新
            self.id = m.id
        m.content = text
        m.parts = dump_parts(self.parts)
        m.duration = round(time.monotonic() - self.t0, 1)
        m.media_url = self.last_media.get("url", "")
        m.prompt = self.last_media.get("prompt", "")
        m.status = status
        self.db.commit()

    def save(self, status: str, throttle: bool = False) -> None:
        """写入草稿；throttle=True 时最多每秒一次（用于逐 token 的高频更新）。"""
        now = time.monotonic()
        if throttle and now - self._last_save < 1.0:
            return
        self._last_save = now
        try:
            self._write(status)
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass

    def discard_if_empty(self) -> None:
        """空回复（无文本、无媒体、无工具块）：删除草稿，避免留下空消息。"""
        try:
            has = bool(self.last_media.get("url")) or any(
                p.get("type") in ("text", "tool") for p in self.parts)
            if self.id and not has:
                m = self.db.get(MicroMessage, self.id)
                if m is not None:
                    self.db.delete(m)
                    self.db.commit()
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass


async def _do_generation(out, *, dt, kind: str, prompt: str, width: int = 0, height: int = 0,
                         seconds: int = 0, ref: str | None, tid: str, lang: str,
                         parts: list, last_media: dict) -> str:
    """执行一次生成（图/视频），把 tool/tool_status/media/tool_error 事件写入 out。

    返回给 LLM 的结果文本（成功/失败），与提示词内容无关；生成参数快照写入内容块。
    """
    if kind == "image":
        label = L(lang, "正在生成图像…", "Generating image…")
    elif seconds:
        label = L(lang, f"正在生成视频（约 {int(seconds)} 秒）…", f"Generating video (~{int(seconds)}s)…")
    else:
        label = L(lang, "正在生成视频…", "Generating video…")
    block = {"type": "tool", "id": tid, "label": label, "prompt": prompt,
             "status": "running", "media": kind, "url": "", "message": "",
             # 参数快照（可复现 / 一键重跑）
             "model": (dt.model_video if kind == "video" else dt.model_image) or "",
             "width": int(width or 0), "height": int(height or 0), "seconds": int(seconds or 0),
             "ref_url": _media_url(ref) if ref else ""}
    parts.append(block)
    await out.put((E.TOOL, {"id": tid, "label": label, "prompt": prompt, "media": kind}))
    params = {}
    if width and height:
        params = {"width": int(width), "height": int(height)}
    if kind == "video" and seconds:
        params["seconds"] = int(seconds)
    # 生成状态实时透传（如「正在等待 Draw Things 恢复…」）→ 前端工具气泡
    _loop = asyncio.get_running_loop()
    dt.on_status = lambda msg: _loop.call_soon_threadsafe(
        out.put_nowait, (E.TOOL_STATUS, {"id": tid, "message": msg}))
    try:
        if kind == "image":
            path = await run_sync(partial(dt.generate_image, prompt, ref_path=ref, params=params))
        else:
            path = await run_sync(partial(dt.generate_video, prompt, ref_video_path=ref, params=params))
    except Exception as e:
        block["status"] = "error"
        block["message"] = str(e)
        await out.put((E.TOOL_ERROR, {"id": tid, "message": str(e), "prompt": prompt}))
        return f"生成失败：{e}。请向用户说明原因并建议如何调整。"
    finally:
        dt.on_status = None
    url = _media_url(path)
    block["status"] = "ok"
    block["url"] = url
    last_media["url"] = url
    last_media["prompt"] = prompt
    last_media["path"] = path
    await out.put((E.MEDIA, {"id": tid, "media": kind, "url": url, "prompt": prompt}))
    return f"生成成功，媒体地址：{url}"


async def run_micro_chat(out: asyncio.Queue, *, db, session, work, llm_cfg, dt_cfg,
                         img_paths: list[str], user_message: str, history: list[dict],
                         assistant_idx: int, lang: str = "zh", cancel_event=None,
                         assistant_id: int | None = None) -> None:
    """执行一次微创作对话：事件写入 `out`，增量落库，结束发 `(E.EOF, None)`。

    `assistant_id` = 已建好的助手草稿消息 id（可恢复流）；None 时首次写入自动创建。
    """
    t0 = time.monotonic()
    parts: list[dict] = []
    tool_ids = itertools.count(1)
    last_media: dict = {}
    persister = _MsgPersister(db, session, parts, last_media, t0, assistant_idx, assistant_id)

    if not llm_cfg:
        msg = L(lang, "请选择有效的 VLM 配置", "Please select a valid VLM config")
        parts.append({"type": "error", "message": msg})
        persister.save("interrupted")
        persister.final = True
        await out.put((E.ERROR, {"message": msg}))
        await out.put((E.DONE, {}))
        await out.put((E.EOF, None))
        return

    try:
        # DrawThings 客户端（gRPC）+ 生效能力：功能级模型/参考图开关优先（作品自选），空 = 跟随配置
        dt = dt_client(dt_cfg, data_dir, work)
        if dt is not None:
            dt.cancel_event = cancel_event  # 调用方中断时协作式停止生成
        _caps = caps(dt, dt_cfg, work)
        can_image, can_video = _caps.can_image, _caps.can_video
        eff_ref_img, eff_ref_vid = _caps.ref_image, _caps.ref_video

        instructions = build_instructions(can_image, can_video, dt_cfg,
                                          eff_ref_img, eff_ref_vid, lang)
        model = build_model(llm_cfg)
        agent = Agent(model, instructions=instructions)

        def _add_text(delta: str) -> None:
            """追加文本增量：与上一块同为文本则合并，否则新起一个文本块（保证与生成块的相对顺序）。"""
            if parts and parts[-1].get("type") == "text":
                parts[-1]["text"] = parts[-1].get("text", "") + delta
            else:
                parts.append({"type": "text", "text": delta})

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
                        await out.put((E.TOOL_ERROR, {"id": f"t{next(tool_ids)}", "message": msg, "prompt": prompt}))
                        return f"生成失败：{msg}"
                    tid = f"t{next(tool_ids)}"
                    # 参考图：当前消息附图优先，回退本会话最近生成的媒体（跨轮次生效）
                    ref = img_paths[-1] if img_paths else (last_media.get("path")
                                                           or latest_session_media_path(db, session.id))
                    result = await _do_generation(
                        out, dt=dt, kind=kind, prompt=prompt, width=width, height=height,
                        seconds=seconds, ref=ref, tid=tid, lang=lang,
                        parts=parts, last_media=last_media)
                    persister.save("streaming")  # 生成的里程碑立即落库（断连可恢复）
                    return result

            async with agent.run_stream(user_prompt(user_message, img_paths),
                                        message_history=to_message_history(history)) as result:
                async for text in result.stream_text(delta=True, debounce_by=None):
                    _add_text(text)
                    await out.put((E.TOKEN, {"text": text}))
                    persister.save("streaming", throttle=True)  # 节流增量落库（≈1s）
                await result.get_output()
        persister.discard_if_empty()
        persister.save("done")
        persister.final = True
        await out.put((E.DONE, {}))
    except asyncio.CancelledError:
        persister.final = True
        persister.save("interrupted")  # 断连/取消：保留部分输出
        raise
    except Exception as e:
        persister.final = True
        msg = L(lang, f"对话失败：{e}", f"Chat failed: {e}")
        parts.append({"type": "error", "message": msg})
        persister.save("interrupted")
        await out.put((E.ERROR, {"message": msg}))
        await out.put((E.DONE, {}))
    finally:
        if not persister.final:
            persister.save("interrupted")
        try:
            await out.put((E.EOF, None))
        except BaseException:
            pass


async def run_regenerate(out: asyncio.Queue, *, db, session, work, dt_cfg,
                         assistant_id: int, kind: str, prompt: str, width: int = 0,
                         height: int = 0, seconds: int = 0, ref_url: str = "",
                         lang: str = "zh", cancel_event=None) -> None:
    """一键重跑：跳过 LLM，按内容块保存的参数直接重生成（结果可复现）。

    `ref_url` = 原生成所用的参考图（/media/xxx）；空则回退本会话最近生成的媒体。
    """
    t0 = time.monotonic()
    parts: list[dict] = []
    last_media: dict = {}
    persister = _MsgPersister(db, session, parts, last_media, t0, 0, assistant_id)

    try:
        dt = dt_client(dt_cfg, data_dir, work)
        if dt is not None:
            dt.cancel_event = cancel_event
        _caps = caps(dt, dt_cfg, work)
        can_image, can_video = _caps.can_image, _caps.can_video
        if dt is None or (kind == "video" and not can_video) or (kind == "image" and not can_image):
            name = "视频" if kind == "video" else "图像"
            msg = L(lang, f"未配置{name}模型：请在 DrawThings 配置里填写{name}模型。",
                    f"No {'video' if kind == 'video' else 'image'} model configured — set one in the DrawThings config.")
            parts.append({"type": "error", "message": msg})
            persister.save("interrupted")
            persister.final = True
            await out.put((E.ERROR, {"message": msg}))
            await out.put((E.DONE, {}))
            return
        ref = media_path_from_url(ref_url) if ref_url else None
        if ref is None:
            p = latest_session_media_path(db, session.id)
            ref = p
        await _do_generation(out, dt=dt, kind=kind, prompt=prompt, width=width, height=height,
                             seconds=seconds, ref=str(ref) if ref else None, tid="t1",
                             lang=lang, parts=parts, last_media=last_media)
        persister.save("done" if last_media.get("url") else "interrupted")
        persister.final = True
        await out.put((E.DONE, {}))
    except asyncio.CancelledError:
        persister.final = True
        persister.save("interrupted")
        raise
    except Exception as e:
        persister.final = True
        msg = L(lang, f"重跑失败：{e}", f"Re-generate failed: {e}")
        parts.append({"type": "error", "message": msg})
        persister.save("interrupted")
        await out.put((E.ERROR, {"message": msg}))
        await out.put((E.DONE, {}))
    finally:
        if not persister.final:
            persister.save("interrupted")
        try:
            await out.put((E.EOF, None))
        except BaseException:
            pass


__all__ = ["MC_SYSTEM", "build_instructions", "latest_session_media_path",
           "run_micro_chat", "run_regenerate"]
