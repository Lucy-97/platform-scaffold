"""
微内核工具框架单元测试
========================

覆盖 AgentCoreRobustTool / @agent_tool / ToolRegistry / ToolExecutor 全链路。

运行方式：
    cd agent-core
    pip install pytest pytest-asyncio
    python -m pytest tests/test_tools.py -v
"""

import asyncio
import json
import re

import pytest
import pytest_asyncio

from agent_core.tools.base import AgentCoreRobustTool, ToolExecutionResult, ToolSafetyLevel
from agent_core.tools.decorator import agent_tool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.executor import ToolExecutor


# ═══════════════════════════════════════════════════════════════════════════
# 辅助：用于测试的 mock 工具
# ═══════════════════════════════════════════════════════════════════════════

class ReadFileTool(AgentCoreRobustTool):
    """只读工具 — 用于测试 SAFE 级别。"""
    name = "read_file"
    description = "读取文件内容"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    safety_level = ToolSafetyLevel.SAFE
    concurrency_safe = True

    async def call(self, args, ctx):
        return json.dumps({"content": f"file content of {args['path']}"}, ensure_ascii=False)


class DeleteFileTool(AgentCoreRobustTool):
    """高危工具 — 用于测试 DESTRUCTIVE 级别。"""
    name = "delete_file"
    description = "删除文件"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    safety_level = ToolSafetyLevel.DESTRUCTIVE
    concurrency_safe = False

    def get_activity_description(self, input_data):
        return f"🗑️ 正在删除: {input_data.get('path', '?')}"

    async def call(self, args, ctx):
        return json.dumps({"status": "deleted", "path": args["path"]}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════
# 1. AgentCoreRobustTool 抽象基类测试
# ═══════════════════════════════════════════════════════════════════════════

class TestAgentCoreRobustTool:
    """AgentCoreRobustTool 抽象基类行为测试。"""

    def test_cannot_instantiate_abstract(self):
        """不能直接实例化抽象基类。"""
        with pytest.raises(TypeError):
            AgentCoreRobustTool()

    def test_safe_tool_is_read_only(self):
        """SAFE 级别工具默认 is_read_only=True。"""
        tool = ReadFileTool()
        assert tool.is_read_only({"path": "/tmp/test.txt"}) is True
        assert tool.is_destructive({"path": "/tmp/test.txt"}) is False

    def test_destructive_tool_defaults(self):
        """DESTRUCTIVE 级别工具默认 is_destructive=True。"""
        tool = DeleteFileTool()
        assert tool.is_destructive({"path": "/tmp/test.txt"}) is True
        assert tool.is_read_only({"path": "/tmp/test.txt"}) is False

    def test_concurrency_safe_flag(self):
        """concurrency_safe 属性应正确传播。"""
        assert ReadFileTool().is_concurrency_safe() is True
        assert DeleteFileTool().is_concurrency_safe() is False

    def test_to_openai_definition(self):
        """to_openai_definition 应生成标准 JSON Schema。"""
        tool = ReadFileTool()
        defn = tool.to_openai_definition()
        assert defn["type"] == "function"
        assert defn["function"]["name"] == "read_file"
        assert "properties" in defn["function"]["parameters"]

    def test_default_activity_description(self):
        """默认 UI 钩子应返回工具名称。"""
        tool = ReadFileTool()
        desc = tool.get_activity_description({"path": "/tmp"})
        assert "read_file" in desc

    def test_custom_activity_description(self):
        """子类覆盖的 UI 钩子应返回自定义描述。"""
        tool = DeleteFileTool()
        desc = tool.get_activity_description({"path": "/tmp/foo.txt"})
        assert "foo.txt" in desc

    @pytest.mark.asyncio
    async def test_call_returns_str(self):
        """call() 应返回 JSON 字符串。"""
        tool = ReadFileTool()
        result = await tool.call({"path": "/tmp/test.txt"}, {})
        data = json.loads(result)
        assert "content" in data


# ═══════════════════════════════════════════════════════════════════════════
# 2. @agent_tool 装饰器测试
# ═══════════════════════════════════════════════════════════════════════════

class TestAigcToolDecorator:
    """@agent_tool 装饰器行为测试。"""

    def test_basic_decoration(self):
        """装饰后应生成 AgentCoreRobustTool 实例。"""
        @agent_tool(
            name="test_tool",
            description="测试装饰器",
            parameters={"type": "object", "properties": {}},
        )
        async def my_handler(args, ctx):
            return "ok"

        assert isinstance(my_handler, AgentCoreRobustTool)
        assert my_handler.name == "test_tool"
        assert my_handler.description == "测试装饰器"

    def test_safety_level_propagation(self):
        """safety_level 应正确传播到实例。"""
        @agent_tool(
            name="safe_tool",
            description="安全工具",
            parameters={"type": "object", "properties": {}},
            safety_level=ToolSafetyLevel.SAFE,
        )
        async def my_handler(args, ctx):
            return "ok"

        assert my_handler.safety_level == ToolSafetyLevel.SAFE
        assert my_handler.is_read_only({}) is True

    def test_dynamic_destructive_callback(self):
        """lambda 回调应支持基于输入的动态判定。"""
        @agent_tool(
            name="bash",
            description="bash 命令",
            parameters={"type": "object", "properties": {}},
            # 动态判定：包含 "rm -rf" 时为高危
            is_destructive=lambda args: "rm -rf" in args.get("command", ""),
            is_read_only=lambda args: bool(re.match(r"^\s*(ls|cat)", args.get("command", ""))),
        )
        async def handle_bash(args, ctx):
            return "ok"

        # 安全命令
        assert handle_bash.is_destructive({"command": "ls -la"}) is False
        assert handle_bash.is_read_only({"command": "ls -la"}) is True

        # 高危命令
        assert handle_bash.is_destructive({"command": "rm -rf /"}) is True
        assert handle_bash.is_read_only({"command": "rm -rf /"}) is False

    def test_fixed_bool_callbacks(self):
        """固定 bool 值应作为回调使用。"""
        @agent_tool(
            name="always_safe",
            description="始终安全",
            parameters={"type": "object", "properties": {}},
            is_read_only=True,
            is_destructive=False,
        )
        async def my_handler(args, ctx):
            return "ok"

        assert my_handler.is_read_only({"any": "input"}) is True
        assert my_handler.is_destructive({"any": "input"}) is False

    def test_ui_hook_lambda(self):
        """lambda UI 钩子应正确工作。"""
        @agent_tool(
            name="render",
            description="渲染",
            parameters={"type": "object", "properties": {}},
            ui_hook=lambda args: f"🎬 渲染 {args.get('scene', '?')}",
        )
        async def my_handler(args, ctx):
            return "ok"

        desc = my_handler.get_activity_description({"scene": "第三幕"})
        assert "第三幕" in desc

    def test_ui_hook_fixed_str(self):
        """固定字符串 UI 钩子应正确工作。"""
        @agent_tool(
            name="scan",
            description="扫描",
            parameters={"type": "object", "properties": {}},
            ui_hook="🔍 正在扫描...",
        )
        async def my_handler(args, ctx):
            return "ok"

        assert my_handler.get_activity_description({}) == "🔍 正在扫描..."

    @pytest.mark.asyncio
    async def test_call_invokes_handler(self):
        """call() 应正确调用原函数。"""
        @agent_tool(
            name="echo",
            description="回声",
            parameters={"type": "object", "properties": {}},
        )
        async def echo_handler(args, ctx):
            return json.dumps({"echo": args.get("msg", "")})

        result = await echo_handler.call({"msg": "hello"}, {})
        assert json.loads(result)["echo"] == "hello"

    @pytest.mark.asyncio
    async def test_sync_handler_support(self):
        """装饰器应兼容同步 handler。"""
        @agent_tool(
            name="sync_tool",
            description="同步工具",
            parameters={"type": "object", "properties": {}},
        )
        def sync_handler(args, ctx):
            return "sync_ok"

        result = await sync_handler.call({}, {})
        assert result == "sync_ok"

    def test_to_openai_definition(self):
        """装饰后工具应能正确生成 OpenAI Schema。"""
        @agent_tool(
            name="my_tool",
            description="我的工具",
            parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        async def my_handler(args, ctx):
            return "ok"

        defn = my_handler.to_openai_definition()
        assert defn["function"]["name"] == "my_tool"
        assert "x" in defn["function"]["parameters"]["properties"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. ToolRegistry 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestToolRegistry:
    """ToolRegistry 注册中心测试。"""

    def test_register_and_get(self):
        """注册后应能按名称查找。"""
        registry = ToolRegistry()
        tool = ReadFileTool()
        registry.register(tool)
        assert registry.get("read_file") is tool

    def test_duplicate_register_raises(self):
        """重复注册同名工具应抛出 ValueError。"""
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        with pytest.raises(ValueError, match="已注册"):
            registry.register(ReadFileTool())

    def test_get_nonexistent_returns_none(self):
        """查找不存在的工具应返回 None。"""
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_get_all_definitions(self):
        """get_all_definitions 应返回所有工具的 OpenAI Schema。"""
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        registry.register(DeleteFileTool())

        defs = registry.get_all_definitions()
        assert len(defs) == 2
        names = {d["function"]["name"] for d in defs}
        assert names == {"read_file", "delete_file"}

    def test_get_all_names(self):
        """get_all_names 应返回所有工具名称。"""
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        registry.register(DeleteFileTool())
        assert set(registry.get_all_names()) == {"read_file", "delete_file"}

    def test_contains(self):
        """__contains__ 应支持 'in' 语法。"""
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        assert "read_file" in registry
        assert "nonexistent" not in registry

    def test_len(self):
        """__len__ 应返回已注册工具数量。"""
        registry = ToolRegistry()
        assert len(registry) == 0
        registry.register(ReadFileTool())
        assert len(registry) == 1

    def test_register_legacy(self):
        """register_legacy 应将裸函数包装为微内核工具。"""
        registry = ToolRegistry()

        async def my_handler(args, ctx):
            return "legacy_ok"

        registry.register_legacy(
            name="legacy_tool",
            description="旧式工具",
            parameters={"type": "object", "properties": {}},
            handler=my_handler,
        )

        tool = registry.get("legacy_tool")
        assert tool is not None
        assert tool.name == "legacy_tool"
        # 旧式工具默认为 MODERATE
        assert tool.safety_level == ToolSafetyLevel.MODERATE

    @pytest.mark.asyncio
    async def test_legacy_handler_call(self):
        """旧式包装工具的 call() 应正确调用原函数。"""
        registry = ToolRegistry()

        async def my_handler(args, ctx):
            return json.dumps({"result": args.get("x", 0) * 2})

        registry.register_legacy("double", "加倍", {"type": "object", "properties": {}}, my_handler)
        tool = registry.get("double")
        result = await tool.call({"x": 21}, {})
        assert json.loads(result)["result"] == 42

    @pytest.mark.asyncio
    async def test_legacy_handler_single_arg(self):
        """旧式工具应兼容只接受 args 一个参数的 handler。"""
        registry = ToolRegistry()

        async def single_arg_handler(args):
            return "single_ok"

        registry.register_legacy("single", "单参数", {"type": "object", "properties": {}}, single_arg_handler)
        tool = registry.get("single")
        result = await tool.call({}, {})
        assert result == "single_ok"


# ═══════════════════════════════════════════════════════════════════════════
# 4. ToolExecutor 执行管线测试
# ═══════════════════════════════════════════════════════════════════════════

class TestToolExecutor:
    """ToolExecutor 执行管线测试。"""

    @pytest.mark.asyncio
    async def test_safe_tool_no_approval(self):
        """SAFE 工具应直接执行，不触发审批。"""
        approval_called = False

        async def mock_approval(name, args):
            nonlocal approval_called
            approval_called = True
            return True

        executor = ToolExecutor(approval_callback=mock_approval)
        tool = ReadFileTool()

        result = await executor.execute(tool, {"path": "/tmp/test"}, {})
        assert result.status == "ok"
        assert approval_called is False  # SAFE 工具不触发审批

    @pytest.mark.asyncio
    async def test_destructive_tool_triggers_approval(self):
        """DESTRUCTIVE 工具应触发审批回调。"""
        approval_called = False

        async def mock_approval(name, args):
            nonlocal approval_called
            approval_called = True
            return True

        executor = ToolExecutor(approval_callback=mock_approval)
        tool = DeleteFileTool()

        result = await executor.execute(tool, {"path": "/tmp/test"}, {})
        assert result.status == "ok"
        assert approval_called is True
        assert result.was_approved is True

    @pytest.mark.asyncio
    async def test_destructive_denied(self):
        """用户拒绝高危操作应返回 denied。"""
        async def mock_deny(name, args):
            return False

        executor = ToolExecutor(approval_callback=mock_deny)
        tool = DeleteFileTool()

        result = await executor.execute(tool, {"path": "/tmp/test"}, {})
        assert result.status == "denied"
        assert result.was_approved is False
        assert "拒绝" in result.content

    @pytest.mark.asyncio
    async def test_destructive_no_callback_continues(self):
        """无审批回调时高危操作应仅日志告警后继续执行。"""
        executor = ToolExecutor()  # 不提供 approval_callback
        tool = DeleteFileTool()

        result = await executor.execute(tool, {"path": "/tmp/test"}, {})
        assert result.status == "ok"
        assert result.was_approved is None  # 未经过审批

    @pytest.mark.asyncio
    async def test_sse_callback_called(self):
        """SSE 推送回调应被调用。"""
        sse_events = []

        async def mock_sse(event):
            sse_events.append(event)

        executor = ToolExecutor(sse_callback=mock_sse)
        tool = ReadFileTool()

        await executor.execute(tool, {"path": "/tmp"}, {})
        assert len(sse_events) == 1
        assert sse_events[0]["type"] == "tool_activity"
        assert sse_events[0]["tool_name"] == "read_file"

    @pytest.mark.asyncio
    async def test_execution_error_handled(self):
        """工具执行异常应被安全封装为 error 结果。"""
        @agent_tool(
            name="explode",
            description="会爆炸的工具",
            parameters={"type": "object", "properties": {}},
            safety_level=ToolSafetyLevel.SAFE,
        )
        async def explode_handler(args, ctx):
            raise RuntimeError("💥 BOOM!")

        executor = ToolExecutor()
        result = await executor.execute(explode_handler, {}, {})
        assert result.status == "error"
        assert "BOOM" in result.content

    @pytest.mark.asyncio
    async def test_duration_ms_tracked(self):
        """执行耗时应被记录。"""
        @agent_tool(
            name="slow",
            description="慢工具",
            parameters={"type": "object", "properties": {}},
            safety_level=ToolSafetyLevel.SAFE,
        )
        async def slow_handler(args, ctx):
            await asyncio.sleep(0.05)
            return "done"

        executor = ToolExecutor()
        result = await executor.execute(slow_handler, {}, {})
        assert result.duration_ms >= 40  # 至少 40ms

    @pytest.mark.asyncio
    async def test_dynamic_destructive_via_decorator(self):
        """通过装饰器声明的动态 is_destructive 应被管线识别。"""
        approval_log = []

        async def mock_approval(name, args):
            approval_log.append(args)
            return True

        @agent_tool(
            name="bash",
            description="bash",
            parameters={"type": "object", "properties": {}},
            is_destructive=lambda args: "rm -rf" in args.get("cmd", ""),
            is_read_only=lambda args: args.get("cmd", "").startswith("ls"),
        )
        async def bash_handler(args, ctx):
            return "executed"

        executor = ToolExecutor(approval_callback=mock_approval)

        # 安全命令 → 不触发审批
        r1 = await executor.execute(bash_handler, {"cmd": "ls -la"}, {})
        assert r1.status == "ok"
        assert len(approval_log) == 0

        # 高危命令 → 触发审批
        r2 = await executor.execute(bash_handler, {"cmd": "rm -rf /"}, {})
        assert r2.status == "ok"
        assert len(approval_log) == 1

    @pytest.mark.asyncio
    async def test_concurrency_safe_false_serializes(self):
        """concurrency_safe=False 的工具应串行执行（不会同时进入 call）。"""
        call_count = 0
        max_concurrent = 0

        @agent_tool(
            name="serial_tool",
            description="串行工具",
            parameters={"type": "object", "properties": {}},
            safety_level=ToolSafetyLevel.SAFE,
            concurrency_safe=False,
        )
        async def serial_handler(args, ctx):
            nonlocal call_count, max_concurrent
            call_count += 1
            current = call_count
            await asyncio.sleep(0.02)
            max_concurrent = max(max_concurrent, current)
            call_count -= 1
            return "ok"

        executor = ToolExecutor()

        # 并发提交 3 个请求
        results = await asyncio.gather(
            executor.execute(serial_handler, {}, {}),
            executor.execute(serial_handler, {}, {}),
            executor.execute(serial_handler, {}, {}),
        )

        # 所有都应成功
        assert all(r.status == "ok" for r in results)
        # 全局锁保证同时只执行一个 — max_concurrent 不应超过 1
        assert max_concurrent <= 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. 向后兼容测试
# ═══════════════════════════════════════════════════════════════════════════

class TestBackwardCompat:
    """确保旧式注册方式在新架构下正常工作。"""

    @pytest.mark.asyncio
    async def test_legacy_registration_via_runtime(self):
        """通过 AgentRuntime.register_tools() 注册的旧式工具应正常工作。"""
        from agent_core.runtime import AgentRuntime

        runtime = AgentRuntime(model="test", api_key="test")

        async def my_handler(args):
            return json.dumps({"result": "legacy"})

        defs = [{
            "type": "function",
            "function": {
                "name": "test_legacy",
                "description": "旧接口测试",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        handlers = {"test_legacy": my_handler}

        runtime.register_tools(defs, handlers)

        # 应能在注册表中找到
        assert "test_legacy" in runtime.tool_names
        tool = runtime.tool_registry.get("test_legacy")
        assert tool is not None

        # 应能正常执行
        result = await tool.call({}, {})
        assert json.loads(result)["result"] == "legacy"

    @pytest.mark.asyncio
    async def test_robust_tool_registration_via_runtime(self):
        """通过 AgentRuntime.register_robust_tool() 注册的微内核工具应正常工作。"""
        from agent_core.runtime import AgentRuntime

        runtime = AgentRuntime(model="test", api_key="test")
        runtime.register_robust_tool(ReadFileTool())

        assert "read_file" in runtime.tool_names
        tool = runtime.tool_registry.get("read_file")
        assert tool.safety_level == ToolSafetyLevel.SAFE


# ═══════════════════════════════════════════════════════════════════════════
# 6. 模块导入完整性测试
# ═══════════════════════════════════════════════════════════════════════════

class TestModuleImport:
    """确保 tools 包可正常导入。"""

    def test_all_exports(self):
        """__all__ 中的所有名称应可导入。"""
        from agent_core import tools
        for name in tools.__all__:
            assert hasattr(tools, name), f"缺少导出: {name}"

    def test_direct_imports(self):
        """主要组件应可直接从 agent_core.tools 导入。"""
        from agent_core.tools import (
            AgentCoreRobustTool,
            ToolSafetyLevel,
            ToolExecutionResult,
            agent_tool,
            ToolRegistry,
            ToolExecutor,
        )
        assert AgentCoreRobustTool is not None
