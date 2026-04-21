import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Typography,
  message,
} from "antd";
import type { FormInstance } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  fetchAdminSession,
  fetchConfigBundle,
  fetchHistory,
  fetchUpstreamConfig,
  logoutAdmin,
  publishDraft,
  resetRealtimeSession,
  updateUpstreamConfig,
} from "../lib/api";
import type {
  AutoEndMode,
  ConfigBundle,
  ConfigHistoryItem,
  MuseumConfig,
  UpstreamConfigResponse,
  UpstreamConfigUpdateRequest,
  UpstreamMode,
} from "../types/api";

const modelFamilyOptions = [
  { value: "O", label: "O 标准对话版" },
  { value: "O2.0", label: "O2.0 标准增强版" },
  { value: "SC", label: "SC 角色扮演版" },
  { value: "SC2.0", label: "SC2.0 角色增强版" },
];

const autoEndModeOptions: Array<{ value: AutoEndMode; label: string }> = [
  { value: "screen_idle", label: "无操作超时自动结束" },
  { value: "disconnect_only", label: "仅页面断开时结束" },
];

const autoEndModeLabelMap: Record<AutoEndMode, string> = {
  screen_idle: "无操作超时自动结束",
  disconnect_only: "仅页面断开时结束",
};

const upstreamModeOptions: Array<{ value: UpstreamMode; label: string }> = [
  { value: "volcengine", label: "火山云实时语音" },
  { value: "qwen", label: "通义千问实时语音" },
  { value: "aliyun_split", label: "阿里云分离链路 (ASR+LLM+TTS)" },
  { value: "mock", label: "Mock 本地兜底" },
];

const upstreamModeLabelMap: Record<UpstreamMode, string> = {
  volcengine: "火山云实时语音",
  qwen: "通义千问实时语音",
  aliyun_split: "阿里云分离链路 (ASR+LLM+TTS)",
  mock: "Mock 本地兜底",
};

const DEFAULT_DISPLAY_TITLE = "科技馆数字人";
const DEFAULT_DISPLAY_SUBTITLE = "点击开始，和我一起玩猜年龄游戏吧！";
const DEFAULT_UPSTREAM_BASE_URL = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue";
const DEFAULT_UPSTREAM_RESOURCE_ID = "volc.speech.dialog";
const DEFAULT_UPSTREAM_APP_KEY = "PlgvMymc7f3tQnJ6";
const DEFAULT_QWEN_BASE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime";
const DEFAULT_QWEN_MODEL = "qwen3.5-omni-flash-realtime";
const DEFAULT_QWEN_VOICE = "Momo";
const DEFAULT_ALIYUN_ASR_MODEL = "paraformer-realtime-v2";
const DEFAULT_ALIYUN_LLM_MODEL = "qwen3.5-flash";
const DEFAULT_ALIYUN_TTS_MODEL = "cosyvoice-v3-flash";
const DEFAULT_ALIYUN_TTS_VOICE = "longjielidou_v3";
const DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE = 800;

const qwenVoiceOptions = [
  { value: "Momo", label: "茉兔 Momo — 撞娇搞怪，逗你开心" },
  { value: "Ono Anna", label: "小野杏 Ono Anna — 鬼灵精怪" },
  { value: "Tina", label: "甜甜 Tina — 甘甘的暖暖的" },
  { value: "Ethan", label: "晨煦 Ethan — 阳光温暖活力" },
  { value: "Evan", label: "江晨 Evan — 年轻男生" },
  { value: "Qiao", label: "小乔妹 Qiao — 台式可爱" },
  { value: "Serena", label: "苏瑶 Serena — 温柔小姐姐" },
  { value: "Sunnybobi", label: "知芥 Sunnybobi — 邻家姑娘" },
  { value: "Raymond", label: "林川野 Raymond — 声音清亮" },
  { value: "Cherry", label: "Cherry — 经典女声" },
  { value: "Chelsie", label: "Chelsie — 经典女声" },
];

type FormNamePath = string | number | Array<string | number>;

interface UpstreamFormValues {
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

function buildTrimmedRequiredRule(messageText: string) {
  return {
    validator: async (_rule: unknown, value: unknown) => {
      if (typeof value !== "string" || !value.trim()) {
        throw new Error(messageText);
      }
    },
  };
}

const idleTimeoutRule = {
  validator: async (_rule: unknown, value: unknown) => {
    if (typeof value !== "number" || Number.isNaN(value)) {
      throw new Error("请输入无操作超时秒数。");
    }

    if (value < 5 || value > 600) {
      throw new Error("无操作超时需在 5 到 600 秒之间。");
    }
  },
};

function toNamePath(fieldName: string): FormNamePath {
  return fieldName.includes(".") ? fieldName.split(".") : fieldName;
}

function clearFieldErrors(targetForm: FormInstance) {
  const fields = targetForm
    .getFieldsError()
    .filter((field) => field.errors.length > 0)
    .map((field) => ({
      name: field.name,
      errors: [] as string[],
    }));

  if (fields.length > 0) {
    targetForm.setFields(fields);
  }
}

function applyFieldErrors(targetForm: FormInstance, fieldErrors: Record<string, string[]>) {
  const entries = Object.entries(fieldErrors);
  if (entries.length === 0) {
    return false;
  }

  targetForm.setFields(
    entries.map(([fieldName, errors]) => ({
      name: toNamePath(fieldName),
      errors,
    })),
  );

  targetForm.scrollToField(toNamePath(entries[0][0]));
  return true;
}

function buildPromptSummary(config: MuseumConfig): Array<{ label: string; text: string }> {
  if (config.model_family === "SC" || config.model_family === "SC2.0") {
    return [
      {
        label: "当前角色设定",
        text: config.character_manifest ?? "未设置",
      },
    ];
  }

  return [
    {
      label: "当前角色人设",
      text: config.system_role,
    },
    {
      label: "当前互动风格",
      text: config.speaking_style,
    },
  ];
}

function formatUpstreamMeta(config: UpstreamConfigResponse | null): string {
  if (!config) {
    return "正在加载上游配置。";
  }

  const actorText = config.updated_by ? `，最近更新人：${config.updated_by}` : "";
  let keyText = "";
  if (config.mode === "volcengine") {
    keyText = config.access_key_configured
      ? `，Access Key：${config.access_key_masked ?? "已配置"}`
      : "，Access Key：未配置";
  } else if (config.mode === "qwen") {
    keyText = config.qwen_api_key_configured
      ? `，API Key：${config.qwen_api_key_masked ?? "已配置"}`
      : "，API Key：未配置";
  } else if (config.mode === "aliyun_split") {
    keyText = config.qwen_api_key_configured
      ? `，DashScope API Key：${config.qwen_api_key_masked ?? "已配置"}`
      : "，DashScope API Key：未配置";
  }

  return `当前模式：${upstreamModeLabelMap[config.mode]}，最近更新时间：${config.updated_at}${actorText}${keyText}`;
}

export function AdminDashboardPage() {
  const navigate = useNavigate();
  const [configForm] = Form.useForm<MuseumConfig>();
  const [upstreamForm] = Form.useForm<UpstreamFormValues>();
  const [csrfToken, setCsrfToken] = useState("");
  const [bundle, setBundle] = useState<ConfigBundle | null>(null);
  const [history, setHistory] = useState<ConfigHistoryItem[]>([]);
  const [upstreamConfig, setUpstreamConfig] = useState<UpstreamConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [savingUpstream, setSavingUpstream] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentFamily = Form.useWatch("model_family", configForm) ?? "O2.0";
  const currentAutoEndMode = Form.useWatch("auto_end_mode", configForm) ?? "screen_idle";
  const currentUpstreamMode = Form.useWatch("mode", upstreamForm) ?? "mock";

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const session = await fetchAdminSession();
      setCsrfToken(session.csrf_token);
      if (!session.authenticated) {
        void navigate("/admin/login", { replace: true });
        return;
      }

      const [configBundle, configHistory, currentUpstreamConfig] = await Promise.all([
        fetchConfigBundle(),
        fetchHistory(),
        fetchUpstreamConfig(),
      ]);

      setBundle(configBundle);
      setHistory(configHistory);
      setUpstreamConfig(currentUpstreamConfig);
      clearFieldErrors(configForm);
      clearFieldErrors(upstreamForm);

      configForm.setFieldsValue({
        ...configBundle.published.config,
        playback_tone: "panda_warm",
      });

      if (!configBundle.published.config.avatar_url) {
        configForm.setFieldValue("avatar_url", "/models/panda-v2.glb");
      }

      upstreamForm.setFieldsValue({
        mode: currentUpstreamConfig.mode,
        base_url: currentUpstreamConfig.base_url || DEFAULT_UPSTREAM_BASE_URL,
        app_id: currentUpstreamConfig.app_id,
        access_key: "",
        resource_id: currentUpstreamConfig.resource_id || DEFAULT_UPSTREAM_RESOURCE_ID,
        app_key: currentUpstreamConfig.app_key || DEFAULT_UPSTREAM_APP_KEY,
        qwen_api_key: "",
        qwen_base_url: currentUpstreamConfig.qwen_base_url || DEFAULT_QWEN_BASE_URL,
        qwen_model: currentUpstreamConfig.qwen_model || DEFAULT_QWEN_MODEL,
        qwen_voice: currentUpstreamConfig.qwen_voice || DEFAULT_QWEN_VOICE,
        aliyun_asr_model: currentUpstreamConfig.aliyun_asr_model || DEFAULT_ALIYUN_ASR_MODEL,
        aliyun_llm_model: currentUpstreamConfig.aliyun_llm_model || DEFAULT_ALIYUN_LLM_MODEL,
        aliyun_tts_model: currentUpstreamConfig.aliyun_tts_model || DEFAULT_ALIYUN_TTS_MODEL,
        aliyun_tts_voice: currentUpstreamConfig.aliyun_tts_voice || DEFAULT_ALIYUN_TTS_VOICE,
        aliyun_asr_max_sentence_silence: currentUpstreamConfig.aliyun_asr_max_sentence_silence || DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE,
      });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "后台加载失败。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const buildConfigPayload = (values: MuseumConfig): MuseumConfig => {
    const publishedConfig = bundle?.published.config;
    return {
      ...(publishedConfig ?? values),
      ...values,
      bot_name: values.bot_name?.trim() || publishedConfig?.bot_name || "",
      welcome_text: values.welcome_text?.trim() || publishedConfig?.welcome_text || "",
      system_role: values.system_role?.trim() || publishedConfig?.system_role || "",
      speaking_style: values.speaking_style?.trim() || publishedConfig?.speaking_style || "",
      character_manifest: values.character_manifest?.trim() || null,
      display_title: values.display_title?.trim() || publishedConfig?.display_title || DEFAULT_DISPLAY_TITLE,
      display_subtitle:
        values.display_subtitle?.trim() || publishedConfig?.display_subtitle || DEFAULT_DISPLAY_SUBTITLE,
      avatar_url: values.avatar_url ?? publishedConfig?.avatar_url ?? "/models/panda-v2.glb",
      playback_tone: "panda_warm",
    };
  };

  const buildUpstreamPayload = (values: UpstreamFormValues): UpstreamConfigUpdateRequest => {
    const isVolc = values.mode === "volcengine";
    const isQwen = values.mode === "qwen";
    const isAliyunSplit = values.mode === "aliyun_split";
    return {
      mode: values.mode,
      base_url: isVolc ? (values.base_url?.trim() || DEFAULT_UPSTREAM_BASE_URL) : (upstreamConfig?.base_url || DEFAULT_UPSTREAM_BASE_URL),
      app_id: isVolc ? (values.app_id?.trim() || "") : (upstreamConfig?.app_id || ""),
      access_key: isVolc ? (values.access_key?.trim() || "") : undefined,
      resource_id: isVolc ? (values.resource_id?.trim() || DEFAULT_UPSTREAM_RESOURCE_ID) : (upstreamConfig?.resource_id || DEFAULT_UPSTREAM_RESOURCE_ID),
      app_key: isVolc ? (values.app_key?.trim() || DEFAULT_UPSTREAM_APP_KEY) : (upstreamConfig?.app_key || DEFAULT_UPSTREAM_APP_KEY),
      qwen_api_key: (isQwen || isAliyunSplit) ? (values.qwen_api_key?.trim() || "") : undefined,
      qwen_base_url: isQwen ? (values.qwen_base_url?.trim() || DEFAULT_QWEN_BASE_URL) : (upstreamConfig?.qwen_base_url || DEFAULT_QWEN_BASE_URL),
      qwen_model: isQwen ? (values.qwen_model?.trim() || DEFAULT_QWEN_MODEL) : (upstreamConfig?.qwen_model || DEFAULT_QWEN_MODEL),
      qwen_voice: isQwen ? (values.qwen_voice?.trim() || DEFAULT_QWEN_VOICE) : (upstreamConfig?.qwen_voice || DEFAULT_QWEN_VOICE),
      aliyun_asr_model: isAliyunSplit ? (values.aliyun_asr_model?.trim() || DEFAULT_ALIYUN_ASR_MODEL) : (upstreamConfig?.aliyun_asr_model || DEFAULT_ALIYUN_ASR_MODEL),
      aliyun_llm_model: isAliyunSplit ? (values.aliyun_llm_model?.trim() || DEFAULT_ALIYUN_LLM_MODEL) : (upstreamConfig?.aliyun_llm_model || DEFAULT_ALIYUN_LLM_MODEL),
      aliyun_tts_model: isAliyunSplit ? (values.aliyun_tts_model?.trim() || DEFAULT_ALIYUN_TTS_MODEL) : (upstreamConfig?.aliyun_tts_model || DEFAULT_ALIYUN_TTS_MODEL),
      aliyun_tts_voice: isAliyunSplit ? (values.aliyun_tts_voice?.trim() || DEFAULT_ALIYUN_TTS_VOICE) : (upstreamConfig?.aliyun_tts_voice || DEFAULT_ALIYUN_TTS_VOICE),
      aliyun_asr_max_sentence_silence: isAliyunSplit ? (values.aliyun_asr_max_sentence_silence || DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE) : (upstreamConfig?.aliyun_asr_max_sentence_silence || DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE),
    };
  };

  const handlePublish = async (values: MuseumConfig) => {
    setPublishing(true);
    clearFieldErrors(configForm);

    try {
      await publishDraft(csrfToken, buildConfigPayload(values));
      message.success("发布成功，新配置将在下一次会话生效。");
      await refresh();
    } catch (requestError) {
      if (requestError instanceof ApiError) {
        applyFieldErrors(configForm, requestError.fieldErrors);
        message.error(requestError.message);
      } else {
        message.error(requestError instanceof Error ? requestError.message : "发布失败，请稍后再试。");
      }
    } finally {
      setPublishing(false);
    }
  };

  const handleSaveUpstream = async (values: UpstreamFormValues) => {
    setSavingUpstream(true);
    clearFieldErrors(upstreamForm);

    try {
      await updateUpstreamConfig(buildUpstreamPayload(values), csrfToken);
      const modeMessages: Record<UpstreamMode, string> = {
        volcengine: "火山云实时语音配置已保存，新会话会使用新配置。",
        qwen: "通义千问实时语音配置已保存，新会话会使用新配置。",
        aliyun_split: "阿里云分离链路配置已保存，新会话会使用新配置。",
        mock: "已切换为 Mock 模式，新会话将不再连接上游。",
      };
      message.success(modeMessages[values.mode]);
      await refresh();
    } catch (requestError) {
      if (requestError instanceof ApiError) {
        applyFieldErrors(upstreamForm, requestError.fieldErrors);
        message.error(requestError.message);
      } else {
        message.error(requestError instanceof Error ? requestError.message : "保存失败，请稍后再试。");
      }
    } finally {
      setSavingUpstream(false);
    }
  };

  const handleConfigFinishFailed = ({
    errorFields,
  }: {
    errorFields: Array<{ name: Array<string | number> }>;
  }) => {
    if (errorFields.length > 0) {
      configForm.scrollToField(errorFields[0].name);
    }
    message.error("发布失败，请先检查标红字段。");
  };

  const handleUpstreamFinishFailed = ({
    errorFields,
  }: {
    errorFields: Array<{ name: Array<string | number> }>;
  }) => {
    if (errorFields.length > 0) {
      upstreamForm.scrollToField(errorFields[0].name);
    }
    message.error("保存失败，请先检查标红字段。");
  };

  return (
    <main className="admin-page">
      <header className="admin-header">
        <div>
          <Typography.Text className="eyebrow">ADMIN</Typography.Text>
          <Typography.Title level={2}>后台配置</Typography.Title>
        </div>
        <Space>
          <Button
            onClick={() => void navigate("/", { replace: true })}
          >
            返回游戏
          </Button>
          <Button
            onClick={async () => {
              await logoutAdmin(csrfToken);
              void navigate("/admin/login", { replace: true });
            }}
          >
            退出登录
          </Button>
          <Button
            danger
            onClick={async () => {
              await resetRealtimeSession(csrfToken);
              message.success("已尝试结束当前会话。");
            }}
          >
            结束当前会话
          </Button>
        </Space>
      </header>

      {error ? <Alert type="error" showIcon message={error} /> : null}

      <div className="admin-grid">
        <Card loading={loading} title="实时语音上游配置" className="admin-card admin-card--wide">
          <Form
            layout="vertical"
            form={upstreamForm}
            onFinish={handleSaveUpstream}
            onFinishFailed={handleUpstreamFinishFailed}
          >
            <section className="admin-form-section">
              <div className="form-grid">
                <Form.Item label="运行模式" name="mode" rules={[{ required: true }]}>
                  <Select popupClassName="admin-select-dropdown" options={upstreamModeOptions} />
                </Form.Item>
              </div>
            </section>

            {currentUpstreamMode === "volcengine" && (
              <section className="admin-form-section">
                <div className="form-grid">
                  <Form.Item
                    label="App ID"
                    name="app_id"
                    rules={[buildTrimmedRequiredRule("App ID 不能为空。")]}
                  >
                    <Input placeholder="火山云 App ID" />
                  </Form.Item>
                  <Form.Item
                    label="Access Key"
                    name="access_key"
                    rules={[
                      {
                        validator: async (_rule, value) => {
                          if (upstreamConfig?.access_key_configured && (!value || !String(value).trim())) {
                            return;
                          }
                          if (!value || !String(value).trim()) {
                            throw new Error("Access Key 不能为空。");
                          }
                        },
                      },
                    ]}
                    extra={
                      upstreamConfig?.access_key_configured
                        ? `当前：${upstreamConfig.access_key_masked ?? "已配置"}，留空保留原值`
                        : undefined
                    }
                  >
                    <Input.Password
                      autoComplete="new-password"
                      placeholder={
                        upstreamConfig?.access_key_configured
                          ? "留空保留原值，或输入新 Key"
                          : "请输入 Access Key"
                      }
                    />
                  </Form.Item>
                  <Form.Item label="App Key" rules={[buildTrimmedRequiredRule("App Key 不能为空。")]}>
                    <Input disabled placeholder="固定值，无需修改" value={DEFAULT_UPSTREAM_APP_KEY} />
                  </Form.Item>
                </div>
              </section>
            )}

            {currentUpstreamMode === "qwen" && (
              <section className="admin-form-section">
                <div className="form-grid">
                  <Form.Item
                    label="DashScope API Key"
                    name="qwen_api_key"
                    rules={[
                      {
                        validator: async (_rule, value) => {
                          if (upstreamConfig?.qwen_api_key_configured && (!value || !String(value).trim())) {
                            return;
                          }
                          if (!value || !String(value).trim()) {
                            throw new Error("DashScope API Key 不能为空。");
                          }
                        },
                      },
                    ]}
                    extra={
                      upstreamConfig?.qwen_api_key_configured
                        ? `当前：${upstreamConfig.qwen_api_key_masked ?? "已配置"}，留空保留原值`
                        : undefined
                    }
                  >
                    <Input.Password
                      autoComplete="new-password"
                      placeholder={
                        upstreamConfig?.qwen_api_key_configured
                          ? "留空保留原值，或输入新 Key"
                          : "请输入 DashScope API Key"
                      }
                    />
                  </Form.Item>
                  <Form.Item label="WebSocket 地址" name="qwen_base_url">
                    <Input placeholder={DEFAULT_QWEN_BASE_URL} />
                  </Form.Item>
                  <Form.Item label="模型" name="qwen_model">
                    <Input placeholder={DEFAULT_QWEN_MODEL} />
                  </Form.Item>
                  <Form.Item
                    label={
                      <span>
                        音色{" "}
                        <a
                          href="https://help.aliyun.com/zh/model-studio/omni-voice-list"
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ fontSize: 12, fontWeight: "normal" }}
                        >
                          试听全部音色 ↗
                        </a>
                      </span>
                    }
                    name="qwen_voice"
                  >
                    <Select popupClassName="admin-select-dropdown" options={qwenVoiceOptions} />
                  </Form.Item>
                </div>
              </section>
            )}

            {currentUpstreamMode === "aliyun_split" && (
              <section className="admin-form-section">
                <h3>阿里云分离链路配置</h3>
                <div className="form-grid">
                  <Form.Item
                    label="DashScope API Key"
                    name="qwen_api_key"
                    rules={[{ required: true, message: "请输入 DashScope API Key" }]}
                  >
                    <Input.Password
                      autoComplete="new-password"
                      placeholder={
                        upstreamConfig?.qwen_api_key_configured
                          ? "留空保留原值，或输入新 Key"
                          : "请输入 DashScope API Key"
                      }
                    />
                  </Form.Item>
                  <Form.Item label="ASR 模型" name="aliyun_asr_model">
                    <Input placeholder={DEFAULT_ALIYUN_ASR_MODEL} />
                  </Form.Item>
                  <Form.Item label="LLM 模型" name="aliyun_llm_model">
                    <Input placeholder={DEFAULT_ALIYUN_LLM_MODEL} />
                  </Form.Item>
                  <Form.Item label="TTS 模型" name="aliyun_tts_model">
                    <Input placeholder={DEFAULT_ALIYUN_TTS_MODEL} />
                  </Form.Item>
                  <Form.Item label="TTS 音色" name="aliyun_tts_voice">
                    <Input placeholder={DEFAULT_ALIYUN_TTS_VOICE} />
                  </Form.Item>
                  <Form.Item label="VAD 静音阈值 (ms)" name="aliyun_asr_max_sentence_silence">
                    <InputNumber
                      min={200}
                      max={6000}
                      step={100}
                      style={{ width: "100%" }}
                      placeholder={String(DEFAULT_ALIYUN_ASR_MAX_SENTENCE_SILENCE)}
                    />
                  </Form.Item>
                </div>
              </section>
            )}

            <div className="admin-action-bar">
              <Button type="primary" htmlType="submit" loading={savingUpstream}>
                保存
              </Button>
            </div>
          </Form>
        </Card>

        <Card loading={loading} title="发布配置" className="admin-card">
          <Form
            layout="vertical"
            form={configForm}
            onFinish={handlePublish}
            onFinishFailed={handleConfigFinishFailed}
          >
            <section className="admin-form-section">
              <div className="form-grid">
                <Form.Item label="角色名称" name="bot_name" rules={[buildTrimmedRequiredRule("不能为空。")]}>
                  <Input maxLength={20} placeholder="角色名称" />
                </Form.Item>
                <Form.Item label="模型版本" name="model_family" rules={[{ required: true }]}>
                  <Select popupClassName="admin-select-dropdown" options={modelFamilyOptions} />
                </Form.Item>
                <Form.Item className="form-grid__full" label="欢迎语" name="welcome_text" rules={[buildTrimmedRequiredRule("不能为空。")]}>
                  <Input.TextArea rows={3} placeholder="欢迎语" />
                </Form.Item>
                <Form.Item label="自动结束" name="auto_end_mode" rules={[{ required: true }]}>
                  <Select popupClassName="admin-select-dropdown" options={autoEndModeOptions} />
                </Form.Item>
                <Form.Item label="无操作超时（秒）" name="idle_timeout_sec" rules={[idleTimeoutRule]}>
                  <InputNumber min={5} max={600} disabled={currentAutoEndMode !== "screen_idle"} style={{ width: "100%" }} />
                </Form.Item>
              </div>
            </section>

            <section className="admin-form-section">
              <div className="form-grid">
                {(currentFamily === "O" || currentFamily === "O2.0") && (
                  <>
                    <Form.Item
                      className="form-grid__full"
                      label="角色人设"
                      name="system_role"
                      rules={[buildTrimmedRequiredRule("角色人设不能为空。")]}
                    >
                      <Input.TextArea rows={6} placeholder="请输入角色人设" />
                    </Form.Item>
                    <Form.Item
                      className="form-grid__full"
                      label="互动风格"
                      name="speaking_style"
                      rules={[buildTrimmedRequiredRule("互动风格不能为空。")]}
                    >
                      <Input.TextArea rows={4} placeholder="请输入互动风格" />
                    </Form.Item>
                  </>
                )}

                {(currentFamily === "SC" || currentFamily === "SC2.0") && (
                  <Form.Item
                    className="form-grid__full"
                    label="角色设定"
                    name="character_manifest"
                    rules={[buildTrimmedRequiredRule("角色设定不能为空。")]}
                  >
                    <Input.TextArea rows={8} placeholder="请输入角色设定" />
                  </Form.Item>
                )}
              </div>
            </section>

            <Form.Item name="display_title" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="display_subtitle" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="speaker" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="avatar_url" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="model" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="playback_tone" hidden initialValue="panda_warm">
              <Input />
            </Form.Item>
            <Form.Item name={["location", "city"]} hidden>
              <Input />
            </Form.Item>
            <Form.Item name={["location", "province"]} hidden>
              <Input />
            </Form.Item>
            <Form.Item name={["location", "country"]} hidden>
              <Input />
            </Form.Item>
            <Form.Item name={["location", "country_code"]} hidden>
              <Input />
            </Form.Item>
            <Form.Item name={["location", "district"]} hidden>
              <Input />
            </Form.Item>
            <Form.Item name={["location", "address"]} hidden>
              <Input />
            </Form.Item>
            <Form.Item hidden label="严格审核" name="strict_audit" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item hidden label="识别退出意图" name="enable_user_query_exit" valuePropName="checked">
              <Switch />
            </Form.Item>

            <div className="admin-action-bar">
              <Button type="primary" htmlType="submit" loading={publishing}>
                发布
              </Button>
            </div>
          </Form>
        </Card>

        <Card loading={loading} title="当前已发布" className="admin-card">
          {bundle ? (
            <div className="published-summary">
              <p>v{bundle.published.version} · {bundle.published.config.bot_name} · {bundle.published.config.model_family}</p>
              <p>发布时间：{bundle.published.timestamp}</p>
              <p>超时：{bundle.published.config.auto_end_mode === "screen_idle" ? `${bundle.published.config.idle_timeout_sec}s` : "关闭"}</p>
            </div>
          ) : null}
        </Card>

        <Card loading={loading} title="发布历史" className="admin-card admin-card--wide">
          <Table
            rowKey="version"
            pagination={false}
            dataSource={history}
            columns={[
              { title: "版本", dataIndex: "version", width: 90 },
              { title: "角色", render: (_, record) => record.config.bot_name },
              { title: "模型版本", render: (_, record) => record.config.model_family },
              { title: "发布时间", dataIndex: "published_at" },
            ]}
          />
        </Card>
      </div>
    </main>
  );
}
