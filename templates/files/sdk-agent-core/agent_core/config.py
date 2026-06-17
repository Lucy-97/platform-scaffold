"""
Agent 运行时 YAML 配置系统 — AgentConfig
==========================================

借鉴 DeerFlow 2.0 的配置管理模式：
  - 使用 YAML 文件管理 Agent 运行时配置（与 .env 互补）
  - 支持 $ENV_VAR 环境变量解析
  - 通过 mtime 检测实现热重载（无需重启服务）
  - config_version 字段用于配置格式升级管理

与 .env 的分工：
  - .env: 基础设施级别（数据库、Redis、端口、密钥等）
  - agent_config.yaml: Agent 行为级别（中间件参数、工具开关、记忆策略等）
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from loguru import logger
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 配置模型（Pydantic）
# ---------------------------------------------------------------------------

class LoopDetectionConfig(BaseModel):
    """循环检测中间件配置。"""
    enabled: bool = Field(default=True, description="是否启用循环检测")
    warn_threshold: int = Field(default=3, description="警告阈值：同一 hash 出现次数")
    hard_limit: int = Field(default=5, description="强制停止阈值")
    window_size: int = Field(default=20, description="滑动窗口大小")


class SummarizationConfig(BaseModel):
    """上下文摘要中间件配置。"""
    enabled: bool = Field(default=True, description="是否启用上下文摘要")
    trigger_tokens: int = Field(default=15000, description="触发摘要的 token 阈值")
    trigger_messages: int = Field(default=30, description="触发摘要的消息数阈值")
    keep_recent: int = Field(default=10, description="保留的最近消息数")


class MemoryConfig(BaseModel):
    """长期记忆系统配置。"""
    enabled: bool = Field(default=True, description="是否启用长期记忆")
    debounce_seconds: float = Field(default=30.0, description="去抖等待秒数")
    max_facts: int = Field(default=100, description="每用户每Agent最大事实数")
    confidence_threshold: float = Field(default=0.3, description="事实置信度过滤阈值")
    max_injection_facts: int = Field(default=15, description="每次最多注入的事实数")


class SkillInjectionConfig(BaseModel):
    """技能注入中间件配置 (借鉴 superpowers SKILL.md)。"""
    enabled: bool = Field(default=True, description="是否启用技能自动注入")
    max_skills: int = Field(default=5, description="每次最多注入的技能数")


class SubAgentDelegationConfig(BaseModel):
    """子代理委派中间件配置 (借鉴 deepagents + superpowers)。"""
    enabled: bool = Field(default=True, description="是否启用子代理委派")
    max_delegations_per_turn: int = Field(default=3, description="每轮最多委派次数")


class MiddlewareConfig(BaseModel):
    """中间件链总配置。"""
    loop_detection: LoopDetectionConfig = Field(
        default_factory=LoopDetectionConfig,
        description="循环检测配置",
    )
    summarization: SummarizationConfig = Field(
        default_factory=SummarizationConfig,
        description="上下文摘要配置",
    )
    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description="长期记忆配置",
    )
    skill_injection: SkillInjectionConfig = Field(
        default_factory=SkillInjectionConfig,
        description="技能注入配置 (借鉴 superpowers)",
    )
    subagent_delegation: SubAgentDelegationConfig = Field(
        default_factory=SubAgentDelegationConfig,
        description="子代理委派配置 (借鉴 deepagents)",
    )


class ToolConfig(BaseModel):
    """单个工具的配置。"""
    enabled: bool = Field(default=True, description="是否启用")
    timeout: int = Field(default=10, description="执行超时秒数")


class ToolsConfig(BaseModel):
    """通用工具总配置。"""
    search_web: ToolConfig = Field(
        default_factory=lambda: ToolConfig(enabled=True, timeout=15),
        description="网页搜索配置",
    )
    web_fetch: ToolConfig = Field(
        default_factory=lambda: ToolConfig(enabled=True, timeout=20),
        description="网页抓取配置",
    )
    bash: ToolConfig = Field(
        default_factory=lambda: ToolConfig(enabled=True, timeout=10),
        description="Bash 命令配置",
    )
    exec_code: ToolConfig = Field(
        default_factory=lambda: ToolConfig(enabled=True, timeout=10),
        description="代码执行配置",
    )


class AgentConfig(BaseModel):
    """Agent 运行时 YAML 配置根模型。

    配置文件格式示例::

        config_version: 1
        middleware:
          loop_detection:
            enabled: true
            warn_threshold: 3
          memory:
            enabled: true
            debounce_seconds: 30
        tools:
          search_web:
            enabled: true
    """
    config_version: int = Field(default=1, description="配置版本号，用于升级管理")
    middleware: MiddlewareConfig = Field(
        default_factory=MiddlewareConfig,
        description="中间件配置",
    )
    tools: ToolsConfig = Field(
        default_factory=ToolsConfig,
        description="工具配置",
    )


# ---------------------------------------------------------------------------
# 环境变量解析
# ---------------------------------------------------------------------------

# 匹配 $VAR_NAME 或 ${VAR_NAME} 格式
_ENV_VAR_PATTERN = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)\}?")


def _resolve_env_vars(value: Any) -> Any:
    """递归解析值中的环境变量引用。

    支持 $VAR_NAME 和 ${VAR_NAME} 两种格式。
    仅对字符串类型的值进行解析。

    Args:
        value: 配置值（可以是 str、dict、list 等）。

    Returns:
        解析后的值。
    """
    if isinstance(value, str):
        def _replace(match):
            var_name = match.group(1)
            env_val = os.environ.get(var_name, "")
            if not env_val:
                logger.debug(f"[AgentConfig] Env var ${var_name} not found, using empty string")
            return env_val
        return _ENV_VAR_PATTERN.sub(_replace, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# 配置加载器（热重载单例）
# ---------------------------------------------------------------------------

# 默认配置文件路径
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "agent_config.yaml",
)

# 缓存
_cached_config: Optional[AgentConfig] = None
_cached_mtime: float = 0.0
_cached_path: str = ""


def get_agent_config(
    config_path: Optional[str] = None,
    force_reload: bool = False,
) -> AgentConfig:
    """获取 Agent 运行时配置（带 mtime 热重载）。

    首次调用时加载配置文件，后续调用检查文件修改时间：
      - 未修改：直接返回缓存
      - 已修改：重新加载（热重载）
      - 文件不存在：返回默认配置

    Args:
        config_path: 配置文件路径，默认为 backend/agent_config.yaml。
        force_reload: 是否强制重新加载（忽略缓存）。

    Returns:
        解析后的 AgentConfig 实例。
    """
    global _cached_config, _cached_mtime, _cached_path

    path = config_path or _DEFAULT_CONFIG_PATH

    # 检查文件是否存在
    if not os.path.isfile(path):
        if _cached_config is None or _cached_path != path:
            logger.info(
                f"[AgentConfig] Config file not found: {path}, "
                f"using defaults"
            )
            _cached_config = AgentConfig()
            _cached_path = path
        return _cached_config

    # 检查 mtime 是否变化
    try:
        current_mtime = os.path.getmtime(path)
    except OSError:
        if _cached_config is None:
            _cached_config = AgentConfig()
            _cached_path = path
        return _cached_config

    if (
        not force_reload
        and _cached_config is not None
        and _cached_path == path
        and current_mtime == _cached_mtime
    ):
        return _cached_config

    # 加载并解析
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)

        if not isinstance(raw_data, dict):
            logger.warning(f"[AgentConfig] Invalid YAML format in {path}")
            raw_data = {}

        # 解析环境变量
        resolved = _resolve_env_vars(raw_data)

        # 版本检查
        file_version = resolved.get("config_version", 1)
        if file_version != 1:
            logger.warning(
                f"[AgentConfig] Config version {file_version} != expected 1. "
                f"Some settings may not be recognized."
            )

        config = AgentConfig(**resolved)
        _cached_config = config
        _cached_mtime = current_mtime
        _cached_path = path

        if force_reload:
            logger.info(f"[AgentConfig] Force-reloaded from {path}")
        else:
            logger.info(
                f"[AgentConfig] Loaded config from {path} "
                f"(version={config.config_version})"
            )

        return config

    except Exception as e:
        logger.error(f"[AgentConfig] Failed to load {path}: {e}")
        if _cached_config is None:
            _cached_config = AgentConfig()
            _cached_path = path
        return _cached_config
