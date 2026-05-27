# Doc Mind — AI 知识库

本地运行的 AI 知识管理工具。支持上千份文档的批量导入、自动 OCR 解析、Markdown 生成，以及基于知识库的自然语言问答。

## 技术架构

```
Vue 3 (CDN)
    │ HTTP / SSE
    ▼
FastAPI 单进程
  ├─ 路由层：文档 / 会话 / 对话 / 配置 / 任务
  ├─ 服务层：解析 → 切块 → Embedding → 检索 → RAG 编排
  ├─ Worker 层：后台线程池消费 Job 队列（扫描 / 索引）
  └─ 存储层：SQLite (元数据 + 任务队列) + ChromaDB (向量)
    │ OpenAI 兼容 API
    ▼
MLX / Ollama / DeepSeek / OpenAI / 自定义 API
```

- **后端**：Python 3.12+ / FastAPI / SQLAlchemy / ChromaDB
- **前端**：Vue 3 CDN + Inter 字体，暗色/亮色自动切换
- **AI 引擎**：支持 MLX、Ollama、OpenAI、Claude、DeepSeek 及任意兼容 API
- **平台**：macOS Apple Silicon

## 主要功能

- **文档管理**：支持 PDF / Word / Excel / PowerPoint / MOBI / Markdown / TXT
- **SHA256 去重**：上传时自动检测重复文件，避免重复存储
- **批量导入**：选择本地目录，递归扫描并自动导入所有文档
- **扫描件 OCR**：Tesseract 离线 OCR + 多模态模型（Ollama/MLX）并行识别
- **两阶段处理**：快速扫描（秒级预览）→ 后台全文索引（OCR + 向量化）
- **Markdown 生成**：每份文档自动生成 index.md，源文件归档保留
- **知识问答**：自然语言提问，AI 基于知识库回答，带来源引用
- **流式输出**：SSE 实时流式返回
- **任务进度**：可视化进度条，实时显示处理状态
- **多模型配置**：对话模型 / OCR 模型独立配置，支持自定义 Base URL
- **暗色模式**：跟随系统自动切换

## 环境要求

- macOS (Apple Silicon)
- Python 3.12+
- 对话模型任选其一：MLX / Ollama / OpenAI / Claude / DeepSeek / 自定义
- OCR 引擎任选其一：Tesseract（`brew install tesseract tesseract-lang`）/ 本地多模态模型

## 快速开始

```bash
# 安装依赖
pip install -e ".[dev]"

# 安装 Tesseract（可选，用于 OCR）
brew install tesseract tesseract-lang

# 启动服务
python server/main.py
```

浏览器打开 `http://localhost:8000`，在设置页面配置模型即可使用。

## API 概览

所有接口前缀 `/api/v1`

### 文档 & 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/documents/upload` | 上传文档 |
| `GET` | `/documents` | 文档列表 |
| `GET` | `/documents/{id}` | 文档详情 |
| `DELETE` | `/documents/{id}` | 删除文档 |
| `GET` | `/jobs` | 任务列表 |
| `GET` | `/jobs/stats` | 任务统计（pending/running/completed/failed） |
| `POST` | `/jobs/{id}/retry` | 重试失败任务 |

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/conversations` | 创建会话 |
| `GET` | `/conversations` | 会话列表 |
| `GET` | `/conversations/{id}` | 会话详情 |
| `PUT` | `/conversations/{id}` | 重命名会话 |
| `POST` | `/chat/ask` | 同步问答 |
| `POST` | `/chat/stream` | 流式问答 (SSE) |

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/config` | 获取配置 |
| `PUT` | `/config` | 更新配置 |
| `GET` | `/config/models` | 可用模型 |
| `GET` | `/health` | 健康检查 |

## 项目结构

```
server/
├── main.py              # 应用入口
├── database.py          # 数据库连接 + 迁移
├── config.py            # KV 配置系统
├── models/
│   ├── base.py          # ORM 基类
│   ├── document.py      # Document / DocumentChunk
│   ├── conversation.py  # Conversation / Message
│   └── job.py           # Job（任务队列）
├── routers/
│   ├── documents.py     # 文档管理 API
│   ├── conversations.py # 会话管理 API
│   ├── chat.py          # 问答 + SSE 流式 API
│   ├── config.py        # 配置管理 API
│   └── jobs.py          # 任务状态 API
├── services/
│   ├── parser.py        # 统一文档解析
│   ├── formats/         # 格式解析器（pdf/docx/xlsx/pptx/mobi）
│   ├── scanner.py       # 快速扫描器（标题/预览）
│   ├── chunker.py       # 文本切块
│   ├── embedder.py      # Embedding 服务
│   ├── retriever.py     # 向量检索
│   ├── llm.py           # LLM 适配器（支持 OpenAI/Anthropic 格式）
│   ├── rag.py           # RAG 编排
│   ├── pipeline.py      # 文档处理管道
│   └── worker.py        # 后台 Worker 线程池
├── vector/
│   └── store.py         # ChromaDB 封装
└── templates/
    └── index.html       # 前端 SPA（Vue 3）
```

## 数据存储

```
data/
├── files/<doc_id>/   # 源文件 + index.md
├── chroma/           # ChromaDB 向量数据
└── app.db            # SQLite（元数据 + 任务队列）
```

备份/迁移只需复制整个 `data/` 目录。

## 运行测试

```bash
python -m pytest server/tests/ -v
```
