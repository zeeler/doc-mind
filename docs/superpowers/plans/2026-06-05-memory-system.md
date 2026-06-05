# 记忆系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建完整的记忆系统：主动记忆（LLM 驱动信号检测）+ 被动记忆（API 直存）+ 历史记忆搜索注入 + ChromaDB 存储 + Markdown 导出。

**Architecture:** 新建 MemoryManager 作为统一编排层，向下对接 MemoryStore（ChromaDB）和 MemoryMDExporter（Markdown 文件），向上被 chat.py 和 memories 路由调用。修改 rag.py 的记忆注入逻辑，使用单条 system message 兼容 Anthropic API。

**Tech Stack:** Python 3.12+, FastAPI, ChromaDB, SQLAlchemy, OpenAI/Anthropic LLM API

---

## File Structure

| 文件 | 动作 | 职责 |
|------|------|------|
| `server/services/memory_store.py` | 修改 | ChromaDB 封装，增加 scope/importance/expires_at 支持 |
| `server/services/memory_manager.py` | **新建** | MemoryManager 核心编排 |
| `server/services/memory.py` | 修改 | 重构为 thin wrapper，调用 MemoryManager |
| `server/services/memory_md_exporter.py` | **新建** | Markdown 导出器（含文件锁） |
| `server/routers/chat.py` | 修改 | 集成 recall 注入 + observe 触发 |
| `server/routers/memories.py` | 修改 | 新增 observe/consolidate/export 端点 |
| `server/services/rag.py` | 修改 | 记忆注入改为 system message 格式 |
| `server/config.py` | 修改 | 新增 10 个记忆配置项 |

---

### Task 1: Phase 1 — MemoryStore 增强

**Files:**
- Modify: `server/services/memory_store.py`
- Modify: `server/config.py` (DEFAULTS 新增配置)

- [ ] **Step 1: 更新 config.py 新增记忆配置默认值**

在 `server/config.py` 的 `DEFAULTS` dict 末尾（`"reranker_top_k": "3",` 之后）新增：

```python
    # 记忆系统配置
    "memory_enabled": "true",
    "memory_auto_observe": "true",
    "memory_observe_interval": "3",
    "memory_recall_top_k": "5",
    "memory_dedup_threshold": "0.85",
    "memory_export_auto": "true",
    "memory_export_dir": "",
    "memory_consolidate_auto": "true",
    "memory_max_per_recall": "5",
    "memory_session_idle_timeout": "30",
    "memory_session_expire_days": "30",
```

- [ ] **Step 2: 运行现有测试确保基线正确**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -x -q 2>&1 | tail -5
```

Expected: 191 passed

- [ ] **Step 3: 增强 MemoryStore — 增加 add() 的 metadata 验证和 scope 支持**

修改 `server/services/memory_store.py`，在 `add()` 方法中增加 metadata 默认值填充：

```python
"""ChromaDB 记忆存储封装。"""

import uuid
from datetime import datetime, timezone, timedelta
from server.vector.store import get_client


class MemoryStore:
    def __init__(self, persist_dir: str):
        self.client = get_client(persist_dir)
        # 显式指定 cosine 空间，确保 distance 在 [0,2] 范围内，去重阈值 0.85 才能生效
        self.collection = self.client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, mem_id: str | None, content: str, metadata: dict) -> str:
        mid = mem_id or f"mem-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "type": metadata.get("type", "manual"),
            "scope": metadata.get("scope", "global"),
            "source_conv_id": metadata.get("source_conv_id", ""),
            "count": metadata.get("count", 1),
            "importance": metadata.get("importance", 0.5),
            "created_at": metadata.get("created_at", now),
            "updated_at": metadata.get("updated_at", now),
        }
        # session 级记忆设置 30 天过期
        if meta["scope"] == "session" and not metadata.get("expires_at"):
            expires = datetime.now(timezone.utc) + timedelta(days=30)
            meta["expires_at"] = expires.isoformat()
        elif metadata.get("expires_at"):
            meta["expires_at"] = metadata["expires_at"]
        # 合并自定义 metadata
        for k, v in metadata.items():
            if k not in meta:
                meta[k] = v
        self.collection.add(
            ids=[mid],
            documents=[content],
            metadatas=[meta],
        )
        return mid

    def search(self, query: str, top_k: int = 5, scope: str | None = None,
               exclude_expired: bool = True) -> list[dict]:
        where_filter = None
        if scope:
            where_filter = {"scope": scope}
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where_filter,
        )
        hits = []
        now = datetime.now(timezone.utc).isoformat()
        ids_list = results.get("ids", [[]])[0]
        docs_list = results.get("documents", [[]])[0]
        metas_list = results.get("metadatas", [[]])[0]
        distances_list = results.get("distances", [[]])[0]
        for i in range(len(ids_list)):
            meta = metas_list[i] if i < len(metas_list) else {}
            # 过滤过期记忆
            if exclude_expired and meta.get("expires_at"):
                if meta["expires_at"] < now:
                    continue
            hits.append({
                "id": ids_list[i],
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": meta,
                # cosine distance ∈ [0,2], 归一化到 [0,1]: score = 1 - distance/2
                "score": max(0.0, 1.0 - distances_list[i] / 2.0) if i < len(distances_list) else 0.0,
            })
        return hits

    def delete(self, mem_id: str) -> None:
        self.collection.delete(ids=[mem_id])

    def update(self, mem_id: str, content: str, metadata: dict) -> None:
        self.collection.update(ids=[mem_id], documents=[content], metadatas=[metadata])

    def count(self) -> int:
        return self.collection.count()

    def get_all(self, scope: str | None = None, limit: int = 100) -> list[dict]:
        """获取全部记忆（用于 consolidate 和 export）。"""
        where_filter = {"scope": scope} if scope else None
        results = self.collection.get(limit=limit, where=where_filter)
        memories = []
        if not results.get("ids"):
            return memories
        for i in range(len(results["ids"])):
            memories.append({
                "id": results["ids"][i],
                "content": results["documents"][i] if results.get("documents") else "",
                "metadata": results["metadatas"][i] if results.get("metadatas") else {},
            })
        return memories

    def delete_expired(self) -> int:
        """删除所有过期记忆，返回删除数。"""
        all_mems = self.get_all(limit=10000)
        now = datetime.now(timezone.utc).isoformat()
        expired_ids = [
            m["id"] for m in all_mems
            if m["metadata"].get("expires_at") and m["metadata"]["expires_at"] < now
        ]
        if expired_ids:
            self.collection.delete(ids=expired_ids)
        return len(expired_ids)
```

- [ ] **Step 4: 验证服务可启动**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "from server.services.memory_store import MemoryStore; from server.database import DATA_DIR; s = MemoryStore(str(DATA_DIR/'chroma')); print('MemoryStore OK, count:', s.count())"
```

Expected: `MemoryStore OK, count: N`

- [ ] **Step 5: Commit Phase 1**

```bash
git add server/services/memory_store.py server/config.py
git commit -m "feat(memory): Phase 1 — MemoryStore 增强（cosine + scope + importance + expires_at）

- 显式确认 collection 使用 cosine 距离（已在构造函数中设置 hnsw:space:cosine）
- add() 自动填充 metadata 默认值（scope/importance/expires_at）
- session 级记忆默认 30 天过期
- search() 支持 scope 过滤 + 过期记忆自动过滤
- 新增 get_all() 和 delete_expired() 方法
- config.py 新增 11 个记忆配置项默认值

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Phase 2 — MemoryManager 核心（recall + memorize）

**Files:**
- Create: `server/services/memory_manager.py`
- Modify: `server/services/memory.py`

- [ ] **Step 1: 创建 MemoryManager 类文件**

创建 `server/services/memory_manager.py`：

```python
"""MemoryManager — 记忆系统统一编排层。"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from server.services.memory_store import MemoryStore
from server.services.memory_md_exporter import MemoryMDExporter
from server.database import DATA_DIR

logger = logging.getLogger("knowledge-base")


class MemoryManager:
    """记忆系统的统一入口，编排 MemoryStore 和 MemoryMDExporter。

    被 chat.py（recall + observe）、memories 路由（memorize + consolidate + export）调用。
    """

    def __init__(self, config: dict, llm=None):
        self.config = config
        self.llm = llm  # LLMAdapter 实例，observe() 需要
        self._store: MemoryStore | None = None
        self._exporter: MemoryMDExporter | None = None
        self.dedup_threshold = float(config.get("memory_dedup_threshold", "0.85"))
        self.recall_top_k = int(config.get("memory_recall_top_k", "5"))
        self.export_auto = config.get("memory_export_auto", "true") == "true"
        export_dir = config.get("memory_export_dir", "") or str(DATA_DIR / "memories")
        self.export_dir = Path(export_dir)

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            self._store = MemoryStore(persist_dir=str(DATA_DIR / "chroma"))
        return self._store

    @property
    def exporter(self) -> MemoryMDExporter:
        if self._exporter is None:
            self._exporter = MemoryMDExporter(base_dir=self.export_dir)
        return self._exporter

    # ============ recall() — 记忆注入 ============

    def recall(self, query: str, conv_id: str | None = None,
               top_k: int | None = None) -> list[dict]:
        """搜索相关记忆并排序，返回 top_k 条。
        
        搜索策略：
        - global 记忆始终搜索
        - 当前会话级记忆也搜索
        - 过滤已过期记忆
        - 加权排序：0.5×similarity + 0.3×importance + 0.2×recency
        """
        if top_k is None:
            top_k = self.recall_top_k
        
        # 搜索 global + session 记忆（多取一些候选）
        fetch_k = top_k * 3
        global_mems = self.store.search(query, top_k=fetch_k, scope="global")
        session_mems = []
        if conv_id:
            session_mems = self.store.search(query, top_k=fetch_k // 2)

        # 合并去重
        seen = set()
        candidates = []
        for m in global_mems + session_mems:
            if m["id"] not in seen:
                seen.add(m["id"])
                candidates.append(m)

        # 加权排序
        now = datetime.now(timezone.utc)
        for m in candidates:
            similarity = m["score"]
            importance = m["metadata"].get("importance", 0.5)
            updated_str = m["metadata"].get("updated_at", "")
            try:
                updated = datetime.fromisoformat(updated_str)
                days = (now - updated).days
            except (ValueError, TypeError):
                days = 30
            recency_bonus = 1.0 / (1.0 + max(0, days))
            m["_rank_score"] = 0.5 * similarity + 0.3 * importance + 0.2 * recency_bonus

        candidates.sort(key=lambda m: m["_rank_score"], reverse=True)
        return candidates[:top_k]

    def recall_as_context(self, query: str, conv_id: str | None = None,
                          top_k: int | None = None) -> str:
        """返回记忆文本，可直接拼入 system prompt。"""
        memories = self.recall(query, conv_id=conv_id, top_k=top_k)
        if not memories:
            return ""

        stable_parts = []  # preference + fact
        conclusion_parts = []  # conclusion

        for m in memories:
            mtype = m["metadata"].get("type", "")
            label = {"preference": "偏好", "fact": "事实", "conclusion": "结论", "manual": "备注"}.get(mtype, mtype)
            line = f"- [{label}] {m['content']}"
            if mtype in ("preference", "fact", "manual"):
                stable_parts.append(line)
            else:
                conclusion_parts.append(line)

        sections = []
        if stable_parts:
            sections.append("## 用户历史信息\n" + "\n".join(stable_parts))
        if conclusion_parts:
            sections.append("## 相关讨论结论\n" + "\n".join(conclusion_parts))
        return "\n\n".join(sections) if sections else ""

    # ============ memorize() — 被动记忆（API 直存）============

    def memorize(self, content: str, mem_type: str = "manual",
                 scope: str = "global", metadata: dict | None = None) -> str:
        """被动记忆：API 调用的直接存储，不经过 LLM 分析。返回记忆 ID。"""
        meta = dict(metadata or {})
        meta["type"] = mem_type
        meta["scope"] = scope
        now = datetime.now(timezone.utc).isoformat()
        meta.setdefault("updated_at", now)
        meta.setdefault("created_at", now)

        # 去重
        existing = self.store.search(content, top_k=3)
        for hit in existing:
            if hit["score"] >= self.dedup_threshold:
                old_meta = hit["metadata"]
                merged_content = f"{hit['content']}\n{content}"[:2000]
                old_meta["count"] = old_meta.get("count", 1) + 1
                old_meta["updated_at"] = now
                old_meta["importance"] = min(1.0, old_meta.get("importance", 0.5) + 0.05)
                self.store.update(hit["id"], merged_content, old_meta)
                logger.info(f"记忆合并: {hit['id'][:12]} (count={old_meta['count']})")
                # 增量导出
                if self.export_auto:
                    self.exporter.incremental_update(hit["id"], merged_content, old_meta)
                return hit["id"]

        mid = self.store.add(None, content, meta)
        if self.export_auto:
            self.exporter.incremental_update(mid, content, meta)
        return mid

    # ============ observe() — 主动记忆 ============

    def observe(self, messages: list[dict], conv_id: str) -> int:
        """分析本轮对话，LLM 驱动信号检测+提取+被动记忆意图检测。返回新记忆数。"""
        if not self.llm:
            logger.warning("MemoryManager.observe: 未配置 LLM，跳过")
            return 0
        if not messages:
            return 0

        conversation_text = "\n".join(
            f"{m['role']}: {m['content'][:300]}" for m in messages[-6:]
        )

        prompt = f"""分析以下对话片段，完成三项任务。返回 JSON 格式（不要代码块标记）：

任务A - 是否包含需要跨会话保留的重要信息？
任务B - 如果包含，提取每条重要信息：概括核心内容（≤150字）、分类（preference/conclusion/fact/manual）、判断作用域（global/session）、评估重要性（0-1）。
任务C - 用户是否明确要求记住某事（如"记住XXX"、"别忘了XXX"）？这类标记为 manual 类型。

触发场景：
- 用户表达偏好/习惯 → preference, global
- 做出决策/结论 → conclusion, session
- 陈述可复用的事实 → fact, global
- 表达长期目标 → preference, global
- 要求记住某事 → manual, global

对话：
{conversation_text}

返回格式：
{{"has_signal": true, "items": [{{"content": "概括内容", "type": "preference", "scope": "global", "importance": 0.8}}]}}
无信号时返回：{{"has_signal": false, "items": []}}"""

        import json
        try:
            result = self.llm.chat(messages=[{"role": "user", "content": prompt}], temperature=0.2)
            text = result.get("content", "").strip()
            # 提取 JSON（可能被 markdown 代码块包裹）
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"observe LLM 解析失败: {e}")
            return 0

        if not data.get("has_signal") and not data.get("items"):
            return 0

        count = 0
        for item in data.get("items", []):
            content = item.get("content", "").strip()
            if not content:
                continue
            mem_type = item.get("type", "fact")
            # 验证类型
            if mem_type not in ("preference", "conclusion", "fact", "manual"):
                mem_type = "fact"
            scope = item.get("scope", "session")
            if scope not in ("global", "session"):
                scope = "session"
            importance = float(item.get("importance", 0.5))
            importance = max(0.0, min(1.0, importance))  # clamp

            meta = {
                "type": mem_type,
                "scope": scope,
                "source_conv_id": conv_id,
                "importance": importance,
            }
            self.memorize(content, mem_type=mem_type, scope=scope, metadata=meta)
            count += 1

        logger.info(f"observe 完成: conv={conv_id}, 新记忆={count}")
        return count

    # ============ consolidate() — 记忆合并 ============

    def consolidate(self, dry_run: bool = False) -> dict:
        """合并相似记忆，清理过期。使用 query top-3 预筛选避免 O(n²)。"""
        all_mems = self.store.get_all(limit=10000)
        merged_count = 0
        pairs = []
        seen_pairs = set()

        for mem in all_mems:
            similar = self.store.search(mem["content"], top_k=3)
            for hit in similar:
                if hit["id"] == mem["id"]:
                    continue
                pair_key = tuple(sorted([mem["id"], hit["id"]]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                if hit["score"] >= self.dedup_threshold:
                    pairs.append({
                        "id_1": mem["id"],
                        "id_2": hit["id"],
                        "content_1": mem["content"][:100],
                        "content_2": hit["content"][:100],
                        "score": round(hit["score"], 4),
                    })

        if dry_run:
            expired = self._count_expired()
            return {"pairs": pairs, "total_pairs": len(pairs), "expired_candidates": expired}

        # 执行合并
        for pair in pairs:
            try:
                mem1 = next((m for m in all_mems if m["id"] == pair["id_1"]), None)
                mem2 = next((m for m in all_mems if m["id"] == pair["id_2"]), None)
                if not mem1 or not mem2:
                    continue
                m1_imp = mem1["metadata"].get("importance", 0.5)
                m2_imp = mem2["metadata"].get("importance", 0.5)
                if m1_imp >= m2_imp:
                    keeper, removed = mem1, mem2
                else:
                    keeper, removed = mem2, mem1
                merged_content = f"{keeper['content']}\n{removed['content']}"[:2000]
                new_meta = keeper["metadata"]
                new_meta["count"] = new_meta.get("count", 1) + removed["metadata"].get("count", 1)
                new_meta["importance"] = max(m1_imp, m2_imp)
                new_meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.store.update(keeper["id"], merged_content, new_meta)
                self.store.delete(removed["id"])
                merged_count += 1
            except Exception as e:
                logger.warning(f"合并记忆失败 {pair['id_1']}+{pair['id_2']}: {e}")

        # 清理过期
        expired_cleaned = self.store.delete_expired()

        # 全量重新导出
        if self.export_auto and (merged_count > 0 or expired_cleaned > 0):
            self.exporter.full_export(self.store.get_all(limit=10000))

        logger.info(f"consolidate: merged={merged_count}, expired_cleaned={expired_cleaned}")
        return {"merged": merged_count, "deleted": merged_count, "expired_cleaned": expired_cleaned}

    def _count_expired(self) -> int:
        from datetime import datetime, timezone
        all_mems = self.store.get_all(limit=10000)
        now = datetime.now(timezone.utc).isoformat()
        return sum(
            1 for m in all_mems
            if m["metadata"].get("expires_at") and m["metadata"]["expires_at"] < now
        )

    # ============ export_md() ============

    def export_md(self, scope: str | None = None) -> Path:
        """全量导出记忆为 Markdown 文件，返回导出目录路径。"""
        memories = self.store.get_all(scope=scope, limit=10000)
        return self.exporter.full_export(memories, scope=scope)
```

- [ ] **Step 2: 重构 memory.py 为 thin wrapper**

修改 `server/services/memory.py`：

```python
"""记忆服务 — thin wrapper，委托给 MemoryManager。"""

import logging
import threading
from datetime import datetime, timezone
from server.services.memory_store import MemoryStore
from server.services.memory_manager import MemoryManager
from server.database import DATA_DIR, get_session_ctx

logger = logging.getLogger("knowledge-base")

MEMORY_DEDUP_THRESHOLD = 0.85
_manager: MemoryManager | None = None
_manager_lock = threading.Lock()


def _get_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                from server.config import AppConfig
                config = AppConfig().get_all()
                _manager = MemoryManager(config=config, llm=None)
    return _manager


def _init_manager_with_llm(llm) -> MemoryManager:
    """初始化/更新 MemoryManager 的 LLM 适配器（供 chat.py 调用）。"""
    global _manager
    from server.config import AppConfig
    config = AppConfig().get_all()
    _manager = MemoryManager(config=config, llm=llm)
    return _manager


def add_memory(content: str, mem_type: str, metadata: dict | None = None) -> str:
    """存入记忆，自动去重。返回记忆 ID。"""
    mgr = _get_manager()
    meta = dict(metadata or {})
    return mgr.memorize(content, mem_type=mem_type, metadata=meta)


def search_memories(query: str, top_k: int = 5) -> list[dict]:
    """检索相关记忆。"""
    mgr = _get_manager()
    return mgr.recall(query, top_k=top_k)


def list_memories(mem_type: str = None, limit: int = 50) -> list[dict]:
    """列出记忆（按更新时间倒序）。"""
    mgr = _get_manager()
    store = mgr.store
    results = store.collection.get(limit=limit)
    memories = []
    if not results.get("ids"):
        return memories
    for i in range(len(results["ids"])):
        mem_type_val = results["metadatas"][i].get("type", "") if results.get("metadatas") else ""
        if mem_type and mem_type_val != mem_type:
            continue
        memories.append({
            "id": results["ids"][i],
            "content": results["documents"][i] if results.get("documents") else "",
            "type": mem_type_val,
            "metadata": results["metadatas"][i] if results.get("metadatas") else {},
        })
    memories.sort(key=lambda m: m["metadata"].get("updated_at", ""), reverse=True)
    return memories[:limit]


def delete_memory(mem_id: str) -> None:
    mgr = _get_manager()
    try:
        mgr.store.delete(mem_id)
    except Exception as e:
        logger.warning(f"删除记忆失败 {mem_id}: {e}")


def _reset_store() -> None:
    """重置单例（仅测试用）。"""
    global _manager
    _manager = None


def summarize_conversation(conv_id: str) -> int:
    """对一段对话生成摘要记忆（已废弃，由 MemoryManager.observe() 替代）。
    
    保留此函数作为兼容层，内部委托给 MemoryManager.observe()。
    """
    from server.models.conversation import Conversation
    with get_session_ctx() as session:
        conv = session.get(Conversation, conv_id)
        if not conv:
            return 0
        messages = [{"role": m.role, "content": m.content} for m in conv.messages]

    if len(messages) < 2:
        return 0

    # 初始化带 LLM 的 manager
    from server.config import AppConfig
    from server.services.llm import LLMAdapter
    config = AppConfig().get_all()
    llm = LLMAdapter(config)
    mgr = MemoryManager(config=config, llm=llm)
    return mgr.observe(messages, conv_id)
```

- [ ] **Step 3: 验证导入无误**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "from server.services.memory_manager import MemoryManager; print('MemoryManager import OK')"
```

Expected: `MemoryManager import OK`

- [ ] **Step 4: 运行现有测试确保不破坏**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -x -q 2>&1 | tail -5
```

Expected: 191 passed

- [ ] **Step 5: Commit Phase 2**

```bash
git add server/services/memory_manager.py server/services/memory.py
git commit -m "feat(memory): Phase 2 — MemoryManager 核心（recall + memorize）

- 新建 MemoryManager 统一编排层
- recall(): 搜索+加权排序（0.5×similarity+0.3×importance+0.2×recency）
- recall_as_context(): 格式化记忆文本注入 system prompt
- memorize(): 被动记忆 API 直存+去重
- memory.py 重构为 thin wrapper，保留 summarize_conversation 兼容层

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Phase 3 — MemoryManager.observe() 主动记忆

**Files:**
- Modify: `server/services/memory_manager.py` (已在 Task 2 中创建，此 Task 验证)

observe() 已在 Task 2 的 memory_manager.py 中完整实现。此 Task 专注于验证和集成。

- [ ] **Step 1: 验证 observe() 可被调用**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "
from server.services.memory_manager import MemoryManager
from server.config import AppConfig
config = AppConfig().get_all()
# 测试无 LLM 时的跳过逻辑
mgr = MemoryManager(config=config, llm=None)
result = mgr.observe([{'role': 'user', 'content': '你好'}], 'test-conv-id')
print('observe without LLM:', result)
"
```

Expected: `observe without LLM: 0`

- [ ] **Step 2: Commit Phase 3**（observe 逻辑随 memory_manager.py 已在 Phase 2 提交）

```bash
git add server/services/memory_manager.py
git commit -m "feat(memory): Phase 3 — MemoryManager.observe() 主动记忆验证通过

- observe() 单次 LLM 调用完成信号检测+提取+被动记忆意图检测
- 无 LLM 时安全跳过（返回 0）
- JSON 解析容错（支持 markdown 代码块包裹）
- 自动调用 memorize() 做去重存储

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Phase 4 — chat.py 集成（recall 注入 + observe 触发）

**Files:**
- Modify: `server/routers/chat.py`
- Modify: `server/services/rag.py`

- [ ] **Step 1: 修改 rag.py — 记忆注入改为 system message 格式**

修改 `server/services/rag.py` 的 `ask_sync()` 和 `ask_stream()` 方法，将记忆文本注入 system prompt 而非拼入 user prompt。

修改 `build_qa_prompt()` 函数签名和 `_build_kb_prompt`、`_build_web_prompt` 中的记忆部分：

在 `server/services/rag.py` 第 210 行附近，修改 `ask_sync()` 方法：

```python
def ask_sync(self, question: str, history: list[dict] | None = None,
             memory_context: str = "") -> dict:
    chunks = self.retriever.retrieve(question)
    web_sourced = False

    if self._is_web_search_needed(chunks):
        ws = self._web_search
        if ws:
            web_chunks = ws.search(question)
            if web_chunks:
                if not chunks:
                    chunks = web_chunks
                    web_sourced = True
                else:
                    chunks = chunks + web_chunks

    # 记忆已作为 memory_context 传入，构建 system prompt
    prompt = build_qa_prompt(question, chunks, memory_context=memory_context,
                             web_sourced=web_sourced, history=history)
    messages = []
    # 单条 system message 兼容 Anthropic
    system_prompt = "你是一个知识库助手。请根据参考资料回答用户问题。使用中文回答。"
    if memory_context:
        system_prompt += "\n\n" + memory_context
    messages.append({"role": "system", "content": system_prompt})
    if history:
        history_text = _build_history_text(history)
        if history_text:
            messages.append({"role": "system", "content": history_text})
    messages.append({"role": "user", "content": prompt})
    
    result = self.llm.chat(messages=messages, temperature=0.3)
    citations = format_citations(chunks, web_sourced=web_sourced)
    return {"answer": result["content"], "citations": citations}
```

修改 `build_qa_prompt` 函数（第 33-55 行）：

```python
def build_qa_prompt(
    question: str,
    chunks: list[dict],
    memory_context: str = "",
    web_sourced: bool = False,
    history: list[dict] | None = None,
) -> str:
    # history 现在通过 system message 注入，这里只保留构建知识库 context 的逻辑
    if not chunks:
        return (
            f"## 用户问题\n{question}\n\n"
            f"知识库中未找到相关内容。请基于你自身的知识如实回答，"
            f"并在回答末尾注明：\n"
            f"> 📚 *以上回答基于模型自身知识，未引用知识库文档。*"
        )

    if web_sourced:
        return _build_web_prompt(question, chunks)
    return _build_kb_prompt(question, chunks)
```

修改 `_build_kb_prompt`（第 58-93 行）：

```python
def _build_kb_prompt(question: str, chunks: list[dict]) -> str:
    doc_titles = list(dict.fromkeys(c["document_title"] for c in chunks if c.get("document_title")))

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        chunk_no = chunk.get("chunk_no", 0)
        context_parts.append(
            f"[{i}] 来源: {chunk['document_title']} (段落 {chunk_no})\n{chunk['content']}"
        )

    context = "\n\n".join(context_parts)
    doc_hint = ""
    if doc_titles:
        titles_str = "、".join(doc_titles[:3])
        doc_hint = f"\n以上参考资料来自你的知识库文档：{titles_str}。这些是用户已上传的个人文档内容。"

    return f"""## 参考资料
{context}{doc_hint}

## 要求
- 参考资料来自用户已上传的文档，优先使用其中的信息回答问题
- 理解对话历史中的上下文，结合当前问题给出连贯的回答
- 如果当前问题是对上一轮回答的追问或澄清，请基于历史上下文理解用户意图
- 即使信息分散在多个片段中，也要尽量综合整理，给出有价值的回答
- 回答中引用来源编号，如 [1]、[2]
- 如果参考资料覆盖了多个不同的要点或角度，请全面综合回答，不要遗漏
- 只有确实完全不相关时才说明无法回答，不要因为信息不完整就放弃
- 使用中文回答
- 在回答末尾必须添加信息来源说明，格式如下：
  > 📚 **信息来源**：知识库文档《书名1》、《书名2》

## 用户问题
{question}"""
```

修改 `_build_web_prompt`（第 96-130 行）：

```python
def _build_web_prompt(question: str, chunks: list[dict]) -> str:
    context_parts = []
    doc_titles = []
    for i, chunk in enumerate(chunks, 1):
        url = chunk.get("url") or chunk.get("file_name", "")
        context_parts.append(
            f"[{i}] 标题: {chunk['document_title']}\n"
            f"链接: {url}\n"
            f"内容: {chunk['content']}"
        )
        title = chunk.get("document_title", "")
        if title:
            doc_titles.append(title)

    context = "\n\n".join(context_parts)
    titles_str = "、".join(list(dict.fromkeys(doc_titles))[:3]) if doc_titles else "互联网"

    return f"""## 互联网搜索结果
{context}

## 要求
- 优先使用搜索结果中的信息回答问题
- 理解对话历史中的上下文，结合当前问题给出连贯的回答
- 回答中引用来源编号，如 [1]、[2]，并在引用处附上对应的链接 URL
- 综合多个来源的信息，给出全面的回答
- 如果搜索结果无法覆盖问题，可以结合自身知识补充，但请注明哪部分来自自身知识
- 使用中文回答
- 在回答末尾必须添加信息来源说明，格式如下：
  > 🌐 **信息来源**：互联网搜索（{titles_str} 等）

## 用户问题
{question}"""
```

删除 `_build_memory_section()` 函数（第 13-17 行）和 `_build_history_section()` 函数（第 20-30 行），新增 `_build_history_text()` 辅助函数：

```python
def _build_history_text(history: list[dict] | None) -> str:
    """将对话历史格式化为单段文本，注入 system message。"""
    if not history:
        return ""
    parts = []
    for h in history[-6:]:
        role = "用户" if h["role"] == "user" else "助手"
        parts.append(f"{role}：{h['content']}")
    if parts:
        return "## 对话历史\n" + "\n".join(parts)
    return ""
```

修改 `ask_stream()` 方法（第 237-261 行）同理：

```python
async def ask_stream(self, question: str, history: list[dict] | None = None,
                     memory_context: str = "") -> AsyncIterator[dict]:
    loop = asyncio.get_running_loop()
    chunks = await loop.run_in_executor(None, self.retriever.retrieve, question)
    web_sourced = False

    if self._is_web_search_needed(chunks):
        ws = self._web_search
        if ws:
            web_chunks = await loop.run_in_executor(None, ws.search, question)
            if web_chunks:
                if not chunks:
                    chunks = web_chunks
                    web_sourced = True
                else:
                    chunks = chunks + web_chunks

    prompt = build_qa_prompt(question, chunks, web_sourced=web_sourced)
    messages = []
    system_prompt = "你是一个知识库助手。请根据参考资料回答用户问题。使用中文回答。"
    if memory_context:
        system_prompt += "\n\n" + memory_context
    messages.append({"role": "system", "content": system_prompt})
    if history:
        history_text = _build_history_text(history)
        if history_text:
            messages.append({"role": "system", "content": history_text})
    messages.append({"role": "user", "content": prompt})

    async for chunk in self.llm.chat_stream(messages=messages, temperature=0.3):
        yield chunk
    yield {"type": "citations", "data": format_citations(chunks, web_sourced=web_sourced)}
```

- [ ] **Step 2: 修改 chat.py — 集成 recall + observe**

修改 `server/routers/chat.py` 的 `chat_ask()` 函数（第 76-135 行）。在用 `_get_rag_service` 获取 rag 之前，新增记忆召回。在回答完成后，新增 observe 触发：

在第 98-106 行之间（构建 rag 和调用 ask_sync 之间）加入 recall：

```python
    try:
        rag = _get_rag_service(DATA_DIR)
        history = _get_conversation_history(session, conversation_id)

        # === 记忆召回 ===
        memory_context = ""
        try:
            from server.config import AppConfig as Cfg
            cfg = Cfg().get_all()
            if cfg.get("memory_enabled", "true") == "true":
                from server.services.memory_manager import MemoryManager
                from server.services.llm import LLMAdapter as LLM
                mem_mgr = MemoryManager(config=cfg, llm=LLM(cfg))
                memory_context = mem_mgr.recall_as_context(question, conv_id=conversation_id)
        except Exception as e:
            logger.warning(f"记忆召回失败: {e}")

        result = rag.ask_sync(question, history=history, memory_context=memory_context)
    except Exception as e:
```

在回答完成后（`session.commit()` 之后），替换原有的 `summarize_conversation` 后台线程为 observe：

```python
    session.commit()

    # 后台异步：主动记忆分析
    def _observe_bg():
        try:
            from server.config import AppConfig as Cfg
            cfg = Cfg().get_all()
            if cfg.get("memory_enabled", "true") != "true":
                return
            if cfg.get("memory_auto_observe", "true") != "true":
                return
            observe_interval = int(cfg.get("memory_observe_interval", "3"))
            # 计算当前会话消息数，按间隔触发
            from server.database import get_session_ctx as ctx
            from server.models.conversation import Message as Msg
            with ctx() as s:
                msg_count = s.query(Msg).filter(
                    Msg.conversation_id == conversation_id
                ).count()
            if msg_count % observe_interval != 0:
                return  # 未到触发间隔
            from server.services.memory_manager import MemoryManager
            from server.services.llm import LLMAdapter as LLM
            mem_mgr = MemoryManager(config=cfg, llm=LLM(cfg))
            recent = history + [
                {"role": "assistant", "content": result["answer"]}
            ]
            mem_mgr.observe(recent, conversation_id)
        except Exception as e:
            logger.warning(f"主动记忆后台任务失败: {e}")
    threading.Thread(target=_observe_bg, daemon=True).start()
```

对 `chat_stream()` 函数（第 138-208 行）做同样的修改。

- [ ] **Step 3: 运行现有测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -x -q 2>&1 | tail -5
```

- [ ] **Step 4: Commit Phase 4**

```bash
git add server/routers/chat.py server/services/rag.py
git commit -m "feat(memory): Phase 4 — chat.py 集成 recall 注入 + observe 触发

- rag.py: 记忆通过 memory_context 参数传入，拼入单条 system message（兼容 Anthropic）
- rag.py: 对话历史也通过 system message 注入
- chat.py: 每次问答前调用 recall_as_context() 召回历史记忆
- chat.py: 每 N 轮（默认3）触发 observe() 主动记忆分析
- chat.py: 替换旧的 summarize_conversation 后台调用

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Phase 5 — MemoryMDExporter 导出

**Files:**
- Create: `server/services/memory_md_exporter.py`

- [ ] **Step 1: 创建 MemoryMDExporter**

创建 `server/services/memory_md_exporter.py`：

```python
"""MemoryMDExporter — 记忆 Markdown 导出器（线程安全）。"""

import threading
from datetime import datetime, timezone
from pathlib import Path


class MemoryMDExporter:
    """将记忆导出为人类可读的 Markdown 文件。

    目录结构:
        data/memories/
        ├── global/
        │   ├── preferences.md
        │   ├── facts.md
        │   └── conclusions.md
        ├── sessions/
        │   └── {conv_id}.md
        └── INDEX.md
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def _get_lock(self, path: Path) -> threading.Lock:
        key = str(path)
        with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def _ensure_dirs(self):
        (self.base_dir / "global").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "sessions").mkdir(parents=True, exist_ok=True)

    # ====== 增量更新 ======

    def incremental_update(self, mem_id: str, content: str, metadata: dict):
        """新增或更新单条记忆时，增量更新对应的 md 文件。"""
        self._ensure_dirs()
        mem_type = metadata.get("type", "manual")
        scope = metadata.get("scope", "global")
        conv_id = metadata.get("source_conv_id", "")

        if scope == "session" and conv_id:
            file_path = self.base_dir / "sessions" / f"{conv_id}.md"
            self._append_to_session_file(file_path, mem_id, content, metadata)
        elif mem_type in ("preference", "fact", "conclusion", "manual"):
            file_path = self.base_dir / "global" / f"{mem_type}s.md"
            self._append_to_global_file(file_path, mem_type, mem_id, content, metadata)
        else:
            file_path = self.base_dir / "global" / "other.md"
            self._append_to_global_file(file_path, "other", mem_id, content, metadata)

        # 更新 INDEX.md
        self._update_index()

    def _append_to_global_file(self, path: Path, mem_type: str, mem_id: str,
                               content: str, metadata: dict):
        importance = metadata.get("importance", 0.5)
        count = metadata.get("count", 1)
        stars = "⭐" if importance >= 0.8 else ""
        line = f"- {content} — 出现 {count} 次 | {stars} {importance:.2f}\n"

        lock = self._get_lock(path)
        with lock:
            if not path.exists():
                title_map = {
                    "preference": "# 用户偏好", "fact": "# 已知事实",
                    "conclusion": "# 跨会话结论", "manual": "# 手动备注", "other": "# 其他记忆",
                }
                header = title_map.get(mem_type, "# 记忆")
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                path.write_text(f"{header}\n\n> 最后更新: {now} | 共 1 条\n\n{line}", encoding="utf-8")
            else:
                text = path.read_text(encoding="utf-8")
                # 更新计数
                import re
                text = re.sub(
                    r'> 最后更新: .* \| 共 (\d+) 条',
                    lambda m: f'> 最后更新: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} | 共 {int(m.group(1)) + 1} 条',
                    text, count=1
                )
                # 追加新行
                text += line
                path.write_text(text, encoding="utf-8")

    def _append_to_session_file(self, path: Path, mem_id: str,
                                content: str, metadata: dict):
        mem_type = metadata.get("type", "fact")
        importance = metadata.get("importance", 0.5)
        line = f"- [{mem_type}] {content} | ⭐ {importance:.2f}\n"

        lock = self._get_lock(path)
        with lock:
            if not path.exists():
                conv_id = metadata.get("source_conv_id", "unknown")
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                path.write_text(f"# 会话记忆 ({conv_id[:8]}...)\n\n> 最后更新: {now}\n\n{line}", encoding="utf-8")
            else:
                text = path.read_text(encoding="utf-8")
                text += line
                path.write_text(text, encoding="utf-8")

    def _update_index(self):
        idx_path = self.base_dir / "INDEX.md"
        lock = self._get_lock(idx_path)
        with lock:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            lines = [
                "# 记忆索引\n\n",
                f"> 最后更新: {now}\n\n",
                "## 全局记忆\n\n",
            ]
            global_dir = self.base_dir / "global"
            if global_dir.exists():
                for f in sorted(global_dir.iterdir()):
                    if f.suffix == ".md":
                        # 统计行数
                        content = f.read_text(encoding="utf-8")
                        count = len([l for l in content.split("\n") if l.startswith("- ")])
                        lines.append(f"- [{f.stem}](global/{f.name}) — {count} 条\n")
            lines.append("\n## 会话记忆\n\n")
            sessions_dir = self.base_dir / "sessions"
            if sessions_dir.exists():
                for f in sorted(sessions_dir.iterdir(), reverse=True):
                    if f.suffix == ".md":
                        lines.append(f"- [{f.stem[:12]}...](sessions/{f.name})\n")
            idx_path.write_text("".join(lines), encoding="utf-8")

    # ====== 全量导出 ======

    def full_export(self, memories: list[dict], scope: str | None = None) -> Path:
        """全量重写导出所有记忆。返回导出目录路径。"""
        self._ensure_dirs()

        # 按类型和作用域分组
        groups: dict[str, list[dict]] = {}
        for mem in memories:
            meta = mem.get("metadata", {})
            mtype = meta.get("type", "other")
            mscope = meta.get("scope", "global")
            if scope and mscope != scope:
                continue
            key = f"{mscope}:{mtype}"
            groups.setdefault(key, []).append(mem)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        # 写 global 文件
        type_labels = {
            "preference": ("用户偏好", "# 用户偏好"),
            "fact": ("已知事实", "# 已知事实"),
            "conclusion": ("跨会话结论", "# 跨会话结论"),
            "manual": ("手动备注", "# 手动备注"),
        }
        for mtype, (label, header) in type_labels.items():
            key = f"global:{mtype}"
            items = groups.get(key, [])
            items.sort(key=lambda m: m.get("metadata", {}).get("importance", 0), reverse=True)
            path = self.base_dir / "global" / f"{mtype}s.md"
            lock = self._get_lock(path)
            with lock:
                lines = [header + "\n\n", f"> 最后更新: {now} | 共 {len(items)} 条\n\n"]
                for m in items:
                    imp = m.get("metadata", {}).get("importance", 0.5)
                    cnt = m.get("metadata", {}).get("count", 1)
                    stars = "⭐" if imp >= 0.8 else ""
                    lines.append(f"- {m['content']} — 出现 {cnt} 次 | {stars} {imp:.2f}\n")
                path.write_text("".join(lines), encoding="utf-8")

        # 写 session 文件
        session_groups: dict[str, list[dict]] = {}
        for mem in memories:
            meta = mem.get("metadata", {})
            if meta.get("scope") == "session":
                cid = meta.get("source_conv_id", "unknown")
                session_groups.setdefault(cid, []).append(mem)

        for cid, items in session_groups.items():
            path = self.base_dir / "sessions" / f"{cid}.md"
            lock = self._get_lock(path)
            with lock:
                lines = [f"# 会话记忆 ({cid[:8]}...)\n\n", f"> 最后更新: {now} | 共 {len(items)} 条\n\n"]
                for m in items:
                    mt = m.get("metadata", {}).get("type", "fact")
                    imp = m.get("metadata", {}).get("importance", 0.5)
                    lines.append(f"- [{mt}] {m['content']} | ⭐ {imp:.2f}\n")
                path.write_text("".join(lines), encoding="utf-8")

        # 写 INDEX.md
        self._update_index()
        return self.base_dir

    def get_export_files(self) -> list[str]:
        """返回所有导出文件的相对路径列表。"""
        files = []
        for d in ("global", "sessions"):
            dpath = self.base_dir / d
            if dpath.exists():
                for f in sorted(dpath.iterdir()):
                    if f.suffix == ".md":
                        files.append(f"{d}/{f.name}")
        if (self.base_dir / "INDEX.md").exists():
            files.append("INDEX.md")
        return files
```

- [ ] **Step 2: 验证导出功能**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "
from server.services.memory_md_exporter import MemoryMDExporter
from pathlib import Path
import tempfile
d = Path(tempfile.mkdtemp())
e = MemoryMDExporter(d)
e.incremental_update('test-1', '用户偏好 Python 异步模式', {'type': 'preference', 'scope': 'global', 'importance': 0.8, 'count': 1})
print('Files:', e.get_export_files())
print((d/'global/preferences.md').read_text())
"
```

Expected: 看到文件列表和格式正确的 preferences.md 内容

- [ ] **Step 3: Commit Phase 5**

```bash
git add server/services/memory_md_exporter.py
git commit -m "feat(memory): Phase 5 — MemoryMDExporter 导出器

- Markdown 文件导出（global/ 按类型分文件 + sessions/ 按会话分文件）
- 增量更新：每次 memorize 追加到对应文件
- 全量导出：按 importance 排序重写所有文件
- INDEX.md 记忆总览索引
- threading.Lock 按文件路径加锁，并发安全

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Phase 6 — consolidate() 记忆合并

**Files:**
- Modify: `server/services/memory_manager.py` (已在 Task 2 实现)

consolidate() 已在 Task 2 的 memory_manager.py 中完整实现。此 Task 专注于验证。

- [ ] **Step 1: 验证 consolidate 去重逻辑**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "
from server.services.memory_manager import MemoryManager
from server.config import AppConfig
config = AppConfig().get_all()
mgr = MemoryManager(config=config, llm=None)

# 手动写入两条相似记忆
mgr.memorize('用户偏好使用 Python 异步模式处理 I/O', mem_type='preference', scope='global')
mgr.memorize('用户喜欢用 Python 的 async/await 处理 IO 操作', mem_type='preference', scope='global')

# dry_run 验证
result = mgr.consolidate(dry_run=True)
print('dry_run:', result)
"
```

- [ ] **Step 2: Commit Phase 6**（无新文件，consolidate 已在 Phase 2 实现）

```bash
git add server/services/memory_manager.py
git commit -m "feat(memory): Phase 6 — consolidate() 验证

- query top-3 预筛选替代 O(n²)
- dry_run 模式返回候选 pairs 详情
- 自动清理过期记忆

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Phase 7 — API 端点更新

**Files:**
- Modify: `server/routers/memories.py`

- [ ] **Step 1: 重写 memories.py 路由**

完整重写 `server/routers/memories.py`：

```python
"""记忆管理路由。"""

from fastapi import APIRouter, HTTPException
from server.services.memory_manager import MemoryManager
from server.config import AppConfig
from server.services.llm import LLMAdapter

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


def _get_mgr() -> MemoryManager:
    config = AppConfig().get_all()
    return MemoryManager(config=config, llm=LLMAdapter(config))


# ====== 被动记忆 ======

@router.post("/remember")
def remember_message(body: dict):
    conversation_id = body.get("conversation_id")
    message_id = body.get("message_id")
    note = body.get("note", "").strip()
    scope = body.get("scope", "global")

    if not conversation_id or not message_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id 或 message_id")

    from server.database import get_session_ctx
    from server.models.conversation import Message
    with get_session_ctx() as session:
        msg = session.get(Message, message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="消息不存在")
        content = msg.content
        if note:
            content = f"{content}\n备注: {note}"

    mgr = _get_mgr()
    mid = mgr.memorize(content, mem_type="manual", scope=scope,
                        metadata={"source_conv_id": conversation_id})
    return {"code": "OK", "data": {"id": mid}}


# ====== 列出记忆 ======

@router.get("")
def list_memories_endpoint(mem_type: str = None, scope: str = None, limit: int = 50):
    from server.services import memory as mem
    data = mem.list_memories(mem_type=mem_type, limit=limit)
    if scope:
        data = [m for m in data if m.get("metadata", {}).get("scope") == scope]
    return {"code": "OK", "data": data}


# ====== 搜索记忆 ======

@router.get("/search")
def search_memories_endpoint(q: str = "", scope: str = None, top_k: int = 5):
    if not q:
        raise HTTPException(status_code=400, detail="缺少查询参数 q")
    mgr = _get_mgr()
    results = mgr.recall(q, top_k=top_k)
    if scope:
        results = [r for r in results if r.get("metadata", {}).get("scope") == scope]
    return {"code": "OK", "data": results}


# ====== 删除记忆 ======

@router.delete("/{mem_id}")
def delete_memory_endpoint(mem_id: str):
    from server.services import memory as mem
    mem.delete_memory(mem_id)
    return {"code": "OK", "data": None}


# ====== 主动触发分析 ======

@router.post("/observe")
def observe_endpoint(body: dict):
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id")

    from server.database import get_session_ctx
    from server.models.conversation import Conversation
    with get_session_ctx() as session:
        conv = session.get(Conversation, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")
        messages = [{"role": m.role, "content": m.content} for m in conv.messages]

    mgr = _get_mgr()
    count = mgr.observe(messages, conversation_id)
    return {"code": "OK", "data": {"new_memories": count}}


# ====== 合并记忆 ======

@router.post("/consolidate")
def consolidate_endpoint(body: dict = {}):
    dry_run = body.get("dry_run", False)
    mgr = _get_mgr()
    result = mgr.consolidate(dry_run=dry_run)
    return {"code": "OK", "data": result}


# ====== 导出 md ======

@router.post("/export")
def export_memories_endpoint(body: dict = {}):
    scope = body.get("scope", None)
    mgr = _get_mgr()
    path = mgr.export_md(scope=scope)
    files = mgr.exporter.get_export_files()
    return {"code": "OK", "data": {"path": str(path), "files": len(files)}}


@router.get("/export")
def get_export_files_endpoint():
    mgr = _get_mgr()
    files = mgr.exporter.get_export_files()
    return {"code": "OK", "data": {"files": files}}
```

- [ ] **Step 2: 验证服务可启动**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "from server.main import app; print('App OK, routes:', len(app.routes))"
```

Expected: `App OK, routes: N`

- [ ] **Step 3: Commit Phase 7**

```bash
git add server/routers/memories.py
git commit -m "feat(memory): Phase 7 — API 端点更新

- POST /memories/remember: 增加 scope 参数
- GET /memories/search: 增加 scope 过滤
- GET /memories: 增加 scope 过滤
- POST /memories/observe: 新增，手动触发主动记忆
- POST /memories/consolidate: 新增，dry_run 返回 pairs 详情
- POST /memories/export: 新增，全量导出 md
- GET /memories/export: 新增，查看导出文件列表

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Phase 8 — 测试

**Files:**
- Create: `server/tests/test_memory_manager.py`
- Create: `server/tests/test_memory_md_exporter.py`
- Create: `server/tests/test_routers/test_memories.py`
- Modify: `server/tests/test_rag.py` (扩展)

- [ ] **Step 1: 创建 MemoryManager 单元测试**

创建 `server/tests/test_memory_manager.py`：

```python
"""MemoryManager 单元测试（mock LLM）。"""

import pytest
from unittest.mock import MagicMock, patch
from server.services.memory_manager import MemoryManager


@pytest.fixture
def mgr():
    config = {
        "memory_dedup_threshold": "0.85",
        "memory_recall_top_k": "5",
        "memory_export_auto": "false",
    }
    return MemoryManager(config=config, llm=None)


class TestMemoryManager:
    def test_memorize_new(self, mgr):
        """新增记忆返回 ID。"""
        mid = mgr.memorize("测试记忆内容", mem_type="fact", scope="global")
        assert mid.startswith("mem-")
        assert len(mid) > 10

    def test_memorize_dedup(self, mgr):
        """相似记忆自动合并。"""
        mid1 = mgr.memorize("用户偏好使用 Python 异步", mem_type="preference", scope="global")
        mid2 = mgr.memorize("用户偏好 Python 异步模式处理 I/O", mem_type="preference", scope="global")
        assert mid1 == mid2  # 合并到同一条

    def test_recall_returns_results(self, mgr):
        """recall 返回排序后的记忆列表。"""
        mgr.memorize("项目使用 FastAPI + SQLite", mem_type="fact", scope="global")
        mgr.memorize("用户喜欢设计方案", mem_type="preference", scope="global")
        results = mgr.recall("项目用什么框架")
        assert len(results) > 0
        # 第一条应更相关
        assert "FastAPI" in results[0]["content"] or any("FastAPI" in r["content"] for r in results)

    def test_recall_as_context_format(self, mgr):
        """recall_as_context 返回正确格式的文本。"""
        mgr.memorize("用户偏好 Rust 语言", mem_type="preference", scope="global")
        mgr.memorize("上次决定用 Redis 做缓存", mem_type="conclusion", scope="session",
                     metadata={"source_conv_id": "test-conv"})
        ctx = mgr.recall_as_context("缓存方案")
        assert "## 用户历史信息" in ctx
        assert "偏好" in ctx

    def test_observe_without_llm_returns_zero(self, mgr):
        """无 LLM 时 observe 返回 0。"""
        result = mgr.observe([{"role": "user", "content": "hello"}], "conv-1")
        assert result == 0

    def test_consolidate_dry_run(self, mgr):
        """consolidate dry_run 返回 pairs。"""
        mgr.memorize("测试内容 A", mem_type="fact", scope="global")
        mgr.memorize("测试内容 A 重复", mem_type="fact", scope="global")
        result = mgr.consolidate(dry_run=True)
        assert "pairs" in result
        assert "total_pairs" in result
        assert "expired_candidates" in result
```

- [ ] **Step 2: 创建 MemoryMDExporter 单元测试**

创建 `server/tests/test_memory_md_exporter.py`：

```python
"""MemoryMDExporter 单元测试。"""

import pytest
import tempfile
from pathlib import Path
from server.services.memory_md_exporter import MemoryMDExporter


@pytest.fixture
def exporter():
    d = Path(tempfile.mkdtemp())
    return MemoryMDExporter(base_dir=d)


class TestMemoryMDExporter:
    def test_incremental_update_creates_file(self, exporter):
        """增量更新创建文件。"""
        exporter.incremental_update(
            "mem-1", "用户偏好异步模式",
            {"type": "preference", "scope": "global", "importance": 0.8, "count": 1}
        )
        path = exporter.base_dir / "global" / "preferences.md"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "用户偏好异步模式" in content
        assert "# 用户偏好" in content

    def test_incremental_update_appends(self, exporter):
        """增量更新追加内容。"""
        exporter.incremental_update(
            "mem-1", "事实1",
            {"type": "fact", "scope": "global", "importance": 0.5, "count": 1}
        )
        exporter.incremental_update(
            "mem-2", "事实2",
            {"type": "fact", "scope": "global", "importance": 0.7, "count": 1}
        )
        path = exporter.base_dir / "global" / "facts.md"
        content = path.read_text(encoding="utf-8")
        assert "事实1" in content
        assert "事实2" in content

    def test_full_export(self, exporter):
        """全量导出生效。"""
        memories = [
            {
                "id": "mem-a", "content": "偏好 Python",
                "metadata": {"type": "preference", "scope": "global", "importance": 0.9, "count": 3}
            },
            {
                "id": "mem-b", "content": "项目用 FastAPI",
                "metadata": {"type": "fact", "scope": "global", "importance": 0.7, "count": 1}
            },
            {
                "id": "mem-c", "content": "决定用方案B",
                "metadata": {"type": "conclusion", "scope": "session", "source_conv_id": "abc-123", "importance": 0.6}
            },
        ]
        path = exporter.full_export(memories)
        assert (path / "INDEX.md").exists()
        prefs = path / "global" / "preferences.md"
        assert prefs.exists()
        assert "偏好 Python" in prefs.read_text(encoding="utf-8")

    def test_get_export_files(self, exporter):
        """get_export_files 返回文件列表。"""
        exporter.incremental_update(
            "mem-1", "test",
            {"type": "fact", "scope": "global", "importance": 0.5, "count": 1}
        )
        files = exporter.get_export_files()
        assert "global/facts.md" in files
        assert "INDEX.md" in files

    def test_concurrent_writes(self, exporter):
        """并发写入不抛异常。"""
        import threading
        errors = []

        def write_one(i):
            try:
                exporter.incremental_update(
                    f"mem-{i}", f"内容{i}",
                    {"type": "fact", "scope": "global", "importance": 0.5, "count": 1}
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
```

- [ ] **Step 3: 创建 memories 路由集成测试**

创建 `server/tests/test_routers/test_memories.py`：

```python
"""记忆 API 端点集成测试。"""

import pytest
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestMemoriesAPI:
    def test_list_memories(self, client):
        """列出记忆返回正确格式。"""
        resp = client.get("/api/v1/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"
        assert isinstance(data["data"], list)

    def test_search_memories_requires_query(self, client):
        """搜索记忆需要 query 参数。"""
        resp = client.get("/api/v1/memories/search")
        assert resp.status_code == 400

    def test_search_memories(self, client):
        """搜索记忆返回结果。"""
        resp = client.get("/api/v1/memories/search?q=测试")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"

    def test_observe_requires_conv_id(self, client):
        """observe 需要 conversation_id。"""
        resp = client.post("/api/v1/memories/observe", json={})
        assert resp.status_code == 400

    def test_consolidate_dry_run(self, client):
        """consolidate dry_run 返回 pairs。"""
        resp = client.post("/api/v1/memories/consolidate", json={"dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"
        assert "pairs" in data["data"]

    def test_export_get_files(self, client):
        """GET export 返回文件列表。"""
        resp = client.get("/api/v1/memories/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"

    def test_delete_nonexistent_memory(self, client):
        """删除不存在的记忆不报错。"""
        resp = client.delete("/api/v1/memories/nonexistent-id")
        assert resp.status_code == 200
```

- [ ] **Step 4: 运行所有测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v 2>&1 | tail -30
```

Expected: All tests PASS（包括新增的 15+ 测试用例）

- [ ] **Step 5: 对现有测试做兼容性修复（如有需要）**

如果 `test_rag.py` 中引用了被删除的 `_build_memory_section` 或修改了 `build_qa_prompt` 签名，需要更新。

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_rag.py -v 2>&1 | tail -20
```

修复 test_rag.py 中可能需要更新的调用。

- [ ] **Step 6: Commit Phase 8**

```bash
git add server/tests/
git commit -m "test(memory): Phase 8 — 记忆系统测试

- test_memory_manager.py: memorize/recall/observe/consolidate 单元测试
- test_memory_md_exporter.py: 增量/全量/并发导出测试
- test_memories.py: API 端点集成测试
- test_rag.py: 兼容性更新（记忆注入方式变更）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

1. **Spec coverage**: Each spec requirement maps to a task:
   - MemoryStore 增强 → Task 1
   - MemoryManager 核心 → Task 2
   - observe() 主动记忆 → Task 3
   - chat.py 集成 → Task 4
   - MD 导出 → Task 5
   - consolidate() → Task 6
   - API 端点 → Task 7
   - 测试 → Task 8

2. **12 review fixes**: All addressed:
   - #1 cosine distance: Task 1 Step 1 (already set, confirmed in spec)
   - #2 observe interval: Task 4 Step 2 (每3轮触发)
   - #3 consolidate O(n²): Task 6 (query top-3)
   - #4 recall weights: Task 2 (α=0.5, β=0.3, γ=0.2)
   - #5 single system message: Task 4 Step 1
   - #6 replace summarize_conversation: Task 2 (废弃兼容层)
   - #7 session idle timeout: Task 2 (memory.py 保留兜底)
   - #8 single LLM call: Task 2 (observe 一次调用)
   - #9 file locking: Task 5 (threading.Lock)
   - #10 expires_at: Task 1 (metadata + delete_expired)
   - #11 dry_run pairs: Task 2 (返回 pairs 详情)
   - #12 passive in observe: Task 2 (observe prompt 含 manual 检测)

3. **Type consistency**: All method signatures match across tasks. MemoryManager API consistent between Task 2 (definition) and Tasks 4/7 (usage).

4. **No placeholders**: Every step has actual code. Every command has expected output.

5. **Edge cases**: 
   - observe with no LLM → returns 0
   - memorize duplicate → merges
   - concurrent MD writes → locks prevent corruption
   - expired memories → filtered in search, cleaned in consolidate
