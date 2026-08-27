# 请求全链路文档

## 架构总览

Aion 的消息处理分为三个逻辑层，从上往下逐层收敛：

```
                    ┌───────────────────┐
                    │   Channel 层      │  ← Feishu(WS/Webhook), HTTP, CLI
                    │  (协议转换)        │
                    └────────┬──────────┘
                             │ MessageContext (统一格式)
                             ▼
                    ┌───────────────────┐
                    │  Gateway / 汇聚点  │  ← dispatch_message()
                    │  (路由/调度/队列)   │
                    └────────┬──────────┘
                             │ user_input
                             ▼
                    ┌──────────────────┐
                    │    AgentLoop     │  ← ReAct 循环
                    │  (编排/推理/工具)  │
                    └────────┬─────────┘
                             │ LLM call
                             ▼
                    ┌──────────────────┐
                    │  LLM Provider    │  ← DeepSeek / OpenAI / MiniMax
                    │  (模型推理)       │
                    └──────────────────┘
```

- **Channel 层**：负责协议转换，将外部平台的原始消息（飞书事件、HTTP JSON、CLI 输入）转为统一的 `MessageContext`
- **Gateway / 汇聚点**：`dispatch_message()` 是所有消息的唯一入口，承担斜杠命令拦截、会话绑定、AgentLoop 调度、串行队列控制
- **AgentLoop**：包含 ReAct 主循环、工具注册与执行、子 agent 编排、后处理

---

## 1. Channel 层

### 1.1 ChannelPlugin 抽象

所有 Channel 必须实现 `ChannelPlugin` 抽象基类（`channels/adapters.py`），Gateway 只依赖该抽象，不引入具体实现。

核心接口：

- `channel_id` / `channel_name` — 唯一标识
- `start()` / `stop()` / `is_running()` — 生命周期
- `async send_message(chat_id, content, ...) -> SendResult` — 出站消息
- `get_agent_prompt_adapter()` — 返回通道特定的格式指引（markdown/plain_text、消息工具等）
- `get_command_adapter()` — 返回通道特定的本地命令解析器
- `build_session_key(ctx, agent_id)` — 构建会话绑定键（默认 `agent:{agent_id}:{channel_id}:{chat_id}`）
- `build_footer(...)` — 构建消息尾部的元信息（工作空间、模型名、Token 用量、TraceID）

### 1.2 实现

| Channel | 消息来源 | 接收方式 | 特有行为 |
|---------|----------|----------|----------|
| **Feishu** | 飞书即时消息 | WebSocket（长连接）或 Webhook | @提及检查、消息去重、线程回复、打字指示器 |
| **HTTP** | HTTP POST | `do_POST` 处理器 | 同步响应模式，无持久连接 |
| **CLI** | 终端输入 | Click 命令 `aion chat` | 直接面向终端输出 |

### 1.3 Feishu 消息接收流程

```
飞书 WebSocket 事件
    │
    ▼
FeishuChannel._handle_event()
    │
    ▼
FeishuEventDispatcher.dispatch()
    │
    ▼
FeishuEventDispatcher._handle_message_event()
    ├─ 消息去重 (FeishuDedup)
    ├─ 群聊 @ 校验 (requireMention / mentioned_bot)
    ├─ 发送打字指示器
    ▼
FeishuChannel._handle_message()
    ├─ 转换飞书消息为统一 MessageContext
    ▼
dispatch_func(ctx, channel)  → 即 dispatch_message()
```

### 1.4 HTTP 消息接收

```
HTTP POST /api/chat
    │
    ▼
GatewayServer._RequestHandler.do_POST()
    ├─ 解析 JSON body {message, session_id}
    ├─ 构建 MessageContext(channel_id="http", ...)
    ▼
dispatch_message(ctx, http_adapter)
```

---

## 2. Gateway / 汇聚点

### 2.1 GatewayServer

GatewayServer 负责：

1. **启动**：在独立线程中创建 asyncio 事件循环，并行启动所有 enabled Channel
2. **通道加载**：通过 `importlib.import_module(f".channels.{channel_id}", package="aion")` 动态加载 Channel 的 `create_channel()` 工厂函数——不直接 import 具体 Channel 类
3. **HTTP 服务**：内置 `HTTPServer`，处理 POST 请求作为消息入口

### 2.2 消息汇聚点：dispatch_message

**`dispatch_message(ctx: MessageContext, channel: ChannelPlugin) -> DispatchResult`** 是所有消息（Feishu、HTTP、CLI）的唯一汇聚入口，位于 `gateway/dispatch.py`。

完整流程：

```
dispatch_message(ctx, channel)
    │
    ├─ 1. 设置 TraceID（从 ctx.message_id 取前16字符）
    │
    ├─ 2. 解析配置：工作空间、leader agent
    │
    ├─ 3. 斜杠命令拦截
    │     如果 content.startswith("/"):
    │         result = handle_slash_command(...)
    │         如果 result 非 None → 直接返回（命令已处理）
    │
    ├─ 4. 会话绑定
    │     session_key = channel.build_session_key(ctx, agent_id)
    │     session_id = SessionBinder.get_or_create_session_id(session_key)
    │
    ├─ 5. AgentLoop 获取 / 创建
    │     loop = get_or_create_agent_loop(session_id, workspace_name, _session_loops)
    │
    ├─ 6. 入队
    │     item = SessionQueueManager.enqueue(session_id, user_input)
    │
    ├─ 7. 判断角色
    │     是 drainer → 循环 dequeue 并执行 AgentLoop.run()
    │     是 排队者  → await item.future 等待 drainer 处理
    │
    ▼
DispatchResult
    ├─ command_handled: bool
    ├─ thinking_parts: list[str]
    ├─ response: str
    ├─ footer: str
    ├─ session_id: str
    ├─ traceid: str
    └─ error: Optional[str]
```

### 2.3 会话队列机制（SessionQueueManager）

同一 session 的消息必须串行处理，防止并发导致上下文错乱。

```
                  同一 session_id 的多条消息
                         │
                         ▼
               SessionQueueManager.enqueue()
                         │
                    ┌────┴────┐
                    ▼         ▼
                drainer      排队者
          (队列中的第一个)   (后续消息)
                    │         │
                    │         await item.future
                    │         (等待 drainer 处理完毕)
                    ▼
          while queue not empty:
              item = dequeue()
              agent_loop.run(item.input)
              item.future.set_result(result)
                    │
                    ▼
               SessionQueueManager.next_or_done()
```

- **drainer**：调用 `enqueue` 返回 `True`，负责依次处理队列中所有消息
- **排队者**：返回 `False`，其 `item.future` 被 drainer 完成后唤醒

---

## 3. AgentLoop / ReAct 循环

### 3.1 初始化

AgentLoop 在首次请求时通过工厂创建并缓存（`get_or_create_agent_loop()`），构造时完成：

1. **ContextManager** — 消息列表管理、压缩、修剪、会话持久化
2. **ToolRegistry** — 注册所有内置工具、用 bootstrap 感知包装敏感工具、注册 subagent 工具
3. **LangGraph ReAct Agent** — `create_react_agent(model, tools)`，系统 prompt 在消息中注入
4. **BootstrapMonitor** — 启动状态审计（检测 LLM 是否错误声称引导已完成）
5. **PostProcessor** — 后处理管道
6. **构建系统 Prompt** — 委托给 `ContextManager.build_system_prompt()`

### 3.2 主循环 AgentLoop.run()

```
AgentLoop.run(user_input)
    │
    ├─ 第一轮(仅首次)：MCP 工具延迟初始化
    │
    ├─ 时间锚注入：追加 "Current time: ..." 到用户消息
    │
    ├─ 内容去重：如果最后一条消息内容相同则跳过
    │
    ├─ context.add_user(user_input)
    │
    ├─ 进入 ReAct 主循环 (while True):
    │   │
    │   ├─ 压缩：ctx_mgr.compact_if_needed(messages)
    │   ├─ 修剪：ctx_mgr.prune(messages)
    │   ├─ 硬上限安全网：ctx_mgr.hard_cap_safety_net(messages)
    │   │
    │   ├─ LangGraph astream 推理
    │   │   ├─ tools 节点 → 执行工具调用 → 检查 bootstrap 刷新
    │   │   ├─ agent 节点 → 收集 AI 回复、记录 tool_calls
    │   │   └─ 循环直到 LLM 不再调用工具
    │   │
    │   ├─ context.add_assistant(iteration_response)
    │   │
    │   ├─ 子 agent 收割
    │   │   ├─ 有等待注入的子 agent 结果 → 注入为新用户消息 → 继续循环
    │   │   ├─ 有活跃子 agent → 等待结果 → 继续循环
    │   │   └─ 无待处理 → 跳出循环
    │   │
    │   └─ (回到 while True 顶部)
    │
    ├─ PostProcessor.process(raw_text, llm_last_msg)
    │   ├─ 提取 reasoning_content（来自 additional_kwargs）
    │   ├─ 提取 <think>/<thinking> 标签内容
    │   ├─ 剥离 CoT 标签 (<final>)
    │   ├─ 剥离 DSML 标签 (DeepSeek 原生工具调用)
    │   ├─ Bootstrap 错误声明审计
    │   └─ 空响应回退（显示思考或中断消息）
    │
    ├─ ContextManager.persist_turn() → 写入 session JSONL + 同步向量索引（先等待异步索引任务，再索引本轮对话）
    │
    ▼
    返回 (response, thinking_parts, tokens)
```

### 3.3 Tool 工具调用

ToolRegistry 注册的内置工具分组：

- **文件操作**：`file_read`、`file_write`、`file_edit`、`file_delete`、`file_trash`、`file_move`
- **命令执行**：`bash_command`、`bash_script`
- **搜索**：`web_search`、`web_fetch`、`code_search`
- **记忆**：`memory_write`、`daily_memory_write`、`memory_search`、`memory_get`
- **配置**：`config_read`、`config_write`
- **子 agent**：`subagent_delegate`

部分敏感工具（`file_delete`、`file_trash`）在引导完成前有额外安全包装。

### 3.4 子 agent 编排

SubagentOrchestrator 管理子 agent 生命周期：

1. LLM 调用 `subagent_delegate` 工具
2. 子 agent 在后台独立运行
3. 完成时，结果通过 "announce" 机制注入回父 AgentLoop 的上下文
4. 父循环感知到注入内容，继续推理

---

## 4. Prompt 构建

### 4.1 分段结构

系统 Prompt 以 **分段列表**（`list[str]`）的形式构建，每个分段成为独立的一条 `{"role": "system"}` 消息。这提供了比单块 prompt 更好的关注点隔离和缓存粒度。

构建函数：`prompt.build_system_prompt()`，被 `ContextManager.build_system_prompt()` 调用。

### 4.2 全量模式（非子 agent）分段顺序

| 序号 | 分段 | 内容 | 来源 |
|------|------|------|------|
| 1 | **Bootstrap** | 项目启动上下文：合并工作空间中的 MARKDOWN 文件（`WORKSPACE_BOOTSTRAP.md`、`AGENT_BOOTSTRAP.md` 等），每文件上限 20K 字符，总计上限 150K 字符 | `bootstrap_monitor` |
| 2 | **Skills** | 工作空间下已安装的技能（`workspace/skills/<name>/SKILL.md`），格式化为 `<available_skills>` XML 标签 | `SkillsLoader` |
| 3 | **Startup Context** | 分为两部分：<br>① **每日记忆**：`[Untrusted daily memory: ...]` + 最近 N 天的 `memory/YYYY-MM-DD.md` 引用块<br>② **启动记忆回想**：跨会话语义搜索（查询如"用户 名字 称呼""偏好 喜欢 讨厌""待办 计划 任务"） | `context.build_startup_context()` |
| 4 | **工具限制** | 禁止读写系统管理目录（chroma/、memory/、sessions/）的文字说明 | `prompt.py` |
| 5 | **恢复指引** | 大文件读取和截断处理的说明 | `prompt.py` |
| 6 | **推理指引**（可选） | `<think>...</think>` / `<final>...</final>` 格式说明。当 `thinking_config.thinking_level != "off"` 时启用 | `prompt.py` |

最终拼装为多条 `{"role": "system", "content": section}` 消息，追加到对话历史之前。

### 4.3 子 agent 模式

使用最小化 prompt：仅包含 `subagent_system_prompt` 参数 + 推理指引分段，不加载 Bootstrap/Skills/Startup Context。

### 4.4 分隔标签

相邻分段之间无特殊分隔符——每个分段是独立的 system 消息。在内容内部使用的标记性标签包括：

- `[Untrusted daily memory: ...]` — 每日记忆引用前奏
- `<available_skills>...</available_skills>` — 技能列表 XML 包装
- `<think>...</think>` / `<final>...</final>` — 推理格式标签，用于思考过程与最终答案分离

---

## 5. 斜杠命令系统

### 5.1 处理入口

在 `dispatch_message()` 中检测消息是否以 `/` 开头，若是则调用 `handle_slash_command()`。

如命令被识别（返回非 `None` 的 DispatchResult），则**立即返回**，不走 AgentLoop。

```
用户输入 "/new"
    │
    ▼
dispatch_message() → content.startswith("/")
    │
    ▼
handle_slash_command()
    │
    ├─ /new       → SessionBinder.refresh_binding() 生成新 session UUID
    │               loop.reset_context(new_session_id)，开始新对话
    │
    ├─ /switch    → 更新 config.workspaces.current，切换工作空间
    │               旧 AgentLoop 缓存失效
    │
    ├─ /workspaces → 列出所有工作空间及当前选中
    │
    ├─ /status    → 系统状态概览（运行中的 Channel、会话数等）
    │
    └─ /help      → 显示可用命令列表
```

未识别的命令返回 `None`，`dispatch_message` 继续将其传递给 AgentLoop 作为普通用户消息处理。

---

## 6. 消息回传 Channel

### 6.1 回传路径

```
AgentLoop.run() 返回 (response, thinking_parts, tokens)
    │
    ▼
dispatch_message() 构建 DispatchResult
    ├─ thinking_parts ← agent_loop.last_thinking_parts
    ├─ response ← 清洁回复文本
    ├─ footer ← channel.build_footer(workspace, model, tokens, balance, traceid)
    │
    ▼
Channel 层消费 DispatchResult
```

### 6.2 Feishu 出站

对于非命令、无错误的结果：

1. **逐条发送 thinking**：在 `thinking_parts` 上迭代，每条通过 `send_message()` 以**线程回复**方式发送，前一条消息的 ID 作为 `parent_id`
2. **发送最终响应**：将 `response + footer` 拼接，以同样的线程回复机制发送
3. **清除打字指示器**：`remove_typing_indicator(ctx.message_id)`

### 6.3 HTTP 出站

在 `do_POST` 中将 `DispatchResult` 映射为 JSON：

```
{
  "thinking":  [...],    // thinking_parts
  "response":  "...",    // 清洁响应
  "footer":    "...",    // 尾部元信息
  "session_id": "...",
  "traceid":    "..."
}
```

同步返回给调用方。

---

## 7. TraceID 与日志关联

- **TraceID 来源**：从 `ctx.message_id` 取前 16 字符
- **设置方式**：`set_traceid(traceid)` 基于 `contextvars`，不污染全局状态
- **异常安全**：`try/finally` 保证 `reset_traceid()`
- **呈现位置**：footer 中包含 traceid（如 `TraceID: abcdef1234567890`），方便在日志中关联请求

---

## 完整请求流程总结

```
飞书消息         HTTP POST          CLI 输入
    │               │                 │
    ▼               ▼                 ▼
Channel 转换     do_POST 构建       Click 命令
MessageContext   MessageContext     构建 MessageContext
    │               │                 │
    └───────────────┼─────────────────┘
                    ▼
          dispatch_message()        ← 汇聚点
          ├─ TraceID 设置
          ├─ 斜杠命令拦截 → 识别则返回
          ├─ 会话绑定 (SessionBinder)
          ├─ AgentLoop 获取/创建
          ├─ 入队 (SessionQueueManager)
          └─ drainer 模式 → AgentLoop.run()
                    │
                    ▼
          AgentLoop 主循环
          ├─ MCP 延迟初始化(首轮)
          ├─ 时间锚注入
          ├─ 上下文管理 (compact/prune/cap)
          ├─ ReAct: LLM ↔ 工具 ↔ 子 agent
          ├─ 后处理 (PostProcessor)
          └─ 持久化 (session JSONL + 向量索引)
                    │
                    ▼
          DispatchResult
          ├─ thinking_parts
          ├─ response + footer
          └─ error (如有)
                    │
                    ▼
          Channel 回传
          ├─ Feishu: 逐条 thinking(线程) + 最终响应 + 移除打字指示器
          ├─ HTTP:   JSON 响应 {thinking, response, footer}
          └─ CLI:    直接打印
```

---

## 关键设计原则

1. **分层不越界**：Channel 只做协议转换，不做业务逻辑；dispatch_message 是唯一交叉点
2. **无具体 Channel 导入**：Gateway 通过 `importlib` + `ChannelPlugin` 抽象动态加载，不依赖任何具体 Channel 类
3. **串行队列**：基于 drainer 模式的 SessionQueueManager 保证同一会话内的消息顺序处理，防止并发冲突
4. **Prompt 分段化**：系统 prompt 拆为多条独立 `{"role": "system"}` 消息，提升关注点隔离和缓存命中率
5. **子 agent 编排**：子 agent 独立运行、异步等待、结果注入回父循环，形成多级嵌套的智能体协作模式
