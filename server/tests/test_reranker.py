"""Reranker 服务回归测试。"""
import pytest
from unittest.mock import patch, MagicMock
from server.services.reranker import Reranker, QUERY_TEMPLATE, DOC_TEMPLATE


class TestReranker:
    def test_disabled_when_flag_false(self):
        """reranker_enabled=false 时不应启用。"""
        config = {
            "reranker_enabled": "false",
            "reranker_model": "bge",
            "reranker_api_base": "http://localhost:8088/v1",
            "reranker_api_key": "",
        }
        rk = Reranker(config)
        assert rk.enabled is False

    def test_disabled_when_model_empty(self):
        """模型名为空时不应启用。"""
        config = {
            "reranker_enabled": "true",
            "reranker_model": "",
            "reranker_api_base": "http://localhost:8088/v1",
        }
        rk = Reranker(config)
        assert rk.enabled is False

    def test_url_with_v1_suffix(self):
        """Bug: base_url 已含 /v1 时不应拼接出 /v1/v1/rerank。"""
        config = {
            "reranker_enabled": "true",
            "reranker_model": "bge",
            "reranker_api_base": "http://localhost:8088/v1",
            "reranker_api_key": "",
        }
        rk = Reranker(config)
        assert rk._url == "http://localhost:8088/v1/rerank"
        assert "/v1/v1/" not in rk._url

    def test_url_without_v1_suffix(self):
        """base_url 不含 /v1 时应自动补全。"""
        config = {
            "reranker_enabled": "true",
            "reranker_model": "bge",
            "reranker_api_base": "http://localhost:8088",
            "reranker_api_key": "",
        }
        rk = Reranker(config)
        assert rk._url == "http://localhost:8088/v1/rerank"

    def test_template_format_applied(self):
        """Bug: 模板格式未应用时 vLLM 兼容性差（用户反馈）。"""
        query = "测试查询"
        doc = "测试文档"
        formatted_query = QUERY_TEMPLATE.format(query=query)
        formatted_doc = DOC_TEMPLATE.format(doc=doc)

        assert "<|im_start|>user" in formatted_query
        assert "<Instruct>" in formatted_query
        assert query in formatted_query
        assert "<|im_end|>" in formatted_query
        assert "<Document>" in formatted_doc
        assert doc in formatted_doc
        assert "<|im_end|>" in formatted_doc

    def test_rerank_chunks_returns_none_when_disabled(self):
        """禁用时应返回 None，让调用方使用原始排序。"""
        config = {"reranker_enabled": "false", "reranker_model": "", "reranker_api_base": ""}
        rk = Reranker(config)
        result = rk.rerank_chunks("query", [{"content": "test"}], top_k=5)
        assert result is None

    def test_rerank_chunks_returns_none_on_api_failure(self):
        """Bug: API 失败时返回假分数覆盖原始排序结果。现在应返回 None。"""
        config = {
            "reranker_enabled": "true",
            "reranker_model": "bge",
            "reranker_api_base": "http://localhost:8088/v1",
        }
        rk = Reranker(config)
        # 让 rerank() 内部请求失败
        with patch.object(rk, "rerank", return_value=[]):
            result = rk.rerank_chunks("query", [
                {"content": "a", "score": 0.9},
                {"content": "b", "score": 0.8},
            ], top_k=2)
            assert result is None, "API 失败应返回 None 而非覆盖原始结果"

    def test_rerank_chunks_single_document(self):
        """单文档时直接返回，不调用 API。"""
        config = {
            "reranker_enabled": "true",
            "reranker_model": "bge",
            "reranker_api_base": "http://localhost:8088/v1",
        }
        rk = Reranker(config)
        result = rk.rerank_chunks("query", [{"content": "only one"}], top_k=5)
        assert result is not None
        assert len(result) == 1

    @patch("server.services.reranker.requests.post")
    def test_rerank_success(self, mock_post):
        """正常精排流程返回正确排序结果。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.98},
                {"index": 0, "relevance_score": 0.12},
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        config = {
            "reranker_enabled": "true",
            "reranker_model": "bge",
            "reranker_api_base": "http://localhost:8088/v1",
        }
        rk = Reranker(config)
        chunks = [
            {"content": "不相关内容", "score": 0.8},
            {"content": "高度相关内容", "score": 0.7},
        ]
        result = rk.rerank_chunks("相关查询", chunks, top_k=2)
        assert result is not None
        assert len(result) == 2
        # 索引 1 的内容应该排在第一位（score 0.98 > 0.12）
        assert "高度相关" in result[0]["content"]
        assert result[0]["rerank_score"] == 0.98

    @patch("server.services.reranker.requests.post")
    def test_rerank_request_format(self, mock_post):
        """验证 API 请求格式：包含 model、query（模板格式化）、documents（模板格式化）、top_n。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [{"index": 0, "relevance_score": 0.9}]
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        config = {
            "reranker_enabled": "true",
            "reranker_model": "test-reranker",
            "reranker_api_base": "http://localhost:8088/v1",
        }
        rk = Reranker(config)
        rk.rerank("测试查询", ["文档内容"], top_k=3)

        # 验证请求体
        call_args = mock_post.call_args
        body = call_args[1]["json"]
        assert body["model"] == "test-reranker"
        assert "<|im_start|>" in body["query"]
        assert "<Instruct>" in body["query"]
        assert "测试查询" in body["query"]
        assert "<Document>" in body["documents"][0]
        assert body["top_n"] == 3
