"""ASR 转录结果存储 — TranscriptRecorder 测试。"""

from __future__ import annotations

import json
import os
import tempfile

from aion.audio.provider import TranscriptChunk
from aion.audio.transcript import TranscriptRecorder


def test_record_and_finalize() -> None:
    """完整流程：写入 2 句最终结果 + 1 句中间结果 + 原始音频，验证所有产出文件。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_id = "test-session-001"
        recorder = TranscriptRecorder(tmpdir, session_id)

        # 最终结果 1
        chunk1 = TranscriptChunk(
            begin_time=0,
            end_time=1200,
            text="Hello world",
            is_final=True,
            confidence=0.95,
        )
        # 最终结果 2
        chunk2 = TranscriptChunk(
            begin_time=1200,
            end_time=2500,
            text="How are you",
            is_final=True,
            confidence=0.92,
        )
        # 中间结果（应被跳过）
        interim = TranscriptChunk(
            begin_time=0,
            end_time=500,
            text="Hello",
            is_final=False,
            confidence=0.50,
        )

        recorder.record_sentence(chunk1)
        recorder.record_sentence(interim)  # 不应记入最终结果
        recorder.record_sentence(chunk2)

        # 原始音频
        raw_data = b"\x00\x01\x02\x03" * 100
        recorder.record_raw_audio(raw_data)
        recorder.record_raw_audio(raw_data)

        # 结束
        recorder.finalize()

        dir_path = recorder.dir_path
        json_path = os.path.join(dir_path, "transcript.json")
        txt_path = os.path.join(dir_path, "transcript.txt")
        pcm_path = os.path.join(dir_path, "audio.raw.pcm")

        # ---- JSON 校验 ----
        assert os.path.exists(json_path)
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["session_id"] == session_id
        assert data["duration_secs"] > 0
        assert len(data["sentences"]) == 2
        assert data["sentences"][0]["text"] == "Hello world"
        assert data["sentences"][1]["text"] == "How are you"
        assert data["sentences"][0]["is_final"] is True
        assert data["sentences"][1]["is_final"] is True

        # ---- TXT 校验 ----
        assert os.path.exists(txt_path)
        with open(txt_path, "r", encoding="utf-8") as f:
            lines = f.read().strip().split("\n")
        assert len(lines) == 2
        assert "Hello world" in lines[0]
        assert "How are you" in lines[1]

        # ---- PCM 校验 ----
        assert os.path.exists(pcm_path)
        assert os.path.getsize(pcm_path) == len(raw_data) * 2  # 两次写入
