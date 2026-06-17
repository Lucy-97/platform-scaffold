"""
Sandbox Code Executor — 通用 Docker 容器代码执行器
===================================================

所有 LLM 生成的代码在 Docker 容器内执行，提供完整隔离。
本模块是通用执行器，不绑定任何特定 Skill 或业务场景。

安全模型（多层防御）：
  1. Docker 容器隔离: --network=none + --read-only + --memory + --cpus
  2. 黑名单禁止网络/注入模块（双保险）
  3. 超时保护 + 输出截断

依赖管理（Docker Named Volume）：
  skill_loader 将 Python/Node 依赖安装到 Named Volume，
  本模块将其以 :ro 只读挂载，并通过 PYTHONPATH/NODE_PATH 注入。
  沙箱代码可以 import/require 这些依赖，但绝对无法篡改。

生产部署（容器内创建容器）：
  后端本身运行在容器中时，通过 Docker Socket 挂载实现：
  docker-compose 中挂载 /var/run/docker.sock → 后端创建的沙箱容器
  是宿主机 Docker 引擎上的"兄弟容器"，不是嵌套容器（非 DinD）。
"""
import asyncio
import os
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from loguru import logger


@dataclass
class SandboxResult:
    """沙箱执行结果。

    Attributes:
        stdout: 捕获的标准输出。
        stderr: 捕获的标准错误。
        exit_code: 进程退出码（0 = 成功）。
        timed_out: 是否因超时被终止。
    """
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


# ──────────────────────────────────────────────────────────────────────
# 沙箱安全前置脚本 — 注入到用户代码之前，作为 Docker 容器内的第二道防线
# Docker 容器本身已通过 --network=none 禁止网络，这里的黑名单是双保险
# ──────────────────────────────────────────────────────────────────────

_SANDBOX_PRELUDE = '''\
import builtins as _builtins

# 黑名单模式 — 仅禁止真正危险的模块，其余全部放行
# 这是 Docker 容器隔离之上的第二道防线
_BLOCKED_TOP = {
    # 网络 I/O — 容器已 --network=none，这里双保险
    'socket', 'ssl',
    'http', 'xmlrpc',
    'requests', 'httpx', 'aiohttp', 'urllib3',
    'smtplib', 'ftplib', 'telnetlib', 'poplib', 'imaplib', 'nntplib',
    # 底层注入 — 禁止动态代码编译执行
    'ctypes',
    'code', 'codeop', 'compileall',
}

# urllib 需要细粒度控制 — pathlib/docx 内部依赖 urllib.parse
_BLOCKED_FULL = {
    'urllib.request', 'urllib.error', 'urllib.response',
    'urllib.robotparser',
}

_real = _builtins.__import__

def _guard(name: str, *a, **k):
    """沙箱 import 拦截器：禁止黑名单内的模块，其余放行。"""
    t = name.split('.')[0]
    if t in _BLOCKED_TOP or name in _BLOCKED_FULL:
        raise ImportError("Module '" + name + "' is not allowed in sandbox")
    return _real(name, *a, **k)

_builtins.__import__ = _guard
# --- sandbox prelude end ---

'''


# ──────────────────────────────────────────────────────────────────────
# Docker 配置
# ──────────────────────────────────────────────────────────────────────

# 沙箱镜像名（由 Dockerfile.sandbox 构建）
_DOCKER_IMAGE = os.getenv("SANDBOX_DOCKER_IMAGE", "agent-sandbox:latest")

# Docker Named Volume — 持久化沙箱依赖（独立于后端容器生死周期）
_DEPS_VOLUME = os.getenv("SANDBOX_DEPS_VOLUME", "agent_sandbox_deps")


async def execute_code(
    code: str,
    output_dir: Optional[str] = None,
    timeout_seconds: int = 10,
    max_output_bytes: int = 50_000,
    extra_mounts: Optional[List[Tuple[str, str, str]]] = None,
) -> SandboxResult:
    """在 Docker 容器内执行 Python 代码（通用执行器）。

    本方法不绑定任何特定 Skill 或业务场景。输出目录、额外挂载等
    均由调用方按需传入。

    Docker 层（第一道防线 — 物理隔离）：
      - --network=none: 禁止网络访问
      - --memory=128m: 内存上限（OOM Kill）
      - --cpus=0.5: CPU 限制
      - --read-only: 只读根文件系统
      - --tmpfs /tmp:size=10m: 受限临时空间
      - --rm: 执行后自动销毁
      - --user sandbox: 非 root 用户

    黑名单层（第二道防线 — import 拦截）：
      - 禁止 socket/http/requests 等网络模块
      - 禁止 ctypes/code 等注入模块

    挂载策略：
      - /workspace/script.py:ro — 代码文件只读
      - /output:rw — 输出目录读写（仅当 output_dir 不为 None 时挂载）
      - extra_mounts — 额外挂载（如 skill 目录 :ro）

    Args:
        code: 要执行的 Python 源代码。
        output_dir: 宿主机输出目录路径。传入后挂载到容器 /output:rw，
            并设置环境变量 OUTPUT_DIR=/output。不传则无输出挂载（纯计算）。
        timeout_seconds: 最大执行时间，超时后终止。
        max_output_bytes: stdout/stderr 最大捕获字节数。
        extra_mounts: 额外挂载列表 [(host_path, container_path, mode)]，
            mode 为 'ro' 或 'rw'。

    Returns:
        SandboxResult，包含 stdout、stderr、exit_code 和 timed_out。
    """
    logger.info(
        f"[sandbox] execute called: code_length={len(code)} chars, "
        f"timeout={timeout_seconds}s, image={_DOCKER_IMAGE}, "
        f"output_dir={output_dir}"
    )
    logger.debug(f"[sandbox] Code preview:\n{code[:300]}")

    # 代码 + 安全前置脚本 → 临时文件 → 只读挂载到容器
    full_code = _SANDBOX_PRELUDE + code

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        prefix="sandbox_",
    ) as tmp:
        tmp.write(full_code)
        tmp_path = tmp.name

    try:
        docker_cmd = [
            "docker", "run",
            "--rm",                        # 执行后自动销毁
            "--network=none",              # 禁止网络
            "--memory=128m",               # 内存上限
            "--cpus=0.5",                  # CPU 限制
            "--read-only",                 # 只读根文件系统
            "--tmpfs", "/tmp:size=10m",    # 受限临时空间
            "--user", "sandbox",           # 非 root 用户
            "-v", f"{tmp_path}:/workspace/script.py:ro",  # 代码只读
            # Named Volume — 动态依赖只读挂载 + PYTHONPATH/NODE_PATH 注入
            # skill_loader 将依赖安装到这个 Volume，这里以 :ro 挂载防止篡改
            "-v", f"{_DEPS_VOLUME}:/sandbox_deps:ro",
            "-e", "PYTHONPATH=/sandbox_deps/python",
            "-e", "NODE_PATH=/sandbox_deps/node/node_modules",
        ]

        # 有输出目录时才挂载 /output 并注入环境变量
        if output_dir:
            abs_output = os.path.expanduser(output_dir)
            os.makedirs(abs_output, exist_ok=True)
            docker_cmd.extend([
                "-e", "OUTPUT_DIR=/output",
                "-v", f"{abs_output}:/output:rw",
            ])

        # 添加额外挂载（如 skill 目录只读挂载）
        for host_path, container_path, mode in (extra_mounts or []):
            docker_cmd.extend(["-v", f"{host_path}:{container_path}:{mode}"])

        docker_cmd.extend([_DOCKER_IMAGE, "python", "/workspace/script.py"])

        logger.info(f"[sandbox] docker run: {' '.join(docker_cmd[:8])}...")

        process = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timed_out = False
        try:
            # Docker 启动有额外开销，给 5s 缓冲
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds + 5,
            )
        except asyncio.TimeoutError:
            # 超时 → 强制终止容器
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            timed_out = True
            logger.warning(
                f"[sandbox] Execution timed out after {timeout_seconds}s"
            )

        # 截断输出
        stdout = stdout_bytes[:max_output_bytes].decode("utf-8", errors="replace")
        stderr = stderr_bytes[:max_output_bytes].decode("utf-8", errors="replace")

        # 清理 traceback 中的临时路径噪音
        if tmp_path in stderr:
            stderr = stderr.replace(tmp_path, "<sandbox>")

        exit_code = process.returncode if process.returncode is not None else -1

        result = SandboxResult(
            stdout=stdout.strip(),
            stderr=stderr.strip(),
            exit_code=exit_code,
            timed_out=timed_out,
        )
        logger.info(
            f"[sandbox] Finished: exit_code={result.exit_code} "
            f"timed_out={result.timed_out} "
            f"stdout_len={len(result.stdout)} stderr_len={len(result.stderr)}"
        )
        if result.stdout:
            logger.debug(f"[sandbox] stdout: {result.stdout[:300]}")
        if result.stderr:
            logger.info(f"[sandbox] stderr: {result.stderr[:300]}")
        return result

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
