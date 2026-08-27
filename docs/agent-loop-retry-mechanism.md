# AgentLoop 重试机制 — 实现说明

## 为什么需要重试

LLM 偶尔会返回无效响应，主要包括两类：

1. **空响应：** `finish_reason=stop` 但 `content=""`
2. **工具调用丢失：** `finish_reason=tool_calls` 但实际没有 `tool_calls`

重试的目的是让 LLM 以**相同的上下文**重新生成一次，期望得到有效响应。因此重试的关键原则是：**不修改上下文，不将无效请求计入对话历史**。

## 实现机制

### 架构概览

```
自定义 StateGraph

agent ──→ router ──→ tools ──┐
               │              │
               └──→ END       │
               └──────────────┘
```

**三个组件：**

| 组件 | 职责 |
|------|------|
| `call_agent` | 调用 LLM（已绑定工具），内部处理重试 |
| `should_continue` | 判断继续工具循环还是结束 |
| `ToolNode` | 执行工具调用 |

### State 定义

```python
from typing import Annotated, TypedDict
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage

class _RetryAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    # add_messages 是函数引用，不是字符串
    # 函数引用确保 ToolNode 返回的消息追加到已有消息列表
    # 而不是替换它们
```

### 核心机制：Agent 节点内部重试

```python
# llm 已通过 bind_tools(langchain_tools) 绑定工具
# 等价于 create_react_agent 内部行为

async def _call_agent(state, config):
    for attempt in range(6):  # 1 次初始 + 最多 5 次重试
        try:
            response = await llm.ainvoke(state["messages"], config=config)
        except Exception as e:
            if attempt < 5:
                logger.warning(f"LLM call failed: {e}, retry {attempt+1}/5")
                continue
            raise

        if _is_valid_response(response):
            return {"messages": [response]}  # ✅ 有效 → 返回给下游

        if attempt < 5:
            logger.warning(
                "[Agent] empty/tool-less response (finish_reason=%r), retry %d/5",
                response.response_metadata.get("finish_reason", ""),
                attempt + 1,
            )
            continue                           # 🔄 无效 → 重试（state 不变！）

    raise ValueError("LLM 连续 5 次返回无效响应")
```

**关键特性：**
- **幂等重试：** 每次重试使用相同的 `state["messages"]`，不修改上下文
- **无副作用：** 重试期间不执行工具（图还没走到 tools 节点）
- **零入侵：** 无效的 AIMessage 永远不会进入消息列表——node 函数在 `add_messages` reducer 之前就处理掉了
- **工具绑定：** `bind_tools(langchain_tools)` 让 LLM 知晓可用工具，与 `create_react_agent` 内部行为一致
- **消息追加：** `add_messages` 必须是**函数引用**而非字符串 `"add_messages"`，否则 reducer 失效导致 ToolNode 返回的消息替换（而非追加）已有消息列表

### 响应有效性判断

```python
def _is_valid_response(msg: AIMessage) -> bool:
    """LLM 响应有效的条件：有文本内容 或 有合法的工具调用。"""
    content = (msg.content or "").strip()
    fr = msg.response_metadata.get("finish_reason", "")
    tool_calls = getattr(msg, "tool_calls", None) or []

    if not content and fr in ("stop", ""):
        return False    # 空内容 + stop → 无效
    if fr == "tool_calls" and not tool_calls:
        return False    # finish_reason 声称 tool_calls 但实际没有 → 无效
    return True
```

### 图路由

```python
def _should_continue(state):
    last_msg = state["messages"][-1] if state["messages"] else None
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools"     # 还有工具要调 → 继续
    return "__end__"       # 最终回复 → 结束
```

### 执行流程

| 维度 | 说明 |
|------|------|
| **重试触发** | Agent node 内单步检查，LLM 返回后立即判断 |
| **重试方式** | `continue` 在 `for` 循环内，`state["messages"]` 不变 |
| **工具执行** | 重试时根本不执行（图还没走到 tools 节点） |
| **无效响应记录** | Node 内丢弃，不进入 state |
| **失败上限** | 5 次连续无效后抛 ValueError |
| **代码位置** | 集中在一个 node 函数内（`_call_agent`） |

### 正常执行流程示例

```
用户消息 → compact/prune → dict_messages_to_lc → StateGraph

call_agent (attempt=0):
  llm.invoke(messages) → AIMessage(content="", tool_calls=[tc1, tc2])
  _is_valid_response → True
  return {"messages": [AIMessage(tc1, tc2)]}
  ↓
should_continue: 有 tool_calls → "tools"
  ↓
tools: ToolNode 执行 tc1, tc2 → ToolMessage(r1, r2)
  ↓
call_agent (attempt=0):
  llm.invoke(messages + tc_pair + tool_results) → AIMessage(content="最终回复")
  _is_valid_response → True
  return {"messages": [AIMessage("最终回复")]}
  ↓
should_continue: 无 tool_calls → "__end__"
  ↓
StateGraph 结束 → iteration_response = "最终回复"
```

### 重试执行流程示例

```
call_agent (attempt=0):
  llm.invoke(messages) → AIMessage(content="", finish_reason="stop")
  _is_valid_response → False
  attempt=0, attempt < 5 → continue

call_agent (attempt=1):
  llm.invoke(messages) → AIMessage(content="", finish_reason="stop")
  _is_valid_response → False
  attempt=1, log "retry 1/5", attempt < 5 → continue

call_agent (attempt=2):
  llm.invoke(messages) → AIMessage(content="好的，我来处理", tool_calls=[tc1])
  _is_valid_response → True
  return {"messages": [AIMessage]}

# 注意：整个重试过程中 state["messages"] 完全相同
# 没有工具执行过，没有消息被追加
```


