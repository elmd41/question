from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from ..logging_utils import get_logger

logger = get_logger("dashscope_asr")


@dataclass(slots=True)
class ASREvent:
    kind: str  # "speech_started" | "partial" | "sentence_end" | "completed" | "error"
    text: str = ""
    is_final: bool = False
    error_message: str = ""


class _BridgeCallback(RecognitionCallback):
    """Bridge synchronous SDK callbacks to an asyncio.Queue via thread-safe put."""

    def __init__(self, queue: asyncio.Queue[ASREvent], loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    def on_open(self) -> None:
        logger.info("asr_ws_opened")

    def on_close(self) -> None:
        logger.info("asr_ws_closed")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, ASREvent(kind="completed"))

    def on_complete(self) -> None:
        logger.info("asr_task_completed")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, ASREvent(kind="completed"))

    def on_error(self, result: RecognitionResult) -> None:
        msg = result.message if hasattr(result, "message") else str(result)
        logger.error("asr_error message=%s", msg)
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait, ASREvent(kind="error", error_message=msg)
        )

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if not isinstance(sentence, dict) or "text" not in sentence:
            return
        text = sentence.get("text", "")
        is_end = RecognitionResult.is_sentence_end(sentence)
        if is_end:
            logger.info("asr_sentence_end text=%s", text)
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, ASREvent(kind="sentence_end", text=text, is_final=True)
            )
        else:
            logger.debug("asr_partial text=%s", text)
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, ASREvent(kind="partial", text=text, is_final=False)
            )


class AsyncASRWrapper:
    """Wrap DashScope Recognition (sync callback) into an async interface."""

    def __init__(
        self,
        api_key: str,
        model: str = "paraformer-realtime-v2",
        sample_rate: int = 24000,
        max_sentence_silence: int = 800,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._sample_rate = sample_rate
        self._max_sentence_silence = max_sentence_silence
        self._queue: asyncio.Queue[ASREvent] = asyncio.Queue()
        self._recognition: Recognition | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        dashscope.api_key = self._api_key

        callback = _BridgeCallback(self._queue, self._loop)
        # fun-asr 不支持 disfluency_removal / semantic_punctuation，只有 paraformer 才用
        is_paraformer = "paraformer" in self._model
        recognition_kwargs: dict = dict(
            model=self._model,
            format="pcm",
            sample_rate=self._sample_rate,
            max_sentence_silence=self._max_sentence_silence,
            callback=callback,
        )
        if is_paraformer:
            recognition_kwargs["semantic_punctuation_enabled"] = False
            recognition_kwargs["disfluency_removal_enabled"] = True
        self._recognition = Recognition(**recognition_kwargs)
        logger.info(
            "asr_starting model=%s sample_rate=%s max_sentence_silence=%s",
            self._model,
            self._sample_rate,
            self._max_sentence_silence,
        )
        self._recognition.start()
        logger.info("asr_started")

    async def send_audio(self, chunk: bytes) -> None:
        if self._recognition is None:
            return
        self._recognition.send_audio_frame(chunk)

    async def stop(self) -> None:
        if self._recognition is not None:
            self._recognition.stop()
            self._recognition = None

    async def read_event(self) -> ASREvent:
        return await self._queue.get()
