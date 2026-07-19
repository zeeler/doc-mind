# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

知识库（Knowledge Base）— 基于 FastAPI + Vue 3 的 RAG 知识库应用。支持文档上传、解析、向量索引、混合搜索、对话问答、记忆系统。使用 MLX 框架在 Apple Silicon 上本地部署 LLM/Embedding/Reranker 模型。

## Commands

```bash
# 启动服务
python server/main.py

# 运行测试（排除依赖 MLX 的 chat 路由测试）
python -m pytest server/tests/ --ignore=server/tests/test_routers/test_chat.py -q

# 运行全部测试（需 MLX 服务运行中）
python -m pytest server/tests/ -q
```

## Architecture

```
server/
├── config.py              # KV 配置（SQLite 存储 + 5秒 TTL 内存缓存）
├── database.py            # SQLAlchemy + SQLite + FTS5 全文索引
├── main.py                # FastAPI 入口 + 生命周期管理
├── middleware/auth.py     # API Key 认证中间件（纯 ASGI，兼容 SSE）
├── models/                # SQLAlchemy 模型（Document/Chunk/Conversation/Message/Job/Tag）
├── routers/               # API 路由（chat/documents/conversations/config/jobs/memories/search/tags）
├── services/
│   ├── registry.py        # 统一服务缓存（LLM/Embedder/Reranker/SearchService/RAGService）
│   ├── observer.py        # 会话观察器（后台异步记忆提取）
│   ├── memory_manager.py  # 记忆系统编排（内置单例模式）
│   ├── memory_store.py    # ChromaDB 记忆存储
│   ├── memory_md_exporter.py  # 记忆 Markdown 导出
│   ├── llm.py             # LLM 适配器（OpenAI/Anthropic 格式）
│   ├── embedder.py        # Embedding 服务
│   ├── reranker.py        # Reranker 精排
│   ├── pipeline.py        # 文档处理管道（切块→embedding→ChromaDB）
│   ├── chunker.py         # 文本切块
│   ├── parser.py          # 文件解析（PDF/Word/Markdown/TXT）
│   ├── search.py          # 混合搜索（FTS5 + ChromaDB + RRF 融合 + MMR）
│   ├── retriever.py       # 检索服务（查询扩展 + Reranker 精排 + 上下文扩展）
│   ├── rag.py             # RAG 编排（组装 prompt + 调用 LLM）
│   ├── worker.py          # 后台任务 Worker（线程池消费 Job 队列）
│   ├── auto_tagger.py     # LLM 自动打标签
│   ├── tag_utils.py       # 标签工具
│   ├── scanner.py         # 快速扫描
│   ├── bookmark_parser.py # Chrome 书签解析
│   ├── url_fetcher.py     # URL 抓取（含内网地址 SSRF 防护）
│   ├── anysearch.py       # AnySearch 网络搜索（主引擎，JSON-RPC）
│   └── web_search.py      # Tavily 网络搜索（备用引擎）
├── templates/index.html   # Vue 3 单文件前端（inline in Jinja2 template）
├── tests/                 # pytest 测试
└── vector/store.py        # ChromaDB VectorStore 封装
```

## Key Patterns

### 服务获取
- 所有可缓存服务通过 `ServiceRegistry.get_singleton().get_X()` 获取
- 不要直接 `LLMAdapter(config)` 或 `Embedder(config)`
- `MemoryManager` 通过 `MemoryManager.get_singleton()` 获取单例

### 配置读取
- `AppConfig().get_all()` 有 5 秒 TTL 缓存，无需担心性能
- `AppConfig().set()` 写入后立即失效缓存
- 配置默认值在 `config.py:DEFAULTS` 中统一定义

### Session 管理
- FastAPI 路由：`Depends(get_session)` 注入
- 非路由代码（Worker、Service）：`with get_session_ctx() as session:`（正常退出自动 commit，异常自动 rollback；显式 commit 亦可，幂等）

### 单例模式
- ServiceRegistry: 双重检查锁，`get_singleton()` / `reset_singleton()`
- MemoryManager: 双重检查锁，`get_singleton(llm=None)` / `reset_singleton()`

## Frontend: 资料管理页面布局

页面结构从上到下：
1. **标题栏** — 返回按钮 + "资料管理"
2. **搜索工具栏** — 搜索输入框 + 类型选择(片段/文档) + 搜索按钮 + 状态筛选
3. **上传 & 扫描（并排各半）** — 左:拖拽上传区, 右:选择本地目录按钮（等高同宽）
4. **统计信息栏** — 向量维度/数量 + 任务进度 + 资料类型统计（可折叠）
5. **搜索结果**（有搜索时显示）
6. **文档列表**（翻页，每页 20 条）

## Frontend: 侧栏折叠规范

资料管理左侧栏（`.docs-sidebar`）折叠时：
- CSS: `.docs-sidebar.collapsed { width: 36px; padding: 0; }`，父元素需 `position: relative`
- 展开按钮 (`»`): `v-if="docsSidebarCollapsed"`，`position: static`，居中显示
- 收起按钮 (`«`): `v-if="!docsSidebarCollapsed"`，`position: absolute; right: 4px; top: 50%; transform: translateY(-50%)`
- **所有侧栏内容**（目录浏览、标签列表、任何新增区块）必须用 `v-show="!docsSidebarCollapsed"` 包裹，收起时完全隐藏
- 不要用仅 CSS width transition 来实现折叠 — 内容必须通过 Vue 条件渲染隐藏

## Preferences

- 使用中文回答
- 用户偏好「直接做」模式 — 简洁指令后期望直接实施
- 决策时偏好最全面/功能最丰富的方案
- 默认使用 MLX 本地模型，不依赖云端 API
