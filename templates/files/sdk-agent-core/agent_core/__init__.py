"""
agent-core — 通用 AI Agent 运行时框架
==========================================

提供可复用的 Agent 运行时基础设施（纯机制，不含业务策略）：

- ``agent_core.runtime``     — V2 流式事件引擎 + 7 阶段 Hook
- ``agent_core.compaction``  — 多阶段 Token 治理管线
- ``agent_core.memory``      — 五层记忆体系 (L1~L5)
- ``agent_core.skills``      — 双轨技能体系 (Prompt + Code)
- ``agent_core.tools``       — 微内核工具注册 + 安全拦截
- ``agent_core.vfs``         — 虚拟文件系统 (L0/L1/L2 分层)
- ``agent_core.llm``         — LLM 客户端抽象 (litellm)
- ``agent_core.execution``   — Docker 代码沙箱 + 子代理
- ``agent_core.orchestration`` — Redis 后台任务编排
- ``agent_core.evaluation``  — Dev-QA 闭环验证
- ``agent_core.channels``    — IM 渠道适配
- ``agent_core.agent_dsl``   — Agent 模板 Markdown DSL 解析
- ``agent_core.config``      — 运行时配置 (pydantic-settings)

安装::

    pip install agent-core
    pip install "agent-core[neo4j]"   # GraphRAG + Neo4j
    pip install "agent-core[all]"     # 全部可选依赖
"""

__version__ = "0.2.0"

