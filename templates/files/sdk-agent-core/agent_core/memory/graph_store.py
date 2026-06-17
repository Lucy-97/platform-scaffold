"""
GraphStore 抽象接口 + InMemory 实现 — graph_store.py
=====================================================

图谱存储的 Strategy 模式核心入口。

职责：
  1. 定义 GraphStore 抽象基类 — 所有后端（InMemory / Neo4j / ArangoDB）
     必须实现的异步合约
  2. 提供 InMemoryGraphStore — 零依赖内存实现，用于开发调试和测试
  3. 提供 create_graph_store() 工厂函数 — 根据环境变量自动选择后端

设计决策：
  将 GraphStore 接口独立到此文件是为了彻底消除 neo4j_store ↔ graph_rag
  之间的循环 import 依赖。所有上层业务代码只依赖此文件中的 GraphStore 类型。
"""

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# GraphStore 抽象基类（Strategy 模式）
# ---------------------------------------------------------------------------

class GraphStore(ABC):
    """图谱存储的抽象接口。

    所有具体存储后端（InMemory / Neo4j / ArangoDB）均实现此接口。
    上层业务代码（knowledge_graph / tool_registry）通过此接口操作图谱，
    不感知底层存储细节。
    """

    @abstractmethod
    async def upsert_entity(
        self,
        graph_id: str,
        name: str,
        entity_type: str,
        description: str = "",
        attributes: Optional[Dict[str, Any]] = None,
        source: str = "",
    ) -> bool:
        """创建或更新实体节点（MERGE 幂等语义）。

        Args:
            graph_id: 图谱标识（多图谱隔离键）。
            name: 实体名称（同一 graph_id 下唯一）。
            entity_type: 实体类型（如 character / location / item）。
            description: 实体描述。
            attributes: 附加属性字典。
            source: 来源标识（从哪段文本中提取）。

        Returns:
            是否成功。
        """
        ...

    @abstractmethod
    async def upsert_relation(
        self,
        graph_id: str,
        source: str,
        target: str,
        relation_type: str,
        description: str = "",
        weight: float = 1.0,
        bidirectional: bool = False,
    ) -> bool:
        """创建或更新关系边（MERGE 幂等语义）。

        Args:
            graph_id: 图谱标识。
            source: 源实体名称。
            target: 目标实体名称。
            relation_type: 关系类型（如 敌对 / 师徒 / 位于）。
            description: 关系描述。
            weight: 关系权重 (0.0-1.0)。
            bidirectional: 是否双向关系。

        Returns:
            是否成功。
        """
        ...

    @abstractmethod
    async def get_neighbors(
        self,
        graph_id: str,
        entity_name: str,
    ) -> List[Dict[str, Any]]:
        """获取实体的所有邻居。

        Returns:
            邻居信息列表 [{name, entity_type, relation_type, direction}]。
        """
        ...

    @abstractmethod
    async def get_subgraph(
        self,
        graph_id: str,
        center: str,
        depth: int = 1,
    ) -> Dict[str, Any]:
        """以指定实体为中心获取子图。

        Returns:
            {entities: {...}, relations: [...]} 字典。
        """
        ...

    @abstractmethod
    async def get_all(
        self,
        graph_id: str,
    ) -> Dict[str, Any]:
        """获取图谱中所有实体和关系。

        Returns:
            {entities: {...}, relations: [...], stats: {...}} 字典。
        """
        ...

    @abstractmethod
    async def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """按名称或描述搜索实体。

        Returns:
            匹配的实体列表。
        """
        ...

    @abstractmethod
    async def clear(self, graph_id: str) -> int:
        """清空指定图谱的所有数据。

        Returns:
            删除的节点数。
        """
        ...


# ---------------------------------------------------------------------------
# InMemoryGraphStore — 零依赖内存实现
# ---------------------------------------------------------------------------

class InMemoryGraphStore(GraphStore):
    """基于内存字典的图谱存储实现。

    使用嵌套字典模拟图谱数据结构，无需外部数据库依赖。
    适用于：开发调试、单元测试、Demo 演示。

    内部数据结构::

        _entities[graph_id][name] = {name, entity_type, description, attributes, source}
        _relations[graph_id] = [{source, target, relation_type, description, weight, bidirectional}]
    """

    def __init__(self) -> None:
        # graph_id → {entity_name → entity_dict}
        self._entities: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # graph_id → [relation_dict]
        self._relations: Dict[str, List[Dict[str, Any]]] = {}

    def _ensure_graph(self, graph_id: str) -> None:
        """确保 graph_id 对应的容器已初始化。"""
        if graph_id not in self._entities:
            self._entities[graph_id] = {}
            self._relations[graph_id] = []

    async def upsert_entity(
        self, graph_id: str, name: str, entity_type: str,
        description: str = "", attributes: Optional[Dict[str, Any]] = None,
        source: str = "",
    ) -> bool:
        """内存 MERGE 语义：同名实体合并属性，新实体直接插入。"""
        self._ensure_graph(graph_id)
        existing = self._entities[graph_id].get(name)
        if existing:
            # 合并：新属性覆盖旧属性，非空描述覆盖
            merged_attrs = {**(existing.get("attributes") or {}), **(attributes or {})}
            if description:
                existing["description"] = description
            existing["attributes"] = merged_attrs
            existing["entity_type"] = entity_type
            if source:
                existing["source"] = source
            logger.debug(f"[InMemoryGraphStore] Merged entity: {name}")
        else:
            self._entities[graph_id][name] = {
                "name": name,
                "entity_type": entity_type,
                "description": description,
                "attributes": attributes or {},
                "source": source,
            }
            logger.debug(f"[InMemoryGraphStore] Added entity: {name} [{entity_type}]")
        return True

    async def upsert_relation(
        self, graph_id: str, source: str, target: str, relation_type: str,
        description: str = "", weight: float = 1.0, bidirectional: bool = False,
    ) -> bool:
        """内存 MERGE 语义：自动创建缺失的端点实体。"""
        self._ensure_graph(graph_id)

        # 自动创建缺失的端点实体（与 Neo4j 行为一致）
        if source not in self._entities[graph_id]:
            await self.upsert_entity(graph_id, source, "concept")
        if target not in self._entities[graph_id]:
            await self.upsert_entity(graph_id, target, "concept")

        # 检查是否已有同源同目标同类型的关系（幂等更新）
        for rel in self._relations[graph_id]:
            if (rel["source"] == source and rel["target"] == target
                    and rel["relation_type"] == relation_type):
                rel["description"] = description
                rel["weight"] = weight
                rel["bidirectional"] = bidirectional
                return True

        self._relations[graph_id].append({
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "description": description,
            "weight": weight,
            "bidirectional": bidirectional,
        })
        logger.debug(
            f"[InMemoryGraphStore] Added relation: {source} "
            f"--[{relation_type}]--> {target}"
        )
        return True

    async def get_neighbors(
        self, graph_id: str, entity_name: str,
    ) -> List[Dict[str, Any]]:
        """遍历关系列表查找邻居。"""
        self._ensure_graph(graph_id)
        neighbors = []
        for rel in self._relations[graph_id]:
            if rel["source"] == entity_name:
                entity = self._entities[graph_id].get(rel["target"], {})
                neighbors.append({
                    "name": rel["target"],
                    "entity_type": entity.get("entity_type", "concept"),
                    "description": entity.get("description", ""),
                    "relation_type": rel["relation_type"],
                    "rel_description": rel.get("description", ""),
                    "direction": "outgoing",
                })
            elif rel["target"] == entity_name:
                entity = self._entities[graph_id].get(rel["source"], {})
                neighbors.append({
                    "name": rel["source"],
                    "entity_type": entity.get("entity_type", "concept"),
                    "description": entity.get("description", ""),
                    "relation_type": rel["relation_type"],
                    "rel_description": rel.get("description", ""),
                    "direction": "incoming",
                })
        return neighbors

    async def get_subgraph(
        self, graph_id: str, center: str, depth: int = 1,
    ) -> Dict[str, Any]:
        """BFS 遍历到指定深度，返回子图。"""
        self._ensure_graph(graph_id)
        if center not in self._entities[graph_id]:
            return {"entities": {}, "relations": []}

        # BFS 收集所有在 depth 范围内的实体名
        visited = {center}
        frontier = {center}
        for _ in range(depth):
            next_frontier = set()
            for name in frontier:
                for rel in self._relations[graph_id]:
                    if rel["source"] == name and rel["target"] not in visited:
                        visited.add(rel["target"])
                        next_frontier.add(rel["target"])
                    elif rel["target"] == name and rel["source"] not in visited:
                        visited.add(rel["source"])
                        next_frontier.add(rel["source"])
            frontier = next_frontier

        # 收集子图实体和关系
        sub_entities = {
            name: self._entities[graph_id][name]
            for name in visited
            if name in self._entities[graph_id]
        }
        sub_relations = [
            rel for rel in self._relations[graph_id]
            if rel["source"] in visited and rel["target"] in visited
        ]
        return {"entities": sub_entities, "relations": sub_relations}

    async def get_all(self, graph_id: str) -> Dict[str, Any]:
        """返回完整图谱数据及统计信息。"""
        self._ensure_graph(graph_id)
        entities = dict(self._entities[graph_id])
        relations = list(self._relations[graph_id])

        # 统计各类型实体数量
        type_counts: Dict[str, int] = {}
        for e in entities.values():
            t = e.get("entity_type", "concept")
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "entities": entities,
            "relations": relations,
            "stats": {
                "total_entities": len(entities),
                "total_relations": len(relations),
                "entity_types": type_counts,
            },
        }

    async def search(
        self, graph_id: str, query: str, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """CONTAINS 模糊搜索（名称 + 描述）。"""
        self._ensure_graph(graph_id)
        results = []
        for name, entity in self._entities[graph_id].items():
            desc = entity.get("description", "")
            if query in name or query in desc:
                results.append({
                    "name": name,
                    "entity_type": entity.get("entity_type", "concept"),
                    "description": desc,
                    "attributes": entity.get("attributes", {}),
                })
                if len(results) >= limit:
                    break
        return results

    async def clear(self, graph_id: str) -> int:
        """清空指定图谱的所有数据。"""
        count = len(self._entities.get(graph_id, {}))
        self._entities.pop(graph_id, None)
        self._relations.pop(graph_id, None)
        logger.info(f"[InMemoryGraphStore] Cleared graph {graph_id}: {count} nodes deleted")
        return count


# ---------------------------------------------------------------------------
# 工厂函数 — 根据环境变量选择后端
# ---------------------------------------------------------------------------

def create_graph_store() -> GraphStore:
    """根据环境变量自动选择图谱存储后端。

    环境变量 NEO4J_URI 存在时使用 Neo4j，否则使用内存模式。

    Returns:
        GraphStore 实例。
    """
    neo4j_uri = os.getenv("NEO4J_URI", "")
    if neo4j_uri:
        logger.info(f"[GraphStore] Using Neo4j backend: {neo4j_uri}")
        # 延迟导入 Neo4j 实现，避免未安装 neo4j 驱动时报错
        from agent_core.memory.neo4j_store import Neo4jGraphStore
        return Neo4jGraphStore(uri=neo4j_uri)
    else:
        logger.info("[GraphStore] Using in-memory backend (NEO4J_URI not set)")
        return InMemoryGraphStore()


# ---------------------------------------------------------------------------
# 全局单例 — 懒初始化
# ---------------------------------------------------------------------------

_graph_store: Optional[GraphStore] = None


def get_graph_store() -> GraphStore:
    """获取全局 GraphStore 单例（懒初始化）。

    首次调用时根据 NEO4J_URI 环境变量决定后端。
    后续调用直接返回缓存的实例。

    Returns:
        全局唯一的 GraphStore 实例。
    """
    global _graph_store
    if _graph_store is None:
        _graph_store = create_graph_store()
    return _graph_store
