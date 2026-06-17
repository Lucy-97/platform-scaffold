"""
Token 治理子包 — compaction
==============================

四层压缩漏斗管线 + VFS 联动 + 预算管控 + 截断自愈。

管线执行顺序（每轮 PRE_SAMPLING 阶段自动触发）：
  L0: VFS 按需加载（源头治理，已由 VFS 模块实现）
  L1: Snip Compact     — 正则清洗死文本（零成本）
  L2: Microcompact     — 工具返回脱水（含 VFS 感知降级）
  L3: Context Collapse  — 重复输出折叠
  L4: Autocompact      — LLM 驱动摘要（仅超限时触发）
"""

from agent_core.compaction.snip import SnipCompactor
from agent_core.compaction.microcompact import ToolResultDehydrator
from agent_core.compaction.budget import AgentCoreTokenBudget
from agent_core.compaction.recovery import reactive_token_recovery
from agent_core.compaction.result_budget import apply_result_budget
from agent_core.compaction.context_cache import StoryContextCache
from agent_core.compaction.pair_sanitizer import sanitize_tool_pairs

__all__ = [
    "SnipCompactor",
    "ToolResultDehydrator",
    "AgentCoreTokenBudget",
    "reactive_token_recovery",
    "apply_result_budget",
    "StoryContextCache",
    "sanitize_tool_pairs",
]
