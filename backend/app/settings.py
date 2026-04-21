from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from .schemas import (
    DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE,
    DEFAULT_ALIYUN_ASR_MODEL,
    DEFAULT_ALIYUN_LLM_MODEL,
    DEFAULT_ALIYUN_TTS_MODEL,
    DEFAULT_ALIYUN_TTS_VOICE,
    DEFAULT_REALTIME_APP_KEY,
    DEFAULT_REALTIME_BASE_URL,
    DEFAULT_REALTIME_RESOURCE_ID,
    MuseumConfig,
    RealtimeUpstreamConfig,
)


def _load_or_create_session_secret(data_dir: Path) -> str:
    """Load session secret from file, or create and persist a new one."""
    secret_file = data_dir / ".session_secret"
    if secret_file.exists():
        stored = secret_file.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    data_dir.mkdir(parents=True, exist_ok=True)
    new_secret = secrets.token_urlsafe(32)
    secret_file.write_text(new_secret, encoding="utf-8")
    return new_secret


def _resolve_root_dir() -> Path:
    """Return the project root (or PyInstaller bundle root when frozen)."""
    if getattr(sys, "frozen", False):
        # PyInstaller: _MEIPASS is the temp extraction dir containing app/ and frontend/
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # Development: settings.py lives at <root>/backend/app/settings.py → parents[2]
    return Path(__file__).resolve().parents[2]

@dataclass(slots=True)
class Settings:
    app_name: str
    data_dir: Path
    database_path: Path
    log_file_path: Path
    log_level: str
    frontend_dist_dir: Path
    session_secret: str
    admin_password: str
    session_cookie_name: str
    csrf_cookie_name: str
    session_idle_seconds: int
    login_rate_limit_attempts: int
    login_rate_limit_window_seconds: int
    session_resume_window_seconds: int
    upstream_connect_timeout_seconds: int
    upstream_mode: str
    upstream_base_url: str
    upstream_app_id: str
    upstream_access_key: str
    upstream_resource_id: str
    upstream_app_key: str
    qwen_api_key: str
    qwen_base_url: str
    qwen_model: str
    qwen_voice: str
    aliyun_asr_model: str
    aliyun_llm_model: str
    aliyun_tts_model: str
    aliyun_tts_voice: str
    aliyun_asr_max_sentence_silence: int
    default_config: MuseumConfig

    def build_upstream_config(self) -> RealtimeUpstreamConfig:
        return RealtimeUpstreamConfig(
            mode=self.upstream_mode,
            base_url=self.upstream_base_url,
            app_id=self.upstream_app_id,
            access_key=self.upstream_access_key,
            resource_id=self.upstream_resource_id,
            app_key=self.upstream_app_key,
            qwen_api_key=self.qwen_api_key,
            qwen_base_url=self.qwen_base_url,
            qwen_model=self.qwen_model,
            qwen_voice=self.qwen_voice,
            aliyun_asr_model=self.aliyun_asr_model,
            aliyun_llm_model=self.aliyun_llm_model,
            aliyun_tts_model=self.aliyun_tts_model,
            aliyun_tts_voice=self.aliyun_tts_voice,
            aliyun_asr_max_sentence_silence=self.aliyun_asr_max_sentence_silence,
        )

    def apply_upstream_config(self, config: RealtimeUpstreamConfig) -> None:
        self.upstream_mode = config.mode
        self.upstream_base_url = config.base_url
        self.upstream_app_id = config.app_id
        self.upstream_access_key = config.access_key
        self.upstream_resource_id = config.resource_id
        self.upstream_app_key = config.app_key
        self.qwen_api_key = config.qwen_api_key
        self.qwen_base_url = config.qwen_base_url
        self.qwen_model = config.qwen_model
        self.qwen_voice = config.qwen_voice
        self.aliyun_asr_model = config.aliyun_asr_model
        self.aliyun_llm_model = config.aliyun_llm_model
        self.aliyun_tts_model = config.aliyun_tts_model
        self.aliyun_tts_voice = config.aliyun_tts_voice
        self.aliyun_asr_max_sentence_silence = config.aliyun_asr_max_sentence_silence

    @classmethod
    def from_env(cls) -> "Settings":
        root_dir = _resolve_root_dir()
        if getattr(sys, "frozen", False):
            # Frozen: data should live beside the exe, not in temp _MEIPASS
            fallback_data = Path(sys.executable).parent / "data"
        else:
            fallback_data = root_dir / "data"
        data_dir = Path(os.getenv("MUSEUM_DATA_DIR", fallback_data))
        frontend_dist = Path(os.getenv("MUSEUM_FRONTEND_DIST", root_dir / "frontend" / "dist"))
        upstream_app_id = os.getenv("UPSTREAM_APP_ID", "")
        upstream_access_key = os.getenv("UPSTREAM_ACCESS_KEY", "")
        upstream_mode = os.getenv("UPSTREAM_MODE", "")
        default_avatar_url = os.getenv("DEFAULT_AVATAR_URL", "/models/panda-v2.glb") or None
        if not upstream_mode:
            upstream_mode = "aliyun_split"

        default_config = MuseumConfig(
            display_title=os.getenv("DEFAULT_DISPLAY_TITLE", "科技馆数字人"),
            display_subtitle=os.getenv("DEFAULT_DISPLAY_SUBTITLE", "点击开始，和我一起玩猜年龄游戏吧！"),
            avatar_url=default_avatar_url,
            idle_timeout_sec=int(os.getenv("DEFAULT_IDLE_TIMEOUT_SEC", "60")),
            auto_end_mode=os.getenv("DEFAULT_AUTO_END_MODE", "screen_idle"),  # type: ignore[arg-type]
            welcome_text=os.getenv(
                "DEFAULT_WELCOME_TEXT",
                "你好呀！我是小熊猫，我有一个超厉害的本领——猜年龄！我能通过几个小问题猜出你的年龄，要不要试试？",
            ),
            model_family=os.getenv("DEFAULT_MODEL_FAMILY", "O2.0"),  # type: ignore[arg-type]
            model=os.getenv("DEFAULT_MODEL") or None,
            speaker=os.getenv("DEFAULT_SPEAKER", "zh_male_xiaotian_jupiter_bigtts"),
            playback_tone=os.getenv("DEFAULT_PLAYBACK_TONE", "panda_warm"),  # type: ignore[arg-type]
            bot_name=os.getenv("DEFAULT_BOT_NAME", "小熊猫"),
            system_role=os.getenv(
                "DEFAULT_SYSTEM_ROLE",
                (
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
                ),
            ),
            speaking_style=os.getenv(
                "DEFAULT_SPEAKING_STYLE",
                "语气活泼自然，句子简短，根据对方的回答风格自动调整语气，优先使用中文。",
            ),
            character_manifest=os.getenv("DEFAULT_CHARACTER_MANIFEST") or None,
            strict_audit=os.getenv("DEFAULT_STRICT_AUDIT", "false").lower() == "true",
            enable_user_query_exit=os.getenv("DEFAULT_ENABLE_USER_QUERY_EXIT", "false").lower() == "true",
        )

        return cls(
            app_name=os.getenv("APP_NAME", "Science Museum Digital Human"),
            data_dir=data_dir,
            database_path=data_dir / "museum.db",
            log_file_path=Path(os.getenv("APP_LOG_PATH", data_dir / "runtime.log")),
            log_level=os.getenv("APP_LOG_LEVEL", "INFO"),
            frontend_dist_dir=frontend_dist,
            session_secret=os.getenv("SESSION_SECRET") or _load_or_create_session_secret(data_dir),
            admin_password=os.getenv("ADMIN_PASSWORD", "dlsnjkjg"),
            session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "museum_admin_session"),
            csrf_cookie_name=os.getenv("CSRF_COOKIE_NAME", "museum_csrf"),
            session_idle_seconds=int(os.getenv("SESSION_IDLE_SECONDS", "900")),
            login_rate_limit_attempts=int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5")),
            login_rate_limit_window_seconds=int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900")),
            session_resume_window_seconds=int(os.getenv("SESSION_RESUME_WINDOW_SECONDS", "8")),
            upstream_connect_timeout_seconds=int(os.getenv("UPSTREAM_CONNECT_TIMEOUT_SECONDS", "12")),
            upstream_mode=upstream_mode,
            upstream_base_url=os.getenv(
                "UPSTREAM_BASE_URL",
                DEFAULT_REALTIME_BASE_URL,
            ),
            upstream_app_id=upstream_app_id,
            upstream_access_key=upstream_access_key,
            upstream_resource_id=os.getenv("UPSTREAM_RESOURCE_ID", DEFAULT_REALTIME_RESOURCE_ID),
            upstream_app_key=os.getenv("UPSTREAM_APP_KEY", DEFAULT_REALTIME_APP_KEY),
            qwen_api_key=os.getenv("DASHSCOPE_API_KEY", "sk-97e7834191ea4a8e985dfa5b1e159f4b"),
            qwen_base_url=os.getenv("QWEN_BASE_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"),
            qwen_model=os.getenv("QWEN_MODEL", "qwen3.5-omni-flash-realtime"),
            qwen_voice=os.getenv("QWEN_VOICE", "Momo"),
            aliyun_asr_model=os.getenv("ALIYUN_ASR_MODEL", DEFAULT_ALIYUN_ASR_MODEL),
            aliyun_llm_model=os.getenv("ALIYUN_LLM_MODEL", DEFAULT_ALIYUN_LLM_MODEL),
            aliyun_tts_model=os.getenv("ALIYUN_TTS_MODEL", DEFAULT_ALIYUN_TTS_MODEL),
            aliyun_tts_voice=os.getenv("ALIYUN_TTS_VOICE", DEFAULT_ALIYUN_TTS_VOICE),
            aliyun_asr_max_sentence_silence=int(os.getenv("ALIYUN_ASR_MAX_SENTENCE_SILENCE", str(DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE))),
            default_config=default_config,
        )
