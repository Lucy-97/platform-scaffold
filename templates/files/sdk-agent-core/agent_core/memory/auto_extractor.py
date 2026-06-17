"""
自动记忆提取引擎 — AutoMemoryExtractor
==========================================

从对话中提取结构化事实并写入 MemoryStore。
领域无关——不预设任何业务场景（剧本、编程、分析等），
由 LLM 自行判断事实类型和层级。

工作流：
  1. 在 ON_COMPLETE Hook 中被触发
  2. 取最后 N 轮对话，调用 LLM 提取结构化事实
  3. 按 layer + category 分类后写入 MemoryStore
  4. 自动执行冲突消解（新信息覆盖旧信息）
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.memory.models import (
    MemoryCategory,
    MemoryEntry,
    MemoryLayer,
)
from agent_core.memory.store import MemoryStore


# LLM 事实提取 Prompt（领域无关，面向 LLM 所以用英文）
_EXTRACT_PROMPT = """Analyze the following conversation and extract important facts as structured JSON.

For each fact, determine:
1. "layer": One of "L1_session", "L2_task", "L3_project", "L4_user", "L5_global"
   - L1: Temporary session state (what is currently being worked on)
   - L2: Task-specific facts (results, decisions made in this task)
   - L3: Project-level persistent facts (entities, configs, domain knowledge)
   - L4: User preferences and behavior patterns (across projects)
   - L5: Universal knowledge or best practices
2. "category": One of "entity", "relation", "event", "decision", "preference", "pattern", "state", "artifact", "note"
3. "subject": The main entity this fact is about
4. "content": A concise description of the fact in Chinese
5. "confidence": 0.0-1.0, how certain this fact is

Rules:
- Extract 3-8 facts maximum
- Focus on NEW information not previously established
- Do NOT extract trivial or obvious facts
- Entity descriptions → L3_project + entity
- User preferences → L4_user + preference
- Task results → L2_task + artifact
- Decisions and rationale → L3_project + decision

Output ONLY valid JSON array, no markdown formatting:
[{{"layer": "...", "category": "...", "subject": "...", "content": "...", "confidence": 0.9}}]

Conversation:
{conversation}"""


class AutoMemoryExtractor:
    """自动记忆提取引擎——从对话中提取结构化事实，领域无关。

    Args:
        store: 记忆存储后端。
        llm_caller: LLM 调用函数 (async callable)。
        model: 提取用的 LLM 模型（应使用廉价模型）。
        max_turns: 提取时考虑的最大对话轮数。
    """

    def __init__(
        self,
        store: MemoryStore,
        llm_caller: Optional[Any] = None,
        model: str = "gemini/gemini-2.0-flash-lite",
        max_turns: int = 6,
    ) -> None:
        self._store = store
        self._llm_caller = llm_caller
        self._model = model
        self._max_turns = max_turns

    async def extract_and_save(
        self,
        messages: List[Dict[str, str]],
        project_id: str = "",
        task_id: str = "",
        session_id: str = "",
        turn_number: int = 0,
    ) -> List[MemoryEntry]:
        """从对话中提取事实并保存到 MemoryStore。

        Args:
            messages: 对话消息列表。
            project_id: 项目 ID。
            task_id: 任务 ID。
            session_id: 会话 ID。
            turn_number: 当前轮次号。

        Returns:
            提取并保存的 MemoryEntry 列表。
        """
        recent = messages[-self._max_turns * 2:]
        if not recent:
            return []

        if not self._llm_caller:
            logger.info("[AutoExtractor] 无 LLM 调用器，跳过提取")
            return []

        conv_text = "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')[:500]}"
            for m in recent
        )

        try:
            raw_facts = await self._call_llm_extract(conv_text)
            entries = self._parse_facts(
                raw_facts,
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                turn_number=turn_number,
            )

            for entry in entries:
                await self._store.save(entry)

            logger.info(
                f"[AutoExtractor] 提取 {len(entries)} 条记忆 "
                f"(turn={turn_number}, project={project_id})"
            )
            return entries

        except Exception as e:
            logger.error(f"[AutoExtractor] 提取失败: {e}")
            return []

    async def _call_llm_extract(self, conv_text: str) -> str:
        """调用 LLM 执行事实提取。"""
        prompt = _EXTRACT_PROMPT.format(conversation=conv_text)

        response = await self._llm_caller(
            model=self._model,
            messages=[
                {"role": "system", "content": "You are a fact extraction engine. Output ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        return response

    def _parse_facts(
        self,
        raw_json: str,
        project_id: str = "",
        task_id: str = "",
        session_id: str = "",
        turn_number: int = 0,
    ) -> List[MemoryEntry]:
        """解析 LLM 返回的 JSON 为 MemoryEntry 列表。"""
        clean = raw_json.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]
            if clean.endswith("```"):
                clean = clean[:-3].strip()

        try:
            facts = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.warning(f"[AutoExtractor] JSON 解析失败: {e}")
            return []

        if not isinstance(facts, list):
            return []

        entries: List[MemoryEntry] = []
        for fact in facts:
            try:
                layer = MemoryLayer(fact.get("layer", "L2_task"))
                category = MemoryCategory(fact.get("category", "event"))
                entry = MemoryEntry(
                    memory_id=self._store.generate_id(),
                    layer=layer,
                    category=category,
                    subject=fact.get("subject", ""),
                    content=fact.get("content", ""),
                    confidence=float(fact.get("confidence", 0.8)),
                    source_turn=turn_number,
                    source_session=session_id,
                    project_id=project_id,
                    task_id=task_id,
                )
                entries.append(entry)
            except Exception as e:
                logger.warning(f"[AutoExtractor] 跳过无效事实: {e}")
                continue

        return entries

    def create_manual_memory(
        self,
        content: str,
        layer: MemoryLayer = MemoryLayer.PROJECT,
        category: MemoryCategory = MemoryCategory.NOTE,
        subject: str = "",
        project_id: str = "",
        confidence: float = 1.0,
    ) -> MemoryEntry:
        """手动创建记忆条目（用于预置项目知识）。"""
        return MemoryEntry(
            memory_id=self._store.generate_id(),
            layer=layer,
            category=category,
            subject=subject,
            content=content,
            confidence=confidence,
            project_id=project_id,
        )
