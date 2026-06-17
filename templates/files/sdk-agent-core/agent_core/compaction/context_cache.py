"""
文件指纹上下文缓存 — StoryContextCache
==========================================

当 Agent 在同一会话中多次读取同一文件时，
用指纹去重避免重复注入相同内容。

典型场景：
  Agent 反复 vfs_read 角色设定文件——第一次注入全文，
  后续只注入"已缓存"标记。节约约 60% 重复 Token 消耗。
"""

import hashlib
from typing import Dict, Set

from loguru import logger


class StoryContextCache:
    """文件指纹上下文缓存——避免重复注入相同内容。

    Args:
        max_entries: 最大缓存条目数（LRU-like，先进先出）。
    """

    def __init__(self, max_entries: int = 100) -> None:
        self._max = max_entries
        # 指纹集合（用于快速查重）
        self._fingerprints: Set[str] = set()
        # URI → 指纹 映射（用于更新检测）
        self._uri_map: Dict[str, str] = {}
        # 插入顺序列表（用于 LRU 淘汰）
        self._order: list = []

    def _compute_fingerprint(self, content: str) -> str:
        """计算内容指纹（MD5 前 16 位足够去重）。"""
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]

    def check_and_cache(
        self, uri: str, content: str,
    ) -> tuple[bool, str]:
        """检查内容是否已缓存，如果是则返回占位提示。

        Args:
            uri: 文件/资源标识符（如 VFS URI 或文件路径）。
            content: 文件内容。

        Returns:
            (is_cached, result_content)
            - (False, content): 首次加载，返回原始内容
            - (True, placeholder): 已缓存，返回占位符
        """
        fingerprint = self._compute_fingerprint(content)

        # 检查是否已缓存
        if uri in self._uri_map:
            old_fp = self._uri_map[uri]
            if old_fp == fingerprint:
                # 完全相同——返回占位符
                placeholder = (
                    f"[已缓存] {uri} 内容未变化 "
                    f"(首次读取时已注入，{len(content)} 字符)。"
                    f"如需强制刷新请使用 --force 选项。"
                )
                logger.debug(
                    f"[ContextCache] 命中缓存: {uri} "
                    f"(节约 {len(content)} 字符)"
                )
                return True, placeholder
            else:
                # 内容有变化——更新缓存
                self._uri_map[uri] = fingerprint
                self._fingerprints.discard(old_fp)
                self._fingerprints.add(fingerprint)
                logger.debug(f"[ContextCache] 缓存更新: {uri}")
                return False, content
        else:
            # 首次加载——加入缓存
            self._evict_if_needed()
            self._uri_map[uri] = fingerprint
            self._fingerprints.add(fingerprint)
            self._order.append(uri)
            logger.debug(f"[ContextCache] 缓存新增: {uri}")
            return False, content

    def invalidate(self, uri: str) -> bool:
        """手动使指定 URI 的缓存失效。"""
        if uri in self._uri_map:
            fp = self._uri_map.pop(uri)
            self._fingerprints.discard(fp)
            if uri in self._order:
                self._order.remove(uri)
            return True
        return False

    def clear(self) -> None:
        """清空全部缓存。"""
        self._fingerprints.clear()
        self._uri_map.clear()
        self._order.clear()

    def _evict_if_needed(self) -> None:
        """如果超出容量上限，淘汰最旧条目。"""
        while len(self._order) >= self._max:
            oldest = self._order.pop(0)
            if oldest in self._uri_map:
                fp = self._uri_map.pop(oldest)
                self._fingerprints.discard(fp)

    @property
    def size(self) -> int:
        """当前缓存条目数。"""
        return len(self._uri_map)
