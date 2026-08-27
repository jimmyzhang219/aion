"""aion asr — ASR 语音识别子命令组

用法：
    aion asr start [-s mic|system|file] [-d] [path]
    aion asr stop
    aion asr status
    aion asr list [-n N]
    aion asr import <path>
    aion asr export <session_dir>
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import click

from aion.audio.manager import ASRError, ASRManager
from aion.config.loader import load_config
from aion.core.constants import AION_HOME

# ---------------------------------------------------------------------------
# 全局活跃会话追踪
# ---------------------------------------------------------------------------

_ACTIVE_MANAGER: ASRManager | None = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_recordings_dir() -> Path:
    """返回 recordings/ 目录的绝对路径。

    优先读取配置中的工作空间目录，回退到 ``~/.aion/recordings``。
    """
    try:
        config = load_config()
        workspace_name = config.workspaces.current
        if workspace_name:
            ws_dir = AION_HOME / "workspaces" / workspace_name
            if ws_dir.exists():
                return ws_dir / "recordings"
    except Exception:
        pass
    return AION_HOME / "recordings"


def _format_size(size_bytes: int) -> str:
    """将字节数格式化为人类可读的大小字符串。"""
    remainder = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if remainder < 1024:
            return f"{remainder:.1f} {unit}"
        remainder /= 1024
    return f"{remainder:.1f} TB"


def _format_mtime(mtime: float) -> str:
    """将修改时间戳格式化为 YYYY-MM-DD HH:MM:SS。"""
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 命令组
# ---------------------------------------------------------------------------


@click.group("asr")
def asr():
    """语音识别（ASR） — 录音转写子命令组。

    \b
    常用命令：
        aion asr start              启动麦克风录音转写（前台模式，Ctrl+C 停止）
        aion asr start -s system    启动系统音频捕获转写
        aion asr start -s file audio.wav  转写音频文件
        aion asr start -d           静默模式（无终端输出，进程仍在前台）
        aion asr stop               停止当前会话
        aion asr status             查看运行状态
        aion asr list               查看历史转录记录
        aion asr import path/to/audio.wav  导入并转写音频文件
        aion asr export <session_dir>      导出转录结果为纯文本
    """
    pass


# ── start ──────────────────────────────────────────────────────────────────


@asr.command("start")
@click.option(
    "--source",
    "-s",
    type=click.Choice(["mic", "system", "file"]),
    default="mic",
    show_default=True,
    help="音频来源（mic: 麦克风, system: 系统音频, file: 音频文件）",
)
@click.option(
    "--device",
    type=int,
    default=None,
    help="麦克风设备索引（缺省自动选择内置麦克风）",
)
@click.option(
    "--daemon",
    "-d",
    is_flag=True,
    default=False,
    help="静默模式：仅保存文件，无终端输出（非系统级后台进程）",
)
@click.argument("path", required=False)
def start(source: str, device: int | None, daemon: bool, path: str | None):
    """启动录音转写。

    前台模式（默认）实时显示识别文字，按 Ctrl+C 停止。
    静默模式（-d）：不显示终端文字，仅保存转录文件。
    """
    global _ACTIVE_MANAGER

    # 检查是否已有运行中的会话
    if _ACTIVE_MANAGER is not None and _ACTIVE_MANAGER.is_running:
        click.echo(f"错误：已有正在运行的 ASR 会话（{_ACTIVE_MANAGER.session_id}）", err=True)
        click.echo("请先运行 'aion asr stop' 停止后再启动", err=True)
        return

    # 文件模式需要路径
    if source == "file" and not path:
        click.echo("错误：文件模式（-s file）需要指定音频文件路径作为参数", err=True)
        raise click.Abort()

    # 前台模式优先级高于 daemon（daemon 仅在前台模式时会被强制设为 False）
    foreground = not daemon

    manager = ASRManager(foreground=foreground)
    _ACTIVE_MANAGER = manager

    async def _start_and_run():
        """统一事件循环内完成启动 + 运行。"""
        nonlocal manager
        try:
            if source == "mic":
                session_id = await manager.start_mic(device_index=device)
            elif source == "system":
                session_id = await manager.start_system_audio()
            else:
                assert path is not None
                session_id = await manager.start_file(path)

            click.echo(f"ASR 会话已启动：{session_id}")

            if daemon:
                click.echo("静默模式：转录正在运行中（仅保存到文件）")
                async for _ in manager.run():
                    pass
            else:
                click.echo("按 Ctrl+C 停止录音...")
                async for line in manager.run():
                    click.echo(line)

        except ASRError as e:
            click.echo(f"ASR 错误：{e}", err=True)
        except FileNotFoundError as e:
            click.echo(f"文件未找到：{e}", err=True)
        except RuntimeError as e:
            click.echo(f"运行时错误：{e}", err=True)
        except asyncio.CancelledError:
            pass  # Ctrl+C 触发的取消，不报错
        except Exception as e:
            click.echo(f"启动失败：{e}", err=True)
        finally:
            if manager.is_running:
                try:
                    await manager.stop()
                except Exception:
                    pass
            global _ACTIVE_MANAGER
            _ACTIVE_MANAGER = None

    try:
        asyncio.run(_start_and_run())
    except KeyboardInterrupt:
        # asyncio.run 已经触发了事件循环取消，_start_and_run 的
        # finally 块会处理剩余清理。这里只需输出提示。
        click.echo("\n录音已停止")


# ── stop ───────────────────────────────────────────────────────────────────


@asr.command("stop")
def stop():
    """停止当前运行中的 ASR 会话。"""
    global _ACTIVE_MANAGER

    if _ACTIVE_MANAGER is None or not _ACTIVE_MANAGER.is_running:
        click.echo("当前没有正在运行的 ASR 会话")
        return

    session_id = _ACTIVE_MANAGER.session_id
    click.echo(f"正在停止 ASR 会话：{session_id}...")

    try:
        asyncio.run(_ACTIVE_MANAGER.stop())
        click.echo("ASR 会话已停止")
    except Exception as e:
        click.echo(f"停止时出错：{e}", err=True)
    finally:
        _ACTIVE_MANAGER = None


# ── status ─────────────────────────────────────────────────────────────────


@asr.command("status")
def status():
    """查看当前 ASR 运行状态。"""
    global _ACTIVE_MANAGER

    if _ACTIVE_MANAGER is not None and _ACTIVE_MANAGER.is_running:
        click.echo("状态：运行中")
        click.echo(f"会话 ID：{_ACTIVE_MANAGER.session_id}")
    else:
        click.echo("状态：空闲")


# ── list ────────────────────────────────────────────────────────────────────


@asr.command("list")
@click.option(
    "--limit",
    "-n",
    default=10,
    show_default=True,
    help="最多显示的记录数",
)
def list_recordings(limit: int):
    """查看历史转录记录。

    列出 recordings/ 目录中的转录会话文件夹，
    显示每个会话的名称、大小、修改时间。
    """
    recordings_dir = _get_recordings_dir()

    if not recordings_dir.exists():
        click.echo("暂无转录记录（recordings 目录不存在）")
        return

    entries = []
    for entry in sorted(recordings_dir.iterdir(), key=lambda e: e.name, reverse=True):
        if not entry.is_dir():
            continue
        # 计算目录总大小
        total_size = 0
        for f in entry.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
        mtime = entry.stat().st_mtime
        entries.append((entry.name, total_size, mtime))

    if not entries:
        click.echo("暂无转录记录")
        return

    # 截取前 limit 条
    entries = entries[:limit]

    click.echo(f"{'会话目录':<30} {'大小':<10} {'修改时间'}")
    click.echo("-" * 60)
    for name, size, mtime in entries:
        click.echo(f"{name:<30} {_format_size(size):<10} {_format_mtime(mtime)}")

    click.echo(f"\n共 {len(entries)} 条记录（recordings 目录：{recordings_dir}）")


# ── import ─────────────────────────────────────────────────────────────────


@asr.command("import")
@click.argument("path")
def import_audio(path: str):
    """导入音频文件并转写。

    将指定音频文件送入 ASR 引擎进行转写，
    结果保存到 recordings/ 目录。
    """
    file_path = Path(path)

    if not file_path.exists():
        click.echo(f"错误：文件不存在：{file_path}", err=True)
        return

    if not file_path.is_file():
        click.echo(f"错误：路径不是文件：{file_path}", err=True)
        return

    supported_ext = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
    if file_path.suffix.lower() not in supported_ext:
        click.echo(
            f"警告：文件格式 {file_path.suffix} 可能不受支持，支持格式：{', '.join(supported_ext)}",
            err=True,
        )

    manager = ASRManager(foreground=False)

    async def _import():
        nonlocal manager
        try:
            session_id = await manager.start_file(str(file_path))
            click.echo(f"正在转写文件：{file_path.name}（会话：{session_id}）...")

            async for _ in manager.run():
                pass

            # count actual sentences
            sentence_count = len(manager.recorder._sentences) if manager.recorder else 0
            if sentence_count == 0:
                click.echo("  警告：未识别到任何语音内容")
            else:
                click.echo(f"  ✓ 已识别 {sentence_count} 句")

            click.echo(f"转写完成：{session_id}")

            if manager.recorder:
                click.echo(f"结果目录：{manager.recorder.dir_path}")
                click.echo("  文件列表：")
                for f in sorted(Path(manager.recorder.dir_path).iterdir()):
                    click.echo(f"    {f.name}")

        except ASRError as e:
            click.echo(f"转写错误：{e}", err=True)
        except Exception as e:
            click.echo(f"导入失败：{e}", err=True)
        finally:
            if manager.is_running:
                await manager.stop()

    asyncio.run(_import())


# ── export ─────────────────────────────────────────────────────────────────


@asr.command("export")
@click.argument("session_dir")
def export(session_dir: str):
    """导出转录会话结果为纯文本。

    将会话目录中的转录内容合并输出为纯文本。
    优先读取 transcript.txt，若不存在则从 transcript.json 生成。
    """
    dir_path = Path(session_dir)

    if not dir_path.exists():
        click.echo(f"错误：会话目录不存在：{dir_path}", err=True)
        return

    if not dir_path.is_dir():
        click.echo(f"错误：路径不是目录：{dir_path}", err=True)
        return

    # 尝试读取 transcript.txt
    txt_path = dir_path / "transcript.txt"
    if txt_path.exists():
        click.echo(txt_path.read_text(encoding="utf-8").rstrip())
        return

    # 回退：从 transcript.json 生成
    json_path = dir_path / "transcript.json"
    if not json_path.exists():
        click.echo("错误：目录中未找到 transcript.txt 或 transcript.json", err=True)
        return

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        click.echo(f"错误：无法解析 transcript.json：{e}", err=True)
        return

    sentences = data.get("sentences", [])
    if not sentences:
        click.echo("（该会话无转录内容）")
        return

    for s in sentences:
        begin_ms = s.get("begin_time", 0)
        end_ms = s.get("end_time", 0)
        text = s.get("text", "")
        begin_sec = begin_ms / 1000.0
        end_sec = end_ms / 1000.0
        click.echo(f"[{begin_sec:.3f}s - {end_sec:.3f}s] {text}")
