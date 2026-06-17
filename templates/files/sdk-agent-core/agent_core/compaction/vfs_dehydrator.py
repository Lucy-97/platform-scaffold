"""
VFS 感知的分层降级脱水器 — VFSAwareDehydrator
=================================================

与 OpenViking VFS 联动：将过期的 VFS 内容逐层降级而非粗暴折叠。

传统 Microcompact：L2 全文 → 占位符（全丢）
VFS 联动方案：L2 全文 → L1 概览 → L0 摘要 → 占位符（渐进）

核心优势：
  VFS 已经为每个节点预生成了三层内容（OpenViking 架构），
  脱水器只需读取低层内容代替高层，无需额外 LLM 调用。
"""

from typing import Any, Dict, List, Optional

from loguru import logger


class VFSAwareDehydrator:
    """VFS 感知的上下文脱水器——将过期的 VFS 内容逐层降级。

    降级策略：
      - turn_gap >= l2_to_l1_turns: L2 全文 → L1 概览
      - turn_gap >= l1_to_l0_turns: L1 概览 → L0 摘要
      - turn_gap >= l0_fold_turns:  L0 摘要 → 占位符（完全折叠）

    Args:
        vfs: VFS 实例（来自 agent_core.vfs）。
        l2_to_l1_turns: L2 降级为 L1 的轮次阈值（默认 3）。
        l1_to_l0_turns: L1 降级为 L0 的轮次阈值（默认 6）。
        l0_fold_turns: L0 彻底折叠的轮次阈值（默认 10）。
    """

    def __init__(
        self,
        vfs: Optional[Any] = None,
        l2_to_l1_turns: int = 3,
        l1_to_l0_turns: int = 6,
        l0_fold_turns: int = 10,
    ) -> None:
        self._vfs = vfs
        self._l2_to_l1 = l2_to_l1_turns
        self._l1_to_l0 = l1_to_l0_turns
        self._l0_fold = l0_fold_turns

    async def dehydrate(
        self,
        messages: List[Dict[str, Any]],
        current_turn: int,
    ) -> tuple[List[Dict[str, Any]], int]:
        """对 VFS 来源的工具返回实施分层降级。

        识别条件：消息带有 _vfs_uri 元数据。

        Args:
            messages: 消息列表（原地修改）。
            current_turn: 当前轮次。

        Returns:
            (修改后的消息列表, 释放的字符数)。
        """
        if not self._vfs:
            return messages, 0

        total_freed = 0

        for msg in messages:
            if msg.get("role") != "tool":
                continue

            # 检查是否是 VFS 工具返回
            vfs_uri = msg.get("_vfs_uri")
            if not vfs_uri:
                continue

            content = msg.get("content", "")
            if not isinstance(content, str):
                continue

            turn_gap = current_turn - msg.get("_turn", 0)
            current_layer = msg.get("_vfs_layer", "l2")
            original_len = len(content)

            try:
                if turn_gap >= self._l0_fold and current_layer == "l0":
                    # ★ L0 → 彻底折叠
                    msg["content"] = (
                        f"[已折叠] {vfs_uri} 的内容。"
                        f"如需查看请重新 vfs_read。"
                    )
                    msg["_vfs_layer"] = "folded"

                elif turn_gap >= self._l1_to_l0 and current_layer == "l1":
                    # ★ L1 → L0：从概览降级为摘要
                    node = await self._vfs.get_node(vfs_uri)
                    if node and node.abstract:
                        msg["content"] = (
                            f"[已降级为摘要] {vfs_uri}: {node.abstract}"
                        )
                        msg["_vfs_layer"] = "l0"

                elif turn_gap >= self._l2_to_l1 and current_layer == "l2":
                    # ★ L2 → L1：从全文降级为概览
                    node = await self._vfs.get_node(vfs_uri)
                    if node and node.overview:
                        msg["content"] = (
                            f"[已降级为概览] {vfs_uri}\n{node.overview}\n"
                            f"（如需全文请再次调用 vfs_read）"
                        )
                        msg["_vfs_layer"] = "l1"

            except Exception as e:
                # VFS 降级失败不阻塞主流程
                logger.warning(
                    f"[VFSDehydrator] 降级失败: {vfs_uri} error={e}"
                )
                continue

            freed = original_len - len(msg.get("content", ""))
            if freed > 0:
                total_freed += freed

        if total_freed > 0:
            logger.info(
                f"[VFSDehydrator] VFS 降级释放 {total_freed} 字符 "
                f"(~{total_freed // 3} tokens)"
            )

        return messages, total_freed
