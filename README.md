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

## 部署教程

> ⚠️ **平台限制**：本项目当前仅在 **Apple M 系列处理器 + macOS** 上测试通过。MLX 框架是 Apple 官方推出的 Apple Silicon 机器学习框架，不支持 Intel Mac、Windows 或 Linux。如果你使用其他平台，可以将 LLM/Embedding/Reranker 替换为云端 API（OpenAI / Anthropic / 自定义）。

### 环境要求

| 组件 | 要求 |
|------|------|
| 硬件 | Apple M1/M2/M3/M4 芯片（推荐 16GB+ 统一内存） |
| 系统 | macOS 14.0 (Sonoma) 或更高 |
| Python | ≥ 3.12 |
| 磁盘 | ~20GB（含模型文件） |
| 网络 | 模型首次下载需访问 HuggingFace（需国际网络） |

### 第一步：安装 MLX 框架

MLX 是 Apple 官方的机器学习框架，专为 Apple Silicon 优化。所有本地模型基于 MLX 运行。

```bash
# 安装 MLX 核心库
pip install mlx mlx-lm mlx-vlm

# 验证安装
python -c "import mlx; print('MLX 已安装')"
```

如果下载速度慢，可使用 HF 镜像：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

### 第二步：安装 Python 依赖

```bash
# 克隆项目
git clone https://github.com/zeeler/doc-mind.git
cd doc-mind

# 创建虚拟环境（推荐）
python3.12 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 验证
python -c "import fastapi, chromadb, sqlalchemy; print('依赖安装成功')"
```

`requirements.txt` 包含的依赖：FastAPI、ChromaDB（向量存储）、SQLAlchemy（ORM）、liteparse（PDF 解析）、python-docx/openpyxl/python-pptx/ebooklib（文档解析）、pytesseract（OCR）、httpx/sse-starlette 等。

### 第三步：下载模型

模型托管在 HuggingFace 的 `mlx-community` 组织下，这是社区维护的 MLX 格式量化模型集合。首次运行 MLX Server 时会自动下载，也可以提前下载到本地缓存：

```bash
# 国内用户设置镜像加速
export HF_ENDPOINT=https://hf-mirror.com

# 可选：手动预热下载（以 Qwen2.5-7B 为例）
huggingface-cli download mlx-community/Qwen2.5-7B-Instruct-4bit
```

本项目需要的三组模型：

| 用途 | 推荐模型 | 显存占用 | 说明 |
|------|---------|---------|------|
| 对话（Chat） | `mlx-community/Qwen2.5-7B-Instruct-4bit` | ~4.5GB | 中文对话能力优秀，4bit 量化 |
| 向量（Embedding） | `mlx-community/bge-m3-4bit` | ~1.5GB | 多语言 embedding，1024 维 / 2560 维可选 |
| 精排（Reranker） | `BAAI/bge-reranker-v2-m3` | ~1.5GB | 跨语言 reranker，快速重排序 |

> **模型选型建议**：如果内存紧张（8GB Mac），对话模型可选 `Qwen2.5-1.5B-Instruct-4bit`（~1GB）或 `Qwen2.5-3B-Instruct-4bit`（~2GB），embedding 可选 `all-MiniLM-L6-v2-4bit`（~100MB）。

### 第四步：启动本地模型服务

需要启动 **三个** 独立服务进程（建议使用三个终端窗口）：

#### 4.1 LLM 对话服务（端口 8080）

```bash
mlx_lm.server \
  --model mlx-community/Qwen2.5-7B-Instruct-4bit \
  --port 8080
```

启动成功后访问 `http://localhost:8080/v1/models` 可查看模型信息。

#### 4.2 Embedding 向量服务（端口 8081）

```bash
mlx_lm.server \
  --model mlx-community/bge-m3-4bit \
  --port 8081
```

> **注意**：Embedding 模型维度需与 ChromaDB 一致。如果更换了不同维度的模型，需运行 `python scripts/reembed.py` 重建向量。

#### 4.3 Reranker 精排服务（端口 8082）

Reranker 使用 Rapid-MLX 项目部署，提供 OpenAI 兼容的 Rerank API：

```bash
# 先克隆 Rapid-MLX
git clone https://github.com/raullenchai/Rapid-MLX
cd Rapid-MLX
pip install -r requirements.txt

# 启动 Reranker 服务
python -m rapid_mlx.reranker_server \
  --model BAAI/bge-reranker-v2-m3 \
  --port 8082
```

验证服务是否正常：

```bash
curl http://localhost:8082/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-reranker-v2-m3","query":"测试","documents":["文档A","文档B"]}'
```

#### 4.4 OCR 多模态服务（可选，端口 11434）

仅当需要处理**扫描件 PDF**（图片型 PDF，不含文字层）时才需要。普通文字型 PDF 不需要此服务。

```bash
cd Rapid-MLX
python -m mlx_vlm.server \
  --model mlx-community/Qwen2-VL-2B-Instruct-4bit \
  --port 11434
```

### 第五步：启动知识库应用

```bash
# 在项目根目录
python server/main.py
# 或
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问 **http://localhost:8000** 进入 Web 界面。

首次启动会自动创建 `data/` 目录，包含：
```
data/
├── app.db         # SQLite 数据库（文档索引、配置、会话、记忆）
├── chroma/        # ChromaDB 向量存储
├── files/         # 上传的原始文件
└── memories/      # 记忆 Markdown 导出（可选）
```

数据目录可通过环境变量自定义：`KB_DATA_DIR=/path/to/data`

### 第六步：初始配置

打开 http://localhost:8000，点击导航栏「设置」，进行以下配置：

#### 对话模型配置

| 配置项 | 值 |
|--------|-----|
| LLM Provider | `custom` |
| API Type | `openai` |
| API Base | `http://localhost:8080/v1` |
| Chat Model | `qwen2.5-7b-instruct-4bit` |

点击「测试连接」验证。

#### Embedding 模型配置

| 配置项 | 值 |
|--------|-----|
| 启用独立 Embedding | 开 ✓ |
| Embedding Model | `bge-m3-4bit` |
| Embedding API Base | `http://localhost:8081/v1` |

点击「测试连接」验证。

#### Reranker 模型配置

| 配置项 | 值 |
|--------|-----|
| 启用 Reranker | 开 ✓ |
| Reranker Model | `bge-reranker-v2-m3` |
| Reranker API Base | `http://localhost:8082/v1` |

点击「测试连接」验证。

#### OCR 配置（如需扫描件支持）

| 配置项 | 值 |
|--------|-----|
| OCR 引擎 | `ollama` |
| OCR 模型 | `qwen2-vl-2b-instruct-4bit` |
| OCR Base URL | `http://localhost:11434/v1` |

### 第七步：验证

上传一个测试文档，创建对话测试问答：

```bash
# 用 curl 测试完整流程
# 1. 上传文档
curl -F "file=@test.txt" http://localhost:8000/api/v1/documents/upload

# 2. 等待处理完成后创建会话
curl -X POST http://localhost:8000/api/v1/conversations \
  -H "Content-Type: application/json" \
  -d '{"title":"测试"}'

# 3. 提问（将 CONV_ID 替换为上一步返回的 id）
curl -X POST http://localhost:8000/api/v1/chat/ask \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"CONV_ID","question":"总结一下这份文档"}'
```

预期返回带 `[1]`、`[2]` 等引用编号的回答。

### 故障排查

#### 模型服务无法启动

```bash
# 检查端口是否被占用
lsof -i :8080
lsof -i :8081
lsof -i :8082

# 如被占用，可更换端口启动
mlx_lm.server --model ... --port 8090
# 然后在设置中更新 API Base 的端口号
```

#### 内存不足 (OOM)

Apple Silicon 的统一内存由 CPU 和 GPU 共享。同时运行三个模型（对话+Embedding+Reranker）约需要 8-10GB 内存。

**优化方案**：
- 对话模型换为更小的量化版本（如 3B 或 1.5B）
- 关闭 Reranker（设置中禁用）
- 关闭 Embedding 服务，改用 OpenAI API 的 embedding
- Embedding 模型换为 `all-MiniLM-L6-v2-4bit`（~100MB）

#### 文档解析失败

- PDF 扫描件需要 OCR 服务（见第四步 4.4）
- 确保 `pytesseract` 已安装：`brew install tesseract tesseract-lang`
- 大文件（>100MB）处理较慢，请耐心等待后台任务完成

#### 更换 Embedding 模型后搜索不准确

```bash
# 重建所有向量（必须执行）
python scripts/reembed.py
```

该脚本会用新模型重新生成所有文档的向量并写入 ChromaDB。如果新模型维度不同，需先删除旧 collection：

```bash
python -c "
from server.database import DATA_DIR
from server.vector.store import get_client
client = get_client(str(DATA_DIR / 'chroma'))
client.delete_collection('knowledge_base')
print('已删除旧 collection')
"
python scripts/reembed.py
```

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
