import { startTransition, useEffect, useEffectEvent, useReducer, useRef, useState } from "react";
import { computeLevel, createAudioRuntime, type AudioInitError, type AudioRuntime, type PlaybackEvent } from "../lib/audio";
import type { MuseumConfig, VisitorPhase } from "../types/api";

/**
 * 写日志到 Electron 主进程日志文件
 */
function audioFileLog(level: string, message: string): void {
  try {
    const api = (window as unknown as { electronAPI?: { log?: (level: string, message: string) => void } }).electronAPI;
    api?.log?.(level, `[session] ${message}`);
  } catch {
    // 非 Electron 环境忽略
  }
}

interface SessionViewState {
  phase: VisitorPhase;
  assistantText: string;
  assistantReplyId: string | null;
  userText: string;
  error: string | null;
}

type SessionAction =
  | { type: "phase"; phase: VisitorPhase }
  | { type: "assistant"; text: string; replyId: string | null }
  | { type: "user"; text: string }
  | { type: "error"; message: string }
  | { type: "reset" };

const initialState: SessionViewState = {
  phase: "idle",
  assistantText: "",
  assistantReplyId: null,
  userText: "",
  error: null,
};

const LISTENING_ACTIVATION_FRAMES = 2;
const INTERRUPT_ACTIVATION_FRAMES = 5;
const MIN_VAD_LEVEL = 0.026;
const MIN_INTERRUPT_LEVEL = 0.034;
const INTERRUPT_ARM_DELAY_MS = 650;
const VOICE_ACTIVITY_HEARTBEAT_MS = 240;

function reducer(state: SessionViewState, action: SessionAction): SessionViewState {
  switch (action.type) {
    case "phase":
      return {
        ...state,
        phase: action.phase,
      };
    case "assistant":
      return {
        ...state,
        assistantText: action.text,
        assistantReplyId: action.replyId,
      };
    case "user":
      return {
        ...state,
        userText: action.text,
      };
    case "error":
      return {
        ...state,
        phase: "error",
        error: action.message,
      };
    case "reset":
      return initialState;
    default:
      return state;
  }
}

function getWsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/realtime`;
}

function getClientId(): string {
  const key = "museum-client-id";
  const existing = window.localStorage.getItem(key);
  if (existing) {
    return existing;
  }
  const created = crypto.randomUUID();
  window.localStorage.setItem(key, created);
  return created;
}

type TraceEntry = {
  at: string;
  step: string;
  detail?: Record<string, unknown>;
};

function pushTrace(step: string, detail?: Record<string, unknown>) {
  const entry: TraceEntry = {
    at: new Date().toISOString(),
    step,
    detail,
  };
  const traceWindow = window as Window & { __museumSessionTrace?: TraceEntry[] };
  const previous = traceWindow.__museumSessionTrace ?? [];
  traceWindow.__museumSessionTrace = [...previous.slice(-79), entry];
  console.info("[museum-session]", step, detail ?? {});
}

export function useRealtimeSession(config: MuseumConfig | null) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [assistantLevel, setAssistantLevel] = useState(0);
  const [activeConfig, setActiveConfig] = useState<MuseumConfig | null>(null);

  const clientIdRef = useRef<string>(getClientId());
  const resumeTokenRef = useRef<string | null>(window.localStorage.getItem("museum-resume-token"));
  const phaseRef = useRef<VisitorPhase>(initialState.phase);
  const wsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<AudioRuntime | null>(null);
  const heartbeatRef = useRef<number | null>(null);
  const suppressIncomingAudioRef = useRef(false);
  const teardownInFlightRef = useRef(false);
  const expectedSocketCloseRef = useRef(false);
  const unmountedRef = useRef(false);
  const binaryChunkCountRef = useRef(0);
  const playerStartedRef = useRef(false);
  const playerStartedAtRef = useRef<number | null>(null);
  const interruptUnlockAtRef = useRef(0);
  const ttsEndedRef = useRef(false);
  const lastQueuedMsRef = useRef(0);
  const stableSpeechFramesRef = useRef(0);
  const lastVoiceActivitySentAtRef = useRef(0);
  const lastVoiceActivitySpeakingRef = useRef(false);

  const setPhase = useEffectEvent((phase: VisitorPhase) => {
    dispatch({ type: "phase", phase });
  });

  const clearHeartbeat = useEffectEvent(() => {
    if (heartbeatRef.current !== null) {
      window.clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
    }
  });

  const clearResumeToken = useEffectEvent(() => {
    resumeTokenRef.current = null;
    window.localStorage.removeItem("museum-resume-token");
  });

  const resetLocalSpeechGate = useEffectEvent(() => {
    stableSpeechFramesRef.current = 0;
    lastVoiceActivitySentAtRef.current = 0;
    lastVoiceActivitySpeakingRef.current = false;
  });

  const releaseResources = useEffectEvent(async (finalPhase: VisitorPhase = "idle", errorMessage?: string) => {
    if (teardownInFlightRef.current) {
      return;
    }
    teardownInFlightRef.current = true;
    pushTrace("cleanup:start", { finalPhase, hasError: Boolean(errorMessage) });

    clearHeartbeat();

    const socket = wsRef.current;
    wsRef.current = null;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      expectedSocketCloseRef.current = true;
      socket.close();
    }

    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      await audio.close();
    }

    suppressIncomingAudioRef.current = false;
    binaryChunkCountRef.current = 0;
    playerStartedRef.current = false;
    playerStartedAtRef.current = null;
    interruptUnlockAtRef.current = 0;
    ttsEndedRef.current = false;
    lastQueuedMsRef.current = 0;
    resetLocalSpeechGate();
    setAssistantLevel(0);

    if (!unmountedRef.current) {
      if (errorMessage) {
        dispatch({ type: "error", message: errorMessage });
      } else if (finalPhase === "idle") {
        dispatch({ type: "reset" });
        setActiveConfig(null);
      } else {
        dispatch({ type: "phase", phase: finalPhase });
      }
    }

    teardownInFlightRef.current = false;
    pushTrace("cleanup:done", { finalPhase });
  });

  const sendJson = useEffectEvent((payload: unknown) => {
    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify(payload));
  });

  const handlePlaybackEvent = useEffectEvent((event: PlaybackEvent) => {
    if (event.type === "queue_depth") {
      const queuedMs = event.queuedMs ?? 0;
      lastQueuedMsRef.current = queuedMs;

      if (queuedMs > 0 && binaryChunkCountRef.current === 1) {
        pushTrace("tts:queue_depth", { queuedMs });
      }

      if (queuedMs === 0 && ttsEndedRef.current) {
        ttsEndedRef.current = false;
        suppressIncomingAudioRef.current = false;
        playerStartedRef.current = false;
        playerStartedAtRef.current = null;
        interruptUnlockAtRef.current = 0;
        resetLocalSpeechGate();
        setAssistantLevel(0);
        pushTrace("tts:drained");
        sendJson({ type: "playback_ended" });
        if (phaseRef.current !== "user_speaking") {
          setPhase("listening");
        }
      }
      return;
    }
    if (event.type === "player_started" && !playerStartedRef.current) {
      playerStartedRef.current = true;
      playerStartedAtRef.current = performance.now();
      interruptUnlockAtRef.current = playerStartedAtRef.current + INTERRUPT_ARM_DELAY_MS;
      pushTrace("tts:first_audible_play", { interruptArmDelayMs: INTERRUPT_ARM_DELAY_MS });
    }
  });

  const handleBinaryMessage = useEffectEvent(async (data: ArrayBuffer | Blob) => {
    if (suppressIncomingAudioRef.current) {
      return;
    }

    const buffer = data instanceof Blob ? await data.arrayBuffer() : data;
    if (buffer.byteLength === 0) {
      return;
    }

    binaryChunkCountRef.current += 1;
    if (binaryChunkCountRef.current === 1) {
      pushTrace("tts:first_binary_chunk", { bytes: buffer.byteLength });
    }

    const int16 = new Int16Array(buffer);
    const floatChunk = new Float32Array(int16.length);
    for (let index = 0; index < int16.length; index += 1) {
      floatChunk[index] = int16[index] / 32768;
    }
    setAssistantLevel(computeLevel(floatChunk));
    audioRef.current?.enqueueTtsChunk(buffer);
  });

  const startConversation = useEffectEvent(async (configOverride?: MuseumConfig | null) => {
    const effectiveConfig = configOverride ?? config;
    if (!effectiveConfig || phaseRef.current !== "idle") {
      return;
    }

    pushTrace("start:click");
    audioFileLog("info", "用户点击开始对话");
    setActiveConfig(effectiveConfig);
    dispatch({ type: "phase", phase: "opening_session" });
    dispatch({ type: "assistant", text: "", replyId: null });
    dispatch({ type: "user", text: "" });
    binaryChunkCountRef.current = 0;
    playerStartedRef.current = false;
    playerStartedAtRef.current = null;
    interruptUnlockAtRef.current = 0;
    ttsEndedRef.current = false;
    lastQueuedMsRef.current = 0;
    suppressIncomingAudioRef.current = false;
    expectedSocketCloseRef.current = false;
    resetLocalSpeechGate();

    try {
      const audioRuntime = await createAudioRuntime({
        onAudioChunk: (chunk) => {
          const socket = wsRef.current;
          if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
          }
          socket.send(chunk);
        },
        onVad: (level, speaking) => {
          const now = performance.now();
          const stableSpeech = speaking && level >= MIN_VAD_LEVEL;
          stableSpeechFramesRef.current = stableSpeech ? stableSpeechFramesRef.current + 1 : 0;

          const shouldReportSpeaking =
            stableSpeech &&
            (!lastVoiceActivitySpeakingRef.current || now - lastVoiceActivitySentAtRef.current >= VOICE_ACTIVITY_HEARTBEAT_MS);
          const shouldReportStopped = !stableSpeech && lastVoiceActivitySpeakingRef.current;

          if (shouldReportSpeaking || shouldReportStopped) {
            sendJson({
              type: "voice_activity",
              speaking: stableSpeech,
              level,
            });
            lastVoiceActivitySpeakingRef.current = stableSpeech;
            lastVoiceActivitySentAtRef.current = now;
          }

          if (!stableSpeech) {
            return;
          }

          if ((phaseRef.current === "greeting" || phaseRef.current === "speaking") && playerStartedRef.current) {
            const canInterrupt =
              stableSpeechFramesRef.current >= INTERRUPT_ACTIVATION_FRAMES &&
              level >= MIN_INTERRUPT_LEVEL &&
              now >= interruptUnlockAtRef.current;
            if (canInterrupt && !suppressIncomingAudioRef.current) {
              suppressIncomingAudioRef.current = true;
              audioRef.current?.softInterrupt();
              sendJson({ type: "interrupt" });
              setPhase("interrupted");
            }
            return;
          }

          if (phaseRef.current === "listening" && stableSpeechFramesRef.current >= LISTENING_ACTIVATION_FRAMES) {
            setPhase("user_speaking");
          }
        },
        onPlaybackEvent: handlePlaybackEvent,
      }, {
        playbackTone: "panda_warm",
      });

      audioRef.current = audioRuntime;
      await audioRuntime.context.resume();
      pushTrace("media:get_user_media_ok", { sampleRate: audioRuntime.context.sampleRate });
      audioFileLog("info", `AudioContext resume 成功, state=${audioRuntime.context.state}, sampleRate=${audioRuntime.context.sampleRate}`);

      const socket = new WebSocket(getWsUrl());
      socket.binaryType = "arraybuffer";
      wsRef.current = socket;

      socket.onopen = () => {
        pushTrace("ws:open");
        sendJson({
          type: "hello",
          clientId: clientIdRef.current,
          resumeToken: resumeTokenRef.current,
        });
        sendJson({
          type: "start_session",
          clientId: clientIdRef.current,
          resumeToken: resumeTokenRef.current,
        });
        heartbeatRef.current = window.setInterval(() => {
          sendJson({ type: "heartbeat" });
        }, 2000);
      };

      socket.onmessage = (event) => {
        if (typeof event.data === "string") {
          const payload = JSON.parse(event.data) as Record<string, unknown>;
          const type = payload.type;

          if (type === "session_ready") {
            const resumeToken = payload.resumeToken;
            if (typeof resumeToken === "string") {
              resumeTokenRef.current = resumeToken;
              window.localStorage.setItem("museum-resume-token", resumeToken);
            }
            pushTrace("session:ready", {
              state: payload.state,
              upstreamMode: payload.upstreamMode,
              upstreamClient: payload.upstreamClient,
            });
            resetLocalSpeechGate();
            setPhase("greeting");
            return;
          }

          if (type === "state_changed") {
            const nextState = payload.state;
            if (typeof nextState === "string") {
              const shouldHoldListeningForDrain =
                nextState === "listening" &&
                (playerStartedRef.current || lastQueuedMsRef.current > 0 || ttsEndedRef.current);

              if (nextState === "speaking") {
                audioRef.current?.hardInterrupt();
                audioRef.current?.resetPlayerGain();
                ttsEndedRef.current = false;
                lastQueuedMsRef.current = 0;
                suppressIncomingAudioRef.current = false;
                playerStartedRef.current = false;
                playerStartedAtRef.current = null;
                interruptUnlockAtRef.current = 0;
              }
              if (nextState === "greeting") {
                ttsEndedRef.current = false;
                lastQueuedMsRef.current = 0;
              }
              if (nextState === "listening") {
                setAssistantLevel(0);
                playerStartedRef.current = false;
                playerStartedAtRef.current = null;
                interruptUnlockAtRef.current = 0;
                ttsEndedRef.current = false;
                lastQueuedMsRef.current = 0;
                resetLocalSpeechGate();
              }
              pushTrace("session:state_changed", { state: nextState });
              if (!shouldHoldListeningForDrain) {
                setPhase(nextState as VisitorPhase);
              }
            }
            return;
          }

          if (type === "assistant_text" && typeof payload.text === "string") {
            const text = payload.text;
            const replyId = typeof payload.replyId === "string" ? payload.replyId : null;
            startTransition(() => {
              dispatch({ type: "assistant", text, replyId });
            });
            return;
          }

          if (type === "user_transcript" && typeof payload.text === "string") {
            const text = payload.text;
            startTransition(() => {
              dispatch({ type: "user", text });
            });
            return;
          }

          if (type === "barge_in_confirmed") {
            pushTrace("session:barge_in_confirmed");
            suppressIncomingAudioRef.current = false;
            audioRef.current?.hardInterrupt();
            playerStartedRef.current = false;
            playerStartedAtRef.current = null;
            interruptUnlockAtRef.current = 0;
            ttsEndedRef.current = false;
            lastQueuedMsRef.current = 0;
            resetLocalSpeechGate();
            setAssistantLevel(0);
            setPhase("listening");
            return;
          }

          if (type === "tts_end") {
            pushTrace("tts:end", { phase: payload.phase });
            ttsEndedRef.current = true;
            if (lastQueuedMsRef.current <= 0) {
              ttsEndedRef.current = false;
              suppressIncomingAudioRef.current = false;
              playerStartedRef.current = false;
              playerStartedAtRef.current = null;
              interruptUnlockAtRef.current = 0;
              resetLocalSpeechGate();
              setAssistantLevel(0);
              sendJson({ type: "playback_ended" });
              if (phaseRef.current !== "user_speaking") {
                setPhase("listening");
              }
            }
            return;
          }

          if (type === "session_closed") {
            pushTrace("session:closed", { reason: payload.reason });
            clearResumeToken();
            void releaseResources("idle");
            return;
          }

          if (type === "error") {
            pushTrace("session:error", { message: String(payload.message ?? "") });
            void releaseResources("error", String(payload.message ?? "实时会话发生错误。"));
          }
          return;
        }

        if (event.data instanceof ArrayBuffer || event.data instanceof Blob) {
          void handleBinaryMessage(event.data);
        }
      };

      socket.onclose = (event) => {
        const expected = expectedSocketCloseRef.current;
        expectedSocketCloseRef.current = false;
        pushTrace("ws:close", { expected, code: event.code, phase: phaseRef.current });
        if (expected || teardownInFlightRef.current) {
          return;
        }
        if (phaseRef.current !== "idle" && phaseRef.current !== "closing_session" && phaseRef.current !== "error") {
          void releaseResources("idle");
        }
      };

      socket.onerror = () => {
        pushTrace("ws:error");
        void releaseResources("error", "实时连接失败，请检查网络或上游配置。");
      };
    } catch (error) {
      let message: string;
      if (error && typeof error === "object" && "hint" in error) {
        // AudioInitError with hint
        const audioErr = error as AudioInitError;
        message = `${audioErr.message}\n\n排查建议：${audioErr.hint}`;
        audioFileLog("error", `音频初始化失败 [${audioErr.code}]: ${audioErr.message} | 排查: ${audioErr.hint}`);
      } else {
        message = error instanceof Error ? error.message : "启动实时会话失败。";
        audioFileLog("error", `会话启动失败: ${message}`);
      }
      pushTrace("start:failed", { message, code: (error as AudioInitError)?.code });
      await releaseResources("error", message);
    }
  });

  const endConversation = useEffectEvent(async () => {
    if (phaseRef.current === "idle" || phaseRef.current === "closing_session") {
      return;
    }
    pushTrace("end:manual");
    setPhase("closing_session");
    clearResumeToken();
    sendJson({ type: "end_session", reason: "manual_end" });
    window.setTimeout(() => {
      if (phaseRef.current === "closing_session") {
        void releaseResources("idle");
      }
    }, 1500);
  });

  useEffect(() => {
    phaseRef.current = state.phase;
  }, [state.phase]);

  useEffect(() => {
    return () => {
      unmountedRef.current = true;
      if (heartbeatRef.current !== null) {
        window.clearInterval(heartbeatRef.current);
        heartbeatRef.current = null;
      }
      const socket = wsRef.current;
      wsRef.current = null;
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
        expectedSocketCloseRef.current = true;
        socket.close();
      }
      const audio = audioRef.current;
      audioRef.current = null;
      if (audio) {
        void audio.close();
      }
    };
  }, []);

  const statusTextMap: Record<VisitorPhase, string> = {
    boot: "系统启动中",
    idle: "等待开始",
    opening_session: "正在开启会话",
    greeting: "已开启对话",
    listening: "正在听你说话",
    user_speaking: "正在听你说话",
    thinking: "我在思考",
    speaking: "请等待小熊猫说完话",
    interrupted: "检测到你开口，正在打断播报",
    closing_session: "正在结束会话",
    error: "发生错误，请重新开始",
  };

  return {
    phase: state.phase,
    assistantText: state.assistantText,
    assistantReplyId: state.assistantReplyId,
    userText: state.userText,
    error: state.error,
    assistantLevel,
    activeConfig,
    statusText: statusTextMap[state.phase],
    startConversation,
    endConversation,
  };
}
