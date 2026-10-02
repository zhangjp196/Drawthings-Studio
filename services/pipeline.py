"""流水线调度入口：漫画 / 短剧（视频）是两条完全独立的流水线，本模块只按类型分发。

- 漫画 → services/pipeline_comic.py（ComicPipeline）
- 短剧 → services/pipeline_drama.py（DramaPipeline）

两者互不共享业务逻辑（无共同基类、互不引用）；本模块与兼容再导出的通用工具
（来自 pipeline_common）均不含任何类型特有逻辑。main.py 经由本门面按 project.kind
路由到对应流水线，接口形态保持不变。
"""
from .pipeline_common import _now, chars_from_raw, hex_to_rgb, run_sync  # noqa: F401  (兼容: main.py 从本模块导入)
from .pipeline_comic import ComicPipeline
from .pipeline_drama import DramaPipeline

__all__ = [
    "Pipeline", "ComicPipeline", "DramaPipeline",
    "_now", "chars_from_raw", "hex_to_rgb", "run_sync",
]


class Pipeline:
    """调度门面：按 project.kind（comic / drama）把每个请求路由到对应流水线。

    漫画用「月/章」命名（Season/Chapter），短剧用「集/片段」命名（Episode/Clip）；
    两条线方法名可能不同，故按 kind 显式分发（不改动任何模型 / 数据库 / 路由契约）。"""

    def __init__(self, data_dir):
        self.comic = ComicPipeline(data_dir)
        self.drama = DramaPipeline(data_dir)

    # ---------------- 分发规则 ----------------
    def _of(self, project):
        """按项目类型取流水线实例。"""
        return self.comic if (project.kind or "") == "comic" else self.drama

    @staticmethod
    def _is_comic(project) -> bool:
        return (project.kind or "") == "comic"

    # ---------------- 不带项目的方法 ----------------
    def get(self, db, project_id: str):
        return self.comic.get(db, project_id)

    def create(self, db, kind: str, origin: str,
               llm_config_id: str, drawthings_config_id: str,
               title: str = ""):
        """新建：按 kind 参数路由（comic→漫画流水线 / 其他→短剧流水线）。"""
        return (self.comic if kind == "comic" else self.drama).create(
            db, kind, origin, llm_config_id, drawthings_config_id, title=title)

    def list_projects(self, db, kind=None, status=None, q=None, sort="desc",
                      limit: int = 10, offset: int = 0):
        """列表查询对两种类型完全相同（仅按 Project.kind 字段过滤），固定走漫画流水线。"""
        return self.comic.list_projects(db, kind, status, q, sort, limit, offset)

    # ---------------- 带项目的方法（按 project.kind 路由） ----------------
    def delete_project(self, db, project):
        return self._of(project).delete_project(db, project)

    def ensure_first_season(self, db, project):
        comic = self._is_comic(project)
        if comic:
            return self.comic.ensure_first_season(db, project)
        return self.drama.ensure_first_episode(db, project)

    def add_season(self, db, project, title: str = ""):
        if self._is_comic(project):
            return self.comic.add_season(db, project, title=title)
        return self.drama.add_episode(db, project, title=title)

    def _get_season(self, db, project, season_id: str):
        if self._is_comic(project):
            return self.comic._get_season(db, project, season_id)
        return self.drama._get_episode(db, project, season_id)

    def save_season(self, db, project, season, **kwargs):
        if self._is_comic(project):
            return self.comic.save_season(db, project, season, **kwargs)
        return self.drama.save_episode(db, project, season, **kwargs)

    def delete_season(self, db, project, season_id: str):
        if self._is_comic(project):
            return self.comic.delete_season(db, project, season_id)
        return self.drama.delete_episode(db, project, season_id)

    def step_season_arc(self, db, project, season, lang: str = "zh", extra_prompt: str = ""):
        if self._is_comic(project):
            return self.comic.step_season_arc(db, project, season, lang, extra_prompt)
        return self.drama.step_episode_arc(db, project, season, lang, extra_prompt)

    def step_season_chars(self, db, project, season, lang: str = "zh", extra_prompt: str = ""):
        if self._is_comic(project):
            return self.comic.step_season_chars(db, project, season, lang, extra_prompt)
        return self.drama.step_episode_chars(db, project, season, lang, extra_prompt)

    def step_chars(self, db, project, lang: str = "zh", extra_prompt: str = ""):
        return self._of(project).step_chars(db, project, lang, extra_prompt)

    def step_chapters(self, db, project, season, lang: str = "zh",
                      count_min: int = 0, count_max: int = 0):
        if self._is_comic(project):
            return self.comic.step_chapters(db, project, season, lang, count_min, count_max)
        return self.drama.step_clips(db, project, season, lang, count_min, count_max)

    def step_chapters_stream(self, db, project, season, lang: str = "zh",
                             count_min: int = 0, count_max: int = 0, indices: list | None = None,
                             progress_cb=None, chapter_done_cb=None):
        if self._is_comic(project):
            return self.comic.step_chapters_stream(db, project, season, lang, count_min,
                                                   count_max, indices, progress_cb, chapter_done_cb)
        return self.drama.step_clips_stream(db, project, season, lang, count_min,
                                            count_max, indices, progress_cb, chapter_done_cb)

    def step_generate(self, db, project, season, indices: list | None = None,
                      lang: str = "zh", progress_cb=None, chapter_done_cb=None, score_cb=None):
        return self._of(project).step_generate(db, project, season, indices, lang,
                                               progress_cb, chapter_done_cb, score_cb)

    def _season_chapters(self, db, project, season):
        if self._is_comic(project):
            return self.comic._season_chapters(db, project, season)
        return self.drama._episode_clips(db, project, season)

    def save_chapter_fields(self, db, project, season, index: int, prompt: str):
        if self._is_comic(project):
            return self.comic.save_chapter_fields(db, project, season, index, prompt)
        return self.drama.save_clip_fields(db, project, season, index, prompt)

    async def vlm_score_chapter(self, db, project, season, index: int, lang: str = "zh"):
        if self._is_comic(project):
            return await self.comic.vlm_score_chapter(db, project, season, index, lang)
        return await self.drama.vlm_score_clip(db, project, season, index, lang)

    def add_chapter(self, db, project, season):
        if self._is_comic(project):
            return self.comic.add_chapter(db, project, season)
        return self.drama.add_clip(db, project, season)

    def delete_chapter(self, db, project, season, index: int):
        if self._is_comic(project):
            return self.comic.delete_chapter(db, project, season, index)
        return self.drama.delete_clip(db, project, season, index)

    def move_chapter(self, db, project, season, index: int, direction: str):
        if self._is_comic(project):
            return self.comic.move_chapter(db, project, season, index, direction)
        return self.drama.move_clip(db, project, season, index, direction)

    def set_first_image_path(self, db, project, path: str):
        return self._of(project).set_first_image_path(db, project, path)

    def generate_first_image(self, db, project, prompt: str = "",
                             lang: str = "zh", include_title: bool = True):
        return self._of(project).generate_first_image(db, project, prompt, lang, include_title)

    def set_season_first_image_path(self, db, project, season, path: str):
        if self._is_comic(project):
            return self.comic.set_season_first_image_path(db, project, season, path)
        return self.drama.set_episode_first_image_path(db, project, season, path)

    def generate_season_first_image(self, db, project, season, prompt: str = "",
                                    lang: str = "zh", include_title: bool = True):
        if self._is_comic(project):
            return self.comic.generate_season_first_image(db, project, season, prompt, lang, include_title)
        return self.drama.generate_episode_first_image(db, project, season, prompt, lang, include_title)

    def overlay_first_image_title(self, db, project, lang: str = "zh", opts: dict | None = None):
        return self._of(project).overlay_first_image_title(db, project, lang, opts)

    def set_season_cover_ref(self, db, project, season, enabled: bool):
        if self._is_comic(project):
            return self.comic.set_season_cover_ref(db, project, season, enabled)
        return self.drama.set_episode_cover_ref(db, project, season, enabled)

    def overlay_season_first_image_title(self, db, project, season, lang: str = "zh",
                                         opts: dict | None = None):
        if self._is_comic(project):
            return self.comic.overlay_season_first_image_title(db, project, season, lang, opts)
        return self.drama.overlay_episode_first_image_title(db, project, season, lang, opts)

    def set_char_image(self, db, project, char_id: str, path: str):
        return self._of(project).set_char_image(db, project, char_id, path)

    def gen_char_description(self, db, project, char_id: str, lang: str = "zh"):
        return self._of(project).gen_char_description(db, project, char_id, lang)

    def save_outline(self, db, project, **kwargs):
        return self._of(project).save_outline(db, project, **kwargs)

    def export_zip(self, db, project, season=None):
        return self._of(project).export_zip(db, project, season)

    def export_pdf(self, db, project, season=None):
        return self._of(project).export_pdf(db, project, season)

    def export_video(self, db, project, season=None):
        return self._of(project).export_video(db, project, season)
