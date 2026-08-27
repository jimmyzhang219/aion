"""ASR CLI 命令测试"""

from click.testing import CliRunner
from aion.audio.cli import asr


class TestASRCli:
    def setup_method(self):
        self.runner = CliRunner()

    def test_asr_status_idle(self):
        result = self.runner.invoke(asr, ["status"])
        assert result.exit_code == 0
        assert "空闲" in result.output

    def test_asr_stop_no_session(self):
        result = self.runner.invoke(asr, ["stop"])
        assert result.exit_code == 0
        assert "没有正在运行" in result.output

    def test_asr_list_no_recordings(self):
        result = self.runner.invoke(asr, ["list"])
        assert result.exit_code == 0

    def test_asr_start_file_no_path(self):
        result = self.runner.invoke(asr, ["start", "-s", "file"])
        assert result.exit_code != 0
        assert "需要指定音频文件路径" in result.output
