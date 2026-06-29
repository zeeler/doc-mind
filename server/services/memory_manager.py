"""MemoryManager — 记忆系统统一编排层（内置单例模式）。"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from server.services.memory_store import MemoryStore
from server.database import DATA_DIR as _DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

_manager_singleton: 'MemoryManager | None' = None
_manager_singleton_lock = threading.Lock()


class MemoryManager:
    """记忆系统的统一入口，编排 MemoryStore 和 MemoryMDExporter。

    被 chat.py（recall + observe）、memories 路由（memorize + consolidate + export）调用。
    """

    def __init__(self, config: dict, llm=None,
                 persist_dir: str | None = None,
                 export_dir: str | None = None):
        self.config = config
        self.llm = llm  # LLMAdapter 实例，observe() 需要
        self._store: MemoryStore | None = None
        self._exporter = None  # MemoryMDExporter，延迟加载
        self._persist_dir = persist_dir or str(_DEFAULT_DATA_DIR / "chroma")
        self.dedup_threshold = float(config.get("memory_dedup_threshold", "0.85"))
        self.recall_top_k = int(config.get("memory_recall_top_k", "5"))
        self.export_auto = config.get("memory_export_auto", "true") == "true"
        if export_dir is None:
            export_dir = config.get("memory_export_dir", "") or str(_DEFAULT_DATA_DIR / "memories")
        self.export_dir = Path(export_dir)
        self.expire_days = int(config.get("memory_session_expire_days", "30"))

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            self._store = MemoryStore(persist_dir=self._persist_dir)
        return self._store

    @property
    def exporter(self):
        """延迟加载 MemoryMDExporter（可能尚未创建）。"""
        if self._exporter is None:
            try:
                from server.services.memory_md_exporter import MemoryMDExporter
                self._exporter = MemoryMDExporter(base_dir=self.export_dir)
            except ImportError:
                logger.warning("MemoryMDExporter 不可用，导出功能禁用")
                self._exporter = None
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
            session_mems = self.store.search(query, top_k=fetch_k // 2, scope="session")

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
            similarity = m.get("score", 0.5)
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

        stable_parts = []  # preference + fact + manual
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

    # ============ memorize() — 被动记忆（含去重）============

    def memorize(self, content: str, mem_type: str = "manual",
                 scope: str = "global", metadata: dict | None = None) -> str:
        """被动记忆：API 调用的直接存储，不经过 LLM 分析。返回记忆 ID。

        对话中的"记住XXX"由 observe() 检测，不经过此方法。
        此方法供 POST /api/v1/memories/remember 调用。
        """
        meta = dict(metadata or {})
        meta["type"] = mem_type
        meta["scope"] = scope
        now = datetime.now(timezone.utc).isoformat()
        meta.setdefault("updated_at", now)
        meta.setdefault("created_at", now)

        # 参数验证
        if scope not in ("global", "session"):
            raise ValueError(f"无效的 scope: {scope}，必须是 'global' 或 'session'")
        if mem_type not in ("preference", "conclusion", "fact", "manual"):
            raise ValueError(f"无效的 mem_type: {mem_type}")

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
                    self._incremental_export(hit["id"], merged_content, old_meta)
                return hit["id"]

        mid = self.store.add(None, content, meta, expire_days=self.expire_days)
        if self.export_auto:
            self._incremental_export(mid, content, meta)
        return mid

    def _incremental_export(self, mem_id: str, content: str, metadata: dict):
        """增量导出到 md 文件（如果 exporter 可用）。"""
        exp = self.exporter
        if exp:
            try:
                exp.incremental_update(mem_id, content, metadata)
            except Exception as e:
                logger.warning(f"增量导出失败: {e}")

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

        try:
            result = self.llm.chat(messages=[{"role": "user", "content": prompt}], temperature=0.2)
            text = result.get("content", "").strip()
            # 提取 JSON（可能被 markdown 代码块包裹）
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
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

    def consolidate(self, dry_run: bool = False, max_memories: int = 500) -> dict:
        """合并相似记忆，清理过期。使用 query top-3 预筛选避免 O(n²)。"""
        all_mems = self.store.get_all(limit=10000)
        merged_count = 0
        pairs = []
        seen_pairs = set()

        for mem in all_mems[:max_memories]:
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
        mem_by_id = {m["id"]: m for m in all_mems}
        deleted_ids = set()
        for pair in pairs:
            if pair["id_1"] in deleted_ids or pair["id_2"] in deleted_ids:
                continue
            try:
                mem1 = mem_by_id.get(pair["id_1"])
                mem2 = mem_by_id.get(pair["id_2"])
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
                deleted_ids.add(removed["id"])
                merged_count += 1
            except Exception as e:
                logger.warning(f"合并记忆失败 {pair['id_1']}+{pair['id_2']}: {e}")

        # 清理过期
        expired_cleaned = self.store.delete_expired()

        # 全量重新导出
        if self.export_auto and (merged_count > 0 or expired_cleaned > 0):
            exp = self.exporter
            if exp:
                try:
                    exp.full_export(self.store.get_all(limit=10000))
                except Exception as e:
                    logger.warning(f"全量导出失败: {e}")

        logger.info(f"consolidate: merged={merged_count}, expired_cleaned={expired_cleaned}")
        return {"merged": merged_count, "deleted": merged_count, "expired_cleaned": expired_cleaned}

    def _count_expired(self) -> int:
        all_mems = self.store.get_all(limit=10000)
        now = datetime.now(timezone.utc).timestamp()
        return sum(
            1 for m in all_mems
            if m["metadata"].get("expires_at") and m["metadata"]["expires_at"] < now
        )

    # ============ list_memories() ============

    def list_memories(self, mem_type: str | None = None, scope: str | None = None,
                      limit: int = 50) -> list[dict]:
        """列出记忆（按更新时间倒序），支持按类型和作用域过滤。"""
        results = self.store.get_all(limit=limit * 2)
        memories = []
        if not results:
            return memories
        for mem in results:
            mem_type_val = mem.get("metadata", {}).get("type", "")
            mem_scope_val = mem.get("metadata", {}).get("scope", "")
            if mem_type and mem_type_val != mem_type:
                continue
            if scope and mem_scope_val != scope:
                continue
            memories.append({
                "id": mem["id"],
                "content": mem["content"],
                "type": mem_type_val,
                "metadata": mem["metadata"],
            })
        memories.sort(key=lambda m: m["metadata"].get("updated_at", ""), reverse=True)
        return memories[:limit]

    # ============ delete_memory() ============

    def delete_memory(self, mem_id: str) -> None:
        """删除单条记忆。"""
        try:
            self.store.delete(mem_id)
        except Exception as e:
            logger.warning(f"删除记忆失败 {mem_id}: {e}")

    # ============ export_md() ============

    def export_md(self, scope: str | None = None) -> Path:
        """全量导出记忆为 Markdown 文件，返回导出目录路径。"""
        memories = self.store.get_all(scope=scope, limit=10000)
        exp = self.exporter
        if exp:
            return exp.full_export(memories, scope=scope)
        return self.export_dir

    # ============ 单例管理 ============

    @classmethod
    def get_singleton(cls) -> 'MemoryManager':
        """获取全局单例（线程安全，双重检查锁）。"""
        global _manager_singleton
        if _manager_singleton is None:
            with _manager_singleton_lock:
                if _manager_singleton is None:
                    from server.config import AppConfig
                    config = AppConfig().get_all()
                    _manager_singleton = cls(
                        config=config, llm=None,
                        persist_dir=str(_DEFAULT_DATA_DIR / "chroma"),
                    )
        return _manager_singleton

    @classmethod
    def create_with_llm(cls, llm) -> 'MemoryManager':
        """创建带 LLM 的新实例（供 observer 使用，不缓存）。"""
        from server.config import AppConfig
        config = AppConfig().get_all()
        return cls(config=config, llm=llm, persist_dir=str(_DEFAULT_DATA_DIR / "chroma"))

    @classmethod
    def reset_singleton(cls) -> None:
        """重置单例（仅测试用）。"""
        global _manager_singleton
        _manager_singleton = None
