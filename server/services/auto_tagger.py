"""Auto tagging service — uses LLM to generate tags for documents."""

import logging
from server.services.llm import LLMAdapter
from server.services.tag_utils import normalize_tag_name, get_or_create_tag
from server.models.document import Document, DocumentChunk

logger = logging.getLogger("knowledge-base")

AUTO_TAG_PROMPT = """分析以下文档内容，根据文档主题和关键信息，生成 3-5 个中文标签（每个标签 2-6 个字）。
标签应简洁、准确，覆盖文档的核心主题。

标题: {title}
内容摘要:
{excerpt}

请只返回标签，每行一个，不要编号，不要额外说明。"""


def auto_tag_document(doc_id: str, text: str, config: dict, session) -> list[str]:
    """Analyze document content and generate tags.

    Returns list of tag names added.
    """
    doc = session.get(Document, doc_id)
    if not doc:
        logger.warning(f"auto_tag_document: document {doc_id} not found")
        return []

    title = doc.title or ""

    # Build excerpt: first 200 chars of first 3 chunks
    chunks = (
        session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_no)
        .limit(3)
        .all()
    )
    excerpt_parts = [c.content[:200] for c in chunks]
    excerpt = "\n".join(excerpt_parts)
    if not excerpt.strip():
        excerpt = text[:600]

    if not excerpt.strip():
        return []

    llm = LLMAdapter(config)
    prompt = AUTO_TAG_PROMPT.format(title=title, excerpt=excerpt)

    response = llm.chat([
        {"role": "system", "content": "你是一个文档分类和标签生成助手。请根据文档内容生成中文标签。"},
        {"role": "user", "content": prompt},
    ])

    content = response.get("content", "")
    tag_names = []
    for line in content.strip().split("\n"):
        line = line.strip().strip("-*0123456789. ").strip()
        if line and 2 <= len(line) <= 6:
            name = normalize_tag_name(line)
            if name:
                tag_obj = get_or_create_tag(session, name)
                if tag_obj and tag_obj not in doc.tags:
                    doc.tags.append(tag_obj)
                    tag_names.append(name)

    if tag_names:
        session.commit()
        logger.info(f"auto_tag: doc={title[:30]}, tags={tag_names}")

    return tag_names
