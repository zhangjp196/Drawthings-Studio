"""数据维护接口（类型无关的基础设施，供 /configs 页「数据维护」区使用）。

- 孤儿媒体扫描 / 清理（data/media 下不被任何记录引用的文件，含历史遗留）；
- 手动数据库备份（保存到 data/backups/app-<时间戳>.db，与迁移前自动备份同目录）；
- 清空导出目录（data/exports：历史 ZIP/PDF 下载产物，从不自动清理）；
- 清空临时目录（data/tmp：抽帧等中间产物，正常情况下启动已清，这里手动兜底）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db
from i18n import L
from services.api_common import _lang

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.post("/scan-orphans")
def scan_orphans(request: Request, db: Session = Depends(get_db)):
    """扫描孤儿媒体（只报告不删）：返回各文件与统计，供前端预览。"""
    from services.media_files import find_orphan_media

    orphans, count, total = find_orphan_media(db)
    freed = sum(size for _, size in orphans)
    return {
        "orphan_count": len(orphans),
        "orphan_bytes": freed,
        "media_count": count,
        "media_bytes": total,
        "orphans": [{"name": n, "bytes": s} for n, s in orphans[:500]],  # 预览只给前 500 个
    }


@router.post("/purge-orphans")
def purge_orphans(request: Request, db: Session = Depends(get_db)):
    """删除孤儿媒体（不可逆，前端需先确认）。返回清理统计。"""
    lang = _lang(request)
    from services.media_files import purge_orphan_media

    report = purge_orphan_media(db, dry_run=False)
    return {
        "deleted": report["orphan_count"],
        "freed_bytes": report["freed_bytes"],
        "message": L(
            lang,
            f"已删除 {report['orphan_count']} 个孤儿文件，释放 {report['freed_bytes'] / 1024 / 1024:.1f} MB",
            f"Removed {report['orphan_count']} orphan files, freed {report['freed_bytes'] / 1024 / 1024:.1f} MB",
        ),
    }


@router.post("/backup-db")
def backup_db(request: Request, db: Session = Depends(get_db)):
    """手动备份数据库到 data/backups/app-<时间戳>.db（与迁移前自动备份同一轮转池）。"""
    lang = _lang(request)
    from db import _backup_db

    try:
        dest = _backup_db("手动备份")
    except Exception as e:
        raise HTTPException(status_code=500, detail=L(lang, f"备份失败：{e}", f"Backup failed: {e}"))
    if dest is None:
        # 数据库为空/不存在：仍应能建一份（此时纯复制空表也无意义，给个明确提示）
        raise HTTPException(
            status_code=400, detail=L(lang, "数据库为空，无需备份", "Database is empty, nothing to back up")
        )
    return {"ok": True, "path": str(dest), "name": dest.name}


@router.post("/clean-exports")
def clean_exports(request: Request, db: Session = Depends(get_db)):
    """清空 data/exports（历史 ZIP / PDF 导出产物，均已交付给用户，可安全删除）。"""
    lang = _lang(request)
    from pathlib import Path

    from config import data_dir

    d = Path(data_dir) / "exports"
    freed = 0
    count = 0
    if d.is_dir():
        for p in d.iterdir():
            try:
                if p.is_file() and not p.is_symlink():
                    freed += p.stat().st_size
                    p.unlink()
                    count += 1
            except OSError:
                pass
    return {
        "ok": True,
        "deleted": count,
        "freed_bytes": freed,
        "message": L(
            lang,
            f"已清理 {count} 个导出文件，释放 {freed / 1024 / 1024:.1f} MB",
            f"Cleaned {count} exports, freed {freed / 1024 / 1024:.1f} MB",
        ),
    }


@router.post("/clean-tmp")
def clean_tmp(request: Request, db: Session = Depends(get_db)):
    """手动清空 data/tmp（中间产物）。"""
    lang = _lang(request)
    from services.media_files import purge_tmp

    freed = purge_tmp()
    return {
        "ok": True,
        "freed_bytes": freed,
        "message": L(
            lang,
            f"已清理临时目录（释放 {freed / 1024 / 1024:.1f} MB）",
            f"Cleaned tmp dir (freed {freed / 1024 / 1024:.1f} MB)",
        ),
    }
