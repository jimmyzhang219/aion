# Subagent 设计与实现

## 概述

Subagent（子 agent）是 Aion 的跨 agent 任务分配机制。主 agent（leader）在 ReAct 循环中通过 `sessions_spawn` 工具派生子 agent 执行独立子任务，子 agent 完成后结果直接作为 tool response 返回给主 agent。

> 与 subagent 相对的 **member agent（持久化团队协作）** 见 [`docs/superpowers/specs/2026-07-02-agent-teams-design.md`](../superpowers/specs/2026-07-02-agent-teams-design.md)。

### 核心特性

- **同步调用**：`sessions_spawn` 是同步 tool，阻塞直到子 agent 完成，结果直接返回
- **不可嵌套**：subagent 看不到 `sessions_spawn` 工具，从根源上杜绝嵌套派生
- **轻量执行**：子 agent 使用 `AgentRunner`（纯 ReAct 循环），非完整 `AgentLoop`
- **独立会话文件**：子 agent 有独立的 `-subagent.jsonl` 文件，生命周期结束后标记 `.delete`
- **实时持久化**：子 agent 每轮 ReAct 输出通过 emit 回调实时写入 JSONL
- **Langfuse 继承**：子 agent trace 继承父 agent 的 trace_id，形成完整调用链
- **ContextVar 解耦**：tools 层通过 ContextVar 获取当前 AgentLoop，避免循环导入

---

## 架构

```
┌─────────────────────────────────────────────────┐
│                 主 Agent (Leader)               │
│  AgentLoop.run()                                │
│  │                                              │
│  ├─ set_agent_loop(self)  ← ContextVar 设       │
│  │                                              │
│  ├─ ReAct 循环                                  │
│  │  ├─ LLM 推理                                 │
│  │  ├─ 工具调用                                  │
│  │  │  └─ sessions_spawn(task, agent_id?)       │
│  │  │       └─ execute_subagent()               │
│  │  │            ├─ SubagentSession (JSONL)     │
│  │  │            ├─ AgentRunner (ReAct)         │
│  │  │            ├─ emit 回调 → 实时写 JSONL    │
│  │  │            ├─ mark_deleted() → .delete    │
│  │  │            └─ 返回结果文本                 │
│  │  └─ 结果已在上下文中，LLM 综合输出               │
│  └─ reset_agent_loop()  ← ContextVar 重置        │
└─────────────────────────────────────────────────┘
```

---

## 关键组件

### 1. `tools/_context.py` — ContextVar 上下文

提供 tools 层获取当前 AgentLoop 的唯一通道，零依赖（不 import agent 层，防止循环导入）。由 `AgentLoop.run()` 入口设入，`finally` 中重置。

```python
_current_agent_loop: ContextVar[object] = ContextVar("_current_agent_loop", default=None)

def get_agent_loop() -> object
def set_agent_loop(loop: object) -> Token[object]
def reset_agent_loop(token: Token[object]) -> None
```

### 2. `tools/builtin/agent_tools.py` — sessions_spawn 工具

`@tool` 装饰的 async 函数，通过 auto-discovery 自动注册到 `TOOL_REGISTRY`。

```python
@tool
async def sessions_spawn(task: str, agent_id: str | None = None) -> str:
    parent = get_agent_loop()
    if parent is None:
        return "Error: sessions_spawn requires an active agent loop"
    return await parent.execute_subagent(task=task, agent_id=agent_id)
```

**注册方式**：放在 `aion.tools.builtin` 下，`_toolkit.py` 的 `_discover_tools()` 自动发现

**过滤机制**：`main_agent_only=True` 标记。`ToolRegistry.build_langchain_tools()` 对 `is_subagent=True` 的 registry 跳过此工具。

注意 `execute_subagent()` 运行在**父 loop**（`is_subagent=False`）中，`ToolRegistry` 的自动过滤不生效，因此还需**手动按名称过滤**：

```python
# loop.py:885 — execute_subagent 中手动排除 sessions_spawn
tools = [t for t in self.tool_registry.build_langchain_tools() if t.name != "sessions_spawn"]
```

### 3. `agent/loop.py` — execute_subagent()

`AgentLoop` 的方法，创建并同步执行子 agent：

```python
async def execute_subagent(self, task: str, agent_id: str | None = None) -> str:
```

**执行流程**：

1. 生成子 session_id：`sub-{uuid.hex[:12]}`
2. 创建 `SubagentSession`（JSONL 文件）
3. 构建最小 system prompt（Subagent Context 段落）
4. 加载内置工具，按名称过滤掉 `sessions_spawn`（手动过滤，因父 loop 不触发 `main_agent_only`）
5. 为子 agent 创建 Langfuse CallbackHandler（继承父 trace_id）
6. 创建 `AgentRunner`，通过 `emit` 回调实时写入 JSONL
7. 等待 `AgentRunner.run()` 完成
8. 调用 `SubagentSession.mark_deleted()` 标记文件结束
9. 返回子 agent 的响应文本

**LLM 解析**：若指定了 `agent_id` 且与当前 agent 不同，通过 `_resolve_child_llm()` 从配置读取目标 agent 的 provider 创建独立 LLM 实例。

### 4. `agent/subagent/session.py` — SubagentSession

子 agent 专属会话文件管理，独立于主 agent 的 `SessionStore`：

```python
class SubagentSession:
    def __init__(self, session_id: str, agent_id: str, workspace_dir: Path)
    def append_messages(self, messages: list[dict]) -> None  # 'a' 模式追加
    def mark_deleted(self) -> None  # rename → .jsonl.delete
```

**文件路径**：`{workspace_dir}/agents/{agent_id}/sessions/{timestamp}_{session_id}-subagent.jsonl`

**生命周期**：

```
创建:  {timestamp}_{session_id}-subagent.jsonl     # touch 空文件
写入:  同上文件（append，每轮 ReAct 实时写）
结束:  {timestamp}_{session_id}-subagent.jsonl.delete  # rename，文件保留作为审计迹留
```

### 5. `agent/subagent/prompt.py` — Subagent System Prompt

```python
def build_subagent_system_prompt(
    task: str,
    child_session_id: str,
    parent_session_id: str,
    agent_id: str | None = None,
) -> str
```

只包含 **Subagent Context** 段落，无 Bootstrap / Skills / Startup / Recovery：

- 只做分配的任务
- 完成后结果自动返回给父 agent（同步模型）
- 禁止：与用户对话、写记忆、修改配置、伪装成父 agent、派生子 agent
- 直接输出结果，保持简洁

### 6. `agent/agent_runner.py` — AgentRunner

通用 LangGraph ReAct 循环，子 agent 和主 agent 的 `_run_react()` 均使用它。

```python
class AgentRunner:
    async def run(self, messages, *, emit, retry_check, transform_context, should_stop, callbacks, ...) -> AgentResult
```

`emit` 回调机制：每轮 `TurnEnd`/`ToolEnd` 事件实时通知调用方。`execute_subagent()` 利用此回调将每轮消息追加写入 JSONL。

### 7. `agent/tool_registry.py` — main_agent_only 过滤

`build_langchain_tools()` 中对 subagent 过滤：

```python
for name, raw in TOOL_REGISTRY.items():
    if self._is_subagent and getattr(raw, "main_agent_only", False):
        continue  # subagent 看不见 sessions_spawn
```

额外工具（`agents_list`、`subagents`）通过 `_register_subagent_tools()` 注册到 `_extra_tools`。

### 8. `agent/subagent/registry.py` — SubagentRegistry

内存级子 agent 注册表，跟踪 spawn 状态，定义并发和深度限制：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_concurrent` | 5 | 同一父 session 下最大并发子 agent 数 |
| `max_depth` | 3 | 最大嵌套 spawn 深度 |

**注意**：当前 `execute_subagent()` 未调用 `SubagentRegistry.check_can_spawn()` 和 `.register()` 进行准入校验。Registry 仅在 `_run_react()` 中用于 `list_active_by_parent()` / `wait_for_active()` 等查询操作。深度/并发限制目前由代码定义但**未实际生效**，需后续接入。

`SubagentOrchestrator`（`agent/subagent_orchestrator.py`）提供了 `push_result()` / `drain_pending()` / `wait_for_active()` 机制，用于旧异步模型的子 agent 结果协调。当前同步模型中此机制不被主动触发。

### 9. `agent/subagent/tools.py` — Subagent 管理工具

为 AgentLoop 注册 `subagents`（list/kill/await）和 `agents_list` 工具，通过 `ToolRegistry._register_subagent_tools()` 注册。

```python
def create_agents_list_tool(agent_loop) -> callable  # 列出空间内可用 agent
def create_subagents_tool(registry, agent_loop) -> callable  # list/kill/await
```

---

## 配置

在 `aion.json` 的 `workspaces.scopes[].agents` 中定义 agent 以供 spawn：

```json
{
  "agents": {
    "leader": "main",
    "main": { "provider": "deepseek", "description": "主对话 agent" },
    "researcher": { "provider": "deepseek", "description": "擅长搜索" },
    "coder": { "provider": "minimax", "description": "擅长代码" }
  }
}
```

- `leader`：处理用户消息的 agent（由 `WorkspaceConfig.get_leader()` 解析）
- 其他 key 为可 spawn 的 worker agent
- 不同 agent 可以配置不同的 LLM provider（`_resolve_child_llm()` 解析）
- `agents_list` 工具可列出可用 agent 供 LLM 参考

---

## 安全限制

| 限制 | 实现 | 值 |
|------|------|-----|
| 禁止嵌套 spawn | `main_agent_only` + ToolRegistry 过滤 + 手动按名称过滤 | subagent 的 tool 列表中无 sessions_spawn |
| 最大深度 | `SubagentRegistry.max_depth`（已定义，未在 execute_subagent 中校验） | 3（代码定义，待接入） |
| 最大并发 | `SubagentRegistry.max_concurrent`（已定义，未在 execute_subagent 中校验） | 5/父（代码定义，待接入） |

---

## 执行模型

| 维度 | 说明 |
|------|------|
| 返回方式 | 阻塞直到子 agent 完成 |
| 工具响应 | 子 agent 完整结果文本 |
| 执行引擎 | AgentRunner |
| JSONL 管理 | SubagentSession（独立，`.delete`） |
| 语言追踪 | Langfuse CallbackHandler，继承父 trace_id |

SubagentOrchestrator（旧异步模型遗留）提供 push/drain/wait 机制，在当前同步模型中不主动使用。`_run_react()` 中保留了对 `subagent_orch.has_pending()` 和 `SubagentRegistry.list_active_by_parent()` 的检查路径，用于兼容旧异步风格的子 agent 编排。

---

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `src/aion/tools/_context.py` | ContextVar：tools 层获取当前 AgentLoop |
| `src/aion/tools/builtin/agent_tools.py` | `sessions_spawn` @tool + `main_agent_only` 标记 |
| `src/aion/agent/loop.py` | `execute_subagent()` 方法 + ContextVar set/reset + `_resolve_child_llm()` |
| `src/aion/agent/tool_registry.py` | `build_langchain_tools()` 过滤 `main_agent_only` + 注册 subagent 管理工具 |
| `src/aion/agent/subagent/session.py` | `SubagentSession` JSONL 文件管理 |
| `src/aion/agent/subagent/prompt.py` | `build_subagent_system_prompt()` |
| `src/aion/agent/subagent/registry.py` | `SubagentRegistry` 深度/并发限制（已定义，待接入校验） |
| `src/aion/agent/subagent/tools.py` | `create_agents_list_tool` / `create_subagents_tool` |
| `src/aion/agent/subagent_orchestrator.py` | `SubagentOrchestrator` push/drain/wait（旧异步模型遗留） |
| `src/aion/agent/agent_runner.py` | `AgentRunner` LangGraph ReAct 循环 |
| `src/aion/tools/_toolkit.py` | `TOOL_REGISTRY` + `_discover_tools()` 自动发现 |
