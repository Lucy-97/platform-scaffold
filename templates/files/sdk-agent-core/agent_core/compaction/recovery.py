"""
截断自愈续写 — reactive_token_recovery
==========================================

当 LLM 返回 finish_reason='length'（超过 max_tokens 被截断）时，
自动检测并发起续写请求，拼接为完整输出。

借鉴 Claude Code 的 reactive token recovery 策略：
  不直接丢弃截断结果，而是追加一条续写指令，
  让 LLM 从截断点继续，然后自动拼接。
"""

from typing import Any, Dict, List, Optional

from loguru import logger


async def reactive_token_recovery(
    model: str,
    api_key: str,
    messages: List[Dict[str, Any]],
    truncated_content: str,
    max_retries: int = 2,
    api_base: Optional[str] = None,
) -> str:
    """截断自愈续写——检测到 length 截断时自动续写拼接。

    工作流程：
      1. 将已截断的输出追加到 messages
      2. 添加续写指令消息
      3. 发起新一轮 LLM 调用
      4. 拼接截断部分和续写部分

    Args:
        model: LLM 模型。
        api_key: API Key。
        messages: 原始消息列表。
        truncated_content: 被截断的文本。
        max_retries: 最大续写次数（防止无限循环）。
        api_base: API Base URL。

    Returns:
        拼接后的完整文本。
    """
    import litellm

    full_text = truncated_content
    attempts = 0

    while attempts < max_retries:
        attempts += 1
        logger.info(
            f"[Recovery] 截断自愈第 {attempts} 次续写 "
            f"(已有 {len(full_text)} 字符)"
        )

        # 构建续写消息
        recovery_messages = messages.copy()
        recovery_messages.append({
            "role": "assistant",
            "content": full_text,
        })
        recovery_messages.append({
            "role": "user",
            "content": (
                "[系统] 你的上一次回复因为长度限制被截断了。"
                "请从截断点继续输出。"
                "不要重复已输出的内容，直接接续。"
            ),
        })

        try:
            call_params: Dict[str, Any] = {
                "model": model,
                "messages": recovery_messages,
                "api_key": api_key,
                "temperature": 0.3,  # 续写用较低温度保持一致性
                "max_tokens": 4096,
            }
            if api_base:
                call_params["api_base"] = api_base

            response = await litellm.acompletion(**call_params)
            continuation = response.choices[0].message.content or ""
            finish_reason = response.choices[0].finish_reason

            # 拼接
            full_text += continuation

            logger.info(
                f"[Recovery] 续写完成: +{len(continuation)} 字符, "
                f"finish_reason={finish_reason}"
            )

            # 如果本次没被截断，说明已完整
            if finish_reason != "length":
                break

        except Exception as e:
            logger.error(f"[Recovery] 续写失败: {e}")
            break

    return full_text
