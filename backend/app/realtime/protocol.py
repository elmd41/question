from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 0b0001
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

MSG_WITH_EVENT = 0b0100
NO_SERIALIZATION = 0b0000
JSON = 0b0001
GZIP = 0b0001

def generate_header(
    version: int = PROTOCOL_VERSION,
    message_type: int = CLIENT_FULL_REQUEST,
    message_type_specific_flags: int = MSG_WITH_EVENT,
    serial_method: int = JSON,
    compression_type: int = GZIP,
    reserved_data: int = 0x00,
    extension_header: bytes = bytes(),
) -> bytearray:
    header = bytearray()
    header_size = int(len(extension_header) / 4) + 1
    header.append((version << 4) | header_size)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    header.extend(extension_header)
    return header

def build_json_frame(event: int, payload: dict[str, Any], session_id: str | None = None) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    frame = bytearray(generate_header())
    frame.extend(event.to_bytes(4, "big"))
    if session_id is None:
        frame.extend(len(body).to_bytes(4, "big"))
        frame.extend(body)
        return bytes(frame)

    session_bytes = session_id.encode("utf-8")
    frame.extend(len(session_bytes).to_bytes(4, "big"))
    frame.extend(session_bytes)
    frame.extend(len(body).to_bytes(4, "big"))
    frame.extend(body)
    return bytes(frame)

def build_audio_frame(event: int, audio: bytes, session_id: str) -> bytes:
    body = gzip.compress(audio)
    frame = bytearray(
        generate_header(
            message_type=CLIENT_AUDIO_ONLY_REQUEST,
            serial_method=NO_SERIALIZATION,
        )
    )
    session_bytes = session_id.encode("utf-8")
    frame.extend(event.to_bytes(4, "big"))
    frame.extend(len(session_bytes).to_bytes(4, "big"))
    frame.extend(session_bytes)
    frame.extend(len(body).to_bytes(4, "big"))
    frame.extend(body)
    return bytes(frame)

def parse_response(response: str | bytes) -> dict[str, Any]:
    if isinstance(response, str):
        return {}

    header_size = response[0] & 0x0F
    message_type = response[1] >> 4
    message_type_flags = response[1] & 0x0F
    serialization_method = response[2] >> 4
    compression_type = response[2] & 0x0F
    payload = response[header_size * 4 :]
    result: dict[str, Any] = {}

    if message_type in {SERVER_FULL_RESPONSE, SERVER_ACK}:
        result["message_type"] = "SERVER_ACK" if message_type == SERVER_ACK else "SERVER_FULL_RESPONSE"
        offset = 0
        if message_type_flags & MSG_WITH_EVENT:
            result["event"] = int.from_bytes(payload[offset : offset + 4], "big")
            offset += 4
        if len(payload[offset:]) < 4:
            return result
        session_len = int.from_bytes(payload[offset : offset + 4], "big")
        offset += 4
        result["session_id"] = payload[offset : offset + session_len].decode("utf-8", errors="ignore")
        offset += session_len
        payload_len = int.from_bytes(payload[offset : offset + 4], "big")
        offset += 4
        payload_msg = payload[offset : offset + payload_len]
    elif message_type == SERVER_ERROR_RESPONSE:
        result["message_type"] = "SERVER_ERROR"
        result["code"] = int.from_bytes(payload[:4], "big")
        payload_len = int.from_bytes(payload[4:8], "big")
        payload_msg = payload[8 : 8 + payload_len]
    else:
        return result

    if compression_type == GZIP and payload_msg:
        payload_msg = gzip.decompress(payload_msg)

    if serialization_method == JSON and payload_msg:
        result["payload_msg"] = json.loads(payload_msg.decode("utf-8"))
    elif serialization_method == NO_SERIALIZATION:
        result["payload_msg"] = payload_msg
    elif payload_msg:
        result["payload_msg"] = payload_msg.decode("utf-8", errors="ignore")
    else:
        result["payload_msg"] = {}
    return result


@dataclass(slots=True)
class UpstreamEvent:
    event: int | None
    message_type: str
    payload: Any
