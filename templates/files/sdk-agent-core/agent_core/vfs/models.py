"""
VFS 数据模型与 URI 工具函数
============================

定义 VFS 虚拟文件系统的核心数据结构：
  - ContextLayer: L0/L1/L2 上下文层级枚举
  - VFSNode: 虚拟文件系统节点（文件/目录）
  - URI 解析/构建/安全检查工具函数
"""

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════════
# 上下文层级枚举
# ═══════════════════════════════════════════════════════════════════════════

class ContextLayer(str, Enum):
    """上下文层级枚举（与 aigc-backend BlackboardContext 的 ContextLayer 保持一致）。"""
    L0_ABSTRACT = "l0"   # 一句话摘要（~100 tokens）
    L1_OVERVIEW = "l1"   # 核心概览（~2000 tokens）
    L2_DETAIL = "l2"     # 完整原始内容


# ═══════════════════════════════════════════════════════════════════════════
# VFSNode 数据类
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class VFSNode:
    """虚拟文件系统节点 — 同时承载文件和目录。

    Attributes:
        name: 节点名称（如 "alice"、"chars"）。
        node_type: 类型，"file" 或 "dir"。
        content: L2 全量内容（仅 file 类型有效）。
        abstract: L0 一句话摘要。
        overview: L1 概览。
        metadata: 自定义元数据（如 role_id、type 等业务字段）。
        children: 子节点名称列表（仅 dir 类型有效，存储用）。
    """
    name: str
    node_type: str = "file"  # "file" | "dir"
    content: str = ""
    abstract: str = ""
    overview: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    children: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（用于 Redis/MySQL 存储）。"""
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "VFSNode":
        """从 dict 反序列化。"""
        return VFSNode(**data)

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_json(raw: str) -> "VFSNode":
        """从 JSON 字符串反序列化。"""
        return VFSNode.from_dict(json.loads(raw))


# ═══════════════════════════════════════════════════════════════════════════
# URI 工具函数
# ═══════════════════════════════════════════════════════════════════════════

VFS_SCHEME = "vfs://"


def parse_uri(uri: str) -> List[str]:
    """解析 VFS URI 为路径段列表。

    示例::
        parse_uri("vfs://story_001/chars/alice") → ["story_001", "chars", "alice"]
        parse_uri("vfs://story_001/chars/")      → ["story_001", "chars"]

    Args:
        uri: VFS URI 字符串。

    Returns:
        路径段列表。

    Raises:
        ValueError: URI 格式不合法。
    """
    if not uri.startswith(VFS_SCHEME):
        raise ValueError(f"URI 必须以 '{VFS_SCHEME}' 开头，收到: {uri}")
    path = uri[len(VFS_SCHEME):]
    # 过滤空段（处理尾部斜杠）
    segments = [s for s in path.split("/") if s]
    # 安全检查：禁止 .. 和 . 路径穿越
    for seg in segments:
        if seg in (".", ".."):
            raise ValueError(f"不允许路径穿越符号 '{seg}': {uri}")
    return segments


def build_uri(segments: List[str]) -> str:
    """从路径段列表构建 VFS URI。

    Args:
        segments: 路径段列表。

    Returns:
        VFS URI 字符串。
    """
    return VFS_SCHEME + "/".join(segments)


def parent_uri(uri: str) -> Optional[str]:
    """获取父路径 URI。

    Args:
        uri: 当前 URI。

    Returns:
        父路径 URI；根路径时返回 None。
    """
    segments = parse_uri(uri)
    if len(segments) <= 1:
        return None
    return build_uri(segments[:-1])
