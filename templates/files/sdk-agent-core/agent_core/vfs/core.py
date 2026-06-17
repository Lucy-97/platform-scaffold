"""
VFS 主类 — 虚拟文件系统对外统一 API
=====================================

Agent 的上下文管理核心入口。
提供文件系统风格的 API（mount / ls / read / tree 等），
Agent 通过 Tool-Calling 调用这些操作按需获取上下文，
替代传统的全量 Prompt 注入方式。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.vfs.backends import VFSBackend
from agent_core.vfs.models import ContextLayer, VFSNode, build_uri, parse_uri, parent_uri


class VFS:
    """虚拟文件系统 — Agent 的上下文管理层。

    Args:
        backend: VFSBackend 实例（InMemory / Redis / MySQL / CachedBackend）。
    """

    def __init__(self, backend: VFSBackend) -> None:
        self._backend = backend

    async def mount(
        self,
        uri: str,
        content: str = "",
        abstract: str = "",
        overview: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """挂载节点到 VFS（自动创建父目录）。

        Args:
            uri: VFS URI（如 "vfs://story_001/chars/alice"）。
            content: L2 全量内容。
            abstract: L0 一句话摘要。
            overview: L1 概览。
            metadata: 自定义元数据。
        """
        segments = parse_uri(uri)
        if not segments:
            raise ValueError(f"URI 路径不能为空: {uri}")

        # 自动创建沿途所有父目录
        for i in range(1, len(segments)):
            parent_segs = segments[:i]
            parent = build_uri(parent_segs)
            existing = await self._backend.get_node(parent)
            if existing is None:
                # 创建中间目录节点
                dir_node = VFSNode(
                    name=parent_segs[-1],
                    node_type="dir",
                    children=[],
                )
                await self._backend.set_node(parent, dir_node)
                logger.debug(f"[VFS] 自动创建目录: {parent}")

            # 将当前段注册为父目录的子节点
            child_name = segments[i]
            dir_node = await self._backend.get_node(parent)
            if dir_node and child_name not in dir_node.children:
                dir_node.children.append(child_name)
                await self._backend.set_node(parent, dir_node)

        # 写入叶节点
        node = VFSNode(
            name=segments[-1],
            node_type="file",
            content=content,
            abstract=abstract,
            overview=overview,
            metadata=metadata or {},
        )
        await self._backend.set_node(uri, node)
        logger.debug(f"[VFS] 挂载文件: {uri} | abstract='{abstract[:50]}...'")

    async def mount_dir(
        self,
        uri: str,
        abstract: str = "",
        overview: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """显式创建目录节点（含 L0/L1 摘要）。

        Args:
            uri: 目录 URI（如 "vfs://story_001/chars/"）。
            abstract: L0 摘要。
            overview: L1 概览。
            metadata: 自定义元数据。
        """
        segments = parse_uri(uri)
        if not segments:
            raise ValueError(f"URI 路径不能为空: {uri}")

        # 自动创建沿途父目录
        for i in range(1, len(segments)):
            parent_segs = segments[:i]
            parent = build_uri(parent_segs)
            existing = await self._backend.get_node(parent)
            if existing is None:
                dir_node = VFSNode(name=parent_segs[-1], node_type="dir", children=[])
                await self._backend.set_node(parent, dir_node)

            child_name = segments[i]
            dir_node = await self._backend.get_node(parent)
            if dir_node and child_name not in dir_node.children:
                dir_node.children.append(child_name)
                await self._backend.set_node(parent, dir_node)

        # 写入目录节点
        existing = await self._backend.get_node(uri)
        node = VFSNode(
            name=segments[-1],
            node_type="dir",
            abstract=abstract,
            overview=overview,
            metadata=metadata or {},
            children=existing.children if existing else [],
        )
        await self._backend.set_node(uri, node)
        logger.debug(f"[VFS] 创建目录: {uri}")

    async def ls(self, uri: str) -> List[Dict[str, Any]]:
        """列出目录内容（附带每个子节点的 L0 摘要）。

        Args:
            uri: 目录 URI。

        Returns:
            子节点信息列表 [{name, type, abstract}]。
        """
        node = await self._backend.get_node(uri)
        if node is None:
            raise FileNotFoundError(f"路径不存在: {uri}")
        if node.node_type != "dir":
            raise NotADirectoryError(f"不是目录: {uri}")

        entries = []
        for child_name in node.children:
            child_uri = f"{uri.rstrip('/')}/{child_name}"
            child_node = await self._backend.get_node(child_uri)
            if child_node:
                entries.append({
                    "name": child_node.name,
                    "type": child_node.node_type,
                    "abstract": child_node.abstract,
                    "uri": child_uri,
                })
            else:
                # 子节点记录存在但实际节点缺失（脏数据兜底）
                entries.append({
                    "name": child_name,
                    "type": "unknown",
                    "abstract": "",
                    "uri": child_uri,
                })
        return entries

    async def read(self, uri: str, layer: ContextLayer = ContextLayer.L2_DETAIL) -> str:
        """读取节点内容（支持按层级选择分辨率）。

        Args:
            uri: 文件或目录 URI。
            layer: 读取层级（默认 L2 全文）。

        Returns:
            对应层级的文本内容。
        """
        node = await self._backend.get_node(uri)
        if node is None:
            raise FileNotFoundError(f"路径不存在: {uri}")

        if layer == ContextLayer.L0_ABSTRACT:
            return node.abstract
        elif layer == ContextLayer.L1_OVERVIEW:
            return node.overview
        else:
            return node.content

    async def abstract(self, uri: str) -> str:
        """读取 L0 摘要（快捷方法）。"""
        return await self.read(uri, ContextLayer.L0_ABSTRACT)

    async def overview(self, uri: str) -> str:
        """读取 L1 概览（快捷方法）。"""
        return await self.read(uri, ContextLayer.L1_OVERVIEW)

    async def tree(
        self,
        uri: str,
        max_depth: int = 3,
        _current_depth: int = 0,
    ) -> List[Dict[str, Any]]:
        """递归遍历目录树（输出每个节点的 name + type + abstract + 层级缩进）。

        Args:
            uri: 起始目录 URI。
            max_depth: 最大遍历深度。

        Returns:
            扁平化的节点列表 [{name, type, abstract, uri, depth}]。
        """
        node = await self._backend.get_node(uri)
        if node is None:
            return []

        result: List[Dict[str, Any]] = [{
            "name": node.name,
            "type": node.node_type,
            "abstract": node.abstract,
            "uri": uri,
            "depth": _current_depth,
        }]

        if node.node_type == "dir" and _current_depth < max_depth:
            for child_name in node.children:
                child_uri = f"{uri.rstrip('/')}/{child_name}"
                child_entries = await self.tree(
                    child_uri,
                    max_depth=max_depth,
                    _current_depth=_current_depth + 1,
                )
                result.extend(child_entries)

        return result

    async def exists(self, uri: str) -> bool:
        """检查 URI 是否存在。"""
        node = await self._backend.get_node(uri)
        return node is not None

    async def rm(self, uri: str, recursive: bool = False) -> bool:
        """删除节点。

        Args:
            uri: 要删除的 URI。
            recursive: 是否递归删除目录。

        Returns:
            是否成功删除。
        """
        node = await self._backend.get_node(uri)
        if node is None:
            return False

        if node.node_type == "dir" and node.children:
            if not recursive:
                raise OSError(f"目录非空，需要 recursive=True: {uri}")
            # 递归删除子节点
            for child_name in list(node.children):
                child_uri = f"{uri.rstrip('/')}/{child_name}"
                await self.rm(child_uri, recursive=True)

        # 从父目录的 children 中移除
        p_uri = parent_uri(uri)
        if p_uri:
            parent_node = await self._backend.get_node(p_uri)
            if parent_node and node.name in parent_node.children:
                parent_node.children.remove(node.name)
                await self._backend.set_node(p_uri, parent_node)

        return await self._backend.delete_node(uri)

    async def get_metadata(self, uri: str) -> Dict[str, Any]:
        """获取节点元数据。"""
        node = await self._backend.get_node(uri)
        if node is None:
            raise FileNotFoundError(f"路径不存在: {uri}")
        return dict(node.metadata)
