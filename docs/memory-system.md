# Aion 记忆系统设计文档

> 涉及模块：`memory/`、`rag/`、`session/`、`agent/`

---

## 目录

### 第一部分：记忆机制设计

1. [概述](#1-概述)
2. [三层记忆架构](#2-三层记忆架构)
3. [存储格式与持久化](#3-存储格式与持久化)
4. [写入一致性：on_write 回调 + 异步索引任务](#4-写入一致性on_write-回调--异步索引任务)
5. [关键数据流](#5-关键数据流)

### 第二部分：多路召回

6. [双通道检索机制：稠密 + 稀疏](#6-双通道检索机制稠密--稀疏)
7. [评分融合与时间衰减](#7-评分融合与时间衰减)
8. [检索流](#8-检索流)
9. [配置参考](#9-配置参考)
10. [常见问题](#10-常见问题)

---

# 第一部分：记忆机制设计

## 1. 概述

Aion 的记忆系统采用**三层记忆 + 双通道（稠密 + 稀疏）检索**架构，将对话过程中的信息持久化到磁盘，并在需要时通过语义和关键词两种方式高效召回。

一句话总结：

> 三层记忆（会话/每日/永久）通过 JSONL/Markdown 持久化到磁盘，由 **on_write 回调异步触发 VectorIndexer** 进行 Chroma（稠密）+ FTS5（稀疏）双写，检索时以 Dense Vector 覆盖全部、FTS5 BM25 覆盖全部，在应用层融合加权、时间衰减后返回给 LLM。

### 核心设计原则

- **写磁盘同步、索引异步**：文件写入实时完成，向量索引通过 `asyncio.create_task` 异步处理，不阻塞对话
- **双通道互补**：语义搜索（Dense/稠密）捕捉意图，关键词搜索（BM25/稀疏）精确命中
- **失败降级**：任一通道故障时自动降级到另一通道，不中断服务
- **无硬截断**：所有记忆数据在程序逻辑中不做强制截断，超预算时记录警告但保留全文
- **MEMORY.md 自动加载**：永久记忆作为 bootstrap 文件之一，每次对话自动全量注入 system prompt
- **JSONL 永久保留**：会话 JSONL 文件永不删除，历史可回溯
- **chunk_id 融合**：双通道检索以 chunk_id 为键 Union，不做文件级别折叠

第一部分介绍记忆的存储、写入和持久化机制；第二部分详细介绍多路召回的实现细节。

---

## 2. 三层记忆架构

```
┌─────────────────────────────────────────────────────────────┐
│                       AgentLoop                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                   ContextManager                     │   │
│  │  ┌──────────────┐  ┌──────────┐  ┌───────────────┐   │   │
│  │  │ SessionStore │  │DailyFile │  │ LongTermStore │   │   │
│  │  │  (会话级)     │  │ Store    │  │  (永久记忆)    │   │   │
│  │  │              │  │ (每日记忆)│  │               │   │   │
│  │  └──────┬───────┘  └────┬─────┘  └───────┬───────┘   │   │
│  │         │               │                │           │   │
│  │         ▼               ▼                ▼           │   │
│  │    JSONL 文件       .md 文件         .md 文件          │   │
│  │         │               │                │           │   │
│  │         │        on_write 回调    on_write 回调       │   │
│  │         │               │                │           │   │
│  │         ▼               ▼                ▼           │   │
│  │    ┌────────────────────────────────────────────┐    │   │
│  │    │       VectorIndexer.handle_task()          │    │   │
│  │    │      [Chroma + FTS5 双写引擎]               │    │   │
│  │    └────────────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 会话级记忆（SessionStore）

**用途**：记录每一轮 user↔assistant 的完整对话，用于上下文加载和历史回溯。

**文件路径**：
```
{workspace}/agents/{agent_id}/sessions/{YYYY-MM-DD_HH-MM-SS}_{session_id}.jsonl
```

**JSONL 格式**（每行一个 JSON 对象）：

```json
{"type":"message","message":{"role":"user","content":"你好","timestamp":"2026-06-16T09:01:43.416188"}}
{"type":"message","message":{"role":"assistant","content":"你好！有什么可以帮助你的？","reasoning_content":"我会先问候用户","timestamp":"2026-06-16T09:01:43.519201"}}
{"type":"compaction","message":{"role":"system","content":"最近一次摘要消息","timestamp":"2026-06-16T09:31:43.416188"}}
```

- assistant 消息可选包含 `reasoning_content` 字段（用于 DeepSeek 等推理模型的思维链内容）

- **写入时机**：每轮 LLM 响应生成后、返回用户前，`persist_turn()` 同步追加写入
- **异步屏障**：写入后 `await asyncio.gather(*_pending_index_tasks, return_exceptions=True)` 等待所有异步索引完成；再同步索引本轮会话内容（确保下一轮搜索可命中）
- **Compaction**：当上下文中 token 数达到模型窗口的 80% 时，触发 LLM 摘要压缩，追加 `type:"compaction"` 条目（不删除原始消息）
- **`会话历史`**：最后一条 compaction + 其后的所有消息（不含本次 user 消息）
- **文件保留**：旧 JSONL **永不删除**。`/new` 或新会话只创建新文件，历史文件永久保留用于回溯

### 2.2 每日记忆（DailyFileStore）

**用途**：按日历日期归档重要的对话摘要和自动记忆，用于跨会话信息检索。

**文件路径**：
```
{workspace}/agents/{agent_id}/memory/YYYY-MM-DD.md
```

**文件格式**（Markdown + 时间戳）：

```markdown
<!-- 2026-06-16T09:01:43.416188 -->
用户询问了 TypeScript 类型系统的设计思路

<!-- 2026-06-16T09:31:19.521034 -->
讨论了 React 状态管理方案，最终选择了 Zustand
```

- **写入时机**（两种触发方式）：
  1. **LLM 主动调用**：通过 `daily_memory_write` 工具写入摘要
  2. **`/new` 命令 / 空闲超时**：开始新会话前，LLM 自动生成当前 session 的摘要后追加写入（无需 LLM 主动调用）
- **写入方式**：`append()` 同步追加写入 Markdown → `on_write` 回调创建 `asyncio.create_task()` 异步触发 `VectorIndexer.handle_task()` 全量覆盖索引（先删除该 date 的旧块，重新分块后全量写入 Chroma + FTS5）
- **时间衰减**：按文件名日期计算衰减系数
- **on_write 失败**：记录 `logger.warning`，不中断写入流程

### 2.3 永久记忆（LongTermStore）

**用途**：保存用户的长期偏好、个人信息、关键决定等需要跨所有会话保持的信息。

**文件路径**：
```
{workspace}/agents/{agent_id}/memory/MEMORY.md
```

- **写入时机**：LLM 通过 `memory_write` 工具全量覆盖写入
- **加载方式**：作为 bootstrap 文件自动全量加载到 `# Project Context` 中（与 WORKSPACE.md、USER.md 等一起），LLM 在每次对话开始即可看到完整内容
- **字符约束**：prompt 中引导 LLM 控制内容在 12000 字符以内，程序不做硬截断
- **覆盖语义**：`overwrite()` 同步写磁盘 → `on_write` 回调（ContextManager 内通过 `asyncio.create_task()` 异步触发全量覆盖索引，先按 path 删除旧块再重新分块写入；LLM 工具函数内则通过 `_sync_index()` 同步索引）
- **时间衰减**：不衰减
- **on_write 失败**：记录 `logger.warning`，不中断写入流程

---

## 3. 存储格式与持久化

### 3.1 目录结构

```
~/.aion/workspaces/{workspace_id}/
└── agents/
    └── {agent_id}/
        ├── chroma/                  ← ChromaDB 向量库（Dense Vector 通道）
        ├── fts5/
        │   └── memory_search.db     ← SQLite FTS5 全文索引（BM25 通道）
        ├── memory/
        │   ├── MEMORY.md            ← 永久记忆
        │   ├── 2026-06-15.md        ← 每日记忆（按日期）
        │   └── 2026-06-16.md
        └── sessions/
            ├── 2026-06-15_10-30-00_{uuid}.jsonl
            └── 2026-06-16_09-01-43_{uuid}.jsonl
```

### 3.2 ChromaDB（Dense Vector 语义通道）

```
Collection 名: "{workspace_name}_{agent_id}_memories"
Embedding: create_embeddings(config) 工厂方法
HNSW space: cosine

Document:
  id:        "{chunk_id}"     — 与 FTS5 一致的 chunk_id（融合时用）
  document:  "分块后的文本内容"
  embedding: 自动生成的 Dense Vector
  metadata:
    id:      "{chunk_id}"     — 与 FTS5 共享的 chunk_id，用于融合去重
    source:  "sessions" | "daily" | "memory" | "rag_doc"
    path:    "agents/main/memory/MEMORY.md"
    date:    "2026-06-16"
    seq:     块序号

> **注**：此外 `source:"rag_doc"` 用于通过 `VectorIndexer.index_document()` 导入的外部文档
```

### 3.3 SQLite FTS5（BM25 关键词通道）

```sql
-- 内容表存储元数据
CREATE TABLE memory_content (
    id      TEXT PRIMARY KEY,
    path    TEXT NOT NULL,
    source  TEXT NOT NULL,
    date    TEXT NOT NULL,
    seq     INTEGER NOT NULL DEFAULT 0,
    text    TEXT NOT NULL
);

-- FTS5 虚拟表（trigram 分词，天然支持中文）
CREATE VIRTUAL TABLE memory_fts USING fts5(
    text,
    content=memory_content,
    tokenize='trigram',
    content_rowid='rowid'
);

CREATE INDEX idx_mc_date   ON memory_content(date);
CREATE INDEX idx_mc_source ON memory_content(source);
CREATE INDEX idx_mc_path   ON memory_content(path);
```

使用 SQLite FTS5 的 **trigram tokenizer** 处理中文分词，无需额外依赖（不依赖 jieba/ICU/分词库）。

---

## 4. 写入一致性：on_write 回调 + 异步索引任务

### 4.1 设计动机

三层记忆分别写入磁盘后，需要同步索引到 ChromaDB 和 FTS5 两个独立存储系统。直接串行写入会阻塞对话流程，完全异步则可能出现时序错乱。当前设计采用 **同步写磁盘 + 异步索引 + persist_turn 等待屏障** 的混合策略：

- 每日/永久记忆写磁盘后，通过 `on_write` 回调异步触发索引，不阻塞 LLM
- 会话级记忆在 `persist_turn()` 中同步等待所有异步索引完成后再返回
- `persist_turn()` 等待队列确保会话索引在下一轮检索前完成

### 4.2 架构

```
写入线程（调用线程立即执行）:
  ├── SessionStore.append()        → 追加 JSONL（会话级，同步）
  ├── DailyFileStore.append()      → 追加 YYYY-MM-DD.md（每日，同步）
  ├── DailyFileStore.overwrite()   → 覆写 YYYY-MM-DD.md（每日，同步）
  └── LongTermStore.overwrite()    → 覆写 MEMORY.md（永久，同步）

索引触发（两种路径）:
  │
  ├── ContextManager 内（on_write 回调，异步）:
  │   ├── _on_daily_write()  — 创建 WriteTask(type="overwrite_daily")
  │   │   └── asyncio.create_task(VectorIndexer.handle_task(task))
  │   │       └── _track_task() 注册到 _pending_index_tasks
  │   │
  │   └── _on_memory_write() — 创建 WriteTask(type="overwrite_memory")
  │       └── asyncio.create_task(VectorIndexer.handle_task(task))
  │           └── _track_task() 注册到 _pending_index_tasks
  │
  ├── LLM 工具函数内（同步索引）:
  │   └── memory_write / daily_memory_write 创建独立的 store 实例，
  │       on_write 回调调 _sync_index()（同步）:
  │       └── VectorIndexer._delete_by_path() + _write_chunks()
  │           不涉及 asyncio.create_task，不经过 _pending_index_tasks
  │
  ├── VectorIndexer.handle_task() 执行顺序:
  │   1. 分块（段落感知，chunk_size=400, overlap=80）
  │   2. 先写 FTS5（同步 SQLite 写入）
  │   3. 再写 Chroma（同步 embedding + 插入）
  │
  └── 失败处理:
      ├── FTS5 写失败 → 记录警告，尝试 Chroma
      ├── Chroma 写失败 → 记录警告，FTS5 已写入
      └── 两者均失败 → 记录错误

persist_turn 等待屏障（对话轮次结束时）:
  ├── 1. SessionStore.append_messages() 同步写入本轮对话到 JSONL
  ├── 2. await asyncio.gather(*_pending_index_tasks, return_exceptions=True)
  │     等待所有异步索引完成（单个任务失败不阻塞整体）
  └── 3. 同步索引本轮会话内容（取首条 user + 末条 assistant 合并）到 Chroma+FTS5
```

### 4.3 同步 vs 异步索引

| 记忆层 | 写磁盘 | 索引触发 | 等待保证 |
|--------|--------|----------|----------|
| 会话级（SessionStore） | 同步 | `persist_turn()` 直接调用 `handle_task()` | **同步等待**，下一轮检索前已完成 |
| 每日（DailyFileStore） | 同步 | `on_write` 回调 → `asyncio.create_task()` | 异步不等待，`persist_turn` 汇总等待 |
| 永久（LongTermStore） | 同步 | `on_write` 回调 → `asyncio.create_task()` | 异步不等待，`persist_turn` 汇总等待 |

> **注**：LLM 工具函数 `memory_write` / `daily_memory_write` 的索引路径独立于 ContextManager，使用同步 `_sync_index()` 直接写入 FTS5 + Chroma，不涉及 `asyncio.create_task`。

### 4.4 分块策略（段落感知）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `chunk_size` | 400 字符 | 每块最大字符数（中文约 260 tokens） |
| `chunk_overlap` | 80 字符 | 相邻块重叠字符数 |

分块逻辑按段落边界切分（`\n` 分隔），单段落超限时按字符滑窗切分。

---

## 5. 关键数据流

### 5.1 对话写入流

```
用户消息 → AgentLoop.run()
  ├── ctx_mgr.persist_turn(user_input, response)
  │   ├── SessionStore.append()       → 同步写入 session JSONL
  │   ├── await gather(pending_tasks) → 等待异步索引完成
  │   └── await vector_indexer.handle_task(task)  → 同步索引本轮对话到 Chroma+FTS5
  │
  ├── LLM 调 memory_write 工具
  │   ├── LongTermStore.overwrite()   → 同步覆写 MEMORY.md
  │   └── on_write 回调               → _sync_index() 同步索引到 FTS5 + Chroma
  │       （工具函数使用独立 VectorIndexer 实例，同步写入，不经过 ContextManager 的事件循环）
  │
  ├── LLM 调 daily_memory_write 工具
  │   ├── DailyFileStore.append()     → 同步追加 YYYY-MM-DD.md
  │   └── on_write 回调               → _sync_index() 同步索引到 FTS5 + Chroma
  │
  └── /new 命令 / 空闲超时
      ├── LLM 生成当前 session 摘要
      └── DailyFileStore.append()     → 同步追加摘要到 YYYY-MM-DD.md
```

### 5.2 会话加载流

```
网关启动 / 会话恢复
  │
  └── ContextManager.__init__()
      ├── SessionStore(session_id, sessions_dir)
      │   ├── _find_existing()        → 查找已有 session JSONL
      │   └── 创建或复用文件路径
      │
      ├── VectorIndexer               → 初始化 Chroma + FTS5 索引器
      ├── Compaction                  → 初始化压缩管理器
      │
      └── _load_history()
          ├── SessionStore.get_compaction_boundary()
          │   → 返回 (last_compaction_entry, subsequent_messages)
          ├── compaction 存在 → 注入 system 消息作为摘要
          └── subsequent messages → 注入到 context 作为对话历史
```

### 5.3 启动上下文召回流

```
AgentLoop.run() 首次构建上下文
  │
  ├── build_system_prompt()             → 注入 bootstrap 文件（SOUL/USER/MEMORY.md）
  │
  └── build_startup_context()           → 注入 Startup Context（启动上下文）
      │
      ├── 1. build_daily_memory_startup_prelude()
      │   ├── 按 daily_memory_days（默认 2 天）列出 YYYY-MM-DD.md
      │   ├── 同时扫描 workspace/memory/ 和 agents/{agent_id}/memory/
      │   ├── 读取近 N 天日记忆文件内容
      │   └── 格式化为 [Untrusted daily memory: ...] quoted block
      │
      └── 2. Startup Memory Recall（跨会话语义召回）
          ├── 创建 MemorySearchTool(max_results=5, min_score=0.01)
          ├── 3 个固定召回查询：
          │   ├── "用户 名字 用户名 称呼"     → 搜索用户名/称呼
          │   ├── "偏好 喜欢 讨厌"           → 搜索用户偏好
          │   └── "待办 计划 任务"           → 搜索待办事项
          ├── 按 path 去重，调用 tool.get() 获取完整内容
          └── 格式化为 "Startup Context — Memory Recall" 注入 context
```

### 5.4 Compaction 流

```
发消息前检测: 上下文 token > 80% 模型窗口?
  │
  ├── 否 → 正常处理
  │
  └── 是 → Compaction.compact(messages)
      ├── 复制 session JSONL 为 .checkpoint.{uuid}.jsonl
      ├── 调用 LLM 生成对话摘要
      ├── 摘要注入 context（替换原始历史）
      └── 追加 compaction 条目到 session JSONL（不删原文）
```

---

# 第二部分：多路召回

## 6. 双通道检索机制：稠密 + 稀疏

```
                    用户查询
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
   Chroma Dense（全部来源）    SQLite FTS5 BM25（全部来源）
   n_results=200              n_results=200
          │                             │
          └──────────┬─────────────────┘
                     ▼
           应用层融合器（Chunk-level Linear Weighted Fusion）:
            1. 各通道分数归一化到 [0, 1]
            2. Union by chunk_id（不做 path 级别折叠）
            3. 时间衰减（按 source 不同 λ）
            4. 加权融合（0.6 vectorScore + 0.4 textScore）
            5. min_score 过滤（默认 0.2）
            6. Top-K 排序（默认 10）
                     │
                     ▼
              返回给 LLM
```

### 6.1 Chroma Dense 通道（稠密检索）

- 使用 `similarity_search_with_relevance_scores` 或 `similarity_search_with_score`（自动适配 API）
- 使用 Embedding 模型将查询转为稠密向量，在 ChromaDB 中做余弦相似度搜索
- 搜索全部 `memories` collection，不设 metadata 过滤
- 返回候选集大小：默认 200 条
- 降级：Chroma 不可用时返回空列表 → 纯 BM25 通道

### 6.2 FTS5 BM25 通道（稀疏检索）

- 使用 SQLite FTS5 内置 `bm25()` 函数计算 BM25 分数
- 查询词条在倒排索引中匹配，按词频和逆文档频率加权
- 搜索全部来源（不设 WHERE 过滤）
- trigram tokenizer 自动处理中英文混合分词
- 异常查询（特殊字符、格式错误）返回空列表 → 纯 Dense 通道

### 6.3 降级策略

| Chroma（稠密） | FTS5（稀疏） | 行为 |
|--------|------|------|
| ✅ | ✅ | 双通道融合 |
| ❌ | ✅ | 纯稀疏检索（FTS5 BM25） |
| ✅ | ❌ | 纯稠密检索（Chroma） |
| ❌ | ❌ | 返回空结果 |

---

## 7. 评分融合与时间衰减

### 7.1 融合公式（稠密 + 稀疏加权）

采用 **Chunk-level Linear Weighted Fusion**，以 chunk_id 为键做 Union，不折叠同一文件的多个块。

```
Map<chunk_id, { vectorScore, textScore, content }>

1. Vector 结果全部插入 Map（textScore = 0）
2. FTS5 结果遍历合并（同 chunk_id 只补 textScore）
3. final_score = vectorWeight × vectorScore + textWeight × textScore

权重: vectorWeight = 0.6, textWeight = 0.4（归一化 sum = 1）
```

### 7.2 字段所有权

| 字段 | 来源 | 说明 |
|------|------|------|
| `chunk_id`, `path`, `source`, `date` | 共同 | 索引时写入一致 |
| `vectorScore` | Chroma Dense 通道 | FTS5 无条件设置 0 |
| `textScore` | FTS5 BM25 通道 | 合并时覆盖 Map 中的值 |
| `content` | FTS5 优先 | FTS5 的片段包含命中关键词 |

### 7.3 时间衰减

按来源类型使用不同的衰减系数，反映不同层记忆的时效性：

| 来源 | λ（衰减系数） | 10 天后剩余 | 30 天后剩余 | 依据 |
|------|:----------:|:---------:|:---------:|------|
| sessions（会话级） | 0.3 | 0.77 | 0.53 | 会话信息快速过时 |
| daily（每日记忆） | 0.15 | 0.87 | 0.69 | 每日摘要有一定持久性 |
| memory（永久记忆） | 0.0 | 1.0 | 1.0 | 永久记忆不衰减 |

衰减公式：

```
decay = 1.0 / (1.0 + λ × days_ago)
score_decayed = score × max(0.5, decay)
```

`max(0.5, decay)` 确保衰减后的分数不低于原始值的 50%，避免过旧信息完全不可见。

### 7.4 默认参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `top_k` | 10 | 最终返回给 LLM 的结果数。**工具函数实际值**：`memory_search` 使用 `top_k=6`；启动上下文召回使用 `max_results=5` |
| `n_results_per_channel` | 200 | 各通道候选集大小 |
| `min_score` | 0.2 | 最终分数阈值，低于此值丢弃 |
| `weight_dense` | 0.6 | 稠密通道融合权重（vectorScore） |
| `weight_bm25` | 0.4 | 稀疏通道融合权重（textScore） |

---

## 8. 检索流

```
LLM 决定调用 memory_search(query)
  │
  ├── MemorySearchTool.search(query)
  │   ├── _semantic_search(query)     → Chroma Dense（稠密检索，全部来源）
  │   ├── _keyword_search(query)      → FTS5 BM25（稀疏检索，全部来源）
  │   └── _fuse_results(dense, fts)   → 融合排序
  │
  └── 返回 top_k 条结果的格式化文本给 LLM
```

---

## 9. 配置参考

### 9.1 MemoryConstants（`core/constants.py`）

```python
class MemoryConstants:
    # 分块
    CHUNK_SIZE: int = 400            # 分块字符数
    CHUNK_OVERLAP: int = 80          # 重叠字符数

    # 检索
    MAX_RESULTS: int = 10            # 检索返回上限
    MIN_SCORE: float = 0.2           # 最低相关度阈值
    N_RESULTS_PER_CHANNEL: int = 200 # 各通道候选集
    WEIGHT_DENSE: float = 0.6        # vectorScore 融合权重
    WEIGHT_BM25: float = 0.4         # textScore 融合权重

    # 时间衰减
    DECAY_LAMBDA_SESSION: float = 0.3   # 会话衰减
    DECAY_LAMBDA_DAILY: float = 0.15    # 每日衰减
    DECAY_LAMBDA_MEMORY: float = 0.0    # 永久不衰减
```

### 9.2 记忆模块配置（`aion.json`）

记忆模块支持在 `aion.json` 中精细化配置：

```json
{
  "memory": {
    "enabled": true,
    "startup_context_enabled": true,
    "daily_memory_days": 2,
    "max_file_bytes": 16384,
    "max_file_chars": 1200,
    "max_total_chars": 2800,
    "bootstrap_max_chars": 20000,
    "bootstrap_total_max_chars": 150000,
    "memory_search": true,
    "memory_get": true,
    "context_injection": "always",
    "embedding": {
      "provider": "ollama",
      "openai": {
        "model": "text-embedding-3-small",
        "api_key": ""
      },
      "ollama": {
        "model": "bge-m3",
        "base_url": "http://localhost:11434"
      }
    }
  }
}
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | true | 记忆模块总开关 |
| `startup_context_enabled` | bool | true | 启动时是否加载相关记忆到 context |
| `daily_memory_days` | int | 2 | 每日记忆扫描天数（兼容字段，不再用于检索范围） |
| `max_file_bytes` | int | 16384 | 单个文件最大读取字节数 |
| `max_file_chars` | int | 1200 | 单个文件注入最大字符数 |
| `max_total_chars` | int | 2800 | 记忆注入总字符上限 |
| `bootstrap_max_chars` | int | 20000 | 单个 bootstrap 文件最大字符数 |
| `bootstrap_total_max_chars` | int | 150000 | 所有 bootstrap 文件合计最大字符数 |
| `memory_search` | bool | true | 是否启用 memory_search 工具 |
| `memory_get` | bool | true | 是否启用 memory_get 工具 |
| `context_injection` | str | "always" | 上下文注入策略（always / on_demand） |
| `embedding.provider` | str | "ollama" | 嵌入模型提供商（ollama / openai） |
| `embedding.openai.model` | str | "text-embedding-3-small" | OpenAI 嵌入模型名 |
| `embedding.ollama.model` | str | "bge-m3" | Ollama 本地嵌入模型名 |

嵌入模型配置支持 `ollama`（bge-m3 等本地模型，默认）和 `openai`（text-embedding-3-small，兼容智谱等国内厂商）。

> **注意**：`memory` 配置位于 `aion.json` **顶层**（与 `workspaces` 平级）。实际项目中，`aion setup` 生成的默认配置将 `memory` 写在工作空间内部，但 Pydantic 验证时会将该键忽略并回退到默认值。`compaction` 和 `pruning` 配置位于工作空间层级（与 `agents` 平级）。详细默认值如下：

**CompactionConfig**（工作空间级别）：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `compaction.enabled` | bool | true | 是否启用压缩 |
| `compaction.trigger_ratio` | float | 0.8 | 触发压缩的 token 比例阈值 |
| `compaction.keep_recent` | int | 4 | 保留最近 N 条消息不压缩 |
| `compaction.use_checkpoint` | bool | true | 是否在压缩前创建 checkpoint 快照 |

**PruningConfig**（工作空间级别）：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `pruning.enabled` | bool | true | 是否启用裁剪 |
| `pruning.max_messages` | int | 30 | 上下文最大消息条数 |
| `pruning.keep_recent` | int | 6 | 始终保留最近 N 条 |
| `pruning.keep_system` | bool | true | 是否保留 system 消息 |
| `pruning.max_context_chars` | int | 50000 | 上下文总字符上限 |

---

## 10. 常见问题

### Q: 为什么选择 SQLite FTS5 而不是 Elasticsearch？

FTS5 零依赖（Python 内置 sqlite3 模块），部署简单，适合单机场景。trigram tokenizer 自动处理中文分词。Elasticsearch 需要单独部署服务，对于个人 AI 助手场景太重。

### Q: 为什么会话级记忆也要索引到向量库？

最初设计只索引每日和永久记忆，但实践中发现用户经常需要跨会话搜索之前的完整对话（不仅是摘要）。三级记忆全量索引到 Chroma + FTS5，通过 `metadata.source` 区分来源，检索时按需过滤或全量搜索。

### Q: 为什么 Compaction 不删除原始消息？

早期版本在压缩后删除原始消息以节省磁盘空间，但这会导致：
- 无法回溯压缩前的完整对话（只能依赖 checkpoint 快照）
- 向量库中的会话块成为孤儿（原始 JSONL 行已删除）

改为追加 compaction 保留原文后，向量索引始终有效，checkpoint 文件只在压缩时创建一次。

### Q: Chroma 和 FTS5 写入失败怎么办？

两者独立写入，一方失败不影响另一方。检索时自动降级到可用的通道。两通道均失败时返回空结果，不会中断对话流程。错误记录到 logger.warning。

### Q: `check_same_thread=False` 安全吗？

FTS5 的 sqlite3 连接设置了 `check_same_thread=False`，因为 LLM 工具函数可能在线程池中被 LangGraph 调用。SQLite 的 WAL 模式允许多线程读，写入通过锁串行化，在此场景下安全。

### Q: 融合策略为什么用 chunk_id 而不是 path 去重？

同一文件的不同 chunk 可能被不同通道分别命中。path 级别去重会丢失其中一个 chunk 的内容。chunk_id 级别的 Union 保留所有匹配的 chunk，各自独立参与融合排序。

### Q: 为什么旧的 session JSONL 文件不删除？

历史 JSONL 文件永久保留，用于回溯和 future 搜索。磁盘空间约占每轮对话几 KB，影响可忽略。删除文件不会释放向量库中对应的索引（索引独立管理），删除只会丢失原文检索能力。

### Q: 为什么不再使用独立的 FIFO 队列（asyncio.Queue）？

早期版本使用 `memory/queue.py` 的 FIFO WriteQueue + 单协程消费者来串行化索引任务，但该设计引入了额外的复杂度：
- 队列消费者的启停时序需要与 AgentLoop 生命周期严格同步
- 关闭时残留的任务无法优雅等待完成

当前设计改用 **on_write 回调 + asyncio.create_task + persist_turn 等待屏障**：
- DailyFileStore 和 LongTermStore 通过构造函数注入的 `on_write` 回调触发异步索引
- 回调创建 `asyncio.Task` 并通过 `_pending_index_tasks` 集合追踪
- `persist_turn()` 在每轮对话结束前 `await gather(*)` 等待所有异步索引完成
- 无需管理队列生命周期，任务追踪自动随 ContextManager 生命周期

### Q: 会话级索引为什么是同步的？

`persist_turn()` 在追加本轮对话 JSONL 后，先 `await gather(*_pending_index_tasks, return_exceptions=True)` 等待之前异步索引完成（单任务失败不阻塞），再同步调用 `handle_task()` 索引本轮会话内容。这样设计保证了：下一轮 LLM 调用 `memory_search` 时，之前所有索引都已就绪。每日/永久记忆的索引在生成后不一定立刻需要被搜索到，因此可以异步不等待；但会话记忆是短期高频检索的，同步索引确保了即时可见。
