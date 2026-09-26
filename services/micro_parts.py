"""助手回复「有序内容块」(parts) 的 schema 与版本化读写。

每条助手消息的 parts 记录文本 / 生成 / 错误的**实际先后顺序**（刷新后与流式过程一致）。
历史格式是**裸 JSON 数组**（v0）；本模块引入版本包装 `{"v": 1, "blocks": [...]}`（v1），
读取时两者都兼容，写入时统一 v1。这样将来新增块类型（附件 / 引用 / 多模态）可平滑迁移。

设计要点：
- 已知块类型用 Pydantic 判别联合校验（类型安全）；
- 未知块类型**原样保留**（向前兼容，不丢新前端写入的数据）；
- 任何解析失败都退化为「丢弃该块」，绝不因历史脏数据导致接口 500。
"""
import json
from typing import Literal, Union

from pydantic import BaseModel, TypeAdapter

PARTS_VERSION = 1


class TextBlock(BaseModel):
    """文本块：与相邻文本块合并，保证与生成块的相对顺序。"""
    type: Literal["text"] = "text"
    text: str = ""


class ToolBlock(BaseModel):
    """生成块：一次 generate_media 调用（生成中 → 已生成 / 失败）。

    附「生成参数快照」（model/width/height/seconds/ref_url），用于结果复现与一键重跑。
    """
    type: Literal["tool"] = "tool"
    id: str = ""
    label: str = ""
    prompt: str = ""
    status: str = "running"      # running | ok | error
    media: str = "image"         # image | video
    url: str = ""
    message: str = ""
    # ---- 生成参数快照（B5：可复现 / 一键重跑）----
    model: str = ""              # 实际使用的模型名
    width: int = 0               # 图片宽（0=跟随预设）
    height: int = 0
    seconds: int = 0             # 视频时长（秒；0=用上限）
    ref_url: str = ""            # 实际参考图（/media/xxx；空=无参考）


class MediaBlock(BaseModel):
    """独立媒体块（兼容旧/异常流：无 tool 包裹的媒体结果）。"""
    type: Literal["media"] = "media"
    media: str = ""
    url: str = ""
    prompt: str = ""


class ErrorBlock(BaseModel):
    """错误块：本会话/本次生成失败。"""
    type: Literal["error"] = "error"
    message: str = ""


Block = Union[TextBlock, ToolBlock, MediaBlock, ErrorBlock]
_adapter: TypeAdapter = TypeAdapter(Block)
_KNOWN = {"text", "tool", "media", "error"}


def _coerce(block: dict) -> dict | None:
    """单个块转 dict：已知类型经校验后 model_dump；未知类型原样返回；非法丢弃。"""
    if not isinstance(block, dict):
        return None
    btype = str(block.get("type") or "")
    if btype not in _KNOWN:
        return dict(block)  # 向前兼容：保留未来/未知块
    try:
        return _adapter.validate_python(block).model_dump()
    except Exception:
        return None


def dump_parts(parts: list[dict]) -> str | None:
    """校验并序列化内容块为版本化 JSON 字符串；空列表返回 None（不写库）。"""
    blocks: list[dict] = []
    for p in parts or []:
        b = _coerce(p)
        if b is not None:
            blocks.append(b)
    if not blocks:
        return None
    return json.dumps({"v": PARTS_VERSION, "blocks": blocks}, ensure_ascii=False)


def load_parts(raw: str | None) -> list[dict]:
    """读取 parts（兼容 v0 裸数组 / v1 版本包装）；非法块丢弃。返回块列表。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):
        data = data.get("blocks")
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for p in data:
        b = _coerce(p)
        if b is not None:
            out.append(b)
    return out


__all__ = ["PARTS_VERSION", "TextBlock", "ToolBlock", "MediaBlock", "ErrorBlock",
           "dump_parts", "load_parts"]
