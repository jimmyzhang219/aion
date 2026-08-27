"""日志模块

提供结构化 JSON 日志输出能力：
- Logger: 结构化 JSON 日志器
- 支持日志轮转（避免单文件过大）
- 磁盘满时自动清理最旧日志

也提供标准 logging 集成：
- configure_logging(): 配置全局 logging（文件+控制台）
- get_logger(name): 获取标准 logging.Logger 实例

日志格式（标准模式）：
{
    "ts": "ISO timestamp",
    "level": "INFO/WARN/ERROR",
    "traceid": "唯一追踪 ID",
    "source": "日志来源",
    "msg": "日志消息",
    ...extra
}
"""

import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path

_log_configured = False  # 全局 logging 是否已初始化（避免重复添加 handler）


class TraceFormatter(logging.Formatter):
    """自定义 Formatter：安全处理 ``%(traceid)s`` 字段（缺失时默认为 ``-``）。"""

    def formatMessage(self, record):
        """格式化单条 LogRecord，注入 traceid 字段。

        Args:
            record: 标准 logging LogRecord。

        Returns:
            格式化后的日志行字符串。
        """
        record.traceid = getattr(record, "traceid", "-") or "-"
        return super().formatMessage(record)


class DailyRotatingFileHandler(logging.FileHandler):
    """每天 0 点自动切换日志文件，确保日志只写入当天的文件。"""

    def __init__(self, log_dir: Path, mode: str = "a", encoding: str = "utf-8"):
        self.log_dir = log_dir
        self._today: str | None = None
        super().__init__(self._today_path(), mode=mode, encoding=encoding)

    def _today_path(self) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return str(self.log_dir / f"aion-{today}.log")

    def emit(self, record):
        current = self._today_path()
        if current != self.baseFilename:
            self.baseFilename = current
            if self.stream:
                self.stream.close()
                self.stream = self._open()
        super().emit(record)


def configure_logging(verbose: bool = False):
    """从配置读取 log_level 并设置全局日志级别，同时输出到文件和控制台（仅配置一次）

    Args:
        verbose: 如果为 True，强制控制台输出 DEBUG 级别日志（忽略配置中的 log_level）。

    Returns:
        None
    """
    global _log_configured
    if _log_configured:
        return

    from aion.config.loader import load_config

    try:
        config = load_config()
        level_name = config.log_level
    except Exception:
        level_name = "info"

    level = getattr(logging, level_name.upper(), logging.INFO)

    # verbose 模式：控制台强制 DEBUG，文件保持配置级别
    console_level = logging.DEBUG if verbose else level

    log_dir = Path.home() / ".aion" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    trace_format = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: [%(traceid)s] %(message)s"
    file_handler = DailyRotatingFileHandler(log_dir)
    file_handler.setLevel(level)
    file_handler.setFormatter(TraceFormatter(trace_format, datefmt="%Y-%m-%d %H:%M:%S"))

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(console_level)
    stream_handler.setFormatter(TraceFormatter(trace_format, datefmt="%Y-%m-%d %H:%M:%S"))

    # 设置根 logger 级别为 CRITICAL，只显示 aion 包的日志
    root = logging.getLogger()
    root.setLevel(logging.CRITICAL)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    # 设置 aion 包日志级别，propagate=False 避免日志向上传播到 root 导致重复输出
    aion_logger = logging.getLogger("aion")
    aion_logger.setLevel(level)
    aion_logger.propagate = False
    aion_logger.addHandler(file_handler)
    aion_logger.addHandler(stream_handler)

    # 抑制第三方库的 DEBUG 噪声
    for noisy in ("lark_oapi", "Lark", "lark", "httpx", "httpcore", "urllib3"):
        lg = logging.getLogger(noisy)
        lg.setLevel(logging.WARNING)
        lg.propagate = False

    _log_configured = True


def get_logger(name: str) -> logging.Logger:
    """获取标准 ``logging.Logger`` 实例（需先调用 ``configure_logging``）。

    Args:
        name: 通常传 ``__name__``。

    Returns:
        标准 Logger 实例。
    """
    return logging.getLogger(name)


# ---- traceid context propagation ----

import contextvars
from typing import Optional

_traceid_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "traceid", default=None
)  # 异步上下文 traceid 存储


def set_traceid(tid: str) -> contextvars.Token:
    """设置当前上下文的 traceid，供日志与链路追踪使用。

    Args:
        tid: 追踪 ID 字符串。

    Returns:
        contextvars Token，供 ``reset_traceid`` 恢复先前值。
    """
    return _traceid_var.set(tid)


def reset_traceid(token: contextvars.Token) -> None:
    """重置 traceid 到 ``set_traceid`` 之前的值。

    Args:
        token: ``set_traceid`` 返回的 Token。

    Returns:
        None
    """
    _traceid_var.reset(token)


def generate_traceid() -> str:
    """生成 32 字符 hex traceid，直接兼容 Langfuse trace_id 格式。

    使用 ``uuid.uuid4().hex`` 生成 128 位随机 ID 的 32 字符小写 hex 表示。
    Langfuse SDK 4.x 要求 trace_id 为 32 位小写十六进制。

    Returns:
        32 字符小写十六进制字符串。
    """
    return uuid.uuid4().hex


class TraceLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter：从 contextvars 自动注入 traceid 到 ``extra``。"""

    def process(self, msg, kwargs):
        """在 emit 前将 traceid 写入 kwargs.extra。

        Args:
            msg: 日志消息。
            kwargs: logging 关键字参数。

        Returns:
            ``(msg, kwargs)`` 元组。
        """
        traceid = _traceid_var.get()
        if traceid:
            kwargs.setdefault("extra", {})["traceid"] = traceid
        return msg, kwargs


def get_trace_logger(name: str) -> TraceLoggerAdapter:
    """获取带 traceid 自动注入的 LoggerAdapter。

    Args:
        name: 通常传 ``__name__``。

    Returns:
        ``TraceLoggerAdapter`` 实例。
    """
    return TraceLoggerAdapter(logging.getLogger(name), {})
