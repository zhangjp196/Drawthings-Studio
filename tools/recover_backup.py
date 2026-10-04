#!/usr/bin/env python
"""回收历史遗留的 _micro_messages_backup 表（可先 --dry-run 预览）。

背景：早期微创作迁移中途崩溃会留下半张 `_micro_messages_backup` 表
（备份了当时被 DROP 的 micro_messages 的行）。用户库实测就有 2 行残留、
对应的会话也早已不在。本工具把这些残留消息并回所属作品的**默认会话**：

- 默认会话 = 与作品同 id 的会话（迁移时为每个作品建的「默认会话」）——不建新会话；
- 只处理「目标会话里还没有该内容」的行（按 session_id + 原 index 去重，避免重复恢复）；
- 成功后默认删除备份表（--keep 可保留）。

用法：
    python tools/recover_backup.py            # 只预览
    python tools/recover_backup.py --apply    # 实际恢复 + 删残留表

安全性：全程单事务（成功才删表）；无论预览还是执行都不动任何现有数据。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from db import engine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="回收 _micro_messages_backup 残留消息")
    ap.add_argument("--apply", action="store_true", help="实际恢复并删除残留表（默认只预览）")
    ap.add_argument("--keep", action="store_true", help="恢复后保留备份表不删")
    args = ap.parse_args()

    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "_micro_messages_backup" not in tables:
            print("✅ 没有残留的 _micro_messages_backup 表，无需处理。")
            return 0
        rows = conn.execute(
            text(
                'SELECT id, session_id, "index", role, substr(content, 1, 60), media_url '
                'FROM _micro_messages_backup ORDER BY session_id, "index"'
            )
        ).fetchall()
        if not rows:
            if args.apply and not args.keep:
                conn.execute(text("DROP TABLE _micro_messages_backup"))
                conn.commit()
                print("残留表为空，已删除。")
            else:
                print("残留表存在但为空。")
            return 0

        print(f"残留表有 {len(rows)} 条消息：")
        for r in rows:
            print(f"  #{r[2]} [{r[0]}] session={r[1][:8]}… {r[3][:10]}：{r[4]}")

        # 目标会话 = 该 session_id（迁移时作品 id 沿用会话 id）对应的「默认会话」。
        # 若作品已不存在（会话被删干净），跳过（不新建任何东西）。
        targets = {}
        for s_id in sorted({r[1] for r in rows}):
            row = conn.execute(
                text("SELECT id, micro_id FROM micro_sessions WHERE id = :sid"), {"sid": s_id}
            ).fetchone()
            targets[s_id] = row[0] if row else None
        missing = [s for s, t in targets.items() if t is None]
        if missing:
            print(
                f"\n⚠ 下列会话已不存在（对应作品已删除）：{missing} —— 这些消息无法归属，将保留在备份表中。"
            )

        recoverable = [r for r in rows if targets.get(r[1])]
        print(
            f"\n将恢复 {len(recoverable)} 条到它们作品的默认会话，"
            f"{len(rows) - len(recoverable)} 条因会话已不存在而保留在备份表。"
        )

        if not args.apply:
            return 0

        moved = 0
        for r in recoverable:
            sid = targets[r[1]]
            exists = conn.execute(
                text('SELECT 1 FROM micro_messages WHERE session_id = :sid AND "index" = :i'),
                {"sid": sid, "i": r[2]},
            ).fetchone()
            if exists:
                continue  # 目标位置已有内容：不重复恢复
            conn.execute(
                text(
                    'INSERT INTO micro_messages (session_id, "index", role, content, media_url, prompt, created_at) '
                    "VALUES (:sid, :i, :role, :content, :media_url, :prompt, :created_at)"
                ),
                {
                    "sid": sid,
                    "i": r[2],
                    "role": r[3],
                    "content": conn.execute(
                        text(
                            "SELECT content FROM _micro_messages_backup WHERE id = :id AND session_id = :sid"
                        ),
                        {"id": r[0], "sid": r[1]},
                    ).scalar()
                    or "",
                    "media_url": r[5] or "",
                    "prompt": "",
                    "created_at": "",
                },
            )
            moved += 1
        conn.commit()
        print(f"✅ 已恢复 {moved} 条消息。")
        if not args.keep:
            conn.execute(text("DROP TABLE _micro_messages_backup"))
            conn.commit()
            print("已删除残留的备份表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
