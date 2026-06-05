# 记忆系统设计审核意见

**审核对象**: [docs/superpowers/specs/2026-06-05-memory-system-design.md](/Users/terry/Documents/cc_projects/my_agent1/docs/superpowers/specs/2026-06-05-memory-system-design.md)
**审核时间**: 2026-06-05

---

## 严重问题

### 1. MemoryStore 底层距离度量与去重阈值不兼容（继承自现有 bug）

设计沿用 `memory_dedup_threshold: "0.85"`，但现有 [memory_store.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/memory_store.py:29) 的 `score = 1.0 - distance` 转换假设余弦距离，而 ChromaDB 默认使用 L2。L2 距离可远大于 1.0，score 变负数，0.85 阈值永远达不到，去重彻底失效。

**建议**：Phase 1 显式规定创建 collection 时设置 `hnsw:space: "cosine"`。

---

### 2. `observe()` 默认每轮触发，LLM 调用量翻倍

`memory_observe_interval` 默认值 1，意味着每次 `/ask` 都额外调用 LLM（信号检测 + 可能的信息提取）。即使无信号，信号检测 prompt 仍然消耗 token。对于一个 20 轮的对话，这就是 20 次额外 LLM 调用。

**建议**：`observe_interval` 默认值至少设为 3，或改为"每 N 条消息"而非"每 N 轮"。

---

### 3. `consolidate()` 的全量 O(n²) 比较不可扩展

> 对 ChromaDB 中所有记忆两两比较相似度

记忆数增长到 500 条时就是 125,000 次相似度比较。没有分批策略、没有增量合并（只合并最近 N 条）、没有 ChromaDB 原生 `query` 的 top-k 预筛选。

**建议**：改为"对每条记忆，搜索其 top-3 最相似记忆，仅对候选对做合并判断"。

---

## 中等问题

### 4. `recall()` 排序公式的三个权重未给出默认值

```python
score = α·similarity + β·importance + γ·recency_bonus
```

`similarity` 在 0–1 范围，`importance` 在 0–1，`recency_bonus` 的范围未定义。三个值量纲一致所以可行，但 α、β、γ 的默认值缺失。

**建议**：明确 α=0.5, β=0.3, γ=0.2，`recency_bonus = 1 / (1 + days_since_update)`。

---

### 5. 两个 system message 在 Anthropic 格式下可能不被支持

设计中的注入结构：

```python
messages = [
    {"role": "system", "content": system_prompt + "\n## 用户历史信息\n- ..."},
    {"role": "system", "content": "## 上次讨论结论\n- ..."},
    ...
]
```

OpenAI API 允许多个 system message，但 Anthropic Messages API 只接受单个 `system` 参数（不在 messages 数组里）。现有的 [LLMAdapter._to_anthropic_messages](/Users/terry/Documents/cc_projects/my_agent1/server/services/llm.py:173-180) 按 message 逐个转换，两个 system message 会导致第二个覆盖第一个或 API 报错。

**建议**：合并为单条 system message，或增加 provider 感知的注入逻辑。

---

### 6. `observe()` 和现有 `summarize_conversation()` 功能高度重叠

现有 [memory.py](/Users/terry/Documents/cc_projects/my_agent1/server/services/memory.py:83) 已有 `summarize_conversation(conv_id)`，同样做"提取偏好/结论/事实 → 分类 → 存储"。新设计增加了信号检测步骤，但核心提取逻辑完全重复。设计文档没有说明是要替换、增强还是一起保留。

**建议**：将 `summarize_conversation` 重构为 `observe()` 的兜底实现，去掉独立函数。

---

### 7. "会话结束兜底"的触发时机未定义

> （会话结束）兜底 summarize → 存入

HTTP 是无状态的——什么算"会话结束"？用户 5 分钟不发言？关闭浏览器标签？下次创建新会话时？如果触发时机不明确，这个兜底逻辑可能永远不执行。

**建议**：明确定义为"同一会话超过 N 分钟无新消息，且该会话有 ≥2 条未被 observe 的消息时，后台线程触发兜底"。

---

## 轻度问题

### 8. `observe()` 中的两次 LLM 调用可以合并

信号检测和信息提取是两个独立 prompt，但可以合并为一次调用：让 LLM 直接返回 `{has_signal: bool, items: [...]}`，无信号时 items 为空。减少一次 API 调用延迟。

---

### 9. Markdown 增量导出的并发安全缺失

多个请求同时 `memorize()` → 同一 md 文件被并发写入，缺少文件锁。

**建议**：使用 `threading.Lock` 按文件路径加锁，或在 MemoryMDExporter 中实现单文件写入队列。

---

### 10. 会话级记忆的过期清理未定义

session 级记忆在 ChromaDB 中会无限累积。设计没有清理策略：是会话删除时级联清理？N 天后自动过期？

**建议**：在 Phase 1 的 metadata 中增加 `expires_at`，并在 consolidate 时清理过期记忆。

---

### 11. API 的 `dry_run` 语义不完整

`POST /api/v1/memories/consolidate` 的 `dry_run: true` 返回什么？只返回计数还是返回具体的"哪些记忆对会被合并"？调用方无法预览合并结果。

**建议**：dry_run 返回 `{"pairs": [{"id_1": "...", "id_2": "...", "score": 0.91}, ...]}`。

---

### 12. 被动记忆的意图检测归属不清晰

设计说 `memorize()` 跳过信号检测，直接进入提取+存储。那谁负责检测用户是否说了"记住 XXX"？如果在 chat.py 中预先用正则匹配，那和设计的"使用 LLM 做意图检测（非关键词匹配）"矛盾；如果在 chat.py 中多调一次 LLM 做意图检测，成本太高。

**建议**：在 `observe()` 的信号检测 prompt 中增加一个"记忆请求"信号类型，复用同一次 LLM 调用。

---

## 总结

| 严重程度 | 数量 | 关键问题 |
|---------|------|---------|
| 严重 | 3 | L2 距离导致去重失效、observe 每轮调用 LLM 成本过高、consolidate O(n²) 不可扩展 |
| 中等 | 4 | 排序权重缺失、Anthropic 多 system message 不兼容、与现有 summarize_conversation 重复、会话结束触发时机未定义 |
| 轻度 | 5 | 两次 LLM 调用可合并、md 导出并发不安全、会话记忆无过期策略、dry_run 语义不足、被动记忆检测归属不清 |

设计整体方向正确，最大的风险是 #1（去重基石失效）和 #2（默认每轮烧 token）。Phase 1 建议优先修复 MemoryStore 的距离度量，Phase 4 建议将 `observe_interval` 默认值改为 3 并在信号检测 prompt 中合并被动记忆意图检测。
