"""RAG 编排 — 组装 prompt、调用 LLM、流式输出。"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator
from server.services.llm import LLMAdapter
from server.services.web_search import WebSearchClient
from server.services.anysearch import AnySearchClient

logger = logging.getLogger(__name__)

# 网络搜索专用线程池（延迟初始化）：用于与知识库检索并行预取
_web_executor: ThreadPoolExecutor | None = None
_web_executor_lock = threading.Lock()


def _get_web_executor() -> ThreadPoolExecutor:
    global _web_executor
    if _web_executor is None:
        with _web_executor_lock:
            if _web_executor is None:
                _web_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-prefetch")
    return _web_executor


def _build_history_text(history: list[dict] | None) -> str:
    """将对话历史格式化为单段文本，注入 system message。"""
    if not history:
        return ""
    parts = []
    for h in history[-6:]:
        role = "用户" if h["role"] == "user" else "助手"
        parts.append(f"{role}：{h['content']}")
    if parts:
        return "## 对话历史\n" + "\n".join(parts)
    return ""


SYSTEM_PROMPT_BASE = "你是一个知识库助手。请根据参考资料回答用户问题。使用中文回答。"


def _build_messages(prompt: str, history: list[dict] | None = None,
                    memory_context: str = "") -> list[dict]:
    """Build the messages list with a single system message (Anthropic compatible)."""
    system_parts = [SYSTEM_PROMPT_BASE]
    if history:
        history_text = _build_history_text(history)
        if history_text:
            system_parts.append(history_text)
    if memory_context:
        system_parts.append(memory_context)
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": prompt},
    ]


def build_qa_prompt(
    question: str,
    chunks: list[dict],
    web_sourced: bool = False,
    web_searched: bool = False,
) -> str:
    if not chunks:
        search_scope = "知识库和网络搜索" if web_searched else "知识库"
        return (
            f"## 用户问题\n{question}\n\n"
            f"## 重要：{search_scope}未找到相关内容。\n"
            f"请如实告知用户未找到相关信息，不要编造、推测或追问。"
            f"建议用户尝试更换关键词或上传相关文档。"
            f"使用中文回答，简洁明确。"
        )
    web_chunks = [c for c in chunks if c.get("match_type") == "web"]
    kb_chunks = [c for c in chunks if c.get("match_type") != "web"]
    if web_sourced or not kb_chunks:
        return _build_web_prompt(question, chunks)
    if web_chunks:
        # 混合模式：KB 为主 + 网络补充，来源分节呈现，避免网络内容被标成《文档》
        return _build_mixed_prompt(question, kb_chunks, web_chunks)
    return _build_kb_prompt(question, chunks)


def _build_kb_prompt(question: str, chunks: list[dict]) -> str:
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

    return f"""## 参考资料（知识库文档）
{context}{doc_hint}

## 要求
- 你是一个严谨的知识库检索助手，请只根据上述参考资料回答问题
- 每个结论必须标注来源编号，如 [1]、[2]
- 参考资料来自用户已上传的个人文档，优先使用其中的信息
- 理解对话历史上下文，结合当前问题给出连贯回答
- 全面综合多个片段的信息，覆盖所有相关要点
- 如果参考资料只覆盖了部分问题，诚实说明哪些能回答、哪些不能
- 如果参考资料完全不相关，直接说明"知识库中未找到相关内容"，不要编造
- 使用中文回答
- 在回答末尾添加信息来源说明，格式：
  > 📚 **信息来源**：知识库文档《书名1》、《书名2》

## 用户问题
{question}"""


def _build_web_prompt(question: str, chunks: list[dict]) -> str:
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
    titles_str = "、".join(list(dict.fromkeys(doc_titles))[:3]) if doc_titles else "互联网"

    return f"""## 互联网搜索结果
{context}

## 要求
- 你是一个严谨的信息检索助手，请只根据上述互联网搜索结果回答问题
- 每个结论必须标注来源编号和链接，如 [1](url)
- 优先使用搜索结果中的信息，综合多个来源给出全面回答
- 如果搜索结果只覆盖了部分问题，诚实说明哪些能回答、哪些不能
- 如果搜索结果完全不相关或不够充分，直接说明"网络搜索未能找到足够信息"，不要编造
- 使用中文回答
- 在回答末尾添加信息来源说明，格式：
  > 🌐 **信息来源**：互联网搜索（{titles_str} 等）

## 用户问题
{question}"""


def _build_mixed_prompt(question: str, kb_chunks: list[dict], web_chunks: list[dict]) -> str:
    """KB 结果 + 网络结果混合场景：分节呈现并要求分别标注来源。"""
    kb_parts = []
    for i, chunk in enumerate(kb_chunks, 1):
        kb_parts.append(
            f"[{i}] 来源: {chunk['document_title']} (段落 {chunk.get('chunk_no', 0)})\n{chunk['content']}"
        )
    web_parts = []
    offset = len(kb_chunks)
    for j, chunk in enumerate(web_chunks, 1):
        url = chunk.get("url") or chunk.get("file_name", "")
        web_parts.append(
            f"[{offset + j}] 标题: {chunk['document_title']}\n链接: {url}\n内容: {chunk['content']}"
        )
    kb_titles = "、".join(
        list(dict.fromkeys(c["document_title"] for c in kb_chunks if c.get("document_title")))[:3]
    )

    return f"""## 参考资料一：知识库文档
{chr(10).join(kb_parts)}

## 参考资料二：互联网搜索结果（补充）
{chr(10).join(web_parts)}

## 要求
- 你是一个严谨的检索助手，只根据上述参考资料回答问题，不要编造
- 每个结论必须标注来源编号：知识库结论用 [编号]，网络结论用 [编号](链接)
- 优先使用知识库文档的信息，网络搜索结果仅作补充
- 如果参考资料只覆盖了部分问题，诚实说明哪些能回答、哪些不能
- 使用中文回答
- 在回答末尾分别列出信息来源，格式：
  > 📚 **知识库来源**：《{kb_titles}》
  > 🌐 **网络来源**：列出用到的链接

## 用户问题
{question}"""


def format_citations(chunks: list[dict], web_sourced: bool = False) -> list[dict]:
    seen_ids: set[str] = set()
    result = []
    for c in chunks:
        chunk_id = c.get("chunk_id", "")
        if chunk_id and chunk_id in seen_ids:
            continue
        if chunk_id:
            seen_ids.add(chunk_id)
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

    def _anysearch_usable(self) -> bool:
        return (
            self.config.get("anysearch_enabled", "true") == "true"
            and bool(self.config.get("anysearch_api_key", "").strip())
        )

    def _has_web_engine(self) -> bool:
        """任一搜索引擎配置可用（AnySearch 或 Tavily）。"""
        return self._anysearch_usable() or bool(self.config.get("tavily_api_key", "").strip())

    def _web_search_enabled(self) -> bool:
        """网络搜索总开关（设置页）。对话框勾选联网时也受它控制。"""
        return self.config.get("web_search_enabled", "false") == "true"

    def _should_search_web(self, web_search: bool) -> bool:
        """复选框是本次请求的硬开关；不因本地结果不足而自动联网。"""
        return web_search and self._web_search_enabled() and self._has_web_engine()

    def _do_web_search(self, question: str) -> tuple[list[dict], str | None]:
        """执行网络搜索：AnySearch 主 → Tavily 备。返回 (chunks, source)。
        source 为 'anysearch' | 'tavily' | None（无结果时）。

        AnySearch 未配置时 Tavily 直接作为主引擎（不受 fallback 开关限制）；
        AnySearch 配置了但失败/无结果时，是否回退 Tavily 由 web_search_fallback 控制。"""
        anysearch_usable = self._anysearch_usable()

        # 1. AnySearch（主）
        if anysearch_usable:
            try:
                max_results = int(self.config.get("anysearch_max_results", "5"))
                client = AnySearchClient(
                    api_key=self.config.get("anysearch_api_key", "").strip(),
                    max_results=max_results,
                )
                results = client.search(question)
                if results:
                    logger.info("AnySearch 命中: %d 条结果", len(results))
                    return results, "anysearch"
                else:
                    logger.info("AnySearch 无结果，尝试 Tavily")
            except Exception as e:
                logger.warning("AnySearch 调用失败: %s，尝试 Tavily", e)

        # 2. Tavily（备/替补主引擎）
        fallback_enabled = self.config.get("web_search_fallback", "true") == "true"
        tavily_key = self.config.get("tavily_api_key", "").strip()
        if tavily_key and (fallback_enabled or not anysearch_usable):
            try:
                max_results = int(self.config.get("web_search_max_results", "5"))
                results = WebSearchClient(tavily_key, max_results).search(question)
                if results:
                    logger.info("Tavily 命中: %d 条结果", len(results))
                    return results, "tavily"
                else:
                    logger.info("Tavily 无结果")
            except Exception as e:
                logger.warning("Tavily 调用失败: %s", e)

        return [], None

    def _can_parallel_web(self, web_search: bool) -> bool:
        """仅在本次允许联网时决定并行/串行；旧配置键仅控制执行顺序。"""
        return (
            self._should_search_web(web_search)
            and self.config.get("web_search_speculative", "true") == "true"
        )

    def _merge_web_results(
        self,
        kb_chunks: list[dict],
        web_chunks: list[dict],
        source: str | None,
        web_search: bool,
    ) -> tuple[list[dict], bool, int, int]:
        """勾选联网时保留两类来源；取消勾选时仅返回本地结果。"""
        if not self._should_search_web(web_search) or not web_chunks:
            return kb_chunks, False, len(kb_chunks), 0

        if not kb_chunks:
            return web_chunks, True, 0, len(web_chunks)

        logger.info("网络搜索补充(%s): %d 条结果", source, len(web_chunks))
        return kb_chunks + web_chunks, False, len(kb_chunks), len(web_chunks)

    def ask_sync(self, question: str, history: list[dict] | None = None,
                 memory_context: str = "", web_search: bool = True,
                 doc_ids: list[str] | None = None) -> dict:
        if self._can_parallel_web(web_search):
            # 网络搜索是纯 I/O 且最耗时，与检索并行发起，避免串行累加
            web_fut = _get_web_executor().submit(self._do_web_search, question)
            kb_chunks = self.retriever.retrieve(question, doc_ids=doc_ids)
            try:
                web_chunks, source = web_fut.result()
            except Exception as e:
                logger.warning("网络搜索失败: %s", e)
                web_chunks, source = [], None
        else:
            kb_chunks = self.retriever.retrieve(question, doc_ids=doc_ids)
            web_chunks, source = [], None
            if self._should_search_web(web_search):
                web_chunks, source = self._do_web_search(question)

        chunks, web_sourced, _, _ = self._merge_web_results(
            kb_chunks, web_chunks, source, web_search
        )

        prompt = build_qa_prompt(question, chunks, web_sourced=web_sourced,
                                 web_searched=self._should_search_web(web_search))

        messages = _build_messages(prompt, history=history, memory_context=memory_context)

        result = self.llm.chat(messages=messages, temperature=0.3)
        citations = format_citations(chunks, web_sourced=web_sourced)
        return {"answer": result["content"], "citations": citations}

    async def ask_stream(self, question: str, history: list[dict] | None = None,
                         memory_context: str = "", web_search: bool = True,
                         doc_ids: list[str] | None = None) -> AsyncIterator[dict]:
        loop = asyncio.get_running_loop()
        # 本次允许联网且启用并行时，同时检索两类来源。
        web_fut = (
            loop.run_in_executor(_get_web_executor(), self._do_web_search, question)
            if self._can_parallel_web(web_search) else None
        )
        kb_chunks = await loop.run_in_executor(None, self.retriever.retrieve, question, doc_ids)

        web_chunks: list[dict] = []
        source: str | None = None
        if web_fut is not None:
            try:
                web_chunks, source = await web_fut
            except Exception as e:
                logger.warning("网络搜索失败: %s", e)
        elif self._should_search_web(web_search):
            web_chunks, source = await loop.run_in_executor(
                _get_web_executor(), self._do_web_search, question
            )

        chunks, web_sourced, kb_count, web_count = self._merge_web_results(
            kb_chunks, web_chunks, source, web_search
        )

        # 先推送检索结果，前端可立即显示"已命中 N 篇"，不必空等到第一个 token
        yield {"type": "retrieval", "data": {"kb_count": kb_count, "web_count": web_count}}

        prompt = build_qa_prompt(question, chunks, web_sourced=web_sourced,
                                 web_searched=self._should_search_web(web_search))

        messages = _build_messages(prompt, history=history, memory_context=memory_context)

        async for chunk in self.llm.chat_stream(messages=messages, temperature=0.3):
            yield chunk
        yield {"type": "citations", "data": format_citations(chunks, web_sourced=web_sourced)}
