from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .logging_utils import get_logger
from .realtime.upstream import MockRealtimeClient, QwenRealtimeClient, VolcengineRealtimeClient, create_upstream_client
from .realtime.aliyun_split import AliyunSplitClient
from .schemas import MuseumConfig
from .settings import Settings
from .store import ConfigStore

logger = get_logger("session")


@dataclass(slots=True)
class SessionHandle:
    client_id: str
    resume_token: str
    session_id: str
    config_version: int
    config: MuseumConfig
    websocket: WebSocket | None
    upstream: MockRealtimeClient | VolcengineRealtimeClient | QwenRealtimeClient
    created_at: float = field(default_factory=time.monotonic)
    last_activity_at: float = field(default_factory=time.monotonic)
    state: str = "opening_session"
    closed: bool = False
    detached_deadline: float | None = None
    receive_task: asyncio.Task | None = None
    detach_task: asyncio.Task | None = None
    greeting_active: bool = True
    first_tts_seen: bool = False
    assistant_reply_id: str | None = None
    assistant_text_buffer: str = ""


class RealtimeSessionManager:
    def __init__(self, settings: Settings, store: ConfigStore) -> None:
        self.settings = settings
        self.store = store
        self._lock = asyncio.Lock()
        self._active: SessionHandle | None = None

    async def start_session(
        self,
        websocket: WebSocket,
        client_id: str,
        resume_token: str | None,
    ) -> SessionHandle:
        async with self._lock:
            if (
                self._active
                and not self._active.closed
                and self._active.client_id == client_id
                and resume_token
                and self._active.resume_token == resume_token
            ):
                if self._active.detach_task is not None:
                    self._active.detach_task.cancel()
                    self._active.detach_task = None
                self._active.websocket = websocket
                self._active.detached_deadline = None
                await self._send_json(
                    self._active,
                    {
                        "type": "session_ready",
                        "sessionId": self._active.session_id,
                        "resumeToken": self._active.resume_token,
                        "configVersion": self._active.config_version,
                        "autoEndMode": self._active.config.auto_end_mode,
                        "idleTimeoutSec": self._active.config.idle_timeout_sec,
                        "resumed": True,
                        "state": self._active.state,
                        "upstreamMode": self.settings.upstream_mode,
                        "upstreamClient": type(self._active.upstream).__name__,
                    },
                )
                self.store.log_session_event(
                    self._active.session_id,
                    client_id,
                    self._active.config_version,
                    "session_resumed",
                    {"state": self._active.state},
                )
                return self._active

            if self._active and not self._active.closed:
                await self._close_session_locked(self._active, "session_replaced")

            published = self.store.get_published()
            handle = SessionHandle(
                client_id=client_id,
                resume_token=secrets.token_urlsafe(24),
                session_id=str(uuid.uuid4()),
                config_version=published.version,
                config=published.config,
                websocket=websocket,
                upstream=create_upstream_client(self.settings),
            )
            self._active = handle
            logger.info(
                "session_opening session_id=%s client_id=%s upstream_mode=%s upstream_client=%s config_version=%s speaker=%s playback_tone=%s",
                handle.session_id,
                handle.client_id,
                self.settings.upstream_mode,
                type(handle.upstream).__name__,
                handle.config_version,
                handle.config.speaker,
                handle.config.playback_tone,
            )
            timeout = self.settings.upstream_connect_timeout_seconds
            try:
                await asyncio.wait_for(handle.upstream.connect(), timeout=timeout)
                await asyncio.wait_for(handle.upstream.start_session(handle.config, handle.session_id), timeout=timeout)
                if isinstance(handle.upstream, (MockRealtimeClient, AliyunSplitClient)):
                    await asyncio.wait_for(handle.upstream.say_hello(handle.config.welcome_text), timeout=timeout)
                else:
                    await asyncio.wait_for(handle.upstream.say_hello(handle.config.welcome_text, handle.session_id), timeout=timeout)
            except asyncio.TimeoutError as exc:
                await self._cleanup_failed_open(handle)
                raise RuntimeError("开启对话超时，请检查网络或上游实时语音配置。") from exc
            except Exception as exc:
                await self._cleanup_failed_open(handle)
                raise RuntimeError(f"开启对话失败：{exc}") from exc

            handle.state = "greeting"
            handle.receive_task = asyncio.create_task(self._receive_loop(handle))
            self.store.log_session_event(
                handle.session_id,
                handle.client_id,
                handle.config_version,
                "session_created",
                {"mode": self.settings.upstream_mode},
            )
            await self._send_json(
                handle,
                {
                    "type": "session_ready",
                    "sessionId": handle.session_id,
                    "resumeToken": handle.resume_token,
                    "configVersion": handle.config_version,
                    "autoEndMode": handle.config.auto_end_mode,
                    "idleTimeoutSec": handle.config.idle_timeout_sec,
                    "resumed": False,
                    "state": handle.state,
                    "upstreamMode": self.settings.upstream_mode,
                    "upstreamClient": type(handle.upstream).__name__,
                },
            )
            # Send welcome text as subtitle so greeting shows captions
            greeting_reply_id = f"greeting-{handle.session_id}"
            handle.assistant_reply_id = greeting_reply_id
            handle.assistant_text_buffer = handle.config.welcome_text
            await self._send_json(
                handle,
                {
                    "type": "assistant_text",
                    "text": handle.config.welcome_text,
                    "replyId": greeting_reply_id,
                },
            )
            return handle

    async def send_audio(self, handle: SessionHandle, audio_chunk: bytes) -> None:
        if handle.closed:
            return
        handle.last_activity_at = time.monotonic()
        if not handle.first_tts_seen and len(audio_chunk) > 0:
            logger.debug(
                "session_audio_upload session_id=%s bytes=%s state=%s",
                handle.session_id,
                len(audio_chunk),
                handle.state,
            )
        if isinstance(handle.upstream, (MockRealtimeClient, AliyunSplitClient)):
            await handle.upstream.send_audio(audio_chunk)
        else:
            await handle.upstream.send_audio(handle.session_id, audio_chunk)

    async def record_voice_activity(self, handle: SessionHandle, speaking: bool, level: float | None = None) -> None:
        if handle.closed or not speaking:
            return
        handle.last_activity_at = time.monotonic()
        self.store.log_session_event(
            handle.session_id,
            handle.client_id,
            handle.config_version,
            "local_vad_start",
            {"level": level},
        )

    async def heartbeat(self, handle: SessionHandle) -> None:
        if handle.closed:
            return

    async def detach(self, handle: SessionHandle, websocket: WebSocket) -> None:
        async with self._lock:
            if handle.closed or handle.websocket is not websocket:
                return
            handle.websocket = None
            handle.detached_deadline = time.monotonic() + self.settings.session_resume_window_seconds
            handle.detach_task = asyncio.create_task(self._detach_timeout(handle))
            self.store.log_session_event(
                handle.session_id,
                handle.client_id,
                handle.config_version,
                "browser_detached",
                {"resumeWindowSec": self.settings.session_resume_window_seconds},
            )

    async def close_session(self, handle: SessionHandle, reason: str) -> None:
        async with self._lock:
            await self._close_session_locked(handle, reason)

    async def force_close_active(self, reason: str) -> bool:
        async with self._lock:
            if self._active is None or self._active.closed:
                return False
            await self._close_session_locked(self._active, reason)
            return True

    async def _detach_timeout(self, handle: SessionHandle) -> None:
        try:
            await asyncio.sleep(self.settings.session_resume_window_seconds)
            async with self._lock:
                if self._active is handle and not handle.closed and handle.websocket is None:
                    await self._close_session_locked(handle, "resume_timeout")
        except asyncio.CancelledError:
            return

    async def _cleanup_failed_open(self, handle: SessionHandle) -> None:
        handle.closed = True
        if handle.detach_task is not None:
            handle.detach_task.cancel()
            handle.detach_task = None
        if handle.receive_task is not None:
            handle.receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handle.receive_task
            handle.receive_task = None

        with contextlib.suppress(Exception):
            if isinstance(handle.upstream, MockRealtimeClient):
                await handle.upstream.finish_session()
                await handle.upstream.finish_connection()
            else:
                await handle.upstream.finish_session(handle.session_id)
                await handle.upstream.finish_connection()

        with contextlib.suppress(Exception):
            await handle.upstream.close()

        if self._active is handle:
            self._active = None

    async def _receive_loop(self, handle: SessionHandle) -> None:
        try:
            while not handle.closed:
                event = await handle.upstream.receive()
                await self._handle_upstream_event(handle, event)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            async with self._lock:
                if not handle.closed:
                    await self._close_session_locked(handle, "upstream_error", {"detail": str(exc)})

    async def _handle_upstream_event(self, handle: SessionHandle, event: Any) -> None:
        if handle.closed:
            return

        payload = event.payload if hasattr(event, "payload") else {}
        event_code = event.event if hasattr(event, "event") else None
        # event 352 = TTS audio chunk, very noisy → DEBUG
        log_fn = logger.debug if event_code == 352 else logger.info
        log_fn(
            "session_upstream_event session_id=%s event=%s message_type=%s state=%s payload=%s",
            handle.session_id,
            event_code,
            getattr(event, "message_type", "UNKNOWN"),
            handle.state,
            summarize_payload(payload),
        )

        if event_code == 450:
            handle.state = "listening"
            self.store.log_session_event(
                handle.session_id,
                handle.client_id,
                handle.config_version,
                "barge_in",
                None,
            )
            await self._send_json(handle, {"type": "barge_in_confirmed"})
            await self._send_json(handle, {"type": "state_changed", "state": handle.state})
            return

        if event_code == 451:
            results = payload.get("results", []) if isinstance(payload, dict) else []
            if results:
                latest = results[-1]
                await self._send_json(
                    handle,
                    {
                        "type": "user_transcript",
                        "text": latest.get("text", ""),
                        "isInterim": bool(latest.get("is_interim", False)),
                    },
                )
            return

        if event_code == 459:
            handle.state = "thinking"
            handle.last_activity_at = time.monotonic()
            self.store.log_session_event(
                handle.session_id,
                handle.client_id,
                handle.config_version,
                "asr_ended",
                None,
            )
            await self._send_json(handle, {"type": "state_changed", "state": handle.state})
            return

        if event_code == 550:
            content = payload.get("content", "") if isinstance(payload, dict) else ""
            reply_id = payload.get("reply_id") if isinstance(payload, dict) else None
            if reply_id != handle.assistant_reply_id:
                handle.assistant_reply_id = reply_id if isinstance(reply_id, str) else None
                handle.assistant_text_buffer = ""

            if isinstance(content, str) and content:
                handle.assistant_text_buffer += content

            if not handle.assistant_text_buffer:
                return

            self.store.log_session_event(
                handle.session_id,
                handle.client_id,
                handle.config_version,
                "assistant_text",
                {"content": handle.assistant_text_buffer, "reply_id": handle.assistant_reply_id},
            )
            logger.debug("Sending assistant_text to frontend: buffer=%s", handle.assistant_text_buffer)
            await self._send_json(
                handle,
                {
                    "type": "assistant_text",
                    "text": handle.assistant_text_buffer,
                    "replyId": handle.assistant_reply_id,
                },
            )
            return

        if event_code == 352:
            if not handle.first_tts_seen:
                handle.first_tts_seen = True
                handle.state = "speaking"
                self.store.log_session_event(
                    handle.session_id,
                    handle.client_id,
                    handle.config_version,
                    "first_tts_audio",
                    None,
                )
                logger.info("session_tts_started session_id=%s", handle.session_id)
                await self._send_json(handle, {"type": "state_changed", "state": handle.state})
            handle.last_activity_at = time.monotonic()
            await self._send_bytes(handle, payload if isinstance(payload, bytes) else b"")
            return

        if event_code == 359:
            handle.first_tts_seen = False
            handle.last_activity_at = time.monotonic()
            phase = "greeting" if handle.greeting_active else "assistant"
            handle.greeting_active = False
            handle.state = "listening"
            self.store.log_session_event(
                handle.session_id,
                handle.client_id,
                handle.config_version,
                "tts_ended",
                {"phase": phase},
            )
            logger.info("session_tts_ended session_id=%s phase=%s", handle.session_id, phase)
            await self._send_json(handle, {"type": "tts_end", "phase": phase})
            await self._send_json(handle, {"type": "state_changed", "state": handle.state})
            if isinstance(payload, dict) and payload.get("status_code") == "20000002":
                async with self._lock:
                    if not handle.closed:
                        await self._close_session_locked(handle, "user_exit")

    async def _close_session_locked(
        self,
        handle: SessionHandle,
        reason: str,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        if handle.closed:
            return
        handle.closed = True
        if handle.detach_task is not None:
            handle.detach_task.cancel()
            handle.detach_task = None

        if isinstance(handle.upstream, (MockRealtimeClient, AliyunSplitClient)):
            await handle.upstream.finish_session()
            await handle.upstream.finish_connection()
            await handle.upstream.close()
        else:
            await handle.upstream.finish_session(handle.session_id)
            await handle.upstream.finish_connection()
            await handle.upstream.close()

        current_task = asyncio.current_task()
        if handle.receive_task is not None:
            if handle.receive_task is not current_task:
                handle.receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await handle.receive_task
            handle.receive_task = None

        self.store.log_session_event(
            handle.session_id,
            handle.client_id,
            handle.config_version,
            "session_closed",
            {"reason": reason, **(extra_payload or {})},
        )
        logger.info("session_closed session_id=%s reason=%s", handle.session_id, reason)
        await self._send_json(handle, {"type": "session_closed", "reason": reason})
        if handle.websocket is not None:
            with contextlib.suppress(Exception):
                await handle.websocket.close()
        if self._active is handle:
            self._active = None

    async def _send_json(self, handle: SessionHandle, payload: dict[str, Any]) -> None:
        if handle.websocket is None:
            return
        with contextlib.suppress(Exception):
            await handle.websocket.send_json(payload)

    async def _send_bytes(self, handle: SessionHandle, payload: bytes) -> None:
        if handle.websocket is None or not payload:
            return
        with contextlib.suppress(Exception):
            await handle.websocket.send_bytes(payload)


def summarize_payload(payload: Any) -> str:
    if payload is None:
        return "null"
    if isinstance(payload, bytes):
        return f"bytes:{len(payload)}"
    if isinstance(payload, dict):
        reduced: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str) and len(value) > 120:
                reduced[key] = f"{value[:117]}..."
            elif isinstance(value, bytes):
                reduced[key] = f"bytes:{len(value)}"
            elif isinstance(value, list) and len(value) > 4:
                reduced[key] = f"list:{len(value)}"
            else:
                reduced[key] = value
        return str(reduced)
    if isinstance(payload, list):
        return f"list:{len(payload)}"
    return str(payload)
