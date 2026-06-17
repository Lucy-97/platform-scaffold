"""
Tool Call / Result 配对完整性校验 — pair_sanitizer
=====================================================

解决上下文压缩后可能出现的消息配对断裂问题：
  - 孤立的 tool result（其对应的 assistant tool_call 已被压缩掉）
  - 缺失 result 的 tool call（tool result 被压缩掉了）

任何一种不完整配对都会导致 LLM API 返回 400 错误。

移植自 Hermes Agent 的 _sanitize_tool_pairs() 逻辑，
适配 agent-core 的 OpenAI 消息格式。
"""

from typing import Any, Dict, List, Set, Tuple

from loguru import logger

# 孤立 tool result 的存根替换内容
_STUB_CONTENT = "[此工具调用的结果已在上下文压缩中省略 — 请参考上方摘要获取相关信息]"


def sanitize_tool_pairs(
    messages: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """修复消息列表中孤立的 tool_call / tool_result 配对。

    在上下文压缩（Autocompact / SnipCompact）执行后调用，
    确保每个 tool result 都有对应的 assistant tool_call，
    每个 assistant tool_call 都有对应的 tool result。

    Args:
        messages: OpenAI 格式的消息列表（会被原地修改）。

    Returns:
        (修复后的消息列表, 修复的配对数)。
    """
    # ── 第一步：收集所有存活的 assistant tool_call IDs ──
    surviving_call_ids: Set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            # 兼容 dict 和对象两种格式（litellm 可能返回对象）
            cid = _extract_id(tc)
            if cid:
                surviving_call_ids.add(cid)

    # ── 第二步：收集所有 tool result 的 call IDs ──
    result_call_ids: Set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            cid = msg.get("tool_call_id")
            if cid:
                result_call_ids.add(cid)

    fixes = 0

    # ── 第三步：移除孤立的 tool results ──
    # 即 result 存在但对应的 assistant tool_call 已被压缩掉
    orphaned_results = result_call_ids - surviving_call_ids
    if orphaned_results:
        original_len = len(messages)
        messages[:] = [
            m for m in messages
            if not (
                m.get("role") == "tool"
                and m.get("tool_call_id") in orphaned_results
            )
        ]
        removed = original_len - len(messages)
        fixes += removed
        logger.debug(
            f"[PairSanitizer] 移除 {removed} 条孤立 tool result "
            f"(IDs: {orphaned_results})"
        )

    # ── 第四步：为缺失 result 的 tool calls 插入存根 ──
    # 即 assistant 发出了 tool_call 但 result 被压缩掉了
    missing_results = surviving_call_ids - result_call_ids
    if missing_results:
        patched: List[Dict[str, Any]] = []
        for msg in messages:
            patched.append(msg)
            if msg.get("role") != "assistant":
                continue
            # 在 assistant 消息后面紧跟插入缺失的 tool result 存根
            for tc in msg.get("tool_calls") or []:
                cid = _extract_id(tc)
                if cid and cid in missing_results:
                    patched.append({
                        "role": "tool",
                        "content": _STUB_CONTENT,
                        "tool_call_id": cid,
                    })
                    fixes += 1
        messages[:] = patched
        logger.debug(
            f"[PairSanitizer] 插入 {len(missing_results)} 条 tool result 存根 "
            f"(IDs: {missing_results})"
        )

    if fixes > 0:
        logger.info(f"[PairSanitizer] 共修复 {fixes} 个配对问题")

    return messages, fixes


def _extract_id(tc: Any) -> str:
    """从 tool_call 条目中提取 ID — 兼容 dict 和对象格式。"""
    if isinstance(tc, dict):
        return tc.get("id", "")
    return getattr(tc, "id", "")
