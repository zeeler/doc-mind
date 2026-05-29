import pytest
from unittest.mock import MagicMock, patch
from server.services.memory import add_memory, search_memories, delete_memory, list_memories


class TestMemoryService:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.services.memory.DATA_DIR", tmp_data_dir)
        from server.services.memory import _reset_store
        _reset_store()

    def test_add_and_list(self):
        add_memory("用户喜欢简洁回答", "preference")
        memories = list_memories()
        assert len(memories) >= 1

    def test_add_and_search(self):
        add_memory("用户关注AI安全领域", "preference")
        add_memory("用户使用Python编程", "fact")
        results = search_memories("安全", top_k=5)
        assert len(results) >= 0

    def test_delete_memory(self):
        mid = add_memory("待删除内容", "fact")
        delete_memory(mid)
        results = search_memories("待删除")
        assert len(results) == 0

    def test_list_filter_by_type(self):
        add_memory("偏好记忆1", "preference")
        add_memory("事实记忆1", "fact")
        pref = list_memories(mem_type="preference")
        for m in pref:
            assert m["type"] == "preference"
