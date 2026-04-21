from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from ..logging_utils import get_logger
from ..schemas import MuseumConfig
from ..settings import Settings
from .dashscope_asr import ASREvent, AsyncASRWrapper
from .dashscope_llm import stream_chat
from .dashscope_tts import AsyncTTSWrapper
from .protocol import UpstreamEvent

logger = get_logger("aliyun_split")


class AliyunSplitClient:
    """Split-pipeline upstream: ASR (Paraformer) → LLM (Qwen) → TTS (CosyVoice).

    Implements the same interface as MockRealtimeClient (no session_id params).
    Audio is continuously forwarded to ASR; server-side VAD decides sentence boundaries.
    On sentence_end, the recognized text is sent through LLM then TTS.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.queue: asyncio.Queue[UpstreamEvent] = asyncio.Queue()
        self._asr: AsyncASRWrapper | None = None
        self._tts: AsyncTTSWrapper | None = None
        self._asr_task: asyncio.Task | None = None
        self._llm_tts_task: asyncio.Task | None = None
        self._cancel: asyncio.Event = asyncio.Event()
        self._config: MuseumConfig | None = None
        self.conversation_history: list[dict[str, str]] = []
        self._speaking = False
        self._closed = False

    # ── UpstreamClient interface ──────────────────────────────────

    async def connect(self) -> None:
        api_key = self.settings.qwen_api_key
        asr_model = getattr(self.settings, "aliyun_asr_model", "paraformer-realtime-v2")
        max_silence = getattr(self.settings, "aliyun_asr_max_sentence_silence", 800)

        logger.info(
            "upstream_connect mode=aliyun_split asr_model=%s max_silence=%s api_key_suffix=%s",
            asr_model,
            max_silence,
            api_key[-6:] if api_key else "<empty>",
        )
        self._asr = AsyncASRWrapper(
            api_key=api_key,
            model=asr_model,
            sample_rate=16000,
            max_sentence_silence=max_silence,
        )
        await self._asr.start()

    async def start_session(self, config: MuseumConfig, session_id: str) -> None:
        self._config = config
        self._closed = False
        self._cancel.clear()
        logger.info(
            "upstream_start_session mode=aliyun_split session_id=%s",
            session_id,
        )
        await self.queue.put(
            UpstreamEvent(
                event=100,
                message_type="SERVER_FULL_RESPONSE",
                payload={"session_id": session_id},
            )
        )
        self._asr_task = asyncio.create_task(self._asr_receive_loop())

    async def say_hello(self, text: str) -> None:
        logger.info("upstream_say_hello mode=aliyun_split text_len=%s", len(text))
        await self._run_tts_only(text, reply_id="greeting-hello")

    async def send_audio(self, audio_chunk: bytes) -> None:
        if self._asr is None:
            return
        await self._asr.send_audio(audio_chunk)

    async def receive(self) -> UpstreamEvent:
        return await self.queue.get()

    def notify_playback_ended(self) -> None:
        """Frontend signals TTS playback has fully drained."""
        logger.info("playback_ended speaking=%s", self._speaking)
        self._speaking = False

    async def finish_session(self) -> None:
        self._closed = True
        self._speaking = False
        self._cancel.set()
        if self._asr is not None:
            await self._asr.stop()
            self._asr = None
        if self._llm_tts_task is not None:
            self._llm_tts_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._llm_tts_task
            self._llm_tts_task = None
        self.conversation_history.clear()

    async def finish_connection(self) -> None:
        pass

    async def close(self) -> None:
        self._closed = True
        self._cancel.set()
        if self._asr_task is not None:
            self._asr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._asr_task
            self._asr_task = None
        if self._llm_tts_task is not None:
            self._llm_tts_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._llm_tts_task
            self._llm_tts_task = None
        if self._asr is not None:
            await self._asr.stop()
            self._asr = None
        if self._tts is not None:
            await self._tts.stop()
            self._tts = None
        self.conversation_history.clear()

    # ── Internal ──────────────────────────────────────────────────

    async def _asr_receive_loop(self) -> None:
        """Read ASR events and dispatch to queue / trigger LLM+TTS."""
        try:
            while not self._closed and self._asr is not None:
                event = await self._asr.read_event()

                if event.kind == "sentence_end" and event.text.strip():
                    if self._speaking:
                        logger.info("asr_sentence_end_dropped speaking=True text=%s", event.text.strip())
                        continue
                    user_text = event.text.strip()
                    logger.info("asr_sentence_end text=%s", user_text)

                    # Emit user transcript
                    await self.queue.put(
                        UpstreamEvent(
                            event=451,
                            message_type="SERVER_FULL_RESPONSE",
                            payload={"results": [{"text": user_text, "is_interim": False}]},
                        )
                    )
                    # Emit ASR ended → thinking state
                    await self.queue.put(
                        UpstreamEvent(
                            event=459,
                            message_type="SERVER_FULL_RESPONSE",
                            payload={},
                        )
                    )

                    # Cancel any ongoing LLM+TTS (barge-in)
                    self._cancel.set()
                    if self._llm_tts_task is not None:
                        self._llm_tts_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await self._llm_tts_task

                    # Start new LLM+TTS pipeline
                    self._cancel.clear()
                    self._llm_tts_task = asyncio.create_task(
                        self._run_llm_tts(user_text)
                    )

                elif event.kind == "partial":
                    # Intermediate ASR result — can be used for live captions
                    pass

                elif event.kind == "error":
                    logger.error("asr_error message=%s", event.error_message)

                elif event.kind == "completed":
                    logger.info("asr_session_completed")
                    break

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("asr_receive_loop_error error=%s", exc)

    async def _run_llm_tts(self, user_text: str) -> None:
        """Run LLM streaming → TTS streaming pipeline."""
        if self._config is None:
            return

        api_key = self.settings.qwen_api_key
        llm_model = getattr(self.settings, "aliyun_llm_model", "qwen3.5-flash")
        tts_model = getattr(self.settings, "aliyun_tts_model", "cosyvoice-v3-flash")
        tts_voice = getattr(self.settings, "aliyun_tts_voice", "longjielidou_v3")

        # Build messages with conversation history + round tracking
        round_num = len(self.conversation_history) // 2 + 1
        if round_num >= 5:
            round_hint = (
                f"\n\n【第{round_num}轮 - 必须猜测】"
                "现在必须给出你的年龄猜测！格式：我猜你XX岁！因为……"
                "不要再问问题了，直接猜。"
            )
        elif round_num >= 3:
            round_hint = (
                f"\n\n【第{round_num}轮 - 准备猜测】"
                "你最多还能问1-2个问题就必须猜了，问最关键的。"
            )
        else:
            round_hint = f"\n\n【第{round_num}轮】"

        system_content = (
            f"{self._config.system_role}\n{self._config.speaking_style}"
            "\n\n【必须遵守】"
            "1. 只问能区分年龄段的问题：小时候看什么动画片、玩什么游戏、第一部手机是什么、听谁的歌。"
            "2. 不要顺着对方的回答追问细节（比如对方提到爸爸就问爸爸的事），要换新话题。"
            "3. 不要说废话，直接问。严格20字以内。"
            "4. 禁止问年龄、几岁、生日、出生年份、年级、上学还是上班。"
            f"{round_hint}"
        )
        messages = [
            {"role": "system", "content": system_content},
            *self.conversation_history,
            {"role": "user", "content": user_text},
        ]

        reply_id = str(uuid.uuid4())
        full_reply = ""

        # Mark speaking started
        self._speaking = True

        # Start TTS session (speech_rate=1.2 for faster delivery)
        self._tts = AsyncTTSWrapper(
            api_key=api_key,
            model=tts_model,
            voice=tts_voice,
            speech_rate=1.2,
        )
        await self._tts.start()

        # TTS audio reader task
        tts_reader_task = asyncio.create_task(self._tts_audio_reader())

        try:
            async for text_chunk in stream_chat(api_key, llm_model, messages, max_tokens=60, temperature=0.3):
                if self._cancel.is_set():
                    break
                full_reply += text_chunk
                await self.queue.put(
                    UpstreamEvent(
                        event=550,
                        message_type="SERVER_FULL_RESPONSE",
                        payload={"content": text_chunk, "reply_id": reply_id},
                    )
                )
                await self._tts.send_text(text_chunk)

            if not self._cancel.is_set():
                await self._tts.complete()

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("llm_tts_error error=%s", exc)
        finally:
            # Wait for TTS reader to finish consuming audio
            if tts_reader_task is not None and not tts_reader_task.done():
                # Give it a moment to drain remaining audio
                try:
                    await asyncio.wait_for(tts_reader_task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    tts_reader_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await tts_reader_task

            # Emit TTS end event
            if not self._cancel.is_set():
                await self.queue.put(
                    UpstreamEvent(
                        event=359,
                        message_type="SERVER_FULL_RESPONSE",
                        payload={},
                    )
                )

            # Update conversation history
            if full_reply:
                self.conversation_history.append({"role": "user", "content": user_text})
                self.conversation_history.append({"role": "assistant", "content": full_reply})

            # Release speaking lock as soon as TTS audio is fully queued
            self._speaking = False

            await self._tts.stop()
            self._tts = None

    async def _tts_audio_reader(self) -> None:
        """Read audio chunks from TTS and forward to queue."""
        if self._tts is None:
            return
        try:
            while not self._cancel.is_set():
                chunk = await self._tts.read_audio()
                if chunk is None:
                    break
                await self.queue.put(
                    UpstreamEvent(
                        event=352,
                        message_type="SERVER_ACK",
                        payload=chunk,
                    )
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("tts_audio_reader_error error=%s", exc)

    async def _run_tts_only(self, text: str, reply_id: str = "greeting-hello") -> None:
        """TTS-only path for welcome message (no LLM)."""
        api_key = self.settings.qwen_api_key
        tts_model = getattr(self.settings, "aliyun_tts_model", "cosyvoice-v3-flash")
        tts_voice = getattr(self.settings, "aliyun_tts_voice", "longjielidou_v3")

        self._speaking = True

        self._tts = AsyncTTSWrapper(
            api_key=api_key,
            model=tts_model,
            voice=tts_voice,
            speech_rate=1.2,
        )
        await self._tts.start()

        tts_reader_task = asyncio.create_task(self._tts_audio_reader())

        try:
            # Emit text event for frontend caption
            await self.queue.put(
                UpstreamEvent(
                    event=550,
                    message_type="SERVER_FULL_RESPONSE",
                    payload={"content": "", "reply_id": reply_id},
                )
            )
            await self.queue.put(
                UpstreamEvent(
                    event=550,
                    message_type="SERVER_FULL_RESPONSE",
                    payload={"content": text, "reply_id": reply_id},
                )
            )

            await self._tts.send_text(text)
            await self._tts.complete()

            # Wait for TTS reader to drain
            try:
                await asyncio.wait_for(tts_reader_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                tts_reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tts_reader_task

            await self.queue.put(
                UpstreamEvent(
                    event=359,
                    message_type="SERVER_FULL_RESPONSE",
                    payload={},
                )
            )
        except Exception as exc:
            logger.error("tts_only_error error=%s", exc)
        finally:
            # Release speaking lock as soon as TTS audio is fully queued
            self._speaking = False
            await self._tts.stop()
            self._tts = None
