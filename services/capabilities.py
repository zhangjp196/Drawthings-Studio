"""功能级生成能力解析（项目 / 微创作共用）。

功能级字段在 `Project` 与 `MicroWork` 上语义完全一致：
- `dt_model_image` / `dt_model_video`：本作品自选出图/出视频模型，空 = 跟随 DrawThings 配置；
- `dt_ref_image` / `dt_ref_video`：是否用参考图（图生图 / 图生视频），'' = 跟随配置，'0'/'1' = 显式覆盖；
- `dt_max_steps_image` / `dt_max_steps_video`：图像 / 视频各自的最大 Step 数
  （随所选模型一起配，两者需求差别很大），0 = 跟随预设自带步数。

把这些「覆盖规则」集中到这里，避免项目侧与微创作各写一份而漂移。
"""

from dataclasses import dataclass
from pathlib import Path

from services.drawthings import build_drawthings_client, norm_ref_flag


@dataclass(frozen=True)
class Capabilities:
    """一次生成生效的能力：能否出图/出视频 + 是否启用参考图。"""

    can_image: bool
    can_video: bool
    ref_image: bool
    ref_video: bool


def ref_image_enabled(cfg, subject) -> bool:
    """生效的「图像支持参考图片」：功能级优先，配置兜底。"""
    _r = norm_ref_flag(getattr(subject, "dt_ref_image", ""))
    return bool(_r) if _r is not None else bool(getattr(cfg, "ref_image", 0))


def ref_video_enabled(cfg, subject) -> bool:
    """生效的「视频支持参考图片」：功能级优先，配置兜底。"""
    _r = norm_ref_flag(getattr(subject, "dt_ref_video", ""))
    return bool(_r) if _r is not None else bool(getattr(cfg, "ref_video", 0))


def _model_override(subject, attr: str) -> str:
    """功能级模型覆盖值：'' = 跟随配置；MODEL_NONE = 明确不启用；其余 = 指定模型。"""
    return str(getattr(subject, attr, "") or "").strip()


def dt_client(cfg, data_dir, subject):
    """按 subject 的功能级覆盖构造 DrawThings 客户端；cfg 为空返回 None。"""
    if not cfg:
        return None
    return build_drawthings_client(
        cfg,
        Path(data_dir),
        model_image=_model_override(subject, "dt_model_image"),
        model_video=_model_override(subject, "dt_model_video"),
        ref_image=norm_ref_flag(getattr(subject, "dt_ref_image", "")),
        ref_video=norm_ref_flag(getattr(subject, "dt_ref_video", "")),
        max_steps_image=int(getattr(subject, "dt_max_steps_image", 0) or 0),
        max_steps_video=int(getattr(subject, "dt_max_steps_video", 0) or 0),
        max_side=int(getattr(subject, "dt_max_side", 0) or 0),
        max_seconds=int(getattr(subject, "dt_max_seconds", 0) or 0),
    )


def caps(dt, cfg, subject) -> Capabilities:
    """由客户端 + 配置 + 作品解析生效能力（dt 为 None 时均不可生成）。"""
    return Capabilities(
        can_image=bool(dt and dt.supports_image()),
        can_video=bool(dt and dt.supports_video()),
        ref_image=ref_image_enabled(cfg, subject),
        ref_video=ref_video_enabled(cfg, subject),
    )


__all__ = ["Capabilities", "caps", "dt_client", "ref_image_enabled", "ref_video_enabled"]
