"""
鲁棒 I/O — SafeWriter
========================

包装 stdout / stderr，静默吞掉 BrokenPipeError / OSError。

典型场景：
  - Agent 以后台 Daemon 运行时，stdout pipe 可能随时被关闭
  - Celery Worker / FastAPI 后台任务中 print 输出到已关闭的管道
  - Docker 容器日志驱动重启时的瞬态 I/O 失败

不处理这些异常会导致 Agent 进程直接崩溃。

借鉴自 Hermes Agent 的 _SafeWriter 模式。

使用方式::

    from agent_core.runtime.safe_io import SafeWriter

    # 一行代码安装（通常在进程入口处调用）
    SafeWriter.install()

    # 之后所有 print / logger 输出都不会因为 BrokenPipe 而崩溃

    # 恢复原始流
    SafeWriter.uninstall()
"""

import sys
from typing import IO, Any, Optional


class SafeWriter:
    """包装 stdout/stderr 的鲁棒写入器。

    静默吞掉 BrokenPipeError 和 OSError，
    防止后台/无头环境中 I/O 异常导致进程崩溃。

    Args:
        stream: 原始输出流（stdout 或 stderr）。
    """

    # 类级别状态：保存被替换的原始流
    _original_stdout: Optional[IO[str]] = None
    _original_stderr: Optional[IO[str]] = None

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream

    def write(self, data: str) -> int:
        """写入数据 — BrokenPipeError 时静默返回 0。"""
        try:
            return self._stream.write(data)
        except (BrokenPipeError, OSError):
            # 管道断开或 I/O 错误 — 静默处理
            return 0

    def flush(self) -> None:
        """刷新缓冲区 — BrokenPipeError 时静默。"""
        try:
            self._stream.flush()
        except (BrokenPipeError, OSError):
            pass

    def fileno(self) -> int:
        """返回底层文件描述符。"""
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        """返回底层流的编码。"""
        return getattr(self._stream, "encoding", "utf-8")

    @property
    def errors(self) -> Optional[str]:
        """返回底层流的错误处理模式。"""
        return getattr(self._stream, "errors", None)

    def isatty(self) -> bool:
        """检查底层流是否连接到终端。"""
        try:
            return self._stream.isatty()
        except (BrokenPipeError, OSError):
            return False

    def writable(self) -> bool:
        """始终返回 True — 这是一个可写流。"""
        return True

    def __getattr__(self, name: str) -> Any:
        """代理所有其他属性到底层流。"""
        return getattr(self._stream, name)

    @classmethod
    def install(cls) -> None:
        """一键安装 — 替换全局 sys.stdout 和 sys.stderr。

        幂等操作：重复调用不会嵌套包装。
        """
        if not isinstance(sys.stdout, cls):
            cls._original_stdout = sys.stdout
            sys.stdout = cls(sys.stdout)  # type: ignore[assignment]

        if not isinstance(sys.stderr, cls):
            cls._original_stderr = sys.stderr
            sys.stderr = cls(sys.stderr)  # type: ignore[assignment]

    @classmethod
    def uninstall(cls) -> None:
        """恢复原始 stdout/stderr。"""
        if cls._original_stdout is not None:
            sys.stdout = cls._original_stdout
            cls._original_stdout = None

        if cls._original_stderr is not None:
            sys.stderr = cls._original_stderr
            cls._original_stderr = None
