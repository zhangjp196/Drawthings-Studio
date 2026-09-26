"""数据模型（SQLAlchemy）。

两张配置表（LLM / DrawThings，用户可在页面增删）、全局基础配置（单行 JSON）、项目、季（篇章）、章节、
微创作（作品 → 会话 → 消息）。媒体文件本身存 data/media 目录，这里只存路径引用。

多季（篇章）设计：项目 = 统一世界观（总纲/核心角色/风格/封面），季 = 独立故事段（自己的大纲/新增角色/章节）。
章节 index 为扁平全局序号（按季连续），季内展示序号由分组位置计算。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Integer, Float, ForeignKey, JSON, Boolean, UniqueConstraint
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
    supports_vision = Column(String(5), default="yes")   # 遗留字段：VLM 一律按支持图片输入处理，不再可配置
    thinking = Column(String(10), default="default")      # default|yes|no：深度思考（推理）开关
    thinking_param = Column(String(20), default="auto")    # auto|reasoning_effort|enable_thinking：发送方式
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)


class DrawThingConfig(Base):
    """Draw Things 配置（Mac 本地出图/出视频，仅 gRPC）。

    app 的 API server 设为 **gRPC**（默认端口 7859），base_url 形如 127.0.0.1:7859。
    请求必须自带完整生成配置，故需指定 **图像模型 / 视频模型**
    （`model_image` / `model_video`，可只填其一 = 只支持该类型）与各自的
    **预设**（`preset_image` / `preset_video`，提供 steps/sampler 等；留空则按模型名自动推断）。
    其余为个性化参数，0/空 = 跟随预设。

    「支持参考图片」（`ref_image` / `ref_video`）分别声明图像/视频模型能否图生图 / 图生视频：
    勾选后才会把上一章媒体作为参考图传给 Draw Things，未勾选一律按文生图 / 文生视频。
    """

    __tablename__ = "drawthing_configs"

    id = Column(String(12), primary_key=True)
    name = Column(String(100), nullable=False)
    base_url = Column(String(500), nullable=False)       # gRPC 端点 host:port（如 127.0.0.1:7859）
    model_image = Column(String(200), default="")        # 图像模型文件名（可空）
    model_video = Column(String(200), default="")        # 视频模型文件名（可空）
    max_side = Column(Integer, default=0)                # 最大分辨率（仅最长边，0=不限/跟随预设）
    max_seconds = Column(Integer, default=8)             # 视频最大时长（秒，0=用内置上限 8s；实际时长可由生成请求决定，不超过上限）
    ref_image = Column(Integer, default=0)               # 图像模型支持参考图片（图生图）；0=纯文生图
    ref_video = Column(Integer, default=0)               # 视频模型支持参考图片（图生视频）；0=纯文生视频
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)


class AppSettings(Base):
    """全局基础配置（单行 JSON）：新建创作默认配置 / 默认生成参数等。"""

    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, default=1)
    data = Column(JSON, default=dict)


class Project(Base):
    """项目：携带所选的 LLM / DrawThings 配置 id，运行时现场构建客户端。"""

    __tablename__ = "projects"

    id = Column(String(12), primary_key=True)
    kind = Column(String(10), nullable=False)             # comic | drama
    title = Column(String(200), default="")               # 标题（作品名，可编辑）
    origin = Column(Text, nullable=False)                 # 主题（一句话创意）
    llm_config_id = Column(String(12), ForeignKey("llm_configs.id"), nullable=False)
    drawthings_config_id = Column(String(12), ForeignKey("drawthing_configs.id"), nullable=False)
    dt_model_image = Column(String(200), default="")            # 功能级模型：本项目出图模型（空=跟随 DrawThings 配置）
    dt_model_video = Column(String(200), default="")             # 功能级模型：本项目出视频模型（空=跟随 DrawThings 配置）
    dt_ref_image = Column(String(1), default="")                 # 功能级参考图开关（''=跟随配置，0=关，1=开）
    dt_ref_video = Column(String(1), default="")                 # 功能级参考图开关（''=跟随配置，0=关，1=开）
    status = Column(String(20), default="planning")       # planning|arced|done（done 为遗留值；完成已下沉到季，不再由 UI 设置）
    scope = Column(JSON, default=dict)                     # 风格/主题/基调（整体，供后续保持一致）
    arc = Column(Text, default="")                         # 总纲（整部作品主线；各季 arc 为其分段，可编辑）
    characters = Column(Text, default="")                  # 核心角色设定（贯穿各季的主要角色，名字/形象/性格）
    global_prompt = Column(Text, default="")               # 全局提示词（要点/约束）：注入到每次章节 LLM 调用
    res_width = Column(Integer, default=0)                 # 默认分辨率宽（0=跟随智能体/出图端）
    res_height = Column(Integer, default=0)                # 默认分辨率高（0=跟随智能体/出图端）
    count_mode = Column(String(10), default="range")       # 章节数量模式（遗留字段，仅范围；现按季存于 Season）
    count_min = Column(Integer, default=0)                 # range 模式：最少章节数（遗留）
    count_max = Column(Integer, default=0)                 # range 模式：最多章节数（遗留）
    auto_score = Column(Integer, default=1)                # 自动评分：每章画面生成后按 0–100 评分（1=开启）
    score_min = Column(Integer, default=60)                # 评分阈值：低于该分且开启自动重做 → 重新生成
    auto_redo = Column(Integer, default=1)                 # 低分自动重做：1=开启（最多重做 2 次）
    first_image = Column(String(500), default="")          # 封面路径（作品封面：列表缩略图/导出封面）
    first_image_base = Column(String(500), default="")     # 封面原图（无叠字）：叠字每次从原图重绘，反复调整不叠加
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)
    llm_config = relationship("LLMConfig")
    drawthings_config = relationship("DrawThingConfig")
    seasons = relationship("Season", back_populates="project", order_by="Season.number")
    chapters = relationship("Chapter", back_populates="project", order_by="Chapter.index")


class Season(Base):
    """季（篇章/弧）：统一世界观下的独立故事段（类似七龙珠的赛亚人篇/弗利萨篇）。

    一个项目下可有多个季；每季有自己的 大纲 / 新增角色 / 章节数量设定 / 章节。
    项目层的 arc（总纲）/ characters（核心角色）/ scope（风格）/ global_prompt 为全局共享。
    季内章节按 Chapter.index（扁平全局序号）连续排列，季内展示序号由分组位置计算。
    """

    __tablename__ = "seasons"

    id = Column(String(12), primary_key=True)
    project_id = Column(String(12), ForeignKey("projects.id"), nullable=False)
    number = Column(Integer, nullable=False)                # 季序号（1 起）
    title = Column(String(200), default="")                 # 季名（如「赛亚人篇」，空=第N季）
    arc = Column(Text, default="")                           # 季大纲（本段故事路线，可编辑）
    characters = Column(Text, default="")                    # 本季新增角色（JSON，结构同 project.characters）
    first_image = Column(String(500), default="")             # 季封面（媒体路径，语义同项目封面）
    first_image_base = Column(String(500), default="")        # 季封面原图（无叠字），同项目封面
    cover_as_first_ref = Column(Boolean, default=False)       # 是否把季封面作为本季第 1 章参考（漫画 img2img / 短剧首帧）
    count_mode = Column(String(10), default="range")          # 本季章节数量（仅范围 min~max）
    count_min = Column(Integer, default=0)                   # range：最少章节数
    count_max = Column(Integer, default=0)                   # range：最多章节数
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)
    project = relationship("Project", back_populates="seasons")
    chapters = relationship("Chapter", back_populates="season", order_by="Chapter.index")


class Chapter(Base):
    """章节：属于某个项目的某一季。index 为扁平全局序号（按季有序，0 起）。"""

    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(12), ForeignKey("projects.id"), nullable=False)
    season_id = Column(String(12), ForeignKey("seasons.id"), nullable=True)  # 所属季（旧数据可空=迁移归入第1季）
    index = Column(Integer, nullable=False)                # 扁平全局章序号（0 起，按季连续）
    title = Column(String(200), default="")
    summary = Column(Text, default="")                     # 大纲里的每章主题摘要（规划用，供后续章节保持一致）
    description = Column(Text, default="")                 # 剧本描述（章节页「生成剧本」产出的详细剧本）
    prompt = Column(Text, default="")                      # 出图/出视频提示词
    width = Column(Integer, default=0)                     # 智能体决定的具体分辨率宽（0=跟随 app）
    height = Column(Integer, default=0)                    # 智能体决定的具体分辨率高（0=跟随 app）
    ref_path = Column(String(500), default="")             # 参考（上一张图/上一视频末帧）
    media_path = Column(String(500), default="")           # 生成的图/视频路径
    status = Column(String(10), default="pending")         # pending|done|error
    error = Column(Text, default="")
    score = Column(Integer, default=0)                     # 评分 0–100（0=未评分）
    score_note = Column(String(300), default="")           # 评分评语（自动评分说明 / 手动评分标记）
    project = relationship("Project", back_populates="chapters")
    season = relationship("Season", back_populates="chapters")


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
    dt_model_image = Column(String(200), default="")         # 功能级模型：本作品出图模型（空=跟随 DrawThings 配置）
    dt_model_video = Column(String(200), default="")         # 功能级模型：本作品出视频模型（空=跟随 DrawThings 配置）
    dt_ref_image = Column(String(1), default="")             # 功能级参考图开关（''=跟随配置，0=关，1=开）
    dt_ref_video = Column(String(1), default="")             # 功能级参考图开关（''=跟随配置，0=关，1=开）
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
    created_at = Column(String(40), default="")            # 消息时间（ISO）
    duration = Column(Float, default=0)                    # 助手消息耗时（秒：开始输出 → 完成）
    content = Column(Text, nullable=False)                 # 文本内容
    images = Column(Text, nullable=True)                  # 用户附带的图片（JSON 列表，/media/xxx；仅视觉模型）
    media_url = Column(String(500), default="")            # 助手消息附带的生成媒体（/media/xxx，兼容旧逻辑：取最后一次）
    prompt = Column(Text, default="")                      # 生成时用的提示词（兼容旧逻辑：取最后一次）
    parts = Column(Text, nullable=True)                   # 助手回复的有序内容块（JSON：[{type:text|tool|error,...}]，保序）
    status = Column(String(12), default="done")            # done | streaming | interrupted（可恢复流：断连时的部分输出）
    session = relationship("MicroSession", back_populates="messages")


class Asset(Base):
    """生成资产（Asset Graph）：每次成功生成的媒体一条记录，带参数快照与来源。

    与消息解耦：消息是「展示」，资产可被跨轮/跨会话复用为参考图、被画廊/导出按资产维度处理。
    同一助手消息内多次生成 → 多条资产（block_id 区分）。消息删除时随外键级联删除。
    """

    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    micro_id = Column(String(12), ForeignKey("micro_works.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(12), ForeignKey("micro_sessions.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(Integer, ForeignKey("micro_messages.id", ondelete="CASCADE"), nullable=True)
    block_id = Column(String(20), default="")             # 来源内容块 id（同一消息内多次生成区分）
    kind = Column(String(10), default="image")            # image | video
    url = Column(String(500), default="")                 # /media/xxx
    prompt = Column(Text, default="")                     # 生成用提示词
    model = Column(String(200), default="")               # 实际使用的模型
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    seconds = Column(Integer, default=0)
    ref_url = Column(String(500), default="")             # 参考图（/media/xxx；空=无参考）
    created_at = Column(String(40), default=_now)

    __table_args__ = (
        UniqueConstraint("message_id", "block_id", name="uq_asset_message_block"),
    )


class GenerationJob(Base):
    """生成任务（Job）：把「一次生成」建模为可观测、可取消的任务。

    - 对话（chat）/ 重跑（regenerate）开始时建任务（running），事件推进时更新 note，
      结束时置 done / error / interrupted / cancelled；
    - 进程内注册表（api_micro._RUNNING_JOBS）持有运行中任务的 cancel_event，供取消接口使用；
    - 与可恢复流配合：任务中断时消息保留为 interrupted，任务本身记为 interrupted/cancelled。
    """

    __tablename__ = "generation_jobs"

    id = Column(String(12), primary_key=True)
    micro_id = Column(String(12), ForeignKey("micro_works.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(12), ForeignKey("micro_sessions.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(Integer, ForeignKey("micro_messages.id", ondelete="CASCADE"), nullable=True)
    kind = Column(String(12), default="chat")             # chat | regenerate
    status = Column(String(12), default="running")        # running | done | error | interrupted | cancelled
    media = Column(String(10), default="")                # image | video（已知时）
    prompt = Column(Text, default="")
    note = Column(Text, default="")                       # 最新状态文案（如「正在生成图像…」）
    error = Column(Text, default="")
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)
    finished_at = Column(String(40), default="")


class ProjectJob(Base):
    """项目生成任务（Job）：把「一次长生成/规划/批评分」建模为可观测、可取消的任务。

    与微创作的 GenerationJob 分开（作用域不同）；kind: chapters | generate | score | single。
    运行中的取消句柄存进程内注册表（services/jobs），取消接口据此置位。
    """

    __tablename__ = "project_jobs"

    id = Column(String(12), primary_key=True)
    project_id = Column(String(12), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(12), default="generate")         # chapters | generate | score | single
    status = Column(String(12), default="running")        # running | done | error | interrupted | cancelled
    note = Column(Text, default="")                       # 最新状态文案（如进度）
    error = Column(Text, default="")
    created_at = Column(String(40), default=_now)
    updated_at = Column(String(40), default=_now)
    finished_at = Column(String(40), default="")
