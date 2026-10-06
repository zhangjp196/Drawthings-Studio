"""微创作会话引擎：系统提示词拼装 + generate_image / generate_video 工具 + 有序内容块 + 落库。

从 main.py 的 `_micro_stream` 抽出，与项目侧 `pipeline_comic/drama` 形成对称结构：
本模块只负责「一次对话的推理与生成」，传输（SSE 帧 / 心跳）由 `services/api_micro.py` 负责。

- `build_instructions(...)`：按生效能力（图片/视频/参考图）拼装系统提示词；
- `run_micro_chat(...)`：多轮对话（LLM 流式 + generate_image / generate_video 工具调用）；
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
from services import events as E
from services.agent import ScoreOut, build_model, image_data_uri, make_agent, to_message_history, user_prompt
from services.api_common import _media_url
from services.capabilities import caps, dt_client, prompt_language
from services.drawthings import MAX_VIDEO_SECONDS, MODEL_NONE, extract_last_frame
from services.media_files import media_path_from_url
from services.micro_parts import dump_parts, load_parts
from services.pipeline import _now, run_sync

MC_SYSTEM = (
    "你是漫画/短剧创作的创意助手，擅长创意构思、角色与剧情设计、分镜和提示词，回答简洁、具体。"
    "用户只是提问时直接文本回答；用户要求生成图片/视频时，调用对应的生成工具"
    "（提供详细提示词：主体、场景、构图、光线、风格；视频补充运镜与动态；语言按下方要求），"
    "生成成功后用一两句话说明结果。"
    "始终用用户所用的语言回答（用户用中文提问则答中文，用英文提问则答英文）。"
)


def build_instructions(
    can_image: bool,
    can_video: bool,
    dt_cfg,
    eff_ref_img: bool,
    eff_ref_vid: bool,
    lang: str = "zh",
    max_side: int = 0,
    max_seconds: int = 0,
    prompt_lang_image: str = "en",
    prompt_lang_video: str = "en",
) -> str:
    """按生效能力拼装系统提示词（生成类型 / 提示词语言 / 比例 / 时长 / 参考图模式 / 无生成服务）。

    `max_side` / `max_seconds` = 功能级上限（作品的dt_max_side / dt_max_seconds），
    已从 DrawThings 连接配置移到功能级，故由调用方传入而非读 dt_cfg。
    `prompt_lang_image` / `prompt_lang_video` = 图像 / 视频各自生效的提示词语言（'zh' | 'en'），
    由调用方用 `prompt_language(dt_cfg, kind, lang)` 解析后传入。
    """
    if not (can_image or can_video):
        return (
            MC_SYSTEM + "当前不出图/出视频：用户要求生成时，请说明暂时无法生成，"
            "但可以代为撰写详细的提示词供其后续使用。"
        )
    kinds = []
    if can_image:
        kinds.append("图片")
    if can_video:
        kinds.append("视频")
    avail = "、".join(kinds)
    # 独立工具：图片走 generate_image、视频走 generate_video（不再用 media 参数区分）
    if can_image and can_video:
        tool_hint = (
            f"当前可生成：{avail}。生成图片调用 generate_image 工具，生成视频调用 generate_video 工具"
            f"（用户未明确要图还是视频时默认生成视频）。"
        )
    elif can_image:
        tool_hint = f"当前可生成：{avail}。生成图片调用 generate_image 工具。"
    else:
        tool_hint = f"当前可生成：{avail}。生成视频调用 generate_video 工具。"
    # 提示词语言（按模型分开）：图像 / 视频可各自不同
    img_pl = "中文" if prompt_lang_image == "zh" else "英文"
    vid_pl = "中文" if prompt_lang_video == "zh" else "英文"
    if can_image and can_video:
        lang_hint = f"提示词语言：generate_image 用{img_pl}，generate_video 用{vid_pl}。"
    elif can_image:
        lang_hint = f"提示词语言：generate_image 用{img_pl}。"
    else:
        lang_hint = f"提示词语言：generate_video 用{vid_pl}。"
    ratio = ""
    if can_image:
        limit = int(max_side or 0) or 1024
        ratio = (
            f"生成图片时：用户指定比例或用途（海报 / 手机壁纸 / 横屏 / 竖屏 / 方形等）时，"
            f"换算成具体宽高传给 generate_image 的 width/height（均为 64 的倍数，最长边 ≤ {limit}；"
            f"参考：1:1=768×768、3:4 竖=576×768、4:3 横=768×576、9:16 竖=576×1024、16:9 横=1024×576）；"
            f"用户未指定时 width/height 传 0。"
        )
    sec_hint = ""
    if can_video:
        cap = int(max_seconds or 0) or MAX_VIDEO_SECONDS
        cap = min(cap, MAX_VIDEO_SECONDS)
        sec_hint = (
            f"生成视频时用 generate_video 的 seconds 参数指定时长（秒，1~{cap}；用户未指定时传 0 = 用 {cap} 秒），"
            f"单段视频最长 {cap} 秒。"
        )
    instructions = MC_SYSTEM + tool_hint + lang_hint + sec_hint + ratio
    # 参考图模式：勾选「支持参考图片」后，附图 / 最近生成的媒体会作为参考图 →
    # 提示词写成基于参考图的修改指令，而不是从头完整描述
    if eff_ref_img or eff_ref_vid:
        instructions += (
            "参考图模式：附图（无附图时为本会话最近一次生成的媒体）将作为图生图 / 图生视频的参考图。"
            "此时 prompt 必须写成针对参考图的修改指令：先用一句话点明需与参考图保持一致的元素"
            "（角色、服装、画风、光照、构图），再具体描述用户本次要求的改动；不要从头重新描述整个画面。"
            "若要参考本会话中更早的某张已生成媒体，给 generate_image / generate_video 传 ref_index"
            "（1=最近一张，2=倒数第二张…）。"
        )
    return instructions


def _no_cap_msg(kind: str, subject, lang: str = "zh") -> str:
    """说明「这一类生成不了」的原因：区分功能级显式「不启用」与 DrawThings 配置里真没配模型。"""
    name = "视频" if kind == "video" else "图像"
    attr = "dt_model_video" if kind == "video" else "dt_model_image"
    if str(getattr(subject, attr, "") or "").strip() == MODEL_NONE:
        return L(
            lang,
            f"已禁用{name}生成：该模型在配置设置里选了「不启用」。",
            f'{name} generation is disabled — that model is set to "Disabled".',
        )
    return L(
        lang,
        f"未配置{name}模型：请在 DrawThings 配置里填写{name}模型。",
        f"No {kind} model configured — set one in the DrawThings config.",
    )


def latest_session_media_path(db, session_id: str) -> str | None:
    """本会话最近一次生成的媒体磁盘路径（无参考图时的回退参考；跨轮次也生效）。

    优先查资产表（Asset Graph，含参数与类型）；旧数据无资产时回退按消息扫描。
    """
    try:
        a = (
            db.query(Asset)
            .filter(Asset.session_id == session_id, Asset.url.isnot(None), Asset.url != "")
            .order_by(Asset.id.desc())
            .first()
        )
        if a is not None:
            p = media_path_from_url(a.url or "")
            if p:
                return str(p)
        m = (
            db.query(MicroMessage)
            .filter(
                MicroMessage.session_id == session_id,
                MicroMessage.role == "assistant",
                MicroMessage.media_url.isnot(None),
                MicroMessage.media_url != "",
            )
            .order_by(MicroMessage.index.desc())
            .first()
        )
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
        a = (
            db.query(Asset)
            .filter(Asset.session_id == session_id, Asset.url.isnot(None), Asset.url != "")
            .order_by(Asset.id.desc())
            .offset(n - 1)
            .first()
        )
    except Exception:
        return None
    if not a:
        return None
    p = media_path_from_url(a.url or "")
    return str(p) if p else None


_VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".gif")


def is_video_path(path: str | None) -> bool:
    return bool(path) and Path(path).suffix.lower() in _VIDEO_SUFFIXES


async def image_ref_path_async(ref: str | None) -> str | None:
    """图片生成用的参考路径：视频 → 取末帧图；图片 → 原样（供「引用」任意媒体后做图生图）。

    抽帧（OpenCV 解码 / ffmpeg，自身最长 60s 超时）必须放线程池，否则会阻塞事件循环。
    """
    if not ref:
        return None
    if is_video_path(ref):
        return await run_sync(partial(extract_last_frame, ref)) or ref
    return ref


_SCORE_SYSTEM_IMAGE = (
    "你是美术/视频审片。**仅根据画面本身评估，不要参考任何文字提示词或描述**。"
    "按 100 分制打分，只输出一个 JSON 对象，字段：score（0–100 整数）与 note（一句话中文评语，不超过 30 字）。"
    "评估维度：构图、光影、清晰度、结构与人体合理性、画面/风格一致性、有无明显畸变或伪影、整体观感。"
)
_SCORE_SYSTEM_PROMPT = (
    "你是美术/视频审片。请**对照生成提示词**评估画面：先判断是否呈现了提示词的关键元素与意图，"
    "再看构图、光影、清晰度、一致性、有无畸变。按 100 分制打分，只输出一个 JSON 对象，"
    "字段：score（0–100 整数）与 note（一句话中文评语，不超过 30 字）。"
)


def norm_score_mode(v) -> str:
    """评分依据：'prompt'=结合提示词相符度；其余（含空）=仅评画面（忽略提示词）。"""
    return "prompt" if str(v or "").strip().lower() == "prompt" else "image"


async def vlm_score_media(
    llm_cfg, image_path: str, lang: str = "zh", mode: str = "image", prompt: str = ""
) -> tuple[int, str]:
    """用作品所选 VLM 评分：返回 (score, note)。

    mode='image'（默认）：仅评画面本身（忽略生成提示词）；mode='prompt'：结合提示词评相符度。
    视频请先取末帧再传入。
    """
    use_prompt = norm_score_mode(mode) == "prompt"
    model = build_model(llm_cfg)
    agent = make_agent(
        model, _SCORE_SYSTEM_PROMPT if use_prompt else _SCORE_SYSTEM_IMAGE, output_type=ScoreOut
    )
    if use_prompt:
        text = (
            f"Prompt: {prompt or '(none)'}\nEvaluate how well the image matches the prompt and its quality; "
            f"give a score and a one-line comment."
            if lang == "en"
            else f"生成提示词：{prompt or '（无）'}\n请对照提示词评估画面并给出评分与一句话评语。"
        )
    else:
        text = (
            "Evaluate the image quality by the image alone (do not consider any prompt/text), then give a score "
            "and a one-line comment."
            if lang == "en"
            else "请仅根据画面本身评价质量并给出评分与一句话评语。"
        )
    content = [ImageUrl(url=await run_sync(image_data_uri, image_path)), text]
    data = (await agent.run(content)).output
    s = int(getattr(data, "score", 0) or 0)
    s = max(0, min(100, s))  # 收敛到 0–100
    return s, str(getattr(data, "note", "") or "").strip()


class _PromptOut(BaseModel):
    prompt: str = ""


def _refine_system(prompt_lang: str = "en") -> str:
    """重做（改进提示词）的系统提示词；提示词语言与被改进的块保持一致（zh|en）。"""
    pl = "中文" if prompt_lang == "zh" else "英文"
    return (
        f"你是出图/出视频的提示词优化师。根据「原始{pl}提示词」与「评分评语」，"
        f"在保持主体、角色与风格一致的前提下，针对评语指出的问题给出改进后的{pl}提示词。"
        f"只输出一个 JSON 对象，字段：prompt（改进后的{pl}提示词）。"
    )


async def refine_prompt(
    llm_cfg, prompt: str, score: int, note: str, lang: str = "zh", prompt_lang: str = "en"
) -> str:
    """结合评分评语改进提示词（失败则回退原提示词）。prompt_lang 决定改进后提示词的语言（zh|en）。"""
    if not llm_cfg or not prompt:
        return prompt
    try:
        model = build_model(llm_cfg)
        agent = make_agent(model, _refine_system(prompt_lang), output_type=_PromptOut)
        pl = "中文" if prompt_lang == "zh" else "英文"
        user = (
            f"原始提示词：{prompt}\n评分：{int(score or 0)}\n评语：{note or '（无）'}\n"
            f"请给出改进后的{pl}提示词。"
        )
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
            by_url = {
                b.get("url"): (int(b.get("score") or 0), b.get("score_note") or "")
                for b in prev
                if b.get("type") == "tool" and b.get("url")
            }
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
            m = MicroMessage(session_id=self.session.id, index=self.idx, role="assistant", created_at=_now())
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
                a = self.db.query(Asset).filter(Asset.message_id == message_id, Asset.block_id == bid).first()
                if a is None:
                    a = Asset(
                        micro_id=self.session.micro_id,
                        session_id=self.session.id,
                        message_id=message_id,
                        block_id=bid,
                        created_at=_now(),
                    )
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
                p.get("type") in ("text", "tool") for p in self.parts
            )
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


async def _do_generation(
    out,
    *,
    dt,
    kind: str,
    prompt: str,
    width: int = 0,
    height: int = 0,
    seconds: int = 0,
    ref: str | None,
    tid: str,
    lang: str,
    parts: list,
    last_media: dict,
    llm_cfg=None,
    score_mode: str = "image",
    auto_score: bool = False,
) -> str:
    """执行一次生成（图/视频），把 tool/tool_status/media/tool_error 事件写入 out。

    - 生成参数快照写入内容块；
    - 成功且提供了 llm_cfg 时**自动用该 VLM 评分**（图/视频；视频取末帧），评分依据由 `score_mode` 决定
      （'image'=仅画面忽略提示词；'prompt'=结合提示词相符度），评分写入内容块并经 `media_score` 事件下发
      （失败静默忽略，不影响生成）。
    返回给 LLM 的结果文本（成功/失败），与提示词内容无关。
    """
    if kind == "image":
        label = L(lang, "正在生成图像…", "Generating image…")
    elif seconds:
        label = L(lang, f"正在生成视频（约 {int(seconds)} 秒）…", f"Generating video (~{int(seconds)}s)…")
    else:
        label = L(lang, "正在生成视频…", "Generating video…")
    block = {
        "type": "tool",
        "id": tid,
        "label": label,
        "prompt": prompt,
        "status": "running",
        "media": kind,
        "url": "",
        "message": "",
        # 参数快照（可复现 / 一键重跑）
        "model": (dt.model_video if kind == "video" else dt.model_image) or "",
        "width": int(width or 0),
        "height": int(height or 0),
        "seconds": int(seconds or 0),
        "ref_url": _media_url(ref) if ref else "",
    }
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
        out.put_nowait, (E.TOOL_STATUS, {"id": tid, "message": msg})
    )
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
    # 自动评分已移除：生成后不再自动评分（手动点卡片上的「评分」按钮仍可用）
    return f"生成成功，媒体地址：{url}"


async def run_micro_chat(
    out: asyncio.Queue,
    *,
    db,
    session,
    work,
    llm_cfg,
    dt_cfg,
    img_paths: list[str],
    user_message: str,
    history: list[dict],
    assistant_idx: int,
    lang: str = "zh",
    cancel_event=None,
    assistant_id: int | None = None,
    quoted_ref: str | None = None,
) -> None:
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

        instructions = build_instructions(
            can_image,
            can_video,
            dt_cfg,
            eff_ref_img,
            eff_ref_vid,
            lang,
            max_side=int(getattr(work, "dt_max_side", 0) or 0),
            max_seconds=int(getattr(work, "dt_max_seconds", 0) or 0),
            prompt_lang_image=prompt_language(dt_cfg, "image", lang),
            prompt_lang_video=prompt_language(dt_cfg, "video", lang),
        )
        model = build_model(llm_cfg)
        agent = Agent(model, instructions=instructions)

        def _add_text(delta: str) -> None:
            """追加文本增量：与上一块同为文本则合并，否则新起一个文本块（保证与生成块的相对顺序）。"""
            if parts and parts[-1].get("type") == "text":
                parts[-1]["text"] = parts[-1].get("text", "") + delta
            else:
                parts.append({"type": "text", "text": delta})

        async with agent:
            # 图片 / 视频各用一个独立 function call；某一类「不启用」时该工具不注册，
            # 两类都「不启用」时一个都不注册 → 与其让模型去调一个必然失败的工具
            # （每次都返回 TOOL_ERROR），不如让它按纯对话处理。
            if dt and (can_image or can_video):

                async def _run_generation(
                    kind: str, prompt: str, *, width: int, height: int, seconds: int, ref_index: int
                ) -> str:
                    """两个工具共用的执行体：解析参考图 → 调 _do_generation → 落库里程碑。"""
                    tid = f"t{next(tool_ids)}"
                    # 参考优先级：右键「引用」的媒体 > 本条消息附图 > 历史资产(ref_index) > 本会话最近生成的媒体
                    ref = quoted_ref or (img_paths[-1] if img_paths else None)
                    if ref is None and ref_index and int(ref_index) > 1:
                        ref = asset_path_by_recency(db, session.id, int(ref_index))
                    if ref is None:
                        ref = last_media.get("path") or latest_session_media_path(db, session.id)
                    if kind == "image":
                        # 引用视频做图生图 → 取末帧（async 版：抽帧进线程池）
                        ref = await image_ref_path_async(ref)
                    result = await _do_generation(
                        out,
                        dt=dt,
                        kind=kind,
                        prompt=prompt,
                        width=width,
                        height=height,
                        seconds=seconds,
                        ref=ref,
                        tid=tid,
                        lang=lang,
                        parts=parts,
                        last_media=last_media,
                        llm_cfg=llm_cfg,
                        score_mode=norm_score_mode(getattr(work, "score_mode", "image")),
                        auto_score=bool(getattr(work, "auto_score", 0)),
                    )
                    persister.save("streaming")  # 生成的里程碑立即落库（断连可恢复）
                    return result

                if can_image:

                    @agent.tool
                    async def generate_image(
                        ctx: RunContext,
                        prompt: str,
                        width: int = 0,
                        height: int = 0,
                        ref_index: int = 0,
                    ) -> str:
                        """生成图片：根据详细提示词产出单张图片。

                        若用户当前消息附带了图片，会自动作为参考图做图生图（受配置「支持参考图片」开关
                        控制，未开启时按文生图）；无附图时回退使用本会话最近一次生成的媒体作参考。
                        若用户想参考本会话中**更早的某张已生成图片/视频**，用 ref_index 指定
                        （1=最近一张，2=倒数第二张…）。

                        Args:
                            prompt: 详细提示词（主体、场景、构图、光线、风格）；语言按系统要求
                            width: 图片宽（64 的倍数；用户未指定比例时传 0）
                            height: 图片高（64 的倍数；用户未指定比例时传 0）
                            ref_index: 参考本会话倒数第几张生成媒体（0/1=最近一张；>1 更早；无附图时生效）
                        """
                        return await _run_generation(
                            "image", prompt, width=width, height=height, seconds=0, ref_index=ref_index
                        )

                if can_video:

                    @agent.tool
                    async def generate_video(
                        ctx: RunContext,
                        prompt: str,
                        seconds: int = 0,
                        ref_index: int = 0,
                    ) -> str:
                        """生成视频：根据详细提示词产出单个视频。

                        若用户当前消息附带了图片，会自动作为参考图做图生视频（受配置「支持参考图片」开关
                        控制，未开启时按文生视频）；无附图时回退使用本会话最近一次生成的媒体作参考。
                        若用户想参考本会话中**更早的某张已生成媒体**，用 ref_index 指定
                        （1=最近一张，2=倒数第二张…）。

                        Args:
                            prompt: 详细提示词（主体、场景、构图、光线、风格；补充运镜与动态）；语言按系统要求
                            seconds: 视频时长（秒，1~上限；用户未指定时传 0 = 用配置上限）
                            ref_index: 参考本会话倒数第几张生成媒体（0/1=最近一张；>1 更早；无附图时生效）
                        """
                        return await _run_generation(
                            "video", prompt, width=0, height=0, seconds=seconds, ref_index=ref_index
                        )

            async with agent.run_stream(
                user_prompt(user_message, img_paths), message_history=to_message_history(history)
            ) as result:
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


async def run_regenerate(
    out: asyncio.Queue,
    *,
    db,
    session,
    work,
    dt_cfg,
    assistant_id: int,
    kind: str,
    prompt: str,
    width: int = 0,
    height: int = 0,
    seconds: int = 0,
    ref_url: str = "",
    lang: str = "zh",
    cancel_event=None,
    llm_cfg=None,
    improve: bool = False,
    score: int = 0,
    note: str = "",
) -> None:
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
            msg = _no_cap_msg(kind, work, lang)
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
        # 重做：结合评分评语用 LLM 改进提示词（失败回退原提示词）；语言与该块一致
        if improve:
            prompt = await refine_prompt(
                llm_cfg, prompt, score, note, lang, prompt_language(dt_cfg, kind, lang)
            )
        await _do_generation(
            out,
            dt=dt,
            kind=kind,
            prompt=prompt,
            width=width,
            height=height,
            seconds=seconds,
            ref=str(ref) if ref else None,
            tid="t1",
            lang=lang,
            parts=parts,
            last_media=last_media,
            llm_cfg=llm_cfg,
            score_mode=norm_score_mode(getattr(work, "score_mode", "image")),
            auto_score=bool(getattr(work, "auto_score", 0)),
        )
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


__all__ = [
    "MC_SYSTEM",
    "build_instructions",
    "latest_session_media_path",
    "run_micro_chat",
    "run_regenerate",
    "vlm_score_media",
]
