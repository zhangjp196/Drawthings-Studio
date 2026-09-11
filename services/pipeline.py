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
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai.messages import ImageUrl

from models import Project, Chapter, LLMConfig, DrawThingConfig

from config_store import ConfigStore
from .agent import (
    ChaptersOut,
    ScopeOut,
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
    def _configs(self, db, project) -> tuple[LLMConfig, DrawThingConfig]:
        cs = ConfigStore(db)
        llm_cfg = cs.get_llm(project.llm_config_id)
        dt_cfg = cs.get_drawthing(project.drawthings_config_id)
        if not llm_cfg or not dt_cfg:
            raise RuntimeError("项目所选配置已被删除，请到「配置管理」重新选择或新建")
        return llm_cfg, dt_cfg

    def _clients(self, db, project):
        """DrawThings 客户端（出图/出视频用）。"""
        _, dt_cfg = self._configs(db, project)
        return DrawThingsClient(dt_cfg, data_dir=self.data_dir)

    def _llm_cfg(self, db, project) -> LLMConfig:
        llm_cfg, _ = self._configs(db, project)
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

    def delete_project(self, db, project: Project):
        """删除项目：先删媒体文件（章节媒体 + 首图），再删章节与项目记录。"""
        media_dir = Path(self.data_dir) / "media"

        def _rm(path: str):
            try:
                p = Path(path or "")
                if p and p.is_file() and p.resolve().is_relative_to(media_dir.resolve()):
                    p.unlink()
            except OSError:
                pass

        for ch in db.query(Chapter).filter(Chapter.project_id == project.id).all():
            _rm(ch.media_path)
            db.delete(ch)
        _rm(project.first_image)
        db.delete(project)
        db.commit()

    def list_projects(self, db, kind=None, status=None, sort_desc=True,
                      limit: int = 10, offset: int = 0) -> tuple[list, int]:
        q = db.query(Project)
        if kind:
            q = q.filter(Project.kind == kind)
        if status:
            q = q.filter(Project.status == status)
        total = q.count()
        order = Project.created_at.desc() if sort_desc else Project.created_at.asc()
        rows = q.order_by(order).limit(limit).offset(offset).all()
        return rows, total

    def _save(self, db, project: Project):
        project.updated_at = _now()
        db.add(project)
        db.commit()
        db.refresh(project)

    # ---------------- 阶段 1：设定篇幅 ----------------
    async def step_scope(self, db, project: Project) -> Project:
        llm_cfg = self._llm_cfg(db, project)
        style = ((project.scope or {}).get("style") or "").strip()
        system = "你是一个漫画/短剧策划。根据一句话创意规划篇幅与风格。"
        agent = make_agent(build_model(llm_cfg), system, output_type=ScopeOut)
        user = project.origin + (f"\n风格（用户指定，请沿用）：{style}" if style else "")
        async with agent:
            data = (await agent.run(user)).output
        project.scope = {
            "total_chapters": int(data.total_chapters or 4),
            # 用户指定的风格优先于 LLM 推荐
            "style": style or data.style,
            "theme": data.theme,
            "tone": data.tone,
        }
        project.status = "scoped"
        self._save(db, project)
        return project

    # ---------------- 阶段 2：整体路线（故事总纲） ----------------
    async def step_arc(self, db, project: Project) -> Project:
        """生成整体故事总纲（开端→发展→高潮→结局），作为章节拆分的总路线。
        用户可编辑总纲（save_arc）后重新生成章节，实现“整体路线控制”。"""
        llm_cfg = self._llm_cfg(db, project)
        scope = project.scope or {}
        system = ("你是资深编剧。根据一句话创意、风格与篇幅，为整部作品设计整体故事总纲：\n"
                  "分 开端、发展、高潮、结局 四段，每段用 1-2 句话讲清楚发生什么、如何承接到下一段；\n"
                  "最后可附 1-3 条贯穿全篇的主线设定（角色、动机、核心冲突）。"
                  "直接输出总纲文本。")
        agent = make_agent(build_model(llm_cfg), system)
        user = (f"一句话创意：{project.origin}\n"
                f"风格：{scope.get('style', '')}\n"
                f"主题：{scope.get('theme', '')}\n"
                f"基调：{scope.get('tone', '')}\n"
                f"总篇幅：{scope.get('total_chapters', 3)} 章")
        async with agent:
            out = (await agent.run(user)).output
        project.arc = (out or "").strip()
        if not project.arc:
            raise RuntimeError("模型未返回总纲内容，请重试")
        project.status = "arced"
        self._save(db, project)
        return project

    def save_arc(self, db, project: Project, arc: str) -> Project:
        """保存用户手改的总纲（不改状态，由用户决定是否重生成章节）。"""
        project.arc = (arc or "").strip()
        self._save(db, project)
        return project

    # ---------------- 阶段 3：章节设定 ----------------
    async def step_chapters(self, db, project: Project) -> Project:
        llm_cfg = self._llm_cfg(db, project)
        scope = project.scope
        system = ("你是分集/分章策划。把规划拆成若干章，每章给标题和一句话场景。"
                  "若提供了整体故事总纲，必须按总纲的节奏（开端→发展→高潮→结局）分配章节。")
        agent = make_agent(build_model(llm_cfg), system, output_type=ChaptersOut)
        user = f"主题：{scope.get('theme', '')}\n风格：{scope.get('style', '')}\n基调：{scope.get('tone', '')}\n共{scope.get('total_chapters', 3)}章"
        if (project.arc or "").strip():
            user += f"\n\n整体故事总纲（按此总纲拆章）：\n{project.arc.strip()}"
        async with agent:
            data = (await agent.run(user)).output
        # 先清空旧章节，再按新数量重建
        db.query(Chapter).filter(Chapter.project_id == project.id).delete()
        db.commit()
        for i, c in enumerate(data.chapters):
            db.add(Chapter(
                project_id=project.id, index=i,
                title=c.title or f"第{i + 1}章",
                description=c.scene,
            ))
        db.commit()
        project.status = "chaptered"
        self._save(db, project)
        return project

    def _load_chapters(self, db, project: Project) -> list[Chapter]:
        return (db.query(Chapter)
                .filter(Chapter.project_id == project.id)
                .order_by(Chapter.index).all())

    # ---------------- 阶段 4：剧本编写 ----------------
    async def step_script(self, db, project: Project) -> Project:
        llm_cfg = self._llm_cfg(db, project)
        supports_vision = (getattr(llm_cfg, "supports_vision", None) or "yes").lower() == "yes"
        chapters = self._load_chapters(db, project)
        media_dir = Path(self.data_dir) / "media"
        scope = project.scope or {}
        style = (scope.get("style") or "").strip()
        first_img = (project.first_image or "").strip()
        system = ("你是编剧兼分镜提示词作者。根据上一章内容和本章场景，"
                  "写本章详细剧本描述和出图/出视频提示词（prompt 用英文，保持风格与上一章连贯）。")
        agent = make_agent(build_model(llm_cfg), system, output_type=ScriptOut)
        async with agent:
            for i, ch in enumerate(chapters):
                prev = chapters[i - 1] if i > 0 else None
                context = ""
                ref_img = None
                if prev:
                    context = f"上一章《{prev.title}》：{prev.description}"
                    # 若上一章已生成则附带参考图；视频（短剧）先抽末帧，避免把 mp4 当图片喂给 LLM
                    prev_media = (prev.media_path or "").strip()
                    if prev_media:
                        ext = Path(prev_media).suffix.lower()
                        if ext in (".mp4", ".mov", ".webm", ".gif"):
                            ref_img = extract_last_frame(prev_media, media_dir)
                        else:
                            ref_img = prev_media
                # 第 1 章：首图是全片视觉基准（角色形象/风格），随剧本一并给 LLM 对齐
                if i == 0 and first_img and Path(first_img).is_file():
                    context += "\n附首图：它是全系列的视觉基准（角色形象/风格），请保持主角与风格与其一致。"
                    if supports_vision:
                        ref_img = first_img
                if not supports_vision:
                    ref_img = None  # 纯文本模型：不附带任何参考图
                user = (f"整体风格：{style}\n" if style else "") + \
                    f"本章《{ch.title}》场景：{ch.description}\n\n{context}"
                prompt_content: str | list = user
                if ref_img:
                    prompt_content = [ImageUrl(url=image_data_uri(ref_img)), user]
                data = (await agent.run(prompt_content)).output
                ch.description = data.description
                ch.prompt = data.prompt
        db.commit()
        project.status = "scripted"
        self._save(db, project)
        return project

    # ---------------- 阶段 5：单任务进行 ----------------
    def step_generate(self, db, project: Project, index: int | None = None) -> Project:
        """index 为 None 时生成所有未完成的章；否则只生成第 index 章（支持单个调整/重生成）。

        连续性参考：第 1 章用首图（若已设置）——漫画作 img2img 参考、短剧作视频首帧；
        其余章沿用上一章媒体（漫画=上一张图，短剧=上一视频）。"""
        dt = self._clients(db, project)
        chapters = self._load_chapters(db, project)
        if index is not None:
            targets = [chapters[index]]
        else:
            targets = [c for c in chapters if c.status != "done"]
        first_img = (project.first_image or "").strip()
        for ch in targets:
            if ch.index == 0 and first_img and Path(first_img).is_file():
                ref = first_img
            else:
                prev = chapters[ch.index - 1] if ch.index > 0 else None
                ref = prev.media_path if (prev and prev.media_path) else ""
            try:
                if project.kind == "comic":
                    ch.media_path = dt.generate_image(ch.prompt, ref_path=ref)
                else:
                    ch.media_path = dt.generate_video(ch.prompt, ref_video_path=ref)
                ch.status = "done"
                ch.error = ""
            except Exception as e:  # 单章失败不影响其他章
                ch.status = "error"
                ch.error = str(e)
        db.commit()
        if index is None and chapters and all(c.status == "done" for c in chapters):
            project.status = "done"
        self._save(db, project)
        return project

    def edit_prompt(self, db, project: Project, index: int, prompt: str) -> Project:
        ch = self._load_chapters(db, project)[index]
        ch.prompt = prompt
        db.commit()
        self._save(db, project)
        return project

    def regenerate(self, db, project: Project, index: int) -> Project:
        """单独重生成第 index 章（用其提示词 + 上一章作参考）。"""
        return self.step_generate(db, project, index=index)

    # ---------------- 首图 ----------------
    def set_first_image_path(self, db, project: Project, path: str) -> Project:
        """记录首图路径（文件已由调用方落盘到 data/media）。"""
        project.first_image = (path or "").strip()
        self._save(db, project)
        return project

    async def generate_first_image(self, db, project: Project, prompt: str = "") -> Project:
        """用 DrawThings 生成首图（文生图）。

        prompt 为空时让 LLM 根据一句话创意+风格自动写首图提示词。
        产物存为 data/media/first_<项目id>.<ext>，供第 1 章作参考（可重复生成覆盖）。"""
        llm_cfg = self._llm_cfg(db, project)
        dt = self._clients(db, project)
        prompt = (prompt or "").strip()
        if not prompt:
            scope = project.scope or {}
            system = ("你是分镜提示词作者。根据一句话创意与风格，写一段详细的首图英文提示词"
                      "（主角形象、场景、构图、光线、风格关键词）。只输出提示词文本。")
            agent = make_agent(build_model(llm_cfg), system)
            user = f"一句话创意：{project.origin}\n风格：{scope.get('style', '')}"
            async with agent:
                prompt = ((await agent.run(user)).output or "").strip()
        if not prompt:
            raise RuntimeError("未能获得首图提示词，请填写后重试")
        path = dt.generate_image(prompt)
        media_dir = Path(self.data_dir) / "media"
        dest = media_dir / f"first_{project.id}{Path(path).suffix or '.png'}"
        if Path(path).resolve() != dest.resolve():
            shutil.move(str(path), str(dest))
        project.first_image = str(dest)
        self._save(db, project)
        return project


