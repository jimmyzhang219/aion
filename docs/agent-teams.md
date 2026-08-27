# Agent Teams 设计实现

> 基于工作空间的「agent teams」——工作空间下的持久化成员 agent 通过 `sessions_send` 协作，并结合 Langfuse 全链路观测。

## 1. 术语

| 术语 | 说明                                                            |
|------|---------------------------------------------------------------|
| **Agent Teams** | 一个 workspace 下的所有 agent 构成一个 team                             |
| **Leader Agent** | team 的主 agent，与 Channel 通信，拥有完整 AgentLoop，可调用 `sessions_send` |
| **Member Agent** | team 成员，有独立 `agents/{id}/` 目录、CONFIG.md、记忆、session 文件           |
| **Subagent** | `sessions_spawn` 发起的临时子任务执行者，无持久身份                            |

## 2. Leader / Member 角色设计

所有 agent 的目录结构完全对等。唯一区别是 `aion.json` 中 `"leader"` 键的指向：

```json
"agents": {
    "leader": "main",              ← 字符串，指向 leader agent 的 ID
    "main": { "provider": "deepseek" },
    "researcher": { "provider": "deepseek", "description": "擅长搜索" },
    "coder": { "provider": "minimax", "description": "擅长代码" }
}
```

- `"leader"` 是字符串键，引用一个存在的 agent ID
- 其他 `dict` 类型的条目是 member agent
- 任何 member 只要被配置为 `"leader"` 即可成为 leader，无需改目录结构
- `WorkspaceConfig.get_leader()` 返回 leader 的 agent_id（`src/aion/config/schema.py`）

## 3. 目录结构

```
{workspace_dir}/agents/
├── main/                     ← leader（名称不固定，由 config 决定）
│   ├── CONFIG.md
│   ├── memories/
│   └── sessions/
│       ├── {ts}_{sid}.jsonl
│       └── ...
├── researcher/               ← member
│   ├── CONFIG.md
│   ├── memories/
│   └── sessions/
└── coder/                    ← member
    ├── CONFIG.md
    ├── memories/
    └── sessions/
```

所有 agent 的 session 文件统一使用 `{timestamp}_{session_id}.jsonl` 格式。

## 4. 入口：只创建 Leader

`src/aion/gateway/server.py` — `_get_or_create_agent_loop()` 方法，每个会话只创建 leader 的 `AgentLoop`，传入 `agent_id=leader_id`。用户消息不会直接路由到 member agent。

## 5. Leader System Prompt 注入

在 `src/aion/agent/prompt.py` 中：

### `build_system_prompt(is_leader=True)`

当 `is_leader=True` 时调用 `_build_agent_teams_section()`，在 system prompt 末尾注入：

```
## Agent Teams

你所在的团队有以下成员可供调用：

- `researcher`: 擅长信息搜索和资料整理
- `coder`: 擅长代码编写和调试

你可以通过 `sessions_send` 工具将任务分配给团队成员。
团队成员会独立执行任务并返回结果。
```

### 规则

- 仅 leader 的 system prompt 注入此 section
- member agent 的 system prompt 通过 `is_leader=False` 排除此 section
- member agent 不加载 `sessions_send` 工具（`main_agent_only=True` 过滤）
- description 来自 `aion.json agents.{id}.description`（唯一事实源）

## 6. Session 文件命名

### 规范

所有 agent 类型的 session 文件命名：

| Agent 类型 | session_id 格式 | 文件名格式 | 生命周期 |
|-----------|----------------|-----------|---------|
| Leader/Main | `str(uuid.uuid4())` | `{timestamp}_{session_id}.jsonl` | 持久，SessionBinder 复用 |
| Member | `str(uuid.uuid4())` | `{timestamp}_{session_id}.jsonl` | 持久 |
| Subagent | `sub-{uuid_hex[:12]}` | `{timestamp}_{session_id}-subagent.jsonl` | 瞬态，标记 `.delete` |

Member agent 的 session_id 和文件名与 Leader agent 格式对等。

实现位置：`src/aion/agent/loop.py:1081`

```python
child_session_id = str(uuid.uuid4())
session_file = session_dir / f"{timestamp}_{child_session_id}.jsonl"
```

## 7. sessions_send 工具

在 `src/aion/tools/builtin/agent_tools.py` 中定义：

```python
@tool
async def sessions_send(task: str, agent_id: str) -> str:
    """向成员 agent 发送任务以供执行。"""
    parent = get_agent_loop()
    if parent is None:
        return "Error: sessions_send requires an active agent loop"
    return await parent.execute_agent_send(agent_id=agent_id, task=task)

object.__setattr__(sessions_send, "main_agent_only", True)
```

校验规则：
- `agent_id` 必须是当前 workspace agents 列表中的成员
- 不能向自己发送任务（`agent_id == leader_id` 或 `agent_id == self.agent_id`）
- `main_agent_only=True` → ToolRegistry 对 member agent 过滤此工具

## 8. execute_agent_send 执行流程

在 `AgentLoop` 中（`src/aion/agent/loop.py:967`）：

```
Leader ReAct
  └─ sessions_send(task, agent_id)
       └─ execute_agent_send(agent_id, task)
            ├─ 1. 校验 agent_id 是否在 workspace config 中
            ├─ 2. 解析 member agent 的 provider → LLM 实例
            ├─ 3. 读 CONFIG.md + build_system_prompt(is_leader=False)
            ├─ 4. 构建工具列表（文件工具做路径隔离，见第 9 节）
            ├─ 5. 创建 Langfuse CallbackHandler（见第 11 节）
            ├─ 6. 创建 session 文件（持久保留）
            ├─ 7. AgentRunner.run(messages=[system_prompt + task], callbacks=[...])
            └─ 8. Tracer.flush() → 结果文本返回给 leader
```

Session 文件保留在磁盘上（与 subagent 不同），支持后续通过 `SessionLister` 回溯。

## 9. 文件隔离

Member agent 的文件工具（read/write/edit/delete/ls/grep/trash）通过 `_build_isolated_tool()` 包装：

```python
blocked_prefixes = [
    str((workspace_dir / "agents" / other_id).resolve())
    for other_id in all_agent_ids
    if other_id != current_agent_id
]
```

- 可以访问：工作空间根目录下的文件、自己的 `agents/{id}/` 目录
- 不能访问：其他 agent 的 `agents/{other_id}/` 目录

## 10. 与 Subagent 对照

| | `sessions_spawn` (subagent) | `sessions_send` (member) |
|---|---|---|
| **概念** | 临时子任务分解 | 团队协作 |
| **生命周期** | 临时，用完清理（`.delete`） | 持久，始终存在 |
| **身份** | 无，无独立目录 | 有，`agents/{id}/` 目录 |
| **配置来源** | 父 agent 上下文 | workspace config + CONFIG.md |
| **记忆** | 无 | 有独立记忆（每次独立会话） |
| **执行引擎** | AgentRunner | AgentRunner |
| **文件隔离** | 无 | 限制访问其他 agent 目录 |
| **Session 文件** | `-subagent.jsonl` → `.delete` | `.jsonl`（与 leader 对等，保留） |
| **能否嵌套** | 否（`main_agent_only`） | 否（`main_agent_only`） |
| **调用方式** | `sessions_spawn(task, agent_id?)` | `sessions_send(task, agent_id)` |

## 11. Traceid / Langfuse 全链路观测

全链路观测已独立文档化，详见 [全链路观测（Observability）](full-link-observability.md)。

内容包括：
- 链路结构（父子 trace 树）
- Trace 上下文传播机制（`_current_trace_id` / `_current_lf_session_id`）
- 子执行创建 Langfuse CallbackHandler 的做法
- Langfuse 中各类组件（CHAIN / AGENT / GENERATION / TOOL）的观测类型
- 错误处理规则

## 12. 涉及修改的文件

| 文件 | 修改内容 |
|------|---------|
| `src/aion/agent/prompt.py` | 增加 `_build_agent_teams_section()`，leader 时注入 |
| `src/aion/agent/loop.py` | 新增 `execute_agent_send()`、`_build_isolated_tool()`、`_resolve_child_lll()` |
| `src/aion/agent/loop.py` | `__init__()` 加 `_current_trace_id` / `_current_lf_session_id` |
| `src/aion/agent/loop.py` | `run()` 保存 trace 上下文到实例变量 |
| `src/aion/agent/loop.py` | `execute_subagent()` 添加 Langfuse CallbackHandler + Tracer.flush() |
| `src/aion/agent/loop.py` | `execute_agent_send()` 统一 session 命名格式 + 添加 Langfuse CallbackHandler |
| `src/aion/tools/builtin/agent_tools.py` | 新增 `sessions_send` 工具 |
| `src/aion/agent/tool_registry.py` | 确认 `main_agent_only` 过滤逻辑覆盖 `sessions_send` |
| `src/aion/agent/agent_runner.py` | AgentRunner 接受 `callbacks` 参数，传递给 LangGraph astream |

## 13. 相关文档

- [全链路观测（Observability）](full-link-observability.md)
- [多 Agent / Subagent 协作机制](multi-agent-architecture.md)
- [Agent Teams 细化设计](superpowers/specs/2026-07-02-agent-teams-refinements-design.md)
- [Agent Teams 实现计划](superpowers/plans/2026-07-02-agent-teams-implementation.md)
