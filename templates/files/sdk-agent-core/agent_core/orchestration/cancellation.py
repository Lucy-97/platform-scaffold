"""
级联取消链 — CascadingCancel
================================

移植 Claude Code CLI 的 parentAbortController → childAbortController 级联模式。
使用 asyncio.Event 实现：父任务取消时自动取消所有子孙任务。

使用场景：
  - 用户在前端点击"取消"当前项目 → 主 Agent 取消 → 所有子渲染/子合成任务自动中止
  - 父子 Agent 级联：父被杀时子自动退出
"""

import asyncio
from typing import List, Optional


class CascadingCancel:
    """级联取消链——基于 asyncio.Event 实现树状取消传播。

    用法::

        # 创建根取消器
        root = CascadingCancel()

        # 派生子级取消器
        child_a = root.create_child()
        child_b = root.create_child()
        grandchild = child_a.create_child()

        # 取消根 → child_a / child_b / grandchild 全部自动取消
        root.cancel()

        assert child_a.is_cancelled
        assert grandchild.is_cancelled
    """

    def __init__(self, parent: Optional["CascadingCancel"] = None) -> None:
        self._event = asyncio.Event()
        self._children: List["CascadingCancel"] = []

        if parent is not None:
            parent._children.append(self)
            # 启动监听协程——等待父级取消信号
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._watch_parent(parent._event))
            except RuntimeError:
                # 没有运行中的事件循环时（如单元测试），跳过自动监听
                pass

    async def _watch_parent(self, parent_event: asyncio.Event) -> None:
        """后台监听父级取消信号，收到后级联取消自身。"""
        await parent_event.wait()
        self.cancel()

    def cancel(self) -> None:
        """取消自身，并**同步地**级联取消所有子级。

        同步设计是有意为之——确保取消传播是瞬时的，
        不会因为事件循环调度延迟导致子任务在父已取消后还跑了几轮。
        """
        self._event.set()
        for child in self._children:
            child.cancel()

    def create_child(self) -> "CascadingCancel":
        """创建子级取消器——自动挂载到当前节点。"""
        return CascadingCancel(parent=self)

    @property
    def is_cancelled(self) -> bool:
        """是否已取消。"""
        return self._event.is_set()

    async def wait(self) -> None:
        """等待取消信号（可用于在工具执行中检查是否应中止）。"""
        await self._event.wait()
