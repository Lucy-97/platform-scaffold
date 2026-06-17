"""
VFS Agent Tool 定义生成器
==========================

将 VFS 操作暴露为 OpenAI Function-Calling 格式的 Agent Tools，
让 LLM 通过 Tool-Calling 按需浏览和读取上下文。

生成的工具:
  - vfs_ls:   浏览目录结构（返回子节点 + L0 摘要）
  - vfs_read: 按需读取内容（支持 l0/l1/l2 层级选择）
  - vfs_tree: 递归展示全景目录树
"""

import json
from typing import Any, Callable, Dict, List, Tuple

from agent_core.vfs.core import VFS
from agent_core.vfs.models import ContextLayer


def build_vfs_tools(vfs: VFS) -> Tuple[List[Dict[str, Any]], Dict[str, Callable]]:
    """生成 VFS 相关的 Agent Tools（OpenAI Function-Calling 格式）。

    返回 (tool_definitions, tool_handlers):
      - tool_definitions: 传给 LLM 的 tools 参数
      - tool_handlers: {tool_name: async handler_fn} 映射

    Args:
        vfs: VFS 实例。

    Returns:
        (tool_definitions, tool_handlers) 元组。
    """

    async def _handle_vfs_ls(args: Dict[str, Any]) -> str:
        """处理 vfs_ls 工具调用。"""
        uri = args.get("uri", "vfs://")
        try:
            entries = await vfs.ls(uri)
            return json.dumps({"status": "success", "entries": entries}, ensure_ascii=False)
        except (FileNotFoundError, NotADirectoryError) as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

    async def _handle_vfs_read(args: Dict[str, Any]) -> str:
        """处理 vfs_read 工具调用。"""
        uri = args.get("uri", "")
        layer = args.get("layer", "l2")
        try:
            ctx_layer = ContextLayer(layer)
            content = await vfs.read(uri, ctx_layer)
            return json.dumps({"status": "success", "uri": uri, "layer": layer, "content": content}, ensure_ascii=False)
        except FileNotFoundError as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

    async def _handle_vfs_tree(args: Dict[str, Any]) -> str:
        """处理 vfs_tree 工具调用。"""
        uri = args.get("uri", "vfs://")
        max_depth = args.get("max_depth", 3)
        try:
            entries = await vfs.tree(uri, max_depth=max_depth)
            return json.dumps({"status": "success", "entries": entries}, ensure_ascii=False)
        except FileNotFoundError as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

    tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": "vfs_ls",
                "description": "列出虚拟文件系统中指定目录的内容。返回每个子节点的名称、类型和一句话摘要(L0)。用于浏览可用的上下文信息。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": "目录 URI，如 'vfs://story_001/chars/'",
                        },
                    },
                    "required": ["uri"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "vfs_read",
                "description": "读取虚拟文件系统中指定路径的内容。可选择读取不同层级：l0(一句话摘要)、l1(概览)、l2(完整内容)。默认读取完整内容(l2)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": "文件或目录 URI，如 'vfs://story_001/chars/alice'",
                        },
                        "layer": {
                            "type": "string",
                            "enum": ["l0", "l1", "l2"],
                            "description": "读取层级: l0=一句话摘要, l1=概览, l2=完整内容。默认 l2。",
                        },
                    },
                    "required": ["uri"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "vfs_tree",
                "description": "递归展示虚拟文件系统的目录树结构，包含每个节点的名称、类型和摘要。用于快速了解整体上下文组织结构。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": "起始目录 URI，如 'vfs://story_001/'",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "最大遍历深度，默认 3",
                        },
                    },
                    "required": ["uri"],
                },
            },
        },
    ]

    tool_handlers = {
        "vfs_ls": _handle_vfs_ls,
        "vfs_read": _handle_vfs_read,
        "vfs_tree": _handle_vfs_tree,
    }

    return tool_definitions, tool_handlers
