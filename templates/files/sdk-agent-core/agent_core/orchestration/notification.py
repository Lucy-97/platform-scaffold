"""
JSON 结果回注 — 后台任务完成通知
==================================

将已完成的后台任务结果以 JSON 格式注入主 Agent 的消息队列。
主 Agent 在下一轮推理时自然读到结构化通知，无需回调。

设计决策：
  - JSON 而非 XML：Qwen / DeepSeek / vLLM 对 JSON 理解力更强
  - user role 消息：确保 LLM 能在对话上下文中看到
"""

import json
from typing import Any, Dict, List

from loguru import logger

from .task_state import AgentTaskState


def build_task_notification(task: AgentTaskState) -> Dict[str, Any]:
    """构造单条任务完成通知——作为 user message 注入消息队列。

    Returns:
        OpenAI 兼容格式的 message dict（role=user）。

    NOTE: 使用 user role 而非 system role，因为部分模型（如 Qwen）
    在多轮对话中对中间插入的 system 消息处理不稳定。
    通知内容用 [系统通知] 前缀和 JSON 格式帮助 LLM 识别。
    """
    notification_payload = {
        "type": "background_task_notification",
        "task_id": task.id,
        "task_type": task.task_type.value,
        "status": task.status.value,
        "description": task.description,
        "result": task.result_summary or "",
        "error": task.error_message or "",
    }

    return {
        "role": "user",
        "content": (
            f"[系统通知] 后台任务完成，请根据结果继续工作：\n"
            f"{json.dumps(notification_payload, ensure_ascii=False, indent=2)}"
        ),
    }


async def inject_completed_notifications(
    messages: List[Dict[str, Any]],
    task_store: Any,
    project_id: str,
) -> int:
    """一站式函数：检查已完成任务 → 注入通知 → 标记已通知。

    在 AgentRuntime.run() 主循环每轮开始前调用。

    Args:
        messages: 当前消息历史（原地修改，追加通知消息）。
        task_store: TaskStore 实例。
        project_id: 当前项目 ID（多租户隔离）。

    Returns:
        本次注入的通知数量。
    """
    completed_tasks = await task_store.get_completed_unnotified(project_id)

    if not completed_tasks:
        return 0

    for task in completed_tasks:
        notification_msg = build_task_notification(task)
        messages.append(notification_msg)
        await task_store.mark_notified(task.id)
        logger.info(
            f"[Notification] 注入任务通知 {task.id} "
            f"({task.task_type.value}/{task.status.value})"
        )

    return len(completed_tasks)
