"""数据库引擎与会话管理（SQLite + SQLAlchemy）。

数据存储：结构化数据（配置/项目/章节）走 SQLite + ORM，媒体文件存 data/media 目录。
"""

import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

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
        f"或改用一个可写目录（如 DATA_DIR=~/drawthings-data）。"
    ) from e


def uuid_hex12() -> str:
    """12 位十六进制 id（与项目/季/配置等一致）。"""
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


BACKUP_KEEP = 3  # 保留最近几份备份
BACKUP_DIRNAME = "backups"

# 已废弃、启动时会被 DROP COLUMN 删掉的列（不可逆 → 触发迁移前备份）
_DEPRECATED_COLUMNS = {
    "llm_configs": ("mode",),
    "projects": (
        "cover_as_first_ref",  # 已移除：封面不再作为第 1 章参考
        "characters",  # 已移除：角色只挂在季上（各季 characters）
        "arc",  # 已移除：整体故事大纲并入「生成本季大纲」（总纲不再单独存）
        "scope",  # 已移除：风格并入 global_prompt（单一「全局要求」字段）
        "count_mode",
        "count_min",
        "count_max",  # 已移除：章节数量按季存于 Season
        "dt_max_steps",
    ),  # 已拆分：图像/视频分开（dt_max_steps_image / _video）
    "drawthing_configs": (
        "mode",
        "protocol",
        "shared_secret",
        "model_name",
        "media_type",
        "transport",  # 已移除：只保留 gRPC
        "model",
        "preset",
        "preset_image",
        "preset_video",  # 预设改为按模型名自动推断
        "max_frames",  # 已改为 max_seconds（秒）
        "steps",
        "guidance_scale",
        "num_frames",
        "fps",
        "width",
        "height",  # 历史字段已移除（分辨率改功能级 max_side 最长边）
        "max_side",
        "max_seconds",
    ),  # 已迁到功能级（Project.dt_max_side / dt_max_seconds）
    "seasons": ("count_mode",),  # 已移除：只剩范围模式（count_min~count_max），无需模式字段
    "chapters": ("ref_path",),  # 已移除：参考图路径从未落库（生成时用调用方入参，无需持久化）
    "micro_works": (
        "media_type",  # 产出类型改由 app 当前模型自动判断
        "dt_max_steps",
    ),  # 已拆分：图像/视频分开（dt_max_steps_image / _video）
}


def _db_wal_checkpoint() -> None:
    """把 WAL 内容合并回主库文件，否则复制出来的 .bak 缺最近的事务。"""
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def _backup_db(reason: str) -> Path | None:
    """迁移前把 app.db 复制到 data/backups/app-<时间戳>.db，保留最近 BACKUP_KEEP 份。

    app.db 是用户唯一的创作记录（项目 / 剧本 / 评分 / 对话历史），而迁移里有
    DROP COLUMN、RENAME TABLE、DROP TABLE 这类**不可逆**操作，且历史上真出过
    中途崩溃留下半张表的事故（见 _micro_messages_backup 残留）。这里先留一份可回滚的快照。
    返回备份路径；无需备份（如新建空库）或失败返回 None。
    """
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return None  # 全新库：没什么可丢的
    try:
        bdir = DB_PATH.parent / BACKUP_DIRNAME
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = bdir / f"app-{stamp}.db"
        n = 0
        while dest.exists():  # 同一秒内多次启动：加序号防覆盖
            n += 1
            dest = bdir / f"app-{stamp}-{n}.db"
        _db_wal_checkpoint()
        shutil.copy2(DB_PATH, dest)
        # 轮转：只留最近 BACKUP_KEEP 份
        olds = sorted(bdir.glob("app-*.db"), key=lambda p: p.name, reverse=True)
        for old in olds[BACKUP_KEEP:]:
            try:
                old.unlink()
            except OSError:
                pass
        logging.getLogger("drawthings").info("迁移前已备份数据库：%s（%s）", dest.name, reason)
        return dest
    except Exception as e:
        logging.getLogger("drawthings").error("迁移前备份数据库失败（继续迁移）：%s", e)
        return None


def _migrate_micro_structure() -> None:
    """微创作三级结构迁移（独立 sqlite3 连接，因为需要两条 PRAGMA + 单事务原子性）。

    旧结构：micro_sessions（配置随会话）→ micro_works（作品）；
    重建 micro_sessions（挂 micro_id），旧会话整体变成该作品的「默认会话」，历史消息保留。

    为什么必须用裸 sqlite3 + 两条 PRAGMA（SQLite 官方的「改表结构」配方）：
    - `foreign_keys=OFF`：重建期间 micro_works / micro_sessions 会短暂不存在，
      而 assets / generation_jobs 持有指向它们的外键；开着外键 DROP 会直接报错
      （实测："no such table: main.micro_works"）。PRAGMA 在事务内是 no-op，
      所以必须在 BEGIN 之前设置。
    - `legacy_alter_table=ON`：RENAME 时**不要**自动改写其他表里指向本表的外键。
      否则 assets / generation_jobs 的 micro_sessions 外键会被悄悄改写成指向 micro_works
      （错误的表），而它们本应继续指向重建后的 micro_sessions。

    崩溃安全：SQLite 的 DDL 是事务性的，全程单连接单事务，中途任何异常都整体回滚，
    数据库停留在迁移前状态。历史上这里中途 commit 过，崩一次就留下半张
    _micro_messages_backup 表 + 丢失消息（用户的库里至今还留着那张残表）。
    即便真出事，_backup_db() 留的 data/backups/app-*.db 也是回滚点。
    """
    import sqlite3

    import models  # noqa: F401  取 MicroSession / MicroMessage 的表定义

    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)  # 手动 BEGIN/COMMIT
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "micro_sessions" not in tables:
            return
        sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(micro_sessions)")}
        if "micro_id" in sess_cols:
            return  # 已是新结构

        def _ddl(table) -> str:
            from sqlalchemy.schema import CreateTable

            return str(CreateTable(table).compile(dialect=engine.dialect))

        conn.execute("BEGIN")
        try:
            if "micro_works" in tables:
                # create_all 可能已建出空的 micro_works：仅当无数据时可删掉让位
                if conn.execute("SELECT COUNT(*) FROM micro_works").fetchone()[0]:
                    raise RuntimeError("micro_works 已有数据，无法执行微创作结构迁移")
                conn.execute("DROP TABLE micro_works")
            conn.execute("CREATE TABLE _micro_messages_backup AS SELECT * FROM micro_messages")
            conn.execute("DROP TABLE micro_messages")
            conn.execute("ALTER TABLE micro_sessions RENAME TO micro_works")
            conn.execute(_ddl(models.MicroSession.__table__))
            conn.execute(_ddl(models.MicroMessage.__table__))
            # 为每个作品建「默认会话」（id 与作品相同，历史消息自动挂上）
            conn.execute(
                "INSERT INTO micro_sessions (id, micro_id, title, created_at, updated_at) "
                "SELECT id, id, '默认会话', created_at, updated_at FROM micro_works"
            )
            # 显式列名恢复：备份表可能缺少新列（如 images）；index 是保留字需引号
            conn.execute(
                'INSERT INTO micro_messages (id, session_id, "index", role, content, media_url, prompt) '
                'SELECT id, session_id, "index", role, content, media_url, prompt '
                "FROM _micro_messages_backup"
            )
            conn.execute("DROP TABLE _micro_messages_backup")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")  # 回到迁移前状态，不留半成品
            raise
    finally:
        conn.close()


def _needs_migration() -> bool:
    """判断本次启动是否会做**结构性**迁移（不可逆的那些）。

    结构性 = 删列（DROP COLUMN）、微创作表改名重建。纯 ADD COLUMN / 一次性数据修正
    不算——那些都可逆或不丢数据，不值得每次启动都留一份备份。
    """
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            # ① 待删的废弃列
            for table, cols in _DEPRECATED_COLUMNS.items():
                if table not in tables:
                    continue
                existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
                if existing & set(cols):
                    return True
            # ② 微创作表改名重建
            if "micro_sessions" in tables:
                sess_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(micro_sessions)"))}
                if "micro_id" not in sess_cols:
                    return True
    except Exception:
        return False
    return False


def _backup_before_migration() -> None:
    if _needs_migration():
        _backup_db("检测到结构性迁移")


def _migrate():
    """SQLite 轻量迁移：给旧库各表补新列（ALTER TABLE ADD COLUMN）。"""
    from sqlalchemy import text

    # 一次性标记表：用于记录已执行的「数据修正」类迁移（避免每次启动重复执行）
    with engine.connect() as conn:
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS app_flags (k VARCHAR(64) PRIMARY KEY, v VARCHAR(64) DEFAULT '')")
        )
        conn.commit()
    new_cols = {
        "llm_configs": {
            "supports_vision": "VARCHAR(5) DEFAULT 'yes'",
            "thinking": "VARCHAR(10) DEFAULT 'default'",
            "thinking_param": "VARCHAR(20) DEFAULT 'auto'",
        },
        "drawthing_configs": {
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
            "dt_max_steps_image": "INTEGER DEFAULT 0",
            "dt_max_steps_video": "INTEGER DEFAULT 0",
            "dt_max_side": "INTEGER DEFAULT 0",
            "dt_max_seconds": "INTEGER DEFAULT 0",
        },
        "micro_works": {
            "dt_model_image": "VARCHAR(200) DEFAULT ''",
            "dt_model_video": "VARCHAR(200) DEFAULT ''",
            "dt_ref_image": "VARCHAR(1) DEFAULT ''",
            "dt_ref_video": "VARCHAR(1) DEFAULT ''",
            "dt_max_steps_image": "INTEGER DEFAULT 0",
            "dt_max_steps_video": "INTEGER DEFAULT 0",
            "dt_max_side": "INTEGER DEFAULT 0",
            "dt_max_seconds": "INTEGER DEFAULT 0",
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
                conn.execute(
                    text(
                        "UPDATE drawthing_configs SET model_image = model "
                        "WHERE (model_image IS NULL OR model_image = '') AND model IS NOT NULL AND model != ''"
                    )
                )
            except Exception:
                pass
        if "preset" in cols and "preset_video" in cols:
            try:
                conn.execute(
                    text(
                        "UPDATE drawthing_configs SET preset_video = preset "
                        "WHERE (preset_video IS NULL OR preset_video = '') AND preset IS NOT NULL AND preset != ''"
                    )
                )
            except Exception:
                pass
        # 一次性修正：项目层核心角色已移除（角色只挂在季上）——旧项目的核心角色并入**第 1 季**，
        # 仅当该季还没有角色时才写入（不覆盖已有季角色），避免历史作品的人物设定直接丢失。
        # 必须先于下面的删列步骤：projects.characters 一旦被 DROP 就再也读不到了。
        try:
            done = conn.execute(text("SELECT v FROM app_flags WHERE k='proj_chars_to_s1'")).scalar()
            if not done:
                cols = {row[1] for row in conn.execute(text("PRAGMA table_info(projects)"))}
                rows = []
                if "characters" in cols:
                    rows = conn.execute(
                        text("SELECT id, characters FROM projects WHERE COALESCE(characters, '') != ''")
                    ).fetchall()
                for pid, chars in rows:
                    s1 = conn.execute(
                        text(
                            "SELECT id, COALESCE(characters, '') FROM seasons "
                            "WHERE project_id = :pid AND number = 1 LIMIT 1"
                        ),
                        {"pid": pid},
                    ).fetchone()
                    if s1 is not None and not s1[1]:
                        conn.execute(
                            text("UPDATE seasons SET characters = :c WHERE id = :sid"),
                            {"c": chars, "sid": s1[0]},
                        )
                conn.execute(text("INSERT OR REPLACE INTO app_flags (k, v) VALUES ('proj_chars_to_s1','1')"))
        except Exception:
            pass  # 旧库尚无相关表：忽略
        # 最大分辨率 / 最大秒数从 drawthing_configs 迁到功能级（projects / micro_works）：
        # 先按各行**实际关联**的 DrawThings 配置播种，再由下面的 deprecated 删掉旧列 —— 顺序有依赖。
        try:
            row = conn.execute(text("SELECT v FROM app_flags WHERE k = 'dt_limits_to_subject'")).fetchone()
            if not row:
                dt_cols = {c[1] for c in conn.execute(text("PRAGMA table_info(drawthing_configs)"))}
                if "max_side" in dt_cols and "max_seconds" in dt_cols:
                    for tbl in ("projects", "micro_works"):
                        sub_cols = {c[1] for c in conn.execute(text(f"PRAGMA table_info({tbl})"))}
                        if not {"dt_max_side", "dt_max_seconds"} <= sub_cols:
                            continue
                        conn.execute(
                            text(
                                f"UPDATE {tbl} SET dt_max_side = COALESCE((SELECT d.max_side FROM"
                                f" drawthing_configs d WHERE d.id = {tbl}.drawthings_config_id), 0),"
                                f" dt_max_seconds = COALESCE((SELECT d.max_seconds FROM"
                                f" drawthing_configs d WHERE d.id = {tbl}.drawthings_config_id), 0)"
                                f" WHERE drawthings_config_id IS NOT NULL"
                            )
                        )
                conn.execute(
                    text("INSERT OR REPLACE INTO app_flags (k, v) VALUES ('dt_limits_to_subject','1')")
                )
                conn.commit()
        except Exception:
            pass  # 旧库尚无相关表：忽略
        # 移除已废弃的列（SQLite >= 3.35 支持 DROP COLUMN）
        for table, cols in _DEPRECATED_COLUMNS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col in cols:
                if col in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
                    except Exception:
                        pass  # 旧版本 SQLite 删不了列：保留该列但不再使用
        # 微创作三级结构迁移见 _migrate_micro_structure()：它开自己的 sqlite3 连接
        # （需要 PRAGMA foreign_keys=OFF / legacy_alter_table=ON 且整体单事务），
        # 故必须在本函数的事务提交之后、作为独立步骤调用。
        # 一次性修正：已移除「完结/锁定」，旧项目停在 status=done 会被永久锁死无法编辑 —— 归一到 chaptered。
        try:
            done = conn.execute(text("SELECT v FROM app_flags WHERE k='done_status_unlock'")).scalar()
            if not done:
                conn.execute(text("UPDATE projects SET status='chaptered' WHERE status='done'"))
                conn.execute(
                    text("INSERT OR REPLACE INTO app_flags (k, v) VALUES ('done_status_unlock','1')")
                )
        except Exception:
            pass  # 旧库尚无相关表：忽略
        # 一次性修正：整体故事大纲已并入「生成本季大纲」，status 不再有 arced（总纲已定）态。
        # 旧项目停在该态会被并入 chaptered（章节已定）——语义上它至少已规划过，向后兼容即可。
        try:
            done = conn.execute(text("SELECT v FROM app_flags WHERE k='arced_status_merge'")).scalar()
            if not done:
                conn.execute(text("UPDATE projects SET status='chaptered' WHERE status='arced'"))
                conn.execute(
                    text("INSERT OR REPLACE INTO app_flags (k, v) VALUES ('arced_status_merge','1')")
                )
        except Exception:
            pass  # 旧库尚无相关表：忽略
        # 一次性修正：短剧曾短暂支持「规划/剧本自动写时长」，会把 seconds(1–8) 传给 Draw Things
        # 导致 LTX 等模型因非原生帧数异常。这里把短剧片段的 seconds 归零一次（恢复「0=跟随预设上限」），
        # 用户之后仍可在界面手动设置时长。仅执行一次（app_flags 标记）。
        try:
            done = conn.execute(text("SELECT v FROM app_flags WHERE k='drama_seconds_reset'")).scalar()
            if not done:
                conn.execute(
                    text(
                        "UPDATE chapters SET seconds=0 WHERE seconds>0 AND season_id IN ("
                        "  SELECT s.id FROM seasons s JOIN projects p ON p.id=s.project_id WHERE p.kind='drama')"
                    )
                )
                conn.execute(
                    text("INSERT OR REPLACE INTO app_flags (k, v) VALUES ('drama_seconds_reset','1')")
                )
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
                s1 = Season(
                    id=uuid_hex12(),
                    project_id=p.id,
                    number=1,
                    title="",
                    created_at=_now_iso(),
                    updated_at=_now_iso(),
                )
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
    _backup_before_migration()
    _migrate()
    _migrate_micro_structure()  # 独立连接（见函数 docstring），必须在 _migrate 提交之后
    _migrate_seasons()
    _mark_interrupted_streams()
    _indexes()
    _purge_tmp_files()


def _purge_tmp_files():
    """清空 data/tmp（视频抽帧等中间产物）。失败不影响启动。"""
    try:
        from services.media_files import purge_tmp

        freed = purge_tmp()
        if freed:
            logging.getLogger("drawthings").info("已清理临时目录 data/tmp：释放 %.1f MB", freed / 1024 / 1024)
    except Exception as e:
        logging.getLogger("drawthings").warning("清理 data/tmp 失败（可忽略）：%s", e)
