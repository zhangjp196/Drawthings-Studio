"""短剧 API 路由（/api/dramas/*）：仅短剧走向，不涉及漫画逻辑。

- 只调用短剧流水线（pipeline.drama），无 kind 分支
- 漫画对应模块：services/api_comic.py（/api/comics/*）
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db
from i18n import L
from config_store import ConfigStore
from services.drawthings import norm_ref_flag
from services.api_common import _lang, _json_body
from services.runtime import pipeline

router = APIRouter(prefix="/api/dramas", tags=["drama"])


@router.post("")
async def drama_create(request: Request, db: Session = Depends(get_db)):
    """新建短剧创作：标题 + 主题（一句话）→ 短剧流水线。"""
    lang = _lang(request)
    body = await _json_body(request)
    origin = str(body.get("origin") or "").strip()
    if not origin:
        raise HTTPException(status_code=400,
                            detail=L(lang, "主题不能为空", "The idea (origin) cannot be empty"))
    style = str(body.get("style_custom") or "").strip() or str(body.get("style") or "").strip()
    cs = ConfigStore(db)
    llm_cfg = cs.get_llm(str(body.get("llm_config_id") or ""))
    dt_cfg = cs.get_drawthing(str(body.get("drawthings_config_id") or ""))
    if not llm_cfg or not dt_cfg:
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择有效的 VLM 与 DrawThings 配置",
                                     "Please select valid VLM and DrawThings configs"))
    # 功能级模型：项目侧自选；留空 = 跟随 DrawThings 配置里的模型（两边都空才拒绝）
    dt_model_image = str(body.get("dt_model_image") or "").strip()[:200]
    dt_model_video = str(body.get("dt_model_video") or "").strip()[:200]
    # 功能级参考图开关：缺省 = 跟随配置；显式 0/1 = 覆盖
    dt_ref_image = norm_ref_flag(body.get("dt_ref_image"))
    dt_ref_video = norm_ref_flag(body.get("dt_ref_video"))
    if not dt_model_video and not (dt_cfg.model_video or ""):
        raise HTTPException(status_code=400,
                            detail=L(lang, "请选择出视频模型（DrawThings 配置里也未设置模型）",
                                     "Please pick a video model (none set in the Draw Things config)"))
    project = pipeline.drama.create(db, "drama", origin, llm_cfg.id, dt_cfg.id,
                                    style=style, title=str(body.get("title") or "").strip()[:200])
    if dt_model_image:
        project.dt_model_image = dt_model_image
    if dt_model_video:
        project.dt_model_video = dt_model_video
    if dt_ref_image is not None:
        project.dt_ref_image = "1" if dt_ref_image else "0"
    if dt_ref_video is not None:
        project.dt_ref_video = "1" if dt_ref_video else "0"
    if dt_model_image or dt_model_video or dt_ref_image is not None or dt_ref_video is not None:
        db.commit()
    return {"id": project.id}
