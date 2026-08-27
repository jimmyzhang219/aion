"""配置模块

管理 aion 的所有配置：
- Config: 主配置 Pydantic 模型（多工作空间架构 v4）
- load_config: 从 JSON 文件加载配置
- defaults: 默认配置生成

配置结构（v4）：
- llm: 系统级 LLM 配置（所有工作空间共享）
- workspaces: 工作空间字典，key 是工作空间名称

每个工作空间包含：
- agents: Agent 配置（leader + 具体 agent）
- memory: 记忆配置
- compaction: 压缩配置
- pruning: 裁剪配置
- mcpServers: MCP 服务器配置（dict 格式）
"""
