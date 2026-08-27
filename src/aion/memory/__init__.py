"""记忆模块

三层记忆架构 + 向量索引：
- 短期（Short）：JSONL 文件 + context.messages，每轮自动写入（memory/short.py）
- 中期（Mid）：memory/YYYY-MM-DD.md，每轮 auto-save 原始对话
- 长期（Long）：memory/MEMORY.md，LLM 主动 memory_write 写入

向量索引：
- VectorIndexer: 将记忆文件内容分块 → 嵌入 → ChromaDB（单一集合）
- MemorySearchTool: 搜索召回（向量 + BM25 + 时间衰减 + 关键词）
- 自动注入 top-K 相关记忆到每轮 prompt
"""

from .short import SessionStore
from .mid import DailyFileStore
from .long import LongTermStore

__all__ = [
    "SessionStore",
    "DailyFileStore",
    "LongTermStore",
]
