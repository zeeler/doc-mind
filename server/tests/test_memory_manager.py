"""MemoryManager 单元测试（mock LLM）。"""

import pytest
from unittest.mock import MagicMock
from server.services.memory_manager import MemoryManager


@pytest.fixture
def mgr(tmp_data_dir):
    config = {
        "memory_dedup_threshold": "0.85",
        "memory_recall_top_k": "5",
        "memory_export_auto": "false",
    }
    return MemoryManager(config=config, llm=None, persist_dir=str(tmp_data_dir / "chroma"))


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
        assert mid1 == mid2

    def test_recall_returns_results(self, mgr):
        """recall 返回排序后的记忆列表。"""
        mgr.memorize("项目使用 FastAPI + SQLite", mem_type="fact", scope="global")
        mgr.memorize("用户喜欢设计方案", mem_type="preference", scope="global")
        results = mgr.recall("项目用什么框架")
        assert len(results) > 0

    def test_recall_as_context_format(self, mgr):
        """recall_as_context 返回正确格式的文本。"""
        mgr.memorize("用户偏好 Rust 语言", mem_type="preference", scope="global")
        ctx = mgr.recall_as_context("编程语言偏好")
        assert "## 用户历史信息" in ctx
        assert "偏好" in ctx

    def test_recall_session_scope(self, mgr):
        """recall 支持会话级记忆搜索。"""
        mgr.memorize("本次讨论决定用方案B", mem_type="conclusion", scope="session",
                     metadata={"source_conv_id": "conv-1"})
        results = mgr.recall("方案B", conv_id="conv-1")
        assert len(results) >= 1

    def test_observe_without_llm_returns_zero(self, mgr):
        """无 LLM 时 observe 返回 0。"""
        result = mgr.observe([{"role": "user", "content": "hello"}], "conv-1")
        assert result == 0

    def test_observe_empty_messages(self, mgr):
        """空消息列表返回 0。"""
        result = mgr.observe([], "conv-1")
        assert result == 0

    def test_consolidate_dry_run(self, mgr):
        """consolidate dry_run 返回 pairs。"""
        mgr.memorize("测试内容 A", mem_type="fact", scope="global")
        mgr.memorize("测试内容 A 重复类似", mem_type="fact", scope="global")
        result = mgr.consolidate(dry_run=True)
        assert "pairs" in result
        assert "total_pairs" in result
        assert "expired_candidates" in result

    def test_consolidate_no_dry_run(self, mgr):
        """consolidate 正常模式返回 merged/deleted/expired_cleaned。"""
        result = mgr.consolidate(dry_run=False)
        assert "merged" in result
        assert "deleted" in result
        assert "expired_cleaned" in result

    def test_memorize_with_metadata(self, mgr):
        """memorize 接受自定义 metadata。"""
        mid = mgr.memorize("重要信息", mem_type="fact", scope="global",
                           metadata={"importance": 0.9, "source_conv_id": "test-123"})
        assert mid.startswith("mem-")
        # 验证记忆可以通过 recall 找到
        results = mgr.recall("重要信息")
        found = [r for r in results if r["id"] == mid]
        assert len(found) == 1
        assert found[0]["metadata"]["importance"] == 0.9
