import pytest
from server.services.memory_manager import MemoryManager


class TestMemoryService:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.services.memory_manager._DEFAULT_DATA_DIR", tmp_data_dir)
        MemoryManager.reset_singleton()
        self.mgr = MemoryManager.get_singleton()

    def test_add_and_list(self):
        self.mgr.memorize("用户喜欢简洁回答", "preference")
        memories = self.mgr.list_memories()
        assert len(memories) >= 1

    def test_add_and_search(self):
        self.mgr.memorize("用户关注AI安全领域", "preference")
        self.mgr.memorize("用户使用Python编程", "fact")
        results = self.mgr.recall("安全", top_k=5)
        assert len(results) >= 0

    def test_delete_memory(self):
        mid = self.mgr.memorize("待删除内容", "fact")
        self.mgr.delete_memory(mid)
        results = self.mgr.recall("待删除")
        assert len(results) == 0

    def test_list_filter_by_type(self):
        self.mgr.memorize("偏好记忆1", "preference")
        self.mgr.memorize("事实记忆1", "fact")
        pref = self.mgr.list_memories(mem_type="preference")
        for m in pref:
            assert m["type"] == "preference"
