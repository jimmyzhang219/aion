# 工具系统

aion 提供三层工具扩展能力，按集成深度递增：

1. **内置工具** — 应用内置，开箱即用
2. **Skills** — 工作空间级自定义技能指令，注入对话上下文
3. **MCP** — 连接外部 MCP 服务器，扩展任意工具

---

## 内置工具

aion 内置了 20+ 个工具，Agent 在 ReAct 循环中按需调用。

### 文件操作

| 工具 | 功能 | 源码 | 安全保护 |
|------|------|------|---------|
| `read` | 读取文件，支持行偏移和自适应分页 | `src/aion/tools/builtin/read.py` | 工作空间路径约束 |
| `write` | 写入文件（新建或覆盖），自动创建父目录 | `src/aion/tools/builtin/write.py` | 路径遍历保护 + Memory Flush 锁定 |
| `edit` | 唯一匹配文本替换（从底部向上，防位置偏移） | `src/aion/tools/builtin/edit.py` | 唯一匹配语义（拒绝不明确替换） |
| `ls` | 列出目录内容，子目录以 `/` 后缀标识 | `src/aion/tools/builtin/ls.py` | 工作空间路径约束 |
| `trash` | 移动到系统垃圾桶（可恢复） | `src/aion/tools/builtin/trash.py` | 引导文件保护（bootstrap 校验） |
| `delete` | 永久删除文件（不可恢复） | `src/aion/tools/builtin/delete.py` | 引导文件保护（bootstrap 校验） |
| `apply_patch` | 应用 git unified diff 格式补丁 | `src/aion/tools/builtin/apply_patch_tool.py` | 工作空间相对路径强制 |

安全特性：
- **路径遍历保护**：所有文件工具通过 `Path.relative_to()` 校验目标在工作空间内
- **引导文件保护**：`WORKSPACE_BOOTSTRAP.md` / `AGENT_BOOTSTRAP.md` 有额外删除安全检查（由 `tool_registry.py` 的 `_wrap_bootstrap_tools()` 注入闭包）
- **Memory Flush 模式**：在上下文压缩前，`write` 工具锁定为仅追加到日记文件

### 命令执行

| 工具 | 功能 | 支持的操作 |
|------|------|-----------|
| `exec` | 执行 shell 命令（前台同步或后台异步） | 前台：PTY/普通模式；后台：异步启动 |
| `process` | 管理后台进程 | `list` / `poll` / `log` / `kill` / `write` / `send-keys` / `submit` / `paste` |

- `exec` 后台模式下，stdout/stderr 写入 `workspace/.aion/exec_sessions/`
- `process` 支持向运行中进程的 stdin 写入数据（`write`/`send-keys`/`submit`/`paste`）
- `log` 与 `poll` 区别：`poll` 返回后若进程已结束会自动清理记录；`log` 只读不改变任务状态

### 搜索

| 工具 | 功能 | 参数 |
|------|------|------|
| `grep` | 正则搜索文件内容（自动跳过 `.git`/`.venv`/`node_modules` 等） | `pattern`, `path`, `glob_pattern`, `max_matches`, `max_file_bytes` |
| `find` | glob 模式搜索文件路径（支持 `**` 递归） | `pattern`, `path`, `max_files` |

### 记忆

| 工具 | 功能 | 存储路径 |
|------|------|---------|
| `memory_write` | 写入/覆盖永久记忆 MEMORY.md | `agents/{agent_id}/memory/MEMORY.md` |
| `daily_memory_write` | 追加今日日记 | `agents/{agent_id}/memory/YYYY-MM-DD.md` |
| `memory_search` | 语义搜索记忆（向量 + BM25） | ChromaDB + FTS5 |
| `memory_get` | 按路径读取记忆文件指定行范围 | — |

`memory_write` 全量覆盖，`daily_memory_write` 追加写入。写入后自动同步索引（FTS5 + Chroma）。

### 网络

| 工具 | 功能 | 配置 |
|------|------|------|
| `web_fetch` | HTTP GET 网页内容提取，支持 text / markdown 模式 | 无（内置 httpx + BeautifulSoup） |
| `web_search` | 联网文本搜索（博查 / 百度） | `search.webSearch.provider` + `providers.{bocha\|baidu}.apiKey` |

`web_search` 支持博查（bocha）与百度（baidu）两个 provider，由 `search.webSearch.provider` 指定当前使用哪一个。
配置结构：`search.webSearch.{provider, providers.{bocha:{apiKey}, baidu:{apiKey}}}`（两端点均固定，无需 url）。
支持 `freshness`（时间过滤：noLimit/day/week/month/semiyear/year）、`country`（国家）、`language`（语言）等参数（部分参数仅博查支持）。

### 文档处理

| 工具 | 功能 |
|------|------|
| `process_document` | 读取文本文件 → 分块 → 写入向量库，索引后可通过 `memory_search` 检索 |

### 配置管理

| 工具 | 功能 |
|------|------|
| `gateway` | 运行时查询和修改 aion 配置 |

支持动作：
- `config.get` — 获取当前完整配置
- `config.patch` — 部分更新配置（deep merge 模式）
- `config.apply` — 完整替换配置
- `config.schema_lookup` — 查看指定配置路径的说明文档
- `restart` — 发送重启信号
- `update.run` — 发送热重载信号（SIGUSR1）

安全保护：受保护路径（`tools.exec.*` 等禁止修改）+ `base_hash` 乐观锁。

### 子 Agent

| 工具 | 功能 | 可用范围 |
|------|------|---------|
| `sessions_spawn` | 派生子 Agent 执行独立任务（临时，有独立 session） | 仅 leader（`main_agent_only`） |
| `sessions_send` | 向持久团队成员的 agent 发送任务 | 仅 leader（`main_agent_only`） |
| `agents_list` | 列出工作空间所有可用 agent | 仅 leader |
| `subagents` | 管理子 Agent（`list` / `kill` / `await`） | 全部 |

- `sessions_spawn` / `sessions_send` 通过 `@tool` 自动发现注册，标记了 `main_agent_only`，subagent 循环中自动过滤
- `agents_list` / `subagents` 由 `tool_registry.py` 的 `_register_subagent_tools()` 动态创建，非 `@tool` 声明

---

## Skills

Skills 是工作空间级别的自定义指令系统。通过在工作空间目录下放置 `skills/` 目录，
管理员可以为 AI 注入特定领域的操作指南。

### 目录结构

```
{workspace}/
└── skills/
    ├── git-workflow/
    │   └── SKILL.md
    └── code-review/
        └── SKILL.md
```

### SKILL.md 格式

```markdown
---
name: Git 工作流
description: Git 分支管理和 PR 流程指引
---

Git 工作流规范：
- feature 分支从 master 分出
- commit message 使用 Conventional Commits
- 合并前必须通过 CI
```

`name` 和 `description` 来自文件头 frontmatter（YAML 格式，aion 使用正则解析，无需 yaml 库）。

### 工作原理

1. **模块位置**：Skills 核心实现在 `src/aion/skills/`（`SkillsLoader`/`Skill` 等），`src/aion/agent/skills.py` 仅为向后兼容的转发层
2. `SkillsLoader` 在 Agent 启动时扫描 `{workspace}/skills/` 目录
3. 解析每个 `SKILL.md` 的 frontmatter，构建 `<available_skills>` XML 块
4. 注入到 System Prompt 中，LLM 根据任务描述按需通过 `read` 工具读取技能内容
5. `SkillsLoader` 内部缓存结果，同一 Agent 生命周期内只扫描一次

### 注入后 System Prompt 效果

```
The following skills provide specialized instructions for specific tasks.
Use the read tool to load a skill's file when the task matches its description.

<available_skills>
  <skill>
    <name>Git 工作流</name>
    <description>Git 分支管理和 PR 流程指引</description>
    <location>{workspace}/skills/git-workflow/SKILL.md</location>
  </skill>
</available_skills>
```

### 使用场景

- 团队编码规范（命名、commit、review 标准）
- 领域知识（行业术语、业务流程）
- 工具链配置（CI/CD、部署流程）
- 安全策略（敏感信息处理规则）

---

## MCP

MCP（Model Context Protocol）是 AI 模型与外部工具之间的标准化通信协议。
aion 内置 MCP 客户端，支持连接第三方 MCP 服务器扩展工具集。

### 配置方式

在 `aion.json` 的当前工作空间配置中添加 `mcpServers`：

```json
{
  "workspaces": {
    "scopes": [{
      "default": {
        "mcpServers": {
          "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
          },
          "weather": {
            "url": "http://localhost:8000/mcp",
            "transport": "streamable-http"
          }
        }
      }
    }]
  }
}
```

### 连接模式

| 模式 | 状态 | 说明 |
|------|------|------|
| `stdio` | ✅ 已实现 | 启动子进程，通过 stdin/stdout JSON-RPC 通信 |
| `http` / `streamable_http` | ✅ 已实现 | Streamable HTTP 远程连接，支持 TCP 可达性检测 |

### 工具命名

MCP 工具按 `{server_name}_{tool_name}` 格式注册到 Agent。
例如 `filesystem` 服务器的 `read` 工具的实际调用名为 `filesystem_read`。

### 生命周期

1. **AgentLoop 首次 run() 时**（`_run_prelude()`）调用 `_init_mcp_async()`
2. **`initialize_mcp_servers()`**（`src/aion/mcp/__init__.py`）依次连接配置中的服务器，发送 initialize → tools/list
3. 工具列表聚合到 `ToolRegistry` 的 `_mcp_structured_tools` 中
4. **幂等保护**：`MCPServerManager._initialized` 防护重复初始化
5. **配置热更新**：`initialize_mcp_servers()` 检测配置变更后自动重连
6. **tools/list_changed**：服务器通知工具变更时自动刷新注册表
7. **close_all()** 在 Gateway 停止时关闭所有连接
8. **Workspace 隔离**：每个 workspace 有独立的 `MCPServerManager` 实例，由 `_mcp_instances` 按 `workspace_key` 缓存

### 协议

基于 JSON-RPC 2.0，stdio 模式下每行一条 JSON 消息（newline-delimited JSON）：

```json
→ {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read","arguments":{"path":"/test.txt"}}}
← {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"file content"}]}}
```

### 限制

- 子进程断开后不会自动重连
- 每个工具调用有默认超时（由 MCP 服务器自身控制）
