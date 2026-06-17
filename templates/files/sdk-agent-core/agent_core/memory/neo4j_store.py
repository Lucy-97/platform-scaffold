"""
Neo4j 图谱存储适配器 — neo4j_store.py
=========================================

基于 Neo4j 异步驱动的 GraphStore 实现。
使用 Cypher 查询完成所有图谱增删查操作。

Neo4j 数据模型映射::

    Entity  → 节点 (label = GraphEntity, properties = graph_id/name/entity_type/description/attributes)
    Relation → 关系 (type = relation_type, properties = description/weight)

节点标签策略：
  - 每个节点统一使用 `GraphEntity` 标签
  - `graph_id` 属性实现多图谱隔离（不同项目/会话的数据互不干扰）

环境变量::

    NEO4J_URI      — Bolt 连接地址 (默认 bolt://localhost:7687)
    NEO4J_USER     — 用户名 (默认 neo4j)
    NEO4J_PASSWORD — 密码 (默认 agent_neo4j_dev)
"""

import json
import os
from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.memory.graph_store import GraphStore


class Neo4jGraphStore(GraphStore):
    """基于 Neo4j 的图谱存储实现。

    使用 Neo4j 官方异步驱动，通过 Cypher 查询实现所有图谱操作。
    驱动采用懒初始化 + 连接池复用策略，避免启动时阻塞。

    Args:
        uri: Neo4j Bolt 连接地址。
        user: 认证用户名。
        password: 认证密码。
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self._uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self._user = user or os.getenv("NEO4J_USER", "neo4j")
        self._password = password or os.getenv("NEO4J_PASSWORD", "agent_neo4j_dev")
        self._driver = None

    async def _get_driver(self):
        """懒初始化 Neo4j 异步驱动（连接池复用）。"""
        if self._driver is None:
            try:
                from neo4j import AsyncGraphDatabase
                self._driver = AsyncGraphDatabase.driver(
                    self._uri,
                    auth=(self._user, self._password),
                )
                # 验证连接可用性
                await self._driver.verify_connectivity()
                logger.info(
                    f"[Neo4jGraphStore] Connected to {self._uri}"
                )
                # 首次连接时创建索引
                await self._ensure_indexes()
            except Exception as e:
                logger.error(f"[Neo4jGraphStore] Connection failed: {e}")
                raise
        return self._driver

    async def _ensure_indexes(self) -> None:
        """创建 Neo4j 索引以加速查询。"""
        driver = self._driver
        async with driver.session() as session:
            # 实体名称+图谱ID 复合索引
            await session.run(
                "CREATE INDEX entity_graph_idx IF NOT EXISTS "
                "FOR (n:GraphEntity) ON (n.graph_id, n.name)"
            )
            # 全文搜索索引（名称+描述），Community 版本可能不支持
            try:
                await session.run(
                    "CREATE FULLTEXT INDEX entity_fulltext_idx IF NOT EXISTS "
                    "FOR (n:GraphEntity) ON EACH [n.name, n.description]"
                )
            except Exception:
                # 全文索引可能已存在或 Community 版本不支持
                pass

            logger.debug("[Neo4jGraphStore] Indexes ensured")

    async def close(self) -> None:
        """关闭 Neo4j 驱动（释放连接池）。"""
        if self._driver:
            await self._driver.close()
            self._driver = None
            logger.info("[Neo4jGraphStore] Driver closed")

    # -----------------------------------------------------------------------
    # GraphStore 接口实现
    # -----------------------------------------------------------------------

    async def upsert_entity(
        self,
        graph_id: str,
        name: str,
        entity_type: str,
        description: str = "",
        attributes: Optional[Dict[str, Any]] = None,
        source: str = "",
    ) -> bool:
        """MERGE 语义创建/更新节点。"""
        driver = await self._get_driver()
        attrs_json = json.dumps(attributes or {}, ensure_ascii=False)

        async with driver.session() as session:
            await session.run(
                """
                MERGE (n:GraphEntity {graph_id: $graph_id, name: $name})
                SET n.entity_type = $entity_type,
                    n.description = CASE WHEN $description <> '' THEN $description ELSE n.description END,
                    n.attributes = $attributes,
                    n.source = CASE WHEN $source <> '' THEN $source ELSE n.source END,
                    n.updated_at = datetime()
                """,
                graph_id=graph_id,
                name=name,
                entity_type=entity_type,
                description=description,
                attributes=attrs_json,
                source=source,
            )

        logger.debug(f"[Neo4jGraphStore] Upserted entity: {name} [{entity_type}]")
        return True

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
        """MERGE 语义创建/更新关系。如果源/目标节点不存在则自动创建。"""
        driver = await self._get_driver()
        # 关系类型需要是合法的 Neo4j 标识符，替换空格和特殊字符
        safe_rel_type = relation_type.replace(" ", "_").replace("-", "_").upper()
        if not safe_rel_type:
            safe_rel_type = "RELATED_TO"

        async with driver.session() as session:
            await session.run(
                f"""
                MERGE (a:GraphEntity {{graph_id: $graph_id, name: $source}})
                ON CREATE SET a.entity_type = 'concept', a.updated_at = datetime()
                MERGE (b:GraphEntity {{graph_id: $graph_id, name: $target}})
                ON CREATE SET b.entity_type = 'concept', b.updated_at = datetime()
                MERGE (a)-[r:{safe_rel_type}]->(b)
                SET r.description = $description,
                    r.weight = $weight,
                    r.bidirectional = $bidirectional,
                    r.relation_type = $relation_type,
                    r.graph_id = $graph_id,
                    r.updated_at = datetime()
                """,
                graph_id=graph_id,
                source=source,
                target=target,
                relation_type=relation_type,
                description=description,
                weight=weight,
                bidirectional=bidirectional,
            )

        logger.debug(
            f"[Neo4jGraphStore] Upserted relation: {source} --[{relation_type}]--> {target}"
        )
        return True

    async def get_neighbors(
        self,
        graph_id: str,
        entity_name: str,
    ) -> List[Dict[str, Any]]:
        """Cypher 查询实体的所有邻居。"""
        driver = await self._get_driver()
        neighbors = []

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (a:GraphEntity {graph_id: $graph_id, name: $name})-[r]-(b:GraphEntity)
                WHERE b.graph_id = $graph_id
                RETURN b.name AS name,
                       b.entity_type AS entity_type,
                       b.description AS description,
                       r.relation_type AS relation_type,
                       r.description AS rel_description,
                       CASE WHEN startNode(r) = a THEN 'outgoing' ELSE 'incoming' END AS direction
                """,
                graph_id=graph_id,
                name=entity_name,
            )
            async for record in result:
                neighbors.append({
                    "name": record["name"],
                    "entity_type": record["entity_type"],
                    "description": record["description"],
                    "relation_type": record["relation_type"],
                    "rel_description": record["rel_description"],
                    "direction": record["direction"],
                })

        return neighbors

    async def get_subgraph(
        self,
        graph_id: str,
        center: str,
        depth: int = 1,
    ) -> Dict[str, Any]:
        """变长路径获取子图。"""
        driver = await self._get_driver()
        entities = {}
        relations = []

        async with driver.session() as session:
            # 使用变长路径一次性获取 depth 深度内的所有节点和关系
            result = await session.run(
                f"""
                MATCH path = (center:GraphEntity {{graph_id: $graph_id, name: $center}})
                             -[*1..{depth}]-(neighbor:GraphEntity)
                WHERE neighbor.graph_id = $graph_id
                WITH nodes(path) AS ns, relationships(path) AS rs
                UNWIND ns AS n
                WITH COLLECT(DISTINCT n) AS all_nodes, rs
                UNWIND all_nodes AS node
                RETURN DISTINCT node.name AS name,
                       node.entity_type AS entity_type,
                       node.description AS description,
                       node.attributes AS attributes
                """,
                graph_id=graph_id,
                center=center,
            )
            async for record in result:
                entities[record["name"]] = {
                    "name": record["name"],
                    "entity_type": record["entity_type"],
                    "description": record["description"] or "",
                    "attributes": json.loads(record["attributes"] or "{}"),
                }

            # 获取子图内的关系
            if entities:
                entity_names = list(entities.keys())
                rel_result = await session.run(
                    """
                    MATCH (a:GraphEntity {graph_id: $graph_id})-[r]->(b:GraphEntity {graph_id: $graph_id})
                    WHERE a.name IN $names AND b.name IN $names
                    RETURN a.name AS source, b.name AS target,
                           r.relation_type AS relation_type,
                           r.description AS description,
                           r.weight AS weight,
                           r.bidirectional AS bidirectional
                    """,
                    graph_id=graph_id,
                    names=entity_names,
                )
                async for record in rel_result:
                    relations.append({
                        "source": record["source"],
                        "target": record["target"],
                        "relation_type": record["relation_type"] or "",
                        "description": record["description"] or "",
                        "weight": record["weight"] or 1.0,
                        "bidirectional": record["bidirectional"] or False,
                    })

        return {"entities": entities, "relations": relations}

    async def get_all(
        self,
        graph_id: str,
    ) -> Dict[str, Any]:
        """获取图谱中所有实体和关系。"""
        driver = await self._get_driver()
        entities = {}
        relations = []

        async with driver.session() as session:
            # 所有节点
            node_result = await session.run(
                """
                MATCH (n:GraphEntity {graph_id: $graph_id})
                RETURN n.name AS name, n.entity_type AS entity_type,
                       n.description AS description, n.attributes AS attributes,
                       n.source AS source
                """,
                graph_id=graph_id,
            )
            async for record in node_result:
                entities[record["name"]] = {
                    "name": record["name"],
                    "entity_type": record["entity_type"],
                    "description": record["description"] or "",
                    "attributes": json.loads(record["attributes"] or "{}"),
                    "source": record["source"] or "",
                }

            # 所有关系
            rel_result = await session.run(
                """
                MATCH (a:GraphEntity {graph_id: $graph_id})-[r]->(b:GraphEntity {graph_id: $graph_id})
                RETURN a.name AS source, b.name AS target,
                       r.relation_type AS relation_type,
                       r.description AS description,
                       r.weight AS weight,
                       r.bidirectional AS bidirectional
                """,
                graph_id=graph_id,
            )
            async for record in rel_result:
                relations.append({
                    "source": record["source"],
                    "target": record["target"],
                    "relation_type": record["relation_type"] or "",
                    "description": record["description"] or "",
                    "weight": record["weight"] or 1.0,
                    "bidirectional": record["bidirectional"] or False,
                })

        # 统计
        type_counts: Dict[str, int] = {}
        for e in entities.values():
            t = e["entity_type"]
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
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """搜索实体（模糊匹配名称和描述）。"""
        driver = await self._get_driver()
        results = []

        async with driver.session() as session:
            # 使用 CONTAINS 进行模糊匹配（全文索引在 Community 版可能不可用）
            result = await session.run(
                """
                MATCH (n:GraphEntity {graph_id: $graph_id})
                WHERE n.name CONTAINS $query OR n.description CONTAINS $query
                RETURN n.name AS name, n.entity_type AS entity_type,
                       n.description AS description, n.attributes AS attributes
                LIMIT $limit
                """,
                graph_id=graph_id,
                query=query,
                limit=limit,
            )
            async for record in result:
                results.append({
                    "name": record["name"],
                    "entity_type": record["entity_type"],
                    "description": record["description"] or "",
                    "attributes": json.loads(record["attributes"] or "{}"),
                })

        return results

    async def clear(self, graph_id: str) -> int:
        """删除指定图谱的所有数据。"""
        driver = await self._get_driver()

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (n:GraphEntity {graph_id: $graph_id})
                DETACH DELETE n
                RETURN count(n) AS deleted
                """,
                graph_id=graph_id,
            )
            record = await result.single()
            deleted = record["deleted"] if record else 0

        logger.info(f"[Neo4jGraphStore] Cleared graph {graph_id}: {deleted} nodes deleted")
        return deleted
