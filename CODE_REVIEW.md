# 代码审查报告

**项目**: 知识库 (Knowledge Base) RAG 应用  
**日期**: 2026-06-21  
**审查范围**: `server/` 全部 Python 模块（config、database、main、models、routers、services、vector、tests）

---

## 1. 致命 Bug

### 1.1 worker.py — `_execute_job` 在分发 `bookmark_import` 前错误检查 document_id

文件: [worker.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/worker.py)

```python
# 行 161-166
doc = s.get(Document, job.document_id)
if not doc:
    job.status = "failed"
    ...
    return
```

`bookmark_import` 类型的 Job 的 `document_id` 为 `None`（书签导入不关联单一文档）。当前执行流会在到达 `elif job.job_type == "bookmark_import"` 之前就被 `doc is None` 拦截，导致书签导入任务永远以 "failed" 状态结束。

**修复方向**: 在检查 `doc` 存在性之前先判断 `job.job_type == "bookmark_import"` 并跳过。

### 1.2 VectorStore 默认空间不一致导致 score 计算不可靠

文件: [store.py](/Users/terry/Documents/cc_projects/my_agent1/server/vector/store.py)

`VectorStore` 创建 collection 时未指定 `hnsw:space`，ChromaDB 默认使用 `l2`（平方欧几里得距离），但 `search()` 中的 score 计算为 `1.0 - distance`。L2 距离无上界，`1.0 - distance` 可得到负值或远低于 0.5 的值。

对比 `MemoryStore.__init__()` 正确指定了 `metadata={"hnsw:space": "cosine"}` 并用 `1.0 - distance/2.0` 归一化。

**影响**: 向量搜索结果分数不可比、不可解释；MMR 和 RRF 融合中 L2 距离的原始值被当作用户友好的分数使用。

**修复方向**: 与 MemoryStore 对齐，显式使用 cosine 空间并做 `1 - distance/2` 归一化。

---

## 2. 严重设计问题

### 2.1 `registry.py` 与 `registry_v2.py` 完全重复

两个文件内容完全相同，代码重复 100%。项目中所有 import 都引用 `server.services.registry`，`registry_v2.py` 是从未合并/切换的遗留。应删除 `registry_v2.py` 以免混淆。

### 2.2 MemoryManager 单例模式不一致

文件: [memory_manager.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/memory_manager.py)

`get_singleton(llm=None)` 与 `get_singleton(llm=...)` 的行为截然不同：
- 传 `llm` 时：每次都创建**新实例**（绕过了单例）
- 不传时：返回全局单例（无 LLM）

`memories.py:observe_endpoint` 调用 `_get_mgr(with_llm=True)` 每次都新建一个 MemoryManager。`chat.py` 的 observer 通过 `MemoryManager.get_singleton(llm=llm)` 也是每次新建。

这意味着 observer 每次都在创建新的 MemoryStore（底层 ChromaDB 客户端），造成资源泄漏和锁竞争。

**修复方向**: `get_singleton` 应始终返回同一实例。LLM 应在需要时通过 ServiceRegistry 动态获取，而非构造时注入。

### 2.3 Retriever + RAGService 的配置 self-healing 不可靠

文件: [retriever.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/retriever.py)

`Retriever.__init__` 在构造时读取一次 `config`，之后配置变更不会触发重建 — 除非通过 `ServiceRegistry.get_rag_service()` 检测到 key 变化。但 `get_rag_service` 的 key 并不包含所有检索相关配置（如 `retrieval_rrf_alpha`、`reranker_top_k`），这些参数变更不会触发 RAGService 重建。

类似地，`SearchService` 的 key 仅含 `(data_dir, top_k)`，不跟踪 embedding/reranker 配置变化。

### 2.4 RAGService 内部的 LLMAdapter 绕过 ServiceRegistry 缓存

文件: [rag.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/rag.py)

```python
# 行 165
self.llm = LLMAdapter(config)
```

RAGService 在构造时自己创建 LLMAdapter，而不是通过 `ServiceRegistry.get_llm()`。一份配置对应两个 LLMAdapter 实例（ServiceRegistry 中一个，RAGService 中一个），浪费内存且缓存策略不统一。

---

## 3. 并发问题

### 3.1 `observer.py` — `run_observe_bg` 的并发保护不完整

文件: [observer.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/observer.py)

`run_observe_bg` 的并发流程：
1. 加锁检查 `conversation_id in _conv_observing` → 加入集合
2. 释放锁
3. 执行可能耗时的 `mem_mgr.observe(...)`（调用 LLM）

如果 chat_ask 和 chat_stream 几乎同时触发同一会话的 observe，步骤 1 的锁保护是有效的。但 `_conv_observing` 集合作为并发控制的唯一手段，在 LLM 调用失败时 finally 清理会永久丢弃该会话的后续 observe 机会。

更严重的是：`_conv_last_observe` 只在成功执行后才更新（步骤 3 之后），但如果 LLM 调用超时或阻塞，同会话的 observe 会因为这个 `_conv_observing` 标记被永久阻塞。

### 3.2 `worker.py` — `_claim_job` 锁范围交叉

文件: [worker.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/worker.py)

```python
with _claim_lock:
    with get_session_ctx() as s:
        ...
        s.commit()
```

`_claim_job` 在 `_claim_lock` 内创建 session、查询、更新、commit。虽然目标是串行 claim，但 session commit 在锁内执行增加了锁持有时间。更干净的方案：先 claim（更新 status → running + commit），再 expunge 退出锁。

### 3.3 `MemoryStore` 的 `delete_expired` 使用 `$lt` 过滤不可靠

文件: [memory_store.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/memory_store.py)

ChromaDB 的 `where` 过滤对 `$lt` 操作符的支持取决于 ChromaDB 版本。若过滤失败，所有记忆都会被当作过期删除。建议增加安全检查：先统计 expired_ids 数量，超过合理阈值（如总记忆数的 50%）时拒绝执行。

---

## 4. 代码质量

### 4.1 LLM 相关的配置键分散定义

文件: [config.py](/Users/terry/Documents/cc_projects/my_agent1/server/config.py) 和 [registry.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/registry.py)

`registry.py` 中 `get_rag_service` 的 key 包含 15 个配置项，而 `DEFAULTS` 中有 40+ 项。配置变更检测的正确性依赖于 key 是否覆盖了所有影响行为的配置项，缺少编译期保证。建议将每个服务的配置键定义为常量元组。

### 4.2 `parser.py` — `_ocr_ollama` 为每页创建新 OpenAI 客户端

文件: [parser.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/parser.py)

```python
def ocr_page(idx: int, s) -> tuple[int, str]:
    client = OpenAI(base_url=base_url, api_key="ocr")
```

每页 OCR 创建新的 `OpenAI` 客户端，浪费连接资源。应在线程池外部创建后共享。

### 4.3 `chunker.py` — `estimate_tokens` 是 O(n²) 实现

文件: [chunker.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/chunker.py)

```python
for ch in re.findall(r"[一-鿿]", text):
    other = other.replace(ch, "", 1)
```

对长文本会产生大量 `str.replace` 调用。建议直接用 `len(re.sub(r'[一-鿿]', '', text))` 替代循环。

### 4.4 全局 `pdf_lock` 限制并行度

文件: [parser.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/parser.py)

为了解决 liteparse C 库的 double-free bug，所有 PDF 解析被一个全局锁串行化。对于多 PDF 上传场景，2 个 Worker 线程只能串行解析。注释中已记录原因，这是合理的临时方案，但可通过子进程隔离来根本解决。

### 4.5 `memory_md_exporter.py` 的增量更新会无限增长 global 文件

文件: [memory_md_exporter.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/memory_md_exporter.py)

`_append_to_global_file` 只在文件不存在时写入 header 并计数 1，之后每次调用都追加一行并手动增加计数。但 `incremental_update` 在 memorize 去重命中时调用（合并场景），此时计数会增加但行数不变（因为内容是覆盖而非新增）。这会导致 header 中的 "共 X 条" 与实际行数不一致。

---

## 5. 测试覆盖

### 5.1 缺少 worker 和 observer 的端到端测试

`test_routers/` 覆盖了所有路由的 HTTP 层测试，但 `worker.py`、`observer.py` 的异步/后台行为没有对应的单元测试。worker 的 claim 竞态、observer 的并发互斥是高风险区域，却没有测试覆盖。

### 5.2 MMR 的 embedding fallback 路径未测试

文件: [search.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/search.py)

`_mmr_rerank` 有 embedding 和 bigram-Jaccard 两条路径。`test_search.py` 只测了 FTS5 路径，没有覆盖 MMR 的两种实现。

### 5.3 `conftest.py` 使用了未声明的依赖

文件: [conftest.py](/Users/terry/Documents/cc_projects/my_agent1/server/tests/conftest.py)

```python
import fitz  # PyMuPDF
```

未在项目依赖文件中声明 `PyMuPDF`，测试需要手动安装。

---

## 6. 安全性

### 6.1 文件上传路径未做路径穿越防护

文件: [documents.py](/Users/terry/Documents/cc_projects/my_agent1/server/routers/documents.py)

```python
# 行 55
file_path = file_dir / file.filename
```

若客户端发送 `filename="../../../etc/passwd"`，`Path` 的 `/` 运算符会将 `..` 解析为绝对路径的一部分，例如 `/data/files/abc/../../../etc/passwd` 解析后为 `/etc/passwd`。应使用 `Path(file.filename).name` 仅取文件名部分。

### 6.2 SQL 注入风险已基本防护，但 LIKE 回退路径值得注意

文件: [documents.py](/Users/terry/Documents/cc_projects/my_agent1/server/routers/documents.py)

```python
q = q.filter(Document.title.ilike(f"%{search}%"))
```

当 FTS5 搜索无结果时回退到 `ilike`，虽然 SQLAlchemy 的参数化查询会转义 LIKE 通配符，但用户的 `%` 和 `_` 字符可能产生非预期的匹配行为。风险低，因为输入不来自 SQL。

---

## 7. 其他发现

### 7.1 `main.py` 中 `_ensure_models_loaded` 与模块顶部 import 重复

模型在模块顶部已被 import，`_ensure_models_loaded()` 再次 import 属于冗余保护。可以删除函数或合并。

### 7.2 配置值统一为 `str` 类型导致处处手动转换

`AppConfig.get_all()` 返回值全部为字符串。每个使用数值配置的地方都需要手动 `int()` 或 `float()`。建议在 `get_all()` 返回前做类型推断。

### 7.3 `_build_messages` 将对话历史注入 system message 而非标准多轮对话

文件: [rag.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/rag.py)

对话历史以 "用户：...\n助手：..." 格式并入单个 system message，而非利用 Anthropic/OpenAI 的 user/assistant 交替消息。放弃了结构化对话的优势。

---

## 总结

| 严重度 | 数量 | 关键项 |
|--------|------|--------|
| 致命 Bug | 2 | bookmark_import 执行流错误、向量空间不一致 |
| 严重设计问题 | 4 | registry_v2 冗余、MemoryManager 单例不一致、配置变更检测不完整、RAGService 绕过 ServiceRegistry |
| 并发问题 | 3 | observer 阻塞风险、claim 锁范围、delete_expired 批量风险 |
| 代码质量 | 5 | 配置键分散、OCR 客户端重复创建、token 估算 O(n²)、PDF 全局锁、增量导出计数不一致 |
| 测试覆盖 | 3 | worker/observer 无端到端测试、MMR 路径未覆盖、conftest 依赖未声明 |
| 安全 | 1 | 文件名路径穿越风险 |

**建议优先级**:
1. 立即修复 1.1（bookmark_import bug）和 6.1（路径穿越）
2. 修复 1.2（向量空间一致性）
3. 清理 2.1（删除 registry_v2.py）
4. 重构 2.2（MemoryManager 统一单例）
5. 补充 5.1/5.2 的测试覆盖
