"""新建创作「AI 生成标题/主题」会话引擎（类型无关，漫画/短剧共用）。

- 多轮对话 + 流式输出；
- 信息不足时模型主动**提问**；信息足够或用户要求时调用 `set_fields` 工具
  写入「标题 title」与「主题 origin」，前端据此填充新建表单（`fields` 事件）。
- 不落库（尚未创建项目），会话历史由前端持有并随请求带回。
"""
import asyncio

from pydantic_ai import Agent, RunContext

from i18n import L
from services.agent import build_model, to_message_history, user_prompt
from services import events as E

IDEA_SYSTEM = (
    "你是漫画/短剧创作的创意助手。用户可能只有一个模糊的想法。请：\n"
    "1) 若关键信息不足（题材/类型、主角、基调、大致走向等），先用**提问**的方式逐步问清，"
    "每轮只问 1–2 个最关键的问题，别一次问太多；\n"
    "2) 当信息足够，或用户明确要你直接生成时，调用 set_fields 工具写入"
    "「标题 title」与「主题 origin（一句话创意）」；标题精炼有吸引力，"
    "主题要具体、可展开（含主角 / 冲突 / 基调 / 看点）；\n"
    "3) 写入后可以用一两句话说明，并问用户是否需要调整。\n"
    "始终用用户所用的语言回答；回答简洁，不要长篇大论。")


async def run_idea_chat(out: asyncio.Queue, *, llm_cfg, kind: str = "comic",
                        user_message: str, message_history_payload: list | None = None,
                        history: list | None = None, lang: str = "zh") -> None:
    """执行一轮「AI 生成标题/主题」对话：事件写入 out，结束发 (E.EOF, None)。

    事件：token{text} / fields{title,origin} / error{message} / done{}。
    `history`（兼容旧名 `message_history_payload`）为 [{role, content}] 多轮历史。
    """
    hist = history if history is not None else (message_history_payload or [])
    if not llm_cfg:
        await out.put((E.ERROR, {"message": L(lang, "请选择有效的 VLM 配置",
                                              "Please select a valid VLM config")}))
        await out.put((E.DONE, {}))
        await out.put((E.EOF, None))
        return
    try:
        model = build_model(llm_cfg)
        agent = Agent(model, instructions=IDEA_SYSTEM)

        async with agent:
            @agent.tool
            async def set_fields(ctx: RunContext, title: str, origin: str) -> str:
                """把生成好的「标题」与「主题」写入新建创作表单。

                Args:
                    title: 作品标题（精炼、有吸引力）
                    origin: 主题 / 一句话创意（含主角、冲突、基调、看点，可展开）
                """
                await out.put((E.FIELDS, {"title": str(title or "").strip(),
                                          "origin": str(origin or "").strip()}))
                return "已写入标题与主题。可用一两句话说明并询问用户是否需要调整。"

            async with agent.run_stream(user_prompt(user_message),
                                        message_history=to_message_history(hist)) as result:
                async for text in result.stream_text(delta=True, debounce_by=None):
                    await out.put((E.TOKEN, {"text": text}))
                await result.get_output()
        await out.put((E.DONE, {}))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await out.put((E.ERROR, {"message": L(lang, f"生成失败：{e}", f"Failed: {e}")}))
        await out.put((E.DONE, {}))
    finally:
        try:
            await out.put((E.EOF, None))
        except BaseException:
            pass


__all__ = ["IDEA_SYSTEM", "run_idea_chat"]
