# 全链路观测（Observability）

> 基于 Langfuse 的父子 agent 全链路观测设计 —— `sessions_spawn`（subagent）和 `sessions_send`（member agent）执行的子任务在 Langfuse 中作为同一 trace 下的子 span 可见。

## 1. 链路结构

所有子 agent 执行共享父 trace 的 `trace_id`，同时使用自己的 `session_id`，形成一棵完整的 trace 树：

```
父 trace (id="abc", name="写一份报告")
  ├── AGENT → GENERATION: 父 LLM 调用
  │   └── 决定调用 sessions_send("搜索资料", agent_id="researcher")
  ├── TOOL sessions_send({"agent_id":"researcher", "task":"搜索资料"})
  │   └── 子 LangGraph
  │       ├── AGENT → GENERATION: researcher LLM 调用
  │       ├── TOOL search / read / ...
  │       ├── AGENT → GENERATION: researcher 第二次 LLM 调用
  │       └── AGENT → GENERATION: researcher 最终回复
  └── AGENT → GENERATION: 父 LLM 综合输出
```

## 2. 核心设计

### 2a. Trace 上下文传播

父 AgentLoop 在 `run()` 中将当前 trace 信息存入实例变量，供子执行路径继承。

**位置：** `src/aion/agent/loop.py`

```python
class AgentLoop:
    def __init__(self, ...):
        self._current_trace_id: str = ""
        self._current_lf_session_id: str = ""

    async def run(self, ...):
        langfuse_trace_id = trace_id or generate_traceid()
        ...
        self._current_trace_id = langfuse_trace_id
        self._current_lf_session_id = session_id
```

### 2b. 子执行创建 Langfuse CallbackHandler

`execute_subagent()` 和 `execute_agent_send()` 在调用 `AgentRunner.run()` 前，构造一个继承父 `trace_id` 的 `CallbackHandler`：

```python
from aion.log import generate_traceid
from langfuse.langchain import CallbackHandler

_child_lf_cb = CallbackHandler(trace_context={
    "trace_id": self._current_trace_id or generate_traceid(),
    "trace_name": task[:20],
    "session_id": child_session_id,
})

result = await runner.run(
    messages=[...],
    callbacks=[_child_lf_cb] if _child_lf_cb else None,
)
```

关键规则：
- **trace_id 继承**：子 agent 的 trace_id 取自 `self._current_trace_id`，与父 trace 相同 → Langfuse 自动关联为同一 trace
- **兜底生成**：`_current_trace_id` 可能为空（如 CLI 首次调用前无父 trace），用 `generate_traceid()` 兜底
- **session_id 独立**：每个子 agent 使用自己的 `session_id`，区分不同子调用

### 2c. 错误处理

- `CallbackHandler` 构造和 `Tracer.flush()` 不因 Langfuse 不可用而阻塞主流程
- 子 agent 执行失败不影响父 trace 的完整性

## 3. 调用位置

| 调用方 | 方法 | 创建子 trace | session_id 来源 |
|--------|------|-------------|----------------|
| `sessions_spawn` | `AgentLoop.execute_subagent()` | ✅ CallbackHandler | `sub-{uuid_hex[:12]}` |
| `sessions_send` | `AgentLoop.execute_agent_send()` | ✅ CallbackHandler | `str(uuid.uuid4())` |

## 4. Langfuse 观测类型

在 trace 中，各组件表现为：

| Langfuse 类型 | 对应组件 | 说明 |
|--------------|---------|------|
| `CHAIN "LangGraph"` | AgentRunner 的 LangGraph StateGraph | 子 agent 的 ReAct 循环容器 |
| `AGENT "agent"` | LangGraph agent node | 每轮 LLM 决策 |
| `GENERATION "..."` | 实际 LLM 调用（DeepSeek / OpenAI / ...） | token 用量、模型名 |
| `TOOL "sessions_send"` | sessions_send 工具的函数级 span | agent_id + task 作为 input |
| `TOOL "sessions_spawn"` | sessions_spawn 工具的函数级 span | task + 可选 agent_id |
| `TOOL "write/exec/..."` | 子 agent 调用的文件/执行工具 | 文件路径和内容摘要 |

## 5. 涉及代码文件

| 文件 | 修改内容 |
|------|---------|
| `src/aion/agent/loop.py` | `__init__()` 加 `_current_trace_id` / `_current_lf_session_id` |
| `src/aion/agent/loop.py` | `run()` 保存 trace 上下文到实例变量 |
| `src/aion/agent/loop.py` | `execute_subagent()` 添加 Langfuse CallbackHandler + Tracer.flush() |
| `src/aion/agent/loop.py` | `execute_agent_send()` 添加 Langfuse CallbackHandler + Tracer.flush() |
| `src/aion/agent/agent_runner.py` | AgentRunner 接受 `callbacks` 参数，传递给 LangGraph astream |

## 6. 相关文档

- [Agent Teams 设计实现](agent-teams.md)
- [多 Agent / Subagent 协作机制](multi-agent-architecture.md)
