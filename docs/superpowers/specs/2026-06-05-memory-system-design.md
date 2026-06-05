# 记忆系统设计

> 日期: 2026-06-05 | 状态: 待实施

## 概述

为知识库 AI agent 构建完整的记忆系统，实现**主动记忆**（对话中自动发现要点）+ **被动记忆**（被要求记住的信息），记忆内容经过 LLM 概括后落盘存储，每次对话自动搜索历史记忆注入上下文。

## 核心决策

| 决策点 | 选择 |
|--------|------|
| 存储方式 | 混合方案：ChromaDB 主存储（语义检索）+ Markdown 文件导出（人类可读） |
| 主动记忆触发 | 智能触发（LLM 信号检测）+ 会话结束兜底 |
| 记忆注入方式 | 混合注入：稳定信息（偏好/事实）→ system prompt，临时信息（结论）→ 上下文消息 |
| 作用域 | 两级：全局记忆跨会话共享 + 会话级记忆归属特定对话 |

## 架构

```
┌──────────────────────────────────────────────────────────┐
│                     chat.py (对话路由)                     │
│                                                          │
│  POST /ask  ←── ① 对话前 recall() → 注入上下文             │
│       │        ② 对话后 observe() → 主动记忆               │
│       │        ③ 被动触发 memorize() → 被动记忆             │
└───────┼──────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────┐
│                  MemoryManager (新增)                      │
│                                                          │
│  recall(query, scope) → list[Memory]    搜索+注入决策      │
│  observe(messages, conv_id) → int       主动发现+存储      │
│  memorize(content, type, scope) → str   被动存储+去重      │
│  consolidate() → int                    定期合并相似记忆    │
│  export_md() → Path                     导出可读 md 文件   │
│                                                          │
│  内部依赖: LLMAdapter (智能检测+概括)                       │
└───┬─────────────────────┬────────────────────────────────┘
    │                     │
┌───▼─────────┐  ┌────────▼──────────┐
│ MemoryStore │  │  MemoryMDExporter │
│ (ChromaDB)  │  │  (data/memories/) │
│ 语义检索+去重 │  │  .md 文件导出      │
└─────────────┘  └───────────────────┘
```

### 数据流（一次完整对话）

```
用户提问 → recall(问题) → 搜索全局+会话记忆 → 拼入上下文
       → LLM 生成回答
       → observe(本轮消息) → LLM 检测信号
         → 有信号: 提取要点 → 概括 → 存入 ChromaDB
         → 无信号: 跳过
       → 检测到 "记住XXX": memorize(XXX) → 直接存储
       → (会话结束) 兜底 summarize → 存入
```

## 数据模型

### ChromaDB metadata 结构

```python
{
    "type": "preference" | "conclusion" | "fact" | "manual",  # 记忆类型
    "scope": "global" | "session",          # 作用域
    "source_conv_id": "uuid-xxx",           # 来源会话（session级必有）
    "count": 3,                              # 被合并/强化次数
    "importance": 0.8,                       # 重要性评分 0-1
    "created_at": "2026-06-05T10:00:00Z",
    "updated_at": "2026-06-05T12:00:00Z",
}
```

### 记忆类型

| 类型 | 说明 | 默认作用域 | 示例 |
|------|------|-----------|------|
| `preference` | 用户偏好、习惯、风格 | global | "偏好 Python 异步模式" |
| `fact` | 可复用的事实信息 | global | "项目使用 ChromaDB+SQLite" |
| `conclusion` | 分析结论、决策 | session/global | "决定使用方案B实现记忆" |
| `manual` | 用户明确要求记住 | 用户指定 | "API key=xxx" |

## MemoryManager API

```python
class MemoryManager:
    def __init__(self, config: dict, llm: LLMAdapter)

    # —— 核心方法 ——
    def recall(self, query: str, conv_id: str | None = None,
               top_k: int = 5) -> list[Memory]:
        """搜索相关记忆，返回排序后的记忆列表。"""

    def observe(self, messages: list[dict], conv_id: str) -> int:
        """分析本轮对话，主动发现需要记忆的要点。返回新记忆数。"""

    def memorize(self, content: str, mem_type: str = "manual",
                 scope: str = "global", metadata: dict | None = None) -> str:
        """被动记忆：用户明确要求记住的信息。返回记忆 ID。"""

    # —— 维护方法 ——
    def consolidate(self) -> int:
        """合并相似记忆，删除冗余。返回合并数。"""

    def export_md(self) -> Path:
        """导出所有记忆为 markdown 文件。"""
```

## 核心流程详解

### 1. observe() 主动记忆

```
本轮对话消息
    │
    ▼
┌─────────────────────────────┐
│ ① 信号检测 (LLM prompt)      │
│   判断对话是否包含需要跨会话   │
│   保留的重要信息              │
│   返回: {has_signal, items}  │
└───────────┬─────────────────┘
            │ has_signal=false → 跳过
            ▼ has_signal=true
┌─────────────────────────────┐
│ ② 信息提取 + 概括 (LLM)      │
│   对每条 item:               │
│   - 提取核心内容（≤200字）    │
│   - 分类到四种记忆类型        │
│   - 判断作用域: global/      │
│     session                  │
│   - 评估重要性: 0-1          │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│ ③ 去重 + 存储 (MemoryStore)  │
│   相似度 ≥ 0.85 → 合并强化    │
│   相似度 < 0.85 → 新增        │
└─────────────────────────────┘
```

**信号检测的关键触发场景**：
- 用户明确表达偏好/习惯（"我更喜欢..."、"我一般..."）
- 做出决策/结论（"我们决定..."、"最终方案是..."）
- 陈述可复用的事实（"项目用的是..."、"API 地址是..."）
- 表达长期目标（"我想实现..."、"目标是..."）

### 2. recall() 记忆注入

```
用户当前问题
    │
    ▼
┌─────────────────────────────┐
│ ① 搜索 ChromaDB              │
│   - global 记忆: 始终搜索     │
│   - 当前会话级记忆: 搜索      │
│   返回 top_k=10 候选          │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ ② 排序                       │
│   score = α·similarity       │
│         + β·importance       │
│         + γ·recency_bonus    │
│   取 top 5                   │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ ③ 分流注入                   │
│   全局稳定记忆                │
│     → system prompt 前缀     │
│   会话结论                    │
│     → 上下文消息              │
└─────────────────────────────┘
```

注入后的 messages 结构：

```python
messages = [
    {"role": "system", "content": system_prompt + "\n## 用户历史信息\n- ..."},
    {"role": "system", "content": "## 上次讨论结论\n- ..."},
    *history,          # 最近 N 条对话
    {"role": "user", "content": question},
]
```

### 3. memorize() 被动记忆

用户在对话中说"记住 XXX"、"别忘了 XXX"、"帮我记一下 XXX"时触发。使用 LLM 做意图检测（非关键词匹配），识别到"要求记忆"意图后，提取核心内容 → LLM 概括 → 去重存储。与 observe() 的区别：被动记忆跳过信号检测步骤，直接进入提取+存储流程。

### 4. consolidate() 记忆合并

定期运行（会话结束时），对 ChromaDB 中所有记忆两两比较相似度，≥0.85 的合并为一条概括性记忆。

## Markdown 导出

### 目录结构

```
data/memories/
├── global/
│   ├── preferences.md       # 用户偏好
│   ├── facts.md             # 已知事实
│   └── conclusions.md       # 跨会话结论
├── sessions/
│   ├── {conv_id_1}.md       # 每个会话一个文件
│   └── {conv_id_2}.md
└── INDEX.md                 # 记忆总览索引
```

### 格式示例

```markdown
# 用户偏好

> 最后更新: 2026-06-05 12:00 | 共 5 条

## 编码风格
- **偏好 Python 异步模式处理 I/O 操作** — 出现 3 次 | ⭐ 0.85
- 使用 type hints + pydantic 做数据验证 — 出现 2 次 | ⭐ 0.72

## 决策模式
- **决策时偏向选择最全面的方案** — 出现 4 次 | ⭐ 0.91
```

### 导出触发

| 触发方式 | 说明 |
|---------|------|
| 每次存储后增量更新 | memorize()/observe() 存入后自动更新对应 md |
| 手动导出 API | POST /api/v1/memories/export 全量重写 |
| consolidate() 后 | 合并后重写受影响文件 |

### 增量策略

1. 新记忆 → 追加到对应类型/会话的 md 文件
2. 合并/更新记忆 → 重写该条所在段落
3. 删除记忆 → 从 md 中移除对应行

## API 设计

### 端点列表

| 方法 | 路径 | 说明 | 变更 |
|------|------|------|------|
| POST | /api/v1/memories/remember | 被动记忆 | 修改：增加 scope 参数 |
| GET | /api/v1/memories/search | 搜索记忆 | 修改：增加 scope 过滤 |
| GET | /api/v1/memories | 列出记忆 | 修改：增加 scope 过滤 |
| POST | /api/v1/memories/observe | 主动触发分析 | 新增 |
| POST | /api/v1/memories/consolidate | 合并相似记忆 | 新增 |
| POST | /api/v1/memories/export | 导出 md | 新增 |
| GET | /api/v1/memories/export | 获取导出文件列表 | 新增 |
| DELETE | /api/v1/memories/{id} | 删除记忆 | 不变 |

### 新增端点详情

```
POST /api/v1/memories/observe
  触发主动记忆分析
  Request:  {"conversation_id": "uuid"}
  Response: {"code": "OK", "data": {"new_memories": 3, "skipped": 0}}

POST /api/v1/memories/consolidate
  合并相似记忆
  Request:  {} 或 {"dry_run": true}
  Response: {"code": "OK", "data": {"merged": 5, "deleted": 5}}

POST /api/v1/memories/export
  全量导出 md 文件
  Request:  {} 或 {"scope": "global"}
  Response: {"code": "OK", "data": {"path": "data/memories/", "files": 8}}

GET /api/v1/memories/export
  查看导出文件列表
  Response: {"code": "OK", "data": {"files": ["global/preferences.md", ...]}}
```

## 配置项

在 `server/config.py` 的 `DEFAULTS` 中新增：

```python
"memory_enabled": "true",              # 是否启用记忆系统
"memory_auto_observe": "true",         # 是否自动分析每轮对话
"memory_observe_interval": "1",        # 每隔 N 轮对话触发一次 observe
"memory_recall_top_k": "5",            # 每次对话注入的记忆数
"memory_dedup_threshold": "0.85",      # 去重相似度阈值
"memory_export_auto": "true",          # 是否自动增量导出 md
"memory_export_dir": "",               # 导出目录（空=默认 data/memories/）
"memory_consolidate_auto": "true",     # 是否自动定期合并
"memory_max_per_recall": "5",          # 单次召回最大记忆数
```

## 文件变更清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `server/services/memory_manager.py` | 新增 | MemoryManager 核心编排 |
| `server/services/memory_store.py` | 修改 | 增加 scope + importance 字段 |
| `server/services/memory.py` | 修改 | 重构为调用 MemoryManager |
| `server/services/memory_md_exporter.py` | 新增 | Markdown 导出器 |
| `server/routers/chat.py` | 修改 | 集成 recall + observe |
| `server/routers/memories.py` | 修改 | 新增 API 端点 |
| `server/config.py` | 修改 | 新增记忆配置默认值 |

## 测试策略

### 测试文件

| 文件 | 层级 | 覆盖内容 |
|------|------|---------|
| `test_memory_manager.py` | 单元 | MemoryManager 各方法（mock LLM + store） |
| `test_memory_store.py` | 单元 | CRUD + scope 过滤 + 去重 |
| `test_memory_md_exporter.py` | 单元 | MD 导出/增量更新/格式正确性 |
| `test_chat_memory.py` | 集成 | chat.py recall→注入→observe 完整流程 |
| `test_memories_api.py` | 集成 | 新增 API 端点行为 |
| `test_rag.py` (扩展) | 端到端 | 记忆注入后问答质量验证 |

### 关键测试用例

```
test_observe_detects_preference    — 用户说"我更喜欢用异步"→自动生成 preference
test_observe_detects_decision      — 用户说"我们决定用方案B"→自动生成 conclusion
test_observe_no_signal_skips       — 闲聊无信号→不产生记忆
test_memorize_passive_store        — "记住这个"→manual 类型存储
test_recall_injects_global         — recall() 返回全局记忆拼入 system prompt
test_recall_injects_session        — recall() 返回会话结论作为上下文消息
test_md_export_incremental         — 新增记忆后 md 文件增量更新
test_md_export_format              — 导出 md 格式符合设计
test_consolidate_merges_similar    — 两条相似度>阈值的记忆合并
test_dedup_on_store                — 存入重复记忆时合并而非新增
```

## 实施计划

| 阶段 | 内容 | 依赖 |
|------|------|------|
| Phase 1 | MemoryStore 增强（scope + importance） | 无 |
| Phase 2 | MemoryManager 核心（recall + memorize） | Phase 1 |
| Phase 3 | MemoryManager.observe() 主动记忆 | Phase 2 |
| Phase 4 | chat.py 集成（recall 注入 + observe 触发） | Phase 2, 3 |
| Phase 5 | MemoryMDExporter 导出 | Phase 1 |
| Phase 6 | consolidate() 记忆合并 | Phase 2 |
| Phase 7 | API 端点更新 + 新增 | Phase 2-6 |
| Phase 8 | 测试 | Phase 1-7 |
