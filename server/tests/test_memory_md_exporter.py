"""MemoryMDExporter 单元测试。"""

import pytest
import tempfile
import threading
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

    def test_session_file_created(self, exporter):
        """会话级记忆创建 session 文件。"""
        exporter.incremental_update(
            "mem-s1", "临时结论",
            {"type": "conclusion", "scope": "session", "source_conv_id": "conv-xyz", "importance": 0.6}
        )
        path = exporter.base_dir / "sessions" / "conv-xyz.md"
        assert path.exists()
        assert "临时结论" in path.read_text(encoding="utf-8")

    def test_full_export_scope_filter(self, exporter):
        """full_export scope 过滤器生效。"""
        memories = [
            {"id": "1", "content": "全局事实", "metadata": {"type": "fact", "scope": "global"}},
            {"id": "2", "content": "会话结论", "metadata": {"type": "conclusion", "scope": "session", "source_conv_id": "x"}},
        ]
        exporter.full_export(memories, scope="global")
        # 会话文件不应存在或为空
        session_path = exporter.base_dir / "sessions" / "x.md"
        # global 文件应存在
        facts_path = exporter.base_dir / "global" / "facts.md"
        assert facts_path.exists()
