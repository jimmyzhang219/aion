"""aion logs --traceid xxxx 命令

按 TraceID 过滤并格式化展示 Gateway/Agent 等模块的结构化日志。
Agent / ReAct 循环日志自动以缩进树状展示轮次结构。
"""

import click
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

# 日志行正则：匹配 "YYYY-MM-DD HH:MM:SS [LEVEL] module:line: [traceid] message" 格式
_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"\[(\w+)\] "
    r"([\w.]+:\d+): "
    r"\[([^\]]+)\] "
    r"(.*)$"
)

_SOURCE_MAP = {
    "aion.gateway.server": "Gateway",
    "aion.agent.loop": "Agent",
    "aion.tools.builtin.web_tools": "web_search",
    "aion.search.providers.bocha": "web_search_bocha",
    "aion.search.providers.baidu": "web_search_baidu",
    "aion.channels.feishu.client": "feishu",
    "aion.channels.feishu.channel": "feishu",
}

# ──────────────────────────────────────────
# 纯 Python tail 实现（替代 subprocess tail）
# ──────────────────────────────────────────


def _tail_file(path: Path, n: int = 100) -> str:
    """读取文件末尾 N 行（纯 Python 实现，替代 ``tail -n``）。

    Args:
        path: 日志文件路径
        n: 返回行数

    Returns:
        文件末尾最多 n 行的文本；文件不存在时返回空字符串。
    """
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        f.seek(0, 2)  # 跳到末尾
        size = f.tell()
        # 估算读取量：每行约 120 字节，加 N*4 安全垫
        read_size = min(size, max(4096, n * 120))
        f.seek(-read_size, 2)
        data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        return "\n".join(lines[-n:])


# ──────────────────────────────────────────
# ReAct 树状展示 —— 解析 Agent 日志中的轮次结构
# ──────────────────────────────────────────


@dataclass
class _Entry:
    """一条解析后的日志条目。"""

    ts: str
    level: str
    module_line: str
    label: str
    body: str
    raw: str


# 轮次识别正则
_P_ROUND = re.compile(r"(?:\[Agent\]\s+)?Round\s+(\d+)")
_P_TOOL_CALL = re.compile(r"LLM\s*→\s*(\w+)\(")
_P_TOOL_RES = re.compile(r"(\w+)\s*→\s*(\d+)\s*chars")
_P_TEXT_RSP = re.compile(r"LLM\s*→\s*text\s*\((\d+)\s*chars[^)]*\)")
_P_TOOL_CALLS_INFO = re.compile(r"tool_calls:\s*\d+\s+tools")

_SEPARATOR = "─" * 58
_INDENT = "    "  # 子条目缩进


def _build_traceid_pattern(traceid: str) -> re.Pattern:
    """将 traceid 字符串编译为搜索用的正则 Pattern。

    对于以 om_、local-、test- 开头或等于 "none" 的 traceid，
    直接使用原始字符串（这些已经是正则安全的 ID 格式）；
    否则用 re.escape() 做安全转义。
    """
    if traceid.startswith("om_") or traceid == "none" or traceid.startswith("local-") or traceid.startswith("test-"):
        raw = traceid
    else:
        raw = re.escape(traceid)
    return re.compile(raw)


def _parse_log_line(clean: str) -> _Entry | None:
    """将一行清理后的日志解析为 _Entry。

    如果匹配标准日志格式则返回 _Entry，否则返回 None。
    """
    m = _LOG_RE.match(clean)
    if not m:
        return None
    ts, level, module_line, tid, msg = m.groups()
    label = _SOURCE_MAP.get(
        module_line.rsplit(":", 1)[0],
        module_line.rsplit(":", 1)[0].split(".")[-1],
    )
    body = re.sub(r"^\[\w+\]\s*", "", msg)
    return _Entry(ts, level, module_line, label, body, clean)


@click.command("logs")
@click.option(
    "--traceid", "traceid", required=False, default=None, help="按 TraceID 过滤日志（可选，缺省显示最近 N 条）"
)
@click.option("--follow", "-f", "follow", is_flag=True, default=False, help="实时跟踪新日志（类似 tail -f）")
@click.option(
    "--lines", "-n", "lines", default=50, show_default=True, help="回溯行数（仅无 --traceid 或带 --follow 时生效）"
)
@click.option("--log-file", "log_file", default=None, help="日志文件路径（默认今天）")
@click.option("--verbose", "-v", is_flag=True, help="显示完整原始日志行")
@click.option("--flat", is_flag=True, help="禁用 ReAct 树状展示，使用传统 flat 格式")
def logs(traceid: str | None, follow: bool, lines: int, log_file: str | None, verbose: bool, flat: bool):
    """按 TraceID 过滤并显示日志（Agent ReAct 自动树状展示）。

    \b
    示例：
        aion logs                    # 最近 50 条日志
        aion logs -n 100             # 最近 100 条日志
        aion logs --traceid om_xxx   # 按 traceid 过滤（带 ReAct 树状展示）
        aion logs --traceid om_xxx -v
        aion logs --traceid om_xxx --flat
        aion logs -f                 # 实时跟踪所有日志
        aion logs -f --traceid om_xxx  # 实时跟踪特定 traceid 的日志
        aion logs --log-file /path/to/log
    """
    if log_file:
        log_path = Path(log_file)
    else:
        log_dir = Path.home() / ".aion" / "logs"
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = log_dir / f"aion-{today}.log"

    # ── 模式 1: --follow（实时跟踪）──
    if follow:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()
        _follow_logs(log_path, traceid, lines, verbose)
        return

    if not log_path.exists():
        click.echo(f"日志文件不存在: {log_path}")
        return

    # ── 模式 2: 无 traceid（显示最近 N 条）──
    if not traceid:
        _show_recent(log_path, lines, verbose, regex=None)
        return

    # ── 模式 3: 有 traceid（现有行为：过滤 + ReAct 树状展示）──
    regex = _build_traceid_pattern(traceid)

    matched = 0
    entries: list[_Entry] = []

    if verbose:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                if not regex.search(line):
                    continue
                matched += 1
                click.echo(line.rstrip())
        if matched == 0:
            click.echo(f"没有找到匹配 traceid={traceid} 的日志")
        else:
            click.echo(f"\n共 {matched} 条日志")
        return

    # 普通模式：解析条目
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            if not regex.search(line):
                continue
            matched += 1
            clean = line.rstrip()
            e = _parse_log_line(clean)
            if e:
                entries.append(e)
            else:
                entries.append(_Entry("", "", "", "", "", clean))

    if flat:
        output_lines = _format_flat(entries)
    else:
        output_lines = _format_entries(entries)

    for line in output_lines:
        click.echo(line)

    if matched == 0:
        click.echo(f"没有找到匹配 traceid={traceid} 的日志")
    else:
        click.echo(f"\n共 {matched} 条日志")


# ──────────────────────────────────────────
# 格式化逻辑
# ──────────────────────────────────────────


def _level_fmt(level: str) -> str:
    """给 level 加颜色。"""
    s = f"[{level:<5}]"
    if level == "ERROR":
        return click.style(s, fg="red", bold=True)
    elif level == "WARNING":
        return click.style(s, fg="yellow")
    elif level == "INFO":
        return click.style(s, fg="cyan")
    return s


def _format_entry_line(e: _Entry) -> str:
    """将一条 _Entry 格式化为 flat 输出行。"""
    if not e.ts:
        return e.raw
    return f"{e.ts} {_level_fmt(e.level)} {e.label:<10} {e.module_line:<30} {e.body}"


def _format_flat(entries: list[_Entry]) -> list[str]:
    lines = []
    for e in entries:
        lines.append(_format_entry_line(e))
    return lines


def _format_entries(entries: list[_Entry]) -> list[str]:
    """格式化条目 —— Agent 条目按 ReAct 轮次分组为树状，其余 flat。"""
    lines: list[str] = []
    agent_buf: list[_Entry] = []

    def _flush():
        if not agent_buf:
            return
        groups = _group_react_rounds(agent_buf)
        for i, g in enumerate(groups):
            lines.extend(_render_round(g))
            if i < len(groups) - 1:
                lines.append(_SEPARATOR)
        agent_buf.clear()

    for e in entries:
        if e.label == "Agent":
            agent_buf.append(e)
        else:
            _flush()
            if not e.ts:
                lines.append(e.raw)
            else:
                lines.append(f"{e.ts} {_level_fmt(e.level)} {e.label:<10} {e.module_line:<30} {e.body}")

    _flush()
    return lines


def _group_react_rounds(entries: list[_Entry]) -> list[dict]:
    """将连续 Agent 条目按 ReAct 轮次分组。

    支持两种模式：
    - 原生模式：有显式 Round N 头部
    - LangGraph 模式：无 Round 头部，按 LLM → tool 自动开新轮次

    返回 list[dict]，每项为 { round_num, header, calls, results, final, others }
    或 { singleton }（不在轮次内的单条日志）。
    """
    groups: list[dict] = []
    cur: dict | None = None

    for e in entries:
        body = e.body
        m_round = _P_ROUND.match(body)
        m_text = _P_TEXT_RSP.match(body)
        m_call = _P_TOOL_CALL.match(body)
        m_res = _P_TOOL_RES.match(body)
        m_tc_info = _P_TOOL_CALLS_INFO.match(body)

        if m_round:
            # 显式轮次头部（原生模式）
            if cur:
                groups.append(cur)
            cur = {
                "round_num": int(m_round.group(1)),
                "header": e,
                "calls": [],
                "results": [],
                "final": None,
                "others": [],
            }
        elif m_tc_info:
            # tool_calls 信息行（"tool_calls: N tools, finish_reason=..."）
            # 标识 LLM 即将发起工具调用，开启新轮次
            if cur:
                groups.append(cur)
            cur = {
                "round_num": len(groups) + 1,
                "header": e,
                "calls": [],
                "results": [],
                "final": None,
                "others": [],
            }
        elif m_text:
            # 文本回复（必须优先于 m_call，因为 _P_TOOL_CALL 也会匹配 LLM → text(...)）
            if cur is None:
                cur = {
                    "round_num": len(groups) + 1,
                    "header": e,
                    "calls": [],
                    "results": [],
                    "final": None,
                    "others": [],
                }
            cur["final"] = e
        elif m_call:
            # 工具调用
            if cur is None:
                # LangGraph 模式：无显式 Round 头部，自动开新轮次
                cur = {
                    "round_num": len(groups) + 1,
                    "header": e,
                    "calls": [],
                    "results": [],
                    "final": None,
                    "others": [],
                }
            cur["calls"].append(e)
        elif m_res:
            if cur is None:
                cur = {
                    "round_num": len(groups) + 1,
                    "header": e,
                    "calls": [],
                    "results": [],
                    "final": None,
                    "others": [],
                }
            cur["results"].append(e)
        elif cur:
            cur["others"].append(e)
        else:
            groups.append({"singleton": e})

    if cur:
        groups.append(cur)
    return groups


def _render_round(g: dict) -> list[str]:
    """将单个轮次组渲染为缩进树文本行。"""
    lines: list[str] = []

    # 非轮次的独立 Agent 条目（如初始化日志）
    if "singleton" in g:
        e = g["singleton"]
        lines.append(f"{e.ts} {_level_fmt(e.level)} {e.label:<10} {e.module_line:<30} {e.body}")
        return lines

    # ── 轮次标题行 ──
    hdr = g["header"]
    rn = g["round_num"]
    lf = _level_fmt(hdr.level)

    # 提取本轮调用的工具名
    tool_names = []
    for c in g["calls"]:
        m = re.search(r"LLM\s*→\s*(\w+)\(", c.body)
        if m:
            tool_names.append(m.group(1))

    # 从 header（_P_TOOL_CALLS_INFO）提取 finish_reason 附加到行尾
    fr_match = re.search(r"finish_reason=(\w+)", hdr.body)
    fr_suffix = f" (finish_reason={fr_match.group(1)})" if fr_match else ""
    if tool_names:
        lines.append(f"{hdr.ts} {lf} [Round {rn}] LLM → {', '.join(tool_names)}{fr_suffix}")
    elif g["final"]:
        lines.append(f"{hdr.ts} {lf} [Round {rn}] LLM → (final)")
    else:
        # 尝试从 header 提取工具名
        hdr_tools = re.search(r"LLM\s*→\s*(.+)", hdr.body)
        if hdr_tools:
            lines.append(f"{hdr.ts} {lf} [Round {rn}] LLM → {hdr_tools.group(1)}")
        else:
            lines.append(f"{hdr.ts} {lf} [Round {rn}] {hdr.body}")

    # ── 工具结果（缩进）──
    for r in g["results"]:
        m = re.search(r"(\w+)\s*→\s*(\d+)\s*chars", r.body)
        if m:
            lines.append(f"{_INDENT}{m.group(1)} → {m.group(2)} chars")
        else:
            lines.append(f"{_INDENT}{r.body}")

    # ── 最终文本回复 ──
    if g["final"]:
        lines.append(f"{_INDENT}{g['final'].body}")

    # ── 轮次内其他日志 ──
    for o in g.get("others", []):
        lines.append(f"{_INDENT}[{o.level}] {o.body}")

    return lines


def _show_recent(log_path: Path, lines: int, verbose: bool, regex: re.Pattern | None) -> int:
    """显示日志文件中最新的 N 行。

    使用纯 Python tail 实现获取最近日志行。
    返回显示的行数。
    """
    text = _tail_file(log_path, n=lines)
    raw_lines = text.splitlines()
    matched = 0

    for line in raw_lines:
        clean = line.rstrip()

        if regex is not None and not regex.search(clean):
            continue

        matched += 1

        if verbose:
            click.echo(clean)
            continue

        e = _parse_log_line(clean)
        if e:
            click.echo(_format_entry_line(e))
        else:
            click.echo(clean)

    if matched == 0:
        click.echo("没有找到匹配的日志")
    else:
        click.echo(f"\n共 {matched} 条日志")

    return matched


def _follow_logs(log_path: Path, traceid: str | None, lines: int, verbose: bool) -> None:
    """实时跟踪日志文件（纯 Python 实现，替代 ``tail -f``）。"""
    import time

    regex = _build_traceid_pattern(traceid) if traceid else None

    # 先显示最近 N 行
    _show_recent(log_path, lines, verbose, regex)

    # 然后实时跟踪新内容
    try:
        with open(log_path, encoding="utf-8") as f:
            # 跳到当前末尾（不重复显示已读内容）
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    clean = line.rstrip()

                    if regex is not None and not regex.search(clean):
                        continue

                    if verbose:
                        click.echo(clean)
                    else:
                        e = _parse_log_line(clean)
                        if e:
                            click.echo(_format_entry_line(e))
                        else:
                            click.echo(clean)
                else:
                    time.sleep(0.1)
    except KeyboardInterrupt:
        pass
