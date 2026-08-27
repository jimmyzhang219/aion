"""ASR 转录结果存储 — TranscriptRecorder."""

from __future__ import annotations

import json
import os
from datetime import datetime

from aion.audio.provider import TranscriptChunk


class TranscriptRecorder:
    """转录结果记录器，管理 JSON / TXT / RAW 文件存储。"""

    def __init__(
        self,
        workspace_dir: str,
        session_id: str,
        provider: str = "",
    ) -> None:
        date_str = datetime.now().strftime("%Y%m%d")
        dir_name = f"{date_str}_{session_id}"
        self._dir_path = os.path.join(workspace_dir, "recordings", dir_name)
        os.makedirs(self._dir_path, exist_ok=True)

        self._session_id = session_id
        self._started_at = datetime.now().isoformat()
        self._provider = provider
        self._sentences: list[TranscriptChunk] = []
        self._first_sentence = True

        # 初始化 JSON 文件头
        self._json_path = os.path.join(self._dir_path, "transcript.json")
        with open(self._json_path, "w", encoding="utf-8") as f:
            f.write("{\n")
            f.write(f'  "session_id": "{session_id}",\n')
            f.write(f'  "started_at": "{self._started_at}",\n')
            f.write(f'  "provider": "{provider}",\n')
            f.write('  "sentences": [\n')

    @property
    def dir_path(self) -> str:
        return self._dir_path

    # ------------------------------------------------------------------
    # 逐句追加最终结果到 JSON
    # ------------------------------------------------------------------

    def record_sentence(self, chunk: TranscriptChunk) -> None:
        """追加一条最终识别结果到 JSON 文件（中间结果 / 空文本跳过）。"""
        if not chunk.is_final or not chunk.text:
            return
        self._sentences.append(chunk)
        obj = {
            "begin_time": chunk.begin_time,
            "end_time": chunk.end_time,
            "text": chunk.text,
            "is_final": chunk.is_final,
            "confidence": chunk.confidence,
        }
        line = json.dumps(obj, ensure_ascii=False)
        with open(self._json_path, "a", encoding="utf-8") as f:
            if self._first_sentence:
                f.write(f"    {line}")
                self._first_sentence = False
            else:
                f.write(f",\n    {line}")

    # ------------------------------------------------------------------
    # 原始音频
    # ------------------------------------------------------------------

    def record_raw_audio(self, chunk: bytes) -> None:
        """追加原始 PCM 音频数据。"""
        raw_path = os.path.join(self._dir_path, "audio.raw.pcm")
        with open(raw_path, "ab") as f:
            f.write(chunk)

    # ------------------------------------------------------------------
    # 结束写入
    # ------------------------------------------------------------------

    def finalize(self) -> None:
        """回填 duration_secs，关闭 JSON，生成 transcript.txt。"""
        duration_secs = self._sentences[-1].end_time / 1000.0 if self._sentences else 0.0

        # 关闭 JSON
        with open(self._json_path, "a", encoding="utf-8") as f:
            f.write("\n  ],\n")
            f.write(f'  "duration_secs": {duration_secs}\n')
            f.write("}\n")

        # 生成 transcript.txt
        txt_path = os.path.join(self._dir_path, "transcript.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            for s in self._sentences:
                begin_sec = s.begin_time / 1000.0
                end_sec = s.end_time / 1000.0
                f.write(f"[{begin_sec:.3f}s - {end_sec:.3f}s] {s.text}\n")
