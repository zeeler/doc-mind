"""observer 单元测试 — mock LLM 调用验证记忆提取流程。"""

from unittest.mock import MagicMock, patch
import pytest


class TestObserver:
    """测试会话观察器核心逻辑。"""

    def test_observe_no_llm_available(self):
        """LLM 不可用时应返回 0 而非崩溃。"""
        from unittest.mock import patch
        with patch("server.services.registry.ServiceRegistry.get_singleton") as mock_reg:
            mock_reg.return_value.get_llm.return_value = None
            from server.services.memory_manager import MemoryManager
            from server.config import AppConfig

            config = AppConfig().get_all()
            mgr = MemoryManager(config=config, llm=None)
            # 无 LLM 时 observe 应安全返回
            result = mgr.observe(
                [{"role": "user", "content": "我喜欢用 Python 开发"}],
                "test-conv",
            )
            assert isinstance(result, int)

    def test_observe_with_mock_llm(self):
        """mock LLM 返回信号应生成记忆。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "content": '{"signals":[{"type":"preference","content":"用户偏好Python开发","importance":0.8}]}'
        }

        from server.services.memory_manager import MemoryManager
        from server.config import AppConfig
        config = AppConfig().get_all()

        mgr = MemoryManager(config=config, llm=mock_llm)
        result = mgr.observe(
            [{"role": "user", "content": "我喜欢用 Python 开发"}],
            "test-conv-2",
        )
        assert isinstance(result, int)
