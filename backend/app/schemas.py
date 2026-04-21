from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

ModelFamily = Literal["O", "O2.0", "SC", "SC2.0"]
PlaybackTone = Literal["natural", "panda_warm"]
AutoEndMode = Literal["screen_idle", "disconnect_only"]
UpstreamMode = Literal["mock", "volcengine", "qwen", "aliyun_split"]

DEFAULT_REALTIME_BASE_URL = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
DEFAULT_REALTIME_RESOURCE_ID = "volc.speech.dialog"
DEFAULT_REALTIME_APP_KEY = "PlgvMymc7f3tQnJ6"

DEFAULT_QWEN_BASE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_QWEN_MODEL = "qwen3.5-omni-flash-realtime"
DEFAULT_QWEN_VOICE = "Momo"

DEFAULT_ALIYUN_ASR_MODEL = "fun-asr-realtime"
DEFAULT_ALIYUN_LLM_MODEL = "qwen3.5-plus"
DEFAULT_ALIYUN_TTS_MODEL = "cosyvoice-v3-flash"
DEFAULT_ALIYUN_TTS_VOICE = "longjielidou_v3"
DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE = 800

DEFAULT_SPEAKER_BY_FAMILY: dict[ModelFamily, str] = {
    "O": "zh_male_xiaotian_jupiter_bigtts",
    "O2.0": "zh_male_xiaotian_jupiter_bigtts",
    "SC": "ICL_zh_female_wenrouwenya_tob",
    "SC2.0": "saturn_zh_female_wenrouwenya_tob",
}


def _strip_text(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def mask_secret(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    if len(normalized) <= 8:
        return "*" * len(normalized)
    return f"{normalized[:3]}***{normalized[-3:]}"


class LocationConfig(BaseModel):
    city: str = "北京"
    province: str | None = None
    country: str = "中国"
    country_code: str = "CN"
    district: str | None = None
    address: str | None = None

class MuseumConfig(BaseModel):
    display_title: str = "科技馆数字人"
    display_subtitle: str = "点击开始，和我一起玩猜年龄游戏吧！"
    avatar_url: str | None = None
    idle_timeout_sec: int = 60
    auto_end_mode: AutoEndMode = "screen_idle"
    welcome_text: str = "你好呀！我是小熊猫，我有一个超厉害的本领——猜年龄！我能通过几个小问题猜出你的年龄，要不要试试？"
    model_family: ModelFamily = "O2.0"
    model: str | None = None
    speaker: str = "zh_male_xiaotian_jupiter_bigtts"
    playback_tone: PlaybackTone = "panda_warm"
    bot_name: str = "小熊猫"
    system_role: str = (
        "你是小熊猫，一只会猜年龄的可爱熊猫。\n"
        "你的任务是通过提问来推理对方的年龄。\n"
        "\n"
        "【你的做法】\n"
        "每轮直接问一个能区分年龄段的问题，不寒暄不铺垫不废话。\n"
        "不要顺着对方的回答追问细节，每轮换一个新话题。\n"
        "\n"
        "【好问题示例】\n"
        "小时候看什么动画片？第一部手机是什么？最喜欢的歌手是谁？玩过什么游戏？\n"
        "\n"
        "【坏问题示例—绝对不能问】\n"
        "爸爸几点下班？坐谁的车？定闹钟没？这种跟年龄无关的不要问。\n"
        "\n"
        "【禁止】\n"
        "不能问年龄、几岁、生日、出生年份、几年级、上学还是上班。\n"
        "\n"
        "【格式】\n"
        "每次只回复一句话，20字以内，直接问问题或给出猜测。\n"
    )
    speaking_style: str = "语气活泼自然，句子简短，根据对方的回答风格自动调整语气，优先使用中文。"
    character_manifest: str | None = None
    strict_audit: bool = False
    enable_user_query_exit: bool = False
    location: LocationConfig = Field(default_factory=LocationConfig)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_auto_end_mode(cls, data: dict) -> dict:  # type: ignore[override]
        if isinstance(data, dict) and data.get("auto_end_mode") == "silence_timeout":
            data["auto_end_mode"] = "screen_idle"
        return data

    @field_validator("display_title")
    @classmethod
    def ensure_display_title(cls, value: str) -> str:
        value = _strip_text(value)
        if not value:
            raise ValueError("展示标题不能为空。")
        return value

    @field_validator("display_subtitle")
    @classmethod
    def ensure_display_subtitle(cls, value: str) -> str:
        value = _strip_text(value)
        if not value:
            raise ValueError("展示副标题不能为空。")
        return value

    @field_validator("welcome_text")
    @classmethod
    def ensure_welcome_text(cls, value: str) -> str:
        value = _strip_text(value)
        if not value:
            raise ValueError("欢迎语不能为空。")
        return value

    @field_validator("bot_name")
    @classmethod
    def ensure_bot_name(cls, value: str) -> str:
        value = _strip_text(value)
        if not value:
            raise ValueError("角色名称不能为空。")
        if len(value) > 20:
            raise ValueError("角色名称不能超过 20 个字。")
        return value

    @field_validator("idle_timeout_sec")
    @classmethod
    def ensure_idle_timeout_sec(cls, value: int) -> int:
        if value < 5 or value > 600:
            raise ValueError("无操作超时需在 5 到 600 秒之间。")
        return value

    @field_validator("system_role")
    @classmethod
    def ensure_system_role(cls, value: str, info: ValidationInfo) -> str:
        value = _strip_text(value)
        family = info.data.get("model_family")
        if family in {"O", "O2.0"} and not value:
            raise ValueError("角色人设不能为空。")
        return value

    @field_validator("speaking_style")
    @classmethod
    def ensure_speaking_style(cls, value: str, info: ValidationInfo) -> str:
        value = _strip_text(value)
        family = info.data.get("model_family")
        if family in {"O", "O2.0"} and not value:
            raise ValueError("互动风格不能为空。")
        return value

    @field_validator("character_manifest")
    @classmethod
    def ensure_character_manifest(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        normalized = _strip_text(value) or None
        family = info.data.get("model_family")
        if family in {"SC", "SC2.0"} and not normalized:
            raise ValueError("角色设定不能为空。")
        return normalized

    @field_validator("playback_tone", mode="before")
    @classmethod
    def force_panda_warm(cls, _: str | None) -> PlaybackTone:
        return "panda_warm"

    @model_validator(mode="after")
    def apply_voice_defaults(self) -> "MuseumConfig":
        self.speaker = DEFAULT_SPEAKER_BY_FAMILY[self.model_family]
        return self

    def to_upstream_payload(self) -> dict:
        dialog_payload: dict = {
            "location": self.location.model_dump(exclude_none=True),
            "extra": {
                "strict_audit": self.strict_audit,
                "audit_response": "当前问题我不方便继续回答，我们可以换个科技馆相关的话题。",
                "input_mod": "audio",
                "enable_user_query_exit": self.enable_user_query_exit,
            },
        }
        if self.model:
            dialog_payload["extra"]["model"] = self.model

        if self.model_family.startswith("SC"):
            dialog_payload["character_manifest"] = (
                self.character_manifest
                or "你是科技馆数字人讲解员，语气自然、专业、热情，擅长用展品故事引导用户继续发问。"
            )
        else:
            dialog_payload["bot_name"] = self.bot_name
            dialog_payload["system_role"] = self.system_role
            dialog_payload["speaking_style"] = self.speaking_style

        return {
            "asr": {
                "extra": {
                    "end_smooth_window_ms": 1500,
                }
            },
            "tts": {
                "speaker": self.speaker,
                "audio_config": {
                    "channel": 1,
                    "format": "pcm_s16le",
                    "sample_rate": 24000,
                },
            },
            "dialog": dialog_payload,
        }


class RealtimeUpstreamConfig(BaseModel):
    mode: UpstreamMode = "mock"
    base_url: str = DEFAULT_REALTIME_BASE_URL
    app_id: str = ""
    access_key: str = ""
    resource_id: str = DEFAULT_REALTIME_RESOURCE_ID
    app_key: str = DEFAULT_REALTIME_APP_KEY
    qwen_api_key: str = ""
    qwen_base_url: str = DEFAULT_QWEN_BASE_URL
    qwen_model: str = DEFAULT_QWEN_MODEL
    qwen_voice: str = DEFAULT_QWEN_VOICE
    aliyun_asr_model: str = DEFAULT_ALIYUN_ASR_MODEL
    aliyun_llm_model: str = DEFAULT_ALIYUN_LLM_MODEL
    aliyun_tts_model: str = DEFAULT_ALIYUN_TTS_MODEL
    aliyun_tts_voice: str = DEFAULT_ALIYUN_TTS_VOICE
    aliyun_asr_max_sentence_silence: int = DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return _strip_text(value) or DEFAULT_REALTIME_BASE_URL

    @field_validator("resource_id")
    @classmethod
    def normalize_resource_id(cls, value: str) -> str:
        return _strip_text(value) or DEFAULT_REALTIME_RESOURCE_ID

    @field_validator("app_key")
    @classmethod
    def normalize_app_key(cls, value: str) -> str:
        return _strip_text(value) or DEFAULT_REALTIME_APP_KEY

    @field_validator("app_id")
    @classmethod
    def ensure_app_id(cls, value: str, info: ValidationInfo) -> str:
        normalized = _strip_text(value)
        if info.data.get("mode") == "volcengine" and not normalized:
            raise ValueError("App ID 不能为空。")
        return normalized

    @field_validator("access_key")
    @classmethod
    def ensure_access_key(cls, value: str, info: ValidationInfo) -> str:
        normalized = _strip_text(value)
        if info.data.get("mode") == "volcengine" and not normalized:
            raise ValueError("Access Key 不能为空。")
        return normalized

    @field_validator("qwen_api_key")
    @classmethod
    def ensure_qwen_api_key(cls, value: str, info: ValidationInfo) -> str:
        normalized = _strip_text(value)
        if info.data.get("mode") == "qwen" and not normalized:
            raise ValueError("DashScope API Key 不能为空。")
        return normalized


class UpstreamConfigSnapshot(BaseModel):
    config: RealtimeUpstreamConfig
    updated_at: datetime
    updated_by: str | None = None


class UpstreamConfigResponse(BaseModel):
    mode: UpstreamMode
    base_url: str
    app_id: str
    resource_id: str
    app_key: str
    access_key_configured: bool
    access_key_masked: str | None = None
    qwen_base_url: str = DEFAULT_QWEN_BASE_URL
    qwen_model: str = DEFAULT_QWEN_MODEL
    qwen_voice: str = DEFAULT_QWEN_VOICE
    qwen_api_key_configured: bool = False
    qwen_api_key_masked: str | None = None
    aliyun_asr_model: str = DEFAULT_ALIYUN_ASR_MODEL
    aliyun_llm_model: str = DEFAULT_ALIYUN_LLM_MODEL
    aliyun_tts_model: str = DEFAULT_ALIYUN_TTS_MODEL
    aliyun_tts_voice: str = DEFAULT_ALIYUN_TTS_VOICE
    aliyun_asr_max_sentence_silence: int = DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE
    updated_at: datetime
    updated_by: str | None = None


class UpstreamConfigUpdateRequest(BaseModel):
    mode: UpstreamMode = "mock"
    base_url: str = DEFAULT_REALTIME_BASE_URL
    app_id: str = ""
    access_key: str | None = None
    resource_id: str = DEFAULT_REALTIME_RESOURCE_ID
    app_key: str = DEFAULT_REALTIME_APP_KEY
    qwen_api_key: str | None = None
    qwen_base_url: str = DEFAULT_QWEN_BASE_URL
    qwen_model: str = DEFAULT_QWEN_MODEL
    qwen_voice: str = DEFAULT_QWEN_VOICE
    aliyun_asr_model: str = DEFAULT_ALIYUN_ASR_MODEL
    aliyun_llm_model: str = DEFAULT_ALIYUN_LLM_MODEL
    aliyun_tts_model: str = DEFAULT_ALIYUN_TTS_MODEL
    aliyun_tts_voice: str = DEFAULT_ALIYUN_TTS_VOICE
    aliyun_asr_max_sentence_silence: int = DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE

    @field_validator("base_url")
    @classmethod
    def normalize_update_base_url(cls, value: str) -> str:
        return _strip_text(value) or DEFAULT_REALTIME_BASE_URL

    @field_validator("app_id")
    @classmethod
    def normalize_update_app_id(cls, value: str) -> str:
        return _strip_text(value)

    @field_validator("access_key")
    @classmethod
    def normalize_update_access_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_text(value)

    @field_validator("resource_id")
    @classmethod
    def normalize_update_resource_id(cls, value: str) -> str:
        return _strip_text(value) or DEFAULT_REALTIME_RESOURCE_ID

    @field_validator("app_key")
    @classmethod
    def normalize_update_app_key(cls, value: str) -> str:
        return _strip_text(value) or DEFAULT_REALTIME_APP_KEY

class ConfigSnapshot(BaseModel):
    version: int
    config: MuseumConfig
    timestamp: datetime
    actor: str | None = None

class DraftSnapshot(BaseModel):
    config: MuseumConfig
    updated_at: datetime
    updated_by: str | None = None

class ConfigBundle(BaseModel):
    draft: DraftSnapshot
    published: ConfigSnapshot

class PublicConfigResponse(BaseModel):
    version: int
    config: MuseumConfig

class ConfigHistoryItem(BaseModel):
    version: int
    config: MuseumConfig
    published_at: datetime
    published_by: str | None = None

class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)

class AdminSessionStatus(BaseModel):
    authenticated: bool
    csrf_token: str
