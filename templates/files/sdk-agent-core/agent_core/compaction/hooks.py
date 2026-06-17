"""
Token 治理管线 Hook 集成 — CompactionHooks
=============================================

将所有压缩模块注册为 Runtime V2 的 LifecycleHook。

集成方式：
  一次调用 register_all() 即可将整个管线挂载到引擎。

管线执行顺序（PRE_SAMPLING 阶段）：
  p10: Snip Compact        — 正则清洗死文本
  p20: Microcompact         — 工具返回脱水
  p30: VFS Dehydrate        — VFS 分层降级
  p40: Autocompact          — LLM 驱动摘要（条件触发）

POST_TOOL 阶段:
  p10: Result Budget        — 大结果截断落盘

POST_SAMPLING 阶段:
  p10: Token Budget Track   — 预算消费追踪
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.compaction.autocompact import Autocompactor
from agent_core.compaction.pair_sanitizer import sanitize_tool_pairs
from agent_core.compaction.budget import AgentCoreTokenBudget
from agent_core.compaction.context_cache import StoryContextCache
from agent_core.compaction.microcompact import ToolResultDehydrator
from agent_core.compaction.result_budget import apply_result_budget
from agent_core.compaction.snip import SnipCompactor
from agent_core.compaction.vfs_dehydrator import VFSAwareDehydrator
from agent_core.runtime.hooks import HookPhase, LifecycleHookRegistry


class CompactionHooks:
    """Token 治理管线——一键注册全部压缩 Hook。

    Args:
        hooks: 目标 Hook 注册表。
        snip: 正则清洗器（可选，默认创建）。
        dehydrator: 工具脱水器（可选，默认创建）。
        vfs_dehydrator: VFS 脱水器（可选，需传 VFS 实例）。
        autocompactor: 自动摘要器（可选，需传 LLM 配置）。
        budget: Token 预算管控器（可选）。
        context_cache: 文件指纹缓存（可选，默认创建）。
        result_max_chars: 工具返回截断上限（默认 3000 字符）。
    """

    def __init__(
        self,
        hooks: LifecycleHookRegistry,
        snip: Optional[SnipCompactor] = None,
        dehydrator: Optional[ToolResultDehydrator] = None,
        vfs_dehydrator: Optional[VFSAwareDehydrator] = None,
        autocompactor: Optional[Autocompactor] = None,
        budget: Optional[AgentCoreTokenBudget] = None,
        context_cache: Optional[StoryContextCache] = None,
        result_max_chars: int = 3000,
    ) -> None:
        self._hooks = hooks
        self._snip = snip or SnipCompactor()
        self._dehydrator = dehydrator or ToolResultDehydrator()
        self._vfs_dehydrator = vfs_dehydrator
        self._autocompactor = autocompactor
        self._budget = budget
        self._context_cache = context_cache or StoryContextCache()
        self._result_max_chars = result_max_chars

        # 统计数据
        self._stats = {
            "snip_freed": 0,
            "dehydrate_freed": 0,
            "vfs_freed": 0,
            "autocompact_freed": 0,
            "result_truncated": 0,
            "pairs_fixed": 0,
        }

    def register_all(self) -> None:
        """一键注册全部压缩 Hook。"""

        # ── PRE_SAMPLING: 压缩管线（按优先级顺序） ──
        self._hooks.register(
            HookPhase.PRE_SAMPLING,
            self._snip_hook,
            name="compaction_snip",
            priority=10,
        )
        self._hooks.register(
            HookPhase.PRE_SAMPLING,
            self._microcompact_hook,
            name="compaction_microcompact",
            priority=20,
        )

        if self._vfs_dehydrator:
            self._hooks.register(
                HookPhase.PRE_SAMPLING,
                self._vfs_dehydrate_hook,
                name="compaction_vfs_dehydrate",
                priority=30,
            )

        if self._autocompactor:
            self._hooks.register(
                HookPhase.PRE_SAMPLING,
                self._autocompact_hook,
                name="compaction_autocompact",
                priority=40,
            )

        # ── POST_TOOL: 结果截断 ──
        self._hooks.register(
            HookPhase.POST_TOOL,
            self._result_budget_hook,
            name="compaction_result_budget",
            priority=10,
        )

        # ── POST_SAMPLING: 预算追踪 ──
        if self._budget:
            self._hooks.register(
                HookPhase.POST_SAMPLING,
                self._budget_track_hook,
                name="compaction_budget_track",
                priority=10,
            )

        logger.info(
            f"[CompactionHooks] 已注册 Token 治理管线, "
            f"Hook 总览: {self._hooks.summary()}"
        )

    # ── Hook 实现 ──

    async def _snip_hook(self, messages: list = None, **kw) -> None:
        """PRE_SAMPLING: 正则清洗。"""
        if messages:
            _, freed = self._snip.compact(messages)
            self._stats["snip_freed"] += freed

    async def _microcompact_hook(
        self, messages: list = None, turn: int = 0, **kw
    ) -> None:
        """PRE_SAMPLING: 工具返回脱水。"""
        if messages:
            _, freed = self._dehydrator.dehydrate(messages, turn)
            self._stats["dehydrate_freed"] += freed

    async def _vfs_dehydrate_hook(
        self, messages: list = None, turn: int = 0, **kw
    ) -> None:
        """PRE_SAMPLING: VFS 分层降级。"""
        if messages and self._vfs_dehydrator:
            _, freed = await self._vfs_dehydrator.dehydrate(messages, turn)
            self._stats["vfs_freed"] += freed

    async def _autocompact_hook(
        self, messages: list = None, **kw
    ) -> None:
        """PRE_SAMPLING: 自动摘要（条件触发）+ 配对完整性修复。"""
        if messages and self._autocompactor:
            new_msgs, freed = await self._autocompactor.compact(messages)
            if freed > 0:
                # 原地替换消息列表
                messages.clear()
                messages.extend(new_msgs)
                self._stats["autocompact_freed"] += freed

                # v2: 压缩后修复可能断裂的 tool call/result 配对
                _, fixes = sanitize_tool_pairs(messages)
                self._stats["pairs_fixed"] += fixes

    async def _result_budget_hook(
        self, tool_name: str = "", tool_result: str = "", **kw
    ) -> None:
        """POST_TOOL: 大结果截断落盘。"""
        if tool_result and len(tool_result) > self._result_max_chars:
            self._stats["result_truncated"] += 1

    async def _budget_track_hook(
        self, content: str = "", turn: int = 0, **kw
    ) -> None:
        """POST_SAMPLING: 预算消费追踪。"""
        if self._budget and content:
            # 粗略估算 token 消费
            tokens = int(len(content) * 0.75)
            result = self._budget.consume(tokens, source="llm")
            if result.get("action") == "warn":
                logger.warning(result.get("message", ""))
            elif result.get("action") == "limit":
                logger.error(result.get("message", ""))

    @property
    def stats(self) -> Dict[str, Any]:
        """获取管线统计数据。"""
        return dict(self._stats)
