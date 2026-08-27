"""内置工具模块

提供 LLM 可调用的内置工具。所有工具通过 @tool 装饰器自动注册到 TOOL_REGISTRY，
由 _toolkit.py 的自动发现机制收集，新增/删除工具无需修改本文件。

使用：
    from aion.tools._toolkit import TOOL_REGISTRY
    raw = TOOL_REGISTRY["read"]  # → StructuredTool with .name/.description/.args_schema
"""

from .._toolkit import TOOL_REGISTRY as TOOL_REGISTRY
