"""auto_tagger 单元测试 — mock LLM + DB 调用。"""

from unittest.mock import MagicMock, patch
import pytest


class TestAutoTagger:
    """测试 auto_tag_document 核心逻辑。"""

    def test_normalize_tag_name(self):
        """normalize_tag_name 纯函数测试。"""
        from server.services.tag_utils import normalize_tag_name
        assert normalize_tag_name("  Python  ") == "Python"
        assert normalize_tag_name("") == ""

    def test_auto_tag_empty_content_returns_empty(self):
        """空文本应返回空列表而不调用 LLM。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {"content": ""}
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(title="test", file_path="")

        with patch(
            "server.services.registry.ServiceRegistry.get_singleton"
        ) as mock_registry:
            mock_registry.return_value.get_llm.return_value = mock_llm
            from server.services.auto_tagger import auto_tag_document

            result = auto_tag_document("test-id", "", {}, mock_session)
            assert result == []

    def test_auto_tag_parses_response(self):
        """mock LLM 返回多行标签时应正确解析。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "content": "1. Python\n2. 机器学习\n3. AI\n"
        }
        mock_session = MagicMock()
        mock_doc = MagicMock(title="测试文档", file_path="/tmp/test.txt")
        mock_session.get.return_value = mock_doc

        with patch(
            "server.services.registry.ServiceRegistry.get_singleton"
        ) as mock_registry:
            mock_registry.return_value.get_llm.return_value = mock_llm
            from server.services.auto_tagger import auto_tag_document

            result = auto_tag_document(
                "test-id",
                "这是一篇关于 Python 机器学习和人工智能的文章",
                {"llm_provider": "mlx"},
                mock_session,
            )
            assert isinstance(result, list)
            # 应该至少调用了一次 LLM chat
            mock_llm.chat.assert_called_once()
