"""Exec / Process 工具模块

在工作空间内执行 shell 命令，支持前台同步与后台异步模式。
前台支持 PTY 伪终端模式（pty=True）用于交互式命令。
后台任务通过 process 工具进行 list / poll / kill 管理。
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)

_KILL_TIMEOUT = 5.0

# 前台命令 stdout+stderr 合并输出的最大字符数
MAX_CAPTURE_CHARS = 200_000
# 前台命令默认超时（秒）
DEFAULT_TIMEOUT_SEC = 300.0
# 后台任务日志 tail 读取的最大字节数
LOG_TAIL_BYTES = 120_000


@dataclass
class _BgJob:
    """后台 shell 任务的状态记录

    Attributes:
        id: 会话 ID（12 位 hex）
        command: 执行的 shell 命令字符串
        cwd: 工作目录
        proc: subprocess.Popen 进程对象
        out_path: stdout 重定向文件路径
        err_path: stderr 重定向文件路径
    """

    id: str
    command: str
    cwd: Path
    proc: subprocess.Popen
    out_path: Path
    err_path: Path


class ExecTool:
    """Shell 执行与后台任务管理工具

    前台 exec 捕获输出并附加 exit code；background=true 时启动后台进程，
    输出写入 .aion/exec_sessions/ 下的日志文件，由 process 轮询。

    Attributes:
        workspace_root: 工作空间根目录
        default_timeout_sec: 前台命令默认超时
        _jobs: 当前会话内的后台任务表（sessionId -> _BgJob）
    """

    def __init__(self, workspace_root: Path, default_timeout_sec: float = DEFAULT_TIMEOUT_SEC):
        """初始化 ExecTool

        Args:
            workspace_root: 工作空间根目录，workdir 须在其内
            default_timeout_sec: 前台命令默认超时秒数
        """
        self.workspace_root = workspace_root.resolve()
        self.default_timeout_sec = default_timeout_sec
        self._jobs: dict[str, _BgJob] = {}

    def _sessions_dir(self) -> Path:
        """获取并创建后台会话日志目录

        Returns:
            workspace_root/.aion/exec_sessions 路径
        """
        d = self.workspace_root / ".aion" / "exec_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _resolve_cwd(self, workdir: str | None) -> Path:
        """解析并校验工作目录

        Args:
            workdir: 相对或绝对路径；None 或空则使用 workspace_root

        Returns:
            解析后的绝对工作目录路径

        Raises:
            ValueError: workdir 不在工作空间内
        """
        if workdir is None or not str(workdir).strip():
            return self.workspace_root
        p = Path(str(workdir)).expanduser()
        cwd = p.resolve() if p.is_absolute() else (self.workspace_root / p).resolve()
        try:
            cwd.relative_to(self.workspace_root)
        except ValueError as e:
            raise ValueError(f"workdir 必须位于工作空间内: {cwd}") from e
        return cwd

    def _exec_pty(
        self,
        command: str,
        cwd: Path,
        run_env: dict[str, str],
        timeout: float,
    ) -> tuple[str, int]:
        """通过 PTY 伪终端执行 shell 命令（用于交互式 / TTY 依赖命令）

        Returns:
            (output_text, exit_code)
        """
        # Windows 没有 pty 模块，降级到普通 subprocess
        import sys

        if sys.platform == "win32":
            logger.warning("PTY mode not supported on Windows, falling back to subprocess")
            return self._exec_subprocess(command, cwd, run_env, timeout)

        import pty as pty_module

        master_fd, slave_fd = pty_module.openpty()
        pid = os.fork()
        if pid == 0:
            # 子进程：建立新 session 并将 stdio 连接到 PTY
            os.close(master_fd)
            os.setsid()
            for fd in (0, 1, 2):
                os.dup2(slave_fd, fd)
            if slave_fd > 2:
                os.close(slave_fd)
            os.chdir(str(cwd))
            os.execve("/bin/sh", ["/bin/sh", "-c", command], run_env)

        # 父进程：读取 PTY 输出
        os.close(slave_fd)
        output: list[str] = []
        start = time.monotonic()
        timed_out = False

        while True:
            remaining = timeout - (time.monotonic() - start) if timeout > 0 else 1.0
            if timeout > 0 and remaining <= 0:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.2)
                os.kill(pid, signal.SIGKILL)
                timed_out = True
                break
            r, _, _ = select.select([master_fd], [], [], min(remaining, 1.0))
            if r:
                try:
                    data = os.read(master_fd, 65536)
                    if not data:
                        break
                    output.append(data.decode("utf-8", errors="replace"))
                except OSError:
                    break

        if not timed_out:
            _, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
        else:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            exit_code = -1

        os.close(master_fd)
        return "".join(output), exit_code

    def _exec_subprocess(
        self,
        command: str,
        cwd: Path,
        run_env: dict[str, str],
        timeout: float,
    ) -> tuple[str, int]:
        """通过 subprocess.run 执行命令（非 PTY 模式，供 Windows PTY fallback 使用）。

        Returns:
            (output_text, exit_code)
        """
        import subprocess

        try:
            r = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                env=run_env,
                capture_output=True,
                text=True,
                timeout=timeout if timeout > 0 else None,
            )
        except subprocess.TimeoutExpired as e:
            out = e.stdout or ""
            err = e.stderr or ""
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            if isinstance(err, bytes):
                err = err.decode(errors="replace")
            out = out[-8000:]
            err = err[-8000:]
            raise  # 上层已处理超时
        output = (r.stdout or "") + (("\n--- stderr ---\n" + r.stderr) if (r.stderr or "").strip() else "")
        return output, r.returncode

    def exec(
        self,
        command: str,
        workdir: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        background: bool = False,
        pty: bool = False,
        **_: Any,
    ) -> str:
        """执行 shell 命令（前台或后台）

        Args:
            command: shell 命令字符串
            workdir: 工作目录，须在工作空间内
            timeout: 前台超时秒数；None 用 default_timeout_sec；0 表示无超时
            env: 额外环境变量，合并到 os.environ
            background: True 时后台启动，返回 sessionId 提示
            pty: True 时通过 PTY 伪终端执行（用于交互式/TTY 依赖命令）

        Returns:
            前台：命令输出 + exit code；后台：sessionId 与 process 使用说明
        """
        cmd = (command or "").strip()
        if not cmd:
            logger.warning("[exec] command 为空")
            return "错误：command 不能为空"
        try:
            cwd = self._resolve_cwd(workdir)
        except ValueError as e:
            logger.error("[exec] 执行异常: %s", e)
            return f"错误：{e}"

        run_env = os.environ.copy()
        if env:
            for k, v in env.items():
                if k is None:
                    continue
                ks = str(k)
                if isinstance(v, str):
                    run_env[ks] = v

        if background:
            return self._start_background(cmd, cwd, run_env)

        tsec = self.default_timeout_sec if timeout is None else float(timeout)

        if pty:
            pty_out, exit_code = self._exec_pty(cmd, cwd, run_env, tsec if tsec > 0 else 0)
            if len(pty_out) > MAX_CAPTURE_CHARS:
                half = MAX_CAPTURE_CHARS // 2
                pty_out = pty_out[:half] + "\n... [truncated] ...\n" + pty_out[-half:]
            body = pty_out if pty_out.strip() else "(no output)"
            return f"{body}\n[exit code: {exit_code}]"

        try:
            r_out, r_code = self._exec_subprocess(cmd, cwd, run_env, tsec if tsec > 0 else 0)
        except subprocess.TimeoutExpired as e:
            out = e.stdout or ""
            err = e.stderr or ""
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            if isinstance(err, bytes):
                err = err.decode(errors="replace")
            out = out[-8000:]
            err = err[-8000:]
            logger.warning("[exec] 命令超时 %ds", tsec)
            return f"错误：命令超时（{tsec}s）\nstdout(尾):\n{out}\nstderr(尾):\n{err}"
        except Exception as e:
            logger.error("[exec] 执行失败: %s", e)
            return f"错误：执行失败 {e}"

        if len(r_out) > MAX_CAPTURE_CHARS:
            half = MAX_CAPTURE_CHARS // 2
            r_out = r_out[:half] + "\n... [truncated] ...\n" + r_out[-half:]
        body = r_out if r_out.strip() else "(no output)"
        return f"{body}\n[exit code: {r_code}]"

    def _start_background(self, cmd: str, cwd: Path, run_env: dict[str, str]) -> str:
        """启动后台 shell 进程并将 stdout/stderr 写入日志文件

        Args:
            cmd: shell 命令
            cwd: 工作目录
            run_env: 完整环境变量字典

        Returns:
            含 sessionId 的成功提示或错误说明
        """
        # 12 位 hex 会话 ID，日志写入 .aion/exec_sessions/
        sid = uuid.uuid4().hex[:12]
        sdir = self._sessions_dir()
        out_path = sdir / f"{sid}.out"
        err_path = sdir / f"{sid}.err"
        out_f = open(out_path, "wb")
        err_f = open(err_path, "wb")
        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=str(cwd),
                env=run_env,
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
            )
        except Exception as e:
            out_f.close()
            err_f.close()
            logger.error("[exec] 后台启动失败: %s", e)
            return f"错误：后台启动失败 {e}"
        finally:
            out_f.close()
            err_f.close()
        self._jobs[sid] = _BgJob(sid, cmd, cwd, proc, out_path, err_path)
        return (
            f"后台已启动 sessionId={sid}\n"
            f"command: {cmd}\n"
            f"cwd: {cwd}\n"
            "使用 process：list 查看任务；poll + sessionId 拉取输出；"
            "write + sessionId + data 写入 stdin；send-keys + keys 发送按键；"
            "submit 提交输入；paste + text 粘贴文本；kill 终止。"
        )

    @staticmethod
    def _read_tail(path: Path, max_bytes: int = LOG_TAIL_BYTES) -> str:
        """读取文件尾部内容（用于 poll 后台日志）

        Args:
            path: 日志文件路径
            max_bytes: 最多读取的字节数（从文件末尾向前）

        Returns:
            解码后的文本；文件不存在时返回空字符串
        """
        if not path.exists():
            return ""
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")

    def process(
        self,
        action: str,
        sessionId: str | None = None,
        timeout: float | None = None,
        offset: int | None = None,
        limit: int | None = None,
        data: str | None = None,
        keys: str | None = None,
        text: str | None = None,
        **extra: Any,
    ) -> str:
        """管理后台 exec 任务：列出、轮询输出、终止、stdin 交互

        Args:
            action: list | poll | kill | log | write | send-keys | submit | paste
            sessionId: 后台任务 ID（list 以外必填）
            timeout: poll 时可选阻塞等待毫秒数（最大 120s）
            offset: poll 时对 stdout/stderr 行切片起始行
            limit: poll 时行切片最大行数
            data: write 时写入 stdin 的数据
            keys: send-keys 时发送的按键内容
            text: paste 时粘贴的文本
            **extra: 兼容 session_id 别名

        Returns:
            各 action 对应的格式化结果或错误说明
        """
        act = (action or "").strip().lower()
        sid = sessionId or extra.get("session_id")

        if act == "list":
            if not self._jobs:
                return "无后台任务（本会话内通过 exec 且 background=true 启动的才会出现在此列表）。"
            lines: list[str] = []
            for bg_job in sorted(self._jobs.values(), key=lambda j: j.id):
                rc = bg_job.proc.poll()
                st = "running" if rc is None else f"exited({rc})"
                short = bg_job.command[:100] + ("…" if len(bg_job.command) > 100 else "")
                lines.append(f"{bg_job.id}\t{st}\t{short}")
            return "\n".join(lines)

        if not sid:
            logger.warning("[exec] process 缺少 sessionId")
            return "错误：除 list 外需要提供 sessionId"

        job: _BgJob | None = self._jobs.get(str(sid))
        if not job:
            logger.warning("[exec] 未找到 sessionId=%s", sid)
            return f"错误：未找到 sessionId={sid}"

        # stdin 交互操作：write / send-keys / submit / paste
        stdin_actions = {"write", "send-keys", "submit", "paste"}
        if act in stdin_actions:
            if job.proc.poll() is not None:
                logger.warning("[exec] 进程已结束 %s", sid)
                return f"错误：进程已结束（code={job.proc.returncode}），无法写入 stdin"
            if job.proc.stdin is None or job.proc.stdin.closed:
                logger.warning("[exec] stdin 不可用 %s", sid)
                return "错误：stdin 不可用（进程可能已关闭输入）"
            try:
                if act == "write":
                    if not data:
                        logger.warning("[exec] write data 为空")
                        return "错误：write 需要提供 data 参数"
                    job.proc.stdin.write(data.encode("utf-8"))
                elif act == "send-keys":
                    if not keys:
                        logger.warning("[exec] send-keys keys 为空")
                        return "错误：send-keys 需要提供 keys 参数"
                    job.proc.stdin.write(keys.encode("utf-8"))
                elif act == "submit":
                    job.proc.stdin.write(b"\n")
                elif act == "paste":
                    if not text:
                        logger.warning("[exec] paste text 为空")
                        return "错误：paste 需要提供 text 参数"
                    job.proc.stdin.write(text.encode("utf-8"))
                job.proc.stdin.flush()
                return f"已写入 stdin（sessionId={sid}）"
            except BrokenPipeError:
                logger.warning("[exec] stdin 已关闭 %s", sid)
                return "错误：stdin 已关闭（进程可能已结束）"
            except OSError as e:
                logger.warning("[exec] stdin 写入失败: %s", e)
                return f"错误：stdin 写入失败: {e}"

        if act == "kill":
            if job.proc.poll() is not None:
                del self._jobs[str(sid)]
                return f"进程已结束（code={job.proc.returncode}），会话已移除。"
            job.proc.terminate()
            try:
                job.proc.wait(timeout=_KILL_TIMEOUT)
            except subprocess.TimeoutExpired:
                job.proc.kill()
                job.proc.wait(timeout=_KILL_TIMEOUT)
            del self._jobs[str(sid)]
            return f"已终止 sessionId={sid}"

        # log 与 poll 共享读取逻辑，但 log 不等待也不移除
        if act in ("poll", "log"):
            # poll 模式可选阻塞等待
            if act == "poll" and timeout is not None and float(timeout) > 0:
                deadline = time.monotonic() + min(float(timeout) / 1000.0, 120.0)
                while job.proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
            out = self._read_tail(job.out_path).strip()
            err = self._read_tail(job.err_path).strip()
            if offset is not None or limit is not None:
                off = max(0, int(offset or 0))
                lim = int(limit) if limit is not None else 10**9
                if out:
                    ol = out.splitlines()
                    out = "\n".join(ol[off : off + lim])
                if err:
                    el = err.splitlines()
                    err = "\n".join(el[off : off + lim])
            rc = job.proc.poll()
            parts = []
            if out:
                parts.append("--- stdout (tail) ---\n" + out)
            if err:
                parts.append("--- stderr (tail) ---\n" + err)
            body = "\n\n".join(parts) if parts else "(no output yet)"
            if act == "log":
                # log：仅返回输出，不改变任务状态
                return body
            if rc is None:
                return f"{body}\n[status: running]"
            del self._jobs[str(sid)]
            return f"{body}\n[exit code: {rc}]"

        logger.warning("[exec] 未知 action: %s", action)
        return f"错误：未知 action={action!r}（支持 list / poll / log / kill / write / send-keys / submit / paste）"


@tool("exec", parse_docstring=True)
def exec_command(
    command: str,
    workdir: str | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    background: bool = False,
    pty: bool = False,
) -> str:
    """执行 shell 命令，支持前台同步与后台异步运行。
    对长时间运行的任务，使用 background=true 通过 process 工具后续管理。
    任何时候需要日志、状态或干预时，使用 process 工具。
    不要用 exec 的 sleep 或延迟循环来做提醒；目前无定时工具替代。
    对需要 TTY 的交互式命令（vim、htop 等），使用 pty=true 分配伪终端执行。

    Args:
        command: 要执行的 shell 命令
        workdir: 工作目录（可选，默认工作空间根；须在工作空间内）
        timeout: 超时秒数（可选，<=0 表示不限；默认 300）
        env: 额外环境变量（字符串键值，可选）
        background: 若为 true，后台运行并通过 process 工具 list/poll/kill 管理
        pty: 若为 true，通过 PTY 伪终端执行（用于交互式/TTY 依赖命令）
    """
    ws = current_workspace.get()
    tool = ExecTool(workspace_root=ws)
    return tool.exec(command, workdir=workdir, timeout=timeout, env=env, background=background, pty=pty)


@tool(parse_docstring=True)
def process(
    action: str,
    sessionId: str | None = None,
    timeout: float | None = None,
    offset: int | None = None,
    limit: int | None = None,
    data: str | None = None,
    keys: str | None = None,
    text: str | None = None,
) -> str:
    """管理已启动的 exec 后台任务。支持 list/poll/log/kill/write/send-keys/submit/paste。
    当需要确认后台任务完成状态、查看输出日志、终止任务或向 stdin 写入时使用。
    不要用 process 轮询来模拟计时器或周期性检查。
    write/send-keys/submit/paste 用于向运行中进程的 stdin 写入数据。

    Args:
        action: list | poll | log | kill | write | send-keys | submit | paste
        sessionId: 后台任务 id（list 以外必填）
        timeout: poll 时最长等待毫秒数（可选）
        offset: 日志行偏移（可选）
        limit: 日志最大行数（可选）
        data: 写入 stdin 的数据（write 必填）
        keys: 发送的按键内容（send-keys 必填）
        text: 粘贴的文本（paste 必填）
    """
    ws = current_workspace.get()
    tool = ExecTool(workspace_root=ws)
    return tool.process(
        action, sessionId=sessionId, timeout=timeout, offset=offset, limit=limit, data=data, keys=keys, text=text
    )
