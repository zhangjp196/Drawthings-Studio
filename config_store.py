"""配置增删查（LLM 与 DrawThings 各自的配置）。

由 main.py 按请求传入 db 会话构造（ConfigStore(db)），不持有全局状态。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_

from db import SessionLocal
from models import LLMConfig, DrawThingConfig, Project


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConfigStore:
    def __init__(self, db):
        self.db = db

    # ---------------- LLM 配置 ----------------
    def create_llm(self, name, base_url, api_key, model,
                   supports_vision="yes") -> LLMConfig:
        cfg = LLMConfig(
            id=uuid.uuid4().hex[:12],
            name=name,
            base_url=base_url,
            api_key=api_key or "",
            model=model,
            supports_vision=(supports_vision or "yes").lower(),
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(cfg)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def list_llm(self) -> list[LLMConfig]:
        return self.db.query(LLMConfig).order_by(LLMConfig.created_at.desc()).all()

    def get_llm(self, config_id) -> LLMConfig | None:
        return self.db.get(LLMConfig, config_id)

    def delete_llm(self, config_id) -> bool:
        cfg = self.db.get(LLMConfig, config_id)
        if not cfg:
            return False
        self.db.delete(cfg)
        self.db.commit()
        return True

    # ---------------- DrawThings 配置 ----------------
    def create_drawthing(self, name, base_url, protocol="http",
                         model_name="", shared_secret="", media_type="image") -> DrawThingConfig:
        cfg = DrawThingConfig(
            id=uuid.uuid4().hex[:12],
            name=name,
            base_url=base_url,
            protocol=(protocol or "http").lower(),
            model_name=model_name or "",
            shared_secret=shared_secret or "",
            media_type=(media_type or "image").lower(),
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(cfg)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def list_drawthing(self) -> list[DrawThingConfig]:
        return self.db.query(DrawThingConfig).order_by(DrawThingConfig.created_at.desc()).all()

    def get_drawthing(self, config_id) -> DrawThingConfig | None:
        return self.db.get(DrawThingConfig, config_id)

    def delete_drawthing(self, config_id) -> bool:
        cfg = self.db.get(DrawThingConfig, config_id)
        if not cfg:
            return False
        self.db.delete(cfg)
        self.db.commit()
        return True

    # ---------------- 引用检查（删除前）----------------
    def is_referenced(self, config_id) -> bool:
        """某配置是否已被任一项目/微创作作品选用（避免删除后无法运行）。"""
        n = self.db.query(Project).filter(
            or_(Project.llm_config_id == config_id, Project.drawthings_config_id == config_id)
        ).count()
        if n:
            return True
        from models import MicroWork
        n = self.db.query(MicroWork).filter(
            or_(MicroWork.llm_config_id == config_id, MicroWork.drawthings_config_id == config_id)
        ).count()
        return n > 0
