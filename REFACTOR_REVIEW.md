# 代码精简审查 & 重构方案

**项目**: 知识库 (Knowledge Base) RAG 应用  
**日期**: 2026-06-21  
**目标**: 找出死代码、冗余抽象、可简化的结构，提供可执行的重构方案

---

## 一、确认的死代码（可直接删除）

| # | 文件 | 内容 | 证据 |
|---|------|------|------|
| 1 | `server/services/bookmark_parser.py` | 整个文件（`BookmarkParser` 类 + `parse_bookmarks` 函数） | 全项目无任何 import 引用。书签导入实际在 `worker.py:_execute_bookmark_import` 中手动解析 JSON |
| 2 | `server/services/registry_v2.py` | 整个文件 | 与 `registry.py` 100% 重复，全项目无 import 引用 |
| 3 | `server/services/chunker.py:123` | `_split_paragraphs()` 函数 | 仅定义，无任何调用点。实际切分使用 `_split_by_structure()` |
| 4 | `server/services/chunker.py:18` | `_CHAPTER_BOUNDARY` 正则 | 仅定义，无引用。`_STRUCTURE_BOUNDARY` 已包含章节标记模式 |
| 5 | `server/config.py:44` | `chunk_structure_aware` 配置键 | DEFAULTS 中唯一个未被任何代码读取的配置项 |
| 6 | `server/main.py:37-40` | `_ensure_models_loaded()` 函数 | 与模块顶部的 `from server.models import Document, ...` 完全重复。同时导致 3 个无用 import |
| 7 | `server/main.py:14,16,17` | `HTMLResponse`, `Base`, `Job` 三个 import | `HTMLResponse` 从未使用；`Base` 和 `Job` 仅在 `_ensure_models_loaded()` 的重复 import 中出现 |
| 8 | `server/routers/documents.py:524` | `from sqlalchemy import select` | 从未在文件中使用 |
| 9 | `server/services/retriever.py:39` | `vector_store` 参数 | 文档注明"保留用于向后兼容，不再强制要求"，方法体内未使用 |

**删除预估**：约 350 行代码，2 个完整文件。

---

## 二、冗余抽象（可内联/简化）

### 2.1 `_build_history_text()` → 使用标准多轮 messages 格式

**文件**: [rag.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/rag.py:11)

当前：将对话历史转为 `"用户：...\n助手：..."` 文本 blob，塞入 system message。
问题：放弃了 OpenAI/Anthropic 原生多轮对话的结构化优势。system message 过长会稀释指令。

**方案**：直接在 `_build_messages` 中将历史消息作为独立的 `user`/`assistant` 消息追加：

```python
def _build_messages(prompt, history=None, memory_context=""):
    msgs = []
    if history:
        msgs.extend(history[-6:])  # 直接使用原生 role/content
    system_text = _build_system(memory_context)  # system 只用指令+记忆
    return [{"role": "system", "content": system_text}] + msgs + [{"role": "user", "content": prompt}]
```

**收益**：删除 `_build_history_text()`，减少约 15 行。

### 2.2 `SearchService._get_embedder()` → 直接调用 ServiceRegistry

**文件**: [search.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/search.py:88)

当前：`SearchService` 有一个 `_get_embedder()` 方法，仅做 `return ServiceRegistry.get_singleton().get_embedder()`。
问题：无意义的委托层。且 `SearchService` 本身是通过 `get_search_service()` 获取的，而 `get_search_service()` 内部也调用 `ServiceRegistry`。

**方案**：将 `_get_query_embedding` 中对 `self._get_embedder()` 的调用改为直接 `ServiceRegistry.get_singleton().get_embedder()`，删除 `_get_embedder()` 方法及 `_embedder`/`_embedder_config_key` 属性。

**收益**：删除约 10 行，消除一层间接调用。

### 2.3 `get_search_service()` 自由函数 → 全部内联为 ServiceRegistry 调用

**文件**: [search.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/search.py:327)

函数本身已注释"已弃用，请使用 ServiceRegistry"。但仍有 3 处调用：
- `retriever.py:42` — `self.search_service = get_search_service(...)`
- `documents.py:364` — `search_svc = get_search_service(...)`
- `search.py` 自身 — 路由 `/search`

**方案**：全部改为 `ServiceRegistry.get_singleton().get_search_service(DATA_DIR, top_k)`，删除 `get_search_service()` 函数。

**收益**：消除已弃用的 wrapper。

### 2.4 `pipeline.py:_init_embedder()` → 内联到 `_index_chunks()`

**文件**: [pipeline.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/pipeline.py:16)

`_init_embedder()` 是一个 11 行的函数，仅在 `_index_chunks()` 中被调用一次。它同时做了两件事：(1) 检查 `has_embedding_model(config)`；(2) 创建 `Embedder` 并测试连接。

问题：可读性不如直接展开。且 `_index_chunks` 已经有 `_try_index_chunks` → `_rollback_chunks` → `_try_index_chunks` 的重试逻辑，再加一层函数调用使追踪困难。

**方案**：将逻辑直接写入 `_index_chunks()`。

**收益**：删除 1 个函数，调用链扁平化。

### 2.5 `_is_web_search_needed()` — 过度复杂的多分数尺度判断

**文件**: [rag.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/rag.py:192)

该函数需处理三种不同分数尺度（Reranker 0-1、RRF ~0.008-0.017、FTS5 0.09-0.5），每种有不同的"低质量"阈值。当 Reranker 启用时，rerank 分数已覆盖 `score` 字段，但 FTS5-only 和 RRF 路径的判断逻辑完全依赖推测的分数范围。

**方案**：统一为单一分数尺度。`SearchService` 返回时一律归一化到 0-1。删除多分支阈值判断，使用单一阈值（如 `avg_score < 0.2`）。

**收益**：减少约 25 行，提升可维护性。

---

## 三、架构精简

### 3.1 MemoryManager 单例模式统一化

**当前问题**（见 [memory_manager.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/memory_manager.py:237)）：
- `get_singleton(llm=None)` → 返回全局单例（无 LLM）
- `get_singleton(llm=llm)` → **每次新建实例**（绕过单例）

导致 `observe()` 每次创建新的 `MemoryStore`（即新的 ChromaDB `PersistentClient`），资源泄漏。

**方案**：移除 `llm` 参数，`get_singleton()` 始终返回同一实例。`observe()` 需要 LLM 时通过 `ServiceRegistry.get_singleton().get_llm()` 动态获取：

```python
@classmethod
def get_singleton(cls) -> 'MemoryManager':
    global _manager_singleton
    if _manager_singleton is None:
        with _manager_singleton_lock:
            if _manager_singleton is None:
                _manager_singleton = cls(config=AppConfig().get_all(), ...)
    return _manager_singleton

def observe(self, messages, conv_id):
    llm = ServiceRegistry.get_singleton().get_llm()  # 动态获取
    ...
```

**收益**：删除约 10 行，消除资源泄漏，语义一致。

### 3.2 RAGService 内部的 LLMAdapter 共享化

**文件**: [rag.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/rag.py:164)

```python
self.llm = LLMAdapter(config)  # 绕过 ServiceRegistry
```

RAGService 自己创建 `LLMAdapter`，与 `ServiceRegistry` 中的实例重复。

**方案**：`RAGService.__init__` 不创建 `self.llm`，改为通过 `ServiceRegistry.get_llm()` 获取。`chat()` 和 `chat_stream()` 调用处改为：

```python
llm = ServiceRegistry.get_singleton().get_llm()
result = llm.chat(messages=messages, temperature=0.3)
```

**收益**：消灭重复 LLMAdapter 实例，统一缓存策略。

### 3.3 前端模板拆分

**文件**: [index.html](/Users/terry/Documents/cc_projects/my_agent1/server/templates/index.html) — 1285 行，121 个 Vue 指令

单文件内有聊天、资料管理、搜索、配置 4 大功能区域，CSS、JS、HTML 混在一起。Vue 3 支持 SFC 单文件组件，但当前项目没有构建工具链。

**方案**（低摩擦）：将 JS 逻辑抽取为 `<script>` 外链，CSS 抽取为 `<style>` 外链，保留在 Jinja2 模板内。结构变为：

```
templates/
├── index.html      # 仅结构骨架 + Jinja2 变量
├── static/
│   ├── app.js      # Vue 3 应用逻辑
│   └── app.css     # 样式
```

**收益**：主模板从 1285 行缩减到约 200 行，CSS/JS 可独立缓存。

---

## 四、配置债务

### 4.1 DEFAULTS 默认值与代码 fallback 不一致

| 配置键 | DEFAULTS 值 | 代码 fallback 值 | 文件 |
|--------|------------|-----------------|------|
| `ocr_max_workers` | `"2"` | `"4"` | parser.py:71, parser.py:85 |

代码使用 `config.get("ocr_max_workers", "4")`，但 DEFAULTS 中用 `"2"`。由于 key 存在于 DEFAULTS 中，实际生效的是 2，fallback 永远不会触发。但 fallback 值会误导阅读者。

**方案**：统一为 `"2"`（或提升 DEFAULTS 为 `"4"` 后统一 fallback）。

### 4.2 DEFAULTS 与 test_config.py 的测试耦合

`test_config.py` 中有多个测试断言"特定 key 必须在 DEFAULTS 中，否则新增配置项会导致 400"。
这间接要求每次新增配置项时修改两个文件（`config.py:DEFAULTS` + `test_config.py`）。

**方案**：将白名单校验从 `if k not in DEFAULTS` 改为 `if k not in KNOWN_KEYS`，其中 `KNOWN_KEYS` 由 `DEFAULTS` 生成，测试不再需要单独维护。

---

## 五、重构优先级 & 执行计划

### 第一轮：删代码（低风险，立即可做）
1. 删除 `bookmark_parser.py`
2. 删除 `registry_v2.py`
3. 删除 `_split_paragraphs()` 和 `_CHAPTER_BOUNDARY`
4. 删除 `chunk_structure_aware` 配置键
5. 删除 `_ensure_models_loaded()` + 无用 import
6. 删除 `vector_store` 参数
7. 删除 `select` import

**预计**：净删除约 350 行，零行为变化。

### 第二轮：内联简化（中等风险，需测试覆盖）
8. 内联 `_get_embedder()` → ServiceRegistry 直接调用
9. 删除 `get_search_service()` 自由函数
10. 内联 `_init_embedder()` 到 `_index_chunks()`
11. 简化 `_is_web_search_needed()` 分数判断

**预计**：净删除约 60 行，行为等价需验证。

### 第三轮：架构调整（需仔细测试）
12. 统一 MemoryManager 单例模式
13. RAGService 共享 LLMAdapter
14. 统一配置默认值与 fallback
15. `_build_history_text()` → 标准多轮 messages

**预计**：净删除约 40 行，行为有变化需全量回归。

### 第四轮：前端拆分（需浏览器验证）
16. 拆分 `index.html` 的 CSS/JS

**预计**：主模板从 1285 行减少到约 200 行，需 Playwright 端到端测试。

---

## 六、总结

| 类别 | 数量 | 预计净删行数 |
|------|------|-------------|
| 死代码（直接删除） | 9 项 | ~350 行 |
| 冗余抽象（内联/简化） | 5 项 | ~60 行 |
| 架构精简 | 3 项 | ~40 行 |
| 配置债务 | 2 项 | ~10 行 |
| 前端拆分 | 1 项 | ~1085 行（从模板移出） |
| **合计** | **20 项** | **~450 行净删除 + 1085 行移出** |
