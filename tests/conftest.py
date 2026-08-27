"""pytest 全局配置"""

from aion.log import configure_logging


def pytest_configure(config):
    """在测试 session 启动时初始化 logging（文件 handler），
    确保 dispatch_message / AgentLoop 等模块的日志能写入
    ~/.aion/logs/aion-{today}.log。
    """
    configure_logging()
    config.addinivalue_line("markers", "integration: marks tests that require API keys or external services")
