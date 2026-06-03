"""RAG 编排 — 组装 prompt、调用 LLM、流式输出。"""

import asyncio
import logging
from typing import AsyncIterator
from server.services.llm import LLMAdapter
from server.services.memory import search_memories
from server.services.web_search import WebSearchClient

logger = logging.getLogger("knowledge-base")


def _build_memory_section(memories: list[dict] | None) -> str:
    if not memories:
        return ""
    parts = [f"- {m['content']}" for m in memories[:3]]
    return (f"\n## 相关记忆\n" + "\n".join(parts) + "\n") if parts else ""


def build_qa_prompt(
    question: str,
    chunks: list[dict],
    memories: list[dict] | None = None,
    web_sourced: bool = False,
) -> str:
    memory_section = _build_memory_section(memories)

    if not chunks:
        return (
            f"用户问题：{question}\n\n"
            f"{memory_section}"
            f"知识库中未找到相关内容。请基于你自身的知识如实回答，"
            f"并在回答末尾注明：\n"
            f"> 📚 *以上回答基于模型自身知识，未引用知识库文档。*"
        )

    if web_sourced:
        return _build_web_prompt(question, chunks, memory_section)
    return _build_kb_prompt(question, chunks, memory_section)


def _build_kb_prompt(question: str, chunks: list[dict], memory_section: str) -> str:
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
- 在回答末尾必须添加信息来源说明，格式如下：
  > 📚 **信息来源**：知识库文档《书名1》、《书名2》

## 用户问题
{question}"""


def _build_web_prompt(question: str, chunks: list[dict], memory_section: str) -> str:
    context_parts = []
    doc_titles = []
    for i, chunk in enumerate(chunks, 1):
        url = chunk.get("url") or chunk.get("file_name", "")
        context_parts.append(
            f"[{i}] 标题: {chunk['document_title']}\n"
            f"链接: {url}\n"
            f"内容: {chunk['content']}"
        )
        title = chunk.get("document_title", "")
        if title:
            doc_titles.append(title)

    context = "\n\n".join(context_parts)
    titles_str = "、".join(dict.fromkeys(doc_titles)[:3]) if doc_titles else "互联网"

    return f"""你是一个知识库助手。以下内容来自互联网实时搜索结果，请结合这些信息回答用户问题。

{memory_section}
## 互联网搜索结果
{context}

## 要求
- 优先使用搜索结果中的信息回答问题
- 回答中引用来源编号，如 [1]、[2]，并在引用处附上对应的链接 URL
- 综合多个来源的信息，给出全面的回答
- 如果搜索结果无法覆盖问题，可以结合自身知识补充，但请注明哪部分来自自身知识
- 使用中文回答
- 在回答末尾必须添加信息来源说明，格式如下：
  > 🌐 **信息来源**：互联网搜索（{titles_str} 等）

## 用户问题
{question}"""


def format_citations(chunks: list[dict], web_sourced: bool = False) -> list[dict]:
    seen_titles: set[str] = set()
    result = []
    for c in chunks:
        title = c.get("document_title", "") or c.get("file_name", "")
        if title in seen_titles:
            continue
        seen_titles.add(title)
        is_web = web_sourced or c.get("match_type") == "web"
        citation: dict = {
            "source_type": "web_search" if is_web else "document_chunk",
            "chunk_id": c.get("chunk_id", ""),
            "document_id": c.get("document_id", ""),
            "document_title": c.get("document_title", ""),
            "file_name": c.get("file_name", ""),
            "chunk_no": c.get("chunk_no", 0),
            "excerpt": c.get("content", "")[:300],
        }
        if is_web:
            citation["url"] = c.get("url") or c.get("file_name", "")
        result.append(citation)
    return result


class RAGService:
    def __init__(self, retriever, config: dict):
        self.retriever = retriever
        self.llm = LLMAdapter(config)
        self.config = config
        self._web_search_client: WebSearchClient | None = None

    @property
    def _web_search(self) -> WebSearchClient | None:
        """延迟初始化 WebSearchClient，仅在启用且配置了 Key 时可用。"""
        if self._web_search_client is not None:
            return self._web_search_client
        enabled = self.config.get("web_search_enabled", "false") == "true"
        api_key = self.config.get("tavily_api_key", "")
        if enabled and api_key:
            max_results = int(self.config.get("web_search_max_results", "5"))
            self._web_search_client = WebSearchClient(api_key, max_results)
        return self._web_search_client

    def _is_web_search_needed(self, chunks: list[dict]) -> bool:
        """判断是否需要触发网络搜索：KB 结果太少或相关性太低。"""
        client = self._web_search
        if client is None:
            return False
        if not chunks:
            return True
        avg = sum(c.get("score", 0.0) for c in chunks) / len(chunks)
        if avg < 0.15:
            return True
        good = [c for c in chunks if c.get("score", 0.0) > 0.3]
        if len(good) < 2 and len(chunks) < 3:
            return True
        return False

    def ask_sync(self, question: str) -> dict:
        chunks = self.retriever.retrieve(question)
        web_sourced = False

        if self._is_web_search_needed(chunks):
            ws = self._web_search
            if ws:
                web_chunks = ws.search(question)
                if web_chunks:
                    logger.info("网络搜索补充: %d 条结果", len(web_chunks))
                    chunks = web_chunks
                    web_sourced = True

        memories = search_memories(question, top_k=3)
        prompt = build_qa_prompt(question, chunks, memories, web_sourced=web_sourced)
        result = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        citations = format_citations(chunks, web_sourced=web_sourced)
        return {"answer": result["content"], "citations": citations}

    async def ask_stream(self, question: str) -> AsyncIterator[dict]:
        loop = asyncio.get_running_loop()
        chunks = await loop.run_in_executor(None, self.retriever.retrieve, question)
        web_sourced = False

        if self._is_web_search_needed(chunks):
            ws = self._web_search
            if ws:
                web_chunks = await loop.run_in_executor(None, ws.search, question)
                if web_chunks:
                    chunks = web_chunks
                    web_sourced = True

        memories = await loop.run_in_executor(None, search_memories, question, 3)
        prompt = build_qa_prompt(question, chunks, memories, web_sourced=web_sourced)
        async for chunk in self.llm.chat_stream(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        ):
            yield chunk
        yield {"type": "citations", "data": format_citations(chunks, web_sourced=web_sourced)}
