# 搜索增强 — 设计文档

**日期**: 2026-05-31
**状态**: 已确认

## 概述

为 Doc Mind 知识库增加混合搜索（关键词 + 向量）、搜索高亮、独立搜索 API。服务于两个场景：QA 对话的 RAG 检索质量提升，以及文档管理页面的独立搜索功能。

## 设计决策

- 关键词检索用 SQLite FTS5（`unicode61` tokenizer 原生支持中文），向量检索沿用 ChromaDB
- 结果融合用 RRF (Reciprocal Rank Fusion)，α=0.5, k=60
- 先不做重排序，混合检索 + 高亮即可
- 高亮用 `<mark>` 标签，前端 CSS 着色

## 架构

```
用户搜索 "机器学习"
        │
        ▼
┌───────────────────────────────────────┐
│           SearchService               │
│                                       │
│  SQLite FTS5              ChromaDB    │
│  ┌──────────┐          ┌──────────┐   │
│  │ 关键词匹配 │          │ 向量相似度 │   │
│  │ BM25 评分 │          │ cosine   │   │
│  └──────────┘          └──────────┘   │
│       │                      │        │
│       └──────┬───────────────┘        │
│              ▼                        │
│        结果融合 + 去重                  │
│        (RRF 加权合并)                  │
│              ▼                        │
│        ┌──────────┐                   │
│        │ 高亮标记   │                   │
│        └──────────┘                   │
└───────────────────────────────────────┘
        │
        ├──→ RAG 检索: 取 top-K chunks → build_qa_prompt
        └──→ 文档搜索: 返回匹配片段 + 高亮 → 前端展示
```

## 数据模型

### FTS5 全文索引

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id,
    content,
    document_title,
    tokenize='unicode61'
);
```

- `unicode61` tokenizer 原生支持中文按字符分词
- 通过 `chunk_id` 关联 `document_chunks` 表

### 索引维护

- 文档处理完成时：`INSERT INTO chunks_fts(...)` 写入
- 删除文档时：`DELETE FROM chunks_fts WHERE chunk_id = ?` 清理
- 在 pipeline.py 中触发，与 VectorStore 写入同步

## API 设计

### 新端点

**GET** `/api/v1/search`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `q` | string | 必填 | 搜索关键词 |
| `type` | string | `chunks` | `chunks` / `documents` |
| `top_k` | int | 10 | 返回数量 |
| `document_id` | string | 可选 | 限定文档内搜索 |

**响应** `type=chunks`：

```json
{
  "code": "OK",
  "data": [{
    "chunk_id": "...",
    "content": "...",
    "excerpt": "...<mark>机器学习</mark>...",
    "score": 0.87,
    "document_id": "...",
    "document_title": "AI入门.pdf",
    "chunk_no": 3,
    "match_type": "hybrid"
  }]
}
```

**响应** `type=documents`：

```json
{
  "code": "OK",
  "data": [{
    "document_id": "...",
    "title": "AI入门.pdf",
    "best_score": 0.87,
    "match_count": 5,
    "top_excerpts": ["...<mark>机器学习</mark>..."],
    "tags": [{"id": "...", "name": "ai"}],
    "category": "技术"
  }]
}
```

### 现有端点变更

- `GET /api/v1/documents?search=` — 内部改为调用 SearchService 文档级搜索
- retriever.py 的 `retrieve()` — 改为调用 SearchService 混合检索

## RRF 融合策略

```
score = α / (k + keyword_rank) + (1-α) / (k + vector_rank)
```

- `k = 60`（经典 RRF 参数）
- `α = 0.5`（关键词和向量等权）
- 同一 chunk 在两边都出现时：取融合后分数、去重

## 高亮策略

- 后端 `highlight(text, query)` 函数：对 query 分词，正则替换为 `<mark>` 标签
- excerpt 截取第一个匹配位置前后各 80 字符
- 前端 CSS：`mark { background: #fde68a; color: #000; padding: 1px 2px; border-radius: 2px; }`

## 前端 UI

文档管理页面工具栏增强：

- 搜索框（替换现有简单筛选） + 模式下拉（chunks/documents） + 搜索按钮
- 搜索时列表区域切换为搜索结果展示，每项显示来源、分数、带高亮的 excerpt
- 清空搜索框回到正常文档列表
- 新增 CSS 约 30 行，新增 JS 方法 `searchDocs()` / `clearSearch()`

## 文件结构

```
新增:
  server/services/search.py     # SearchService + highlight + RRF
  server/routers/search.py      # GET /api/v1/search

修改:
  server/database.py            # FTS5 迁移 + 写入/删除辅助函数
  server/services/pipeline.py   # 索引同步（写入 + 删除）
  server/services/retriever.py  # 切到 SearchService
  server/routers/documents.py   # search= 参数切到 SearchService
  server/templates/index.html   # 搜索 UI

测试:
  server/tests/test_search.py              # FTS / 向量 / RRF / 高亮
  server/tests/test_routers/test_search.py # API 端点
```

## 测试计划

| 模块 | 内容 | 预计用例 |
|------|------|---------|
| test_search.py | FTS 关键词搜索 / 向量搜索 / RRF 融合 / 去重 | 4 |
| test_search.py | 高亮标记 / excerpt 截取 | 2 |
| test_search.py | 文档聚合模式 | 1 |
| test_routers/test_search.py | chunks/documents 模式 / 空查询 / 无结果 / 限文档搜索 | 5 |
| **合计** | | **约 12** |

总测试数：116 → ~128

## 依赖

零新增依赖。SQLite FTS5 内置于 Python 3 标准库。
