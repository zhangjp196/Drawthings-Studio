"""流水线编排：一句话 -> 全局(风格/总纲/核心角色/封面) -> 各季(季大纲/季角色/章节规划) -> 剧本编写 -> 逐章生成。

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
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import anyio
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
    ChaptersOut,
    SeasonArcOut,
    ScriptOut,
    build_model,
    image_data_uri,
    make_agent,
)
from .drawthings import DrawThingsClient, extract_last_frame


# 封面自动模式叠加作品名称用的中文字体：按系统取第一个存在的（结果缓存，避免反复探盘）
_CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)
_cjk_font: str | None = None


def _cjk_font_path() -> str:
    """找一个可渲染中文/日文的字体文件；没有返回 ''（调用方跳过叠加）。"""
    global _cjk_font
    if _cjk_font is None:
        _cjk_font = next((p for p in _CJK_FONT_CANDIDATES if Path(p).is_file()), "")
    return _cjk_font


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_sync(func, *args):
    """把同步阻塞调用放到线程池执行（生图/生视频、ffmpeg、读图编码、HTTP 探测）。

    pipeline 与 main 中所有阻塞型调用统一走这里，避免占用事件循环导致并发请求卡顿。"""
    return await anyio.to_thread.run_sync(func, *args)


# ---------------- 角色设定（多个角色：id / 名字 / 形象性格 / 参考图） ----------------
def chars_from_raw(raw: str) -> list[dict]:
    """解析 project.characters（JSON 列表）为 [{id, name, description, image}]。

    兼容旧版纯文本角色设定：整体视为一个未命名角色的描述。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, list):
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append({
                "id": str(item.get("id") or "") or uuid.uuid4().hex[:12],
                "name": str(item.get("name") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "image": str(item.get("image") or "").strip(),
            })
        return out
    return [{"id": uuid.uuid4().hex[:12], "name": "", "description": raw, "image": ""}]


def chars_to_raw(chars: list[dict]) -> str:
    """角色列表 -> JSON 字符串（存 project.characters）。"""
    return json.dumps(chars or [], ensure_ascii=False)


def chars_to_text(chars: list[dict]) -> str:
    """角色列表 -> 文本（注入 LLM 提示词用）：每个角色一行「名字：设定」。"""
    lines = []
    for c in chars or []:
        name = (c.get("name") or "").strip()
        desc = (c.get("description") or "").strip()
        if name and desc:
            lines.append(f"{name}：{desc}")
        elif name or desc:
            lines.append(name or desc)
    return "\n".join(lines)


class Pipeline:
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
        """DrawThings 客户端（出图/出视频用）。"""
        _, dt_cfg = self._configs(db, project, lang)
        return DrawThingsClient(dt_cfg, data_dir=self.data_dir)

    def _llm_cfg(self, db, project, lang: str = "zh") -> LLMConfig:
        llm_cfg, _ = self._configs(db, project, lang)
        return llm_cfg

    # ---------------- 项目生命周期 ----------------
    def create(self, db, kind: str, origin: str,
               llm_config_id: str, drawthings_config_id: str,
               style: str = "", title: str = "") -> Project:
        style = (style or "").strip()
        project = Project(
            id=uuid.uuid4().hex[:12],
            kind=kind,
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
        # 参考图：角色有图且 LLM 支持视觉才附带（多模态入图）
        img = (char.get("image") or "").strip()
        supports_vision = (getattr(llm_cfg, "supports_vision", None) or "yes").lower() == "yes"
        prompt: str | list = user_text
        if img and Path(img).is_file() and supports_vision:
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

    async def _plan_chapters(self, db, project: Project, lang: str,
                             count_mode: str = "auto", count_min: int = 0, count_max: int = 0) -> list[tuple[str, str]]:
        """依据当前大纲/风格/角色，让模型规划章节，返回 [(标题, 一句话主题摘要), ...]。
        章节数量：auto = 模型自行决定；range = 在 [count_min, count_max] 内选择合适数量。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        chars = chars_to_text(chars_from_raw(project.characters))
        gprompt = (project.global_prompt or "").strip()
        system = ("你是分章策划。依据整体故事大纲、风格、角色设定，把故事拆成章节，"
                  "每章给出：标题（简短）+ 一句话主题摘要（讲清本章发生什么、如何承接前后）。")
        agent = make_agent(build_model(llm_cfg), system, output_type=ChaptersOut)
        if (count_mode or "auto") == "range" and int(count_min or 0) > 0:
            hi = int(count_max) if int(count_max or 0) >= int(count_min) else int(count_min)
            cnt = f"请拆分为 {int(count_min)}~{hi} 章（在该范围内选择一个合适的数量）"
        else:
            cnt = "请拆分为合适的章节数（一般 3-12 章）"
        user = (f"主题：{scope.get('theme', '')}\n风格：{scope.get('style', '')}\n基调：{scope.get('tone', '')}\n"
                + (f"角色设定：{chars}\n" if chars else "")
                + (f"全局要点（务必涵盖/遵循）：{gprompt}\n" if gprompt else "")
                + f"{cnt}\n\n整体故事大纲（按此大纲拆章）：\n{(project.arc or '').strip()}")
        async with agent:
            data = (await agent.run(user)).output
        return [(c.title or f"第{i + 1}章", (c.scene or "").strip()) for i, c in enumerate(data.chapters)]

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
                      count_mode: str | None = None, count_min: int | None = 0, count_max: int | None = 0) -> Project:
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
            project.count_mode = "range" if count_mode == "range" else "auto"
        if count_min is not None:
            project.count_min = int(count_min or 0)
        if count_max is not None:
            project.count_max = int(count_max or 0)
        db.commit()
        self._save(db, project)
        return project

    def save_season(self, db, project: Project, season: Season, *,
                    title: str | None = None, arc: str | None = None,
                    characters: list[dict] | None = None,
                    count_mode: str | None = None, count_min: int | None = 0, count_max: int | None = 0,
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
            season.count_mode = "range" if count_mode == "range" else "auto"
        if count_min is not None:
            season.count_min = int(count_min or 0)
        if count_max is not None:
            season.count_max = int(count_max or 0)
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
            project.cover_as_first_ref = False
            project.res_width = 0
            project.res_height = 0
            project.count_mode = "auto"
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
                             count_mode: str = "auto", count_min: int = 0, count_max: int = 0) -> Project:
        """「章节规划」：依据季大纲/风格/角色 + 章节数量设定（auto / range）规划章节（标题 + 主题摘要）。
        会清空该季已有章节/媒体（按大纲重新拆章）。status → chaptered。"""
        season.count_mode = "range" if (count_mode or "auto") == "range" else "auto"
        season.count_min = int(count_min or 0)
        season.count_max = int(count_max or 0)
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        n = await self._plan_chapter_count(db, project, season, lang,
                                           season.count_mode, season.count_min, season.count_max, model=model)
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
                                   count_mode: str, count_min: int, count_max: int, model=None) -> int:
        """先定总章数：auto→模型给一个合适数(3-12)；range→在 [count_min, count_max] 内选一个数。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        gprompt = (project.global_prompt or "").strip()
        arc = self._season_arc(project, season)
        system = "你是分章策划。依据整体故事大纲判断应拆分为多少章，只给出章数（一个整数）。"
        agent = make_agent(model or build_model(llm_cfg), system, output_type=ChapterCount)
        if (count_mode or "auto") == "range" and int(count_min or 0) > 0:
            hi = int(count_max) if int(count_max or 0) >= int(count_min) else int(count_min)
            cnt = f"请从 {int(count_min)} 到 {hi} 之间选一个合适的章数"
        else:
            cnt = "请选择合适的章数（一般 3-12）"
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
                                    count_mode: str = "auto", count_min: int = 0, count_max: int = 0,
                                    progress_cb=None, chapter_done_cb=None) -> Project:
        """「章节规划」逐章版：先定总章数 N，再逐章规划第 1..N 章（每章一次调用、参考前面章节承接剧情）。
        会清空该季已有章节/媒体（按大纲重新拆章）后逐个补入；status → chaptered。
        progress_cb(current,total,title) 每章开始前；chapter_done_cb(chapter) 每章规划完成后（SSE 实时回传标题/摘要，页面逐个刷新）。"""
        season.count_mode = "range" if (count_mode or "auto") == "range" else "auto"
        season.count_min = int(count_min or 0)
        season.count_max = int(count_max or 0)
        self._rebuild_chapters(db, project, season, [])  # 清空该季旧章节/媒体，随后逐个补入
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        n = await self._plan_chapter_count(db, project, season, lang,
                                           season.count_mode, season.count_min, season.count_max, model=model)
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
        project.status = "chaptered"
        self._save(db, project)
        return project

    def _load_chapters(self, db, project: Project) -> list[Chapter]:
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())

    # ---------------- 阶段 4：剧本编写（按章 / 批量） ----------------
    def _script_agent_context(self, db, project: Project, lang: str):
        """剧本生成所需的上下文：LLM 配置 / 是否支持视觉 / 风格。"""
        llm_cfg, _ = self._configs(db, project, lang)
        supports_vision = (getattr(llm_cfg, "supports_vision", None) or "yes").lower() == "yes"
        return llm_cfg, supports_vision, (project.scope or {})

    def _script_system(self, project: Project) -> str:
        """按作品类型给出剧本/提示词写作的系统提示词。"""
        if project.kind == "comic":
            shot = ("漫画：prompt 描述『一页多格漫画』——一张图内含多个分镜格（竖版漫画页），"
                    "并让画面带文字（分镜旁白、对白气泡）；构图优先竖版（3:4 或 2:3）。")
        else:
            shot = "短剧：prompt 描述一段连贯的视频画面；横/竖构图可按场景自选。"
        return ("你是编剧兼分镜提示词作者。根据上一章内容和本章场景，"
                "写本章详细剧本描述（description）和出图/出视频提示词（prompt 用英文，保持风格与上一章连贯）。\n"
                f"{shot}\n"
                "分辨率统一由项目总体设定决定，无需决定 width/height（不要在提示词里写具体分辨率）。")

    async def _gen_one_script(self, db, project: Project, season: Season, i: int, ch: Chapter,
                               chapters: list[Chapter], lang: str = "zh", model=None) -> Chapter:
        """为第 i 章（季内序号）单独写剧本/提示词（供「按章生成」与「批量生成」复用）。
        chapters 为该季的章节列表；i 为季内 0 起序号。model 复用调用方构建的模型（避免逐章重建客户端）。"""
        llm_cfg, supports_vision, scope = self._script_agent_context(db, project, lang)
        style = (scope.get("style") or "").strip()
        media_dir = Path(self.data_dir) / "media"
        first_img = (project.first_image or "").strip()
        agent = make_agent(model or build_model(llm_cfg), self._script_system(project),
                           output_type=ScriptOut)
        prev = chapters[i - 1] if i > 0 else None
        context = ""
        ref_img = None
        if prev:
            context = f"上一章：{prev.description}"
            prev_media = (prev.media_path or "").strip()
            if prev_media:
                ext = Path(prev_media).suffix.lower()
                if ext in (".mp4", ".mov", ".webm", ".gif"):
                    # 抽末帧走 ffmpeg 子进程：放线程池，避免阻塞事件循环
                    ref_img = await run_sync(extract_last_frame, prev_media, media_dir)
                else:
                    ref_img = prev_media
        else:
            # 季内第 1 章：非第一季则参考上一季末章；第一季则参考封面（若开启）
            if season.number > 1:
                prev_last = self._prev_season_last_chapter(db, project, season)
                if prev_last and prev_last.media_path:
                    context = f"上一季末章：{prev_last.description}"
                    pm = (prev_last.media_path or "").strip()
                    ext = Path(pm).suffix.lower()
                    if ext in (".mp4", ".mov", ".webm", ".gif"):
                        ref_img = await run_sync(extract_last_frame, pm, media_dir)
                    else:
                        ref_img = pm
            elif first_img and Path(first_img).is_file() and project.cover_as_first_ref:
                context += "\n附封面（第 1 章视觉基准）：它是全系列的视觉基准（角色形象/风格），请保持主角与风格与其一致。"
                if supports_vision:
                    ref_img = first_img
        if not supports_vision:
            ref_img = None
        chars = chars_to_text(self._combined_chars(project, season))
        gprompt = (project.global_prompt or "").strip()
        base = (ch.summary or ch.description or "").strip() or "（按大纲与上一章自然续写）"
        user = (
            (f"整体风格：{style}\n" if style else "")
            + (f"角色设定（请保持一致）：{chars}\n" if chars else "")
            + (f"全局要点（务必遵循）：{gprompt}\n" if gprompt else "")
            + f"本章主题摘要：{base}\n\n{context}"
        )
        prompt_content: str | list = user
        if ref_img:
            # 读图 + base64 放线程池（大图编码耗时可观）
            uri = await run_sync(image_data_uri, ref_img)
            prompt_content = [ImageUrl(url=uri), user]
        data = (await agent.run(prompt_content)).output
        ch.description = data.description
        ch.prompt = data.prompt
        return ch

    # ---------------- 阶段 5：逐章生成画面（出图提示词 + 生图，两步连贯） ----------------
    async def step_generate(self, db, project: Project, season: Season,
                             indices: list[int] | None = None,
                             lang: str = "zh", progress_cb=None,
                             chapter_done_cb=None) -> Project:
        """逐章生成画面（季内）：每章跑完整 2 步——① (重新)生成出图提示词/描述/分辨率 ② 生图/生视频。
        indices: 季内章节序号列表（0 起）；None=该季全部章节。
        季内第 1 章参考：非第一季→上一季末章；第一季→封面（若开启）或为空。
        其余章沿用上一章媒体。"""
        dt = self._clients(db, project, lang)
        model = build_model(self._llm_cfg(db, project, lang))  # 复用同一模型（连接池），避免逐章重建
        chapters = self._season_chapters(db, project, season)
        if indices is None:
            targets = list(enumerate(chapters))
        else:
            targets = [(i, chapters[i]) for i in indices if 0 <= i < len(chapters)]
        first_img = (project.first_image or "").strip()
        try:
            for pos, (i, ch) in enumerate(targets, start=1):
                if progress_cb:
                    await progress_cb(pos, len(targets), ch.title)
                await self._gen_one_script(db, project, season, i, ch, chapters, lang, model=model)
                # 第 2 步：生图 / 生视频（参考上一章图；季内第 1 章参考上一季末章或封面）
                if i == 0:
                    if season.number > 1:
                        prev_last = self._prev_season_last_chapter(db, project, season)
                        ref = prev_last.media_path if (prev_last and prev_last.media_path) else ""
                    elif first_img and Path(first_img).is_file() and project.cover_as_first_ref:
                        ref = first_img
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
                    if project.kind == "comic":
                        ch.media_path = await run_sync(
                            partial(dt.generate_image, ch.prompt, ref_path=ref, params=params))
                    else:
                        ch.media_path = await run_sync(
                            partial(dt.generate_video, ch.prompt, ref_video_path=ref, params=params))
                    ch.status = "done"
                    ch.error = ""
                    ch.width = w
                    ch.height = h
                except Exception as e:
                    ch.status = "error"
                    ch.error = str(e)
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
        """记录封面路径（文件已由调用方落盘到 data/media）。"""
        project.first_image = (path or "").strip()
        self._save(db, project)
        return project

    def set_cover_ref(self, db, project: Project, enabled: bool) -> Project:
        """设置是否把封面作为第 1 章参考。"""
        project.cover_as_first_ref = bool(enabled)
        self._save(db, project)
        return project

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

    def _overlay_title(self, path: Path, title: str) -> None:
        """自动生成模式：在成品图底部叠加标题文字（半透明底条 + 居中文字）。

        用系统中文字体渲染（保持原文，不翻译）；标题为空或字体缺失时静默跳过，
        不影响生成本身。就地覆写 path 文件。"""
        title = (title or "").strip()
        font_file = _cjk_font_path()
        if not title or not font_file:
            return
        from PIL import ImageDraw, ImageFont
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        # 字号 ≈ 最短边 8%（下限 20px）；标题过宽时逐级缩小到 90% 图宽以内
        size = max(20, int(min(w, h) * 0.08))
        font = ImageFont.truetype(font_file, size)
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), title, font=font)
        tw = bbox[2] - bbox[0]
        while tw > w * 0.9 and size > 14:
            size = int(size * 0.88)
            font = ImageFont.truetype(font_file, size)
            bbox = draw.textbbox((0, 0), title, font=font)
            tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        # 底部半透明深色底条（高度取字号 1.9 倍 / 图高 10% / 48px 的较大者）
        band = max(size * 19 // 10, h // 10, 48)
        shade = Image.new("RGBA", (w, band), (10, 10, 14, 120))
        img.alpha_composite(shade, (0, h - band))
        draw = ImageDraw.Draw(img)
        draw.text(((w - tw) // 2, h - band + (band - th) // 2 - bbox[1]),
                  title, font=font, fill=(255, 255, 255, 255),
                  stroke_width=max(1, size // 18), stroke_fill=(0, 0, 0, 200))
        out = img if path.suffix.lower() == ".png" else img.convert("RGB")
        out.save(path)

    async def generate_first_image(self, db, project: Project, prompt: str = "",
                                   lang: str = "zh") -> Project:
        """用 DrawThings 生成封面（文生图）。

        prompt 为空时（自动模式）让 LLM 结合一句话创意 + 风格 + 故事大纲 + 角色设定
        自动写封面提示词，并在成品图上用 PIL 叠加作品名称（标题保持原文）。
        产物存为 data/media/first_<项目id>.<ext>（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        dt = self._clients(db, project, lang)
        prompt = (prompt or "").strip()
        auto = not prompt
        if not prompt:
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
                prompt = ((await agent.run(user)).output or "").strip()
        if not prompt:
            raise RuntimeError(L(lang, "未能获得封面提示词，请填写后重试",
                                 "Could not obtain a cover prompt — please fill one in and retry"))
        # Draw Things 生图为同步阻塞调用：放线程池，避免长时间占用事件循环
        path = await run_sync(partial(dt.generate_image, prompt))
        media_dir = Path(self.data_dir) / "media"
        dest = media_dir / f"first_{project.id}{Path(path).suffix or '.png'}"
        if Path(path).resolve() != dest.resolve():
            shutil.move(str(path), str(dest))
        if auto:
            # 自动模式：PIL 叠加作品名称（纯本地快速操作，无需进线程池）
            self._overlay_title(dest, project.title)
        project.first_image = str(dest)
        self._save(db, project)
        return project

    # ---------------- 季封面 ----------------
    def set_season_first_image_path(self, db, project: Project, season: Season, path: str) -> Season:
        """记录季封面路径（文件已由调用方落盘到 data/media）。"""
        season.first_image = (path or "").strip()
        season.updated_at = _now()
        self._save(db, project)
        return season

    @staticmethod
    def _season_label(season: Season, lang: str = "zh") -> str:
        """季名展示：有季名用季名，否则回退「第N季 / Season N」."""
        return (season.title or "").strip() or L(lang, f"第{season.number}季",
                                                  f"Season {season.number}")

    async def generate_season_first_image(self, db, project: Project, season: Season,
                                          prompt: str = "", lang: str = "zh") -> Season:
        """用 DrawThings 生成季封面（文生图）。

        prompt 为空时（自动模式）让 LLM 结合全局一句话创意 + 风格 + 核心角色与
        季标题/季大纲/季新增角色 自动写季封面提示词，并在成品图上用 PIL 叠加季名
        （季名为空回退「第N季」，标题保持原文）。
        产物存为 data/media/seasonfirst_<季id>.<ext>（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        dt = self._clients(db, project, lang)
        prompt = (prompt or "").strip()
        auto = not prompt
        if not prompt:
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
                prompt = ((await agent.run(user)).output or "").strip()
        if not prompt:
            raise RuntimeError(L(lang, "未能获得季封面提示词，请填写后重试",
                                  "Could not obtain a season cover prompt — please fill one in and retry"))
        # Draw Things 生图为同步阻塞调用：放线程池，避免长时间占用事件循环
        path = await run_sync(partial(dt.generate_image, prompt))
        media_dir = Path(self.data_dir) / "media"
        dest = media_dir / f"seasonfirst_{season.id}{Path(path).suffix or '.png'}"
        if Path(path).resolve() != dest.resolve():
            shutil.move(str(path), str(dest))
        if auto:
            # 自动模式：PIL 叠加季名（纯本地快速操作，无需进线程池）
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
        """导出 PDF（仅漫画）：把各章图片按顺序拼成多页 PDF。短剧（视频）不支持。
        season 非空时仅导出该季章节（文件名带 S<季号> 后缀）。返回 (pdf 路径, 文件名)。"""
        if project.kind != "comic":
            raise ValueError("短剧为视频，暂不支持导出 PDF（可导出 ZIP）")
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


