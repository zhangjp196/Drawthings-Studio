#!/usr/bin/env python
"""清理 data/media 下的孤儿文件（不被任何数据库记录引用的媒体）。

孤儿文件的来源（历史上都会漏）：
  - 章节重新生成 / 低分自动重做时覆盖了 media_path，旧文件没删；
  - 生成中途失败（文件已落盘、后续步骤抛错），该文件从未被引用；
  - 微创作消息删除后的残留。

用法（默认只报告，不删）：
    python tools/clean_media.py                  # 预览
    python tools/clean_media.py --delete         # 实际删除（带确认）
    python tools/clean_media.py --delete --yes   # 跳过确认

安全性：只删除 data/media 目录下的普通文件，且必须既不在数据库任何记录里、
又确实位于 MEDIA_DIR 内；子目录、符号链接、非媒体扩展名一律跳过。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SessionLocal, init_db  # noqa: E402
from services.media_files import MEDIA_DIR, find_orphan_media  # noqa: E402


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 data/media 下的孤儿媒体文件")
    ap.add_argument("--delete", action="store_true", help="实际删除（默认只预览）")
    ap.add_argument("--yes", action="store_true", help="不再询问，直接删除")
    args = ap.parse_args()

    init_db()  # 幂等：确保表结构齐全后再统计引用
    db = SessionLocal()
    try:
        orphans, count, total = find_orphan_media(db)
    finally:
        db.close()

    orphan_bytes = sum(size for _, size in orphans)
    print(f"媒体目录：{MEDIA_DIR}")
    print(f"文件总数：{count} 个 / {_mb(total)}")
    if not orphans:
        print("孤儿文件：0 个")
        print("\n✅ 没有孤儿文件，无需清理。")
        return 0

    pct = 100 * orphan_bytes / total if total else 0
    print(f"孤儿文件：{len(orphans)} 个 / {_mb(orphan_bytes)}（占 {pct:.0f}%）")

    if not args.delete:
        print("\n这是预览，未删除任何文件。确认无误后加 --delete 执行。")
        return 0

    if not args.yes:
        print(f"\n即将删除 {len(orphans)} 个文件（{_mb(orphan_bytes)}）。确认？[y/N] ", end="")
        if input().strip().lower() not in ("y", "yes"):
            print("已取消。")
            return 1

    freed = 0
    failed = 0
    for name, size in orphans:
        try:
            (MEDIA_DIR / name).unlink()
            freed += size
        except OSError:
            failed += 1

    print(
        f"✅ 已删除 {len(orphans) - failed} 个文件，释放 {_mb(freed)}"
        + (f"（{failed} 个删除失败）" if failed else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
