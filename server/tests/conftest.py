import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def tmp_data_dir():
    """创建临时 data 目录，测试结束后清理。"""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "files").mkdir()
        (data_dir / "chroma").mkdir()
        yield data_dir


@pytest.fixture
def test_db_url(tmp_data_dir):
    """SQLite 测试数据库 URL。"""
    db_path = tmp_data_dir / "test.db"
    return f"sqlite:///{db_path}"


@pytest.fixture
def sample_pdf():
    """返回一个简单 PDF 文件的路径（用于解析测试）。"""
    import fitz
    path = Path(tempfile.gettempdir()) / "test_sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "这是测试文档内容。人工智能正在改变世界。")
    doc.save(str(path))
    doc.close()
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def sample_txt():
    """返回一个简单 TXT 文件的路径。"""
    path = Path(tempfile.gettempdir()) / "test_sample.txt"
    path.write_text("这是第一段测试内容。\n\n这是第二段测试内容。", encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)
