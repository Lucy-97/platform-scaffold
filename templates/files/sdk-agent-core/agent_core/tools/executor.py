"""
微内核工具执行管线 — ToolExecutor
==================================

在实际调用 tool.call() 前后插入完整的拦截链：
  参数纠偏 → 安全拦截 → UI 推送 → 并发调度 → 执行 → 结果封装

设计原则：
  - 所有拦截决策内聚在工具自身的元数据中，Executor 只做统一调度
  - approval_callback / sse_callback 为可选注入，未提供时优雅降级
  - 并发不安全的工具通过 asyncio.Lock 自动串行化
  - LLM 传入的参数类型根据 JSON Schema 自动纠偏
"""

import asyncio
import json
import time
from typing import Any, Callable, Coroutine, Dict, Optional, Union

from loguru import logger

from agent_core.tools.base import AgentCoreRobustTool, ToolExecutionResult, ToolSafetyLevel

# 审批回调类型：接收工具名+参数，返回 True（批准）/ False（拒绝）
ApprovalCallback = Callable[[str, Dict[str, Any]], Coroutine[Any, Any, bool]]

# SSE 推送回调类型：接收事件 dict
SSECallback = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class ToolExecutor:
    """微内核工具执行管线。

    在 tool.call() 前后自动走完 参数纠偏 → 安全拦截 → UI 推送 → 并发调度 全链路。

    Args:
        approval_callback: 可选，人类审批回调。未提供时 destructive 工具仅做日志告警。
        sse_callback: 可选，前端状态推送回调。未提供时跳过 UI 推送。
    """

    def __init__(
        self,
        approval_callback: Optional[ApprovalCallback] = None,
        sse_callback: Optional[SSECallback] = None,
    ) -> None:
        self._approval_callback = approval_callback
        self._sse_callback = sse_callback
        # 全局串行锁 — 并发不安全的工具在此锁内执行
        self._serial_lock = asyncio.Lock()

    async def execute(
        self,
        tool: AgentCoreRobustTool,
        args: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> ToolExecutionResult:
        """执行微内核工具的完整管线。

        管线步骤:
          0. 参数纠偏 — 根据 JSON Schema 自动修正 LLM 返回的错误类型
          1. 安全拦截 — is_read_only / is_destructive 动态判定
          2. UI 推送 — get_activity_description 推送前端
          3. 并发调度 — is_concurrency_safe 决定串行/并行
          4. 执行 — tool.call(args, ctx)
          5. 结果封装 — ToolExecutionResult

        Args:
            tool: 微内核工具实例。
            args: LLM 传入的工具参数。
            ctx: 运行时上下文。

        Returns:
            ToolExecutionResult 封装的执行结果。
        """
        t_start = time.monotonic()
        was_approved: Optional[bool] = None

        # ================================================================
        # 步骤 0：参数类型纠偏（v2 新增）
        # LLM 经常将 integer 返回为 "42"、boolean 返回为 "true"
        # 根据工具的 JSON Schema 自动修正，避免不必要的工具执行失败
        # ================================================================

        args = coerce_tool_args(tool.name, args, tool.parameters)

        # ================================================================
        # 步骤 1：安全拦截
        # ================================================================

        # 只读快车道：is_read_only=True → 跳过所有安全检查
        if tool.is_read_only(args):
            logger.debug(
                f"[ToolExecutor] ✅ 只读放行: {tool.name} | "
                f"args_keys={list(args.keys())}"
            )
        # 高危拦截：is_destructive=True → 强制审批
        elif tool.is_destructive(args):
            logger.warning(
                f"[ToolExecutor] 🔴 高危操作检测: {tool.name} | "
                f"args={json.dumps(args, ensure_ascii=False)[:200]}"
            )

            if self._approval_callback:
                # 通过回调请求人类审批
                try:
                    approved = await self._approval_callback(tool.name, args)
                    was_approved = approved
                except Exception as e:
                    # 审批回调异常 → 安全起见视为拒绝
                    logger.error(f"[ToolExecutor] 审批回调异常: {e}")
                    approved = False
                    was_approved = False

                if not approved:
                    logger.info(f"[ToolExecutor] ❌ 用户拒绝高危操作: {tool.name}")
                    return ToolExecutionResult(
                        status="denied",
                        content=json.dumps(
                            {
                                "status": "denied",
                                "message": f"操作被用户拒绝: {tool.name}",
                                "tool": tool.name,
                            },
                            ensure_ascii=False,
                        ),
                        tool_name=tool.name,
                        duration_ms=_elapsed_ms(t_start),
                        was_approved=False,
                        safety_level=ToolSafetyLevel.DESTRUCTIVE,
                    )
                logger.info(f"[ToolExecutor] ✅ 用户批准高危操作: {tool.name}")
            else:
                # 未提供审批回调 → 仅日志告警后继续执行（过渡策略）
                logger.warning(
                    f"[ToolExecutor] ⚠️ 高危操作无审批回调，日志告警后继续: {tool.name}"
                )
                was_approved = None
        else:
            # MODERATE 级别 — 记录日志即可
            logger.info(f"[ToolExecutor] 🔧 执行工具: {tool.name}")

        # ================================================================
        # 步骤 2：UI 推送
        # ================================================================

        if self._sse_callback:
            activity_desc = tool.get_activity_description(args)
            try:
                await self._sse_callback({
                    "type": "tool_activity",
                    "tool_name": tool.name,
                    "description": activity_desc,
                    "safety_level": tool.safety_level.value,
                })
            except Exception as e:
                # SSE 推送失败不应阻塞工具执行
                logger.warning(f"[ToolExecutor] SSE 推送失败: {e}")

        # ================================================================
        # 步骤 3 + 4：并发调度 + 执行
        # ================================================================

        try:
            if not tool.is_concurrency_safe():
                # 并发不安全 → 在全局串行锁内执行
                async with self._serial_lock:
                    result_str = await tool.call(args, ctx)
            else:
                result_str = await tool.call(args, ctx)
        except Exception as e:
            logger.error(f"[ToolExecutor] 工具执行异常: {tool.name} | {e}")
            return ToolExecutionResult(
                status="error",
                content=json.dumps(
                    {"status": "error", "message": f"工具执行异常: {e}"},
                    ensure_ascii=False,
                ),
                tool_name=tool.name,
                duration_ms=_elapsed_ms(t_start),
                was_approved=was_approved,
                safety_level=tool.safety_level,
            )

        # ================================================================
        # 步骤 5：结果封装
        # ================================================================

        return ToolExecutionResult(
            status="ok",
            content=result_str,
            tool_name=tool.name,
            duration_ms=_elapsed_ms(t_start),
            was_approved=was_approved,
            safety_level=tool.safety_level,
        )


def _elapsed_ms(t_start: float) -> float:
    """计算从 t_start 到当前的耗时（毫秒）。"""
    return (time.monotonic() - t_start) * 1000


def coerce_tool_args(
    tool_name: str,
    args: Dict[str, Any],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """根据 JSON Schema 声明自动纠偏 LLM 传入的参数类型。

    LLM 在 function-calling 中常见的类型错误：
      - integer 字段返回 "42" 而非 42
      - boolean 字段返回 "true" / "false" 而非 True / False
      - number 字段返回 "3.14" 而非 3.14

    本函数对比 Schema 中每个属性的 type 声明，
    自动尝试类型转换。转换失败时保留原值（不破坏原始数据）。

    Args:
        tool_name: 工具名称（仅用于日志）。
        args: LLM 传入的参数 dict。
        schema: 工具的 JSON Schema（含 properties 定义）。

    Returns:
        纠偏后的参数 dict（可能是原地修改后的同一对象）。
    """
    properties = schema.get("properties", {})
    if not properties:
        return args

    coerced_keys = []

    for key, value in args.items():
        if key not in properties:
            continue

        prop_schema = properties[key]
        expected_type = prop_schema.get("type", "")

        # 只对字符串值做纠偏（LLM 返回了字符串但 Schema 期望其他类型）
        if not isinstance(value, str):
            continue

        try:
            if expected_type == "integer":
                args[key] = int(value)
                coerced_keys.append(f"{key}: str→int")
            elif expected_type == "number":
                args[key] = float(value)
                coerced_keys.append(f"{key}: str→number")
            elif expected_type == "boolean":
                lower = value.lower().strip()
                if lower in ("true", "1", "yes"):
                    args[key] = True
                    coerced_keys.append(f"{key}: str→bool(True)")
                elif lower in ("false", "0", "no"):
                    args[key] = False
                    coerced_keys.append(f"{key}: str→bool(False)")
            elif expected_type == "array":
                # LLM 可能返回 "[1,2,3]" 字符串而非真实数组
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    args[key] = parsed
                    coerced_keys.append(f"{key}: str→array")
            elif expected_type == "object":
                # LLM 可能返回 JSON 字符串而非真实 dict
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    args[key] = parsed
                    coerced_keys.append(f"{key}: str→object")
        except (ValueError, TypeError, json.JSONDecodeError):
            # 转换失败 — 保留原值，不破坏数据
            pass

    if coerced_keys:
        logger.debug(
            f"[ToolExecutor] 参数纠偏 {tool_name}: "
            f"{', '.join(coerced_keys)}"
        )

    return args
