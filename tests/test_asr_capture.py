"""ASR 音频采集模块测试"""

import pytest
from aion.audio.capture import AudioSource
from aion.audio.capture.microphone import MicrophoneSource


class TestMicrophoneSource:
    def test_is_audio_source(self):
        assert issubclass(MicrophoneSource, AudioSource)

    @pytest.mark.asyncio
    async def test_start_stop_no_crash(self):
        mic = MicrophoneSource()
        try:
            await mic.start()
        except OSError:
            pytest.skip("No microphone device available")
        await mic.stop()
