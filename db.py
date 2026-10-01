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
try:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
except OSError as e:
    raise RuntimeError(
        f"无法创建数据目录 {DB_PATH.parent}（{e}）。请检查 DATA_DIR 是否有写权限，"
        f"或改用一个可写目录（如 DATA_DIR=~/drawthings-data）。") from e


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
    """FastAPI 依赖：为每个请求提供一个数据库会话；处理中途异常时回滚，结束后关闭。

    用 BaseException 兜底（含 asyncio.CancelledError / KeyboardInterrupt）：流式响应被客户端
    中断（取消）时也回滚未提交事务，避免残留半开事务影响后续请求。"""
    db = SessionLocal()
    try:
        yield db
    except BaseException:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def _migrate():
    """SQLite 轻量迁移：给旧库各表补新列（ALTER TABLE ADD COLUMN）。"""
    from sqlalchemy import text
    # 一次性标记表：用于记录已执行的「数据修正」类迁移（避免每次启动重复执行）
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS app_flags (k VARCHAR(64) PRIMARY KEY, v VARCHAR(64) DEFAULT '')"))
        conn.commit()
    new_cols = {
        "llm_configs": {
            "supports_vision": "VARCHAR(5) DEFAULT 'yes'",
            "thinking": "VARCHAR(10) DEFAULT 'default'",
            "thinking_param": "VARCHAR(20) DEFAULT 'auto'",
        },
        "drawthing_configs": {
            "max_side": "INTEGER DEFAULT 0",
            "max_seconds": "INTEGER DEFAULT 10",
            "model_image": "VARCHAR(200) DEFAULT ''",
            "model_video": "VARCHAR(200) DEFAULT ''",
            "ref_image": "INTEGER DEFAULT 0",
            "ref_video": "INTEGER DEFAULT 0",
        },
        "chapters": {
            "width": "INTEGER DEFAULT 0",
            "height": "INTEGER DEFAULT 0",
            "seconds": "INTEGER DEFAULT 0",
            "summary": "TEXT DEFAULT ''",
            "season_id": "VARCHAR(12)",
            "score": "INTEGER DEFAULT 0",
            "score_note": "VARCHAR(300) DEFAULT ''",
        },
        "seasons": {
            "first_image": "VARCHAR(500) DEFAULT ''",
            "first_image_base": "VARCHAR(500) DEFAULT ''",
            "cover_as_first_ref": "INTEGER DEFAULT 0",
        },
        "projects": {
            "title": "VARCHAR(200) DEFAULT ''",
            "first_image": "VARCHAR(500) DEFAULT ''",
            "first_image_base": "VARCHAR(500) DEFAULT ''",
            "characters": "TEXT DEFAULT ''",
            "global_prompt": "TEXT DEFAULT ''",
            "res_width": "INTEGER DEFAULT 0",
            "res_height": "INTEGER DEFAULT 0",
            "auto_score": "INTEGER DEFAULT 0",
            "score_min": "INTEGER DEFAULT 60",
            "auto_redo": "INTEGER DEFAULT 0",
            "stop_on_low": "INTEGER DEFAULT 0",
            "dt_model_image": "VARCHAR(200) DEFAULT ''",
            "dt_model_video": "VARCHAR(200) DEFAULT ''",
            "dt_ref_image": "VARCHAR(1) DEFAULT ''",
            "dt_ref_video": "VARCHAR(1) DEFAULT ''",
        },
        "micro_works": {
            "dt_model_image": "VARCHAR(200) DEFAULT ''",
            "dt_model_video": "VARCHAR(200) DEFAULT ''",
            "dt_ref_image": "VARCHAR(1) DEFAULT ''",
            "dt_ref_video": "VARCHAR(1) DEFAULT ''",
            "score_mode": "VARCHAR(10) DEFAULT 'image'",
            "auto_score": "INTEGER DEFAULT 0",
        },
        "micro_messages": {
            "images": "TEXT",
            "created_at": "VARCHAR(40) DEFAULT ''",
            "duration": "REAL DEFAULT 0",
            "parts": "TEXT",
            "status": "VARCHAR(12) DEFAULT 'done'",
        },
    }
    with engine.connect() as conn:
        for table, cols in new_cols.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        # DrawThings 单模型/单预设 → 图像模型 + 视频模型（旧值迁移；旧预设归到视频预设）
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(drawthing_configs)"))}
        if "model" in cols and "model_image" in cols:
            try:
                conn.execute(text(
                    "UPDATE drawthing_configs SET model_image = model "
                    "WHERE (model_image IS NULL OR model_image = '') AND model IS NOT NULL AND model != ''"))
            except Exception:
                pass
        if "preset" in cols and "preset_video" in cols:
            try:
                conn.execute(text(
                    "UPDATE drawthing_configs SET preset_video = preset "
                    "WHERE (preset_video IS NULL OR preset_video = '') AND preset IS NOT NULL AND preset != ''"))
            except Exception:
                pass
        # 移除已废弃的列（SQLite >= 3.35 支持 DROP COLUMN）
        deprecated = {
            "llm_configs": ("mode",),
            "projects": ("cover_as_first_ref",  # 已移除：封面不再作为第 1 章参考
                         "arc",  # 已移除：整体故事大纲并入「生成本季大纲」（总纲不再单独存）
                         "scope",  # 已移除：风格并入 global_prompt（单一「全局要求」字段）
                         "count_mode", "count_min", "count_max"),  # 已移除：章节数量按季存于 Season
            "drawthing_configs": ("mode", "protocol", "shared_secret", "model_name", "media_type",
                                  "transport",  # 已移除：只保留 gRPC
                                  "model", "preset", "preset_image", "preset_video",  # 预设改为按模型名自动推断
                                  "max_frames",  # 已改为 max_seconds（秒）
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
        # 一次性修正：整体故事大纲已并入「生成本季大纲」，status 不再有 arced（总纲已定）态。
        # 旧项目停在该态会被并入 chaptered（章节已定）——语义上它至少已规划过，向后兼容即可。
        try:
            done = conn.execute(text("SELECT v FROM app_flags WHERE k='arced_status_merge'")).scalar()
            if not done:
                conn.execute(text("UPDATE projects SET status='chaptered' WHERE status='arced'"))
                conn.execute(text(
                    "INSERT OR REPLACE INTO app_flags (k, v) VALUES ('arced_status_merge','1')"))
        except Exception:
            pass  # 旧库尚无相关表：忽略
        # 一次性修正：短剧曾短暂支持「规划/剧本自动写时长」，会把 seconds(1–8) 传给 Draw Things
        # 导致 LTX 等模型因非原生帧数异常。这里把短剧片段的 seconds 归零一次（恢复「0=跟随预设上限」），
        # 用户之后仍可在界面手动设置时长。仅执行一次（app_flags 标记）。
        try:
            done = conn.execute(text("SELECT v FROM app_flags WHERE k='drama_seconds_reset'")).scalar()
            if not done:
                conn.execute(text(
                    "UPDATE chapters SET seconds=0 WHERE seconds>0 AND season_id IN ("
                    "  SELECT s.id FROM seasons s JOIN projects p ON p.id=s.project_id WHERE p.kind='drama')"))
                conn.execute(text(
                    "INSERT OR REPLACE INTO app_flags (k, v) VALUES ('drama_seconds_reset','1')"))
        except Exception:
            pass  # 旧库尚无相关表：忽略
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
        "CREATE INDEX IF NOT EXISTS idx_assets_micro ON assets(micro_id)",
        "CREATE INDEX IF NOT EXISTS idx_assets_session ON assets(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_micro ON generation_jobs(micro_id)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON generation_jobs(status)",
        "CREATE INDEX IF NOT EXISTS idx_project_jobs_project ON project_jobs(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_project_jobs_status ON project_jobs(status)",
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


def _mark_interrupted_streams():
    """启动清理：上次进程异常退出时残留的「streaming」助手消息标记为 interrupted
    （可恢复流：断连/崩溃留下部分输出，下次进入会话可见「可能未完成」）；
    残留的 running 生成任务同样标记 interrupted。"""
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("UPDATE micro_messages SET status='interrupted' WHERE status='streaming'"))
        try:
            conn.execute(text("UPDATE generation_jobs SET status='interrupted' WHERE status='running'"))
        except Exception:
            pass  # 表尚未创建（首次启动）
        try:
            conn.execute(text("UPDATE project_jobs SET status='interrupted' WHERE status='running'"))
        except Exception:
            pass
        conn.commit()


def init_db():
    """建表 + 迁移 + 索引。需先 import models 以注册所有表到 metadata。"""
    import models  # noqa: F401  确保模型注册
    Base.metadata.create_all(engine)
    _migrate()
    _migrate_seasons()
    _mark_interrupted_streams()
    _indexes()
