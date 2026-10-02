"""短剧（视频）流水线（独立）：一句话 -> 全局(风格/总纲/核心角色/封面) -> 各季(季大纲/季角色/章节规划) -> 剧本编写 -> 逐章生视频。

与漫画流水线（pipeline_comic.py）完全独立、互不共享业务逻辑：
- 剧本系统提示词面向短剧（连贯视频画面，横/竖构图按场景自选）；
- 逐章生成调用 DrawThings **生视频**（generate_video），前章视频抽末帧作参考；
- 导出仅支持 ZIP（视频不支持拼 PDF）。

设计约定：漫画 / 短剧刻意分成两条重复的独立线（便于分开开发维护），
改本线时按需在 pipeline_comic.py 对称修改，**请勿合并 / 勿抽共享基座**（见 AGENTS.md）。

数据全部走 SQLite（models.py），媒体文件存 data/media。
每个项目携带所选 LLMConfig / DrawThingConfig 的 id，运行时现场构建客户端，
因此不同项目可用不同的端点/模型/模式。

多集（篇章）设计（统一世界观 + 各集独立故事）：
- 项目层：global_prompt（全局要求：风格 + 要点）/ characters（核心角色）/ 封面，全局共享；
- 季层：每季有自己的 arc（本集大纲）/ characters（本集新增角色）/ 章节数量设定 / 章节；
- **不再有项目层总纲**：「生成本集大纲」一步同时负责全篇主线与本集路线，作品标题也随该步产出；
- 章节 index 为扁平全局序号（按季连续），季内展示序号由分组位置计算；
- 剧本/生成上下文 = 全局要求 + 核心角色 + 本集大纲 + 本集角色；
- 连续性：首章（第 1 集第 1 章）用封面（若开启），其余章用上一章媒体（跨集承接上集末章）。
"""
import logging
import shutil
import uuid
import zipfile
from functools import partial
from pathlib import Path

from PIL import Image
from sqlalchemy import or_
from pydantic_ai.messages import ImageUrl

from i18n import L
from models import Project, Chapter, Season, LLMConfig, DrawThingConfig

from config_store import ConfigStore
from .agent import (
    CharsOut,
    CharDescOut,
    ChapterCount,
    ChapterOut,
    SeasonArcOut,
    ScriptOut,
    ScoreOut,
    build_model,
    image_data_uri,
    make_agent,
)
from .drawthings import extract_last_frame, MAX_VIDEO_SECONDS, concat_videos
from .jobs import JobCancelled
from .capabilities import dt_client, ref_video_enabled
from .pipeline_common import (
    MAX_SCORE_REDO,
    _cjk_font_path,
    _now,
    chars_from_raw,
    chars_to_raw,
    chars_to_text,
    clip_text,
    count_range,
    run_sync,
)

# 全篇剧情线上下文里，单集大纲最多注入的字数（多集时按此截断，避免上下文膨胀）
SEASON_ARC_CONTEXT_CHARS = 500


logger = logging.getLogger("drawthings")

# 短剧生成安全网：在提示词末尾追加「无文字/字幕/水印/时间码 + 光影场景稳定」约束，
# 避免模型生成可读文字、穿帮的字幕条/时间码/水印，以及前后段场景/光照跳变导致的画面不稳定。
_VIDEO_NO_TEXT_SUFFIX = (", no text, no subtitles, no captions, no on-screen words, no logos, "
                         "no watermarks, no character introduction cards, no name tags, "
                         "no timestamps, no timecodes, no clocks, no HUD or UI overlays, no website URLs, "
                         "no scene jumps or glitch transitions; consistent lighting and stable scene")

class DramaPipeline:
    """短剧流水线（集 / 片段）：与漫画流水线完全独立。

    术语：短剧把「季」称为**集（episode）**、把「章」称为**片段（clip）**；
    模型层仍复用共享的 Season / Chapter（数据库列 season_id 不变），仅在方法名与局部标识上使用 episode/clip。
    """

    def __init__(self, data_dir):
        self.data_dir = str(data_dir)

    # ---------------- 客户端构造 ----------------
    def _configs(self, db, project, lang: str = "zh") -> tuple[LLMConfig, DrawThingConfig]:
        cs = ConfigStore(db)
        llm_cfg = cs.get_llm(project.llm_config_id)
        dt_cfg = cs.get_drawthing(project.drawthings_config_id)
        if not llm_cfg or not dt_cfg:
            raise RuntimeError(L(lang, "项目所选配置已被删除，请到「配置管理」重新选择或新建",
                                 "The selected config was deleted — re-select or create one in Settings"))
        return llm_cfg, dt_cfg

    def _clients(self, db, project, lang: str = "zh"):
        """DrawThings 客户端（出图/出视频用；仅 gRPC）。

        功能级模型 / 参考图开关：项目自选的覆盖配置（留空 = 跟随配置）。"""
        _, dt_cfg = self._configs(db, project, lang)
        return dt_client(dt_cfg, self.data_dir, project)

    def _llm_cfg(self, db, project, lang: str = "zh") -> LLMConfig:
        llm_cfg, _ = self._configs(db, project, lang)
        return llm_cfg

    # ---------------- 项目生命周期 ----------------
    def create(self, db, kind: str, origin: str,
               llm_config_id: str, drawthings_config_id: str,
               title: str = "") -> Project:
        """新建短剧项目（kind 参数保留供调度门面兼容；短剧流水线固定 kind=drama）。"""
        project = Project(
            id=uuid.uuid4().hex[:12],
            kind="drama",
            title=(title or "").strip(),
            origin=origin,
            llm_config_id=llm_config_id,
            drawthings_config_id=drawthings_config_id,
            created_at=_now(),
            updated_at=_now(),
            status="planning",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        self.ensure_first_episode(db, project)  # 新项目默认带第一季
        return project

    def get(self, db, project_id: str) -> Project:
        return db.get(Project, project_id)

    def _rm_media(self, path: str):
        """删除媒体文件（仅限 data/media 目录内；失败静默忽略）。"""
        try:
            media_dir = Path(self.data_dir) / "media"
            p = Path(path or "")
            if p and p.is_file() and p.resolve().is_relative_to(media_dir.resolve()):
                p.unlink()
        except OSError:
            pass

    def delete_project(self, db, project: Project):
        """删除项目：先删媒体文件（章节媒体 + 封面 + 各季角色参考图），再删季、章节与项目记录。"""
        for ch in db.query(Chapter).filter(Chapter.project_id == project.id).all():
            self._rm_media(ch.media_path)
            db.delete(ch)
        for s in db.query(Season).filter(Season.project_id == project.id).all():
            for c in chars_from_raw(s.characters):
                self._rm_media(c.get("image"))
            db.delete(s)
        self._rm_media(project.first_image)
        db.delete(project)
        db.commit()

    def list_projects(self, db, kind=None, status=None, q=None, sort="desc",
                      limit: int = 10, offset: int = 0) -> tuple[list, int]:
        """列表：类型/状态/关键词（标题或主题）筛选 + 排序。

        sort: "desc"=最新创建 / "asc"=最早创建 / "active"=最近活跃（updated_at 倒序）。"""
        query = db.query(Project)
        if kind:
            query = query.filter(Project.kind == kind)
        if status:
            query = query.filter(Project.status == status)
        if q:
            kw = f"%{q}%"
            query = query.filter(or_(Project.title.ilike(kw), Project.origin.ilike(kw)))
        total = query.count()
        if sort == "active":
            order = Project.updated_at.desc()
        elif sort == "asc":
            order = Project.created_at.asc()
        else:
            order = Project.created_at.desc()
        rows = query.order_by(order).limit(limit).offset(offset).all()
        return rows, total

    def _save(self, db, project: Project):
        project.updated_at = _now()
        db.add(project)
        db.commit()
        db.refresh(project)

    # ---------------- 季（篇章）管理 ----------------
    def _load_episodes(self, db, project: Project) -> list[Season]:
        """项目下的季，按季号升序。"""
        return (db.query(Season)
                .filter(Season.project_id == project.id)
                .order_by(Season.number).all())

    def _get_episode(self, db, project: Project, season_id: str) -> Season | None:
        s = db.get(Season, season_id)
        if s and s.project_id == project.id:
            return s
        return None

    def _episode_clips(self, db, project: Project, season: Season) -> list[Chapter]:
        """某一季的章节（按扁平序号有序，即季内顺序）。"""
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id, Chapter.season_id == season.id)
                .order_by(Chapter.index).all())

    def _combined_chars(self, db, project: Project, season: Season) -> list[dict]:
        """本集可用角色 = 第 1..本集 全部集的角色合并（同 id 者后集覆盖前集）。

        已无项目层核心角色：角色全部挂在季上。后续集会参考前面各集的角色，
        因此本集的剧情/剧本上下文必须能看到**截至本集**的所有角色（不含后续集，避免剧透）。"""
        # 按**名字**去重（而非 id）：每集生成角色时 id 都是新 UUID，同一个人物会被重复登记；
        # 后面的集覆盖前面的描述，保留最新的设定。
        merged: dict[str, dict] = {}
        rows = (db.query(Season)
                .filter(Season.project_id == project.id, Season.number <= season.number)
                .order_by(Season.number.asc()).all())
        for s in rows:
            for c in chars_from_raw(s.characters):
                key = (c.get("name") or "").strip()
                merged[key if key else c["id"]] = dict(c)
        return list(merged.values())

    def _require_season_chars(self, season: Season, lang: str = "zh") -> list[dict]:
        """本集角色不得为空：角色是各段提示词保持人物一致的唯一依据。

        角色为空仍继续拆章，会让后续每段都凭空生成人物 —— 故在此直接拦下。"""
        chars = chars_from_raw(season.characters)
        if not chars:
            raise RuntimeError(L(lang, "本集还没有角色，请先生成或填写本集角色再继续",
                                 "This episode has no characters — generate or fill them in first"))
        return chars

    def _require_episode_arc(self, season: Season, lang: str = "zh") -> str:
        """取本集大纲；为空即抛错（拆章 / 集角色等步骤必须先有本集大纲）。

        项目层不再有总纲兜底：本集大纲是唯一的剧情依据，为空时静默继续会让 LLM 凭空编剧情。"""
        arc = (season.arc or "").strip()
        if not arc:
            raise RuntimeError(L(lang, "请先生成或填写本集大纲再继续",
                                 "Please create or fill in this episode's outline first"))
        return arc

    def _overall_arc(self, db, project: Project) -> str:
        """全篇主线（供项目级步骤参考：核心角色 / 封面提示词）。

        已无项目层总纲，取**第一集大纲**作为全篇起点 —— 第 1 集的路线即作品开篇主线。
        第 1 集大纲尚未生成时返回空串（调用方按「无大纲」处理）。"""
        first = (db.query(Season)
                 .filter(Season.project_id == project.id)
                 .order_by(Season.number.asc()).first())
        return (first.arc or "").strip() if first is not None else ""

    def _prev_episode_last_clip(self, db, project: Project, season: Season) -> Chapter | None:
        """上一季最后一章（用于新季第 1 章的参考图链）。"""
        if season.number <= 1:
            return None
        prev = (db.query(Season)
                .filter(Season.project_id == project.id, Season.number == season.number - 1)
                .first())
        if not prev:
            return None
        chs = self._episode_clips(db, project, prev)
        return chs[-1] if chs else None

    def ensure_first_episode(self, db, project: Project) -> Season:
        """保证项目至少存在第一季（幂等）：无任何季时自动建第一季，
        并把孤儿章节（season_id 为空的旧数据）并入第一季后重排扁平序号。"""
        seasons = self._load_episodes(db, project)
        if seasons:
            return seasons[0]
        season = Season(id=uuid.uuid4().hex[:12], project_id=project.id, number=1,
                        title="", created_at=_now(), updated_at=_now())
        db.add(season)
        db.flush()
        orphans = (db.query(Chapter)
                   .filter(Chapter.project_id == project.id, Chapter.season_id.is_(None))
                   .order_by(Chapter.index).all())
        for ch in orphans:
            ch.season_id = season.id
        if orphans:
            self._reindex_flat(db, project)
        db.commit()
        db.refresh(season)
        return season

    def add_episode(self, db, project: Project, title: str = "") -> Season:
        """新增一季（季号 = 现有最大季号 + 1；空项目从 1 起）。"""
        seasons = self._load_episodes(db, project)
        number = (seasons[-1].number if seasons else 0) + 1
        season = Season(id=uuid.uuid4().hex[:12], project_id=project.id, number=number,
                        title=(title or "").strip(), created_at=_now(), updated_at=_now())
        db.add(season)
        db.commit()
        db.refresh(season)
        self._save(db, project)
        return season

    def rename_episode(self, db, project: Project, season_id: str, title: str) -> Season:
        """修改季名。"""
        season = self._get_episode(db, project, season_id)
        if season is None:
            raise ValueError("集不存在 (Episode not found)")
        season.title = (title or "").strip()[:200]
        season.updated_at = _now()
        db.commit()
        db.refresh(season)
        self._save(db, project)
        return season

    def delete_episode(self, db, project: Project, season_id: str) -> Project:
        """删除一季：连同其章节（清理媒体）与季角色参考图；剩余季重排季号 + 章节扁平重编号。"""
        season = self._get_episode(db, project, season_id)
        if season is None:
            raise ValueError("集不存在 (Episode not found)")
        for ch in self._episode_clips(db, project, season):
            self._rm_media(ch.media_path)
            db.delete(ch)
        for c in chars_from_raw(season.characters):
            self._rm_media(c.get("image"))
        db.delete(season)
        db.commit()
        # 剩余季重排季号（保持相对顺序连续 1..S）
        for i, s in enumerate(self._load_episodes(db, project), start=1):
            s.number = i
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return project

    def _reindex_flat(self, db, project: Project) -> None:
        """按 (季号, 季内顺序) 重排全部章节的扁平序号 0..N-1。

        季内顺序 = 当前扁平顺序按季分组（保持相对次序）。"""
        flat = self._load_chapters(db, project)
        season_no = {s.id: s.number for s in self._load_episodes(db, project)}
        groups: dict[str, list[Chapter]] = {}
        order: list[str] = []
        for ch in flat:
            sid = ch.season_id or ""
            if sid not in groups:
                groups[sid] = []
                order.append(sid)
            groups[sid].append(ch)
        # 排序前记录每季的原始位置作为同季号时的次序 tiebreaker
        # （不能直接 order.index(sid)：list.sort 在计算 key 时会清空原列表，导致 index 抛 ValueError）
        orig_pos = {sid: i for i, sid in enumerate(order)}
        order.sort(key=lambda sid: (season_no.get(sid, 0), orig_pos[sid]))
        new_idx = 0
        for sid in order:
            for ch in groups[sid]:
                ch.index = new_idx
                new_idx += 1
        db.commit()

    def _episode_base_index(self, db, project: Project, season: Season) -> int:
        """向某季插入新章时的起始扁平序号 = 所有前序季（季号 < 本季）的章节总数；无前序季返回 0。

        （旧实现只算紧邻上一季的章数——第 3 季起新章会与更前面的季撞号；
        收尾仍统一走 _reindex_flat 保证整体 0..N-1 连续。）"""
        if season.number <= 1:
            return 0
        prev_ids = [s.id for s in (db.query(Season)
                                   .filter(Season.project_id == project.id,
                                           Season.number < season.number).all())]
        if not prev_ids:
            return 0
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id,
                        Chapter.season_id.in_(prev_ids)).count())

    # ---------------- 企划（每步独立生成：本集大纲 / 角色设定）+ 章节规划 ----------------
    async def step_episode_arc(self, db, project: Project, season: Season, lang: str = "zh",
                              extra_prompt: str = "") -> Project:
        """「生成本集大纲」：**一次调用同时负责全篇主线与本集路线**（原「生成大纲」已并入本步）。

        输入：项目名称（主题）+ 全局要求 + 核心角色 + 本集集号/集名 + 全篇已有剧情线（其他集的大纲）；
        输出：本集四段式大纲 + 3-6 条关键剧情节点 beats，供后续拆章逐章落位。
        作品标题也随本步产出（仅当尚未命名时采用，可在总体页手动改）。
        不触碰全局要求 / 角色 / 章节。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        gprompt = (project.global_prompt or "").strip()
        season_no = season.number
        season_title = (season.title or "").strip()
        chars = chars_to_text(self._combined_chars(db, project, season))
        system = ("你是资深短剧策划兼编剧。根据项目名称（主题）、全局要求与本集集号/集名，"
                  "先在心里理清**整部作品的全篇主线**（开端/发展/高潮/结局，以及贯穿全篇的主线设定），"
                  "再据此写**本集**的剧情大纲：说明本集承接主线的哪一段、本集的开端、发展、高潮、结局。\n"
                  "大纲分 开端、发展、高潮、结局 四段，每段 1-2 句；"
                  "再给出 3-6 条本集**关键剧情节点 beats**（按时间顺序的转折/冲突/爽点节拍，"
                  "每条一句话、可独立成章的推进点），供后续拆章逐章落位。\n"
                  "要求：① 节点均匀覆盖本集全程（开端→高潮→结局），不要都堆在开头或结尾；"
                  "② beats 之间为因果递进（前一个引发后一个），最后一条落到本集结局/下一集钩子；"
                  "③ 多集作品须对齐全篇已有剧情线：与更早的集保持一致（不推翻其已确立的设定）、承接紧邻前集的结局、"
                  "为紧邻后集埋线，且不提前展开后续集的剧情。\n"
                  "另请给出一个吸引人的**作品标题**（title，简短精炼，5-15 字为宜）与一个简洁的"
                  "**集名**（篇章名，如「归来篇」）。")
        agent = make_agent(build_model(llm_cfg), system, output_type=SeasonArcOut)
        user = f"项目名称（主题）：{project.origin or ''}\n集号：第 {season_no} 集"
        if season_title:
            user += f"\n集名：{season_title}"
        if gprompt:
            user += f"\n全局要求（风格 + 务必涵盖/遵循的要点）：{gprompt}"
        if chars:
            user += f"\n角色设定（请保持一致）：\n{chars}"
        ctx = self._episode_neighbor_context(db, project, season)
        if ctx:
            user += f"\n{ctx}"
        if (extra_prompt or "").strip():
            user += f"\n额外要求：{(extra_prompt or '').strip()}"
        async with agent:
            data = (await agent.run(user)).output
        arc = (data.arc or "").strip()
        if not arc:
            raise RuntimeError(L(lang, "模型未返回本集大纲内容，请重试",
                                 "The model returned no episode outline content — please retry"))
        season.arc = self._compose_episode_arc(arc, data.beats)
        # 作品标题：仅在尚未命名（还是新建时的项目名）时采用模型建议
        if (data.title or "").strip() and (project.title or "").strip() in ("", (project.origin or "").strip()):
            project.title = (data.title or "").strip()[:200]
        # 若本集尚无名字且模型给出，则采用模型建议的集名
        if not season_title and (data.title or "").strip():
            season.title = (data.title or "").strip()
        self._save(db, project)
        return project

    def _compose_episode_arc(self, arc: str, beats: list[str]) -> str:
        """把四段式季大纲与关键剧情节点合并为一段可存储文本（beats 作为独立小节追加）。"""
        arc = (arc or "").strip()
        items = [str(b).strip() for b in (beats or []) if str(b).strip()]
        if not items:
            return arc
        lines = "\n".join(f"{i}. {b}" for i, b in enumerate(items, 1))
        return f"{arc}\n\n关键剧情节点：\n{lines}"

    def _episode_neighbor_context(self, db, project: Project, season: Season) -> str:
        """全篇已有剧情线：**除本集外、已写过大纲的所有集**（按集号排序），供本集对齐全篇主线。

        不只看紧邻前后集 —— 否则第 3 集完全不知道第 1、2 集讲过什么，多集之间会主线漂移
        （项目层已无独立总纲，各集大纲就是全篇主线的唯一载体，必须互相可见）。
        紧邻的前/后集额外给出「承接结局 / 埋下钩子」的明确约束；更早的集要求不得推翻其设定。
        每集大纲按字数截断，保证多集时上下文不膨胀。"""
        others = (db.query(Season)
                  .filter(Season.project_id == project.id, Season.id != season.id)
                  .order_by(Season.number.asc()).all())
        parts = []
        for s in others:
            arc = clip_text(s.arc, SEASON_ARC_CONTEXT_CHARS)
            if not arc:
                continue
            label = f"第{s.number}集" + (f"（{s.title}）" if s.title else "")
            if s.number == season.number - 1:
                parts.append(f"{label}（紧邻前集）：{arc}\n  → 本集须承接其结局，不要重复其剧情")
            elif s.number == season.number + 1:
                parts.append(f"{label}（紧邻后集）：{arc}\n  → 本集结局需为其埋线，但不要提前展开它的内容")
            elif s.number < season.number:
                parts.append(f"{label}（更早的集，已发生）：{arc}\n  → 本集须与之一致，不得推翻其已确立的设定")
            else:
                parts.append(f"{label}（后续的集）：{arc}\n  → 本集为其铺垫，不要抢跑它的剧情")
        return ("全篇已有剧情线（其他集的大纲）：\n" + "\n".join(parts)) if parts else ""

    async def step_episode_chars(self, db, project: Project, season: Season, lang: str = "zh",
                                 extra_prompt: str = "") -> Project:
        """「生成本集角色」：基于本集大纲 + 全局要求 + **前面各集已有角色**，列出本集角色。

        角色只挂在季上（无项目层核心角色）：生成时会看到第 1..本集-1 集的角色并**沿用**
        前面已建立的人物，只补本集新出场的人物，保证跨集人物形象连贯。
        本集角色**不得为空** —— 角色是各段提示词保持人物一致的唯一依据。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        gprompt = (project.global_prompt or "").strip()
        season_arc = self._require_episode_arc(season, lang)
        prior = self._combined_chars(db, project, season)   # 含本集旧角色（重生成时沿用）
        prior_names = "、".join((c.get("name") or "").strip() for c in prior
                                if (c.get("name") or "").strip())
        prior_text = chars_to_text(prior)
        system = ("你是资深短剧策划。根据本集剧情大纲与已有角色，列出**本集出场**的角色："
                  "已有角色请沿用其设定（可微调，但不要改名或重写成另一个人物），只补本集新出场的人物；"
                  "每个角色给出 名字 + 形象/性格/核心动机（每人 2-4 句），供本篇章各段保持角色一致。"
                  "本集至少要有一个角色。")
        agent = make_agent(build_model(llm_cfg), system, output_type=CharsOut)
        user = f"本集大纲：\n{season_arc}"
        if gprompt:
            user += f"\n全局要求（风格 + 务必遵循的要点）：{gprompt}"
        if prior_text:
            user += f"\n已有角色（请沿用，仅在需要时补充本集新增的人物）：\n{prior_text}"
        if prior_names:
            user += f"\n已有人物：{prior_names}"
        if (extra_prompt or "").strip():
            user += f"\n额外要求：{(extra_prompt or '').strip()}"
        async with agent:
            data = (await agent.run(user)).output
        chars = [{
            "id": uuid.uuid4().hex[:12],
            "name": (c.name or "").strip(),
            "description": (c.description or "").strip(),
            "image": "",
        } for c in (data.characters or [])
            if ((c.name or "").strip() or (c.description or "").strip())]
        if not chars:
            raise RuntimeError(L(lang, "本集还没有角色：模型未返回角色设定，请重试",
                                 "No characters for this episode — the model returned none, please retry"))
        # 重新生成本集角色 = 整体替换：清理旧季角色的参考图文件（若有）
        for old in chars_from_raw(season.characters):
            self._rm_media(old.get("image"))
        season.characters = chars_to_raw(chars)
        self._save(db, project)
        return project

    async def gen_char_description(self, db, project: Project, season: Season, char_id: str,
                                   lang: str = "zh") -> str:
        """AI 生成单个季角色的形象/性格描述（2-4 句）。
        该角色有参考图且 LLM 支持视觉时，以图片为形象基准（文设贴合图）；
        并结合全局要求 / 本集大纲保持一致。返回写回后的描述。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        chars = chars_from_raw(season.characters)
        char = next((c for c in chars if c["id"] == char_id), None)
        if char is None:
            raise ValueError("角色不存在 (Character not found)")
        gprompt = (project.global_prompt or "").strip()
        arc = (season.arc or "").strip()
        name = (char.get("name") or "").strip()
        system = ("你是资深短剧策划。为一个角色写 形象/性格/核心动机（2-4 句），供后续各段保持角色一致。"
                  "若附带了角色参考图，请以图片为依据描述外形（服饰 / 相貌 / 气质等），让文字设定与图片一致。")
        agent = make_agent(build_model(llm_cfg), system, output_type=CharDescOut)
        user_text = f"项目名称（主题）：{project.origin}"
        if gprompt:
            user_text += f"\n全局要求（风格 + 务必遵循的要点）：{gprompt}"
        if name:
            user_text += f"\n角色名字：{name}"
        if arc:
            user_text += f"\n本集大纲：\n{arc}"
        user_text += "\n请写出这个角色的形象 / 性格 / 核心动机。"
        # 参考图：角色有图即附带（VLM 一律支持图片输入，多模态入图）
        img = (char.get("image") or "").strip()
        prompt: str | list = user_text
        if img and Path(img).is_file():
            prompt = [ImageUrl(url=image_data_uri(img)), user_text]
        async with agent:
            data = (await agent.run(prompt)).output
        desc = (data.description or "").strip()
        if not desc:
            raise RuntimeError(L(lang, "模型未返回角色描述，请重试",
                                 "The model returned no character description — please retry"))
        char["description"] = desc
        season.characters = chars_to_raw(chars)
        self._save(db, project)
        return desc

    def _rebuild_clips(self, db, project: Project, season: Season,
                          plan: list[tuple[str, str, int]]) -> None:
        """按 [(标题, 主题摘要, 建议时长秒)] 重建**该季**章节：清空旧章节（清理其媒体）后按序建新的。
        时长元素可缺省（2 元组）：缺省按 0 处理（0=由生成剧本时决定 / 跟随配置上限）。"""
        for ch in self._episode_clips(db, project, season):
            self._rm_media(ch.media_path)
            db.delete(ch)
        db.commit()
        base = self._episode_base_index(db, project, season)
        for i, item in enumerate(plan):
            title, summary = item[0], item[1]
            sec = int(item[2]) if (len(item) > 2 and item[2]) else 0
            db.add(Chapter(project_id=project.id, season_id=season.id,
                           index=base + i, title=title, summary=summary, seconds=sec))
        db.commit()
        self._reindex_flat(db, project)

    def save_outline(self, db, project: Project, *,
                      global_prompt: str | None = None,
                      res_width: int | None = 0, res_height: int | None = 0,
                      auto_score: int | None = None, score_min: int | None = None,
                      auto_redo: int | None = None, stop_on_low: int | None = None) -> Project:
        """保存总体页手动编辑：全局要求（风格 + 要点）/ 角色设定（多个角色：名字+描述，按 id 保留参考图）/ 默认分辨率 / 评分设置。
        仅更新传入（非 None）的字段；不触碰章节与各集大纲（季按季编辑，见 save_episode），
        也不触碰已生成的剧本/媒体（保存不触发生成）。"""
        if global_prompt is not None:
            project.global_prompt = (global_prompt or "").strip()
        if res_width is not None:
            project.res_width = int(res_width or 0)
        if res_height is not None:
            project.res_height = int(res_height or 0)
        if auto_score is not None:
            project.auto_score = 1 if auto_score else 0
        if score_min is not None:
            project.score_min = max(0, min(100, int(score_min or 0)))
        if auto_redo is not None:
            project.auto_redo = 1 if auto_redo else 0
        if stop_on_low is not None:
            project.stop_on_low = 1 if stop_on_low else 0
        db.commit()
        self._save(db, project)
        return project

    def save_episode(self, db, project: Project, season: Season, *,
                    title: str | None = None, arc: str | None = None,
                    characters: list[dict] | None = None,
                    count_mode: str | None = None, count_min: int | None = None, count_max: int | None = None,
                    chapters: list[dict] | None = None) -> Season:
        """保存季（篇章）级编辑：季名 / 季大纲 / 季角色（名字+描述，按 id 保留参考图）/ 章节数量设定 / 每章(标题+摘要)。
        仅更新传入（非 None）的字段；章节按季内序号对账。"""
        if title is not None:
            season.title = (title or "").strip()[:200]
        if arc is not None:
            season.arc = (arc or "").strip()
        if characters is not None:
            existing = {c["id"]: c for c in chars_from_raw(season.characters)}
            new_chars = []
            for item in characters:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id") or "").strip() or uuid.uuid4().hex[:12]
                old = existing.get(cid) or {}
                img = (old.get("image") or "").strip()
                if img and not Path(img).is_file():
                    img = ""
                new_chars.append({"id": cid, "name": str(item.get("name") or "").strip(),
                                  "description": str(item.get("description") or "").strip(), "image": img})
            kept = {c["id"] for c in new_chars}
            for cid, c in existing.items():
                if cid not in kept and c.get("image"):
                    self._rm_media(c["image"])
            season.characters = chars_to_raw(new_chars)
        if count_mode is not None:
            season.count_mode = "range"           # 仅范围模式（兼容旧参数：一律按范围处理）
        if count_min is not None or count_max is not None:
            lo, hi = count_range(count_min if count_min is not None else season.count_min,
                                 count_max if count_max is not None else season.count_max)
            season.count_min, season.count_max = lo, hi
        if chapters is not None:
            existing = self._episode_clips(db, project, season)
            for i, item in enumerate(chapters):
                t = (item.get("title") or "").strip()
                s = (item.get("summary") or "").strip()
                if i < len(existing):
                    if t:
                        existing[i].title = t
                    existing[i].summary = s
                else:
                    base = self._episode_base_index(db, project, season) + len(existing)
                    db.add(Chapter(project_id=project.id, season_id=season.id,
                                   index=base + (i - len(existing)),
                                   title=t or f"第{i + 1}段", summary=s))
            for ch in existing[len(chapters):]:
                self._rm_media(ch.media_path)
                db.delete(ch)
            self._reindex_flat(db, project)
        season.updated_at = _now()
        db.commit()
        db.refresh(season)
        self._save(db, project)
        return season

    async def step_clips(self, db, project: Project, season: Season, lang: str = "zh",
                             count_min: int = 0, count_max: int = 0) -> Project:
        """「章节规划」：依据季大纲/风格/角色 + 章节数量范围（min~max）规划章节（标题 + 主题摘要）。
        会清空该季已有章节/媒体（按大纲重新拆章）。status → chaptered。"""
        self._require_episode_arc(season, lang)
        self._require_season_chars(season, lang)
        season.count_mode = "range"              # 仅范围模式
        season.count_min, season.count_max = count_range(count_min, count_max)
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        n = await self._plan_clip_count(db, project, season, lang,
                                           season.count_min, season.count_max, model=model)
        prior: list[tuple[str, str, int]] = []
        plan: list[tuple[str, str, int]] = []
        for i in range(1, n + 1):
            t, s, sec = await self._plan_one_clip(db, project, season, lang, i, n, prior, model=model)
            prior.append((t, s, sec))
            plan.append((t, s, sec))
        self._rebuild_clips(db, project, season, plan)
        project.status = "chaptered"
        self._save(db, project)
        return project

    async def _plan_clip_count(self, db, project: Project, season: Season, lang: str,
                                   count_min: int, count_max: int, model=None) -> int:
        """先定总章数：在 [count_min, count_max] 范围内选一个合适的数。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        gprompt = (project.global_prompt or "").strip()
        arc = self._require_episode_arc(season, lang)
        system = "你是分章策划。依据本季大纲判断应拆分为多少章，只给出章数（一个整数）。"
        agent = make_agent(model or build_model(llm_cfg), system, output_type=ChapterCount)
        lo, hi = count_range(count_min, count_max)
        if lo == hi:
            return lo                             # 固定值（min=max）：无需 LLM 挑选
        cnt = f"请从 {lo} 到 {hi} 之间选一个合适的章数（若大纲含「关键剧情节点」，章数宜不少于节点数）"
        user = ((f"全局要求（风格 + 务必涵盖/遵循的要点）：{gprompt}\n" if gprompt else "")
                + f"{cnt}\n\n本季大纲：\n{arc}")
        async with agent:
            data = (await agent.run(user)).output
        return max(1, int(data.count or 0))

    async def _plan_one_clip(self, db, project: Project, season: Season, lang: str, i: int, n: int,
                                 prior: list[tuple[str, str]], model=None) -> tuple[str, str, int]:
        """规划第 i 章（共 n 章）：参考前面已规划的章节承接剧情，返回 (标题, 一句话主题摘要, 0)。
        若本季大纲含「关键剧情节点」，则要求本章落位到对应节点，保证节点均匀覆盖整季。
        注意：规划**不写时长**（时长默认 0 = 跟随配置/预设上限；由用户在卡片手动设置或用工具条「批量时长」）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        chars = chars_to_text(self._combined_chars(db, project, season))
        gprompt = (project.global_prompt or "").strip()
        arc = self._require_episode_arc(season, lang)
        system = ("你是分章策划。为故事规划第 i/n 章，只输出本章：标题（简短）+ 一句话主题摘要"
                  "（讲清本章发生什么、如何承接前面章节并推进本季大纲）。"
                  "若本季大纲给出了「关键剧情节点」，请让本章**落在对应比例的节点上**"
                  "（第 i/n 章对应节点序列中约第 ⌈i/n × 节点数⌉ 个），把该节点展开为本章内容；"
                  "不要提前消耗后面的节点，也不要跳过。")
        agent = make_agent(model or build_model(llm_cfg), system, output_type=ChapterOut)
        prior_text = "\n".join(f"第{k}章：{t}（{s}）" for k, (t, s, *_r) in enumerate(prior, 1)) \
            or "（本章为第一章，尚无前置章节）"
        user = ((f"全局要求（风格 + 务必涵盖/遵循的要点）：{gprompt}\n" if gprompt else "")
                + (f"角色设定：{chars}\n" if chars else "")
                + f"共 {n} 章，现在规划第 {i} 章（进度 {i}/{n}）。\n已规划章节（承接其剧情）：\n{prior_text}\n\n"
                  f"本季大纲（含关键剧情节点，请按比例落位）：\n{arc}")
        async with agent:
            data = (await agent.run(user)).output
        return ((data.title or f"第{i}段").strip(), (data.scene or "").strip(), 0)

    async def step_clips_stream(self, db, project: Project, season: Season, lang: str = "zh",
                                    count_min: int = 0, count_max: int = 0, indices: list[int] | None = None,
                                    mode: str = "replan", progress_cb=None, chapter_done_cb=None,
                                    cancel_event=None) -> Project:
        """「章节规划」逐章版（章节数量：固定值，count_min=count_max=N）：
        - mode='replan'（默认，重做）：indices 省略/空 = 全季重规划（清空该季已有章节/媒体后逐章规划 1..N）；
          indices 非空 = 多选重规划：只重写选中章节的「标题 + 主题摘要」，其余章节与已生成媒体保持不变；
        - mode='append'（新增）：保留已有章节与其媒体，在现有章节末尾之后续规划 N 章（承接前序剧情）。
        status → chaptered。progress_cb(current,total,title) 每章开始前；chapter_done_cb(chapter) 每章规划完成后。"""
        self._require_episode_arc(season, lang)
        self._require_season_chars(season, lang)
        season.count_mode = "range"              # 仅范围模式（固定值即 min=max）
        season.count_min, season.count_max = count_range(count_min, count_max)
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        if mode == "append":
            await self._append_clips(db, project, season, lang, model, season.count_max,
                                        progress_cb, chapter_done_cb, cancel_event)
            project.status = "chaptered"
            self._save(db, project)
            return project
        targets = sorted({int(i) for i in (indices or [])})
        if targets:
            await self._replan_selected(db, project, season, lang, targets, model, progress_cb, chapter_done_cb, cancel_event)
        else:
            await self._replan_episode(db, project, season, lang, model, progress_cb, chapter_done_cb, cancel_event)
        project.status = "chaptered"
        self._save(db, project)
        return project

    async def _replan_episode(self, db, project: Project, season: Season, lang: str, model,
                              progress_cb, chapter_done_cb, cancel_event=None) -> None:
        """全季重规划：清空该季旧章节/媒体后按大纲重新拆章（先定总章数，再逐章规划）。"""
        self._rebuild_clips(db, project, season, [])  # 清空该季旧章节/媒体，随后逐个补入
        n = await self._plan_clip_count(db, project, season, lang,
                                           season.count_min, season.count_max, model=model)
        # base = 所有前序季（季号 < 本季）的章节总数：本季新章紧接「前序季末尾」继续编号
        # （旧实现只算紧邻上一季的章数，第 3 季起会与更前面的季撞号）
        base = self._episode_base_index(db, project, season)
        prior: list[tuple[str, str, int]] = []
        for i in range(1, n + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled()
            if progress_cb:
                await progress_cb(i, n, f"第{i}段")
            title, summary, sec = await self._plan_one_clip(db, project, season, lang, i, n, prior, model=model)
            prior.append((title, summary, sec))
            ch = Chapter(project_id=project.id, season_id=season.id,
                         index=base + i - 1, title=title, summary=summary, seconds=sec)
            db.add(ch)
            db.commit()
            db.refresh(ch)
            if chapter_done_cb:
                await chapter_done_cb(ch)
        # 收尾统一重排扁平序号：本方法逐章插入且中途提交，后续季（季号 > 本季）的旧序号
        # 可能已与新章冲突，须像 _rebuild_clips 一样在末尾重排一次（旧实现漏了这步）。
        self._reindex_flat(db, project)

    async def _replan_selected(self, db, project: Project, season: Season, lang: str, indices: list[int],
                                model, progress_cb, chapter_done_cb, cancel_event=None) -> None:
        """多选重规划：按季内序号就地重写选中章节的标题 + 主题摘要；其余章节、提示词与已生成媒体都不动。"""
        chapters = self._episode_clips(db, project, season)
        n = len(chapters)
        targets = [i for i in indices if 0 <= i < n]
        for pos, idx in enumerate(targets, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled()
            if progress_cb:
                await progress_cb(pos, len(targets), f"第{idx + 1}段")
            # 承接上下文：前面章节（含未选中的）按序传入，规划结果与全季剧情保持连贯
            prior = [(c.title, c.summary, c.seconds or 0) for c in chapters[:idx]]
            title, summary, sec = await self._plan_one_clip(db, project, season, lang, idx + 1, n,
                                                              prior, model=model)
            ch = chapters[idx]
            ch.title, ch.summary = title, summary
            # 注意：重写规划不改动时长（尊重用户手动设置的 seconds）
            db.commit()
            db.refresh(ch)
            if chapter_done_cb:
                await chapter_done_cb(ch)

    async def _append_clips(self, db, project: Project, season: Season, lang: str, model,
                                count: int, progress_cb, chapter_done_cb, cancel_event=None) -> None:
        """新增章节：保留现有章节与其媒体，在现有章节末尾之后续规划 count 章（每章承接前序剧情）。"""
        chapters = self._episode_clips(db, project, season)
        existing = len(chapters)
        base = self._episode_base_index(db, project, season)
        prior: list[tuple[str, str, int]] = [(c.title, c.summary, c.seconds or 0) for c in chapters]
        for k in range(1, count + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled()
            if progress_cb:
                await progress_cb(k, count, f"第{existing + k}段")
            title, summary, sec = await self._plan_one_clip(db, project, season, lang,
                                                               existing + k, existing + count,
                                                               prior, model=model)
            prior.append((title, summary, sec))
            ch = Chapter(project_id=project.id, season_id=season.id,
                         index=base + existing + k - 1, title=title, summary=summary, seconds=sec)
            db.add(ch)
            db.commit()
            db.refresh(ch)
            if chapter_done_cb:
                await chapter_done_cb(ch)
        # 章号可能与后续季旧序号冲突，末尾统一重排扁平序号（同 _replan_episode）
        self._reindex_flat(db, project)

    def _load_chapters(self, db, project: Project) -> list[Chapter]:
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())

    # ---------------- 阶段 4：剧本编写（按章 / 批量） ----------------
    def _script_agent_context(self, db, project: Project, lang: str):
        """剧本生成所需的上下文：LLM 配置 / DrawThings 配置（判断是否图生视频提示词风格）。"""
        llm_cfg, dt_cfg = self._configs(db, project, lang)
        return llm_cfg, dt_cfg

    def _script_system(self, project: Project) -> str:
        """剧本/提示词写作的系统提示词（短剧）。"""
        shot = ("短剧：prompt 描述一段连贯的视频画面；横/竖构图可按场景自选。"
                "prompt 必须与上一画面**连贯**：延续同一角色（外形/服装）、场景、光照与画风，"
                "表现为上一画面之后紧接着发生的动作或运镜。"
                "prompt 必须明确要求画面中**不出现任何文字**：无字幕、无标题、无水印、无 logo、"
                "无人物介绍/名牌/卡片、无时间码/时钟/HUD、无浏览器网址或 UI 叠加、无任何可读文字"
                "（例如加 'no text, no subtitles, no captions, no watermark, no character introduction, "
                "no timestamp, no HUD'）；"
                "且**画面稳定一致**：场景不得无故跳变/瞬移、不得出现转场闪烁或闪帧，"
                "光影（光源方向/色温/时间）与上一画面保持一致。")
        return ("你是编剧兼分镜提示词作者。根据上一章内容和本章场景，"
                "写本章详细剧本描述（description）和出图/出视频提示词（prompt 用英文，保持风格与上一章连贯）。\n"
                f"{shot}\n"
                "分辨率统一由项目总体设定决定，无需决定 width/height（不要在提示词里写具体分辨率）。\n"
                "只输出一个 JSON 对象，字段固定为 description 与 prompt（都是字符串）："
                "description 用一段文字概括本章（即使包含多个分镜，也合成一段文字，不要拆成数组）；"
                "prompt 为单个英文提示词。不要输出数组、Markdown 代码块或任何额外文字。")

    async def _gen_one_clip_script(self, db, project: Project, season: Season, i: int, ch: Chapter,
                               chapters: list[Chapter], lang: str = "zh", model=None,
                               extra_prompt: str = "") -> Chapter:
        """为第 i 章（季内序号）单独写剧本/提示词（供「按章生成」与「批量生成」复用）。
        chapters 为该季的章节列表；i 为季内 0 起序号。model 复用调用方构建的模型（避免逐章重建客户端）。
        extra_prompt：人工「重新生成」时填写的补充修正要求，会作为额外指令交给 LLM 写进 prompt。"""
        llm_cfg, dt_cfg = self._script_agent_context(db, project, lang)
        gprompt = (project.global_prompt or "").strip()
        media_dir = Path(self.data_dir) / "media"
        agent = make_agent(model or build_model(llm_cfg), self._script_system(project),
                           output_type=ScriptOut)
        prev = chapters[i - 1] if i > 0 else None
        context = ""
        ref_img = None
        if prev:
            context = f"上一章：{prev.description}{self._ref_score_line(prev)}"
            prev_media = (prev.media_path or "").strip()
            if prev_media:
                ext = Path(prev_media).suffix.lower()
                if ext in (".mp4", ".mov", ".webm", ".gif"):
                    # 抽末帧走 ffmpeg 子进程：放线程池，避免阻塞事件循环
                    ref_img = await run_sync(extract_last_frame, prev_media, media_dir)
                else:
                    ref_img = prev_media
        else:
            # 季内第 1 章：优先本季封面（若开启）；否则非第一季参考上一季末章；
            # 再否则（第一季且无封面）参考下一章（第 2 章，若已生成）
            season_cover = (season.first_image or "").strip()
            if season.cover_as_first_ref and season_cover and Path(season_cover).is_file():
                context = "附本季封面（本季第 1 章视觉基准）：它是本季的视觉基准（角色形象/风格），请保持主角与风格与其一致。"
                ref_img = season_cover
            elif season.number > 1:
                prev_last = self._prev_episode_last_clip(db, project, season)
                if prev_last and prev_last.media_path:
                    context = f"上一季末章：{prev_last.description}{self._ref_score_line(prev_last)}"
                    pm = (prev_last.media_path or "").strip()
                    ext = Path(pm).suffix.lower()
                    if ext in (".mp4", ".mov", ".webm", ".gif"):
                        ref_img = await run_sync(extract_last_frame, pm, media_dir)
                    else:
                        ref_img = pm
            elif i + 1 < len(chapters):
                nxt = chapters[i + 1]
                if (nxt.media_path or "").strip():
                    context = f"下一章：{nxt.description}{self._ref_score_line(nxt)}"
                    nm = nxt.media_path.strip()
                    ext = Path(nm).suffix.lower()
                    if ext in (".mp4", ".mov", ".webm", ".gif"):
                        ref_img = await run_sync(extract_last_frame, nm, media_dir)
                    else:
                        ref_img = nm
        chars = chars_to_text(self._combined_chars(db, project, season))
        gprompt = (project.global_prompt or "").strip()
        base = (ch.summary or ch.description or "").strip() or "（按大纲与上一章自然续写）"
        user = (
            (f"整体风格：{style}\n" if style else "")
            + (f"角色设定（请保持一致）：{chars}\n" if chars else "")
            + (f"全局要点（务必遵循）：{gprompt}\n" if gprompt else "")
            + f"本章主题摘要：{base}\n\n{context}"
        )
        # 图生视频模式（勾选「支持参考图片」且本章有参考帧）：提示词写成基于参考帧的修改指令，
        # 避免从头完整描述导致重绘覆盖参考画面（功能级开关优先，配置兜底）
        # 冗余防错：先把参考帧读成 data URI（文件丢失 / 损坏 → 退化为纯文本续写，不中断本章）
        ref_uri = None
        if ref_img:
            # 读图 + base64 放线程池（大图编码耗时可观）
            try:
                ref_uri = await run_sync(image_data_uri, ref_img)
            except Exception as e:
                logger.warning("参考图读取失败，本次不带参考图（%s）：%s", ref_img, e)
        if ref_uri and ref_video_enabled(dt_cfg, project):
            user += ("\n【图生视频模式】附图是本次生成的参考帧（上一章画面），视频将基于它生成。"
                     "prompt 必须写成针对参考帧的修改指令：先用一句话点明需与参考帧保持一致的元素"
                     "（角色外形、服装、场景、画风、光照、机位），再具体描述本章的变化（新动作 / 新情节 / 新运镜）；"
                     "不要从头重新描述整个画面。")
        if (extra_prompt or "").strip():
            user += ("\n【人工补充修正要求（务必在 prompt 中落实，修正以下画面问题）】"
                     + extra_prompt.strip())
        prompt_content: str | list = [ImageUrl(url=ref_uri), user] if ref_uri else user
        data = (await agent.run(prompt_content)).output
        ch.description = data.description
        ch.prompt = data.prompt
        # 注意：剧本生成**不写时长**（ch.seconds 保持用户设定；0 = 跟随配置/预设上限）。
        return ch

    def _max_seconds(self, dt_cfg) -> int:
        """单片段时长上限（秒）：配置的 max_seconds（0=不限→内置 10 秒硬上限）。"""
        try:
            cap = int(getattr(dt_cfg, "max_seconds", 0) or 0)
        except (TypeError, ValueError):
            cap = 0
        if cap > 0:
            return min(cap, MAX_VIDEO_SECONDS)
        return MAX_VIDEO_SECONDS

    def _ref_score_line(self, ch: Chapter) -> str:
        """参考章节评分内容：参考章已评分时，把 VLM 分值+评语附进参考上下文，
        让 LLM 知晓参考基准的评分水平（未评分则不附）。"""
        if ch is None or (ch.score or 0) <= 0:
            return ""
        note = (ch.score_note or "").strip()
        return f"（参考评分 {ch.score} 分" + (f"：{note}" if note else "") + "）"

    # ---------------- 阶段 5：逐章生成画面（出图提示词 + 生图，两步连贯） ----------------
    # ---------------- 自动评分 / 手动评分 ----------------
    def _score_system(self, project: Project) -> str:
        """自动评分系统提示词（短剧）：对单章生成视频（抽末帧）按百分制打分。"""
        return ("你是短剧制作的导演。根据当前章节生成视频的末帧抽帧，按 100 分制评估，"
                "只输出一个 JSON 对象，字段：score（0-100 整数）与 note（一句话中文评语，不超过 30 字）。\n"
                "评分标准（按权重）：1) 与本章场景描述 / 出视频提示词的内容是否相符；"
                "2) 角色一致性（角色外形与既有设定一致）；3) 风格一致性（与全局要求一致）；"
                "4) 画面质量（清晰度、构图、色调、无明显畸变 / 伪影）；5) 与上一章画面内容是否连贯。"
                "除 JSON 外不要输出任何文字。")

    async def _score_clip(self, db, project: Project, season: Season, ch: Chapter,
                             model) -> tuple[int, str]:
        """自动评分：对本章已生成的视频（抽末帧）打 0-100 分，返回 (分数, 评语)。"""
        gprompt = (project.global_prompt or "").strip()
        user = (f"章节标题：{ch.title}\n"
                f"场景描述：{ch.description or ch.summary or ''}\n"
                f"出视频提示词：{ch.prompt}\n"
                f"全局要求（风格 + 务必遵循的要点）：{gprompt}")
        media = (ch.media_path or "").strip()
        if not (media and Path(media).is_file()):
            raise ValueError("本章还没有生成视频，无法评分")
        ext = Path(media).suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            frame = media
        else:
            # 视频抽末帧（需 ffmpeg）；取不到帧时**跳过评分并报错**，
            # 绝不退回「视频缺失→打 0 分」，否则会误判低分触发反复重做。
            frame = await run_sync(extract_last_frame, media, Path(self.data_dir) / "media")
        if not (frame and Path(frame).is_file()):
            raise ValueError("无法读取本章视频帧用于评分（需安装 ffmpeg 抽取末帧）；已跳过自动评分")
        prompt = [ImageUrl(url=image_data_uri(frame)),
                  user + "\n请对附带的视频末帧画面评分（标准见系统提示词）。输出 JSON：score（0-100 整数）与 note（一句话评语）。"]
        agent = make_agent(model, self._score_system(project), output_type=ScoreOut)
        async with agent:
            data = (await agent.run(prompt)).output
        score = max(0, min(100, int(data.score or 0)))
        return score, (data.note or "").strip()[:300]

    async def vlm_score_clip(self, db, project: Project, season: Season, index: int,
                                lang: str = "zh") -> tuple[int, str]:
        """VLM 自动评分：对季内第 index 章调用 VLM 重新评分（与流水线自动评分同款，视频抽末帧），
        落库分值 + 评语。无生成视频 / VLM 评分失败 → ValueError（前端提示）。"""
        ch = self._episode_clips(db, project, season)[index]
        media = (ch.media_path or "").strip()
        if not (media and Path(media).is_file()):
            raise ValueError("本章还没有生成视频，无法 VLM 评分")
        try:
            model = build_model(self._llm_cfg(db, project, lang))
            score, note = await self._score_clip(db, project, season, ch, model)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"VLM 评分失败：{e}") from e
        ch.score, ch.score_note = score, note
        db.commit()
        self._save(db, project)
        return score, note

    async def step_generate(self, db, project: Project, season: Season,
                             indices: list[int] | None = None,
                             lang: str = "zh", progress_cb=None,
                             chapter_done_cb=None, score_cb=None, cancel_event=None,
                             extra_prompt: str = "") -> Project:
        """逐章生成画面（季内）：每章跑完整 2 步——① (重新)生成出图提示词/描述/分辨率 ② 生图/生视频。
        开启「自动评分」时追加第 3 步：0-100 评分；低于阈值且开启「低分自动重做」→ 重新生成（最多 MAX_SCORE_REDO 次）。
        score_cb(ch, score, phase, rd)：评分事件回调（phase=scoring/redo/result；score=None 表示进行中）。
        indices: 季内章节序号列表（0 起）；None=该季全部章节。
        季内第 1 章参考：开启「本季封面作为第 1 章参考」→ 本季封面；否则非第一季→上一季末章，第一季→无参考（文生图）。
        其余章沿用上一章媒体。extra_prompt：人工重新生成时填写的补充修正要求（仅单章生成透传）。"""
        dt = self._clients(db, project, lang)
        if cancel_event is not None:
            dt.cancel_event = cancel_event  # 协作式取消：正在跑的生图/生视频尽快停止
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        chapters = self._episode_clips(db, project, season)
        if indices is None:
            targets = list(enumerate(chapters))
        else:
            targets = [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
        try:
            for pos, (i, ch) in enumerate(targets, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise JobCancelled()
                if progress_cb:
                    await progress_cb(pos, len(targets), ch.title)
                # 自动评分：生成画面后按 0-100 评分；低于阈值且开启「低分自动重做」→ 重新生成（最多 MAX_SCORE_REDO 次）
                # 「低于阈值停止生成」为独立开关：某章低于阈值即停止本批后续生成（0=关闭）
                auto_score = bool(project.auto_score)
                auto_redo = bool(project.auto_redo)
                stop_on_low = bool(project.stop_on_low)
                rounds = 1 + MAX_SCORE_REDO if (auto_score and auto_redo) else 1
                stop_batch = False
                scored_ok = False
                score_min = int(project.score_min or 60)
                for rd in range(rounds):
                    if rd:
                        # 上一轮评分低于阈值 → 重做（重新生成提示词 + 视频）
                        if score_cb:
                            await score_cb(ch, None, "redo", rd)
                    await self._gen_one_clip_script(db, project, season, i, ch, chapters, lang, model=model,
                                               extra_prompt=extra_prompt)
                    # 第 2 步：生图 / 生视频（参考上一章图；季内第 1 章参考上一季末章）
                    if i == 0:
                        season_cover = (season.first_image or "").strip()
                        if season.cover_as_first_ref and season_cover and Path(season_cover).is_file():
                            ref = season_cover
                        elif season.number > 1:
                            prev_last = self._prev_episode_last_clip(db, project, season)
                            ref = prev_last.media_path if (prev_last and prev_last.media_path) else ""
                        else:
                            ref = ""
                    else:
                        prev = chapters[i - 1]
                        ref = prev.media_path if (prev and prev.media_path) else ""
                    try:
                        # 分辨率统一跟随总体设定（不再按章覆盖）；本章秒数（>0）覆盖配置上限
                        w = int(project.res_width or 0)
                        h = int(project.res_height or 0)
                        params = {}
                        if w and h:
                            params = {"width": w, "height": h}
                        sec = int(getattr(ch, "seconds", 0) or 0)
                        if sec > 0:
                            params["seconds"] = sec
                        # 提示词追加「无文字/字幕/人物介绍」安全网
                        gen_prompt = (ch.prompt or "").strip() + _VIDEO_NO_TEXT_SUFFIX
                        ch.media_path = await run_sync(
                            partial(dt.generate_video, gen_prompt, ref_video_path=ref, params=params))
                        ch.status = "done"
                        ch.error = ""
                        ch.width = w
                        ch.height = h
                    except Exception as e:
                        ch.status = "error"
                        ch.error = str(e)
                        break  # 生成失败：不评分、不重做
                    if not auto_score:
                        break  # 未开启自动评分：只生成一次
                    if score_cb:
                        await score_cb(ch, None, "scoring", rd)
                    try:
                        score, note = await self._score_clip(db, project, season, ch, model)
                        ch.score, ch.score_note = score, note
                        scored_ok = True
                    except Exception as e:
                        ch.score, ch.score_note = 0, str(e)[:300]  # 评分失败不阻塞流程（并说明原因，不重做）
                        db.commit()
                        break  # 评分失败：不重做（避免拿不到分时反复重生成）
                    db.commit()
                    if score_cb:
                        await score_cb(ch, (ch.score or 0), (ch.score_note or ""), rd)
                    if (ch.score or 0) >= score_min:
                        break  # 达到阈值：本章完成
                if auto_score and scored_ok and (ch.score or 0) < score_min and stop_on_low:
                    # 「低于阈值停止生成」：本章低于阈值 → 停止本批后续生成
                    stop_batch = True
                    if score_cb:
                        await score_cb(ch, (ch.score or 0), "stopped", rd)
                if chapter_done_cb:
                    await chapter_done_cb(ch)
                db.commit()  # 逐章提交：停止/中断时已完成章节不丢失
                if stop_batch:
                    logger.warning("第 %s 章评分 %d 低于阈值 %d，按「低于阈值停止」自动停止后续生成",
                                   ch.index + 1, ch.score or 0, score_min)
                    break
        finally:
            # 异常中断（如用户停止）时尽可能提交当前进度（已完成章节 + 本章已有结果）
            try:
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
        self._save(db, project)
        return project

    # ---------------- 章节字段保存（提示词） ----------------
    def save_clip_fields(self, db, project: Project, season: Season, index: int, prompt: str,
                            seconds: int | None = None) -> Project:
        """手动编辑季内第 index 章的出视频提示词与时长（分辨率统一按总体设定，不再按章覆盖）。
        时长钳制到 [0, 生效上限]（0 = 跟随配置/预设上限）。"""
        ch = self._episode_clips(db, project, season)[index]
        ch.prompt = (prompt or "").strip()
        if seconds is not None:
            try:
                cap = self._max_seconds(self._configs(db, project, "zh")[1])
            except Exception:
                cap = MAX_VIDEO_SECONDS
            ch.seconds = max(0, min(int(seconds), cap))
        db.commit()
        self._save(db, project)
        return project

    # ---------------- 章节增 / 删 / 排序（季内） ----------------
    def add_clip(self, db, project: Project, season: Season) -> Chapter:
        """在该季末尾新增一章（标题/主题摘要留空）。"""
        chapters = self._episode_clips(db, project, season)
        base = self._episode_base_index(db, project, season) + len(chapters)
        ch = Chapter(project_id=project.id, season_id=season.id,
                     index=base, title="新片段", summary="")
        db.add(ch)
        db.commit()
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return ch

    def delete_clip(self, db, project: Project, season: Season, index: int) -> Project:
        """删除季内第 index 章（连同清理其媒体文件），其余章节重新编号。"""
        chapters = self._episode_clips(db, project, season)
        ch = chapters[index]
        self._rm_media(ch.media_path)
        db.delete(ch)
        db.commit()
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return project

    def delete_clips(self, db, project: Project, season: Season,
                     indices: list[int] | None = None) -> Project:
        """批量删除季内片段（连同清理其媒体文件），其余片段重新编号。
        indices=None 表示删除该季全部片段（保留季本身）。"""
        chapters = self._episode_clips(db, project, season)
        targets = list(enumerate(chapters)) if indices is None else \
            [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
        for _, ch in targets:
            self._rm_media(ch.media_path)
            db.delete(ch)
        db.commit()
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return project

    def clear_clips(self, db, project: Project, season: Season,
                       indices: list[int] | None = None) -> Project:
        """清空季内章节的产物：清掉出图/出视频提示词、评分与已生成画面/视频（删除媒体文件、重置状态）。
        保留标题 / 摘要 / 剧本描述等文字。indices=None 表示该季全部章节。"""
        chapters = self._episode_clips(db, project, season)
        targets = list(enumerate(chapters)) if indices is None else \
            [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
        for _, ch in targets:
            self._rm_media(ch.media_path)
            ch.media_path = ""
            ch.prompt = ""
            ch.score = 0
            ch.score_note = ""
            ch.status = "pending"
            ch.error = ""
        db.commit()
        self._save(db, project)
        return project

    def move_clip(self, db, project: Project, season: Season, index: int, direction: str) -> Project:
        """上移 / 下移季内第 index 章（direction: up/down），交换后重新编号。"""
        chapters = self._episode_clips(db, project, season)
        j = index - 1 if direction == "up" else index + 1
        if not (0 <= j < len(chapters)) or j == index:
            return project
        chapters[index], chapters[j] = chapters[j], chapters[index]
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return project

    # ---------------- 首图 ----------------
    def set_first_image_path(self, db, project: Project, path: str) -> Project:
        """记录封面路径（文件已由调用方落盘到 data/media），并把该图存为原图（叠字每次从原图重绘）。"""
        project.first_image = (path or "").strip()
        project.first_image_base = self._snapshot_base(project.first_image)
        self._save(db, project)
        return project

    @staticmethod
    def _snapshot_base(path: str) -> str:
        """把封面复制一份作为「原图」（无叠字）并返回其路径；path 无效返回 ''。"""
        p = Path(path or "")
        if not p.is_file():
            return ""
        base = p.with_name(f"{p.stem}_base{p.suffix}")
        if base.resolve() != p.resolve():
            shutil.copy(p, base)
        return str(base)

    def _base_or_snapshot(self, display: str, base: str) -> str:
        """取原图路径：已有原图直接返回；否则把当前封面存为原图（历史数据兼容）。"""
        b = Path(base or "")
        if b.is_file():
            return str(b)
        return self._snapshot_base(display)

    @staticmethod
    def _restore_base(display: str, base: str) -> None:
        """叠字前先用原图覆盖显示图，保证每次都是从原图重绘（不会越叠越多）。"""
        d, b = Path(display), Path(base or "")
        if b.is_file() and b.resolve() != d.resolve():
            shutil.copy(b, d)

    def set_episode_cover_ref(self, db, project: Project, season: Season, enabled: bool) -> Season:
        """设置是否把季封面作为本季第 1 章参考。"""
        season.cover_as_first_ref = bool(enabled)
        season.updated_at = _now()
        self._save(db, project)
        return season

    def overlay_first_image_title(self, db, project: Project, lang: str = "zh",
                                  opts: dict | None = None) -> Project:
        """把作品标题叠加到现有封面上（可指定位置/字号/样式/颜色）。

        每次从「原图」重新绘制：反复调整参数只会得到最新一次效果，不会层层叠加。
        文件就地覆写，不重新生图。"""
        path = (project.first_image or "").strip()
        if not path or not Path(path).is_file():
            raise ValueError(L(lang, "暂无封面，请先生成或上传封面",
                               "No cover yet — generate or upload one first"))
        title = (project.title or "").strip()
        if not title:
            raise ValueError(L(lang, "作品标题为空，先给作品起个标题再叠加",
                               "The work title is empty — set one first, then overlay"))
        base = self._base_or_snapshot(path, project.first_image_base)
        if base:
            project.first_image_base = base
            self._restore_base(path, base)
        self._overlay_title(Path(path), title, **(opts or {}))
        self._save(db, project)
        return project

    def overlay_episode_first_image_title(self, db, project: Project, season: Season,
                                         lang: str = "zh", opts: dict | None = None) -> Season:
        """把季名叠加到现有季封面上（每次从原图重绘，反复调整不叠加）；文件就地覆写，不重新生图。"""
        path = (season.first_image or "").strip()
        if not path or not Path(path).is_file():
            raise ValueError(L(lang, "暂无季封面，请先生成或上传季封面",
                               "No season cover yet — generate or upload one first"))
        base = self._base_or_snapshot(path, season.first_image_base)
        if base:
            season.first_image_base = base
            self._restore_base(path, base)
        self._overlay_title(Path(path), self._episode_label(season, lang), **(opts or {}))
        season.updated_at = _now()
        self._save(db, project)
        return season

    def set_char_image(self, db, project: Project, season: Season, char_id: str, path: str) -> Project:
        """设置/清除某季角色的参考图（文件已由调用方落盘到 data/media；path 为空 = 清除并删旧图）。"""
        chars = chars_from_raw(season.characters)
        for c in chars:
            if c["id"] == char_id:
                old = c.get("image") or ""
                c["image"] = (path or "").strip()
                if old and old != c["image"]:
                    self._rm_media(old)
                season.characters = chars_to_raw(chars)
                season.updated_at = _now()
                self._save(db, project)
                return project
        raise ValueError("角色不存在（Character not found）")

    def _overlay_title(self, path: Path, title: str, x: float = 0.5, y: float = 1 / 3,
                       size_pct: float = 8.0, style: str = "bold_outline",
                       color: tuple = (255, 255, 255), band: bool = True) -> None:
        """在成品图指定位置叠加标题文字（封面自动生成 / 手动叠加共用）。

        - 位置：x / y 为图宽、图高的比例（0~1；默认水平居中、垂直 1/3 处）
        - 字号：size_pct = 最短边百分比（默认 8%），过宽时自动缩到图宽内
        - 样式：bold_outline（加粗+深色描边）/ outline（描边）/ shadow（阴影）/ plain（无）
        - 颜色：color = (r,g,b) 文字颜色；band = 是否画半透明背景底条

        用系统中文字体渲染（保持原文，不翻译）；标题为空或字体缺失时静默跳过，
        不影响生成本身。就地覆写 path 文件。"""
        title = (title or "").strip()
        font_file = _cjk_font_path()
        if not title or not font_file:
            return
        from PIL import ImageDraw, ImageFont
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        size = max(14, int(min(w, h) * float(size_pct) / 100.0))
        font = ImageFont.truetype(font_file, size)
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), title, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        while tw > w * 0.98 and size > 10:      # 标题过宽 → 逐级缩字号（最多接近图宽）
            size = int(size * 0.88)
            font = ImageFont.truetype(font_file, size)
            bbox = draw.textbbox((0, 0), title, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        cx = int(max(0.0, min(1.0, float(x))) * w)
        cy = int(max(0.0, min(1.0, float(y))) * h)
        if band:
            bh = max(size * 19 // 10, 36)
            by = int(max(0, min(cy - bh // 2, h - bh)))
            shade = Image.new("RGBA", (w, bh), (10, 10, 14, 120))
            img.alpha_composite(shade, (0, by))
            draw = ImageDraw.Draw(img)
        px = cx - tw // 2 - bbox[0]
        py = cy - th // 2 - bbox[1]
        col = (int(color[0]), int(color[1]), int(color[2]), 255)
        if style == "shadow":
            off = max(2, size // 12)
            draw.text((px + off, py + off), title, font=font, fill=(0, 0, 0, 170))
            draw.text((px, py), title, font=font, fill=col)
        elif style == "outline":
            draw.text((px, py), title, font=font, fill=col,
                      stroke_width=max(1, size // 18), stroke_fill=(0, 0, 0, 200))
        elif style == "plain":
            draw.text((px, py), title, font=font, fill=col)
        else:  # bold_outline（默认）：同色描边伪加粗 + 深色外描边
            bold_w = max(2, size // 12)
            draw.text((px, py), title, font=font, fill=col,
                      stroke_width=bold_w + 2, stroke_fill=(0, 0, 0, 200))
            draw.text((px, py), title, font=font, fill=col,
                      stroke_width=bold_w, stroke_fill=col)
        out = img if path.suffix.lower() == ".png" else img.convert("RGB")
        out.save(path)

    async def generate_first_image(self, db, project: Project, prompt: str = "",
                                   lang: str = "zh", include_title: bool = True) -> Project:
        """用 DrawThings 生成封面（文生图）。

        prompt 为「额外提示词」：基础提示词始终由 LLM 结合项目名称/主题 + 全局要求 + 第一集大纲 +
        角色设定自动撰写，额外提示词原样追加在末尾作为补充（留空 = 只用基础提示词）。
        分辨率统一跟随总体设定（project.res_width × res_height，与章节一致）。
        include_title 为真时在成品图上用 PIL 叠加作品名称（标题保持原文）。
        产物存为 data/media/first_<项目id>.<ext>（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        dt = self._clients(db, project, lang)
        extra = (prompt or "").strip()
        gprompt = (project.global_prompt or "").strip()
        system = ("你是封面美术提示词作者。请结合项目名称（主题）、全局要求、故事大纲与角色设定，"
                  "写一段详细的封面英文提示词（主体角色、场景、构图、光线、氛围、风格关键词）。"
                  "只输出提示词文本。画面**不得出现任何文字/标题/字幕条/水印/logo/时间码/时钟/网址或 UI 叠加**"
                  "（作品名由程序叠加，不要在提示词里加入任何文字渲染要求）；光影与场景保持一致稳定。")
        agent = make_agent(build_model(llm_cfg), system)
        user = f"项目名称（主题）：{project.origin}"
        if gprompt:
            user += f"\n全局要求（风格 + 务必遵循的要点）：{gprompt}"
        overall = self._overall_arc(db, project)
        if overall:
            user += f"\n故事大纲（第一集主线）：{overall}"
        chars_text = chars_to_text(self._combined_chars(db, project, self.ensure_first_episode(db, project)))
        if chars_text:
            user += f"\n角色设定：{chars_text}"
        async with agent:
            base = ((await agent.run(user)).output or "").strip()
        # 额外提示词原样追加在末尾（基础提示词在前，补充词只作追加）
        prompt = f"{base}, {extra}" if base and extra else (base or extra)
        if not prompt:
            raise RuntimeError(L(lang, "未能获得封面提示词（LLM 无输出），请稍后重试",
                                 "Could not obtain a cover prompt (empty LLM output) — please retry"))
        # 分辨率统一跟随总体设定（与章节生成一致）
        w = int(project.res_width or 0)
        h = int(project.res_height or 0)
        params = {"width": w, "height": h} if (w and h) else {}
        # Draw Things 生图为同步阻塞调用：放线程池，避免长时间占用事件循环
        path = await run_sync(partial(dt.generate_image, prompt + _VIDEO_NO_TEXT_SUFFIX, params=params))
        media_dir = Path(self.data_dir) / "media"
        dest = media_dir / f"first_{project.id}{Path(path).suffix or '.png'}"
        if Path(path).resolve() != dest.resolve():
            shutil.move(str(path), str(dest))
        # 原图留底（无叠字）：叠字每次都从原图重绘
        old_base = project.first_image_base or ""
        project.first_image_base = self._snapshot_base(str(dest))
        if old_base and Path(old_base).name != Path(project.first_image_base or "").name:
            self._rm_media(old_base)   # 重新生成后旧原图不再使用（扩展名可能变化）
        if include_title:
            # 勾选「包含标题」：生成后用 PIL 叠加作品名称（纯本地快速操作，无需进线程池）
            self._overlay_title(dest, project.title)
        project.first_image = str(dest)
        self._save(db, project)
        return project

    # ---------------- 季封面 ----------------
    def set_episode_first_image_path(self, db, project: Project, season: Season, path: str) -> Season:
        """记录季封面路径（文件已由调用方落盘到 data/media），并把该图存为原图（叠字每次从原图重绘）。"""
        season.first_image = (path or "").strip()
        season.first_image_base = self._snapshot_base(season.first_image)
        season.updated_at = _now()
        self._save(db, project)
        return season

    @staticmethod
    def _episode_label(season: Season, lang: str = "zh") -> str:
        """集名展示：有集名用集名，否则回退「第N集 / Episode N」."""
        return (season.title or "").strip() or L(lang, f"第{season.number}集",
                                                  f"Episode {season.number}")

    async def generate_episode_first_image(self, db, project: Project, season: Season,
                                          prompt: str = "", lang: str = "zh",
                                          include_title: bool = True) -> Season:
        """用 DrawThings 生成季封面（文生图）。

        prompt 为「额外提示词」：基础提示词始终由 LLM 结合项目名称/主题 + 全局要求 + 核心角色与
        集标题/集大纲/集新增角色自动撰写，额外提示词原样追加在末尾作为补充（留空 = 只用基础提示词）。
        分辨率统一跟随总体设定（project.res_width × res_height，与章节一致）。
        include_title 为真时在成品图上用 PIL 叠加集名（集名为空回退「第N集」，标题保持原文）。
        产物存为 data/media/seasonfirst_<季id>.<ext>（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        dt = self._clients(db, project, lang)
        extra = (prompt or "").strip()
        gprompt = (project.global_prompt or "").strip()
        system = ("你是封面美术提示词作者。请结合项目名称（主题）、全局要求、角色设定与本集标题/大纲/新增角色，"
                  "写一段详细的集封面英文提示词（主体角色、场景、构图、光线、氛围、风格关键词）。"
                  "只输出提示词文本。画面**不得出现任何文字/标题/字幕条/水印/logo/时间码/时钟/网址或 UI 叠加**"
                  "（集名由程序叠加，不要在提示词里加入任何文字渲染要求）；光影与场景保持一致稳定。")
        agent = make_agent(build_model(llm_cfg), system)
        user = f"项目名称（主题）：{project.origin}"
        if gprompt:
            user += f"\n全局要求（风格 + 务必遵循的要点）：{gprompt}"
        chars_text = chars_to_text(self._combined_chars(db, project, season))
        if chars_text:
            user += f"\n角色设定：{chars_text}"
        user += f"\n本季：{self._episode_label(season, lang)}"
        if (season.arc or "").strip():
            user += f"\n季大纲：{season.arc.strip()}"
        async with agent:
            base = ((await agent.run(user)).output or "").strip()
        # 额外提示词原样追加在末尾（基础提示词在前，补充词只作追加）
        prompt = f"{base}, {extra}" if base and extra else (base or extra)
        if not prompt:
            raise RuntimeError(L(lang, "未能获得季封面提示词（LLM 无输出），请稍后重试",
                                  "Could not obtain a season cover prompt (empty LLM output) — please retry"))
        # 分辨率统一跟随总体设定（与章节生成一致）
        w = int(project.res_width or 0)
        h = int(project.res_height or 0)
        params = {"width": w, "height": h} if (w and h) else {}
        # Draw Things 生图为同步阻塞调用：放线程池，避免长时间占用事件循环
        path = await run_sync(partial(dt.generate_image, prompt + _VIDEO_NO_TEXT_SUFFIX, params=params))
        media_dir = Path(self.data_dir) / "media"
        dest = media_dir / f"seasonfirst_{season.id}{Path(path).suffix or '.png'}"
        if Path(path).resolve() != dest.resolve():
            shutil.move(str(path), str(dest))
        # 原图留底（无叠字）：叠字每次都从原图重绘
        old_base = season.first_image_base or ""
        season.first_image_base = self._snapshot_base(str(dest))
        if old_base and Path(old_base).name != Path(season.first_image_base or "").name:
            self._rm_media(old_base)
        if include_title:
            # 勾选「包含标题」：生成后用 PIL 叠加季名（纯本地快速操作，无需进线程池）
            self._overlay_title(dest, self._episode_label(season, lang))
        season.first_image = str(dest)
        season.updated_at = _now()
        self._save(db, project)
        return season

    # ---------------- 导出（ZIP / PDF） ----------------
    def _safe_name(self, project: Project) -> str:
        return ((project.title or project.origin or project.id) or "export")[:40]

    def export_zip(self, db, project: Project, season: Season | None = None) -> tuple[str, str]:
        """导出 ZIP：大纲/角色/各章剧本文本 + 媒体（图/视频）+ 首图。
        season 非空时仅导出该季章节（文件名带 S<季号> 后缀）。返回 (zip 绝对路径, 文件名)。"""
        chapters = self._episode_clips(db, project, season) if season is not None else self._load_chapters(db, project)
        export_dir = Path(self.data_dir) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        base = f"{self._safe_name(project)}_{project.id}"
        if season is not None:
            base = f"{base}_S{season.number}"
        fname = f"{base}.zip"
        zpath = export_dir / fname
        # 季封面（总体导出 = 全部季；单季导出 = 仅该季）
        seasons_all = ([season] if season is not None
                        else db.query(Season).filter(Season.project_id == project.id)
                        .order_by(Season.number).all())
        cover_lines = []
        if project.first_image and Path(project.first_image).is_file():
            cover_lines.append(f"项目封面：media/00_first_{Path(project.first_image).name}")
        for s in seasons_all:
            if s.first_image and Path(s.first_image).is_file():
                cover_lines.append(f"第{s.number}集封面：media/00_first_S{s.number}_{Path(s.first_image).name}")
        scope_line = (f"导出范围：第{season.number}集" + (f"（{season.title}）" if season.title else "") + "\n") if season is not None else ""
        readme = (
            f"标题：{project.title}\n类型：{project.kind}\n项目名称（主题）：{project.origin}\n"
            + scope_line +
            f"全局要求：{project.global_prompt or ''}\n"
            f"默认分辨率：{project.res_width}×{project.res_height}\n"
            + (("\n".join(cover_lines) + "\n") if cover_lines else "")
        )
        # 各集大纲 + 各集角色（已无项目层总纲 / 核心角色；剧情与人物依据均在季上）
        for s in seasons_all:
            label = f"第{s.number}集" + (f"（{s.title}）" if s.title else "")
            if (s.arc or "").strip():
                readme += f"\n{label}大纲：\n{s.arc.strip()}\n"
            s_chars = chars_from_raw(s.characters)
            if s_chars:
                readme += f"\n{label}角色：\n{chars_to_text(s_chars)}\n"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", readme.encode("utf-8"))
            # 封面排最前（README 之后）：项目封面 + 各季封面
            if project.first_image and Path(project.first_image).is_file():
                z.write(project.first_image, f"media/00_first_{Path(project.first_image).name}")
            for s in seasons_all:
                if s.first_image and Path(s.first_image).is_file():
                    z.write(s.first_image, f"media/00_first_S{s.number}_{Path(s.first_image).name}")
            for c in chars:
                img = (c.get("image") or "").strip()
                if img and Path(img).is_file():
                    z.write(img, f"media/char_{Path(img).name}")
            for i, ch in enumerate(chapters):
                chap_txt = (
                    f"标题：{ch.title}\n主题摘要：{ch.summary}\n剧本：{ch.description}\n"
                    f"提示词：{ch.prompt}\n分辨率：{ch.width}×{ch.height}\n时长：{ch.seconds or 0} 秒\n媒体：{ch.media_path}\n"
                )
                z.writestr(f"clips/{i:02d}.txt", chap_txt.encode("utf-8"))
                if ch.media_path and Path(ch.media_path).is_file():
                    z.write(ch.media_path, f"media/{i:02d}_{Path(ch.media_path).name}")
        return str(zpath), fname

    def export_video(self, db, project: Project, season: Season | None = None) -> tuple[str, str]:
        """把各章已生成的视频按章序首尾相接，合成为一段 mp4（短剧导出）。
        season 非空时仅合成该季章节。返回 (mp4 绝对路径, 文件名)。
        无可用视频时抛 ValueError（供接口转 400 提示）。"""
        chapters = self._episode_clips(db, project, season) if season is not None else self._load_chapters(db, project)
        clips = [ch.media_path for ch in chapters
                 if (ch.media_path or "").strip() and Path(ch.media_path).is_file()
                 and Path(ch.media_path).suffix.lower() in (".mp4", ".mov", ".webm", ".m4v")]
        if not clips:
            raise ValueError("本集还没有可合成的视频（请先生成片段视频）")
        export_dir = Path(self.data_dir) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        base = f"{self._safe_name(project)}_{project.id}"
        if season is not None:
            base = f"{base}_S{season.number}"
        fname = f"{base}.mp4"
        out = export_dir / fname
        if not concat_videos(clips, out):
            raise ValueError("合成视频失败（需要可用的 ffmpeg，或视频文件损坏）")
        return str(out), fname

    def export_pdf(self, db, project: Project, season: Season | None = None) -> tuple[str, str]:
        """短剧为视频，不支持导出 PDF（请用「合成视频」）；保留同名方法供调度门面统一调用。"""
        raise ValueError("短剧为视频，请使用「合成视频」导出（不再支持 PDF）")


