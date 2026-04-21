from __future__ import annotations

import asyncio
from typing import Any

import dashscope
from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer

from ..logging_utils import get_logger

logger = get_logger("dashscope_tts")


class _TTSCallback(ResultCallback):
    """Bridge synchronous TTS callbacks to an asyncio.Queue."""

    def __init__(self, queue: asyncio.Queue[bytes | None], loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    def on_open(self) -> None:
        logger.info("tts_ws_opened")

    def on_close(self) -> None:
        logger.info("tts_ws_closed")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def on_complete(self) -> None:
        logger.info("tts_task_completed")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def on_error(self, message: str) -> None:
        logger.error("tts_error message=%s", message)
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def on_event(self, message: Any) -> None:
        pass

    def on_data(self, data: bytes) -> None:
        if data:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, data)


class AsyncTTSWrapper:
    """Wrap DashScope SpeechSynthesizer (sync callback) into an async interface.

    Usage:
        await tts.start()
        await tts.send_text("Hello")
        await tts.send_text(" world")
        await tts.complete()
        # read audio chunks until None
        while True:
            chunk = await tts.read_audio()
            if chunk is None:
                break
    """

    def __init__(
        self,
        api_key: str,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longjielidou_v3",
        speech_rate: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._speech_rate = speech_rate
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._synthesizer: SpeechSynthesizer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        dashscope.api_key = self._api_key

        callback = _TTSCallback(self._queue, self._loop)
        self._synthesizer = SpeechSynthesizer(
            model=self._model,
            voice=self._voice,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            callback=callback,
            speech_rate=self._speech_rate,
        )
        logger.info("tts_started model=%s voice=%s speech_rate=%s", self._model, self._voice, self._speech_rate)

    async def send_text(self, text: str) -> None:
        if self._synthesizer is None:
            return
        logger.debug("tts_send_text len=%s", len(text))
        self._synthesizer.streaming_call(text)

    async def complete(self) -> None:
        if self._synthesizer is None:
            return
        logger.info("tts_streaming_complete")
        self._synthesizer.streaming_complete()

    async def read_audio(self) -> bytes | None:
        """Read one audio chunk. Returns None when synthesis is complete."""
        return await self._queue.get()

    async def stop(self) -> None:
        self._synthesizer = None
