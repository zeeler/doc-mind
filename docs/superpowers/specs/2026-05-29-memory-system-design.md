# Doc Mind 记忆系统设计

**日期**: 2026-05-29  
**版本**: v1  
**状态**: 已确认

## 1. 背景

现有系统支持对话历史和知识库检索，但缺乏跨对话记忆能力。用户期望 AI 能记住偏好、积累知识、引用之前的讨论结论。

## 2. 核心决策

| 维度 | 决策 |
|------|------|
| 记忆方案 | 对话摘要记忆（方案 A） |
| 存入方式 | 自动摘要 + 手动标记 |
| 去重策略 | 向量相似度 > 0.85 则合并更新 |
| 存储引擎 | ChromaDB 独立 collection |
| 检索集成 | 每次问答同时检索文档 chunk + 记忆 |

## 3. 记忆生命周期

```
对话进行中
  ├─ 用户点击「记住」→ 即时存入 ChromaDB
  └─ 对话结束 → 自动触发摘要
       └─ LLM 提取: 偏好 + 结论 + 事实
            └─ 去重: 向量相似度匹配
                 ├─ 相似度 > 0.85 → 合并更新
                 └─ 相似度 < 0.85 → 新增
```

## 4. 数据模型

ChromaDB `memories` collection：

```
memories collection
├── id: "mem_<uuid>"
├── document: "记忆内容"（自动 embedding）
├── metadata:
│   ├── type: "preference" | "conclusion" | "fact" | "manual"
│   ├── source_conv_id: "conv_xxx"
│   ├── related_docs: ["doc_a"]
│   ├── created_at / updated_at
│   └── merge_count: 0（被合并次数，去重用）
```

## 5. 服务层

### memory.py

| 函数 | 说明 |
|------|------|
| `add_memory(content, type, metadata)` | 存入记忆（先检索去重） |
| `summarize_conversation(conv_id)` | 自动摘要生成记忆 |
| `search_memories(query, top_k=5)` | 检索相关记忆 |
| `delete_memory(mem_id)` | 删除记忆 |

### RAG 集成

`RAGService.ask_stream()` 检索文档后额外调用 `search_memories(question, top_k=3)`，将相关记忆注入 prompt。

## 6. API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/memories/remember` | 手动标记记忆 `{message_id, note?}` |
| `GET` | `/api/v1/memories` | 记忆列表 `?type=preference&limit=50` |
| `GET` | `/api/v1/memories/search` | 搜索记忆 `?q=xxx&top_k=5` |
| `DELETE` | `/api/v1/memories/{id}` | 删除记忆 |
| `POST` | `/api/v1/conversations/{id}/summarize` | 手动触发对话摘要 |

## 7. 前端改动

- 消息旁增加「记住」按钮（hover 显示）
- 对话底部增加记忆面板入口
- 记忆列表展示在侧边栏（新增「记忆」导航项）

## 8. 配置（config.py DEFAULTS）

```
"memory_enabled": "true",
"memory_dedup_threshold": "0.85",
"memory_auto_summarize": "true",
"memory_max_per_conversation": "10",
```
