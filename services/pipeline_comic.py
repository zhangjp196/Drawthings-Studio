"""漫画流水线（独立）：一句话 -> 全局(风格/总纲/核心角色/封面) -> 各季(季大纲/季角色/章节规划) -> 剧本编写 -> 逐章生图。

与短剧流水线（pipeline_drama.py）完全独立、互不共享业务逻辑：
- 剧本系统提示词面向漫画（一页多格、格内带文字、竖版构图）；
- 逐章生成调用 DrawThings **生图**（generate_image），前章图作参考；
- 导出支持 ZIP / PDF（各章图片拼多页 PDF）。

数据全部走 SQLite（models.py），媒体文件存 data/media。
每个项目携带所选 LLMConfig / DrawThingConfig 的 id，运行时现场构建客户端，
因此不同项目可用不同的端点/模型/模式。

多季（篇章）设计（统一世界观 + 各季独立故事，类似七龙珠）：
- 项目层：scope（风格）/ arc（总纲）/ characters（核心角色）/ global_prompt / 封面，全局共享；
- 季层：每季有自己的 arc（季大纲）/ characters（本季新增角色）/ 章节数量设定 / 章节；
- 章节 index 为扁平全局序号（按季连续），季内展示序号由分组位置计算；
- 剧本/生成上下文 = 全局风格 + 核心角色 + 季大纲 + 季角色 + 全局要点；
- 连续性：首章（第 1 季第 1 章）用封面（若开启），其余章用上一章媒体（跨季承接上季末章）。
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
    ArcOut,
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
from .drawthings import extract_last_frame
from .capabilities import dt_client, ref_image_enabled
from .pipeline_common import (
    MAX_SCORE_REDO,
    _cjk_font_path,
    _now,
    chars_from_raw,
    chars_to_raw,
    chars_to_text,
    count_range,
    run_sync,
)


logger = logging.getLogger("drawthings")

class ComicPipeline:
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
               style: str = "", title: str = "") -> Project:
        """新建漫画项目（kind 参数保留供调度门面兼容；漫画流水线固定 kind=comic）。"""
        style = (style or "").strip()
        project = Project(
            id=uuid.uuid4().hex[:12],
            kind="comic",
            title=(title or "").strip(),
            origin=origin,
            llm_config_id=llm_config_id,
            drawthings_config_id=drawthings_config_id,
            created_at=_now(),
            updated_at=_now(),
            status="planning",
            # 用户指定风格优先；留空则由 LLM 在“设定篇幅”阶段推荐
            scope={"style": style} if style else {},
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        self.ensure_first_season(db, project)  # 新项目默认带第一季
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
        """删除项目：先删媒体文件（章节媒体 + 封面 + 核心角色/季角色参考图），再删季、章节与项目记录。"""
        for ch in db.query(Chapter).filter(Chapter.project_id == project.id).all():
            self._rm_media(ch.media_path)
            db.delete(ch)
        for s in db.query(Season).filter(Season.project_id == project.id).all():
            for c in chars_from_raw(s.characters):
                self._rm_media(c.get("image"))
            db.delete(s)
        for c in chars_from_raw(project.characters):
            self._rm_media(c.get("image"))
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
    def _load_seasons(self, db, project: Project) -> list[Season]:
        """项目下的季，按季号升序。"""
        return (db.query(Season)
                .filter(Season.project_id == project.id)
                .order_by(Season.number).all())

    def _get_season(self, db, project: Project, season_id: str) -> Season | None:
        s = db.get(Season, season_id)
        if s and s.project_id == project.id:
            return s
        return None

    def _season_chapters(self, db, project: Project, season: Season) -> list[Chapter]:
        """某一季的章节（按扁平序号有序，即季内顺序）。"""
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id, Chapter.season_id == season.id)
                .order_by(Chapter.index).all())

    def _combined_chars(self, project: Project, season: Season) -> list[dict]:
        """合并项目核心角色 + 季新增角色（季角色覆盖同 id 项目角色）。"""
        merged = {c["id"]: dict(c) for c in chars_from_raw(project.characters)}
        for c in chars_from_raw(season.characters):
            merged[c["id"]] = dict(c)
        return list(merged.values())

    def _season_arc(self, project: Project, season: Season) -> str:
        """季大纲（为空则回退项目总纲）。"""
        return (season.arc or "").strip() or (project.arc or "").strip()

    def _prev_season_last_chapter(self, db, project: Project, season: Season) -> Chapter | None:
        """上一季最后一章（用于新季第 1 章的参考图链）。"""
        if season.number <= 1:
            return None
        prev = (db.query(Season)
                .filter(Season.project_id == project.id, Season.number == season.number - 1)
                .first())
        if not prev:
            return None
        chs = self._season_chapters(db, project, prev)
        return chs[-1] if chs else None

    def ensure_first_season(self, db, project: Project) -> Season:
        """保证项目至少存在第一季（幂等）：无任何季时自动建第一季，
        并把孤儿章节（season_id 为空的旧数据）并入第一季后重排扁平序号。"""
        seasons = self._load_seasons(db, project)
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

    def add_season(self, db, project: Project, title: str = "") -> Season:
        """新增一季（季号 = 现有最大季号 + 1；空项目从 1 起）。"""
        seasons = self._load_seasons(db, project)
        number = (seasons[-1].number if seasons else 0) + 1
        season = Season(id=uuid.uuid4().hex[:12], project_id=project.id, number=number,
                        title=(title or "").strip(), created_at=_now(), updated_at=_now())
        db.add(season)
        db.commit()
        db.refresh(season)
        self._save(db, project)
        return season

    def rename_season(self, db, project: Project, season_id: str, title: str) -> Season:
        """修改季名。"""
        season = self._get_season(db, project, season_id)
        if season is None:
            raise ValueError("季不存在 (Season not found)")
        season.title = (title or "").strip()[:200]
        season.updated_at = _now()
        db.commit()
        db.refresh(season)
        self._save(db, project)
        return season

    def delete_season(self, db, project: Project, season_id: str) -> Project:
        """删除一季：连同其章节（清理媒体）与季角色参考图；剩余季重排季号 + 章节扁平重编号。"""
        season = self._get_season(db, project, season_id)
        if season is None:
            raise ValueError("季不存在 (Season not found)")
        for ch in self._season_chapters(db, project, season):
            self._rm_media(ch.media_path)
            db.delete(ch)
        for c in chars_from_raw(season.characters):
            self._rm_media(c.get("image"))
        db.delete(season)
        db.commit()
        # 剩余季重排季号（保持相对顺序连续 1..S）
        for i, s in enumerate(self._load_seasons(db, project), start=1):
            s.number = i
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return project

    def _reindex_flat(self, db, project: Project) -> None:
        """按 (季号, 季内顺序) 重排全部章节的扁平序号 0..N-1。

        季内顺序 = 当前扁平顺序按季分组（保持相对次序）。"""
        flat = self._load_chapters(db, project)
        season_no = {s.id: s.number for s in self._load_seasons(db, project)}
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

    def _season_base_index(self, db, project: Project, season: Season) -> int:
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

    # ---------------- 企划（每步独立生成：故事大纲 / 角色设定）+ 章节规划 ----------------
    async def step_arc(self, db, project: Project, lang: str = "zh",
                       res_width: int = 0, res_height: int = 0, extra_prompt: str = "") -> Project:
        """「生成大纲」：单独写整体故事大纲（基于一句话创意 + 风格/主题/基调），并设置默认分辨率。
        不触碰风格 / 角色 / 章节。status: → arced。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        style = (scope.get("style") or "").strip()
        theme = (scope.get("theme") or "").strip()
        tone = (scope.get("tone") or "").strip()
        system = ("你是资深漫画/短剧策划兼编剧。根据一句话创意与风格设定，写整体故事大纲：\n"
                  "分 开端、发展、高潮、结局 四段，每段 1-2 句讲清发生什么、如何承接到下一段，"
                  "末尾附 1-3 条贯穿全篇的主线设定。")
        agent = make_agent(build_model(llm_cfg), system, output_type=ArcOut)
        user = project.origin
        if style:
            user += f"\n风格：{style}"
        if theme:
            user += f"\n主题：{theme}"
        if tone:
            user += f"\n基调：{tone}"
        if (extra_prompt or "").strip():
            user += f"\n额外要求：{(extra_prompt or '').strip()}"
        async with agent:
            data = (await agent.run(user)).output
        arc = (data.arc or "").strip()
        if not arc:
            raise RuntimeError(L(lang, "模型未返回大纲内容，请重试",
                                 "The model returned no outline content — please retry"))
        project.arc = arc
        project.res_width = int(res_width or 0)
        project.res_height = int(res_height or 0)
        project.status = "arced"
        self._save(db, project)
        return project

    async def step_season_arc(self, db, project: Project, season: Season, lang: str = "zh",
                              extra_prompt: str = "") -> Project:
        """「生成本季大纲」：基于整体故事大纲（全篇主线）+ 本季季号/季名，为当前季单独写故事大纲。
        不触碰整体大纲 / 风格 / 角色 / 章节。status: → arced。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        style = (scope.get("style") or "").strip()
        theme = (scope.get("theme") or "").strip()
        overall_arc = (project.arc or "").strip()
        season_no = season.number
        season_title = (season.title or "").strip()
        system = ("你是资深漫画/短剧策划兼编剧。根据整体故事大纲（全篇主线）与本季季号/季名，"
                  "写本季的剧情大纲：说明本季承接主线的哪一段、本季的开端、发展、高潮、结局，"
                  "以及与前后季的衔接。字数与整体大纲相当，分 开端、发展、高潮、结局 四段，每段 1-2 句。"
                  "若本季尚无合适名字，请在末尾附一个简洁的季名（篇章名）。")
        agent = make_agent(build_model(llm_cfg), system, output_type=SeasonArcOut)
        user = f"季号：第 {season_no} 季"
        if season_title:
            user += f"\n季名：{season_title}"
        if overall_arc:
            user += f"\n整体故事大纲（全篇主线）：\n{overall_arc}"
        if style:
            user += f"\n风格：{style}"
        if theme:
            user += f"\n主题：{theme}"
        if (extra_prompt or "").strip():
            user += f"\n额外要求：{(extra_prompt or '').strip()}"
        async with agent:
            data = (await agent.run(user)).output
        arc = (data.arc or "").strip()
        if not arc:
            raise RuntimeError(L(lang, "模型未返回本季大纲内容，请重试",
                                 "The model returned no season outline content — please retry"))
        season.arc = arc
        # 若本季尚无名字且模型给出，则采用模型建议的季名
        if not season_title and (data.title or "").strip():
            season.title = (data.title or "").strip()
        project.status = "arced"
        self._save(db, project)
        return project

    async def step_season_chars(self, db, project: Project, season: Season, lang: str = "zh",
                                extra_prompt: str = "") -> Project:
        """「生成季角色」：基于本季大纲（为空则整体大纲）+ 风格 + 已有核心角色，生成本季新增角色。
        不触碰整体大纲 / 风格 / 核心角色 / 章节。本季无新角色时可为空。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        style = (scope.get("style") or "").strip()
        season_arc = self._season_arc(project, season)
        if not season_arc:
            raise RuntimeError(L(lang, "请先生成或填写本季大纲（或整体大纲）再生成季角色",
                                 "Please create the season (or overall) outline before generating season characters"))
        overall_chars = chars_from_raw(project.characters)
        overall_names = "、".join((c.get("name") or "").strip() for c in overall_chars
                                 if (c.get("name") or "").strip())
        system = ("你是资深漫画/短剧策划。根据本季剧情大纲、风格与已有核心角色，列出本篇章新增的角色"
                  "（即不在核心角色之列、本篇章才会出现的角色）：每个角色给出 名字 + 形象/性格（每人 2-4 句），"
                  "供本篇章各章保持角色一致。不要重复已有核心角色；若本篇章没有新角色，返回空列表即可。")
        agent = make_agent(build_model(llm_cfg), system, output_type=CharsOut)
        user = f"本季大纲：\n{season_arc}"
        if style:
            user += f"\n风格：{style}"
        if overall_names:
            user += f"\n已有核心角色（不要重复）：{overall_names}"
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
        # 重新生成整体替换季角色：清理旧季角色的参考图文件（若有）
        for old in chars_from_raw(season.characters):
            self._rm_media(old.get("image"))
        season.characters = chars_to_raw(chars)
        self._save(db, project)
        return project

    async def step_chars(self, db, project: Project, lang: str = "zh",
                         extra_prompt: str = "") -> Project:
        """「生成角色」：单独列出主要角色设定（基于一句话创意 + 风格 + 故事大纲），
        输出为多个角色（名字 + 形象/性格）。不触碰风格 / 大纲 / 章节。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        style = (scope.get("style") or "").strip()
        arc = (project.arc or "").strip()
        system = ("你是资深漫画/短剧策划。根据一句话创意、风格与整体故事大纲，列出主要角色设定："
                  "每个角色给出 名字 + 形象/性格/核心动机（每人 2-4 句），供后续各章保持角色一致。")
        agent = make_agent(build_model(llm_cfg), system, output_type=CharsOut)
        user = project.origin
        if style:
            user += f"\n风格：{style}"
        if arc:
            user += f"\n整体故事大纲：{arc}"
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
            raise RuntimeError(L(lang, "模型未返回角色设定，请重试",
                                 "The model returned no character settings — please retry"))
        # 重新生成整体替换角色：清理旧角色的参考图文件
        for old in chars_from_raw(project.characters):
            self._rm_media(old.get("image"))
        project.characters = chars_to_raw(chars)
        self._save(db, project)
        return project

    async def gen_char_description(self, db, project: Project, char_id: str, lang: str = "zh") -> str:
        """AI 生成单个角色的形象/性格描述（2-4 句）。
        该角色有参考图且 LLM 支持视觉时，以图片为形象基准（文设贴合图）；
        并结合作品一句话创意 / 风格 / 故事大纲保持一致。返回写回后的描述。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        chars = chars_from_raw(project.characters)
        char = next((c for c in chars if c["id"] == char_id), None)
        if char is None:
            raise ValueError("角色不存在 (Character not found)")
        scope = project.scope or {}
        style = (scope.get("style") or "").strip()
        arc = (project.arc or "").strip()
        name = (char.get("name") or "").strip()
        system = ("你是资深漫画/短剧策划。为一个角色写 形象/性格/核心动机（2-4 句），供后续各章保持角色一致。"
                  "若附带了角色参考图，请以图片为依据描述外形（服饰 / 相貌 / 气质等），让文字设定与图片一致。")
        agent = make_agent(build_model(llm_cfg), system, output_type=CharDescOut)
        user_text = f"一句话创意：{project.origin}"
        if style:
            user_text += f"\n风格：{style}"
        if name:
            user_text += f"\n角色名字：{name}"
        if arc:
            user_text += f"\n整体故事大纲：{arc}"
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
        project.characters = chars_to_raw(chars)
        self._save(db, project)
        return desc

    def _rebuild_chapters(self, db, project: Project, season: Season,
                          plan: list[tuple[str, str]]) -> None:
        """按 [(标题, 主题摘要)] 重建**该季**章节：清空旧章节（清理其媒体）后按序建新的。"""
        for ch in self._season_chapters(db, project, season):
            self._rm_media(ch.media_path)
            db.delete(ch)
        db.commit()
        base = self._season_base_index(db, project, season)
        for i, (title, summary) in enumerate(plan):
            db.add(Chapter(project_id=project.id, season_id=season.id,
                           index=base + i, title=title, summary=summary))
        db.commit()
        self._reindex_flat(db, project)

    def save_outline(self, db, project: Project, *, arc: str | None = None,
                      characters: list[dict] | None = None, style: str | None = None,
                      global_prompt: str | None = None,
                      res_width: int | None = 0, res_height: int | None = 0,
                      count_mode: str | None = None, count_min: int | None = None, count_max: int | None = None,
                      auto_score: int | None = None, score_min: int | None = None,
                      auto_redo: int | None = None) -> Project:
        """保存大纲页手动编辑：大纲 / 角色设定（多个角色：名字+描述，按 id 保留参考图）/ 全局提示词 / 风格 / 默认分辨率 / 章节数量设定。
        仅更新传入（非 None）的字段；不触碰章节（章节按季编辑，见 save_season），
        也不触碰已生成的剧本/媒体（保存不触发生成）。"""
        scope = dict(project.scope or {})
        if style is not None:
            scope["style"] = (style or "").strip()
        project.scope = scope
        if arc is not None:
            project.arc = (arc or "").strip()
        if characters is not None:
            existing = {c["id"]: c for c in chars_from_raw(project.characters)}
            new_chars = []
            for item in characters:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id") or "").strip() or uuid.uuid4().hex[:12]
                old = existing.get(cid) or {}
                img = (old.get("image") or "").strip()
                if img and not Path(img).is_file():
                    img = ""  # 参考图文件已不在：丢弃引用
                new_chars.append({
                    "id": cid,
                    "name": str(item.get("name") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "image": img,
                })
            # 删除的角色：清理其参考图文件
            kept = {c["id"] for c in new_chars}
            for cid, c in existing.items():
                if cid not in kept and c.get("image"):
                    self._rm_media(c["image"])
            project.characters = chars_to_raw(new_chars)
        if global_prompt is not None:
            project.global_prompt = (global_prompt or "").strip()
        if res_width is not None:
            project.res_width = int(res_width or 0)
        if res_height is not None:
            project.res_height = int(res_height or 0)
        if count_mode is not None:
            project.count_mode = "range"          # 仅范围模式（遗留的项目级字段）
        if count_min is not None or count_max is not None:
            project.count_min, project.count_max = count_range(count_min, count_max)
        if auto_score is not None:
            project.auto_score = 1 if auto_score else 0
        if score_min is not None:
            project.score_min = max(0, min(100, int(score_min or 0)))
        if auto_redo is not None:
            project.auto_redo = 1 if auto_redo else 0
        db.commit()
        self._save(db, project)
        return project

    def save_season(self, db, project: Project, season: Season, *,
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
            existing = self._season_chapters(db, project, season)
            for i, item in enumerate(chapters):
                t = (item.get("title") or "").strip()
                s = (item.get("summary") or "").strip()
                if i < len(existing):
                    if t:
                        existing[i].title = t
                    existing[i].summary = s
                else:
                    base = self._season_base_index(db, project, season) + len(existing)
                    db.add(Chapter(project_id=project.id, season_id=season.id,
                                   index=base + (i - len(existing)),
                                   title=t or f"第{i + 1}章", summary=s))
            for ch in existing[len(chapters):]:
                self._rm_media(ch.media_path)
                db.delete(ch)
            self._reindex_flat(db, project)
        season.updated_at = _now()
        db.commit()
        db.refresh(season)
        self._save(db, project)
        return season

    def reset_settings(self, db, project: Project, *, title: str, origin: str, style: str,
                        clear_downstream: bool, lang: str = "zh") -> Project:
        """重新设定：修改标题 / 一句话创意（主题）/ 风格。

        默认不清空下游（保留大纲/章节/已生成媒体），项目停留在当前阶段，
        用户可再按需「重新生成全部画面」或重排章节；
        clear_downstream=True 时清空大纲/章节/媒体并回到 planning（相当于按新设定重开大纲）。"""
        origin = (origin or "").strip()
        if not origin:
            raise ValueError(L(lang, "一句话创意不能为空", "The idea (origin) cannot be empty"))
        if (title or "").strip():
            project.title = title.strip()[:200]
        project.origin = origin
        scope = dict(project.scope or {})
        scope["style"] = (style or "").strip()
        project.scope = scope
        if clear_downstream:
            project.arc = ""
            for c in chars_from_raw(project.characters):
                self._rm_media(c.get("image"))
            project.characters = ""
            project.global_prompt = ""
            project.res_width = 0
            project.res_height = 0
            project.count_mode = "range"
            project.count_min = 0
            project.count_max = 0
            for ch in db.query(Chapter).filter(Chapter.project_id == project.id).all():
                self._rm_media(ch.media_path)
                db.delete(ch)
            project.status = "planning"
        self._save(db, project)
        return project

    # ---------------- 整部作品完结（finished）/ 解锁 ----------------
    def is_finished(self, project: Project) -> bool:
        """作品是否已完结（锁定）：status = done。"""
        return (project.status or "") == "done"

    def season_progress(self, db, project: Project) -> list[dict]:
        """各季完成进度：[{id, number, title, total, done, ok}]（ok = 本季有章节且全部已生成）。"""
        out = []
        for s in self._load_seasons(db, project):
            chs = self._season_chapters(db, project, s)
            done = sum(1 for c in chs if c.status == "done")
            out.append({"id": s.id, "number": s.number, "title": s.title or "",
                        "total": len(chs), "done": done,
                        "ok": len(chs) > 0 and done == len(chs)})
        return out

    def check_finished(self, db, project: Project, lang: str = "zh") -> list[str]:
        """完结条件检查：返回未满足项（本地化文案列表）；空列表 = 满足。
        条件：每季至少 1 章，且全部季的全部章节均已生成（done）。"""
        issues: list[str] = []
        for row in self.season_progress(db, project):
            label = L(lang, f"第{row['number']}季", f"Season {row['number']}")
            if row["title"]:
                label += f"（{row['title']}）" if lang == "zh" else f" ({row['title']})"
            if row["total"] == 0:
                issues.append(L(lang, f"{label}：无章节", f"{label}: no chapters"))
            elif row["done"] < row["total"]:
                issues.append(L(lang, f"{label}：已完成 {row['done']}/{row['total']} 章",
                                f"{label}: only {row['done']}/{row['total']} chapters done"))
        return issues

    def complete_project(self, db, project: Project, lang: str = "zh") -> Project:
        """标记整部作品完结（锁定）：要求全部季的章节都已完成（done），否则抛 ValueError（含未满足明细）。"""
        if not self.season_progress(db, project):
            raise ValueError(L(lang, "该项目还没有任何季/章节，不能完结",
                               "No seasons/chapters yet — cannot finish"))
        issues = self.check_finished(db, project, lang)
        if issues:
            sep = "；" if lang == "zh" else "; "
            raise ValueError(L(lang, f"尚不能完结：{sep.join(issues)}",
                               f"Cannot finish yet: {sep.join(issues)}"))
        project.status = "done"
        self._save(db, project)
        return project

    def unlock_project(self, db, project: Project, lang: str = "zh") -> Project:
        """解锁：已完结（锁定）→ chaptered，可继续编辑 / 生成。"""
        if not self.is_finished(project):
            raise ValueError(L(lang, "该作品未处于完结（锁定）状态，无需解锁",
                               "This project is not finished/locked — nothing to unlock"))
        project.status = "chaptered"
        self._save(db, project)
        return project

    async def step_chapters(self, db, project: Project, season: Season, lang: str = "zh",
                             count_min: int = 0, count_max: int = 0) -> Project:
        """「章节规划」：依据季大纲/风格/角色 + 章节数量范围（min~max）规划章节（标题 + 主题摘要）。
        会清空该季已有章节/媒体（按大纲重新拆章）。status → chaptered。"""
        season.count_mode = "range"              # 仅范围模式
        season.count_min, season.count_max = count_range(count_min, count_max)
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        n = await self._plan_chapter_count(db, project, season, lang,
                                           season.count_min, season.count_max, model=model)
        prior: list[tuple[str, str]] = []
        plan: list[tuple[str, str]] = []
        for i in range(1, n + 1):
            t, s = await self._plan_one_chapter(db, project, season, lang, i, n, prior, model=model)
            prior.append((t, s))
            plan.append((t, s))
        self._rebuild_chapters(db, project, season, plan)
        project.status = "chaptered"
        self._save(db, project)
        return project

    async def _plan_chapter_count(self, db, project: Project, season: Season, lang: str,
                                   count_min: int, count_max: int, model=None) -> int:
        """先定总章数：在 [count_min, count_max] 范围内选一个合适的数。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        gprompt = (project.global_prompt or "").strip()
        arc = self._season_arc(project, season)
        system = "你是分章策划。依据整体故事大纲判断应拆分为多少章，只给出章数（一个整数）。"
        agent = make_agent(model or build_model(llm_cfg), system, output_type=ChapterCount)
        lo, hi = count_range(count_min, count_max)
        if lo == hi:
            return lo                             # 固定值（min=max）：无需 LLM 挑选
        cnt = f"请从 {lo} 到 {hi} 之间选一个合适的章数"
        user = (f"主题：{scope.get('theme', '')}\n风格：{scope.get('style', '')}\n基调：{scope.get('tone', '')}\n"
                + (f"全局要点（务必涵盖/遵循）：{gprompt}\n" if gprompt else "")
                + f"{cnt}\n\n整体故事大纲：\n{arc}")
        async with agent:
            data = (await agent.run(user)).output
        return max(1, int(data.count or 0))

    async def _plan_one_chapter(self, db, project: Project, season: Season, lang: str, i: int, n: int,
                                 prior: list[tuple[str, str]], model=None) -> tuple[str, str]:
        """规划第 i 章（共 n 章）：参考前面已规划的章节承接剧情，返回 (标题, 一句话主题摘要)。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        chars = chars_to_text(self._combined_chars(project, season))
        gprompt = (project.global_prompt or "").strip()
        arc = self._season_arc(project, season)
        system = ("你是分章策划。为故事规划第 i/n 章，只输出本章：标题（简短）+ 一句话主题摘要"
                  "（讲清本章发生什么、如何承接前面章节并推进整体大纲）。")
        agent = make_agent(model or build_model(llm_cfg), system, output_type=ChapterOut)
        prior_text = "\n".join(f"第{k}章：{t}（{s}）" for k, (t, s) in enumerate(prior, 1)) \
            or "（本章为第一章，尚无前置章节）"
        user = (f"主题：{scope.get('theme', '')}\n风格：{scope.get('style', '')}\n基调：{scope.get('tone', '')}\n"
                + (f"角色设定：{chars}\n" if chars else "")
                + (f"全局要点（务必涵盖/遵循）：{gprompt}\n" if gprompt else "")
                + f"共 {n} 章，现在规划第 {i} 章。\n已规划章节（承接其剧情）：\n{prior_text}\n\n"
                  f"整体故事大纲：\n{arc}")
        async with agent:
            data = (await agent.run(user)).output
        return ((data.title or f"第{i}章").strip(), (data.scene or "").strip())

    async def step_chapters_stream(self, db, project: Project, season: Season, lang: str = "zh",
                                    count_min: int = 0, count_max: int = 0, indices: list[int] | None = None,
                                    mode: str = "replan", progress_cb=None, chapter_done_cb=None) -> Project:
        """「章节规划」逐章版（章节数量：固定值，count_min=count_max=N）：
        - mode='replan'（默认，重做）：indices 省略/空 = 全季重规划（清空该季已有章节/媒体后逐章规划 1..N）；
          indices 非空 = 多选重规划：只重写选中章节的「标题 + 主题摘要」，其余章节与已生成媒体保持不变；
        - mode='append'（新增）：保留已有章节与其媒体，在现有章节末尾之后续规划 N 章（承接前序剧情）。
        status → chaptered。progress_cb(current,total,title) 每章开始前；chapter_done_cb(chapter) 每章规划完成后。"""
        season.count_mode = "range"              # 仅范围模式（固定值即 min=max）
        season.count_min, season.count_max = count_range(count_min, count_max)
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        if mode == "append":
            await self._append_chapters(db, project, season, lang, model, season.count_max,
                                        progress_cb, chapter_done_cb)
            project.status = "chaptered"
            self._save(db, project)
            return project
        targets = sorted({int(i) for i in (indices or [])})
        if targets:
            await self._replan_selected(db, project, season, lang, targets, model, progress_cb, chapter_done_cb)
        else:
            await self._replan_season(db, project, season, lang, model, progress_cb, chapter_done_cb)
        project.status = "chaptered"
        self._save(db, project)
        return project

    async def _replan_season(self, db, project: Project, season: Season, lang: str, model,
                              progress_cb, chapter_done_cb) -> None:
        """全季重规划：清空该季旧章节/媒体后按大纲重新拆章（先定总章数，再逐章规划）。"""
        self._rebuild_chapters(db, project, season, [])  # 清空该季旧章节/媒体，随后逐个补入
        n = await self._plan_chapter_count(db, project, season, lang,
                                           season.count_min, season.count_max, model=model)
        # base = 所有前序季（季号 < 本季）的章节总数：本季新章紧接「前序季末尾」继续编号
        # （旧实现只算紧邻上一季的章数，第 3 季起会与更前面的季撞号）
        base = self._season_base_index(db, project, season)
        prior: list[tuple[str, str]] = []
        for i in range(1, n + 1):
            if progress_cb:
                await progress_cb(i, n, f"第{i}章")
            title, summary = await self._plan_one_chapter(db, project, season, lang, i, n, prior, model=model)
            prior.append((title, summary))
            ch = Chapter(project_id=project.id, season_id=season.id,
                         index=base + i - 1, title=title, summary=summary)
            db.add(ch)
            db.commit()
            db.refresh(ch)
            if chapter_done_cb:
                await chapter_done_cb(ch)
        # 收尾统一重排扁平序号：本方法逐章插入且中途提交，后续季（季号 > 本季）的旧序号
        # 可能已与新章冲突，须像 _rebuild_chapters 一样在末尾重排一次（旧实现漏了这步）。
        self._reindex_flat(db, project)

    async def _replan_selected(self, db, project: Project, season: Season, lang: str, indices: list[int],
                                model, progress_cb, chapter_done_cb) -> None:
        """多选重规划：按季内序号就地重写选中章节的标题 + 主题摘要；其余章节、提示词与已生成媒体都不动。"""
        chapters = self._season_chapters(db, project, season)
        n = len(chapters)
        targets = [i for i in indices if 0 <= i < n]
        for pos, idx in enumerate(targets, start=1):
            if progress_cb:
                await progress_cb(pos, len(targets), f"第{idx + 1}章")
            # 承接上下文：前面章节（含未选中的）按序传入，规划结果与全季剧情保持连贯
            prior = [(c.title, c.summary) for c in chapters[:idx]]
            title, summary = await self._plan_one_chapter(db, project, season, lang, idx + 1, n,
                                                          prior, model=model)
            ch = chapters[idx]
            ch.title, ch.summary = title, summary
            db.commit()
            db.refresh(ch)
            if chapter_done_cb:
                await chapter_done_cb(ch)

    async def _append_chapters(self, db, project: Project, season: Season, lang: str, model,
                                count: int, progress_cb, chapter_done_cb) -> None:
        """新增章节：保留现有章节与其媒体，在现有章节末尾之后续规划 count 章（每章承接前序剧情）。"""
        chapters = self._season_chapters(db, project, season)
        existing = len(chapters)
        base = self._season_base_index(db, project, season)
        prior: list[tuple[str, str]] = [(c.title, c.summary) for c in chapters]
        for k in range(1, count + 1):
            if progress_cb:
                await progress_cb(k, count, f"第{existing + k}章")
            title, summary = await self._plan_one_chapter(db, project, season, lang,
                                                           existing + k, existing + count,
                                                           prior, model=model)
            prior.append((title, summary))
            ch = Chapter(project_id=project.id, season_id=season.id,
                         index=base + existing + k - 1, title=title, summary=summary)
            db.add(ch)
            db.commit()
            db.refresh(ch)
            if chapter_done_cb:
                await chapter_done_cb(ch)
        # 章号可能与后续季旧序号冲突，末尾统一重排扁平序号（同 _replan_season）
        self._reindex_flat(db, project)

    def _load_chapters(self, db, project: Project) -> list[Chapter]:
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())

    # ---------------- 阶段 4：剧本编写（按章 / 批量） ----------------
    def _script_agent_context(self, db, project: Project, lang: str):
        """剧本生成所需的上下文：LLM 配置 / DrawThings 配置（判断是否图生图提示词风格）/ 风格。"""
        llm_cfg, dt_cfg = self._configs(db, project, lang)
        return llm_cfg, dt_cfg, (project.scope or {})

    def _script_system(self, project: Project) -> str:
        """剧本/提示词写作的系统提示词（漫画）。"""
        shot = ("漫画：prompt 描述『一页多格漫画』——一张图内含多个分镜格（竖版漫画页），"
                "并让画面带文字（分镜旁白、对白气泡）；构图优先竖版（3:4 或 2:3）。")
        return ("你是编剧兼分镜提示词作者。根据上一章内容和本章场景，"
                "写本章详细剧本描述（description）和出图/出视频提示词（prompt 用英文，保持风格与上一章连贯）。\n"
                f"{shot}\n"
                "分辨率统一由项目总体设定决定，无需决定 width/height（不要在提示词里写具体分辨率）。\n"
                "只输出一个 JSON 对象，字段固定为 description 与 prompt（都是字符串）："
                "description 用一段文字概括本章（即使包含多个分镜，也合成一段文字，不要拆成数组）；"
                "prompt 为单个英文提示词。不要输出数组、Markdown 代码块或任何额外文字。")

    async def _gen_one_script(self, db, project: Project, season: Season, i: int, ch: Chapter,
                               chapters: list[Chapter], lang: str = "zh", model=None) -> Chapter:
        """为第 i 章（季内序号）单独写剧本/提示词（供「按章生成」与「批量生成」复用）。
        chapters 为该季的章节列表；i 为季内 0 起序号。model 复用调用方构建的模型（避免逐章重建客户端）。"""
        llm_cfg, dt_cfg, scope = self._script_agent_context(db, project, lang)
        style = (scope.get("style") or "").strip()
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
                prev_last = self._prev_season_last_chapter(db, project, season)
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
        chars = chars_to_text(self._combined_chars(project, season))
        gprompt = (project.global_prompt or "").strip()
        base = (ch.summary or ch.description or "").strip() or "（按大纲与上一章自然续写）"
        user = (
            (f"整体风格：{style}\n" if style else "")
            + (f"角色设定（请保持一致）：{chars}\n" if chars else "")
            + (f"全局要点（务必遵循）：{gprompt}\n" if gprompt else "")
            + f"本章主题摘要：{base}\n\n{context}"
        )
        # 图生图模式（勾选「支持参考图片」且本章有参考图）：提示词写成基于参考图的修改指令，
        # 避免从头完整描述导致重绘覆盖参考画面（功能级开关优先，配置兜底）
        # 冗余防错：先把参考图读成 data URI（文件丢失 / 损坏 → 退化为纯文本续写，不中断本章）
        ref_uri = None
        if ref_img:
            # 读图 + base64 放线程池（大图编码耗时可观）
            try:
                ref_uri = await run_sync(image_data_uri, ref_img)
            except Exception as e:
                logger.warning("参考图读取失败，本次不带参考图（%s）：%s", ref_img, e)
        if ref_uri and ref_image_enabled(dt_cfg, project):
            user += ("\n【图生图模式】附图是本次生成的参考图，画面将基于它生成。"
                     "prompt 必须写成针对参考图的修改指令：先用一句话点明需与参考图保持一致的元素"
                     "（角色外形、服装、画风、光照、构图），再具体描述本章的变化（新动作 / 新场景 / 新物件）；"
                     "不要从头重新描述整个画面。")
        prompt_content: str | list = [ImageUrl(url=ref_uri), user] if ref_uri else user
        data = (await agent.run(prompt_content)).output
        ch.description = data.description
        ch.prompt = data.prompt
        return ch

    def _ref_score_line(self, ch: Chapter) -> str:
        """参考章节评分内容：参考章已评分时，把 VLM 分值+评语附进参考上下文，
        让 LLM 知晓参考基准的评分水平（未评分则不附）。"""
        if ch is None or (ch.score or 0) <= 0:
            return ""
        note = (ch.score_note or "").strip()
        return f"（参考评分 {ch.score} 分" + (f"：{note}" if note else "") + "）"

    # ---------------- 阶段 5：逐章生成画面（出图提示词 + 生图，两步连贯） ----------------
    # ---------------- 自动评分 / VLM 评分 ----------------
    def _score_system(self, project: Project) -> str:
        """自动评分系统提示词（漫画）：对单章生成图按百分制打分。"""
        return ("你是漫画制作的美术总监。对当前章节生成的画面按 100 分制评估，"
                "只输出一个 JSON 对象，字段：score（0-100 整数）与 note（一句话中文评语，不超过 30 字）。\n"
                "评分标准（按权重）：1) 与本章场景描述 / 出图提示词的内容是否相符；"
                "2) 角色一致性（角色外形与既有设定一致）；3) 风格一致性（与整体风格 / 全局提示词一致）；"
                "4) 画面质量（清晰度、构图、无明显畸变 / 伪影）；5) 漫画分镜结构与画面内文字是否清晰。"
                "除 JSON 外不要输出任何文字。")

    async def _score_chapter(self, db, project: Project, season: Season, ch: Chapter,
                             model) -> tuple[int, str]:
        """自动评分：对本章已生成的媒体打 0-100 分，返回 (分数, 评语)。"""
        style = ((project.scope or {}).get("style") or "").strip()
        gprompt = (project.global_prompt or "").strip()
        user = (f"章节标题：{ch.title}\n"
                f"场景描述：{ch.description or ch.summary or ''}\n"
                f"出图提示词：{ch.prompt}\n"
                f"整体风格：{style}\n"
                f"全局提示词：{gprompt}")
        media = (ch.media_path or "").strip()
        prompt: str | list = user + "\n生成图缺失，请打 0 分并在 note 说明原因。"
        if media and Path(media).is_file():
            prompt = [ImageUrl(url=image_data_uri(media)),
                      user + "\n请对附带的生成图评分（标准见系统提示词）。输出 JSON：score（0-100 整数）与 note（一句话评语）。"]
        agent = make_agent(model, self._score_system(project), output_type=ScoreOut)
        async with agent:
            data = (await agent.run(prompt)).output
        score = max(0, min(100, int(data.score or 0)))
        return score, (data.note or "").strip()[:300]

    async def vlm_score_chapter(self, db, project: Project, season: Season, index: int,
                                lang: str = "zh") -> tuple[int, str]:
        """VLM 自动评分：对季内第 index 章调用 VLM 重新评分（与流水线自动评分同款），
        落库分值 + 评语。无生成图 / VLM 评分失败 → ValueError（前端提示）。"""
        ch = self._season_chapters(db, project, season)[index]
        media = (ch.media_path or "").strip()
        if not (media and Path(media).is_file()):
            raise ValueError("本章还没有生成图，无法 VLM 评分")
        try:
            model = build_model(self._llm_cfg(db, project, lang))
            score, note = await self._score_chapter(db, project, season, ch, model)
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
                             chapter_done_cb=None, score_cb=None) -> Project:
        """逐章生成画面（季内）：每章跑完整 2 步——① (重新)生成出图提示词/描述/分辨率 ② 生图/生视频。
        开启「自动评分」时追加第 3 步：0-100 评分；低于阈值且开启「低分自动重做」→ 重新生成（最多 MAX_SCORE_REDO 次）。
        indices: 季内章节序号列表（0 起）；None=该季全部章节。
        季内第 1 章参考：开启「本季封面作为第 1 章参考」→ 本季封面；否则非第一季→上一季末章，第一季→无参考（文生图）。
        其余章沿用上一章媒体。"""
        dt = self._clients(db, project, lang)
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        chapters = self._season_chapters(db, project, season)
        if indices is None:
            targets = list(enumerate(chapters))
        else:
            targets = [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
        try:
            for pos, (i, ch) in enumerate(targets, start=1):
                if progress_cb:
                    await progress_cb(pos, len(targets), ch.title)
                # 自动评分：生成画面后按 0-100 评分；低于阈值且开启「低分自动重做」→ 重新生成（最多 MAX_SCORE_REDO 次）
                auto_score = bool(project.auto_score)
                auto_redo = bool(project.auto_redo)
                rounds = 1 + MAX_SCORE_REDO if (auto_score and auto_redo) else 1
                score_min = int(project.score_min or 60)
                for rd in range(rounds):
                    if rd:
                        # 上一轮评分低于阈值 → 重做（重新生成提示词 + 画面）
                        if score_cb:
                            await score_cb(ch, None, "redo", rd)
                    await self._gen_one_script(db, project, season, i, ch, chapters, lang, model=model)
                    # 第 2 步：生图 / 生视频（参考上一章图；季内第 1 章参考上一季末章）
                    if i == 0:
                        season_cover = (season.first_image or "").strip()
                        if season.cover_as_first_ref and season_cover and Path(season_cover).is_file():
                            ref = season_cover
                        elif season.number > 1:
                            prev_last = self._prev_season_last_chapter(db, project, season)
                            ref = prev_last.media_path if (prev_last and prev_last.media_path) else ""
                        else:
                            ref = ""
                    else:
                        prev = chapters[i - 1]
                        ref = prev.media_path if (prev and prev.media_path) else ""
                    try:
                        # 分辨率统一跟随总体设定（不再按章覆盖）
                        w = int(project.res_width or 0)
                        h = int(project.res_height or 0)
                        params = {}
                        if w and h:
                            params = {"width": w, "height": h}
                        ch.media_path = await run_sync(
                            partial(dt.generate_image, ch.prompt, ref_path=ref, params=params))
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
                        score, note = await self._score_chapter(db, project, season, ch, model)
                        ch.score, ch.score_note = score, note
                    except Exception:
                        ch.score, ch.score_note = 0, ""  # 评分失败不阻塞流程
                        db.commit()
                        break  # 评分失败：不重做（避免拿不到分时反复重生成）
                    db.commit()
                    if score_cb:
                        await score_cb(ch, (ch.score or 0), (ch.score_note or ""), rd)
                    if (ch.score or 0) >= score_min or rd == rounds - 1:
                        break  # 达到阈值，或重做次数用尽：本章完成
                if chapter_done_cb:
                    await chapter_done_cb(ch)
                db.commit()  # 逐章提交：停止/中断时已完成章节不丢失
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
    def save_chapter_fields(self, db, project: Project, season: Season, index: int, prompt: str) -> Project:
        """手动编辑季内第 index 章的出图提示词（分辨率统一按总体设定，不再按章覆盖）。"""
        ch = self._season_chapters(db, project, season)[index]
        ch.prompt = (prompt or "").strip()
        db.commit()
        self._save(db, project)
        return project

    # ---------------- 章节增 / 删 / 排序（季内） ----------------
    def add_chapter(self, db, project: Project, season: Season) -> Chapter:
        """在该季末尾新增一章（标题/主题摘要留空）。"""
        chapters = self._season_chapters(db, project, season)
        base = self._season_base_index(db, project, season) + len(chapters)
        ch = Chapter(project_id=project.id, season_id=season.id,
                     index=base, title="新章节", summary="")
        db.add(ch)
        db.commit()
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return ch

    def delete_chapter(self, db, project: Project, season: Season, index: int) -> Project:
        """删除季内第 index 章（连同清理其媒体文件），其余章节重新编号。"""
        chapters = self._season_chapters(db, project, season)
        ch = chapters[index]
        self._rm_media(ch.media_path)
        db.delete(ch)
        db.commit()
        self._reindex_flat(db, project)
        db.commit()
        self._save(db, project)
        return project

    def move_chapter(self, db, project: Project, season: Season, index: int, direction: str) -> Project:
        """上移 / 下移季内第 index 章（direction: up/down），交换后重新编号。"""
        chapters = self._season_chapters(db, project, season)
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

    def set_season_cover_ref(self, db, project: Project, season: Season, enabled: bool) -> Season:
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

    def overlay_season_first_image_title(self, db, project: Project, season: Season,
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
        self._overlay_title(Path(path), self._season_label(season, lang), **(opts or {}))
        season.updated_at = _now()
        self._save(db, project)
        return season

    def set_char_image(self, db, project: Project, char_id: str, path: str) -> Project:
        """设置/清除某角色的参考图（文件已由调用方落盘到 data/media；path 为空 = 清除并删除旧图）。"""
        chars = chars_from_raw(project.characters)
        for c in chars:
            if c["id"] == char_id:
                old = c.get("image") or ""
                c["image"] = (path or "").strip()
                if old and old != c["image"]:
                    self._rm_media(old)
                project.characters = chars_to_raw(chars)
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

        prompt 为「额外提示词」：基础提示词始终由 LLM 结合一句话创意 + 风格 + 故事大纲 +
        角色设定自动撰写，额外提示词原样追加在末尾作为补充（留空 = 只用基础提示词）。
        分辨率统一跟随总体设定（project.res_width × res_height，与章节一致）。
        include_title 为真时在成品图上用 PIL 叠加作品名称（标题保持原文）。
        产物存为 data/media/first_<项目id>.<ext>（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        dt = self._clients(db, project, lang)
        extra = (prompt or "").strip()
        scope = project.scope or {}
        system = ("你是封面美术提示词作者。请结合一句话创意、风格、故事大纲与角色设定，"
                  "写一段详细的封面英文提示词（主体角色、场景、构图、光线、氛围、风格关键词）。"
                  "只输出提示词文本。不要包含任何文字/标题/字母渲染要求（作品名由程序叠加）。")
        agent = make_agent(build_model(llm_cfg), system)
        user = f"一句话创意：{project.origin}\n风格：{scope.get('style', '')}"
        if (project.arc or "").strip():
            user += f"\n故事大纲：{project.arc.strip()}"
        chars_text = chars_to_text(chars_from_raw(project.characters))
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
        path = await run_sync(partial(dt.generate_image, prompt, params=params))
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
    def set_season_first_image_path(self, db, project: Project, season: Season, path: str) -> Season:
        """记录季封面路径（文件已由调用方落盘到 data/media），并把该图存为原图（叠字每次从原图重绘）。"""
        season.first_image = (path or "").strip()
        season.first_image_base = self._snapshot_base(season.first_image)
        season.updated_at = _now()
        self._save(db, project)
        return season

    @staticmethod
    def _season_label(season: Season, lang: str = "zh") -> str:
        """季名展示：有季名用季名，否则回退「第N季 / Season N」."""
        return (season.title or "").strip() or L(lang, f"第{season.number}季",
                                                  f"Season {season.number}")

    async def generate_season_first_image(self, db, project: Project, season: Season,
                                          prompt: str = "", lang: str = "zh",
                                          include_title: bool = True) -> Season:
        """用 DrawThings 生成季封面（文生图）。

        prompt 为「额外提示词」：基础提示词始终由 LLM 结合全局一句话创意 + 风格 + 核心角色与
        季标题/季大纲/季新增角色自动撰写，额外提示词原样追加在末尾作为补充（留空 = 只用基础提示词）。
        分辨率统一跟随总体设定（project.res_width × res_height，与章节一致）。
        include_title 为真时在成品图上用 PIL 叠加季名（季名为空回退「第N季」，标题保持原文）。
        产物存为 data/media/seasonfirst_<季id>.<ext>（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        dt = self._clients(db, project, lang)
        extra = (prompt or "").strip()
        scope = project.scope or {}
        system = ("你是封面美术提示词作者。请结合一句话创意、风格、角色设定与本季标题/大纲/新增角色，"
                  "写一段详细的季封面英文提示词（主体角色、场景、构图、光线、氛围、风格关键词）。"
                  "只输出提示词文本。不要包含任何文字/标题/字母渲染要求（季名由程序叠加）。")
        agent = make_agent(build_model(llm_cfg), system)
        user = f"一句话创意：{project.origin}\n风格：{scope.get('style', '')}"
        chars_text = chars_to_text(chars_from_raw(project.characters))
        if chars_text:
            user += f"\n核心角色：{chars_text}"
        user += f"\n本季：{self._season_label(season, lang)}"
        if (season.arc or "").strip():
            user += f"\n季大纲：{season.arc.strip()}"
        s_chars_text = chars_to_text(chars_from_raw(season.characters))
        if s_chars_text:
            user += f"\n本季新增角色：{s_chars_text}"
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
        path = await run_sync(partial(dt.generate_image, prompt, params=params))
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
            self._overlay_title(dest, self._season_label(season, lang))
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
        chapters = self._season_chapters(db, project, season) if season is not None else self._load_chapters(db, project)
        scope = project.scope or {}
        export_dir = Path(self.data_dir) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        base = f"{self._safe_name(project)}_{project.id}"
        if season is not None:
            base = f"{base}_S{season.number}"
        fname = f"{base}.zip"
        zpath = export_dir / fname
        chars = chars_from_raw(project.characters)
        # 季封面（总体导出 = 全部季；单季导出 = 仅该季）
        seasons_all = ([season] if season is not None
                        else db.query(Season).filter(Season.project_id == project.id)
                        .order_by(Season.number).all())
        cover_lines = []
        if project.first_image and Path(project.first_image).is_file():
            cover_lines.append(f"项目封面：media/00_first_{Path(project.first_image).name}")
        for s in seasons_all:
            if s.first_image and Path(s.first_image).is_file():
                cover_lines.append(f"第{s.number}季封面：media/00_first_S{s.number}_{Path(s.first_image).name}")
        scope_line = (f"导出范围：第{season.number}季" + (f"（{season.title}）" if season.title else "") + "\n") if season is not None else ""
        readme = (
            f"标题：{project.title}\n类型：{project.kind}\n一句话创意：{project.origin}\n"
            + scope_line +
            f"风格：{scope.get('style', '')}\n主题：{scope.get('theme', '')}\n基调：{scope.get('tone', '')}\n"
            f"默认分辨率：{project.res_width}×{project.res_height}\n"
            + (("\n".join(cover_lines) + "\n") if cover_lines else "") +
            f"\n角色设定：\n{chars_to_text(chars)}\n\n整体故事大纲：\n{project.arc}\n"
        )
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
                    f"提示词：{ch.prompt}\n分辨率：{ch.width}×{ch.height}\n媒体：{ch.media_path}\n"
                )
                z.writestr(f"chapters/{i:02d}.txt", chap_txt.encode("utf-8"))
                if ch.media_path and Path(ch.media_path).is_file():
                    z.write(ch.media_path, f"media/{i:02d}_{Path(ch.media_path).name}")
        return str(zpath), fname

    def export_pdf(self, db, project: Project, season: Season | None = None) -> tuple[str, str]:
        """导出 PDF（漫画）：把各章图片按顺序拼成多页 PDF。
        season 非空时仅导出该季章节（文件名带 S<季号> 后缀）。返回 (pdf 路径, 文件名)。"""
        chapters = self._season_chapters(db, project, season) if season is not None else self._load_chapters(db, project)

        def _load_img(p: str) -> Image.Image | None:
            p = (p or "").strip()
            if p and Path(p).is_file() and Path(p).suffix.lower() in (".png", ".jpg", ".jpeg"):
                with Image.open(p) as im:      # 及时关闭文件句柄；convert 产生独立图像
                    return im.convert("RGB")
            return None

        # 封面页排最前：项目封面 + 各季封面（总体导出 = 全部季；单季导出 = 仅该季）
        seasons_all = ([season] if season is not None
                        else db.query(Season).filter(Season.project_id == project.id)
                        .order_by(Season.number).all())
        imgs = []
        if (cover := _load_img(project.first_image)) is not None:
            imgs.append(cover)
        for s in seasons_all:
            if (simg := _load_img(s.first_image)) is not None:
                imgs.append(simg)
        for ch in chapters:
            if (im := _load_img(ch.media_path)) is not None:
                imgs.append(im)
        if not imgs:
            raise ValueError("没有可导出的图片")
        export_dir = Path(self.data_dir) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        base = f"{self._safe_name(project)}_{project.id}"
        if season is not None:
            base = f"{base}_S{season.number}"
        fname = f"{base}.pdf"
        ppath = export_dir / fname
        try:
            imgs[0].save(ppath, save_all=True, append_images=imgs[1:], resolution=96.0)
        finally:
            for im in imgs:
                im.close()
        return str(ppath), fname


