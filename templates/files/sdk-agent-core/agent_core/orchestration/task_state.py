"""
统一后台任务状态模型 — AgentTaskType / AgentTaskStatus / AgentTaskState
=======================================================================

借鉴 Claude Code CLI 的 Task.ts 统一抽象，将所有异构后台执行体
（ComfyUI 渲染、TTS 合成、ffmpeg 脚本、并行子 Agent、后台记忆整理）
收敛进同一个 5 态有限状态机。

核心设计：
  - Pydantic V2 模型，可直接序列化到 Redis Hash
  - 多租户字段（project_id / user_id），支持按项目隔离查询
  - 前缀 ID 生成，日志中一眼辨别任务来源
"""

import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AgentTaskType(str, Enum):
    """后台执行体类型枚举（按 AgentCore 业务场景定制）"""
    COMFYUI_RENDER = "comfyui_render"   # ComfyUI 生图/视频渲染
    TTS_SYNTHESIS = "tts_synthesis"      # 语音合成（阿里 TTS / CosyVoice）
    BASH_JOB = "bash_job"               # 本地脚本（ffmpeg / 打包 / 转码）
    SUB_AGENT = "sub_agent"             # 并行推理子 Agent
    MEMORY_CLEANUP = "memory_cleanup"   # 后台记忆整理 / VFS 清理


class AgentTaskStatus(str, Enum):
    """统一 5 态状态机"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


def is_terminal(status: AgentTaskStatus) -> bool:
    """判断是否为终态——所有轮询 / 清理 / 回注逻辑的唯一判断入口。

    借鉴 Claude Code 的 isTerminalTaskStatus() 守卫函数，
    避免在代码各处重复写 status == COMPLETED or status == FAILED ...
    """
    return status in (
        AgentTaskStatus.COMPLETED,
        AgentTaskStatus.FAILED,
        AgentTaskStatus.KILLED,
    )


# ID 前缀映射——便于从日志/Redis key 中快速辨别任务来源
_PREFIX_MAP = {
    AgentTaskType.COMFYUI_RENDER: "cr",
    AgentTaskType.TTS_SYNTHESIS: "ts",
    AgentTaskType.BASH_JOB: "bj",
    AgentTaskType.SUB_AGENT: "sa",
    AgentTaskType.MEMORY_CLEANUP: "mc",
}


def generate_task_id(task_type: AgentTaskType) -> str:
    """生成带业务前缀的任务 ID（如 cr_a1f2c3d4）。

    前缀设计借鉴 Claude Code 的 TASK_ID_PREFIXES，
    生产环境日志里看到 cr_ 就知道是 ComfyUI 渲染。
    """
    prefix = _PREFIX_MAP.get(task_type, "x")
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class AgentTaskState(BaseModel):
    """统一任务状态骨架——所有异构后台执行体在 Redis 中只暴露这张标准身份证。

    设计要点：
      - 多租户隔离：project_id / user_id 字段
      - 输出通道：output_stream_key 指向 Redis Stream（替代 Claude 的本地文件）
      - 溯源：triggered_by_tool_id 记录由哪个 tool_call 发起
    """
    id: str = Field(description="带前缀的任务 ID（如 cr_a1f2c3d4）")
    task_type: AgentTaskType = Field(description="执行体类型")
    status: AgentTaskStatus = Field(
        default=AgentTaskStatus.PENDING,
        description="当前状态（5 态 FSM）",
    )
    description: str = Field(description="人类可读描述，直接驱动前端进度条 UI")

    # 多租户隔离
    project_id: Optional[str] = Field(default=None, description="项目 ID（多租户隔离）")
    user_id: Optional[str] = Field(default=None, description="用户 ID（多租户隔离）")

    # 溯源
    triggered_by_tool_id: Optional[str] = Field(
        default=None,
        description="由主 Agent 的哪个 tool_call_id 触发",
    )

    # 时间戳
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None

    # 输出通道——Redis Stream key（替代 Claude 的 outputFile）
    output_stream_key: str = Field(
        default="",
        description="Redis Stream key，格式：agent:task_output:{id}",
    )

    # 通知控制
    notified: bool = Field(default=False, description="防重复通知位")

    # 结果/错误
    result_summary: Optional[str] = Field(default=None, description="终态时的结果摘要")
    error_message: Optional[str] = Field(default=None, description="失败时的错误信息")

    def model_post_init(self, __context) -> None:
        """自动填充 output_stream_key（如果未显式指定）"""
        if not self.output_stream_key:
            self.output_stream_key = f"agent:task_output:{self.id}"
