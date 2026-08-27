"""tests/test_services_base.py"""

import pytest


# 仅测试基类结构 — 确保 ABC 约束正确
def test_service_manager_is_abstract():
    """ServiceManager 应定义所有必需抽象方法"""
    from aion.cli.services.base import ServiceManager
    from abc import ABC

    assert issubclass(ServiceManager, ABC)
    for name in ("start", "stop", "find_pids", "is_running"):
        assert hasattr(ServiceManager, name)


def test_service_manager_enforces_abstract_methods():
    """未实现所有抽象方法的子类在实例化时应报 TypeError"""
    from aion.cli.services.base import ServiceManager

    class IncompleteManager(ServiceManager):
        pass  # 没有实现任何抽象方法

    with pytest.raises(TypeError):
        IncompleteManager()
