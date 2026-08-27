"""Gateway 模块

Gateway 是 aion 的 HTTP API 服务器：
- 提供 REST API 接收外部消息
- 管理飞书 Channel 的连接
- 管理 Session 和 AgentLoop 的生命周期

主要组件：
- GatewayServer: HTTP Server + 飞书 Channel 管理
- SystemScheduler: 系统调度循环，管理 Channel 生命周期
- ChannelRuntime: 单 Channel 的线程+loop 封装
"""

from .scheduler import SystemScheduler, ChannelRuntime, ChannelLifecycleManager

__all__ = ["SystemScheduler", "ChannelRuntime", "ChannelLifecycleManager"]
