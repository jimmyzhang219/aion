"""飞书消息去重机制

设计文档: docs/design/feishu-channel.md 第 6.3 节
"""

import asyncio
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set
from ...core.lock import FileLock


class FeishuDedup:
    """飞书消息去重

    使用磁盘文件存储已处理的消息 ID，进行去重。
    支持并发访问。
    """

    def __init__(self, dedup_dir: Path, account_id: str = "default"):
        """初始化去重器

        启动时会异步从磁盘预热内存缓存。

        Args:
            dedup_dir: 去重文件存储目录（通常为 workspace/.aion）
            account_id: 账号 ID，用于隔离多账号去重文件

        Returns:
            None
        """
        self.dedup_dir = dedup_dir / "feishu_dedup" / account_id  # 账号级去重目录
        self.dedup_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.dedup_dir / "processed.jsonl"  # JSONL 持久化文件
        self._memory_cache: Set[str] = set()  # 内存去重键集合
        self._lock = asyncio.Lock()  # 异步写盘互斥

        # 初始化时加载已有记录到内存
        asyncio.create_task(self._warmup())

    async def _warmup(self) -> None:
        """从磁盘加载已有记录到内存缓存

        Returns:
            None
        """
        if not self.cache_file.exists():
            return

        try:
            with open(self.cache_file, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if "key" in entry:
                            self._memory_cache.add(entry["key"])
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

    def _message_key(self, message_id: str) -> str:
        """生成去重 key

        使用 SHA256 的前 16 位作为 key，既保证唯一性又保持简短。

        Args:
            message_id: 飞书消息 ID

        Returns:
            str: 16 位十六进制去重键
        """
        return hashlib.sha256(message_id.encode()).hexdigest()[:16]

    async def is_duplicate(self, message_id: str) -> bool:
        """检查消息是否已处理

        Args:
            message_id: 飞书消息 ID

        Returns:
            True if 已处理，False if 新消息
        """
        key = self._message_key(message_id)

        # 先检查内存缓存
        if key in self._memory_cache:
            return True

        # 再检查磁盘文件
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r") as f:
                    for line in f:
                        if key in line:
                            # 更新内存缓存
                            self._memory_cache.add(key)
                            return True
            except Exception:
                pass

        return False

    async def mark_processed(self, message_id: str) -> None:
        """标记消息为已处理

        写入 JSONL 并更新内存缓存；写盘失败时静默忽略。

        Args:
            message_id: 飞书消息 ID

        Returns:
            None
        """
        key = self._message_key(message_id)

        entry = {
            "key": key,
            "message_id": message_id,
            "ts": datetime.now().isoformat(),
        }

        try:
            with open(self.cache_file, "a") as f:
                # 使用文件锁保证原子性
                lock = FileLock(f)
                lock.acquire_exclusive()
                try:
                    f.write(json.dumps(entry) + "\n")
                finally:
                    lock.release()

            # 更新内存缓存
            self._memory_cache.add(key)
        except Exception:
            pass

    async def mark_processed_batch(self, message_ids: list[str]) -> None:
        """批量标记消息为已处理

        Args:
            message_ids: 消息 ID 列表

        Returns:
            None
        """
        entries = []
        keys = set()

        for message_id in message_ids:
            key = self._message_key(message_id)
            keys.add(key)
            entries.append(
                json.dumps(
                    {
                        "key": key,
                        "message_id": message_id,
                        "ts": datetime.now().isoformat(),
                    }
                )
            )

        if not entries:
            return

        try:
            with open(self.cache_file, "a") as f:
                lock = FileLock(f)
                lock.acquire_exclusive()
                try:
                    f.write("\n".join(entries) + "\n")
                finally:
                    lock.release()

            # 更新内存缓存
            self._memory_cache.update(keys)
        except Exception:
            pass

    async def cleanup_old(self, max_age_days: int = 7) -> int:
        """清理旧记录

        Args:
            max_age_days: 保留天数，默认 7 天

        Returns:
            清理的记录数
        """
        if not self.cache_file.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=max_age_days)
        remaining = []
        removed = 0

        try:
            with open(self.cache_file, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        ts = datetime.fromisoformat(entry.get("ts", "2000-01-01"))
                        if ts > cutoff:
                            remaining.append(line)
                        else:
                            removed += 1
                            # 从内存缓存移除
                            self._memory_cache.discard(entry.get("key"))
                    except (json.JSONDecodeError, ValueError):
                        remaining.append(line)

            # 重写文件
            if remaining:
                with open(self.cache_file, "w") as f:
                    lock = FileLock(f)
                    lock.acquire_exclusive()
                    try:
                        f.writelines(remaining)
                    finally:
                        lock.release()
            else:
                self.cache_file.unlink()

        except Exception:
            pass

        return removed

    async def get_stats(self) -> dict:
        """获取去重统计信息

        Returns:
            dict: 含 total_processed、memory_cache_size、cache_file 路径
        """
        count = 0
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r") as f:
                    count = sum(1 for _ in f)
            except Exception:
                pass

        return {
            "total_processed": count,
            "memory_cache_size": len(self._memory_cache),
            "cache_file": str(self.cache_file),
        }
