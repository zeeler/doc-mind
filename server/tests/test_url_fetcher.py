"""url_fetcher SSRF 防护测试 — 重定向逐跳检查、fail-closed、响应体截断。"""

import socket

import httpx
import pytest

from server.services import url_fetcher
from server.services.url_fetcher import fetch_url


def _make_client_cls(transport):
    """构造注入 MockTransport 的 httpx.Client 子类。"""
    class MockClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)
    return MockClient


class TestSsrfRedirect:
    def test_redirect_to_private_host_blocked_before_request(self, monkeypatch):
        """重定向到内网地址时，必须先检查再请求 — 内网请求不应被发出。"""
        requested = []

        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data"}
            )

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            "server.services.url_fetcher.httpx.Client", _make_client_cls(transport)
        )
        # 首跳 host 视为公网，重定向落点视为内网
        monkeypatch.setattr(
            "server.services.url_fetcher._is_private_host",
            lambda host: host != "public.example.com",
        )

        result = fetch_url("http://public.example.com/redirect")

        assert result["error"] == "不允许访问内网地址（重定向）"
        # 关键断言：只有首跳请求被发出，内网目标从未被请求
        assert requested == ["http://public.example.com/redirect"]

    def test_redirect_to_public_host_followed(self, monkeypatch):
        """正常公网重定向应能跟随并取回内容。"""
        def handler(request):
            if str(request.url).endswith("/start"):
                return httpx.Response(302, headers={"location": "/final"})
            return httpx.Response(
                200,
                text="<html><head><title>标题</title></head><body><article>正文内容</article></body></html>",
            )

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            "server.services.url_fetcher.httpx.Client", _make_client_cls(transport)
        )
        monkeypatch.setattr(
            "server.services.url_fetcher._is_private_host", lambda host: False
        )

        result = fetch_url("http://public.example.com/start")

        assert result["error"] is None
        assert result["title"] == "标题"
        assert "正文内容" in result["text_content"]

    def test_too_many_redirects(self, monkeypatch):
        """超过最大跳数应报错而不是无限跟随。"""
        def handler(request):
            return httpx.Response(302, headers={"location": "/loop"})

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            "server.services.url_fetcher.httpx.Client", _make_client_cls(transport)
        )
        monkeypatch.setattr(
            "server.services.url_fetcher._is_private_host", lambda host: False
        )

        result = fetch_url("http://public.example.com/loop")

        assert result["error"] == "重定向次数过多"


class TestPrivateHostCheck:
    def test_dns_failure_fail_closed(self, monkeypatch):
        """DNS 解析失败应视为危险（fail-closed），而不是放行。"""
        def raise_gaierror(host, port):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", raise_gaierror)
        assert url_fetcher._is_private_host("nonexistent.invalid") is True

    def test_loopback_blocked(self):
        assert url_fetcher._is_private_host("127.0.0.1") is True
        assert url_fetcher._is_private_host("localhost") is True

    def test_private_ip_blocked(self):
        assert url_fetcher._is_private_host("192.168.1.1") is True
        assert url_fetcher._is_private_host("169.254.169.254") is True

    def test_empty_host_blocked(self):
        assert url_fetcher._is_private_host(None) is True
        assert url_fetcher._is_private_host("") is True
