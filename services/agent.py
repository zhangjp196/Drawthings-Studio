"""Pydantic AI v2 统一 Agent 层：后端所有 LLM 调用都走这里。

- 微创作：流式对话 + generate_media function call（SSE 推送）
- 流水线：结构化输出（篇幅 / 分章 / 剧本）与纯文本生成

所有 LLM 走 OpenAI 兼容协议（Ollama / vLLM / 云端），
base_url / api_key / model 来自用户所选的 LLMConfig。
"""
import base64
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
import httpx2
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, ModelSettings
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

# 模型缓存：key = 配置 id → (签名, 模型)。复用同一模型实例即复用底层 httpx 连接池。
_model_cache: dict[str, tuple[str, OpenAIChatModel]] = {}
_model_cache_lock = threading.Lock()
_MODEL_CACHE_MAX = 16


def _model_signature(cfg: LLMConfig) -> str:
    """模型配置签名：任一字段变化即视为需要重建。"""
    return "|".join(str(getattr(cfg, k, None) or "") for k in
                    ("base_url", "api_key", "model", "thinking", "thinking_param"))


def _is_openai_host(host: str) -> bool:
    """是否为 OpenAI 官方 / Azure 端点（决定 auto 模式下用哪种思考参数）。"""
    host = (host or "").lower()
    return host == "api.openai.com" or host.endswith(".openai.com") \
        or host.endswith(".openai.azure.com")


def _thinking_settings(cfg: LLMConfig) -> ModelSettings | None:
    """按「深度思考」配置构造模型设置。default/未设置 → None（不传）。

    发送方式（thinking_param）：
    - auto：OpenAI 官方/Azure → reasoning_effort；其它（本地 vLLM/Ollama/云端兼容）→ enable_thinking；
    - reasoning_effort：OpenAI 标准参数（经 pydantic-ai 统一 thinking 映射为 reasoning_effort）；
    - enable_thinking：作为 extra_body 发送 enable_thinking 与 chat_template_kwargs.enable_thinking
      （覆盖 Ollama / DashScope 的顶层开关与 vLLM 的 chat_template_kwargs）。
    """
    level = (getattr(cfg, "thinking", "default") or "default").lower()
    if level not in ("yes", "no"):
        return None
    on = level == "yes"
    method = (getattr(cfg, "thinking_param", "auto") or "auto").lower()
    if method not in ("reasoning_effort", "enable_thinking"):
        host = (urlparse(cfg.base_url or "").hostname or "").lower()
        method = "reasoning_effort" if _is_openai_host(host) else "enable_thinking"
    if method == "reasoning_effort":
        return ModelSettings(thinking=on)
    return ModelSettings(extra_body={"enable_thinking": on,
                                     "chat_template_kwargs": {"enable_thinking": on}})



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

    深度思考（thinking）：
    - default：不传该参数，交给模型/服务端默认行为；
    - yes/no：按 thinking_param 选择发送方式（reasoning_effort 或 enable_thinking，见 _thinking_settings）。

    性能：同一配置（含 base_url/api_key/model/思考参数）复用同一模型实例，
    从而复用底层 httpx 连接池（keep-alive），避免每次调用重新建连/握手；
    配置任一字段变化（签名不同）即重建。
    """
    cid = str(getattr(cfg, "id", "") or "")
    sig = _model_signature(cfg)
    if cid:
        with _model_cache_lock:
            hit = _model_cache.get(cid)
            if hit and hit[0] == sig:
                return hit[1]
    host = (urlparse(cfg.base_url or "").hostname or "").lower()
    provider = OpenAIProvider(
        base_url=cfg.base_url,
        api_key=cfg.api_key or "sk-local",
        http_client=httpx2.AsyncClient(timeout=600.0, trust_env=host not in _LOOPBACK_HOSTS),
    )
    model = OpenAIChatModel(cfg.model, provider=provider, settings=_thinking_settings(cfg))
    if cid:
        with _model_cache_lock:
            if len(_model_cache) >= _MODEL_CACHE_MAX:
                _model_cache.clear()  # 配置数量很少；兜底防无限增长
            _model_cache[cid] = (sig, model)
    return model


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
