"""AnySearch 客户端测试 — JSON-RPC 错误识别（HTTP 200 + error 体）。"""

from unittest.mock import MagicMock, patch

import pytest

from server.services.anysearch import AnySearchClient


def _mock_client_with_body(body: dict):
    """构造返回指定 JSON 体的 httpx.Client mock。"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = body
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    return mock_client


class TestJsonRpcError:
    def test_jsonrpc_error_returns_empty(self):
        """JSON-RPC 错误（如无效 key）不应被当作「无结果」。"""
        body = {"jsonrpc": "2.0", "id": 1,
                "error": {"code": -32001, "message": "invalid api key"}}
        mock_client = _mock_client_with_body(body)

        with patch("server.services.anysearch.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_client
            client = AnySearchClient(api_key="bad-key")
            assert client.search("测试") == []

    def test_jsonrpc_error_raises_when_asked(self):
        """raise_errors=True 时 JSON-RPC 错误必须抛出（连接测试才能识别无效 key）。"""
        body = {"jsonrpc": "2.0", "id": 1,
                "error": {"code": -32001, "message": "invalid api key"}}
        mock_client = _mock_client_with_body(body)

        with patch("server.services.anysearch.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_client
            client = AnySearchClient(api_key="bad-key")
            with pytest.raises(RuntimeError, match="invalid api key"):
                client.search("测试", raise_errors=True)

    def test_jsonrpc_error_string_form(self):
        """error 字段为非 dict 时也能正确处理。"""
        body = {"jsonrpc": "2.0", "id": 1, "error": "unauthorized"}
        mock_client = _mock_client_with_body(body)

        with patch("server.services.anysearch.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_client
            client = AnySearchClient(api_key="bad-key")
            with pytest.raises(RuntimeError, match="unauthorized"):
                client.search("测试", raise_errors=True)

    def test_normal_result_still_parsed(self):
        """正常 JSON-RPC 结果不受影响。"""
        import json
        body = {
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(
                {"results": [{"title": "t", "url": "http://x", "snippet": "s"}]}
            )}]},
        }
        mock_client = _mock_client_with_body(body)

        with patch("server.services.anysearch.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_client
            client = AnySearchClient(api_key="good-key")
            results = client.search("测试")
            assert len(results) == 1
            assert results[0]["content"] == "s"
            assert results[0]["match_type"] == "web"
