"""Subagent 子 agent 模块

提供 sessions_spawn 异步派发、SubagentRegistry 状态跟踪、
专用 subagent system prompt（minimal 模式）与工具 schema。
AgentLoop 在非 is_subagent 模式下注册 sessions_spawn / agents_list 工具；
所有 agent（含子 agent）注册 subagents（list/kill/await）。
"""
