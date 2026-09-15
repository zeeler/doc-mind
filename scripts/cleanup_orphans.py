"""清理孤儿数据 — 删除文档/会话后遗留的文件目录、FTS 索引、关联行。

默认 **dry-run**（只统计并列出，不删任何东西）；确认无误后加 --apply 执行。

处理四类残留：
  1. FTS5 孤儿条目      —— chunks_fts 中 chunk_id 已不存在于 document_chunks
  2. 无主文件目录        —— data/files/<id> 在 documents 里没有对应行
  3. 孤儿 chunk / job 行 —— document_id 指向已不存在的文档
  4. 孤儿消息行          —— conversation_id 指向已不存在的会话

用法:
    python scripts/cleanup_orphans.py            # 预览（dry-run）
    python scripts/cleanup_orphans.py --apply    # 实际删除
"""

import argparse
import shutil
import sys
import logging
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup")


def _dir_size_mb(path: Path) -> float:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024
    except Exception:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="清理孤儿数据（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="实际执行删除（不加则只预览）")
    parser.add_argument("--quiet", action="store_true", help="不逐条列出，只输出汇总")
    args = parser.parse_args()

    import sqlalchemy as sa
    from server.database import (
        get_session_ctx, get_engine, init_db, DATA_DIR,
        fts_count_orphans, fts_delete_orphans,
    )
    from server.models.conversation import Conversation, Message
    from server.models.document import Document, DocumentChunk
    from server.models.job import Job

    init_db()
    logger.info(f"=== 孤儿数据清理 · {'实际删除' if args.apply else '预览 (dry-run)'} ===")

    # ---- 1) FTS5 孤儿条目 ----
    n_fts = fts_count_orphans()
    if not n_fts:
        logger.info("[1/4] FTS5 孤儿索引  : 无残留")
    elif args.apply:
        if not args.quiet:
            with get_engine().connect() as conn:
                rows = conn.execute(sa.text(
                    "SELECT f.chunk_id, f.document_title FROM chunks_fts f"
                    " LEFT JOIN document_chunks c ON f.chunk_id = c.id"
                    " WHERE c.id IS NULL LIMIT 10"
                )).fetchall()
            for cid, title in rows:
                logger.info(f"    FTS 孤儿: {cid}  《{(title or '')[:40]}》")
            if n_fts > len(rows):
                logger.info(f"    ...以及另外 {n_fts - len(rows)} 条")
        removed = fts_delete_orphans()
        logger.info(f"[1/4] FTS5 孤儿索引  : 已删除 {removed} 条")
    else:
        logger.info(f"[1/4] FTS5 孤儿索引  : 发现 {n_fts} 条待删除")

    # ---- 2) 无主文件目录 ----
    files_root = DATA_DIR / "files"
    with get_session_ctx() as s:
        doc_ids = {r[0] for r in s.query(Document.id).all()}
    orphan_dirs = sorted(
        p for p in files_root.iterdir()
        if p.is_dir() and p.name not in doc_ids and not p.name.startswith("_import_")
    ) if files_root.exists() else []
    if not orphan_dirs:
        logger.info("[2/4] 无主文件目录    : 无残留")
    else:
        total_mb = sum(_dir_size_mb(p) for p in orphan_dirs)
        if not args.quiet:
            for p in orphan_dirs[:10]:
                logger.info(f"    无主目录: {p.name}  ({_dir_size_mb(p):.1f} MB)")
            if len(orphan_dirs) > 10:
                logger.info(f"    ...以及另外 {len(orphan_dirs) - 10} 个")
        if args.apply:
            deleted = failed = 0
            for p in orphan_dirs:
                try:
                    shutil.rmtree(p)
                    deleted += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"    删除失败 {p.name}: {e}")
            logger.info(f"[2/4] 无主文件目录    : 已删除 {deleted} 个（{total_mb:.1f} MB）"
                        + (f"，失败 {failed} 个" if failed else ""))
        else:
            logger.info(f"[2/4] 无主文件目录    : 发现 {len(orphan_dirs)} 个，占 {total_mb:.1f} MB")

    # ---- 3) 孤儿 chunk / job 行 ----
    with get_session_ctx() as s:
        n_chunks = (
            s.query(DocumentChunk)
            .filter(~DocumentChunk.document_id.in_(s.query(Document.id)))
            .count()
        )
        n_jobs = (
            s.query(Job)
            .filter(Job.document_id.isnot(None), ~Job.document_id.in_(s.query(Document.id)))
            .count()
        )
        if args.apply:
            if n_chunks:
                s.query(DocumentChunk).filter(
                    ~DocumentChunk.document_id.in_(s.query(Document.id))
                ).delete(synchronize_session=False)
            if n_jobs:
                s.query(Job).filter(
                    Job.document_id.isnot(None),
                    ~Job.document_id.in_(s.query(Document.id)),
                ).delete(synchronize_session=False)
            s.commit()
    if n_chunks or n_jobs:
        logger.info(f"[3/4] 孤儿 chunk/job  : {'已删除' if args.apply else '发现'} "
                    f"chunk={n_chunks} job={n_jobs}")
    else:
        logger.info("[3/4] 孤儿 chunk/job  : 无残留")

    # ---- 4) 孤儿消息行 ----
    with get_session_ctx() as s:
        n_msgs = (
            s.query(Message)
            .filter(~Message.conversation_id.in_(s.query(Conversation.id)))
            .count()
        )
        if args.apply and n_msgs:
            s.query(Message).filter(
                ~Message.conversation_id.in_(s.query(Conversation.id))
            ).delete(synchronize_session=False)
            s.commit()
    if n_msgs:
        logger.info(f"[4/4] 孤儿消息行      : {'已删除' if args.apply else '发现'} {n_msgs} 条")
    else:
        logger.info("[4/4] 孤儿消息行      : 无残留")

    if not args.apply:
        logger.info("以上为预览。确认无误后执行: python scripts/cleanup_orphans.py --apply")
    else:
        logger.info("清理完成。建议随后重启服务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
