"""
VFS 虚拟文件系统 — agent-core 通用模块
=============================================

借鉴 OpenViking 的分层上下文管理（L0/L1/L2）与 URI 虚拟文件系统设计，
为 Agent 提供**确定性的上下文按需加载能力**。

核心优势（相比传统扁平 Prompt 注入）：
  - 目录树结构：Agent 通过 ls/read 浏览，而非全量接收
  - 三层分辨率：L0 摘要 → L1 概览 → L2 全文，逐级深入
  - 可插拔后端：InMemory（测试）/ Redis（缓存）/ MySQL（持久化）

模块结构::

    vfs/
    ├── __init__.py   # 本文件：统一导出
    ├── models.py     # VFSNode, ContextLayer, URI 工具函数
    ├── backends.py   # VFSBackend ABC + InMemory/Redis/MySQL/Cached 实现
    ├── core.py       # VFS 主类（mount/ls/read/tree/rm）
    └── tools.py      # build_vfs_tools Agent Tool 生成器

使用示例::

    from agent_core.vfs import VFS, InMemoryBackend

    vfs = VFS(backend=InMemoryBackend())
    await vfs.mount(
        "vfs://project_001/config/database",
        content="PostgreSQL 16, 连接池 20...",
        abstract="数据库配置",
        overview="PostgreSQL 16 主从部署...",
    )
    entries = await vfs.ls("vfs://project_001/config/")
    content = await vfs.read("vfs://project_001/config/database")
"""

from agent_core.vfs.backends import (
    CachedBackend,
    InMemoryBackend,
    MySQLBackend,
    RedisBackend,
    VFSBackend,
)
from agent_core.vfs.core import VFS
from agent_core.vfs.models import ContextLayer, VFSNode
from agent_core.vfs.tools import build_vfs_tools

__all__ = [
    "VFS",
    "VFSNode",
    "VFSBackend",
    "InMemoryBackend",
    "RedisBackend",
    "MySQLBackend",
    "CachedBackend",
    "ContextLayer",
    "build_vfs_tools",
]
