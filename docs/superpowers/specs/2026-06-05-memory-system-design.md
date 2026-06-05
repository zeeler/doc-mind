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
       → observe(本轮消息) → 一次 LLM 调用同时完成:
          ① 信号检测 (has_signal?)
          ② 信息提取 (items: [...])
          ③ 被动记忆意图检测 ("记住XXX" → manual 类型)
         → has_signal=true 或 有 manual items → 去重 + 存入 ChromaDB
         → has_signal=false 且无 manual items → 跳过
       → (会话空闲超时) 兜底 → 对该会话剩余未分析消息做 observe
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
    "expires_at": "2026-09-05T00:00:00Z",   # 过期时间（session级默认30天，global无过期）
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

### 1. observe() 主动记忆（含被动记忆检测）

```
本轮对话消息
    │
    ▼
┌─────────────────────────────────────┐
│ ① 单次 LLM 调用（信号检测+提取+意图） │
│   Prompt: "分析以下对话，完成三项任务： │
│   任务A: 是否包含需要跨会话保留的信息？ │
│   任务B: 如果是，提取+概括+分类       │
│   任务C: 用户是否明确要求记住某事？    │
│                                     │
│   返回 JSON:                         │
│   {                                 │
│     "has_signal": true|false,       │
│     "items": [                      │
│       {                             │
│         "content": "概括内容≤200字",  │
│         "type": "preference|         │
│           conclusion|fact|manual",  │
│         "scope": "global|session",  │
│         "importance": 0.8           │
│       }                             │
│     ]                               │
│   }"                                │
└─────────────┬───────────────────────┘
              │ has_signal=false && items为空 → 跳过
              ▼ 否则
┌─────────────────────────────────────┐
│ ② 去重 + 存储 (MemoryStore)          │
│   每条 item:                         │
│   相似度 ≥ 0.85 → 合并强化 count+1    │
│   相似度 < 0.85 → 新增               │
└─────────────────────────────────────┘
```

**信号检测的关键触发场景**：
- 用户明确表达偏好/习惯（"我更喜欢..."、"我一般..."）
- 做出决策/结论（"我们决定..."、"最终方案是..."）
- 陈述可复用的事实（"项目用的是..."、"API 地址是..."）
- 表达长期目标（"我想实现..."、"目标是..."）
- **被动记忆请求**（"记住 XXX"、"别忘了 XXX"）→ type=manual

**observe 触发频率**：由 `memory_observe_interval` 控制，默认每 3 轮对话触发一次（累计收集 3 轮消息后一次性分析），而非每轮触发。

**会话结束兜底**：同一会话超过 30 分钟无新消息，且该会话有 ≥2 条未被 observe 的消息时，后台线程对剩余消息执行 observe。替换现有的 `summarize_conversation()` 函数。

### 2. recall() 记忆注入

```
用户当前问题
    │
    ▼
┌─────────────────────────────────┐
│ ① 搜索 ChromaDB                  │
│   - global 记忆: 始终搜索         │
│   - 当前会话级记忆: 搜索          │
│   - 过滤已过期的记忆              │
│   返回 top_k=10 候选              │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│ ② 排序（加权分数）               │
│   score = 0.5 × similarity       │
│         + 0.3 × importance       │
│         + 0.2 × recency_bonus    │
│                                  │
│   recency_bonus =                │
│     1 / (1 + days_since_update)  │
│                                  │
│   取 top_k=5                     │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│ ③ 合并为单条 system message      │
│   所有记忆拼入一个 system prompt  │
│   前缀（兼容 Anthropic API）:     │
│   "## 用户历史信息               │
│    - [偏好] ...                  │
│    - [事实] ...                  │
│   ## 相关讨论结论                │
│    - ..."                        │
└─────────────────────────────────┘
```

注入后的 messages 结构（单条 system message，兼容 Anthropic）：

```python
memory_context = "## 用户历史信息\n- 偏好: ...\n- 事实: ...\n## 相关讨论结论\n- ..."
messages = [
    {"role": "system", "content": system_prompt + "\n\n" + memory_context},
    *history,          # 最近 N 条对话
    {"role": "user", "content": question},
]
```

### 3. memorize() 被动记忆（API 调用）

当通过 API `POST /api/v1/memories/remember` 直接存储时使用，跳过 LLM 分析。对话中的被动记忆请求（"记住 XXX"）由 `observe()` 的信号检测 prompt 统一识别（type=manual），不单独调用 memorize。

```python
def memorize(self, content: str, mem_type: str = "manual",
             scope: str = "global", metadata: dict | None = None) -> str:
    """被动记忆：API 调用，直接存储已概括的内容。返回记忆 ID。"""
    # 1. 去重检查
    # 2. 存入 ChromaDB（不经过 LLM）
    # 3. 增量导出 md
```

### 4. consolidate() 记忆合并

使用 ChromaDB 原生查询预筛选，避免 O(n²)：

```
对每条记忆（或最近更新的 N 条记忆）:
  → store.search(memory.content, top_k=3)
  → 对返回的候选对，计算相似度
  → 相似度 ≥ 0.85 → 合并（保留 importance 最高的，合并内容，count 累加）
  → 相似度 < 0.85 → 跳过
```

同时清理 `expires_at < now()` 的过期记忆。

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

### 并发安全

使用 `threading.Lock` 按文件路径加锁，确保多个请求同时写入同一 md 文件时不会交错损坏：

```python
class MemoryMDExporter:
    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}  # file_path → Lock

    def _write_file(self, path: Path, content: str):
        with self._get_lock(path):
            path.write_text(content, encoding="utf-8")
```

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
  Response (正常): {"code": "OK", "data": {"merged": 5, "deleted": 3, "expired_cleaned": 2}}
  Response (dry_run): {"code": "OK", "data": {"pairs": [
      {"id_1": "mem-aaa", "id_2": "mem-bbb", "content_1": "...", "content_2": "...", "score": 0.91},
      {"id_1": "mem-ccc", "id_2": "mem-ddd", "content_1": "...", "content_2": "...", "score": 0.88}
    ], "total_pairs": 2, "expired_candidates": 3}}

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
"memory_auto_observe": "true",         # 是否自动分析对话（关闭后仅手动/API触发）
"memory_observe_interval": "3",        # 每隔 N 轮对话触发一次 observe（≥2）
"memory_recall_top_k": "5",            # 每次对话注入的记忆数
"memory_dedup_threshold": "0.85",      # 去重相似度阈值（依赖 cosine 距离）
"memory_export_auto": "true",          # 是否自动增量导出 md
"memory_export_dir": "",               # 导出目录（空=默认 data/memories/）
"memory_consolidate_auto": "true",     # 是否自动定期合并
"memory_max_per_recall": "5",          # 单次召回最大记忆数
"memory_session_idle_timeout": "30",   # 会话空闲超时（分钟），触发兜底 observe
"memory_session_expire_days": "30",    # 会话级记忆过期天数（global 记忆永不过期）
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

| 阶段 | 内容 | 关键修复/要点 |
|------|------|-------------|
| Phase 1 | MemoryStore 增强 | **修复 #1**: 显式设置 `hnsw:space: "cosine"` 确保去重阈值有效；增加 scope/importance/expires_at 字段；过期记忆过滤 |
| Phase 2 | MemoryManager 核心（recall + memorize） | **修复 #4**: recall 排序权重 α=0.5, β=0.3, γ=0.2；**修复 #5**: 单条 system message 兼容 Anthropic；memorize 为 API 直存模式 |
| Phase 3 | MemoryManager.observe() | **修复 #8**: 单次 LLM 调用完成信号检测+提取+被动记忆意图检测；**修复 #6**: 替换 summarize_conversation；**修复 #12**: 被动记忆意图合入 observe prompt；**修复 #2**: 每 3 轮触发；**修复 #7**: 会话空闲 30min 兜底 |
| Phase 4 | chat.py 集成 | **修复 #5**: 注入时合并为单条 system message；recall 注入 + observe 触发 |
| Phase 5 | MemoryMDExporter | **修复 #9**: 按文件路径加 threading.Lock |
| Phase 6 | consolidate() | **修复 #3**: ChromaDB query top-3 预筛选替代 O(n²)；**修复 #10**: 清理过期记忆 |
| Phase 7 | API 端点更新 | **修复 #11**: dry_run 返回 pairs 详情 |
| Phase 8 | 测试 | 覆盖所有修复点的关键测试用例 |
