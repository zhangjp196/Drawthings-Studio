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
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ImageUrl

from config import data_dir
from i18n import L
from models import Asset, MicroMessage
from services.agent import (build_model, image_data_uri, make_agent,
                            to_message_history, user_prompt, ScoreOut)
from services.api_common import _media_url
from services.capabilities import caps, dt_client
from services.drawthings import MAX_VIDEO_SECONDS, extract_last_frame
from services.media_files import media_path_from_url
from services.micro_parts import dump_parts, load_parts
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
            "（角色、服装、画风、光照、构图），再具体描述用户本次要求的改动；不要从头重新描述整个画面。"
            "若要参考本会话中更早的某张已生成媒体，给 generate_media 传 ref_index（1=最近一张，2=倒数第二张…）。")
    return instructions


def latest_session_media_path(db, session_id: str) -> str | None:
    """本会话最近一次生成的媒体磁盘路径（无参考图时的回退参考；跨轮次也生效）。

    优先查资产表（Asset Graph，含参数与类型）；旧数据无资产时回退按消息扫描。
    """
    try:
        a = (db.query(Asset)
             .filter(Asset.session_id == session_id, Asset.url.isnot(None), Asset.url != "")
             .order_by(Asset.id.desc()).first())
        if a is not None:
            p = media_path_from_url(a.url or "")
            if p:
                return str(p)
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


def asset_path_by_recency(db, session_id: str, n: int) -> str | None:
    """本会话第 n 近的生成资产（1=最近）的磁盘路径；用于「参考更早的某张图」（tool 编排）。"""
    if not n or n < 1:
        return None
    try:
        a = (db.query(Asset)
             .filter(Asset.session_id == session_id, Asset.url.isnot(None), Asset.url != "")
             .order_by(Asset.id.desc()).offset(n - 1).first())
    except Exception:
        return None
    if not a:
        return None
    p = media_path_from_url(a.url or "")
    return str(p) if p else None


_VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".gif")


def is_video_path(path: str | None) -> bool:
    return bool(path) and Path(path).suffix.lower() in _VIDEO_SUFFIXES


def image_ref_path(ref: str | None) -> str | None:
    """图片生成用的参考路径：视频 → 取末帧图；图片 → 原样（供「引用」任意媒体后做图生图）。"""
    if not ref:
        return None
    if is_video_path(ref):
        return extract_last_frame(ref, Path(data_dir) / "media") or ref
    return ref


_SCORE_SYSTEM = ("你是美术/视频审片。对给定的生成画面按 100 分制评估，只输出一个 JSON 对象，"
                 "字段：score（0–100 整数）与 note（一句话中文评语，不超过 30 字）。"
                 "评估维度：与提示词的相符度、构图与清晰度、风格/角色一致性、有无明显畸变或伪影。")


async def vlm_score_media(llm_cfg, image_path: str, prompt: str = "", lang: str = "zh") -> tuple[int, str]:
    """用作品所选 VLM 对一张生成画面评分：返回 (score, note)。视频请先取末帧再传入。"""
    model = build_model(llm_cfg)
    agent = make_agent(model, _SCORE_SYSTEM, output_type=ScoreOut)
    if lang == "en":
        text = f"Prompt: {prompt or '(none)'}\nScore the image and give a one-line comment."
    else:
        text = f"生成提示词：{prompt or '（无）'}\n请评分并给出一句话评语。"
    content = [ImageUrl(url=image_data_uri(image_path)), text]
    data = (await agent.run(content)).output
    s = int(getattr(data, "score", 0) or 0)
    s = max(0, min(100, s))  # 收敛到 0–100
    return s, str(getattr(data, "note", "") or "").strip()


class _PromptOut(BaseModel):
    prompt: str = ""


_REFINE_SYSTEM = ("你是出图/出视频的提示词优化师。根据「原始英文提示词」与「评分评语」，"
                  "在保持主体、角色与风格一致的前提下，针对评语指出的问题给出改进后的英文提示词。"
                  "只输出一个 JSON 对象，字段：prompt（改进后的英文提示词）。")


async def refine_prompt(llm_cfg, prompt: str, score: int, note: str, lang: str = "zh") -> str:
    """结合评分评语改进提示词（失败则回退原提示词）。"""
    if not llm_cfg or not prompt:
        return prompt
    try:
        model = build_model(llm_cfg)
        agent = make_agent(model, _REFINE_SYSTEM, output_type=_PromptOut)
        user = (f"原始提示词：{prompt}\n评分：{int(score or 0)}\n评语：{note or '（无）'}\n"
                f"请给出改进后的英文提示词。")
        out = (await agent.run(user)).output
        p = str(getattr(out, "prompt", "") or "").strip()
        return p or prompt
    except Exception:
        return prompt


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

    def _merge_scores(self, m) -> None:
        """把已落库的分值/评语合并进本次要写的 parts（按媒体 url 匹配）。

        防止「生成进行中对已出现的媒体评分 → 本次落库把分值覆盖掉」。
        """
        try:
            if not (m is not None and getattr(m, "parts", None)):
                return
            prev = load_parts(m.parts)
            by_url = {b.get("url"): (int(b.get("score") or 0), b.get("score_note") or "")
                      for b in prev if b.get("type") == "tool" and b.get("url")}
            if not by_url:
                return
            for b in self.parts:
                if b.get("type") == "tool" and b.get("url") in by_url and not b.get("score"):
                    b["score"], b["score_note"] = by_url[b["url"]]
        except Exception:
            pass

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
        self._merge_scores(m)  # 保留评分期间由「评分」接口写入的分值/评语，避免被本次覆盖
        m.content = text
        m.parts = dump_parts(self.parts)
        m.duration = round(time.monotonic() - self.t0, 1)
        m.media_url = self.last_media.get("url", "")
        m.prompt = self.last_media.get("prompt", "")
        m.status = status
        self._sync_assets(m.id)  # 资产图：把成功的生成写入 assets（幂等 upsert）
        self.db.commit()

    def _sync_assets(self, message_id) -> None:
        """把消息里成功的生成块 upsert 为资产（Asset Graph），按 (message_id, block_id) 幂等。"""
        if not message_id:
            return
        try:
            for b in self.parts:
                if b.get("type") != "tool" or b.get("status") != "ok" or not b.get("url"):
                    continue
                bid = str(b.get("id") or "")
                a = (self.db.query(Asset)
                     .filter(Asset.message_id == message_id, Asset.block_id == bid).first())
                if a is None:
                    a = Asset(micro_id=self.session.micro_id, session_id=self.session.id,
                              message_id=message_id, block_id=bid, created_at=_now())
                    self.db.add(a)
                a.kind = str(b.get("media") or "image")
                a.url = str(b.get("url") or "")
                a.prompt = str(b.get("prompt") or "")
                a.model = str(b.get("model") or "")
                a.width = int(b.get("width") or 0)
                a.height = int(b.get("height") or 0)
                a.seconds = int(b.get("seconds") or 0)
                a.ref_url = str(b.get("ref_url") or "")
        except Exception:
            # 资产写入失败不应影响对话落库
            pass

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
                         assistant_id: int | None = None, quoted_ref: str | None = None) -> None:
    """执行一次微创作对话：事件写入 `out`，增量落库，结束发 `(E.EOF, None)`。

    `assistant_id` = 已建好的助手草稿消息 id（可恢复流）；None 时首次写入自动创建。
    `quoted_ref` = 用户在会话里右键「引用」的媒体磁盘路径（图/视频均可）；作为本轮生成的
    最高优先级参考（视频在生成图片时自动取末帧）。
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
                                         width: int = 0, height: int = 0, seconds: int = 0,
                                         ref_index: int = 0) -> str:
                    """生成图片或视频：根据详细英文提示词产出单张图或单个视频。

                    若用户当前消息附带了图片，会自动作为参考图做图生图 / 图生视频（受配置「支持参考图片」开关
                    控制，未开启时按文生图 / 文生视频）；无附图时回退使用本会话最近一次生成的媒体作参考。
                    若用户想参考本会话中**更早的某张已生成媒体**，用 ref_index 指定（1=最近一张，2=倒数第二张…）。

                    Args:
                        prompt: 详细英文提示词（主体、场景、构图、光线、风格；视频补充运镜与动态）
                        media: 产出类型："image" 生成图片 / "video" 生成视频（用户未明确时可留空）
                        width: 图片宽（64 的倍数；用户未指定比例时传 0）
                        height: 图片高（64 的倍数；用户未指定比例时传 0）
                        seconds: 视频时长（秒，1~上限；用户未指定时传 0 = 用配置上限）
                        ref_index: 参考本会话倒数第几张生成媒体（0/1=最近一张；>1 更早；无附图时生效）
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
                    # 参考优先级：右键「引用」的媒体 > 本条消息附图 > 历史资产(ref_index) > 本会话最近生成的媒体
                    ref = quoted_ref or (img_paths[-1] if img_paths else None)
                    if ref is None and ref_index and int(ref_index) > 1:
                        ref = asset_path_by_recency(db, session.id, int(ref_index))
                    if ref is None:
                        ref = last_media.get("path") or latest_session_media_path(db, session.id)
                    if kind == "image":
                        ref = image_ref_path(ref)  # 引用视频做图生图 → 取末帧
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
                         lang: str = "zh", cancel_event=None,
                         llm_cfg=None, improve: bool = False,
                         score: int = 0, note: str = "") -> None:
    """一键重跑：跳过 LLM，按内容块保存的参数直接重生成（结果可复现）。

    `ref_url` = 原生成所用的参考图（/media/xxx）；空则回退本会话最近生成的媒体。
    `improve=True`（重做）：先用 LLM 结合评分评语改进提示词，再按原参数重生成；
    始终新建助手消息，**不删除原来的内容**。
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
        # 重做：结合评分评语用 LLM 改进提示词（失败回退原提示词）
        if improve:
            prompt = await refine_prompt(llm_cfg, prompt, score, note, lang)
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
           "run_micro_chat", "run_regenerate", "vlm_score_media"]
