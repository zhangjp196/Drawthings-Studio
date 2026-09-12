"""数据模型（SQLAlchemy）。

两张配置表（LLM / DrawThings，用户可在页面增删）、项目、章节、微创作（作品 → 会话 → 消息）。
媒体文件本身存 data/media 目录，这里只存路径引用。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Integer, ForeignKey, JSON
from sqlalchemy.orm import relationship

from db import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LLMConfig(Base):
    """大模型配置（OpenAI 协议，兼容 Ollama / vLLM / 云端 OpenAI）。"""

    __tablename__ = "llm_configs"

    id = Column(String(12), primary_key=True)
    name = Column(String(100), nullable=False)          # 用户自定义名称
    base_url = Column(String(500), nullable=False)       # 端点，如 http://127.0.0.1:11434/v1
    api_key = Column(Text, default="")                    # 本地端点任意非空即可
    model = Column(String(200), nullable=False)          # 模型名
    supports_vision = Column(String(5), default="yes")   # yes|no：OpenAI 多模态是否支持图片输入
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)


class DrawThingConfig(Base):
    """Draw Things 配置（Mac 本地出图/出视频）。

    使用 app 内置 HTTP API（A1111/SD-WebUI 兼容：/sdapi/v1/txt2img、/sdapi/v1/img2img），
    base_url 形如 http://127.0.0.1:7860（端口以 app 显示为准）。
    不使用 gRPC（app 的 gRPC 服务收到生成请求会闪退）。
    模型只能跟随 app 当前选择（API 不支持指定）；其余为个性化参数，0/空 = 跟随 app 当前值。
    """

    __tablename__ = "drawthing_configs"

    id = Column(String(12), primary_key=True)
    name = Column(String(100), nullable=False)
    base_url = Column(String(500), nullable=False)       # HTTP 端点 URL（http://host:port）
    max_side = Column(Integer, default=0)                # 最大分辨率（仅最长边，0=不限/跟随 app）
    max_frames = Column(Integer, default=0)              # 视频最大帧数上限（0=不限，跟随 app）
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)


class Project(Base):
    """项目：携带所选的 LLM / DrawThings 配置 id，运行时现场构建客户端。"""

    __tablename__ = "projects"

    id = Column(String(12), primary_key=True)
    kind = Column(String(10), nullable=False)             # comic | drama
    title = Column(String(200), default="")               # 标题（作品名，可编辑）
    origin = Column(Text, nullable=False)                 # 主题（一句话创意）
    llm_config_id = Column(String(12), ForeignKey("llm_configs.id"), nullable=False)
    drawthings_config_id = Column(String(12), ForeignKey("drawthing_configs.id"), nullable=False)
    status = Column(String(20), default="planning")       # planning|scoped|arced|chaptered|scripted|done
    scope = Column(JSON, default=dict)                     # 篇幅/风格等
    arc = Column(Text, default="")                         # 整体故事总纲（开端→发展→高潮→结局，可编辑）
    first_image = Column(String(500), default="")          # 首图路径（第 1 章参考：漫画 img2img / 短剧首帧）
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)
    llm_config = relationship("LLMConfig")
    drawthings_config = relationship("DrawThingConfig")
    chapters = relationship("Chapter", back_populates="project", order_by="Chapter.index")


class Chapter(Base):
    """章节：属于某个项目。"""

    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(12), ForeignKey("projects.id"), nullable=False)
    index = Column(Integer, nullable=False)                # 章序号（0 起）
    title = Column(String(200), default="")
    description = Column(Text, default="")                 # 剧本描述
    prompt = Column(Text, default="")                      # 出图/出视频提示词
    width = Column(Integer, default=0)                     # 智能体决定的具体分辨率宽（0=跟随 app）
    height = Column(Integer, default=0)                    # 智能体决定的具体分辨率高（0=跟随 app）
    ref_path = Column(String(500), default="")             # 参考（上一张图/上一视频末帧）
    media_path = Column(String(500), default="")           # 生成的图/视频路径
    status = Column(String(10), default="pending")         # pending|done|error
    error = Column(Text, default="")
    project = relationship("Project", back_populates="chapters")


class MicroWork(Base):
    """微创作作品（原“快工具”升级）：不建项目的轻量创作。

    一个作品下存在**多个独立会话**（各自独立的历史对话）。
    配置（LLM / DrawThings / 产出类型 / 负向提示词）随作品保存，供其下所有会话共用；
    drawthings_config_id 为空 = 纯对话模式。
    """

    __tablename__ = "micro_works"

    id = Column(String(12), primary_key=True)
    title = Column(String(200), default="")               # 作品标题（留空自动取）
    llm_config_id = Column(String(12), nullable=False)
    drawthings_config_id = Column(String(12), default="")   # 空 = 纯对话
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)
    sessions = relationship("MicroSession", back_populates="work",
                            cascade="all, delete-orphan", order_by="MicroSession.updated_at.desc()")


class MicroSession(Base):
    """作品下的一个独立会话：独立的历史对话，可新建/删除/进入。"""

    __tablename__ = "micro_sessions"

    id = Column(String(12), primary_key=True)
    micro_id = Column(String(12), ForeignKey("micro_works.id"), nullable=False)
    title = Column(String(200), default="")               # 留空时取首条用户消息
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)
    work = relationship("MicroWork", back_populates="sessions")
    messages = relationship("MicroMessage", back_populates="session",
                            cascade="all, delete-orphan", order_by="MicroMessage.index")


class MicroMessage(Base):
    """会话内的一条消息（用户/助手），持久化历史。"""

    __tablename__ = "micro_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(12), ForeignKey("micro_sessions.id"), nullable=False)
    index = Column(Integer, nullable=False)                # 会话内序号（0 起）
    role = Column(String(10), nullable=False)              # user | assistant
    content = Column(Text, nullable=False)                 # 文本内容
    images = Column(Text, nullable=True)                  # 用户附带的图片（JSON 列表，/media/xxx；仅视觉模型）
    media_url = Column(String(500), default="")            # 助手消息附带的生成媒体（/media/xxx）
    prompt = Column(Text, default="")                      # 生成时用的提示词
    session = relationship("MicroSession", back_populates="messages")
