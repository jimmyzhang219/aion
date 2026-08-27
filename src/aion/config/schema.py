"""配置 schema（Pydantic）

多工作空间配置架构 v5：
- models: 直接是模型字典
- workspaces.scopes: 数组格式，每个元素是一个工作空间（key 是工作空间名字）
- workspaces.current: 字符串，表示当前工作空间名字
- channels: dict 格式，key 是 channel 名称（如 feishu）
- agents: leader 是字符串引用 + 具体 agent 配置
- log_level: 全局日志级别（默认 info）

配置示例：
{
    "models": {
        "deepseek": { "model": "deepseek-chat", "apiKey": "..." }
    },
    "workspaces": {
        "scopes": [
            {
                "default": {
                    "agents": {
                        "leader": "main",
                        "main": { "provider": "deepseek", "fallback": [] }
                    },
                    "memory": {...},
                    "mcpServers": {...}
                }
            }
        ],
        "current": "default"
    },
    "channels": {
        "feishu": {
            "enabled": true,
            "connectionMode": "websocket",
            "appId": "...",
            "appSecret": "...",
            "domain": "feishu"
        }
    },
    "log_level": "info"
}
"""

from dataclasses import dataclass, field
from typing import Literal, Optional, Dict, Any

from pydantic import BaseModel, Field

from .models import EmbeddingConfig  # noqa: F401  # EmbeddingConfig 用于 memory embedding 配置
from .models import ASRConfig  # ASRConfig 用于 ASR 语音识别配置


@dataclass
class MCPServerConfig:
    """MCP 服务器配置（标准 dict 格式）"""

    command: Optional[str] = None  # stdio 模式下的启动命令
    args: list[str] = field(default_factory=list)  # stdio 命令参数列表
    url: Optional[str] = None  # HTTP 模式下的服务 URL
    transport: Optional[str] = None  # 传输类型："streamable-http" / "sse"


@dataclass
class _GatewayConfig:
    """Gateway HTTP 服务配置。"""

    port: int = 19527  # 监听端口


@dataclass
class LangfuseConfig:
    """Langfuse 可观测性配置。"""

    enabled: bool = False
    secret_key: str = ""
    public_key: str = ""
    host: str = "https://your-langfuse-instance.com"
    flush_interval: int = 30
    trace_level: str = "full"  # "full" | "llm_only"
    debug: bool = False  # SDK v4 debug 模式（写入 stderr）


class AgentMemoryConfig(BaseModel):
    """Agent 记忆与启动上下文注入配置。"""

    enabled: bool = True  # 是否启用记忆系统
    startup_context_enabled: bool = Field(default=True)  # 是否在会话启动时注入上下文
    daily_memory_days: int = Field(default=2)  # 加载最近 N 天的每日记忆文件
    max_file_bytes: int = Field(default=16384)  # 单文件最大读取字节数
    max_file_chars: int = Field(default=1200)  # 单文件注入最大字符数
    max_total_chars: int = Field(default=2800)  # 记忆注入总字符上限
    bootstrap_max_chars: int = Field(default=20000)  # 单个 bootstrap 文件最大字符数
    bootstrap_total_max_chars: int = Field(default=150000)  # bootstrap 文件合计上限
    memory_search: bool = Field(default=True)  # 是否启用 memory_search 工具
    memory_get: bool = Field(default=True)  # 是否启用 memory_get 工具
    context_injection: str = Field(default="always")  # 上下文注入策略（always 等）
    embedding: Optional[EmbeddingConfig] = None  # Embedding 模型配置（None=降级为关键词搜索）


class ThinkingConfig(BaseModel):
    """思维链(CoT)与推理内容显示配置。"""

    thinking_level: str = Field(default="off")  # 思维深度：off/minimal/low/medium/high 等
    reasoning_level: str = Field(default="off")  # 推理显示：off/on/stream（stream 暂未实现）


class CompactionConfig(BaseModel):
    """会话上下文压缩（Compaction）策略配置。"""

    enabled: bool = True  # 是否启用自动压缩
    trigger_ratio: float = Field(default=0.8)  # 上下文用量达窗口比例时触发
    keep_recent: int = Field(default=4)  # 保留最近 N 条消息不压缩
    use_checkpoint: bool = Field(default=True)  # 是否使用 checkpoint 机制


class PruningConfig(BaseModel):
    """消息历史裁剪（Pruning）策略配置。"""

    enabled: bool = Field(default=True)  # 是否启用裁剪
    max_messages: int = Field(default=30)  # 上下文最大消息条数
    keep_recent: int = Field(default=6)  # 始终保留最近 N 条
    keep_system: bool = Field(default=True)  # 是否保留 system 消息
    max_context_chars: Optional[int] = Field(default=50000)  # 上下文总字符上限


class WorkspaceConfig(BaseModel):
    """单个工作空间的完整配置（agents、记忆、压缩、MCP 等）。"""

    agents: Dict[str, Any] = Field(default_factory=dict)  # leader + 各 agent 配置
    compaction: Optional[CompactionConfig] = None  # 上下文压缩策略
    pruning: Optional[PruningConfig] = None  # 消息裁剪策略
    max_tool_rounds: int = 50  # Agent 单轮对话最大工具调用轮数
    thinking: Optional[ThinkingConfig] = None  # 思维链(CoT)显示配置
    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=dict, alias="mcpServers"
    )  # MCP 服务器配置（key=服务器名）
    execution_mode: Literal["react", "plan"] = "react"

    def get_leader(self) -> str:
        """获取 leader agent 的名称。

        Returns:
            leader 字段值，缺省为 ``main``。
        """
        return self.agents.get("leader", "main")

    def get_agent_config(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """获取指定 agent 的配置。

        Args:
            agent_id: Agent 标识符。

        Returns:
            Agent 配置字典；不存在时返回 None。
        """
        return self.agents.get(agent_id)


class WorkspacesConfig(BaseModel):
    """workspaces 配置 v5：scopes 数组 + current 当前工作空间名。"""

    scopes: list[Dict[str, WorkspaceConfig]] = Field(default_factory=list)  # 工作空间列表
    current: str  # 当前激活的工作空间名称

    def get_workspace(self, name: str) -> Optional[WorkspaceConfig]:
        """根据名称在 scopes 中查找工作空间。

        Args:
            name: 工作空间名称。

        Returns:
            ``WorkspaceConfig`` 实例；未找到返回 None。
        """
        for scope in self.scopes:
            if name in scope:
                return scope[name]
        return None

    def get_current_workspace(self) -> Optional[WorkspaceConfig]:
        """获取当前工作空间配置。

        Returns:
            ``workspaces.current`` 对应配置；不存在时回退到 scopes 第一项。
        """
        ws = self.get_workspace(self.current)
        if ws is None and self.scopes:
            # fallback to first workspace in scopes
            first_scope = self.scopes[0]
            if first_scope:
                return next(iter(first_scope.values()))
        return ws


def resolve_search_provider(search_cfg: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """解析当前启用的搜索 provider。

    支持新嵌套结构（provider + providers）与旧平铺结构（apiKey/url）。
    旧平铺自动视作 providers.bocha + provider="bocha"（只读兼容，不写回文件）。

    Args:
        search_cfg: ``Config.search`` 字典（即 aion.json 的 ``search`` 段）。

    Returns:
        ``(provider_id, cfg_dict)``；未配置或所选 provider 缺 apiKey 时返回 None。
    """
    ws = search_cfg.get("webSearch", {})
    if not isinstance(ws, dict):
        return None
    if "providers" not in ws:
        # 旧平铺结构
        api_key = ws.get("apiKey", "")
        if not api_key:
            return None
        return "bocha", {"apiKey": api_key}
    providers = ws.get("providers") or {}
    provider_id = ws.get("provider") or "bocha"
    cfg = providers.get(provider_id) or {}
    if not isinstance(cfg, dict) or not cfg.get("apiKey"):
        return None
    return provider_id, cfg


class Config(BaseModel):
    """主配置 v5：models、workspaces、channels、log_level 等全局设置。"""

    models: Dict[str, Any] = Field(default_factory=dict)  # 系统级 LLM 模型配置
    search: Dict[str, Any] = Field(default_factory=dict)  # 全局搜索 API 配置（如 webSearch）
    workspaces: WorkspacesConfig = Field(default_factory=lambda: WorkspacesConfig(current=""))  # 多工作空间
    channels: Dict[str, Any] = Field(default_factory=dict)  # 消息 Channel 配置（如 feishu）
    gateway: _GatewayConfig = Field(default_factory=_GatewayConfig)  # Gateway 服务配置
    memory: Optional[AgentMemoryConfig] = None  # 全局记忆配置（唯一生效的 memory 配置）
    log_level: str = Field(default="info")  # 全局日志级别
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)  # Langfuse 可观测性
    asr: Optional[ASRConfig] = None  # ASR 语音识别配置（None=不启用）

    def get_current_workspace(self) -> Optional[WorkspaceConfig]:
        """获取当前工作空间配置。

        Returns:
            ``workspaces.current`` 对应的 ``WorkspaceConfig``；不存在时回退 scopes 首项。
        """
        return self.workspaces.get_current_workspace()

    def get_workspace(self, name: str) -> Optional[WorkspaceConfig]:
        """根据名称获取工作空间配置。

        Args:
            name: 工作空间名称。

        Returns:
            匹配的 ``WorkspaceConfig``；未找到返回 None。
        """
        return self.workspaces.get_workspace(name)

    def get_model_config(self, model_name: str) -> Optional[Dict[str, Any]]:
        """获取指定 provider/模型别名的配置。

        Args:
            model_name: models 字典中的 key（如 deepseek、openai）。

        Returns:
            模型配置字典；不存在返回 None。
        """
        return self.models.get(model_name)

    def get_search_provider(self) -> tuple[str, dict[str, Any]] | None:
        """解析当前启用的搜索 provider（委托模块级 resolve_search_provider）。

        Returns:
            ``(provider_id, cfg_dict)``；未配置时返回 None。
        """
        return resolve_search_provider(self.search)

    def get_memory_config(self) -> AgentMemoryConfig:
        """获取全局记忆配置。

        Returns:
            顶层 ``memory`` 配置；缺失时返回默认 ``AgentMemoryConfig``。
        """
        return self.memory if self.memory is not None else AgentMemoryConfig()

    def get_thinking_config(self) -> ThinkingConfig:
        """获取当前工作空间的思维链(CoT)配置。

        Returns:
            工作空间 thinking 配置；缺失时返回默认值。
        """
        ws = self.get_current_workspace()
        if ws and ws.thinking:
            return ws.thinking
        return ThinkingConfig()

    def get_mcp_servers(self) -> dict[str, MCPServerConfig]:
        """获取当前工作空间配置的 MCP 服务器字典。

        Returns:
            MCP 服务器配置 dict（key=服务器名）；无工作空间时返回空 dict。
        """
        ws = self.get_current_workspace()
        if ws:
            return ws.mcp_servers
        return {}

    def switch_workspace(self, workspace_name: str) -> bool:
        """切换当前工作空间（仅修改内存中的 current 字段，需调用方持久化）。

        Args:
            workspace_name: 目标工作空间名称。

        Returns:
            切换成功返回 True；工作空间不存在返回 False。
        """
        if self.get_workspace(workspace_name) is None:
            return False
        self.workspaces.current = workspace_name
        return True

    def get_leader_agent_config(self) -> Dict[str, Any]:
        """获取当前工作空间 leader agent 的完整配置。

        Raises:
            ValueError: 未配置工作空间或 leader agent。
        """
        ws = self.get_current_workspace()
        if not ws:
            raise ValueError("未配置工作空间。请在 aion.json 的 workspaces.scopes 中添加至少一个工作空间。")
        leader_id = ws.get_leader()
        agent_cfg = ws.get_agent_config(leader_id)
        if not agent_cfg:
            raise ValueError(
                f"Leader agent '{leader_id}' 未在 workspace agents 中定义。"
                f"请在 aion.json 的 workspaces.scopes 中添加 {leader_id} 的 provider 配置。"
            )
        return agent_cfg
