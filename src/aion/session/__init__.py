"""Session 模块

管理 Agent 的会话历史，包含：
- Transcript: 对话记录存储（JSONL 格式）
- Compaction: 会话压缩（当上下文接近上限时压缩历史）
- context_trimmer: 三层渐进裁剪（soft_trim → hard_clear → drop_oldest）+ 孤儿工具调用修复
- SessionLister: Session 列举器（列表）

设计理念：
- 每个 Session 有独立的 JSONL 文件存储历史
- Compaction 时创建 checkpoint 快照，允许回溯
- context_trimmer 提供 prune_context 裁剪 + cleanup_orphaned_tool_calls 修复配对断裂
"""
