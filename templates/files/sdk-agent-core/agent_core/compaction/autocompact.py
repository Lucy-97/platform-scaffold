"""
第四层：LLM 驱动自动摘要 — Autocompact (v2)
================================================

v2 改进（借鉴 Hermes ContextCompressor）：
  1. 结构化摘要模板 — 7 段式模板强制保留高价值信息
  2. 迭代式摘要更新 — 第 N+1 次压缩在前一次摘要基础上增量更新
  3. Token 预算动态缩放 — 按被压缩内容量动态计算摘要 budget
  4. 摘要失败冷却 — 失败后短时间内不再尝试，避免浪费 token
  5. 保留 AgentCore 专用增强：角色关系/伏笔/VFS URI

管线位置：
  L4 — 仅在前三层物理压缩仍无法控制 Token 时触发。
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ── v2 结构化摘要 Prompt 模板 ──
# 借鉴 Hermes ContextCompressor 的 7 段式结构，
# 并融合 AgentCore 专用的角色状态和伏笔跟踪字段。
_STRUCTURED_SUMMARY_PROMPT = """You are a context compression assistant for an AgentCore story production system.

Analyze the conversation below and output a structured summary using EXACTLY this format:

## Goal
[The user's core objective in this session]

## Constraints & Preferences
[User preferences, coding style, creative constraints, important decisions]

## Progress
### Done
- [Completed work items — include specific file paths, commands run, results obtained]
### In Progress
- [Work currently underway]
### Blocked
- [Issues or blockers encountered]

## Character State (AgentCore-specific)
[For each character mentioned: current status, relationships, emotional state]

## Unresolved Threads
- [Unresolved plot threads, foreshadowing, open questions]

## Key Decisions
- [Important technical/creative decisions and their rationale]

## Relevant Files
- [Files read, modified, or created — with brief descriptions]

## Next Steps
- [What needs to be done to continue the work]

## Critical Context
[Specific values, error messages, configuration details, or plot details that would be lost without explicit preservation]

CRITICAL RULES:
- Preserve ALL character relationship changes
- Preserve ALL unresolved plot threads and foreshadowing
- Preserve file paths and tool call results that affect current state
- Discard intermediate drafting discussions and debug logs
- Be comprehensive but concise
"""

# 迭代更新时注入的附加指令
_ITERATIVE_UPDATE_INSTRUCTION = """
IMPORTANT: Below is the PREVIOUS summary from an earlier compression.
Update it with the new conversation content:
- Move "In Progress" items to "Done" if completed
- Add new work items
- Update character states with new developments
- Only remove information that is explicitly outdated
- Keep all unresolved threads unless they were resolved

PREVIOUS SUMMARY:
{previous_summary}

NEW CONVERSATION TO INTEGRATE:
"""

# 摘要失败后的冷却时间（秒）
_COOLDOWN_SECONDS = 600  # 10 分钟


class Autocompactor:
    """LLM 驱动的自动摘要压缩器（v2 — 结构化摘要 + 迭代更新）。

    v2 核心改进：
      - 结构化摘要模板替代自由文本 JSON
      - _previous_summary 支持跨次压缩的增量更新
      - Token 预算按被压缩内容量动态缩放
      - 摘要失败冷却机制

    Args:
        model: 使用的 LLM 模型（建议用廉价快速模型）。
        api_key: LLM API Key。
        api_base: LLM API Base URL。
        trigger_ratio: 触发摘要的 Token 占用比（默认 0.8 = 80%）。
        token_limit: 上下文 Token 上限（默认 100000）。
        keep_recent: 保留最近的消息数（不被摘要覆盖）。
        summary_ratio: 摘要 Token 占被压缩内容的比例（默认 0.2）。
        max_summary_tokens: 摘要 Token 绝对上限（默认 4000）。
    """

    def __init__(
        self,
        model: str = "gemini/gemini-flash-lite-latest",
        api_key: str = "",
        api_base: Optional[str] = None,
        trigger_ratio: float = 0.8,
        token_limit: int = 100_000,
        keep_recent: int = 4,
        summary_ratio: float = 0.2,
        max_summary_tokens: int = 4000,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.trigger_ratio = trigger_ratio
        self.token_limit = token_limit
        self.keep_recent = keep_recent
        self.summary_ratio = summary_ratio
        self.max_summary_tokens = max_summary_tokens

        # v2: 迭代式摘要状态
        self._previous_summary: str = ""
        # v2: 摘要失败冷却
        self._last_failure_time: float = 0.0

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """粗略估算消息列表的 Token 数。

        中文约 1 字 ≈ 1.5 token，英文约 4 字符 ≈ 1 token。
        取保守估计：每个字符约 0.75 token。
        """
        total_chars = sum(
            len(str(m.get("content", "")))
            for m in messages
        )
        return int(total_chars * 0.75)

    def should_trigger(self, messages: List[Dict[str, Any]]) -> bool:
        """检查是否需要触发 Autocompact。"""
        # 冷却期内不触发
        if self._is_in_cooldown():
            return False
        estimated = self.estimate_tokens(messages)
        threshold = int(self.token_limit * self.trigger_ratio)
        return estimated > threshold

    async def compact(
        self,
        messages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        """执行 v2 Autocompact — 结构化摘要 + 迭代更新。

        工作流程：
          1. 检查是否需要触发（含冷却检查）
          2. 分离 system / 待压缩 / 保留消息
          3. 计算动态 Token 预算
          4. 调用廉价模型生成结构化摘要（支持迭代更新）
          5. 用摘要消息替代中间对话，保留最近 N 条

        Args:
            messages: 消息列表。

        Returns:
            (压缩后的消息列表, 释放的 token 估算量)。
        """
        if not self.should_trigger(messages):
            return messages, 0

        tokens_before = self.estimate_tokens(messages)
        logger.info(
            f"[Autocompact-v2] 触发! 当前估算 {tokens_before} tokens "
            f"(阈值 {int(self.token_limit * self.trigger_ratio)})"
        )

        # 分离消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= self.keep_recent:
            return messages, 0  # 消息太少不值得压缩

        # 需要压缩的部分 + 保留的最近消息
        to_compress = non_system[:-self.keep_recent]
        to_keep = non_system[-self.keep_recent:]

        # v2: 动态计算摘要 Token 预算
        compressed_tokens = self.estimate_tokens(to_compress)
        summary_budget = self._calc_summary_budget(compressed_tokens)

        # 序列化需要压缩的部分
        compress_text = self._serialize_messages(to_compress)

        # v2: 调用 LLM 生成结构化摘要（支持迭代更新）
        summary = await self._call_llm_for_summary(
            compress_text, summary_budget
        )

        if not summary:
            # 摘要失败 — 进入冷却期，避免反复浪费 token
            self._last_failure_time = time.monotonic()
            logger.warning(
                f"[Autocompact-v2] LLM 摘要失败，进入 "
                f"{_COOLDOWN_SECONDS}s 冷却期"
            )
            return messages, 0

        # v2: 更新迭代摘要状态
        self._previous_summary = summary

        # 重组消息列表：system + 摘要 + 保留的最近消息
        summary_msg = {
            "role": "user",
            "content": (
                "[上下文摘要 — 以下是之前对话的结构化压缩摘要]\n\n"
                f"{summary}\n\n"
                "[摘要结束 — 以下是最近的对话]"
            ),
        }

        new_messages = system_msgs + [summary_msg] + to_keep
        tokens_after = self.estimate_tokens(new_messages)
        freed = tokens_before - tokens_after

        logger.info(
            f"[Autocompact-v2] 压缩完成: {tokens_before} → {tokens_after} tokens "
            f"(释放 {freed} tokens, 压缩比 {freed/max(tokens_before,1)*100:.0f}%) "
            f"[迭代摘要: {'是' if self._previous_summary else '首次'}]"
        )

        return new_messages, freed

    def _calc_summary_budget(self, compressed_tokens: int) -> int:
        """v2: 动态计算摘要 Token 预算。

        计算公式（借鉴 Hermes）：
          budget = min(compressed_tokens * ratio, context_limit * 0.05, max_cap)

        大上下文模型获得更丰富的摘要，小上下文模型更紧凑。
        """
        ratio_budget = int(compressed_tokens * self.summary_ratio)
        context_budget = int(self.token_limit * 0.05)
        budget = min(ratio_budget, context_budget, self.max_summary_tokens)
        # 保证最低 500 tokens 的摘要空间
        return max(budget, 500)

    def _is_in_cooldown(self) -> bool:
        """检查是否处于摘要失败冷却期。"""
        if self._last_failure_time <= 0:
            return False
        elapsed = time.monotonic() - self._last_failure_time
        return elapsed < _COOLDOWN_SECONDS

    async def _call_llm_for_summary(
        self, text: str, max_tokens: int
    ) -> str:
        """调用廉价模型生成结构化摘要。

        v2 改进：
          - 使用结构化 Prompt 模板
          - 如果有前一次摘要，注入迭代更新指令
        """
        import litellm

        # 构建 user prompt — 根据是否有前一次摘要决定内容
        if self._previous_summary:
            user_content = (
                _ITERATIVE_UPDATE_INSTRUCTION.format(
                    previous_summary=self._previous_summary
                )
                + text
            )
        else:
            user_content = f"需要压缩的对话历史：\n\n{text}"

        try:
            call_params: Dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": _STRUCTURED_SUMMARY_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "api_key": self.api_key,
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
            if self.api_base:
                call_params["api_base"] = self.api_base

            response = await litellm.acompletion(**call_params)
            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"[Autocompact-v2] LLM 调用失败: {e}")
            return ""

    def _serialize_messages(self, messages: List[Dict[str, Any]]) -> str:
        """序列化消息为可读文本。"""
        lines = []
        for m in messages:
            role = m.get("role", "?").upper()
            content = str(m.get("content", ""))
            # 截断单条过长消息
            if len(content) > 2000:
                content = content[:2000] + "...(已截断)"
            lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines)

    @property
    def previous_summary(self) -> str:
        """获取上一次的摘要内容（用于调试/测试）。"""
        return self._previous_summary

    def reset(self) -> None:
        """重置迭代摘要状态（新会话时调用）。"""
        self._previous_summary = ""
        self._last_failure_time = 0.0
