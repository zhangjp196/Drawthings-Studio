"""Pydantic AI v2 统一 Agent 层：后端所有 LLM 调用都走这里。

- 微创作：流式对话 + generate_media function call（SSE 推送）
- 流水线：结构化输出（篇幅 / 分章 / 剧本）与纯文本生成

所有 LLM 走 OpenAI 兼容协议（Ollama / vLLM / 云端），
base_url / api_key / model 来自用户所选的 LLMConfig。
"""
import base64
from pathlib import Path
from urllib.parse import urlparse

import httpx
import httpx2
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.messages import (
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from models import LLMConfig

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def make_httpx_client(base_url: str, timeout: float = 120.0) -> httpx.Client:
    """构造 httpx 客户端：本地回环端点（Ollama/vLLM/Draw Things）直连，
    不走系统代理（系统代理常把 127.* 转发到远端导致 502）；
    云端端点保留系统代理设置。"""
    host = (urlparse(base_url or "").hostname or "").lower()
    return httpx.Client(timeout=timeout, trust_env=host not in _LOOPBACK_HOSTS)


def build_model(cfg: LLMConfig) -> OpenAIChatModel:
    """由 LLMConfig 构造 Pydantic AI 模型（OpenAI 兼容协议）。

    本地回环端点直连、不走系统代理（系统代理常把 127.* 转发到远端导致 502）；
    云端端点保留系统代理设置。
    """
    host = (urlparse(cfg.base_url or "").hostname or "").lower()
    provider = OpenAIProvider(
        base_url=cfg.base_url,
        api_key=cfg.api_key or "sk-local",
        http_client=httpx2.AsyncClient(timeout=600.0, trust_env=host not in _LOOPBACK_HOSTS),
    )
    return OpenAIChatModel(cfg.model, provider=provider)


def image_data_uri(path: str) -> str:
    """本地图片 -> data URI（供多模态入图）。"""
    p = Path(path)
    suffix = p.suffix.lstrip(".").lower() or "png"
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/{suffix};base64,{data}"


def user_prompt(text: str, image_paths: list[str] | None = None) -> str | list:
    """用户消息内容：纯文本，或 文本 + 本地图片（转 data URI 的 ImageUrl，多模态入图）。"""
    urls = [image_data_uri(p) for p in (image_paths or []) if Path(p).exists()]
    if not urls:
        return text
    parts: list = []
    if text:
        parts.append(TextContent(content=text))
    parts.extend(ImageUrl(url=u) for u in urls)
    return parts


def to_message_history(items: list[dict]) -> list[ModelMessage]:
    """[{role, content, images?}] 历史 -> Pydantic AI 消息（images = 本地图片路径列表）。"""
    history: list[ModelMessage] = []
    for m in items or []:
        content = (m.get("content") or "").strip()
        images = m.get("images") or []
        if not content and not images:
            continue
        if m.get("role") == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=user_prompt(content, images))]))
        elif m.get("role") == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=content)]))
    return history


# ---------------- 流水线结构化输出 ----------------
class ArcOut(BaseModel):
    """企划步骤 1：整体故事大纲。"""
    arc: str = ""


class SeasonArcOut(BaseModel):
    """季大纲：本季名（篇章名，如「赛亚人篇」）+ 本季故事大纲。"""
    title: str = ""
    arc: str = ""


class CharacterOut(BaseModel):
    """单个角色设定（名字 + 形象/性格描述）。"""
    name: str = ""
    description: str = ""


class CharsOut(BaseModel):
    """企划步骤 2：角色设定（多个角色，供后续各章保持一致）。"""
    characters: list[CharacterOut] = []


class CharDescOut(BaseModel):
    """单个角色的形象/性格描述（可结合参考图生成）。"""
    description: str = ""


class ChapterOut(BaseModel):
    title: str
    scene: str


class ChaptersOut(BaseModel):
    """阶段 3：分章。"""
    chapters: list[ChapterOut] = []


class ChapterCount(BaseModel):
    """先定总章数（供逐章规划：先定 N 再逐章规划 1..N）。"""
    count: int = 0


class ScriptOut(BaseModel):
    """阶段 4：单章剧本 + 提示词 + 具体分辨率。"""
    description: str
    prompt: str
    width: int = 0    # 智能体按场景构图决定的分辨率宽（64 的倍数，0=跟随默认）
    height: int = 0   # 智能体按场景构图决定的分辨率高（64 的倍数，0=跟随默认）


def make_agent(model: Model, system: str, output_type: type = str) -> Agent:
    """构造单用途 Agent（系统提示词 + 输出类型）。"""
    return Agent(model, instructions=system, output_type=output_type)
