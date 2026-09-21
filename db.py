"""数据库引擎与会话管理（SQLite + SQLAlchemy）。

数据存储：结构化数据（配置/项目/章节）走 SQLite + ORM，媒体文件存 data/media 目录。
"""
from pathlib import Path

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from config import data_dir

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(data_dir) / "app.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def uuid_hex12() -> str:
    """12 位十六进制 id（与项目/季/配置等一致）。"""
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 15},  # 多线程访问 + 锁等待 15s
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_conn, _record):
    """每个连接的 SQLite 性能设置：
    - journal_mode=WAL：读写不互斥（读多写少场景下并发大幅改善），持久生效
    - synchronous=NORMAL：WAL 下 fsync 次数显著减少（安全）
    - busy_timeout：锁竞争时等待而非立即报错
    - foreign_keys=ON：章节随项目删除等约束生效
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """FastAPI 依赖：为每个请求提供一个数据库会话，请求结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate():
    """SQLite 轻量迁移：给旧库各表补新列（ALTER TABLE ADD COLUMN）。"""
    from sqlalchemy import text
    new_cols = {
        "llm_configs": {
            "supports_vision": "VARCHAR(5) DEFAULT 'yes'",
            "thinking": "VARCHAR(10) DEFAULT 'default'",
            "thinking_param": "VARCHAR(20) DEFAULT 'auto'",
        },
        "drawthing_configs": {
            "max_side": "INTEGER DEFAULT 0",
            "max_frames": "INTEGER DEFAULT 0",
        },
        "chapters": {
            "width": "INTEGER DEFAULT 0",
            "height": "INTEGER DEFAULT 0",
            "summary": "TEXT DEFAULT ''",
            "season_id": "VARCHAR(12)",
        },
        "seasons": {
            "first_image": "VARCHAR(500) DEFAULT ''",
            "first_image_base": "VARCHAR(500) DEFAULT ''",
        },
        "projects": {
            "title": "VARCHAR(200) DEFAULT ''",
            "arc": "TEXT DEFAULT ''",
            "first_image": "VARCHAR(500) DEFAULT ''",
            "first_image_base": "VARCHAR(500) DEFAULT ''",
            "characters": "TEXT DEFAULT ''",
            "global_prompt": "TEXT DEFAULT ''",
            "res_width": "INTEGER DEFAULT 0",
            "res_height": "INTEGER DEFAULT 0",
            "count_mode": "VARCHAR(10) DEFAULT 'auto'",
            "count_min": "INTEGER DEFAULT 0",
            "count_max": "INTEGER DEFAULT 0",
        },
        "micro_messages": {
            "images": "TEXT",
            "created_at": "VARCHAR(40) DEFAULT ''",
            "duration": "REAL DEFAULT 0",
        },
    }
    with engine.connect() as conn:
        for table, cols in new_cols.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        # 移除已废弃的列（SQLite >= 3.35 支持 DROP COLUMN）
        deprecated = {
            "llm_configs": ("mode",),
            "projects": ("cover_as_first_ref",),  # 已移除：封面不再作为第 1 章参考
            "drawthing_configs": ("mode", "protocol", "shared_secret", "model_name", "media_type",
                                  "steps", "guidance_scale", "num_frames", "fps",
                                  "width", "height"),  # 历史字段已移除（分辨率改 max_side 最长边）
            "micro_works": ("media_type",),  # 产出类型改由 app 当前模型自动判断
        }
        for table, cols in deprecated.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col in cols:
                if col in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
                    except Exception:
                        pass  # 旧版本 SQLite 删不了列：保留该列但不再使用
        # 微创作三级结构迁移：旧 micro_sessions（配置随会话）→ micro_works（作品）；
        # 重建 micro_sessions（挂 micro_id），旧会话整体变成作品的「默认会话」，历史消息保留；
        # micro_messages 需重建（SQLite 改名会连带把 FK 指向 micro_works）。
        table_names = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))}
        if "micro_sessions" in table_names:
            sess_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(micro_sessions)"))}
            if "micro_id" not in sess_cols:
                if "micro_works" in table_names:
                    # create_all 可能已建出空的 micro_works：仅当无数据时可删掉让位
                    if conn.execute(text("SELECT COUNT(*) FROM micro_works")).scalar():
                        raise RuntimeError("micro_works 已有数据，无法执行微创作结构迁移")
                    conn.execute(text("DROP TABLE micro_works"))
                import models  # noqa: F401
                conn.execute(text("CREATE TABLE _micro_messages_backup AS SELECT * FROM micro_messages"))
                conn.execute(text("DROP TABLE micro_messages"))
                conn.execute(text("ALTER TABLE micro_sessions RENAME TO micro_works"))
                conn.commit()
                models.MicroSession.__table__.create(engine, checkfirst=True)
                models.MicroMessage.__table__.create(engine, checkfirst=True)
                # 先为每个作品建「默认会话」（id 与作品相同，历史消息自动挂上），再恢复消息
                conn.execute(text(
                    "INSERT INTO micro_sessions (id, micro_id, title, created_at, updated_at) "
                    "SELECT id, id, '默认会话', created_at, updated_at FROM micro_works"))
                # 显式列名恢复：备份表可能缺少新列（如 images）；index 是保留字需引号
                conn.execute(text(
                    'INSERT INTO micro_messages (id, session_id, "index", role, content, media_url, prompt) '
                    'SELECT id, session_id, "index", role, content, media_url, prompt FROM _micro_messages_backup'))
                conn.execute(text("DROP TABLE _micro_messages_backup"))
        conn.commit()


def _indexes():
    """常用查询的索引（幂等）。"""
    from sqlalchemy import text
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_chapters_project ON chapters(project_id)",
        'CREATE INDEX IF NOT EXISTS idx_chapters_project_index ON chapters(project_id, "index")',
        "CREATE INDEX IF NOT EXISTS idx_chapters_season ON chapters(season_id)",
        'CREATE INDEX IF NOT EXISTS idx_chapters_season_index ON chapters(season_id, "index")',
        "CREATE INDEX IF NOT EXISTS idx_seasons_project ON seasons(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)",
        "CREATE INDEX IF NOT EXISTS idx_projects_kind ON projects(kind)",
        "CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_micro_sessions_micro ON micro_sessions(micro_id)",
        'CREATE INDEX IF NOT EXISTS idx_micro_messages_session ON micro_messages(session_id, "index")',
    ]
    with engine.connect() as conn:
        for s in stmts:
            conn.execute(text(s))
        conn.commit()


def _migrate_seasons():
    """多季迁移：旧项目（有章节但无季）自动建「第 1 季」并把其全部章节归入。幂等。"""
    from models import Project, Season
    db = SessionLocal()
    try:
        for p in db.query(Project).all():
            seasons = db.query(Season).filter(Season.project_id == p.id).all()
            if not seasons:
                # 仅在确有章节时才建第 1 季（空项目不必预建）
                from models import Chapter
                has_ch = db.query(Chapter).filter(Chapter.project_id == p.id).count() > 0
                if not has_ch:
                    continue
                s1 = Season(id=uuid_hex12(), project_id=p.id, number=1, title="",
                            created_at=_now_iso(), updated_at=_now_iso())
                db.add(s1)
                db.flush()
                for ch in db.query(Chapter).filter(Chapter.project_id == p.id).all():
                    ch.season_id = s1.id
        db.commit()
    finally:
        db.close()


def init_db():
    """建表 + 迁移 + 索引。需先 import models 以注册所有表到 metadata。"""
    import models  # noqa: F401  确保模型注册
    Base.metadata.create_all(engine)
    _migrate()
    _migrate_seasons()
    _indexes()
