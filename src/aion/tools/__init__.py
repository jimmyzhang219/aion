"""工具模块

提供 Agent 可调用的各类工具及上下文保护机制：

子模块概览：
- builtin/：内置工具实现
  - read / write / edit / ls：文件读写与目录列表
  - exec / process：Shell 执行与后台任务管理
  - grep / find：工作区内容搜索与路径查找
  - apply_patch：unified diff 补丁应用
  - web_fetch / web_search：HTTP 抓取与联网搜索
  - trash / delete：安全删除（垃圾桶）与永久删除
  - memory_write / memory_search / memory_get：中期记忆读写与召回
  - gateway：aion 配置查询与修改（config.get/patch/apply 等）
- registry.py：全局工具注册表，统一管理注册与查找

设计理念：
- 无特殊拦截机制，LLM 通过 System Prompt 中的工具描述自主决策
- System Prompt 告诉 LLM 有哪些工具可用，LLM 自己决定何时调用
- "保存一下记忆" -> LLM 看到 write 或 memory_write 工具，自主调用
- 工具描述就是 LLM 的"眼睛"，告诉它有哪些能力
"""
