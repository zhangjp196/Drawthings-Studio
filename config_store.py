"""配置增删查（LLM 与 DrawThings 各自的配置）。

由 main.py 按请求传入 db 会话构造（ConfigStore(db)），不持有全局状态。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_

from db import SessionLocal
from models import LLMConfig, DrawThingConfig, AppSettings, Project

# 基础配置（单行 JSON）：新建创作的默认 LLM / DrawThings 配置
SETTINGS_DEFAULTS = {
    "default_llm_config_id": "",
    "default_dt_config_id": "",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConfigStore:
    def __init__(self, db):
        self.db = db

    # ---------------- LLM 配置 ----------------
    def create_llm(self, name, base_url, api_key, model,
                   supports_vision="yes", thinking="default",
                   thinking_param="auto") -> LLMConfig:
        cfg = LLMConfig(
            id=uuid.uuid4().hex[:12],
            name=name,
            base_url=base_url,
            api_key=api_key or "",
            model=model,
            supports_vision=(supports_vision or "yes").lower(),
            thinking=(thinking or "default").lower(),
            thinking_param=(thinking_param or "auto").lower(),
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(cfg)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def update_llm(self, config_id, name=None, base_url=None, api_key=None,
                   model=None, supports_vision=None, thinking=None,
                   thinking_param=None) -> LLMConfig | None:
        """编辑 LLM 配置：传 None 的字段保持不变（api_key 空串由调用方转 None=保留原值）。"""
        cfg = self.db.get(LLMConfig, config_id)
        if not cfg:
            return None
        if name is not None:
            cfg.name = name
        if base_url is not None:
            cfg.base_url = base_url
        if api_key is not None:
            cfg.api_key = api_key
        if model is not None:
            cfg.model = model
        if supports_vision is not None:
            cfg.supports_vision = supports_vision
        if thinking is not None:
            cfg.thinking = thinking
        if thinking_param is not None:
            cfg.thinking_param = thinking_param
        cfg.updated_at = _now()
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

    # ---------------- DrawThings 配置（HTTP 协议；个性化参数 0/空 = 跟随 app）----------------
    def create_drawthing(self, name, base_url, **gen) -> DrawThingConfig:
        """gen：width/height/max_frames 个性化参数。"""
        cfg = DrawThingConfig(
            id=uuid.uuid4().hex[:12],
            name=name,
            base_url=base_url,
            created_at=_now(),
            updated_at=_now(),
            **gen,
        )
        self.db.add(cfg)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def update_drawthing(self, config_id, name=None, base_url=None, **gen) -> DrawThingConfig | None:
        """编辑 DrawThings 配置：传 None 的字段保持不变；gen 同 create_drawthing。"""
        cfg = self.db.get(DrawThingConfig, config_id)
        if not cfg:
            return None
        if name is not None:
            cfg.name = name
        if base_url is not None:
            cfg.base_url = base_url
        for k, v in gen.items():
            if v is not None:
                setattr(cfg, k, v)
        cfg.updated_at = _now()
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

    # ---------------- 基础配置（单行 JSON：默认配置 / 默认生成参数） ----------------
    def get_settings(self) -> dict:
        row = self.db.get(AppSettings, 1)
        data = dict(SETTINGS_DEFAULTS)
        if row and isinstance(row.data, dict):
            for k in SETTINGS_DEFAULTS:
                if k in row.data:
                    data[k] = row.data[k]
        return data

    def update_settings(self, patch: dict) -> dict:
        """保存基础配置：仅接受白名单键，返回保存后的完整配置。"""
        row = self.db.get(AppSettings, 1)
        if not row:
            row = AppSettings(id=1, data=dict(SETTINGS_DEFAULTS))
            self.db.add(row)
        data = dict(SETTINGS_DEFAULTS)
        if isinstance(row.data, dict):
            for k in SETTINGS_DEFAULTS:
                if k in row.data:
                    data[k] = row.data[k]
        for k, v in patch.items():
            if k in SETTINGS_DEFAULTS:
                data[k] = v
        row.data = data
        self.db.commit()
        self.db.refresh(row)
        return self.get_settings()

    def clear_default_ref(self, config_type: str, config_id: str) -> None:
        """配置被删除时：若它是某个默认配置，清空对应引用（避免悬空 id）。"""
        key = "default_llm_config_id" if config_type == "llm" else "default_dt_config_id"
        if self.get_settings().get(key) == config_id:
            self.update_settings({key: ""})

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
