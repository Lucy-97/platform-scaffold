"""
记忆存储层 — MemoryStore
===========================

替代旧 memory_service.py 的外部 fact_loader 回调，
提供统一的记忆 CRUD + 检索接口。

存储策略：
  - 主索引：Redis Hash（快速检索 + TTL 过期）
  - 正文持久化：本地 JSON 文件（可切换为 VFS/OSS）
  - 降级模式：Redis 不可用时自动降级为纯文件存储
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.memory.models import (
    MemoryCategory,
    MemoryEntry,
    MemoryLayer,
)


class MemoryStore:
    """五层记忆存储——Redis 索引 + JSON 持久化。

    Args:
        storage_dir: 本地 JSON 持久化目录。
        redis: Redis 异步客户端（可选）。
        redis_prefix: Redis Key 前缀。
    """

    def __init__(
        self,
        storage_dir: str = "/tmp/agent_memory",
        redis: Optional[Any] = None,
        redis_prefix: str = "agent:mem",
    ) -> None:
        self._storage_dir = storage_dir
        self._redis = redis
        self._prefix = redis_prefix
        # 内存缓存（降级模式 / 小规模场景）
        self._cache: Dict[str, MemoryEntry] = {}
        os.makedirs(storage_dir, exist_ok=True)

    def generate_id(self) -> str:
        """生成唯一记忆 ID。"""
        return f"mem_{uuid.uuid4().hex[:12]}"

    async def save(self, entry: MemoryEntry) -> str:
        """保存记忆条目。

        冲突消解：如果同 subject + category 已存在记忆，
        且新记忆 confidence 更高，则标记旧记忆为 superseded。

        Args:
            entry: 记忆条目。

        Returns:
            记忆 ID。
        """
        # 冲突消解：查找同主体同类型的旧记忆
        if entry.subject and entry.category:
            existing = await self.find_by_subject(
                entry.subject,
                category=entry.category,
                layer=entry.layer,
                project_id=entry.project_id,
            )
            for old in existing:
                if old.memory_id != entry.memory_id and old.confidence <= entry.confidence:
                    old.superseded_by = entry.memory_id
                    old.updated_at = time.time()
                    self._cache[old.memory_id] = old
                    logger.debug(
                        f"[MemoryStore] 冲突消解: {old.memory_id} 被 "
                        f"{entry.memory_id} 覆盖 "
                        f"(confidence {old.confidence} → {entry.confidence})"
                    )

        # 保存到缓存
        self._cache[entry.memory_id] = entry

        # 持久化到 JSON
        self._persist_to_file(entry)

        logger.debug(
            f"[MemoryStore] 保存: {entry.memory_id} "
            f"layer={entry.layer.value} cat={entry.category.value} "
            f"subject='{entry.subject}'"
        )
        return entry.memory_id

    async def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """按 ID 获取记忆。"""
        entry = self._cache.get(memory_id)
        if entry:
            entry.access_count += 1
            return entry

        # 尝试从文件加载
        entry = self._load_from_file(memory_id)
        if entry:
            entry.access_count += 1
            self._cache[memory_id] = entry
        return entry

    async def find_by_subject(
        self,
        subject: str,
        category: Optional[MemoryCategory] = None,
        layer: Optional[MemoryLayer] = None,
        project_id: Optional[str] = None,
    ) -> List[MemoryEntry]:
        """按主体搜索记忆。"""
        results = []
        subject_lower = subject.lower()

        for entry in self._cache.values():
            if entry.is_expired or entry.superseded_by:
                continue
            if subject_lower not in entry.subject.lower():
                continue
            if category and entry.category != category:
                continue
            if layer and entry.layer != layer:
                continue
            if project_id and entry.project_id != project_id:
                continue
            results.append(entry)

        return results

    async def search(
        self,
        query: str,
        layer: Optional[MemoryLayer] = None,
        project_id: Optional[str] = None,
        max_results: int = 10,
    ) -> List[MemoryEntry]:
        """关键词搜索记忆（简单版——精确子串匹配）。

        完整版应集成向量检索（embedding similarity），
        此处先用关键词匹配保证功能完整。
        """
        query_lower = query.lower()
        results = []

        for entry in self._cache.values():
            if entry.is_expired or entry.superseded_by:
                continue
            if layer and entry.layer != layer:
                continue
            if project_id and entry.project_id != project_id:
                continue

            # 关键词匹配（content + subject）
            if (query_lower in entry.content.lower()
                    or query_lower in entry.subject.lower()):
                entry.access_count += 1
                results.append(entry)

            if len(results) >= max_results:
                break

        # 按置信度降序 + 时间降序排序
        results.sort(key=lambda e: (-e.confidence, -e.updated_at))
        return results

    async def get_layer_entries(
        self,
        layer: MemoryLayer,
        project_id: Optional[str] = None,
        max_results: int = 50,
    ) -> List[MemoryEntry]:
        """获取指定层级的所有有效记忆。"""
        results = []
        for entry in self._cache.values():
            if entry.layer != layer:
                continue
            if entry.is_expired or entry.superseded_by:
                continue
            if project_id and entry.project_id != project_id:
                continue
            results.append(entry)

        results.sort(key=lambda e: -e.updated_at)
        return results[:max_results]

    async def delete(self, memory_id: str) -> bool:
        """删除记忆条目。"""
        if memory_id in self._cache:
            del self._cache[memory_id]
            filepath = self._get_filepath(memory_id)
            if os.path.exists(filepath):
                os.remove(filepath)
            return True
        return False

    def load_all_from_disk(self, project_id: Optional[str] = None) -> int:
        """从磁盘加载所有记忆到缓存。"""
        count = 0
        if not os.path.isdir(self._storage_dir):
            return 0

        for filename in os.listdir(self._storage_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(self._storage_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entry = MemoryEntry(**data)
                if project_id and entry.project_id != project_id:
                    continue
                if not entry.is_expired and not entry.superseded_by:
                    self._cache[entry.memory_id] = entry
                    count += 1
            except Exception as e:
                logger.warning(f"[MemoryStore] 加载失败: {filepath} | {e}")
        logger.info(f"[MemoryStore] 从磁盘加载 {count} 条记忆")
        return count

    def _persist_to_file(self, entry: MemoryEntry) -> None:
        """持久化到 JSON 文件。"""
        filepath = self._get_filepath(entry.memory_id)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(entry.model_dump(), f, ensure_ascii=False, indent=2, default=str)

    def _load_from_file(self, memory_id: str) -> Optional[MemoryEntry]:
        """从 JSON 文件加载单条记忆。"""
        filepath = self._get_filepath(memory_id)
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return MemoryEntry(**json.load(f))
        except Exception:
            return None

    def _get_filepath(self, memory_id: str) -> str:
        return os.path.join(self._storage_dir, f"{memory_id}.json")

    @property
    def count(self) -> int:
        """缓存中的记忆数。"""
        return len(self._cache)
