# 知识库 Agent（Knowledge Base Agent）

基于本地 MLX 模型栈的个人/团队知识库 RAG 系统。支持多格式文档索引、混合检索、Reranker 精排、网络搜索兜底，以及会话记忆管理。

## 核心流程

```
用户提问 → Embedding 向量化 → FTS5 + ChromaDB 混合检索 → Reranker 精排 → LLM 生成回答
                                                                    ↓
                                                          知识库无结果 → 网络搜索兜底
```

- **回答均附带信息来源**（知识库文档引用或网络搜索链接）
- **会话记忆**：自动提取对话中的偏好/结论/事实，下次沟通时检索相关记忆

## 功能特性

- **多格式文档解析**：PDF（含扫描件 OCR）、Word、Excel、PPTX、MOBI、Markdown、TXT
- **混合检索**：SQLite FTS5 全文索引 + ChromaDB 向量搜索，RRF 融合排序
- **Reranker 精排**：基于 BGE-Reranker / vLLM 兼容 API 对召回结果重排序
- **查询扩展**：自动识别章节编号、书名过滤、宽泛问题拆分
- **MMR 多样性重排**：提升检索结果的覆盖广度
- **网络搜索后备**：Tavily API 联网搜索（知识库无结果时自动触发）
- **流式问答**：SSE 实时输出 LLM 回答
- **后台任务**：多 Worker 线程并行处理文档解析和索引
- **标签与集合**：文档分类管理

## 技术栈

| 层 | 技术 |
|---|------|
| 框架 | FastAPI + Uvicorn |
| 数据库 | SQLite（SQLAlchemy ORM） |
| 全文检索 | SQLite FTS5（CJK 字符级分词） |
| 向量存储 | ChromaDB（PersistentClient） |
| LLM 对话 | MLX 本地部署 / OpenAI / Anthropic / 自定义 API |
| Embedding | MLX 本地部署 / OpenAI / 自定义（独立配置） |
| Reranker | BGE-Reranker 兼容 API（MLX vLLM 本地部署） |
| OCR（扫描件） | 本地多模态模型（Qwen2-VL / Florence-2，MLX 部署） |
| 文档解析 | liteparse（PDF）、python-docx（Word）、openpyxl（Excel）、python-pptx（PPTX） |
| 前端 | 单页 HTML（FastAPI StaticFiles） |

## 项目结构

```
my_agent1/
├── server/
│   ├── main.py              # 应用入口，FastAPI lifespan
│   ├── config.py            # 配置管理（SQLite 持久化）
│   ├── database.py          # 数据库连接、迁移、FTS5 索引
│   ├── models/              # SQLAlchemy ORM 模型
│   │   ├── base.py
│   │   ├── document.py      # Document, DocumentChunk
│   │   ├── conversation.py  # Conversation, Message
│   │   ├── job.py           # Job（后台任务）
│   │   ├── tag.py           # Tag
│   │   └── collection.py    # Collection
│   ├── routers/             # FastAPI 路由
│   │   ├── chat.py          # 问答（同步 + SSE 流式）
│   │   ├── search.py        # 混合搜索 API
│   │   ├── documents.py     # 文档上传/管理/批量操作
│   │   ├── conversations.py # 会话管理
│   │   ├── memories.py      # 记忆管理
│   │   ├── jobs.py          # 后台任务监控/重试
│   │   ├── config.py        # 配置读写
│   │   ├── tags.py          # 标签管理
│   │   └── collections.py   # 集合管理
│   ├── services/            # 核心业务逻辑
│   │   ├── rag.py           # RAG 编排（prompt 构建、LLM 调用）
│   │   ├── retriever.py     # 检索服务（查询扩展、Reranker 精排、上下文扩展）
│   │   ├── search.py        # 混合搜索（FTS5 + ChromaDB + RRF + MMR）
│   │   ├── embedder.py      # Embedding 服务（独立/跟随 LLM 配置）
│   │   ├── reranker.py      # Reranker 客户端
│   │   ├── llm.py           # LLM 适配器（OpenAI/Anthropic 格式）
│   │   ├── pipeline.py      # 文档处理管道（解析→切块→索引）
│   │   ├── chunker.py       # 文本智能切块（结构感知）
│   │   ├── parser.py        # 多格式文档解析 + OCR
│   │   ├── scanner.py       # 快速扫描（标题/页数/预览）
│   │   ├── memory.py        # 记忆服务（去重/合并/摘要）
│   │   ├── memory_store.py  # ChromaDB 记忆封装
│   │   ├── worker.py        # 后台任务 Worker 线程池
│   │   ├── web_search.py    # Tavily 网络搜索
│   │   └── formats/         # 特殊格式解析
│   │       ├── mobi.py
│   │       ├── pptx.py
│   │       └── xlsx.py
│   ├── vector/
│   │   └── store.py         # ChromaDB 向量存储封装
│   └── templates/
│       └── index.html       # 前端页面
├── scripts/
│   ├── reembed.py           # 重建 ChromaDB 向量（模型切换后）
│   └── reindex.py           # 重建 FTS5 索引
├── predocs/                 # 设计文档
├── reviews/                 # 代码审查报告
├── pyproject.toml
├── requirements.txt
└── uv.lock
```

## 部署

### 环境要求

- Python ≥ 3.12
- macOS（MLX 模型部署需 Apple Silicon）

### 1. 安装依赖

```bash
cd my_agent1
pip install -r requirements.txt
# 或使用 uv
uv pip install -r requirements.txt
```

### 2. 部署 MLX 本地模型

项目依赖本地 MLX 部署三个模型服务：

#### 2.1 LLM 对话模型（MLX-LM Server）

```bash
git clone https://github.com/ml-explore/mlx-examples
cd mlx-examples/llm/mlx_lm

pip install -e .

# 启动 OpenAI 兼容 API（以 Qwen2.5-7B-Instruct-4bit 为例）
mlx_lm.server \
  --model mlx-community/Qwen2.5-7B-Instruct-4bit \
  --port 8080
```

#### 2.2 Embedding 模型（MLX-LM Server）

```bash
# 独立 Embedding 服务（推荐，避免与对话模型竞争资源）
mlx_lm.server \
  --model mlx-community/bge-m3-4bit \
  --port 8081
```

支持的 embedding 模型：
- `mlx-community/bge-m3-4bit`（多语言，推荐）
- `mlx-community/bge-large-zh-v1.5-4bit`（中文优化）
- `mlx-community/all-MiniLM-L6-v2-4bit`（轻量英文）

#### 2.3 Reranker 模型（Rapid-MLX vLLM）

```bash
git clone https://github.com/raullenchai/Rapid-MLX
cd Rapid-MLX
pip install -r requirements.txt

# 启动 Reranker 服务（BGE-Reranker v2-m3）
python -m rapid_mlx.reranker_server \
  --model BAAI/bge-reranker-v2-m3 \
  --port 8082
```

#### 2.4 OCR 多模态模型（可选，MLX VLM Server）

仅当需要处理扫描件 PDF 时部署：

```bash
git clone https://github.com/raullenchai/Rapid-MLX
cd Rapid-MLX

# 启动 VLM server（OpenAI 兼容接口）
python -m mlx_vlm.server \
  --model mlx-community/Qwen2-VL-2B-Instruct-4bit \
  --port 11434
```

OCR 模型选型：

| 模型 | 大小 | OCR 效果 | 速度 |
|------|------|---------|------|
| Florence-2-base | ~0.2B | 基础 OCR 足够 | 极快 |
| Qwen2-VL-2B | ~2B | 中文 OCR 更好 | 快 |
| Qwen2-VL-7B | ~7B | 复杂版面最佳 | 中等 |

### 3. 启动服务

```bash
python server/main.py
# 或
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后访问 http://localhost:8000 进入 Web 界面。

### 4. 初始配置

首次启动后在 Web 界面「设置」中配置模型连接：

**对话模型**（必填）：
- Provider: `custom`
- API Base: `http://localhost:8080/v1`
- API Type: `openai`
- Chat Model: `qwen2.5-7b-instruct-4bit`

**Embedding 模型**（必填，用于向量搜索）：
- 启用独立 Embedding: 开
- Model: `bge-m3-4bit`
- API Base: `http://localhost:8081/v1`

**Reranker 模型**（推荐启用）：
- 启用 Reranker: 开
- Model: `bge-reranker-v2-m3`
- API Base: `http://localhost:8082/v1`

**OCR 模型**（如需要处理扫描件 PDF）：
- OCR 引擎: `ollama`
- OCR 模型: `qwen2-vl-2b-instruct-4bit`
- OCR Base URL: `http://localhost:11434/v1`

数据默认存储在项目根目录的 `data/` 文件夹中（可通过环境变量 `KB_DATA_DIR` 自定义）。

## 使用指南

### 上传文档

1. Web 界面点击「上传文档」
2. 支持格式：PDF、Word (.docx)、Excel (.xlsx)、PPTX、MOBI、Markdown、TXT
3. 上传后系统自动创建后台任务：
   - `quick_scan`：提取标题、页数、预览
   - `full_index`：解析文本 → 切块 → Embedding → 索引

### 对话问答

1. 创建新会话
2. 输入问题，系统自动：
   - 从知识库检索相关内容
   - 如无匹配则触发网络搜索（需配置 Tavily API Key）
   - 检索结果经 Reranker 精排
   - LLM 综合生成带引用的回答

### 记忆管理

- 系统自动对每轮对话提取记忆（偏好/结论/事实）
- 后续对话中自动检索相关记忆融入上下文
- 可通过 API 手动添加/删除记忆

## API 概览

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 |
| `/api/v1/documents/upload` | POST | 上传文档 |
| `/api/v1/documents` | GET | 文档列表 |
| `/api/v1/documents/{id}` | GET/DELETE/PUT | 文档详情/删除/更新 |
| `/api/v1/documents/batch` | POST | 批量操作 |
| `/api/v1/conversations` | GET/POST | 会话列表/创建 |
| `/api/v1/conversations/{id}` | GET/DELETE/PUT | 会话详情/删除/更新 |
| `/api/v1/chat/ask` | POST | 同步问答 |
| `/api/v1/chat/stream` | POST | SSE 流式问答 |
| `/api/v1/search` | GET | 混合搜索 |
| `/api/v1/memories` | GET | 记忆列表 |
| `/api/v1/memories/search` | GET | 记忆搜索 |
| `/api/v1/memories/remember` | POST | 手动添加记忆 |
| `/api/v1/memories/{id}` | DELETE | 删除记忆 |
| `/api/v1/jobs` | GET | 任务列表 |
| `/api/v1/jobs/stats` | GET | 任务统计 |
| `/api/v1/jobs/{id}/retry` | POST | 重试任务 |
| `/api/v1/jobs/retry-failed` | POST | 批量重试失败任务 |
| `/api/v1/config` | GET/PUT | 配置读写 |
| `/api/v1/tags` | GET/POST | 标签列表/创建 |
| `/api/v1/tags/{id}` | DELETE | 删除标签 |
| `/api/v1/collections` | GET/POST | 集合列表/创建 |
| `/api/v1/collections/{id}` | DELETE | 删除集合 |

## 脚本工具

### 重建向量索引（模型切换后）

当更换 Embedding 模型后，需要重建所有向量：

```bash
python scripts/reembed.py
```

该脚本会遍历所有已处理的文档，用新模型重新生成 Embedding 并写入 ChromaDB。

### 重建 FTS5 索引

```bash
python scripts/reindex.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KB_DATA_DIR` | `项目根目录/data` | 数据存储目录（SQLite、ChromaDB、上传文件） |

## 配置项说明

所有配置项在 Web 界面「设置」中管理，存储在 SQLite `app_config` 表中。关键配置：

**LLM 对话**：
- `llm_provider`: 模型提供商（mlx/openai/claude/custom）
- 对应 `_api_base`、`_api_key`、`_chat_model` 按 provider 配置

**文档处理**：
- `chunk_size`: 切块大小（默认 800 字符）
- `chunk_overlap`: 相邻块重叠（默认 100 字符）

**检索**：
- `retrieval_top_k`: 召回数量（默认 15）
- `retrieval_fetch_multiplier`: 多召回倍数（默认 3x，用于 RRF 融合后截断）
- `retrieval_enable_mmr`: 启用 MMR 多样性重排（默认开启）
- `retrieval_mmr_lambda`: MMR 相关性/多样性权衡（默认 0.7）
- `retrieval_rrf_alpha`: RRF 关键词权重（默认 0.5）
- `retrieval_enable_query_expansion`: 启用查询扩展（默认开启）
- `retrieval_context_window`: 上下文扩展窗口（默认 3）
- `reranker_top_k`: Reranker 精排后保留数（默认 3）

**网络搜索**：
- `web_search_enabled`: 启用网络搜索兜底（默认关闭）
- `tavily_api_key`: Tavily API Key

**OCR**：
- `ocr_enabled`: 启用 OCR（默认开启）
- `ocr_engine`: 引擎选择（tesseract/ollama）
- `ocr_ollama_model`: 多模态模型 ID
- `ocr_ollama_base_url`: 多模态 API 地址
- `ocr_max_workers`: 并行页数（默认 2）

## 开发

```bash
# 运行测试
pytest server/tests/ -v

# 运行特定模块测试
pytest server/tests/test_search.py -v
pytest server/tests/test_rag.py -v
```

## 许可

MIT
