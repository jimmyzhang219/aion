"""工具注册表与自动发现

TOOL_REGISTRY 收集所有 @tool 装饰的工具，用于 _build_langchain_tools()。
_discover_tools() 自动扫描 aion.tools.builtin 下所有非 _ 开头的模块，
收集其中的 StructuredTool 实例。

新增/删除工具只需在 builtin/ 下放或删一个带 @tool 的文件，
无需修改注册代码。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)

TOOL_REGISTRY: dict[str, StructuredTool] = {}
"""全局工具注册表。key: 工具名，value: StructuredTool 实例。"""


def _discover_tools(package: str = "aion.tools.builtin") -> None:
    """自动扫描指定包下的所有模块，收集 @tool 装饰的函数。

    跳过以 _ 开头的模块（内部模块）。
    """
    try:
        pack = importlib.import_module(package)
        pack_path = getattr(pack, "__path__", None)
        if pack_path is None:
            logger.warning("_discover_tools: %s 不是包", package)
            return
        for mod_info in pkgutil.iter_modules(pack_path, prefix=f"{package}."):
            mod_name = mod_info.name
            short_name = mod_name.rsplit(".", 1)[-1]
            if short_name.startswith("_"):
                continue
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:
                logger.debug("跳过模块 %s: %s", mod_name, e)
                continue
            for _obj_name, obj in inspect.getmembers(mod):
                if isinstance(obj, StructuredTool):
                    TOOL_REGISTRY[obj.name] = obj
    except Exception as e:
        logger.warning("_discover_tools 失败: %s", e)


# 模块加载时自动发现
_discover_tools()
