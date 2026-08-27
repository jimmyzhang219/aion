"""tests/test_logs_tail.py"""

from __future__ import annotations

import tempfile
from pathlib import Path


def test_tail_file_reads_last_n_lines():
    from aion.cli.logs import _tail_file

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
        for i in range(100):
            f.write(f"line {i + 1}\n")
        tmp = f.name

    try:
        result = _tail_file(Path(tmp), n=5)
        lines = result.splitlines()
        assert len(lines) == 5
        assert lines[0] == "line 96"
        assert lines[-1] == "line 100"
    finally:
        Path(tmp).unlink()


def test_tail_file_with_less_lines_than_requested():
    from aion.cli.logs import _tail_file

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
        f.write("hello\nworld\n")
        tmp = f.name

    try:
        result = _tail_file(Path(tmp), n=100)
        assert result == "hello\nworld"
    finally:
        Path(tmp).unlink()


def test_tail_file_nonexistent():
    from aion.cli.logs import _tail_file

    result = _tail_file(Path("/nonexistent/log.log"), n=10)
    assert result == ""
