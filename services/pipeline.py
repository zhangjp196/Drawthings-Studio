"""流水线编排：一句话 -> 设定篇幅 -> 整体路线(总纲) -> 章节设定 -> 剧本编写 -> 单任务进行。

数据全部走 SQLite（models.py），媒体文件存 data/media。
每个项目携带所选 LLMConfig / DrawThingConfig 的 id，运行时现场构建客户端，
因此不同项目可用不同的端点/模型/模式。

连续性设计：
- 总纲（arc）：先定整体故事路线（开端→发展→高潮→结局），章节按总纲拆分；
  用户可编辑总纲后重新生成章节（整体路线控制）
- 首图（first_image）：用户上传或提示词生成，作为第 1 章参考
  （漫画 = img2img 参考图；短剧 = 视频首帧），并在全片剧本中作视觉基准
- 剧本编写：把上一章的“文字描述”作为上下文，保证剧情/风格连贯；
  LLM 配置 supports_vision=no 时不附带任何参考图
- 生成：第 1 章用首图（若设置），其余章用上一张图（漫画）/ 上一视频末帧（短剧）
- 单任务/单个调整：可对任意一章单独重生成或修改提示词
"""
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import anyio
from PIL import Image
from sqlalchemy import or_
from pydantic_ai.messages import ImageUrl

from i18n import L
from models import Project, Chapter, LLMConfig, DrawThingConfig

from config_store import ConfigStore
from .agent import (
    ChaptersOut,
    OutlineOut,
    ScriptOut,
    build_model,
    image_data_uri,
    make_agent,
)
from .drawthings import DrawThingsClient, extract_last_frame


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        """删除项目：先删媒体文件（章节媒体 + 首图），再删章节与项目记录。"""
        for ch in db.query(Chapter).filter(Chapter.project_id == project.id).all():
            self._rm_media(ch.media_path)
            db.delete(ch)
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

    # ---------------- 大纲（整体风格/角色/大纲 + 章节规划：标题 + 主题摘要） ----------------
    async def step_outline(self, db, project: Project, lang: str = "zh",
                           res_width: int = 0, res_height: int = 0) -> Project:
        """「确定大纲」：一次性规划 风格/主题/基调 + 整体故事大纲 + 角色设定，并设置默认分辨率。
        不生成章节（章节由「章节规划」单独生成，不会随大纲保存自动生成）。status: planning → arced。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        style = ((project.scope or {}).get("style") or "").strip()
        system = ("你是资深漫画/短剧策划兼编剧。根据一句话创意，为整部作品设计：\n"
                  "1) 风格（用户已指定则沿用，否则推荐一个）、主题、基调；\n"
                  "2) 整体故事大纲：分 开端、发展、高潮、结局 四段，每段 1-2 句讲清发生什么、如何承接到下一段，"
                  "末尾附 1-3 条贯穿全篇的主线设定；\n"
                  "3) 角色设定：列出主要角色（名字 / 外形形象 / 性格 / 核心动机），供后续各章保持角色一致。")
        agent = make_agent(build_model(llm_cfg), system, output_type=OutlineOut)
        user = project.origin + (f"\n风格（用户指定，请沿用）：{style}" if style else "")
        async with agent:
            data = (await agent.run(user)).output
        arc = (data.arc or "").strip()
        if not arc:
            raise RuntimeError(L(lang, "模型未返回大纲内容，请重试",
                                 "The model returned no outline content — please retry"))
        project.scope = {
            "style": style or (data.style or "").strip(),
            "theme": (data.theme or "").strip(),
            "tone": (data.tone or "").strip(),
        }
        project.arc = arc
        project.characters = (data.characters or "").strip()
        project.res_width = int(res_width or 0)
        project.res_height = int(res_height or 0)
        project.status = "arced"
        self._save(db, project)
        return project

    async def _plan_chapters(self, db, project: Project, lang: str,
                             count_mode: str = "auto", count_min: int = 0, count_max: int = 0) -> list[tuple[str, str]]:
        """依据当前大纲/风格/角色，让模型规划章节，返回 [(标题, 一句话主题摘要), ...]。
        章节数量：auto = 模型自行决定；range = 在 [count_min, count_max] 内选择合适数量。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        scope = project.scope or {}
        chars = (project.characters or "").strip()
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

    def _rebuild_chapters(self, db, project: Project, plan: list[tuple[str, str]]) -> None:
        """按 [(标题, 主题摘要)] 重建章节：清空旧章节（清理其媒体）后按序建新的（剧本/媒体留待章节页生成）。"""
        for ch in db.query(Chapter).filter(Chapter.project_id == project.id).all():
            self._rm_media(ch.media_path)
            db.delete(ch)
        db.commit()
        for i, (title, summary) in enumerate(plan):
            db.add(Chapter(project_id=project.id, index=i, title=title, summary=summary))
        db.commit()

    def save_outline(self, db, project: Project, *, arc: str | None = None,
                     characters: str | None = None, style: str | None = None,
                     global_prompt: str | None = None,
                     res_width: int | None = 0, res_height: int | None = 0,
                     count_mode: str | None = None, count_min: int | None = 0, count_max: int | None = 0,
                     chapters: list[dict] | None = None) -> Project:
        """保存大纲页手动编辑：大纲 / 角色设定 / 全局提示词 / 风格 / 默认分辨率 / 章节数量设定 / 每章(标题+摘要)。
        仅更新传入（非 None）的字段；章节按序号对账：改已有、末尾补新、删多余（清理其媒体），
        不触碰已生成的剧本/媒体（保存不触发生成）。"""
        scope = dict(project.scope or {})
        if style is not None:
            scope["style"] = (style or "").strip()
        project.scope = scope
        if arc is not None:
            project.arc = (arc or "").strip()
        if characters is not None:
            project.characters = (characters or "").strip()
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
        if chapters is not None:
            existing = self._load_chapters(db, project)
            for i, item in enumerate(chapters):
                title = (item.get("title") or "").strip()
                summary = (item.get("summary") or "").strip()
                if i < len(existing):
                    if title:
                        existing[i].title = title
                    existing[i].summary = summary
                else:
                    db.add(Chapter(project_id=project.id, index=i,
                                   title=title or f"第{i + 1}章", summary=summary))
            for ch in existing[len(chapters):]:
                self._rm_media(ch.media_path)
                db.delete(ch)
        self._reindex(db, self._load_chapters(db, project))
        db.commit()
        self._save(db, project)
        return project

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

    async def step_chapters(self, db, project: Project, lang: str = "zh",
                            count_mode: str = "auto", count_min: int = 0, count_max: int = 0) -> Project:
        """「章节规划」：依据当前大纲/风格/角色 + 章节数量设定（auto / range）规划章节（标题 + 主题摘要）。
        会清空已有章节/媒体（按大纲重新拆章）。status → chaptered。"""
        project.count_mode = "range" if (count_mode or "auto") == "range" else "auto"
        project.count_min = int(count_min or 0)
        project.count_max = int(count_max or 0)
        plan = await self._plan_chapters(db, project, lang, project.count_mode, project.count_min, project.count_max)
        self._rebuild_chapters(db, project, plan)
        project.status = "chaptered"
        self._save(db, project)
        return project

    def _load_chapters(self, db, project: Project) -> list[Chapter]:
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())

    # ---------------- 阶段 4：剧本编写（按章 / 批量） ----------------
    def _script_agent_context(self, db, project: Project, lang: str):
        """剧本生成所需的上下文：LLM 配置 / 是否支持视觉 / 分辨率上限 / 风格。"""
        llm_cfg, dt_cfg = self._configs(db, project, lang)
        supports_vision = (getattr(llm_cfg, "supports_vision", None) or "yes").lower() == "yes"
        max_side = int(getattr(dt_cfg, "max_side", 0) or 0)
        limit = max_side if max_side > 0 else 1024
        return llm_cfg, supports_vision, limit, (project.scope or {})

    def _script_system(self, project: Project, limit: int) -> str:
        """按作品类型给出剧本/提示词写作的系统提示词。"""
        if project.kind == "comic":
            shot = ("漫画：prompt 描述『一页多格漫画』——一张图内含多个分镜格（竖版漫画页），"
                    "并让画面带文字（分镜旁白、对白气泡、标题文字）；分辨率优先竖版（3:4 或 2:3）。")
            ratios = "3:4 竖=576×768、2:3 竖=576×896、4:3 横=768×576"
        else:
            shot = "短剧：prompt 描述一段连贯的视频画面；分辨率按场景横/竖构图决定。"
            ratios = "9:16 竖=576×1024、16:9 横=1024×576、4:3 横=768×576"
        return ("你是编剧兼分镜提示词作者。根据上一章内容和本章场景，"
                "写本章详细剧本描述（description）和出图/出视频提示词（prompt 用英文，保持风格与上一章连贯）。\n"
                f"{shot}\n"
                f"同时按本章构图决定出图分辨率 width/height（均为 64 的倍数，最长边不超过 {limit} 像素；参考：{ratios}）。")

    async def _gen_one_script(self, db, project: Project, i: int, ch: Chapter,
                              chapters: list[Chapter], lang: str = "zh") -> Chapter:
        """为第 i 章单独写剧本/提示词/分辨率（供「按章生成」与「批量生成」复用）。"""
        llm_cfg, supports_vision, limit, scope = self._script_agent_context(db, project, lang)
        style = (scope.get("style") or "").strip()
        media_dir = Path(self.data_dir) / "media"
        first_img = (project.first_image or "").strip()
        agent = make_agent(build_model(llm_cfg), self._script_system(project, limit), output_type=ScriptOut)
        prev = chapters[i - 1] if i > 0 else None
        context = ""
        ref_img = None
        if prev:
            context = f"上一章《{prev.title}》：{prev.description}"
            prev_media = (prev.media_path or "").strip()
            if prev_media:  # 上一章已生成则作参考；视频先抽末帧，避免把 mp4 当图片喂给 LLM
                ext = Path(prev_media).suffix.lower()
                if ext in (".mp4", ".mov", ".webm", ".gif"):
                    ref_img = extract_last_frame(prev_media, media_dir)
                else:
                    ref_img = prev_media
        if i == 0 and first_img and Path(first_img).is_file() and project.cover_as_first_ref:
            # 第 1 章：以封面作视觉基准（仅当开启「封面作为第 1 章参考」），随剧本一并给 LLM 对齐
            context += "\n附封面（第 1 章视觉基准）：它是全系列的视觉基准（角色形象/风格），请保持主角与风格与其一致。"
            if supports_vision:
                ref_img = first_img
        if not supports_vision:
            ref_img = None  # 纯文本模型：不附带任何参考图
        chars = (project.characters or "").strip()
        gprompt = (project.global_prompt or "").strip()
        base = (ch.summary or ch.description or "").strip() or "（按大纲与上一章自然续写）"
        user = (
            (f"整体风格：{style}\n" if style else "")
            + (f"角色设定（请保持一致）：{chars}\n" if chars else "")
            + (f"全局要点（务必遵循）：{gprompt}\n" if gprompt else "")
            + f"本章《{ch.title}》主题摘要：{base}\n\n{context}"
        )
        prompt_content: str | list = user
        if ref_img:
            prompt_content = [ImageUrl(url=image_data_uri(ref_img)), user]
        data = (await agent.run(prompt_content)).output
        ch.description = data.description
        ch.prompt = data.prompt
        ch.width = int(data.width or 0)
        ch.height = int(data.height or 0)
        return ch

    # ---------------- 阶段 5：逐章生成画面（出图提示词 + 生图，两步连贯） ----------------
    async def step_generate(self, db, project: Project, indices: list[int] | None = None,
                             lang: str = "zh", progress_cb=None,
                             chapter_done_cb=None) -> Project:
        """逐章生成画面：每章跑完整 2 步——① (重新)生成出图提示词/描述/分辨率（参考上一章已生成的图）② 生图/生视频。
        indices: 章节序号列表；None=全部章节。按序号顺序逐个进行，后章参考前章已生成的图，页面可逐章实时刷新。

        第 2 步生图的连续性参考：第 1 章用封面（若开启）或为空——漫画作 img2img 参考、短剧作视频首帧；
        其余章沿用上一章媒体（漫画=上一张图，短剧=上一视频）。
        阻塞的 LLM / DrawThings 调用放到线程/异步执行（不阻塞事件循环）；
        progress_cb: 可选异步回调 (current, total, chapter_title)，每章开始前调用（SSE 进度）；
        chapter_done_cb: 可选异步回调 (chapter)，每章两步完成后调用（SSE 实时回传该章提示词/媒体/状态，供页面逐个刷新）。"""
        dt = self._clients(db, project, lang)
        chapters = self._load_chapters(db, project)
        if indices is None:
            targets = list(chapters)
        else:
            targets = [chapters[i] for i in indices if 0 <= i < len(chapters)]
        first_img = (project.first_image or "").strip()
        for n, ch in enumerate(targets, start=1):
            if progress_cb:
                await progress_cb(n, len(targets), ch.title)
            # 第 1 步：(重新)生成出图提示词 / 描述 / 分辨率——参考上一章已生成的图（连贯，后章基于前章落定）
            await self._gen_one_script(db, project, ch.index, ch, chapters, lang)
            # 第 2 步：生图 / 生视频（img2img 参考上一章图；第 1 章参考封面或为空）
            if ch.index == 0 and first_img and Path(first_img).is_file() and project.cover_as_first_ref:
                ref = first_img
            else:
                prev = chapters[ch.index - 1] if ch.index > 0 else None
                ref = prev.media_path if (prev and prev.media_path) else ""
            try:
                w = int(ch.width or 0) or int(project.res_width or 0)
                h = int(ch.height or 0) or int(project.res_height or 0)
                params = {}
                if w and h:  # 章节分辨率；缺省回退项目默认分辨率（客户端再按 max_side 限幅）
                    params = {"width": w, "height": h}
                if project.kind == "comic":
                    ch.media_path = await anyio.to_thread.run_sync(
                        lambda _p=ch.prompt, _r=ref, _pr=params: dt.generate_image(_p, ref_path=_r, params=_pr))
                else:
                    ch.media_path = await anyio.to_thread.run_sync(
                        lambda _p=ch.prompt, _r=ref, _pr=params: dt.generate_video(_p, ref_video_path=_r, params=_pr))
                ch.status = "done"
                ch.error = ""
            except Exception as e:  # 单章失败不影响其他章
                ch.status = "error"
                ch.error = str(e)
            if chapter_done_cb:
                await chapter_done_cb(ch)
        db.commit()
        self._save(db, project)
        return project

    # ---------------- 手动完成 ----------------
    def mark_done(self, db, project: Project) -> Project:
        """手动标记完成（不再由「全部生成」自动触发）。"""
        project.status = "done"
        self._save(db, project)
        return project

    # ---------------- 章节字段保存（提示词 + 分辨率） ----------------
    def save_chapter_fields(self, db, project: Project, index: int, prompt: str,
                             width: int = 0, height: int = 0) -> Project:
        """手动编辑第 index 章的出图提示词 / 分辨率（宽/高）。"""
        ch = self._load_chapters(db, project)[index]
        ch.prompt = (prompt or "").strip()
        try:
            w = int(width or 0)
            h = int(height or 0)
            ch.width = w if 0 < w <= 4096 else 0
            ch.height = h if 0 < h <= 4096 else 0
        except (TypeError, ValueError):
            pass
        db.commit()
        self._save(db, project)
        return project

    # ---------------- 章节增 / 删 / 排序 ----------------
    def _reindex(self, db, chapters: list[Chapter]) -> None:
        for i, ch in enumerate(chapters):
            ch.index = i

    def add_chapter(self, db, project: Project) -> Chapter:
        """在末尾新增一章（标题/主题摘要留空，可随后「生成剧本」或手改）。"""
        chapters = self._load_chapters(db, project)
        ch = Chapter(project_id=project.id, index=len(chapters), title="新章节", summary="")
        db.add(ch)
        db.commit()
        self._reindex(db, self._load_chapters(db, project))
        db.commit()
        self._save(db, project)
        return ch

    def delete_chapter(self, db, project: Project, index: int) -> Project:
        """删除第 index 章（连同清理其媒体文件），其余章节重新编号。"""
        chapters = self._load_chapters(db, project)
        ch = chapters[index]
        self._rm_media(ch.media_path)
        db.delete(ch)
        db.commit()
        self._reindex(db, self._load_chapters(db, project))
        db.commit()
        self._save(db, project)
        return project

    def move_chapter(self, db, project: Project, index: int, direction: str) -> Project:
        """上移 / 下移第 index 章（direction: up/down），交换后重新编号。"""
        chapters = self._load_chapters(db, project)
        j = index - 1 if direction == "up" else index + 1
        if not (0 <= j < len(chapters)) or j == index:
            return project
        chapters[index], chapters[j] = chapters[j], chapters[index]
        self._reindex(db, chapters)
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

    async def generate_first_image(self, db, project: Project, prompt: str = "",
                                   lang: str = "zh") -> Project:
        """用 DrawThings 生成封面（文生图）。

        prompt 为空时让 LLM 结合一句话创意 + 风格 + 故事大纲 + 角色设定 自动写封面提示词。
        产物存为 data/media/first_<项目id>.<ext>（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project, lang)
        dt = self._clients(db, project, lang)
        prompt = (prompt or "").strip()
        if not prompt:
            scope = project.scope or {}
            system = ("你是封面美术提示词作者。请结合一句话创意、风格、故事大纲与角色设定，"
                      "写一段详细的封面英文提示词（主体角色、场景、构图、光线、氛围、风格关键词）。只输出提示词文本。")
            agent = make_agent(build_model(llm_cfg), system)
            user = f"一句话创意：{project.origin}\n风格：{scope.get('style', '')}"
            if (project.arc or "").strip():
                user += f"\n故事大纲：{project.arc.strip()}"
            if (project.characters or "").strip():
                user += f"\n角色设定：{project.characters.strip()}"
            async with agent:
                prompt = ((await agent.run(user)).output or "").strip()
        if not prompt:
            raise RuntimeError(L(lang, "未能获得封面提示词，请填写后重试",
                                 "Could not obtain a cover prompt — please fill one in and retry"))
        path = dt.generate_image(prompt)
        media_dir = Path(self.data_dir) / "media"
        dest = media_dir / f"first_{project.id}{Path(path).suffix or '.png'}"
        if Path(path).resolve() != dest.resolve():
            shutil.move(str(path), str(dest))
        project.first_image = str(dest)
        self._save(db, project)
        return project

    # ---------------- 导出（ZIP / PDF） ----------------
    def _safe_name(self, project: Project) -> str:
        return ((project.title or project.origin or project.id) or "export")[:40]

    def export_zip(self, db, project: Project) -> tuple[str, str]:
        """导出 ZIP：大纲/角色/各章剧本文本 + 全部媒体（图/视频）+ 首图。返回 (zip 绝对路径, 文件名)。"""
        chapters = self._load_chapters(db, project)
        scope = project.scope or {}
        export_dir = Path(self.data_dir) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{self._safe_name(project)}_{project.id}.zip"
        zpath = export_dir / fname
        readme = (
            f"标题：{project.title}\n类型：{project.kind}\n一句话创意：{project.origin}\n"
            f"风格：{scope.get('style', '')}\n主题：{scope.get('theme', '')}\n基调：{scope.get('tone', '')}\n"
            f"默认分辨率：{project.res_width}×{project.res_height}\n\n"
            f"角色设定：\n{project.characters}\n\n整体故事大纲：\n{project.arc}\n"
        )
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", readme.encode("utf-8"))
            for i, ch in enumerate(chapters):
                chap_txt = (
                    f"标题：{ch.title}\n主题摘要：{ch.summary}\n剧本：{ch.description}\n"
                    f"提示词：{ch.prompt}\n分辨率：{ch.width}×{ch.height}\n媒体：{ch.media_path}\n"
                )
                z.writestr(f"chapters/{i:02d}.txt", chap_txt.encode("utf-8"))
                if ch.media_path and Path(ch.media_path).is_file():
                    z.write(ch.media_path, f"media/{i:02d}_{Path(ch.media_path).name}")
            if project.first_image and Path(project.first_image).is_file():
                z.write(project.first_image, f"media/00_first_{Path(project.first_image).name}")
        return str(zpath), fname

    def export_pdf(self, db, project: Project) -> tuple[str, str]:
        """导出 PDF（仅漫画）：把各章图片按顺序拼成多页 PDF。短剧（视频）不支持。返回 (pdf 路径, 文件名)。"""
        if project.kind != "comic":
            raise ValueError("短剧为视频，暂不支持导出 PDF（可导出 ZIP）")
        imgs = []
        for ch in self._load_chapters(db, project):
            mp = (ch.media_path or "").strip()
            if mp and Path(mp).is_file() and Path(mp).suffix.lower() in (".png", ".jpg", ".jpeg"):
                imgs.append(Image.open(mp).convert("RGB"))
        if not imgs:
            raise ValueError("没有可导出的章节图片")
        export_dir = Path(self.data_dir) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{self._safe_name(project)}_{project.id}.pdf"
        ppath = export_dir / fname
        imgs[0].save(ppath, save_all=True, append_images=imgs[1:], resolution=96.0)
        for im in imgs:
            im.close()
        return str(ppath), fname


