"""
VFS 存储后端 — 可插拔策略模式
================================

借鉴 agent_core/memory/graph_store.py 的 ABC + 工厂模式，
提供四种存储后端实现：

  - InMemoryBackend: 零依赖内存实现（测试/实验）
  - RedisBackend:    Redis Hash 高速缓存（生产缓存层）
  - MySQLBackend:    MySQL vfs_nodes 表持久化（生产持久化层）
  - CachedBackend:   Redis 前置 + MySQL 回源的组合后端
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.vfs.models import VFSNode


# ═══════════════════════════════════════════════════════════════════════════
# 抽象接口
# ═══════════════════════════════════════════════════════════════════════════

class VFSBackend(ABC):
    """VFS 存储后端抽象接口。

    所有后端（InMemory / Redis / MySQL）必须实现此接口。
    参考 agent_core/memory/graph_store.py 的 GraphStore 设计。
    """

    @abstractmethod
    async def get_node(self, uri: str) -> Optional[VFSNode]:
        """获取指定 URI 的节点。

        Args:
            uri: VFS URI。

        Returns:
            VFSNode 实例，不存在时返回 None。
        """
        ...

    @abstractmethod
    async def set_node(self, uri: str, node: VFSNode) -> None:
        """写入/更新节点。

        Args:
            uri: VFS URI。
            node: VFSNode 实例。
        """
        ...

    @abstractmethod
    async def delete_node(self, uri: str) -> bool:
        """删除指定 URI 的节点。

        Args:
            uri: VFS URI。

        Returns:
            是否成功删除（不存在时返回 False）。
        """
        ...

    @abstractmethod
    async def list_children(self, uri: str) -> List[str]:
        """列出目录下的子节点名称。

        Args:
            uri: 目录的 VFS URI。

        Returns:
            子节点名称列表。
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════
# InMemoryBackend — 零依赖内存实现
# ═══════════════════════════════════════════════════════════════════════════

class InMemoryBackend(VFSBackend):
    """基于内存 Dict 的 VFS 后端。

    适用于：单元测试、CLI 实验、Demo 演示。
    数据存储在 self._store[uri] = VFSNode 中。
    """

    def __init__(self) -> None:
        self._store: Dict[str, VFSNode] = {}

    async def get_node(self, uri: str) -> Optional[VFSNode]:
        """从内存获取节点。"""
        return self._store.get(uri)

    async def set_node(self, uri: str, node: VFSNode) -> None:
        """写入内存。"""
        self._store[uri] = node

    async def delete_node(self, uri: str) -> bool:
        """从内存删除。"""
        if uri in self._store:
            del self._store[uri]
            return True
        return False

    async def list_children(self, uri: str) -> List[str]:
        """列出目录子节点。"""
        node = self._store.get(uri)
        if node and node.node_type == "dir":
            return list(node.children)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# RedisBackend — 高速缓存实现
# ═══════════════════════════════════════════════════════════════════════════

class RedisBackend(VFSBackend):
    """基于 Redis Hash 的 VFS 后端。

    每个节点存储为一个 Redis key，值为 JSON 序列化的 VFSNode。
    key 格式: vfs:{uri}（如 vfs:vfs://story_001/chars/alice）。

    适用于：生产环境高速缓存层（搭配 MySQLBackend 使用）。

    Args:
        redis_client: redis.asyncio.Redis 实例（由调用方注入）。
        key_prefix: Redis key 前缀，默认 "vfs:"。
        ttl: 缓存过期时间（秒），默认 3600（1小时）。None 表示不过期。
    """

    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "vfs:",
        ttl: Optional[int] = 3600,
    ) -> None:
        self._redis = redis_client
        self._prefix = key_prefix
        self._ttl = ttl

    def _key(self, uri: str) -> str:
        """生成 Redis key。"""
        return f"{self._prefix}{uri}"

    async def get_node(self, uri: str) -> Optional[VFSNode]:
        """从 Redis 获取节点。"""
        raw = await self._redis.get(self._key(uri))
        if raw is None:
            return None
        try:
            return VFSNode.from_json(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"[RedisBackend] 反序列化失败 uri={uri}: {e}")
            return None

    async def set_node(self, uri: str, node: VFSNode) -> None:
        """写入 Redis（带可选 TTL）。"""
        key = self._key(uri)
        value = node.to_json()
        if self._ttl:
            await self._redis.set(key, value, ex=self._ttl)
        else:
            await self._redis.set(key, value)

    async def delete_node(self, uri: str) -> bool:
        """从 Redis 删除。"""
        result = await self._redis.delete(self._key(uri))
        return result > 0

    async def list_children(self, uri: str) -> List[str]:
        """从 Redis 获取目录子节点列表。"""
        node = await self.get_node(uri)
        if node and node.node_type == "dir":
            return list(node.children)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# MySQLBackend — 持久化实现
# ═══════════════════════════════════════════════════════════════════════════

class MySQLBackend(VFSBackend):
    """基于 MySQL vfs_nodes 表的 VFS 后端。

    表结构 (需要在数据库初始化时创建)::

        CREATE TABLE IF NOT EXISTS vfs_nodes (
            uri          VARCHAR(512) PRIMARY KEY,
            name         VARCHAR(256) NOT NULL,
            node_type    VARCHAR(16) NOT NULL DEFAULT 'file',
            content      LONGTEXT,
            abstract     TEXT,
            overview     TEXT,
            metadata     JSON,
            children     JSON,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_parent (uri(255))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

    Args:
        pool: aiomysql 连接池实例（由调用方注入）。
    """

    # 建表 DDL — 调用方可使用此常量自动建表
    CREATE_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS vfs_nodes (
        uri          VARCHAR(512) PRIMARY KEY COMMENT 'VFS URI，如 vfs://scope/path',
        name         VARCHAR(256) NOT NULL COMMENT '节点名称',
        node_type    VARCHAR(16) NOT NULL DEFAULT 'file' COMMENT '类型: file/dir',
        content      LONGTEXT COMMENT 'L2 全量内容',
        abstract     TEXT COMMENT 'L0 一句话摘要',
        overview     TEXT COMMENT 'L1 概览',
        metadata     JSON COMMENT '自定义元数据',
        children     JSON COMMENT '子节点名称列表(仅dir类型)',
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='VFS 虚拟文件系统节点表';
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get_node(self, uri: str) -> Optional[VFSNode]:
        """从 MySQL 查询节点。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT name, node_type, content, abstract, overview, metadata, children "
                    "FROM vfs_nodes WHERE uri = %s",
                    (uri,),
                )
                row = await cur.fetchone()
                if not row:
                    return None
                name, node_type, content, abstract, overview, meta_raw, children_raw = row
                # metadata/children 在 MySQL 中以 JSON 存储
                metadata = json.loads(meta_raw) if meta_raw else {}
                children = json.loads(children_raw) if children_raw else []
                return VFSNode(
                    name=name,
                    node_type=node_type,
                    content=content or "",
                    abstract=abstract or "",
                    overview=overview or "",
                    metadata=metadata,
                    children=children,
                )

    async def set_node(self, uri: str, node: VFSNode) -> None:
        """插入或更新 MySQL 中的节点（UPSERT 语义）。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO vfs_nodes (uri, name, node_type, content, abstract, overview, metadata, children)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        name = VALUES(name),
                        node_type = VALUES(node_type),
                        content = VALUES(content),
                        abstract = VALUES(abstract),
                        overview = VALUES(overview),
                        metadata = VALUES(metadata),
                        children = VALUES(children)
                    """,
                    (
                        uri,
                        node.name,
                        node.node_type,
                        node.content,
                        node.abstract,
                        node.overview,
                        json.dumps(node.metadata, ensure_ascii=False),
                        json.dumps(node.children, ensure_ascii=False),
                    ),
                )
                await conn.commit()

    async def delete_node(self, uri: str) -> bool:
        """从 MySQL 删除节点。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM vfs_nodes WHERE uri = %s", (uri,))
                affected = cur.rowcount
                await conn.commit()
                return affected > 0

    async def list_children(self, uri: str) -> List[str]:
        """从 MySQL 获取目录子节点列表。"""
        node = await self.get_node(uri)
        if node and node.node_type == "dir":
            return list(node.children)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# CachedBackend — Redis 缓存 + MySQL 回源组合
# ═══════════════════════════════════════════════════════════════════════════

class CachedBackend(VFSBackend):
    """Redis 缓存 + MySQL 持久化的组合后端。

    读取时：先查 Redis，miss 则查 MySQL 并回填 Redis。
    写入时：同时写入 MySQL 和 Redis。
    删除时：同时从 MySQL 和 Redis 删除。

    Args:
        cache: RedisBackend 实例。
        persistent: MySQLBackend 实例。
    """

    def __init__(self, cache: RedisBackend, persistent: MySQLBackend) -> None:
        self._cache = cache
        self._persistent = persistent

    async def get_node(self, uri: str) -> Optional[VFSNode]:
        """读取：先查缓存，miss 则回源持久化层。"""
        # 先查 Redis
        node = await self._cache.get_node(uri)
        if node is not None:
            return node
        # Cache miss → 查 MySQL
        node = await self._persistent.get_node(uri)
        if node is not None:
            # 回填 Redis 缓存
            await self._cache.set_node(uri, node)
            logger.debug(f"[CachedBackend] Cache miss 回填: {uri}")
        return node

    async def set_node(self, uri: str, node: VFSNode) -> None:
        """写入：同时写 MySQL 持久化 + Redis 缓存。"""
        await self._persistent.set_node(uri, node)
        await self._cache.set_node(uri, node)

    async def delete_node(self, uri: str) -> bool:
        """删除：同时从 MySQL 和 Redis 删除。"""
        # 先删持久化，再删缓存
        persistent_ok = await self._persistent.delete_node(uri)
        await self._cache.delete_node(uri)
        return persistent_ok

    async def list_children(self, uri: str) -> List[str]:
        """列出子节点：走 get_node 逻辑（含缓存）。"""
        node = await self.get_node(uri)
        if node and node.node_type == "dir":
            return list(node.children)
        return []
