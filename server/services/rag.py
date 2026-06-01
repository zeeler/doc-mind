"""RAG 编排 — 组装 prompt、调用 LLM、流式输出。"""

import asyncio
from typing import AsyncIterator
from server.services.llm import LLMAdapter
from server.services.memory import search_memories


def build_qa_prompt(question: str, chunks: list[dict], memories: list[dict] | None = None) -> str:
    memory_section = ""
    if memories:
        mem_parts = []
        for m in memories[:3]:
            mem_parts.append(f"- {m['content']}")
        if mem_parts:
            memory_section = f"\n## 相关记忆\n" + "\n".join(mem_parts) + "\n"

    if not chunks:
        return f"用户问题：{question}\n\n{memory_section}知识库中未找到相关内容，请如实告知用户。"

    # 收集涉及的文档标题
    doc_titles = list(dict.fromkeys(c["document_title"] for c in chunks if c.get("document_title")))

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        chunk_no = chunk.get("chunk_no", 0)
        context_parts.append(
            f"[{i}] 来源: {chunk['document_title']} (段落 {chunk_no})\n{chunk['content']}"
        )

    context = "\n\n".join(context_parts)

    doc_hint = ""
    if doc_titles:
        titles_str = "、".join(doc_titles[:3])
        doc_hint = f"\n以上参考资料来自你的知识库文档：{titles_str}。这些是用户已上传的个人文档内容。"

    return f"""你是一个知识库助手。请根据以下参考资料回答用户问题。

{memory_section}
## 参考资料
{context}{doc_hint}

## 要求
- 参考资料来自用户已上传的文档，优先使用其中的信息回答问题
- 即使信息分散在多个片段中，也要尽量综合整理，给出有价值的回答
- 回答中引用来源编号，如 [1]、[2]
- 如果参考资料覆盖了多个不同的要点或角度，请全面综合回答，不要遗漏
- 只有确实完全不相关时才说明无法回答，不要因为信息不完整就放弃
- 使用中文回答

## 用户问题
{question}"""


def format_citations(chunks: list[dict]) -> list[dict]:
    return [
        {
            "source_type": "document_chunk",
            "chunk_id": c["chunk_id"],
            "document_id": c.get("document_id", ""),
            "document_title": c.get("document_title", ""),
            "file_name": c.get("file_name", ""),
            "chunk_no": c.get("chunk_no", 0),
            "excerpt": c["content"][:300],
        }
        for c in chunks
    ]


class RAGService:
    def __init__(self, retriever, config: dict):
        self.retriever = retriever
        self.llm = LLMAdapter(config)

    def ask_sync(self, question: str) -> dict:
        chunks = self.retriever.retrieve(question)
        memories = search_memories(question, top_k=3)
        prompt = build_qa_prompt(question, chunks, memories)
        result = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        citations = format_citations(chunks)
        return {"answer": result["content"], "citations": citations}

    async def ask_stream(self, question: str) -> AsyncIterator[dict]:
        loop = asyncio.get_running_loop()
        chunks = await loop.run_in_executor(None, self.retriever.retrieve, question)
        memories = await loop.run_in_executor(None, search_memories, question, 3)
        prompt = build_qa_prompt(question, chunks, memories)
        async for chunk in self.llm.chat_stream(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        ):
            yield chunk
        yield {"type": "citations", "data": format_citations(chunks)}
