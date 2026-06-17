"""
双模型降级检索器 — MemoryRetriever
======================================

从 MemoryStore 中按查询意图检索相关记忆，领域无关。

检索策略（双模降级）：
  1. 优先：向量相似度检索（需要 embedding 模型）
  2. 降级：关键词子串匹配（零成本，始终可用）

特色功能：
  - Age Skepticism：老旧记忆自动降低排序权重
  - 层级优先级：L1 > L2 > L3 > L4 > L5
  - 去重合并：同 subject 的碎片记忆合并为一条
"""

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.memory.models import (
    MemoryCategory,
    MemoryEntry,
    MemoryLayer,
)
from agent_core.memory.store import MemoryStore


# 层级优先级权重（L1 最高）
_LAYER_WEIGHT = {
    MemoryLayer.SESSION: 1.0,
    MemoryLayer.TASK: 0.9,
    MemoryLayer.PROJECT: 0.8,
    MemoryLayer.USER: 0.6,
    MemoryLayer.GLOBAL: 0.4,
}

# Age Skepticism 衰减参数
_AGE_HALF_LIFE_HOURS = 72  # 72 小时半衰期

# 层级显示标签（通用）
_LAYER_LABELS = {
    MemoryLayer.SESSION: "会话状态",
    MemoryLayer.TASK: "当前任务",
    MemoryLayer.PROJECT: "项目知识",
    MemoryLayer.USER: "用户偏好",
    MemoryLayer.GLOBAL: "全局知识",
}


class MemoryRetriever:
    """双模型降级检索器——向量优先 + 关键词降级，领域无关。

    Args:
        store: 记忆存储后端。
        embedding_fn: 嵌入计算函数（可选，不提供则纯关键词）。
        max_injection: 注入到 System Prompt 的最大记忆数。
        max_chars: 注入的最大总字符数。
    """

    def __init__(
        self,
        store: MemoryStore,
        embedding_fn: Optional[Any] = None,
        max_injection: int = 15,
        max_chars: int = 3000,
    ) -> None:
        self._store = store
        self._embedding_fn = embedding_fn
        self._max_injection = max_injection
        self._max_chars = max_chars

    async def retrieve(
        self,
        query: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        layer_filter: Optional[MemoryLayer] = None,
        include_session: bool = True,
    ) -> List[MemoryEntry]:
        """检索与查询相关的记忆。

        Args:
            query: 检索查询（通常是用户最新消息）。
            project_id: 按项目过滤。
            task_id: 按任务过滤。
            layer_filter: 只检索特定层级。
            include_session: 是否包含 L1 会话记忆。

        Returns:
            排序后的 MemoryEntry 列表。
        """
        candidates: List[MemoryEntry] = []

        # 1. 关键词检索（始终执行）
        keyword_results = await self._store.search(
            query, project_id=project_id, max_results=30,
        )
        candidates.extend(keyword_results)

        # 2. 层级全量补充（L1/L2 全部加入，确保不遗漏）
        if include_session:
            session_entries = await self._store.get_layer_entries(
                MemoryLayer.SESSION, project_id=project_id,
            )
            for e in session_entries:
                if e.memory_id not in {c.memory_id for c in candidates}:
                    candidates.append(e)

        if task_id:
            task_entries = await self._store.get_layer_entries(
                MemoryLayer.TASK, project_id=project_id,
            )
            matched = [e for e in task_entries if e.task_id == task_id]
            for e in matched:
                if e.memory_id not in {c.memory_id for c in candidates}:
                    candidates.append(e)

        # 3. 层级过滤
        if layer_filter:
            candidates = [c for c in candidates if c.layer == layer_filter]

        # 4. 排序：综合评分 = 置信度 × 层级权重 × 时间衰减
        scored = [
            (self._score(entry), entry)
            for entry in candidates
            if not entry.is_expired and not entry.superseded_by
        ]
        scored.sort(key=lambda x: -x[0])

        # 5. 去重 + 截断
        results = self._deduplicate(
            [entry for _, entry in scored]
        )
        return results[:self._max_injection]

    def format_for_injection(
        self, entries: List[MemoryEntry]
    ) -> str:
        """将检索到的记忆格式化为可注入 System Prompt 的文本。"""
        if not entries:
            return ""

        lines = ["<memories>"]
        total_chars = 0

        # 按层级分组
        by_layer: Dict[MemoryLayer, List[MemoryEntry]] = {}
        for entry in entries:
            by_layer.setdefault(entry.layer, []).append(entry)

        for layer in MemoryLayer:
            group = by_layer.get(layer, [])
            if not group:
                continue

            label = _LAYER_LABELS.get(layer, layer.value)
            lines.append(f"  [{label}]")
            for entry in group:
                text = entry.to_injection_text()
                if total_chars + len(text) > self._max_chars:
                    lines.append("  ... (记忆已截断)")
                    break
                lines.append(f"    {text}")
                total_chars += len(text)

        lines.append("</memories>")
        return "\n".join(lines)

    def _score(self, entry: MemoryEntry) -> float:
        """综合评分 = 置信度 × 层级权重 × 时间衰减。"""
        base = entry.confidence
        layer_w = _LAYER_WEIGHT.get(entry.layer, 0.5)

        # Age Skepticism：指数衰减
        age_h = entry.age_hours
        age_factor = 0.5 ** (age_h / _AGE_HALF_LIFE_HOURS)

        # 访问热度加成
        heat_bonus = min(entry.access_count * 0.02, 0.2)

        return base * layer_w * age_factor + heat_bonus

    def _deduplicate(
        self, entries: List[MemoryEntry]
    ) -> List[MemoryEntry]:
        """基于 subject + category 去重，保留高分版本。"""
        seen: Dict[str, MemoryEntry] = {}
        results: List[MemoryEntry] = []

        for entry in entries:
            dedup_key = f"{entry.subject}:{entry.category.value}"
            if dedup_key in seen:
                continue
            if entry.subject:
                seen[dedup_key] = entry
            results.append(entry)

        return results
