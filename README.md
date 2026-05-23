# 知识库 (Knowledge Base)

本地运行的个人/小团队 AI 知识管理工具。上传文档后用自然语言提问，AI 基于文档内容回答并给出引用来源。

## 技术架构

```
浏览器 (Petite-Vue + TailwindCSS)
    │ HTTP / SSE
    ▼
FastAPI 单进程
  ├─ 路由层：文档 / 会话 / 对话 / 配置
  ├─ 服务层：解析 → 切块 → Embedding → 检索 → RAG 编排
  └─ 存储层：SQLite (元数据) + ChromaDB (向量)
    │ OpenAI 兼容 API
    ▼
MLX (本地 Apple Silicon) 或 OpenAI / Claude (云端)
```

- **后端**：Python 3.12+ / FastAPI / SQLAlchemy / ChromaDB
- **前端**：Petite-Vue + TailwindCSS CDN，单 HTML 文件，零构建
- **AI 引擎**：优先本地 MLX，可切换 OpenAI / Claude
- **平台**：macOS Apple Silicon

## 主要功能

- **文档管理**：上传 PDF / Word / Markdown / TXT，自动解析、切块、向量索引
- **知识问答**：自然语言提问，AI 基于知识库内容回答，带来源引用
- **流式输出**：SSE 实时流式返回回答内容
- **会话管理**：多轮对话，历史会话查看
- **多模型支持**：MLX 本地模型 / OpenAI / Claude 可切换
- **配置管理**：Web UI 中切换模型和参数

## 环境要求

- macOS (Apple Silicon)
- Python 3.12+
- [mlx-lm](https://github.com/ml-explore/mlx-examples) (本地模型) 或 OpenAI / Claude API Key

推荐安装 mlx-lm server 提供本地模型：

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/Qwen2.5-7B-Instruct-4bit
```

## 快速开始

```bash
# 安装依赖
pip install -e ".[dev]"

# 启动服务
python server/main.py
```

输出：

```
✓ SQLite 就绪
✓ ChromaDB 就绪
知识库服务已启动: http://localhost:8000
```

浏览器打开 `http://localhost:8000`，在设置页面配置模型即可开始使用。

## API 概览

所有接口前缀 `/api/v1`

### 文档

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/documents/upload` | 上传文档 |
| `GET` | `/documents` | 文档列表 |
| `GET` | `/documents/{id}` | 文档详情 |
| `DELETE` | `/documents/{id}` | 删除文档 |

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/conversations` | 创建会话 |
| `GET` | `/conversations` | 会话列表 |
| `GET` | `/conversations/{id}` | 会话详情 |
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
├── database.py          # 数据库连接
├── config.py            # KV 配置系统
├── models/
│   ├── base.py          # ORM 基类
│   ├── document.py      # Document / DocumentChunk
│   └── conversation.py  # Conversation / Message
├── routers/
│   ├── documents.py     # 文档管理 API
│   ├── conversations.py # 会话管理 API
│   ├── chat.py          # 问答 + SSE 流式 API
│   └── config.py        # 配置管理 API
├── services/
│   ├── parser.py        # 文档解析 (PDF/Word/Markdown/TXT)
│   ├── chunker.py       # 文本切块
│   ├── embedder.py      # Embedding 服务
│   ├── retriever.py     # 向量检索
│   ├── llm.py           # LLM 适配器 (MLX/OpenAI/Claude)
│   ├── rag.py           # RAG 编排 (Prompt + 流式)
│   └── pipeline.py      # 文档处理管道
├── vector/
│   └── store.py         # ChromaDB 封装
└── templates/
    └── index.html       # 前端 SPA
```

## 数据存储

所有数据集中在 `data/` 目录：

```
data/
├── files/    # 上传的原始文件
├── chroma/   # ChromaDB 向量数据
└── app.db    # SQLite 数据库
```

备份/迁移只需复制整个 `data/` 目录。

## 运行测试

```bash
python -m pytest server/tests/ -v
```
