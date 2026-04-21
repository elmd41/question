export type ModelFamily = "O" | "O2.0" | "SC" | "SC2.0";
export type PlaybackTone = "panda_warm";
export type AutoEndMode = "screen_idle" | "disconnect_only";
export type UpstreamMode = "mock" | "volcengine" | "qwen" | "aliyun_split";

export interface LocationConfig {
  city: string;
  province?: string | null;
  country: string;
  country_code: string;
  district?: string | null;
  address?: string | null;
}

export interface MuseumConfig {
  display_title: string;
  display_subtitle: string;
  avatar_url?: string | null;
  idle_timeout_sec: number;
  auto_end_mode: AutoEndMode;
  welcome_text: string;
  model_family: ModelFamily;
  model?: string | null;
  speaker: string;
  playback_tone: PlaybackTone;
  bot_name: string;
  system_role: string;
  speaking_style: string;
  character_manifest?: string | null;
  strict_audit: boolean;
  enable_user_query_exit: boolean;
  location: LocationConfig;
}

export interface PublicConfigResponse {
  version: number;
  config: MuseumConfig;
}

export interface DraftSnapshot {
  config: MuseumConfig;
  updated_at: string;
  updated_by?: string | null;
}

export interface ConfigSnapshot {
  version: number;
  config: MuseumConfig;
  timestamp: string;
  actor?: string | null;
}

export interface ConfigBundle {
  draft: DraftSnapshot;
  published: ConfigSnapshot;
}

export interface AdminSessionStatus {
  authenticated: boolean;
  csrf_token: string;
}

export interface ConfigHistoryItem {
  version: number;
  config: MuseumConfig;
  published_at: string;
  published_by?: string | null;
}

export interface UpstreamConfigResponse {
  mode: UpstreamMode;
  base_url: string;
  app_id: string;
  resource_id: string;
  app_key: string;
  access_key_configured: boolean;
  access_key_masked?: string | null;
  qwen_base_url: string;
  qwen_model: string;
  qwen_voice: string;
  qwen_api_key_configured: boolean;
  qwen_api_key_masked?: string | null;
  aliyun_asr_model: string;
  aliyun_llm_model: string;
  aliyun_tts_model: string;
  aliyun_tts_voice: string;
  aliyun_asr_max_sentence_silence: number;
  updated_at: string;
  updated_by?: string | null;
}

export interface UpstreamConfigUpdateRequest {
  mode: UpstreamMode;
  base_url: string;
  app_id: string;
  access_key?: string;
  resource_id: string;
  app_key: string;
  qwen_api_key?: string;
  qwen_base_url: string;
  qwen_model: string;
  qwen_voice: string;
  aliyun_asr_model: string;
  aliyun_llm_model: string;
  aliyun_tts_model: string;
  aliyun_tts_voice: string;
  aliyun_asr_max_sentence_silence: number;
}

export type VisitorPhase =
  | "boot"
  | "idle"
  | "opening_session"
  | "greeting"
  | "listening"
  | "user_speaking"
  | "thinking"
  | "speaking"
  | "interrupted"
  | "closing_session"
  | "error";
