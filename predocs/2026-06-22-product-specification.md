# 知识库系统产品规格说明书

> 版本: 2.0 | 日期: 2026-06-22 | 基于提交 `0639c12`

---

## 目录

1. [整体架构设计](#一整体架构设计)
2. [配置管理](#二配置管理)
3. [文档管理（资料管理）](#三文档管理)
4. [文件解析与处理管道](#四文件解析与处理管道)
5. [向量索引与混合搜索](#五向量索引与混合搜索)
6. [RAG 对话问答](#六rag-对话问答)
7. [会话与记忆系统](#七会话与记忆系统)
8. [后台任务系统](#八后台任务系统)
9. [标签系统](#九标签系统)
10. [网络搜索与外部数据源](#十网络搜索与外部数据源)
11. [前端架构](#十一前端架构)
12. [技术栈与依赖](#十二技术栈与依赖)
13. [错误处理与容错机制](#十三错误处理与容错机制)

---

## 十三、错误处理与容错机制

### 13.1 文档处理管道容错

**Embedding 回退链路**（`server/services/pipeline.py`）：

```
外部 Embedding 模型可用?
  -> Yes: 逐 chunk 向量化 -> 写入 ChromaDB
       -> 中途失败? -> 回滚已写入 chunk -> 全部用 ChromaDB 内置 embedding 重试
  -> No: 直接使用 ChromaDB 内置 embedding
```

`_safe_fts_insert()` 包装 FTS5 写入，失败时仅警告不中断索引流程。

**PDF 解析容错**：liteparse 提取文本量 < 100 字符时自动启动 OCR。OCR 返回空结果时保持原始文本。

### 13.2 搜索容错

**向量搜索降级**：ChromaDB 查询失败时回退为纯 FTS5 模式。维度不匹配时（embedding 模型变更后）发出明确警告。

**Reranker 降级**：API 不可用时静默使用原始 RRF/MMR 排序结果。`rerank_chunks()` 返回 `None` 时调用方保留原始排序。

**纯 FTS5 模式**：无外部 Embedding 模型时完全不调用 ChromaDB，避免内置英文 embedding 对中文的低质量搜索。

### 13.3 Worker 错误处理

```
_claim_job() -> claim 失败: sleep 1s 重试
_execute_job() -> 任务失败:
  -> job.status = "failed" + error_message[:500]
  -> 其他任务不受影响，Worker 继续消费队列

特定失败场景:
  -> 文档不存在: job.status = "failed", 不重试
  -> 源文件被删除: job.status = "failed", 不重试
  -> 任务已被删除: 静默跳过
```

**retry 机制**：用户可手动触发 `POST /api/v1/jobs/{id}/retry` 或 `POST /api/v1/jobs/retry-failed` 批量重试。retry 将 failed 状态重置为 pending。

### 13.4 网络搜索降级

`_is_web_search_needed()` 在 KB 返回 0 条结果时自动触发。Tavily API 调用失败（HTTP 错误或网络超时）时返回空列表，不影响 KB 结果。API Key 未配置时完全跳过。

### 13.5 记忆系统容错

- LLM 调用失败：`observe()` 返回 0，不中断对话流
- 合并失败：单对失败不影响其他对，日志记录
- 导出失败：警告日志，不阻塞记忆存储
- 过期批量删除：通过 ChromaDB `where` 过滤，失败时操作被跳过

### 13.6 前端错误状态

- 对话流中断：SSE 连接断开时前端显示错误信息，不丢失已接收的 token
- 文件上传失败：HTTP 413（超限）/ 400（类型不支持）/ 500 在前端显示具体错误消息
- 配置连接测试：Embedding / Reranker 测试按钮在失败时显示 API 返回的具体错误
技术栈与依赖](#十二技术栈与依赖)

---

## 一、整体架构设计

### 1.1 系统定位

基于本地 MLX 模型栈的个人/团队知识库 RAG 系统。核心能力：

- 多格式文档上传、解析、切块、向量索引
- SQLite FTS5 + ChromaDB 混合检索 + Reranker 精排
- LLM 生成带引用的回答，支持流式输出
- 会话记忆系统（自动提取 + 被动存储 + 历史注入）
- 网络搜索作为知识库兜底

### 1.2 技术架构分层

```
┌─────────────────────────────────────────────────────────┐
│  Vue 3 单页前端 (server/templates/index.html)           │
│  聊天 | 资料管理 | 设置 | 搜索                           │
├─────────────────────────────────────────────────────────┤
│  FastAPI 路由层 (server/routers/)                       │
│  chat | documents | conversations | config | jobs       │
│  memories | search | tags                               │
├─────────────────────────────────────────────────────────┤
│  业务服务层 (server/services/)                          │
│  registry (服务缓存) | observer (会话观察)              │
│  rag (RAG编排) | retriever (检索) | search (混合搜索)   │
│  llm | embedder | reranker (模型适配)                   │
│  pipeline | chunker | parser (文档处理)                 │
│  memory_manager | memory_store (记忆系统)               │
│  worker (后台任务) | auto_tagger (自动标签)             │
├─────────────────────────────────────────────────────────┤
│  数据层                                                 │
│  SQLite (SQLAlchemy ORM + FTS5)                        │
│  ChromaDB (向量存储 + 记忆存储)                         │
│  文件系统 (原始文件 + Markdown 备份)                    │
├─────────────────────────────────────────────────────────┤
│  外部模型服务（MLX 本地部署）                           │
│  LLM (localhost:8080)                                  │
│  Embedding (localhost:8081)                            │
│  Reranker (localhost:8082)                             │
│  OCR 多模态 (localhost:11434, 可选)                    │
└─────────────────────────────────────────────────────────┘
```

### 1.3 数据流全景

```
文档上传 → 解析 → 切块 → Embedding → ChromaDB + FTS5
                                  ↓
用户提问 → 查询扩展 → 混合搜索 → RRF 融合 → MMR 重排
                                  ↓
                           Reranker 精排 → 上下文扩展
                                  ↓
                        记忆召回 → 组装 Prompt → LLM 生成
                                  ↓
                      SSE 流式输出 + 引用标注 + 记忆提取
```

### 1.4 服务依赖注入模式

所有可缓存服务通过 `ServiceRegistry` 统一管理生命周期：

```python
from server.services.registry import ServiceRegistry

reg = ServiceRegistry.get_singleton()
llm = reg.get_llm()           # LLMAdapter 单例
rag = reg.get_rag_service(dir) # RAGService 单例
svc = reg.get_search_service(dir)  # SearchService 单例
emb = reg.get_embedder()      # Embedder（配置变更自动重建）
```

`MemoryManager` 有自己的单例入口：

```python
from server.services.memory_manager import MemoryManager
mgr = MemoryManager.get_singleton()
```

### 1.5 配置系统

配置存储在 SQLite `app_config` 表中，`config.py:DEFAULTS` 定义所有默认值（75 项）。运行时 `AppConfig` 提供 5 秒 TTL 内存缓存，写入后立即失效。

---

### 1.6 数据库架构与模式演进

**文件**: `server/database.py`, `server/models/`

系统使用 SQLAlchemy Declarative Base，所有模型继承 `server.models.base.Base`。引擎使用 `check_same_thread=False` 支持多线程访问。

**SQLite 表结构**：

| 表名 | 模型类 | 关键字段 | 用途 |
|------|--------|----------|------|
| `documents` | Document | id(UUID), title, file_type, file_path, status, chunk_count, checksum(SHA256), folder_path, category | 文档元数据与生命周期 |
| `document_chunks` | DocumentChunk | id, document_id(FK CASCADE), chunk_no, content(TEXT), token_count, metadata_json(JSON) | 切块存储 |
| `conversations` | Conversation | id, title, status, created_at, updated_at | 对话会话 |
| `messages` | Message | id, conversation_id(FK CASCADE), role, content(TEXT), citations_json(JSON), created_at | 对话消息 |
| `jobs` | Job | id, document_id(FK CASCADE nullable), job_type, priority, status, progress, error_message | 后台任务 |
| `tags` | Tag | id, name(UNIQUE) | 标签 |
| `document_tags` | -- | doc_id(FK CASCADE), tag_id(FK CASCADE), 联合主键 | 文档-标签多对多关联 |
| `app_config` | AppConfigModel | key(主键), value(JSON), updated_at | KV 配置存储 |

**FTS5 全文索引**：

SQLite FTS5 虚拟表 `chunks_fts` 通过 raw SQL 创建和管理（不经过 SQLAlchemy ORM）：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id,
    content,
    document_title
)
```

核心问题：FTS5 的 unicode61 分词器不识别中文词边界。系统采用**字符级分词策略**——在连续 CJK 字符间插入空格使每个汉字成为独立 token。所有写入 `chunks_fts` 的 content 和 document_title 都经过 `space_cjk()` 预处理（如 "哈佛谈判心理学" -> "哈 佛 谈 判 心 理 学"）。搜索查询词同样处理后进入 FTS5 MATCH。FTS5 特殊运算符 `* ( ) ^` 在查询时转义防止语法错误。

**数据库迁移系统**：`init_db()` 调用链为 `Base.metadata.create_all()` -> `_migrate()`。使用增量迁移策略，通过 SQLite `PRAGMA user_version` 追踪版本：

| 版本 | 迁移内容 |
|------|----------|
| 初始 | documents 表基础结构 |
| v1 | ALTER TABLE 追加 elapsed_ms, checksum, folder_path, category 列 |
| v2 | CREATE TABLE jobs, document_chunks；CREATE TABLE tags + document_tags |
| v3 | CREATE VIRTUAL TABLE chunks_fts (FTS5) |
| v4 | CJK 空格分词：清空 chunks_fts -> fts_rebuild_all() 重建 -> user_version=4 |

迁移使用 `_table_exists(conn, name)` 和 `PRAGMA table_info` 检测表和列的存在性，避免首次部署或跨版本升级时 DDL 报错。

每次连接通过 SQLAlchemy `@event.listens_for(engine, "connect")` 自动执行 `PRAGMA foreign_keys = ON`（SQLite 默认关闭外键）。部分删除操作仍显式清理关联数据以兼容未启用 CASCADE 的旧数据库。

### 1.7 Session 管理模式

**文件**: `server/database.py:get_session`, `get_session_ctx`

| 模式 | 函数 | 适用场景 | 机制 |
|------|------|----------|------|
| FastAPI 依赖注入 | `get_session()` | 路由处理器（有 HTTP 请求上下文） | Generator yield，FastAPI 自动管理生命周期 |
| 上下文管理器 | `get_session_ctx()` | 非路由代码（Worker、Config、Pipeline） | `@contextmanager` 装饰器，with 语句使用 |

```python
# 路由中使用
@router.get("/docs")
def list_docs(session: Session = Depends(get_session)):
    return session.query(Document).all()

# 非路由代码使用
with get_session_ctx() as session:
    doc = session.get(Document, doc_id)
```

两者共享同一个 `sessionmaker`（`autocommit=False`, `autoflush=False`, `expire_on_commit=False`），线程安全。`reset_engine()` 仅测试用，清空引擎和 factory 以切换 DATA_DIR。

### 1.8 ServiceRegistry 缓存策略

**文件**: `server/services/registry.py`

ServiceRegistry 对每个服务实例使用**配置指纹**（config key tuple）检测变更：

| 服务 | 缓存键组成 | 重建触发条件 |
|------|-----------|------------|
| LLMAdapter | (llm_provider, mlx_chat_model, openai_chat_model, claude_chat_model, custom_chat_model) | provider 或模型名变更 |
| Embedder | (embedding_enabled, embedding_model, embedding_api_base, embedding_api_key) | Embedding 配置变更 |
| Reranker | (reranker_model, reranker_api_base, reranker_api_key) | Reranker 配置变更 |
| RAGService | data_dir + 15 个检索/embedding/网络搜索配置项 | 任一检索相关配置变更 |
| SearchService | (data_dir, top_k) | 数据目录或 top_k 变更 |

每个服务独立持有自己的锁（`_llm_lock`, `_embedder_lock` 等），避免不同服务间的锁竞争。全局单例通过双重检查锁（DCL）保证线程安全。

配置变更自动传播：`AppConfig.set()` 写入后立即失效配置内存缓存 -> 下次 `get_all()` 读取最新值 -> 各服务的配置指纹变化 -> 触发实例重建。


## 二、配置管理

### 2.1 后端实现

**文件**: `server/config.py`

**数据模型**: `AppConfigModel` → `app_config` 表（KV 结构，key 为主键，value 为 JSON）

**核心类**: `AppConfig`
- `get(key)` → 返回字符串值
- `set(key, value)` → 写入并失效缓存
- `get_all()` → 返回全部配置字典（5 秒 TTL 缓存）
- `invalidate_cache()` → 强制失效

**配置分类**：

| 分类 | 关键配置项 | 默认值 |
|------|-----------|--------|
| LLM | llm_provider, mlx_chat_model, openai_api_key, claude_api_key | mlx |
| Embedding | embedding_enabled, embedding_model, embedding_api_base | false |
| Reranker | reranker_enabled, reranker_model, reranker_api_base | false |
| 文档处理 | chunk_size, chunk_overlap | 800/100 |
| 检索 | retrieval_top_k, retrieval_enable_mmr, retrieval_fetch_multiplier | 15/true/3 |
| 网络搜索 | web_search_enabled, tavily_api_key | false |
| OCR | ocr_enabled, ocr_engine, ocr_ollama_model | true/tesseract |
| 记忆 | memory_enabled, memory_auto_observe, memory_observe_interval | true/true/3 |

### 2.2 前端实现

设置页面位于 `index.html` 中 `<div v-if="page==='settings'">`，分为三个区块：
- **LLM 对话配置**：provider 选择 + API 参数 + 测试连接按钮
- **Embedding / Reranker 配置**：独立开关 + 模型 + API 地址
- **网络搜索 & 其他**：Tavily Key + OCR 选项

所有配置通过 `GET /api/v1/config` 读取、`PUT /api/v1/config` 写入。

---

## 三、文档管理

### 3.1 前端布局

资料管理页面（`page==='docs'`）自上而下：

1. **标题栏**：返回按钮 + "资料管理" + 批量模式/手动打标签按钮
2. **搜索工具栏**：搜索输入 + 类型选择 + 搜索按钮 + 状态/分类筛选
3. **上传 & 扫描（并排各半）**：左拖拽上传区，右选择本地目录按钮（等高同宽）
4. **统计信息栏**：向量维度/数量 + 任务进度 + 资料类型统计
5. **搜索结果**（有搜索时显示）
6. **文档列表**（翻页，每页 20 条）

左侧栏（`.docs-sidebar`）：
- 目录浏览（文件夹树）
- 标签列表（可点击筛选）
- 折叠按钮（`«` / `»`），收起后宽 36px，所有内容通过 `v-show="!docsSidebarCollapsed"` 隐藏

### 3.2 后端 API

**路由**: `server/routers/documents.py`，前缀 `/api/v1/documents`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/upload` | POST | 上传文件（multipart，`file` + `folder_path`） |
| `/import-url` | POST | URL 导入（JSON：`{url, folder_path}`） |
| `/auto-tag-untagged` | POST | 批量为无标签文档自动打标签 |
| `/batch` | POST | 批量操作：delete/retry/tag/untag/categorize |
| `/stats` | GET | 统计：按类型计数 + 任务摘要 |
| `/folders` | GET | 文件夹列表（去重） |
| `/` | GET | 文档列表（支持 search/folder/category/tag/status 筛选 + 分页） |
| `/{doc_id}` | GET/PUT/DELETE | 文档详情/更新/删除 |
| `/{doc_id}/retag` | POST | 重新打标签 |

### 3.3 文件上传流程

```
客户端上传文件
  → 校验文件类型（SUPPORTED_TYPES）+ 大小（≤200MB）
  → SHA256 去重检查（加锁防 TOCTOU）
  → 保存文件到 data/files/{doc_id}/{safe_name}
  → 创建 Document 记录（status=pending）
  → 创建 Job：quick_scan (priority=1) + full_index (priority=5)
  → Worker 消费 Job 队列处理
```

路径穿越防护：`safe_name = Path(file.filename).name` 仅取文件名部分。

### 3.4 URL 导入流程

```
POST /import-url {url, folder_path}
  → SHA256(url) 去重
  → url_fetcher.fetch_url() 抓取网页
  → BeautifulSoup 提取正文（script/style/nav/footer 后取 body 文本）
  → title 从 <title> 提取
  → 保存为 .md → 创建 Document（file_type="url"）
  → 创建 Job 链 → Worker 处理
```

---

## 四、文件解析与处理管道

### 4.1 文件解析

**文件**: `server/services/parser.py`

支持的格式及解析方式：

| 格式 | 后缀 | 解析库 |
|------|------|--------|
| PDF（文字型） | .pdf | liteparse |
| PDF（扫描件） | .pdf | liteparse + OCR（tesseract 或 ollama 多模态） |
| Word | .docx | python-docx |
| Excel | .xlsx | openpyxl |
| PPTX | .pptx | python-pptx |
| Markdown | .md | 直接读取文本 |
| TXT | .txt | 直接读取文本 |
| 网页 | url | BeautifulSoup 正文提取 |

**OCR 流程**（扫描件 PDF）：
```
liteparse 解析 → 检测是否含文字层
  → 有文字层：直接返回
  → 无文字层：每页渲染为图片 → OCR 引擎识别
    tesseract: pytesseract 直接调用
    ollama: 调用多模态模型（Qwen2-VL）逐页识别
```

**注意**：PDF 解析有全局 `pdf_lock`，因为 liteparse 的 C 库存在 double-free 问题，需串行化。

### 4.2 文本切块

**文件**: `server/services/chunker.py`

```
原始文本
  → 按结构边界（# 标题、第X章、Chapter X）切分为 section
  → section 长度 ≤ section_chunk_size（chunk_size × 2）
  → 否则按段落（\n\n）切分
  → 长段落按句子（。！？.!?）切分，保持 overlap
```

参数：
- `chunk_size`：800 字符
- `chunk_overlap`：100 字符
- `section_chunk_size`：1600 字符（chunk_size × 2）

### 4.3 处理管道

**文件**: `server/services/pipeline.py`

**入口**: `index_document(doc_id, text, config)`

流程：
```
text → chunk_text() 切块
  → 每个 chunk 分配 UUID
  → 写入 SQLite (DocumentChunk 表)
  → Embedding 向量化
  → 写入 ChromaDB (knowledge_base collection)
  → 写入 FTS5 索引 (chunks_fts 表, CJK 字符间自动插空格)
```

外部 Embedding 失败时自动回退：先删已写入的 chunk（ChromaDB + SQLite），再用 ChromaDB 内置 Embedding 重试全部 chunk。

### 4.4 Markdown 备份

Worker 的 `full_index` 流程中，PDF 解析完成后：
- `index.md`：元数据 + 完整文本（`build_index_md(info, text)`）
- `<文件名>.md`：PDF 内容的纯文本备份（供 auto-tag 和用户查看）

---

## 五、向量索引与混合搜索

### 5.1 向量存储

**文件**: `server/vector/store.py`

- 基于 ChromaDB PersistentClient
- Collection: `knowledge_base`
- 距离空间: `cosine`（`hnsw:space=cosine`）
- Score 归一化: `1.0 - distance / 2.0`（cosine 距离 ∈ [0,2]）

### 5.2 混合搜索

**文件**: `server/services/search.py`

**核心类**: `SearchService(data_dir, top_k)`

**搜索策略**:

1. **FTS5 全文搜索**：SQLite FTS5 虚拟表，CJK 字符间自动插空格使每个汉字成为独立 token
2. **ChromaDB 向量搜索**：仅在有外部 Embedding 模型时启用
3. **RRF 融合**（Reciprocal Rank Fusion）：
   ```
   score = α / (k + keyword_rank) + (1-α) / (k + vector_rank)
   ```
   默认 α=0.5（关键词与向量等权重），k=60
4. **MMR 多样性重排**（Maximal Marginal Relevance）：
   贪心选择：既相关又不冗余的 chunk
   有 Embedding 时用余弦相似度，否则退化为 bigram Jaccard
   λ=0.7 控制相关性/多样性权衡
5. **上下文扩展**：取每个结果前后各 N 个相邻 chunk

**垃圾内容过滤**：
- 纯目录页检测（短文本 + ≥3 个章节标记）
- 出版社/公众号推广内容检测

### 5.3 检索服务

**文件**: `server/services/retriever.py`

**核心类**: `Retriever(config)`

**查询扩展**（`retrieval_enable_query_expansion=true` 时）：
- "X有哪些Y" → 拆分出 X + XY + X的Y
- "X和Y" → 分别搜索 X、Y
- "总结/概述/介绍/讲讲X" → 提取主题词 X
- "书名第N章..." → 提取章节编号，生成中阿两种格式
- 去除提问后缀（"讲了什么"、"怎么样"等）

**文档过滤**：查询中识别书名 → 搜索时限定该文档

**Reranker 精排**：取召回分数最高的候选 → 调用 Reranker API 重排序 → 保留 top_k 条

### 5.4 Reranker 服务

**文件**: `server/services/reranker.py`

- 客户端调用 BGE-Reranker 兼容 API
- 使用官方模板格式（`<|im_start|>user\n<Instruct>...`）
- 失败时静默降级为原始排序
- 通过 ServiceRegistry 缓存实例

### 5.5 搜索 API

**路由**: `server/routers/search.py`，`GET /api/v1/search`

参数：`q`, `type`（chunks/documents）, `top_k`, `document_id`

返回：chunk 列表（含 score、match_type、excerpt 高亮）或文档列表（含最佳分数、匹配数、top excerpts）

---

## 六、RAG 对话问答

### 6.1 RAG 编排

**文件**: `server/services/rag.py`

**核心类**: `RAGService(retriever, config)`

**问答流程**（`ask_sync` / `ask_stream`）：

```
用户问题
  → retriever.retrieve() 检索相关 chunk
  → 判断是否需要网络搜索补充
      (KB 结果太少或相关性太低 → WebSearchClient.search())
  → build_qa_prompt() 组装 Prompt
      知识库模式：参考资料 + 要求（引用编号、综合整理）
      网络搜索模式：搜索结果 + 链接引用
  → LLMAdapter.chat() 生成回答（同步/流式）
  → format_citations() 格式化引用
```

**系统 Prompt 结构**：
```
[system] 知识库助手指令 + 对话历史 + 记忆上下文
[user] 参考资料 + 要求 + 用户问题
```

### 6.2 LLM 适配器

**文件**: `server/services/llm.py`

**核心类**: `LLMAdapter(config)`

支持 provider：
- `mlx`：本地 MLX 服务（OpenAI 格式，localhost:8080）
- `openai`：OpenAI API
- `claude`：Anthropic API（原生格式，x-api-key + messages）
- `custom`：自定义 API（可选 OpenAI 或 Anthropic 格式）

**接口**：
- `chat(messages, temperature)` → 同步回答
- `chat_stream(messages, temperature)` → 异步流式（SSE）
- `embed(texts)` → 文本转向量

### 6.3 Embedding 服务

**文件**: `server/services/embedder.py`

两种模式：
1. **独立模式**（`embedding_enabled=true`）：专用 Embedding API
2. **跟随模式**：回退到 LLMAdapter 的 embedding 方法

### 6.4 对话 API

**路由**: `server/routers/chat.py`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/chat/ask` | POST | 同步问答（返回完整回答 + 引用） |
| `/api/v1/chat/stream` | POST | SSE 流式问答（逐 token + 引用） |

**请求体**：`{conversation_id, question}`

**流式事件类型**：
- `meta`：会话 ID
- `data`：`{type: "token", content: "..."}` — 逐 token
- `citations`：引用列表
- `error`：错误信息
- `done`：完成信号

**后台处理**：每次回答后，`observer.run_observe_bg()` 在独立线程池中异步分析对话，提取记忆。

---

## 七、会话与记忆系统

### 7.1 会话管理

**路由**: `server/routers/conversations.py`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/conversations` | GET | 会话列表（按更新时间倒序） |
| `/api/v1/conversations` | POST | 创建新会话 |
| `/api/v1/conversations/{id}` | GET | 会话详情（含消息列表） |
| `/api/v1/conversations/{id}` | DELETE | 删除会话 |
| `/api/v1/conversations/{id}` | PUT | 更新会话（标题等） |

**数据模型**：
- `Conversation`：id, title, created_at, updated_at
- `Message`：id, conversation_id, role（user/assistant）, content, citations_json

### 7.2 记忆系统架构

```
MemoryManager (编排层，内置单例)
├── MemoryStore (ChromaDB 存储)
│   ├── add() → 写入记忆
│   ├── search() → 语义搜索（cosine 距离）
│   ├── update() / delete() → CRUD
│   ├── get_all() → 全量导出
│   └── delete_expired() → 过期清理
└── MemoryMDExporter (Markdown 导出)
    ├── incremental_update() → 增量追加
    └── full_export() → 全量重写
```

### 7.3 记忆类型与生命周期

| 类型 | 说明 | 作用域 | 来源 |
|------|------|--------|------|
| preference | 用户偏好 | global | LLM 自动提取 + 手动 |
| fact | 可复用事实 | global | LLM 自动提取 |
| conclusion | 讨论结论 | session | LLM 自动提取 |
| manual | 用户要求记住 | global | API 手动 |

Session 级记忆有过期时间（`memory_session_expire_days`，默认 30 天）。

### 7.4 主动记忆（Observe）

**文件**: `server/services/observer.py`

**触发时机**（后台异步）：
- 每次对话回答后自动触发
- 空闲超时（`memory_session_idle_timeout`，默认 30 分钟）：检查全量
- 间隔模式（`memory_observe_interval`，默认每 3 条消息）：检查新增

**流程**：
```
近 6 条消息 → LLM 分析 → JSON 输出:
{
  "has_signal": true,
  "items": [
    {"content": "用户偏好简洁回答", "type": "preference",
     "scope": "global", "importance": 0.8}
  ]
}
→ memorize() 去重 (dedup_threshold=0.85) → 存储
```

并发保护：`_conv_observing` 集合防止同一会话的 observe 并发执行。

### 7.5 被动记忆（Remember）

**API**：`POST /api/v1/memories/remember`

用户主动要求系统记住某事时触发，`mem_type=manual`。

### 7.6 记忆注入（Recall）

每次对话时，`_recall_memory_context()` 搜索相关记忆，注入 system prompt：

```
## 用户历史信息
- [偏好] 用户喜欢简洁回答
- [事实] 用户使用 Python 编程

## 相关讨论结论
- [结论] 上次决定使用 Django 而非 Flask
```

排序算法：`0.5 × similarity + 0.3 × importance + 0.2 × recency`

### 7.7 记忆合并（Consolidate）

`POST /api/v1/memories/consolidate`

- 搜索 top-3 相似记忆 → 合并相似对
- 清理过期记忆
- 全量重新导出 .md 文件

### 7.8 记忆导出

`data/memories/` 目录下生成 Markdown 文件：
- `INDEX.md`：按类型分组的记忆列表
- 增量更新：每次 memorize/observe 后追加
- 全量导出：consolidate 后重新生成

### 7.9 记忆 API

**路由**: `server/routers/memories.py`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/memories` | GET | 记忆列表（按 type/scope 过滤） |
| `/api/v1/memories/search` | GET | 搜索记忆 |
| `/api/v1/memories/remember` | POST | 手动添加记忆 |
| `/api/v1/memories/{id}` | DELETE | 删除记忆 |
| `/api/v1/memories/observe` | POST | 手动触发分析 |
| `/api/v1/memories/consolidate` | POST | 合并记忆 + 清理过期 |
| `/api/v1/memories/export` | POST/GET | 导出 Markdown |

---

## 八、后台任务系统

### 8.1 Worker 线程池

**文件**: `server/services/worker.py`

**启动**: `start_workers(num=2)` — 2 个 daemon 线程，应用启动时创建

**Job 类型**：

| 类型 | priority | 说明 |
|------|----------|------|
| quick_scan | 1 | 快速扫描（提取标题/页数） |
| full_index | 5 | 完整索引（解析→切块→向量化→FTS5） |
| bookmark_import | 1 | 书签批量导入 |

**Claim 流程**: `_claim_lock` 保护，查询 pending → 更新 status=running → commit → expunge（在锁外处理）

**启动清理**: `_recover_stuck_jobs()` 恢复卡住的 running 任务、删除孤儿、去重

### 8.2 Job 模型

**字段**：id, document_id（bookmark_import 时为 None）, job_type, priority, status（pending/running/completed/failed/done）, progress, error_message, started_at, finished_at, created_at

**API**: `server/routers/jobs.py`
- `GET /api/v1/jobs` — 任务列表
- `GET /api/v1/jobs/stats` — 任务统计
- `POST /api/v1/jobs/{id}/retry` — 重试单个任务
- `POST /api/v1/jobs/retry-failed` — 批量重试失败任务

### 8.3 任务生命周期

```
create_jobs_for_document(doc_id)
  → 删除该文档已完成/失败的旧任务
  → 创建 quick_scan + full_index（跳过已有的 pending/running）

Worker:
  _claim_job() → status=pending → running
  _execute_job() → 执行具体逻辑
  成功: status=completed/done
  失败: status=failed + error_message

retry:
  POST /jobs/{id}/retry → status=pending → Worker 重新 claim
```

---

## 九、标签系统

### 9.1 数据模型

- `Tag`: id, name（unique）
- `document_tags` 多对多关联表

### 9.2 自动打标签

**文件**: `server/services/auto_tagger.py`

**触发时机**：
- Worker 的 `full_index` 完成后同步调用
- `POST /api/v1/documents/{doc_id}/retag` 手动触发
- `POST /api/v1/documents/auto-tag-untagged` 批量处理

**算法**：
```
取文档 title + 前 3 个 chunk 的前 200 字 → LLM 生成 3-5 个标签
Prompt: "根据以下资料内容，生成3-5个简短的分类标签。只输出标签名，一行一个。"
→ 解析标签名 → normalize_tag_name() → get_or_create_tag()
→ 覆盖式打标签（新标签替换旧标签）
```

### 9.3 标签 API

**路由**: `server/routers/tags.py`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/tags` | GET | 标签列表（含文档计数） |
| `/api/v1/tags` | POST | 创建标签 |
| `/api/v1/tags/{id}` | DELETE | 删除标签 |

---

## 十、网络搜索与外部数据源

### 10.1 Web 搜索

**文件**: `server/services/web_search.py`

- 基于 Tavily API
- 可配置：`web_search_enabled`, `tavily_api_key`, `web_search_max_results`

**触发条件**（`_is_web_search_needed()`）：
- KB 返回 0 条结果
- 平均分数过低（依据实际分数尺度动态判断）

**结果处理**：与 KB chunk 合并或替代，LLM 回答中标注来源。

### 10.2 URL 抓取

**文件**: `server/services/url_fetcher.py`

- httpx 抓取网页（timeout=30s, follow_redirects=true）
- BeautifulSoup 提取正文（去除 script/style/nav/footer）
- 优先 `<article>` → `role="main"` → `<body>`
- 提取 `<title>` 作为标题

### 10.3 快速扫描

**文件**: `server/services/scanner.py`

- `quick_scan(file_path)` 提取文档元数据（标题、页数、格式、大小）
- `build_index_md(info, text)` 生成 index.md（元数据 + 内容）

---

## 十一、前端架构

### 11.1 技术选型

- Vue 3（CDN 加载，Composition API）
- 单文件 HTML（inline CSS + JS，无构建工具）
- FastAPI StaticFiles 挂载 `/` 为 `server/templates/index.html`

### 11.2 页面路由

通过 `page` 响应式变量切换：

| page 值 | 页面 | 说明 |
|---------|------|------|
| `chat` | 对话 | 主界面，会话列表 + 对话面板 |
| `docs` | 资料管理 | 上传/搜索/管理文档 |
| `settings` | 设置 | 配置管理 |

### 11.3 对话页面布局

```
┌─ 侧栏 ───────────┬─ 主面板 ──────────────────┐
│                   │                          │
│ 知识库            │  消息列表                 │
│ [新建会话]        │  用户/助手 气泡           │
│ ─────────         │  Markdown 渲染           │
│ 会话1             │  引用标注                 │
│ 会话2             │                          │
│ 会话3             │  输入框 + 发送按钮        │
│                   │                          │
│ [资料管理]        │                          │
│ [设置]            │                          │
└───────────────────┴──────────────────────────┘
```

### 11.4 资料管理页面布局

```
┌─ 标题栏 ──────────────────────────────────────┐
│ ← 返回对话  资料管理   🏷 手动打标签  批量模式 │
├─ 搜索工具栏 ──────────────────────────────────┤
│ [搜索文档...] [片段▼] [搜索] [✕] [状态▼] [分类▼]│
├─ 上传 & 扫描（并排各半）──────────────────────┤
│ ┌─ 拖拽上传 ─┐ ┌─ 扫描目录 ─┐               │
│ └────────────┘ └────────────┘               │
├─ 统计信息栏 ───────────────────────────────────┤
│ 向量维度 2560 | 向量数 1,234 | 资料总数 45     │
├─ 搜索结果/文档列表 ────────────────────────────┤
│ [侧栏: 目录浏览 + 标签筛选]                   │
└─────────────────────────────────────────────┘
```

### 11.5 关键前端特性

- **流式输出**：EventSource 接收 SSE，实时渲染 Markdown
- **文档上传**：拖拽/点击上传，进度显示
- **批量操作**：多选文档 → 删除/重试/打标签
- **目录扫描**：选择本地目录 → 递归发现文件 → 批量上传
- **侧栏折叠**：`v-show` 条件渲染，收起时仅 36px 宽
- **暗色模式**：`prefers-color-scheme: dark` 媒体查询

---

## 十二、技术栈与依赖

### 12.1 后端

| 组件 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI 0.x + Uvicorn | 异步支持，自动 OpenAPI |
| ORM | SQLAlchemy 2.x | Declarative Base, sessionmaker |
| 数据库 | SQLite | 单文件部署，FTS5 全文索引 |
| 向量存储 | ChromaDB 0.x | PersistentClient, HNSW 索引 |
| LLM 适配 | openai SDK + httpx | 多 provider 统一接口 |
| 文件解析 | liteparse, python-docx, openpyxl, python-pptx | 多格式支持 |
| OCR | pytesseract / ollama 多模态 | 扫描件识别 |
| 前端渲染 | jinja2 + Vue 3 CDN | 单文件部署 |
| 流式输出 | sse-starlette | SSE 协议 |
| 测试 | pytest + unittest.mock | 22 个测试文件，202+ 条测试 |

### 12.2 目录结构

```
server/
├── config.py              # KV 配置（SQLite + 5秒 TTL 缓存）
├── database.py            # SQLAlchemy + FTS5 + 迁移
├── main.py                # FastAPI 入口 + 生命周期
├── models/                # SQLAlchemy 模型 (5 个)
├── routers/               # API 路由 (8 个)
├── services/              # 业务逻辑 (17 个)
│   ├── registry.py        # 统一服务缓存
│   ├── observer.py        # 会话观察器
│   ├── memory_manager.py  # 记忆系统编排
│   ├── memory_store.py    # ChromaDB 记忆存储
│   ├── memory_md_exporter.py  # 记忆中 Markdown 导出
│   ├── llm.py             # LLM 适配器
│   ├── embedder.py        # Embedding 服务
│   ├── reranker.py        # Reranker 精排
│   ├── pipeline.py        # 文档处理管道
│   ├── chunker.py         # 文本切块
│   ├── parser.py          # 文件解析
│   ├── search.py          # 混合搜索
│   ├── retriever.py       # 检索服务
│   ├── rag.py             # RAG 编排
│   ├── worker.py          # 后台任务 Worker
│   ├── auto_tagger.py     # LLM 自动打标签
│   ├── tag_utils.py       # 标签工具
│   ├── scanner.py         # 快速扫描
│   ├── url_fetcher.py     # URL 抓取
│   └── web_search.py      # Tavily 网络搜索
├── vector/
│   └── store.py           # ChromaDB 向量存储封装
├── templates/
│   └── index.html         # Vue 3 单文件前端
└── tests/                 # pytest 测试
```

### 12.3 数据存储

```
data/
├── app.db          # SQLite 数据库
├── chroma/         # ChromaDB 向量存储
├── files/          # 上传的原始文件（按 doc_id 分目录）
│   └── {doc_id}/
│       ├── {filename}     # 原始文件
│       ├── {filename}.md  # PDF OCR 备份
│       └── index.md       # 元数据 + 全文
└── memories/       # 记忆 Markdown 导出
    └── INDEX.md
```
