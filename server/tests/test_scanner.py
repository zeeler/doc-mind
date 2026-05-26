from server.services.scanner import quick_scan, build_index_md


class TestScanner:
    def test_quick_scan_txt(self, sample_txt):
        info = quick_scan(sample_txt)
        assert info["format"] == "txt"
        assert info["title"] is not None
        assert len(info["preview"]) > 0
        assert info["size_bytes"] > 0

    def test_build_index_md_basic(self):
        info = {"title":"测试","format":"pdf","page_count":10,"size_bytes":1024,"status":"scanned","preview":"预览文本"}
        md = build_index_md(info)
        assert "测试" in md
        assert "pdf" in md
        assert "10" in md
        assert "预览文本" in md

    def test_build_index_md_with_full_text(self):
        info = {"title":"Doc","format":"txt","page_count":0,"size_bytes":100,"status":"done"}
        md = build_index_md(info, full_text="完整内容")
        assert "完整内容" in md
        assert "预览" not in md
