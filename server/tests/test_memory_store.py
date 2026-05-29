import pytest
from server.services.memory_store import MemoryStore


class TestMemoryStore:
    @pytest.fixture
    def store(self, tmp_data_dir):
        return MemoryStore(persist_dir=str(tmp_data_dir / "chroma"))

    def test_add_and_search(self, store):
        store.add("mem-1", "用户喜欢简洁的回答风格", {"type": "preference", "count": 1})
        store.add("mem-2", "用户关注AI安全领域", {"type": "preference", "count": 1})

        results = store.search("回答风格", top_k=5)
        assert len(results) > 0
        assert any("简洁" in r["content"] for r in results)

    def test_add_memory_returns_id(self, store):
        mid = store.add("mem-3", "测试记忆", {"type": "fact"})
        assert mid == "mem-3"

    def test_add_memory_generates_id(self, store):
        mid = store.add(None, "测试记忆", {"type": "fact"})
        assert mid.startswith("mem-")
        assert len(mid) > 4

    def test_delete_memory(self, store):
        mid = store.add("mem-4", "待删除", {"type": "fact"})
        store.delete(mid)
        results = store.search("待删除", top_k=5)
        assert len(results) == 0

    def test_update_memory(self, store):
        mid = store.add("mem-5", "原始内容", {"type": "fact", "count": 1})
        store.update(mid, "更新后内容", {"type": "fact", "count": 2})
        results = store.search("更新后内容", top_k=5)
        assert len(results) > 0

    def test_count(self, store):
        assert store.count() == 0
        store.add("mem-6", "内容", {"type": "fact"})
        assert store.count() == 1
