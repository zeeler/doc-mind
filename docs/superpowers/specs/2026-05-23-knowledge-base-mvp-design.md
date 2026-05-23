# 个人知识库 MVP 设计文档

**日期**: 2026-05-23  
**版本**: v1  
**状态**: 已确认

## 1. 项目定位

面向个人/小团队的轻量级知识管理工具。核心体验：上传文档后用自然语言提问，AI 基于文档内容回答并给出引用来源。

### 参考素材

predocs 目录中的 KAP（Knowledge Agent Platform）文档作为设计参考，本项目大幅精简其范围：
- 保留：文档接入、向量检索、流式问答、citation
- 删除：多用户权限、报告生成、Wiki 沉淀、数据分析、多数据源、GraphRAG

## 2. 目标用户与场景

- **用户**：个人（开发/写作/研究场景），可扩展至 3-5 人小团队共享
- **场景**：上传 PDF/Word/Markdown 资料 → 建立个人知识库 → 日常提问检索
- **平台**：仅 macOS（Apple Silicon），本地运行

## 3. 核心决策

| 维度 | 决策 | 理由 |
|------|------|------|
| 交互模式 | 对话优先 | 降低上手门槛，上传就能问 |
| AI 引擎 | MLX（mlx-lm server）为主 + 可选云端 API | Apple Silicon 原生优化，数据本地 |
| 架构 | FastAPI 单进程 + 内嵌前端 | 部署简单，一条命令启动 |
| 前端 | Petite-Vue + TailwindCSS CDN | 零构建，熟悉 Vue 语法，6KB 体积 |
| 元数据存储 | SQLite + SQLAlchemy | 零配置，数据自包含 |
| 向量存储 | ChromaDB（嵌入式模式） | pip install 即可，数据持久化到本地 |
| 文档解析 | markitdown + PyMuPDF + python-docx | 轻量组合，覆盖主流格式 |
| LLM 编排 | 自建轻量 RAG 链路，不引入 LangChain | MVP 流程简单，框架反成负担 |

## 4. 架构

```
浏览器 (Web UI)
    │ HTTP/SSE
    ▼
FastAPI 单进程
  ├─ 路由层：文档管理 / 会话 / 对话 / 配置
  ├─ 服务层：解析→切块→embedding→检索→LLM编排
  ├─ 存储层：SQLite（元数据）+ ChromaDB（向量）
  └─ 前端：index.html（Petite-Vue + TailwindCSS）
    │ OpenAI 兼容 API
    ▼
mlx-lm server (本地)
  ├─ embedding 模型
  └─ chat 模型
  可选: OpenAI / Claude API（云端）
```

- 单进程，FastAPI 同时提供 API 和静态文件服务
- 所有持久化数据集中在 `data/` 目录：原始文件 + ChromaDB + SQLite
- LLM 调用通过统一的 OpenAI 兼容适配器，切换本地/云端只需改配置

## 5. MVP 功能范围

### 一期必做

| 模块 | 功能 |
|------|------|
| 文档上传 | 支持 PDF/Word/Markdown/TXT，拖拽上传，解析→切块→索引异步执行 |
| 文档管理 | 文档列表、状态跟踪（pending/parsing/chunking/indexing/done/failed）、删除 |
| 会话管理 | 创建会话、历史会话列表、查看历史消息 |
| 流式问答 | SSE 流式返回，带 citation（来源文件名 + 片段引用） |
| 配置 | 模型选择（本地 mlx / 云端 API）、API key 设置 |
| 健康检查 | /api/v1/health，检测 mlx-lm server 可用性 |

### 一期不做

多用户登录、报告生成、Wiki 沉淀、数据分析、多知识库、GraphRAG、长期记忆

### 成功标准

- 上传文档 → 自动索引 → 可提问，流程通畅
- 回答带文档引用（来源文件名 + 片段）
- 对话流式输出，响应延迟可接受
- Mac 上一键启动，无需 Docker

## 6. 数据模型

### SQLite 表

```
documents                    conversations
├─ id (UUID, PK)            ├─ id (UUID, PK)
├─ title                    ├─ title
├─ file_name                ├─ status (active/archived)
├─ file_type                ├─ created_at
├─ file_path                └─ updated_at
├─ file_size                    │ 1:N
├─ status                       ▼
│   (pending/parsing/      messages
│    chunking/indexing/     ├─ id (UUID, PK)
│    done/failed)           ├─ conversation_id (FK)
├─ chunk_count              ├─ role (user/assistant)
├─ created_at               ├─ content
└─ updated_at               ├─ citations (JSON)
    │ 1:N                   └─ created_at
    ▼
document_chunks             app_config
├─ id (UUID, PK)            ├─ key (PK)
├─ document_id (FK)         ├─ value (JSON)
├─ chunk_no                 └─ updated_at
├─ content
├─ token_count
└─ metadata (JSON)
```

- 向量数据在 ChromaDB，通过 chunk_id 关联合并
- app_config 为 KV 配置表，存模型选择、API key 等

## 7. API 设计

Base URL: `/api/v1`

### 文档

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/documents/upload` | multipart 上传，返回文档 ID，后台异步处理 |
| `GET` | `/documents` | 文档列表 |
| `GET` | `/documents/{id}` | 文档详情 + chunk 列表 |
| `DELETE` | `/documents/{id}` | 删除文档及向量 |

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/conversations` | 创建会话 |
| `GET` | `/conversations` | 会话列表 |
| `GET` | `/conversations/{id}` | 会话详情（含消息） |
| `POST` | `/chat/ask` | 同步问答 |
| `POST` | `/chat/stream` | 流式问答（SSE） |

SSE 事件: `meta` → `token` → `citations` → `done`

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/config` | 获取配置 |
| `PUT` | `/config` | 更新配置 |
| `GET` | `/config/models` | 可用模型列表 |
| `GET` | `/health` | 健康检查 |

## 8. 项目结构

```
my_agent1/
├── server/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理
│   ├── database.py          # SQLite + SQLAlchemy
│   ├── models/
│   │   ├── document.py      # Document, DocumentChunk
│   │   └── conversation.py  # Conversation, Message
│   ├── routers/
│   │   ├── documents.py     # 文档 CRUD
│   │   ├── conversations.py # 会话 CRUD
│   │   ├── chat.py          # 问答 + SSE 流式
│   │   └── config.py        # 配置读写
│   ├── services/
│   │   ├── parser.py        # 文档解析
│   │   ├── chunker.py       # 文本切块
│   │   ├── embedder.py      # embedding
│   │   ├── retriever.py     # 向量检索
│   │   ├── llm.py           # LLM 适配器
│   │   └── rag.py           # RAG 编排
│   ├── vector/
│   │   └── store.py         # ChromaDB 封装
│   └── templates/
│       └── index.html       # 前端 SPA
├── data/                    # 运行时数据（gitignore）
│   ├── files/               # 原始文件
│   ├── chroma/              # ChromaDB 持久化
│   └── app.db               # SQLite
├── requirements.txt
└── pyproject.toml
```

- 前端为单个 `index.html`，Petite-Vue + TailwindCSS CDN + SSE 客户端
- data/ 目录自包含，备份/迁移只需复制整个目录

## 9. 启动体验

```bash
python server/main.py
# ✓ 检测到 MLX 模型: mlx-community/Qwen2.5-7B-Instruct-4bit
# ✓ 检测到 MLX embedding: mlx-community/bge-small-en-mlx
# ✓ SQLite 就绪
# ✓ ChromaDB 就绪
# 知识库服务已启动: http://localhost:8000
```

- 首次启动自动初始化 data/ 目录和数据库表
- 自动探测本地 mlx-lm 可用的模型
- 浏览器访问 `http://localhost:8000` 直接使用

## 10. 非功能约束

- **平台**：仅 macOS Apple Silicon，依赖 MLX 加速
- **隐私**：数据全量本地存储，不上传云端（使用云端 API 时仅发送检索片段）
- **性能**：10G 文档量级内保持可用响应
- **备份**：复制 data/ 目录即可完成全量迁移
