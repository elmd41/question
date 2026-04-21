from __future__ import annotations

import asyncio
import base64
import contextlib
import json as _json
import uuid
from dataclasses import dataclass
from typing import Any

import websockets

from ..logging_utils import get_logger
from ..schemas import MuseumConfig
from ..settings import Settings
from .aliyun_split import AliyunSplitClient
from .protocol import UpstreamEvent, build_audio_frame, build_json_frame, parse_response

logger = get_logger("upstream")


def chunk_text(text: str, chunk_size: int = 6) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    return [cleaned[index : index + chunk_size] for index in range(0, len(cleaned), chunk_size)]


class MockRealtimeClient:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[UpstreamEvent] = asyncio.Queue()
        self.closed = False
        self._responding = False

    async def connect(self) -> None:
        logger.info("upstream_connect mode=mock")
        return

    async def start_session(self, _: MuseumConfig, session_id: str) -> None:
        logger.info("upstream_start_session mode=mock session_id=%s", session_id)
        await self.queue.put(
            UpstreamEvent(
                event=100,
                message_type="SERVER_FULL_RESPONSE",
                payload={"session_id": session_id},
            )
        )

    async def _stream_text(self, text: str, reply_id: str) -> None:
        await self.queue.put(
            UpstreamEvent(
                event=550,
                message_type="SERVER_FULL_RESPONSE",
                payload={"content": "", "reply_id": reply_id},
            )
        )
        for chunk in chunk_text(text):
            await self.queue.put(
                UpstreamEvent(
                    event=550,
                    message_type="SERVER_FULL_RESPONSE",
                    payload={"content": chunk, "reply_id": reply_id},
                )
            )
            await asyncio.sleep(0.02)

    async def say_hello(self, text: str) -> None:
        logger.info("upstream_say_hello mode=mock text_len=%s", len(text))
        await self._stream_text(text, "mock-welcome")
        await self.queue.put(UpstreamEvent(event=359, message_type="SERVER_FULL_RESPONSE", payload={}))

    async def send_audio(self, _: bytes) -> None:
        if self._responding:
            return
        self._responding = True
        logger.info("upstream_send_audio mode=mock")
        await self.queue.put(UpstreamEvent(event=450, message_type="SERVER_FULL_RESPONSE", payload={}))
        await asyncio.sleep(0.05)
        await self.queue.put(
            UpstreamEvent(
                event=451,
                message_type="SERVER_FULL_RESPONSE",
                payload={"results": [{"text": "这是模拟识别文本。", "is_interim": False}]},
            )
        )
        await asyncio.sleep(0.05)
        await self.queue.put(UpstreamEvent(event=459, message_type="SERVER_FULL_RESPONSE", payload={}))
        await asyncio.sleep(0.15)
        await self._stream_text(
            "当前运行在模拟模式。配置好上游密钥后，这里会切换成真实实时语音。",
            str(uuid.uuid4()),
        )
        await asyncio.sleep(0.05)
        await self.queue.put(UpstreamEvent(event=352, message_type="SERVER_ACK", payload=b"\x00" * 960))
        await asyncio.sleep(0.05)
        await self.queue.put(UpstreamEvent(event=359, message_type="SERVER_FULL_RESPONSE", payload={}))
        self._responding = False

    async def receive(self) -> UpstreamEvent:
        event = await self.queue.get()
        logger.info(
            "upstream_receive mode=mock event=%s message_type=%s payload=%s",
            event.event,
            event.message_type,
            summarize_payload(event.payload),
        )
        return event

    async def finish_session(self) -> None:
        return

    async def finish_connection(self) -> None:
        return

    async def close(self) -> None:
        self.closed = True


class VolcengineRealtimeClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ws = None

    async def connect(self) -> None:
        headers = {
            "X-Api-App-ID": self.settings.upstream_app_id,
            "X-Api-Access-Key": self.settings.upstream_access_key,
            "X-Api-Resource-Id": self.settings.upstream_resource_id,
            "X-Api-App-Key": self.settings.upstream_app_key,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        logger.info(
            "upstream_connect mode=volcengine url=%s app_id=%s access_key_suffix=%s resource_id=%s",
            self.settings.upstream_base_url,
            mask_value(self.settings.upstream_app_id),
            suffix_value(self.settings.upstream_access_key),
            self.settings.upstream_resource_id,
        )
        self.ws = await websockets.connect(
            self.settings.upstream_base_url,
            additional_headers=headers,
            ping_interval=None,
        )
        await self.ws.send(build_json_frame(1, {}))
        ack = parse_response(await self.ws.recv())
        logger.info(
            "upstream_connect_ack mode=volcengine event=%s message_type=%s payload=%s",
            ack.get("event"),
            ack.get("message_type"),
            summarize_payload(ack.get("payload_msg")),
        )

    async def start_session(self, config: MuseumConfig, session_id: str) -> None:
        if self.ws is None:
            raise RuntimeError("websocket is not connected")
        logger.info(
            "upstream_start_session mode=volcengine session_id=%s model_family=%s speaker=%s",
            session_id,
            config.model_family,
            config.speaker,
        )
        await self.ws.send(build_json_frame(100, config.to_upstream_payload(), session_id=session_id))
        ack = parse_response(await self.ws.recv())
        logger.info(
            "upstream_start_session_ack mode=volcengine session_id=%s event=%s message_type=%s payload=%s",
            session_id,
            ack.get("event"),
            ack.get("message_type"),
            summarize_payload(ack.get("payload_msg")),
        )

    async def say_hello(self, text: str, session_id: str) -> None:
        if self.ws is None:
            raise RuntimeError("websocket is not connected")
        logger.info(
            "upstream_say_hello mode=volcengine session_id=%s text_len=%s",
            session_id,
            len(text),
        )
        await self.ws.send(build_json_frame(300, {"content": text}, session_id=session_id))

    async def send_audio(self, session_id: str, audio_chunk: bytes) -> None:
        if self.ws is None:
            raise RuntimeError("websocket is not connected")
        logger.debug(
            "upstream_send_audio mode=volcengine session_id=%s bytes=%s",
            session_id,
            len(audio_chunk),
        )
        await self.ws.send(build_audio_frame(200, audio_chunk, session_id=session_id))

    async def receive(self) -> UpstreamEvent:
        if self.ws is None:
            raise RuntimeError("websocket is not connected")
        response = await self.ws.recv()
        parsed = parse_response(response)
        logger.info(
            "upstream_receive mode=volcengine event=%s message_type=%s session_id=%s payload=%s",
            parsed.get("event"),
            parsed.get("message_type"),
            parsed.get("session_id"),
            summarize_payload(parsed.get("payload_msg")),
        )
        return UpstreamEvent(
            event=parsed.get("event"),
            message_type=parsed.get("message_type", "UNKNOWN"),
            payload=parsed.get("payload_msg"),
        )

    async def finish_session(self, session_id: str) -> None:
        if self.ws is None:
            return
        await self.ws.send(build_json_frame(102, {}, session_id=session_id))

    async def finish_connection(self) -> None:
        if self.ws is None:
            return
        await self.ws.send(build_json_frame(2, {}))

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

class QwenRealtimeClient:
    """DashScope Qwen Omni Realtime client — OpenAI-compatible WS protocol."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ws: Any = None
        self.queue: asyncio.Queue[UpstreamEvent] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._text_buffer: str = ""
        self._current_reply_id: str | None = None

    async def connect(self) -> None:
        api_key = self.settings.qwen_api_key
        base_url = self.settings.qwen_base_url
        model = self.settings.qwen_model
        url = f"{base_url}?model={model}"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        logger.info(
            "upstream_connect mode=qwen url=%s model=%s api_key_suffix=%s",
            base_url,
            model,
            suffix_value(api_key),
        )
        self.ws = await websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
        )
        # Wait for session.created
        raw = await self.ws.recv()
        msg = _parse_json(raw)
        logger.info("upstream_connect_ack mode=qwen type=%s", msg.get("type"))

    async def start_session(self, config: MuseumConfig, session_id: str) -> None:
        if self.ws is None:
            raise RuntimeError("websocket is not connected")
        voice = self.settings.qwen_voice
        instructions = f"{config.system_role}\n{config.speaking_style}"
        logger.info(
            "upstream_start_session mode=qwen session_id=%s voice=%s",
            session_id,
            voice,
        )
        await self._send_json({
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "instructions": instructions,
                "voice": voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {
                    "type": "server_vad",
                    "silence_duration_ms": 800,
                },
                "input_audio_transcription": {
                    "model": "qwen-transcription",
                },
            },
        })
        # Wait for session.updated
        raw = await self.ws.recv()
        msg = _parse_json(raw)
        logger.info("upstream_start_session_ack mode=qwen type=%s", msg.get("type"))
        # Emit session ack as event 100
        await self.queue.put(UpstreamEvent(
            event=100,
            message_type="SERVER_FULL_RESPONSE",
            payload={"session_id": session_id},
        ))
        # Start background receive loop
        self._recv_task = asyncio.create_task(self._ws_receive_loop())

    async def say_hello(self, text: str, session_id: str) -> None:
        if self.ws is None:
            raise RuntimeError("websocket is not connected")
        logger.info("upstream_say_hello mode=qwen text_len=%s", len(text))
        # Create a conversation item with text and trigger response
        await self._send_json({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"[系统指令：请用你的角色身份说出以下欢迎语，不要改变语义] {text}"}],
            },
        })
        await self._send_json({"type": "response.create"})

    async def send_audio(self, session_id: str, audio_chunk: bytes) -> None:
        if self.ws is None:
            raise RuntimeError("websocket is not connected")
        audio_b64 = base64.b64encode(audio_chunk).decode()
        await self._send_json({
            "type": "input_audio_buffer.append",
            "audio": audio_b64,
        })

    async def receive(self) -> UpstreamEvent:
        return await self.queue.get()

    async def finish_session(self, session_id: str) -> None:
        pass

    async def finish_connection(self) -> None:
        pass

    async def close(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def _send_json(self, payload: dict) -> None:
        if self.ws is None:
            return
        await self.ws.send(_json.dumps(payload, ensure_ascii=False))

    async def _ws_receive_loop(self) -> None:
        """Read WS messages and translate to UpstreamEvent queue."""
        try:
            while self.ws is not None:
                raw = await self.ws.recv()
                msg = _parse_json(raw)
                msg_type = msg.get("type", "")

                if msg_type == "input_audio_buffer.speech_started":
                    # Barge-in / user started speaking
                    self._text_buffer = ""
                    await self.queue.put(UpstreamEvent(event=450, message_type="SERVER_FULL_RESPONSE", payload={}))

                elif msg_type == "input_audio_buffer.speech_stopped":
                    # ASR ended
                    await self.queue.put(UpstreamEvent(event=459, message_type="SERVER_FULL_RESPONSE", payload={}))

                elif msg_type == "conversation.item.input_audio_transcription.completed":
                    # User transcript
                    transcript = msg.get("transcript", "")
                    await self.queue.put(UpstreamEvent(
                        event=451,
                        message_type="SERVER_FULL_RESPONSE",
                        payload={"results": [{"text": transcript, "is_interim": False}]},
                    ))

                elif msg_type == "response.audio.delta":
                    # TTS audio chunk
                    audio_b64 = msg.get("delta", "")
                    if audio_b64:
                        audio_bytes = base64.b64decode(audio_b64)
                        await self.queue.put(UpstreamEvent(
                            event=352,
                            message_type="SERVER_ACK",
                            payload=audio_bytes,
                        ))

                elif msg_type == "response.audio_transcript.delta":
                    # Assistant text chunk
                    delta = msg.get("delta", "")
                    reply_id = msg.get("item_id", self._current_reply_id)
                    if reply_id != self._current_reply_id:
                        self._current_reply_id = reply_id
                        self._text_buffer = ""
                    self._text_buffer += delta
                    await self.queue.put(UpstreamEvent(
                        event=550,
                        message_type="SERVER_FULL_RESPONSE",
                        payload={"content": delta, "reply_id": reply_id},
                    ))

                elif msg_type == "response.done":
                    # Full response complete — equivalent to TTS end
                    await self.queue.put(UpstreamEvent(
                        event=359,
                        message_type="SERVER_FULL_RESPONSE",
                        payload={},
                    ))

                elif msg_type == "error":
                    error_msg = msg.get("error", {}).get("message", "unknown error")
                    logger.error("upstream_error mode=qwen error=%s", error_msg)

                elif msg_type in {"response.created", "response.audio.done",
                                  "response.audio_transcript.done", "response.output_item.done",
                                  "response.output_item.added", "conversation.item.created",
                                  "session.created", "session.updated",
                                  "input_audio_buffer.committed", "rate_limits.updated",
                                  "response.content_part.added", "response.content_part.done",
                                  "conversation.item.input_audio_transcription.failed"}:
                    logger.debug("upstream_event_ignored mode=qwen type=%s", msg_type)
                else:
                    logger.info("upstream_event_unknown mode=qwen type=%s", msg_type)

        except asyncio.CancelledError:
            return
        except websockets.ConnectionClosed:
            logger.info("upstream_ws_closed mode=qwen")
        except Exception as exc:
            logger.error("upstream_receive_error mode=qwen error=%s", exc)


def _parse_json(raw: str | bytes) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        return _json.loads(raw)
    except Exception:
        return {}


def create_upstream_client(settings: Settings) -> MockRealtimeClient | VolcengineRealtimeClient | QwenRealtimeClient | AliyunSplitClient:
    if settings.upstream_mode == "mock":
        return MockRealtimeClient()
    if settings.upstream_mode == "qwen":
        return QwenRealtimeClient(settings)
    if settings.upstream_mode == "aliyun_split":
        return AliyunSplitClient(settings)
    return VolcengineRealtimeClient(settings)


def mask_value(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def suffix_value(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 6:
        return value
    return value[-6:]


def summarize_payload(payload: Any) -> str:
    if payload is None:
        return "null"
    if isinstance(payload, bytes):
        return f"bytes:{len(payload)}"
    if isinstance(payload, dict):
        summary: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, bytes):
                summary[key] = f"bytes:{len(value)}"
            elif isinstance(value, str) and len(value) > 120:
                summary[key] = f"{value[:117]}..."
            elif isinstance(value, list) and len(value) > 4:
                summary[key] = f"list:{len(value)}"
            else:
                summary[key] = value
        return str(summary)
    if isinstance(payload, list):
        return f"list:{len(payload)}"
    return str(payload)
