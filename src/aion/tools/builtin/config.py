"""Gateway 工具 - 配置管理

本模块提供 Gateway 配置管理功能。
允许 LLM 通过标准工具调用接口查询和修改 aion 配置。

核心设计理念：
- 无特殊拦截机制，所有操作通过 LLM 自主决策的工具调用实现
- System Prompt 包含工具描述，LLM 根据用户自然语言自主决定调用哪个工具
- 配置更新支持两种模式：部分更新（config.patch）和完整替换（config.apply）

提供的工具 action：
- config.get: 获取当前完整配置（包含 hash 用于乐观锁）
- config.patch: 部分更新配置（deep merge 模式，只更新指定字段）
- config.apply: 完整替换配置（会覆盖整个配置文件）
- config.schema_lookup: 查看指定配置路径的说明文档
- restart: 发送重启信号（由调用方处理）

安全机制：
- 保护路径检查：禁止通过 config.patch/apply 修改危险配置项
- 乐观锁：支持 base_hash 参数防止并发修改冲突

# - src/agents/tools/gateway-tool.ts（完整实现）— 历史参考，对应文件已不存在
# - src/config/merge-patch.ts（merge patch 算法）— 历史参考，对应文件已不存在

使用示例：
    # LLM 自主调用
    gateway_tool("config.get")
    gateway_tool("config.patch", raw='{"memory":{"daily_memory_days":3}}', base_hash="abc123")
    gateway_tool("config.schema_lookup", path="memory")
"""

from __future__ import annotations

import json
import hashlib
from typing import Any, Optional

from langchain_core.tools import tool

# ============================================================================
# 常量定义
# ============================================================================

# 默认配置文件路径（从 core.constants 引入，避免重复定义）
from ...core.constants import DEFAULT_CONFIG_PATH
from ...log import get_logger

logger = get_logger(__name__)

# 保护路径集合 - 这些配置项禁止通过 config.patch/apply 修改
# 原因：这些路径涉及安全敏感配置，修改可能导致系统不安全
PROTECTED_CONFIG_PATHS: set[str] = {
    "tools.exec.ask",  # exec 工具是否需要确认
    "tools.exec.security",  # exec 安全级别
    "tools.exec.safe_bins",  # 安全二进制白名单
    "tools.exec.safe_bin_profiles",  # 安全二进制配置
    "tools.exec.safe_bin_trusted_dirs",  # 安全二进制信任目录
    "tools.exec.strict_inline_eval",  # 严格内联评估
}


# ============================================================================
# 数据结构
# ============================================================================

# ============================================================================
# 内部辅助函数
# ============================================================================


def _compute_config_hash(config: dict) -> str:
    """
    计算配置的哈希值

    使用 SHA256 算法，取前16位十六进制字符串。
    用于检测配置是否发生变化，支持乐观锁机制。

    Args:
        config: 配置字典

    Returns:
        16位十六进制哈希字符串

    算法细节：
        1. 先将 dict 转为 JSON 字符串（sort_keys 确保顺序一致）
        2. 对 JSON 字符串计算 SHA256
        3. 取前16个十六进制字符（约64位，碰撞概率极低）
    """
    raw = json.dumps(config, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _read_config() -> dict:
    """
    从默认路径读取配置文件

    如果文件不存在，返回空字典。

    Returns:
        配置字典，文件不存在时返回 {}

    Raises:
        无异常，直接返回空字典
    """
    if not DEFAULT_CONFIG_PATH.exists():
        return {}
    with open(DEFAULT_CONFIG_PATH) as f:
        return json.load(f)


def _write_config(config: dict) -> None:
    """
    将配置写入默认文件路径

    会自动创建父目录，文件存在则覆盖。

    Args:
        config: 要写入的配置字典

    Note:
        使用 json.dump 格式化输出，indent=2 提高可读性
    """
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def _get_value_at_path(config: dict, path: str) -> Any:
    """
    获取配置字典中指定路径的值

    路径格式：点分隔的嵌套键，如 "agents.defaultLlm"

    Args:
        config: 配置字典
        path: 点分隔的路径，如 "memory.daily_memory_days"

    Returns:
        找到的值，如果路径不存在则返回 None

    Example:
        config = {"memory": {"daily_memory_days": 3}}
        _get_value_at_path(config, "memory.daily_memory_days") -> 3
    """
    parts = path.split(".")
    current = config
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _deep_merge(base: dict, patch: dict) -> dict:
    """
    深度合并 patch 字典到 base 字典

    递归处理嵌套字典，非字典值直接覆盖。
    这是 config.patch 的核心算法。

    Args:
        base: 基础配置字典（会被修改）
        patch: 要合并的补丁字典

    Returns:
        合并后的字典（与 base 同一对象）

    Example:
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        patch = {"b": {"c": 99}, "e": 5}
        result = _deep_merge(base, patch)
        # result = {"a": 1, "b": {"c": 99, "d": 3}, "e": 5}
    """
    result = json.loads(json.dumps(base))  # 深拷贝，避免修改原始 base
    for key, value in patch.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # 两者都是字典，递归合并
            result[key] = _deep_merge(result[key], value)
        else:
            # 直接覆盖
            result[key] = value
    return result


def _check_protected_paths_change(base: dict, next_config: dict) -> list[str]:
    """
    检查配置变更是否涉及保护路径

    Args:
        base: 修改前的配置
        next_config: 修改后的配置

    Returns:
        被修改的保护路径列表，如果无修改则为空列表
    """
    changed = []
    for path in PROTECTED_CONFIG_PATHS:
        base_val = _get_value_at_path(base, path)
        next_val = _get_value_at_path(next_config, path)
        if base_val != next_val:
            changed.append(path)
    return changed


# ============================================================================
# 公共 API 函数
# ============================================================================


def config_get() -> dict:
    """
    获取当前完整配置

    返回配置内容和哈希值，哈希值用于后续修改时的乐观锁验证。

    Returns:
        包含以下字段的字典：
        - ok: bool，操作是否成功
        - config: 完整的配置字典
        - hash: 配置内容的哈希值（用于 base_hash 验证）

    LLM 使用场景：
        在执行 config.patch 或 config.apply 之前，LLM 应该先调用此方法
        获取当前配置的 hash，以便在修改时传入 base_hash 防止并发冲突。
    """
    config = _read_config()
    return {
        "ok": True,
        "config": config,
        "hash": _compute_config_hash(config),
    }


def config_patch(raw: str, base_hash: Optional[str] = None) -> dict:
    """
    部分更新配置（merge 模式）

    将传入的 JSON patch 与当前配置深度合并，只更新指定的字段。
    这是推荐的配置更新方式，比 config.apply 更安全。

    Args:
        raw: JSON 格式的 patch 内容，如 '{"memory":{"daily_memory_days":3}}'
        base_hash: 当前配置的哈希值（可选，用于乐观锁验证）

    Returns:
        包含以下字段的字典：
        - ok: bool，操作是否成功
        - error: 如果失败，错误原因
        - message: 如果成功，操作说明
        - hash: 更新后配置的哈希值

    安全机制：
        1. base_hash 验证：如果传入 base_hash，会验证当前配置是否发生变化
        2. 保护路径检查：禁止修改危险配置项

    错误情况：
        - Invalid JSON: raw 不是有效的 JSON
        - not a JSON object: raw 不是对象类型
        - base_hash mismatch: 配置已被其他操作修改
        - Cannot modify protected paths: 尝试修改保护路径

    Example:
        # 将 memory.daily_memory_days 更新为 3
        config_patch('{"memory":{"daily_memory_days":3}}', base_hash="abc123")
    """
    # 解析 patch
    try:
        patch = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("[config] patch Invalid JSON: %s", e)
        return {"ok": False, "error": f"Invalid JSON: {e}"}

    if not isinstance(patch, dict):
        logger.warning("[config] patch 不是 JSON 对象")
        return {"ok": False, "error": "patch must be a JSON object"}

    # 读取当前配置
    current = _read_config()

    # 验证 base_hash（乐观锁）
    # 如果其他操作已经修改了配置，base_hash 会不匹配
    if base_hash:
        current_hash = _compute_config_hash(current)
        if current_hash != base_hash:
            logger.warning("[config] patch base_hash 不匹配")
            return {"ok": False, "error": "Configuration has been modified by others, base_hash mismatch"}

    # 计算合并后的配置
    next_config = _deep_merge(current, patch)

    # 检查是否修改了保护路径
    changed_protected = _check_protected_paths_change(current, next_config)
    if changed_protected:
        logger.warning("[config] patch 试图修改保护路径: %s", changed_protected)
        return {"ok": False, "error": f"Cannot modify protected config paths: {', '.join(changed_protected)}"}

    # 写入更新后的配置
    _write_config(next_config)

    return {
        "ok": True,
        "message": "Configuration updated successfully",
        "hash": _compute_config_hash(next_config),
    }


def config_apply(raw: str, base_hash: Optional[str] = None) -> dict:
    """
    完整替换配置

    用传入的完整配置替换当前配置。注意：这是覆盖式替换，
    不是 merge。如果只想更新部分字段，请使用 config_patch。

    Args:
        raw: JSON 格式的完整配置，如 '{"workspace":"default","models":{...}}'
        base_hash: 当前配置的哈希值（可选，用于乐观锁验证）

    Returns:
        包含以下字段的字典：
        - ok: bool，操作是否成功
        - error: 如果失败，错误原因
        - message: 如果成功，操作说明
        - hash: 更新后配置的哈希值

    安全机制：
        1. base_hash 验证：防止覆盖未考虑的并发修改
        2. 保护路径检查：禁止修改危险配置项

    Warning:
        此方法会完全替换配置文件。如果只想要更新部分配置，
        请使用 config_patch 以避免丢失未指定的配置项。

    Example:
        # 完整替换配置
        config_apply('{"workspace":"new","models":{"providers":{}}}', base_hash="abc123")
    """
    # 解析配置
    try:
        new_config = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("[config] apply Invalid JSON: %s", e)
        return {"ok": False, "error": f"Invalid JSON: {e}"}

    if not isinstance(new_config, dict):
        logger.warning("[config] apply 不是 JSON 对象")
        return {"ok": False, "error": "config must be a JSON object"}

    # 读取当前配置
    current = _read_config()

    # 验证 base_hash
    if base_hash:
        current_hash = _compute_config_hash(current)
        if current_hash != base_hash:
            logger.warning("[config] apply base_hash 不匹配")
            return {"ok": False, "error": "Configuration has been modified by others, base_hash mismatch"}

    # 检查保护路径
    changed_protected = _check_protected_paths_change(current, new_config)
    if changed_protected:
        logger.warning("[config] apply 试图修改保护路径: %s", changed_protected)
        return {"ok": False, "error": f"Cannot modify protected config paths: {', '.join(changed_protected)}"}

    # 写入新配置（完全覆盖）
    _write_config(new_config)

    return {
        "ok": True,
        "message": "Configuration applied successfully",
        "hash": _compute_config_hash(new_config),
    }


def config_schema_lookup(path: str) -> dict:
    """
    查看配置项的 schema 说明

    返回指定配置路径的描述信息，帮助 LLM 理解可以修改哪些配置。

    Args:
        path: 配置路径，如 "memory"、"agents.defaultLlm"

    Returns:
        包含以下字段的字典：
        - ok: bool，是否找到
        - schema: 配置项的描述字符串
        - error: 如果 ok=False，错误原因

    Note:
        目前 schema 信息是静态定义的，未来可改为从 schema.py 动态获取。

    Example:
        config_schema_lookup("memory")
        # -> {"ok": True, "schema": "记忆配置，包含 daily_memory_days, max_file_chars 等"}
    """
    # 静态 schema 映射表
    # key: 配置路径，value: 描述字符串
    schemas = {
        "workspace": "工作空间名称，字符串类型",
        "models": "模型配置，包含 providers 字典",
        "agents": "Agent 配置字典，key 是 agent ID",
        "agents.defaultLlm": "默认 LLM provider 名称",
        "memory": "记忆配置，包含 daily_memory_days, max_file_chars 等",
        "compaction": "压缩配置，包含 trigger_ratio, max_tokens 等",
        "pruning": "裁剪配置，包含 max_messages, keep_recent 等",
        "mcpServers": "MCP 服务器配置（dict 格式，key 为服务器名）",
    }

    # 精确匹配
    if path in schemas:
        return {"ok": True, "schema": schemas[path]}

    # 尝试返回父路径的描述（如果父路径存在）
    parent = ".".join(path.split(".")[:-1])
    if parent in schemas:
        return {"ok": True, "schema": f"{schemas[parent]}（包含子项）"}

    # 未找到
    logger.warning("[config] schema_lookup 未知路径: %s", path)
    return {"ok": False, "error": f"Unknown config path: {path}"}


@tool("gateway")
def gateway_tool(action: str, **kwargs) -> dict:
    """管理 Gateway 配置：查询、更新配置或重启服务。
    使用 config.get 查看当前配置，config.schema_lookup 查看配置项说明，
    config.patch 进行部分更新（与现有配置合并），config.apply 完整替换配置。
    修改配置前先用 schema_lookup 检查配置路径。

    Args:
        action: 操作类型，支持：
            - "config.get": 获取当前配置
            - "config.patch": 部分更新配置（合并）
            - "config.apply": 完整替换配置
            - "config.schema_lookup": 查看配置项说明
            - "restart": 发送重启信号
            - "update.run": 发送热重载信号
        **kwargs: 传递给具体处理函数的参数
    """
    # 按 action 分发到具体配置 API（无隐式默认分支外的副作用）
    if action == "config.get":
        return config_get()
    elif action == "config.patch":
        return config_patch(
            raw=kwargs.get("raw", "{}"),
            base_hash=kwargs.get("base_hash"),
        )
    elif action == "config.apply":
        return config_apply(
            raw=kwargs.get("raw", "{}"),
            base_hash=kwargs.get("base_hash"),
        )
    elif action == "config.schema_lookup":
        path = kwargs.get("path", "")
        if not path:
            logger.warning("[config] schema_lookup path 为空")
            return {"ok": False, "error": "path is required for config.schema_lookup"}
        return config_schema_lookup(path)
    elif action == "restart":
        # Gateway 重启信号由调用方处理，这里只返回成功
        # 实际的重启逻辑在 Gateway server 中实现
        return {"ok": True, "message": "Restart signal sent"}
    elif action == "update.run":
        # 热重载配置（SIGUSR1），由调用方处理
        return {"ok": True, "message": "Config reload signal sent (hot-reload)"}
    else:
        logger.warning("[config] 未知 action: %s", action)
        return {"ok": False, "error": f"Unknown action: {action}"}


# ============================================================================
