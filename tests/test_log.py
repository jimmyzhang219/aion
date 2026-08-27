"""log 模块测试。"""

from aion.log import generate_traceid


class TestGenerateTraceId:
    def test_returns_32_char_hex(self):
        tid = generate_traceid()
        assert len(tid) == 32
        assert all(c in "0123456789abcdef" for c in tid)

    def test_unique_across_calls(self):
        tids = {generate_traceid() for _ in range(100)}
        assert len(tids) == 100
