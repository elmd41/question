from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .logging_utils import configure_logging, get_logger
from .schemas import (
    AdminLoginRequest,
    AdminSessionStatus,
    ConfigBundle,
    MuseumConfig,
    PublicConfigResponse,
    RealtimeUpstreamConfig,
    UpstreamConfigResponse,
    UpstreamConfigSnapshot,
    UpstreamConfigUpdateRequest,
    mask_secret,
)
from .security import AdminSecurity
from .session_manager import RealtimeSessionManager, SessionHandle
from .settings import Settings
from .store import ConfigStore

logger = get_logger("main")

FIELD_LABELS = {
    "display_title": "展示标题",
    "display_subtitle": "展示副标题",
    "idle_timeout_sec": "静默超时",
    "auto_end_mode": "自动结束模式",
    "welcome_text": "欢迎语",
    "model_family": "模型版本",
    "bot_name": "角色名称",
    "system_role": "讲解角色设定",
    "speaking_style": "回答风格",
    "character_manifest": "角色设定",
    "mode": "运行模式",
    "base_url": "WebSocket 地址",
    "app_id": "App ID",
    "access_key": "Access Key",
    "resource_id": "Resource ID",
    "app_key": "App Key",
}


@dataclass(slots=True)
class ConnectionContext:
    client_id: str | None = None
    resume_token: str | None = None
    session: SessionHandle | None = None


def normalize_validation_message(field_name: str, error: dict) -> str:
    label = FIELD_LABELS.get(field_name, field_name)
    error_type = str(error.get("type", ""))
    message = str(error.get("msg", "")).removeprefix("Value error, ").strip()

    if error_type == "literal_error":
        if field_name == "auto_end_mode":
            return "请选择有效的自动结束模式。"
        if field_name == "model_family":
            return "请选择有效的模型版本。"
        if field_name == "mode":
            return "请选择有效的运行模式。"

    if error_type in {"missing", "string_too_short"}:
        return f"{label}不能为空。"

    if message:
        return message

    return f"{label}填写不正确。"


def build_validation_detail(exc: ValidationError) -> dict:
    field_errors: dict[str, list[str]] = {}
    for error in exc.errors():
        loc = error.get("loc", ())
        if not loc:
            continue
        field_name = ".".join(str(part) for part in loc)
        field_errors.setdefault(field_name, []).append(
            normalize_validation_message(str(loc[-1]), error)
        )

    return {
        "message": "发布失败，请先检查标红字段。",
        "fieldErrors": field_errors,
    }


def build_upstream_response(snapshot: UpstreamConfigSnapshot) -> UpstreamConfigResponse:
    return UpstreamConfigResponse(
        mode=snapshot.config.mode,
        base_url=snapshot.config.base_url,
        app_id=snapshot.config.app_id,
        resource_id=snapshot.config.resource_id,
        app_key=snapshot.config.app_key,
        access_key_configured=bool(snapshot.config.access_key),
        access_key_masked=mask_secret(snapshot.config.access_key) or None,
        qwen_base_url=snapshot.config.qwen_base_url,
        qwen_model=snapshot.config.qwen_model,
        qwen_voice=snapshot.config.qwen_voice,
        qwen_api_key_configured=bool(snapshot.config.qwen_api_key),
        qwen_api_key_masked=mask_secret(snapshot.config.qwen_api_key) or None,
        aliyun_asr_model=snapshot.config.aliyun_asr_model,
        aliyun_llm_model=snapshot.config.aliyun_llm_model,
        aliyun_tts_model=snapshot.config.aliyun_tts_model,
        aliyun_tts_voice=snapshot.config.aliyun_tts_voice,
        aliyun_asr_max_sentence_silence=snapshot.config.aliyun_asr_max_sentence_silence,
        updated_at=snapshot.updated_at,
        updated_by=snapshot.updated_by,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_file_path, settings.log_level)
        app.state.settings = settings
        app.state.security = AdminSecurity(settings)
        app.state.store = ConfigStore(settings.database_path)
        password_hash = app.state.security.hash_password(settings.admin_password)
        app.state.store.initialize(settings.default_config, password_hash, settings.build_upstream_config())
        app.state.settings.apply_upstream_config(app.state.store.get_upstream_config().config)
        app.state.sessions = RealtimeSessionManager(settings, app.state.store)
        logger.info(
            "app_started upstream_mode=%s log_file=%s base_url=%s frontend_dist=%s dist_exists=%s frozen=%s",
            settings.upstream_mode,
            settings.log_file_path,
            settings.upstream_base_url,
            settings.frontend_dist_dir,
            settings.frontend_dist_dir.exists(),
            getattr(__import__("sys"), "frozen", False),
        )
        yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    def get_security() -> AdminSecurity:
        return app.state.security

    def get_store() -> ConfigStore:
        return app.state.store

    def get_sessions() -> RealtimeSessionManager:
        return app.state.sessions

    def validate_config_payload(body: object) -> MuseumConfig:
        try:
            return MuseumConfig.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=build_validation_detail(exc)) from exc

    def validate_upstream_payload(body: object) -> UpstreamConfigUpdateRequest:
        try:
            return UpstreamConfigUpdateRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=build_validation_detail(exc)) from exc

    @app.get("/api/health")
    async def healthcheck() -> dict:
        published = get_store().get_published()
        return {
            "ok": True,
            "upstreamMode": settings.upstream_mode,
            "configVersion": published.version,
        }

    @app.get("/api/public/config", response_model=PublicConfigResponse)
    async def get_public_config() -> PublicConfigResponse:
        published = get_store().get_published()
        return PublicConfigResponse(version=published.version, config=published.config)

    @app.get("/api/admin/session", response_model=AdminSessionStatus)
    async def get_admin_session(request: Request) -> Response:
        security = get_security()
        csrf_token, should_set_cookie = security.get_or_create_csrf_token(request)
        response = JSONResponse(
            {
                "authenticated": security.read_session(request) is not None,
                "csrf_token": csrf_token,
            }
        )
        if should_set_cookie:
            security.set_csrf_cookie(response, csrf_token)
        return response

    @app.post("/api/admin/login", response_model=AdminSessionStatus)
    async def admin_login(request: Request, payload: AdminLoginRequest) -> Response:
        security = get_security()
        security.validate_csrf(request)
        ip_key = request.client.host if request.client else "local"
        security.rate_limiter.ensure_allowed(ip_key)
        stored_hash = get_store().get_admin_password_hash()
        if not security.verify_password(stored_hash, payload.password):
            security.rate_limiter.record_failure(ip_key)
            raise HTTPException(status_code=401, detail="密码错误。")
        csrf_token = request.cookies.get(settings.csrf_cookie_name, "")
        response = JSONResponse(
            {
                "authenticated": True,
                "csrf_token": csrf_token,
            }
        )
        security.create_session(response)
        security.rate_limiter.reset(ip_key)
        return response

    @app.post("/api/admin/logout")
    async def admin_logout(request: Request) -> Response:
        security = get_security()
        security.validate_csrf(request)
        response = JSONResponse({"ok": True})
        security.clear_session(response)
        return response

    @app.get("/api/admin/config", response_model=ConfigBundle)
    async def get_admin_config(request: Request) -> ConfigBundle:
        get_security().require_admin(request)
        store = get_store()
        return ConfigBundle(draft=store.get_draft(), published=store.get_published())

    @app.put("/api/admin/config")
    async def update_draft_config(request: Request) -> dict:
        security = get_security()
        security.validate_csrf(request)
        security.require_admin(request)
        body = await request.json()
        config = validate_config_payload(body)
        draft = get_store().save_draft(config, updated_by="admin")
        return {"updatedAt": draft.updated_at.isoformat(), "ok": True}

    @app.post("/api/admin/config/publish")
    async def publish_config(request: Request) -> dict:
        security = get_security()
        security.validate_csrf(request)
        security.require_admin(request)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        if isinstance(body, dict) and body:
            config = validate_config_payload(body)
            get_store().save_draft(config, updated_by="admin")
        published = get_store().publish_draft("admin")
        return {
            "version": published.version,
            "publishedAt": published.timestamp.isoformat(),
        }

    @app.get("/api/admin/config/history")
    async def config_history(request: Request) -> list[dict]:
        get_security().require_admin(request)
        items = get_store().list_published()
        return [item.model_dump(mode="json") for item in items]

    @app.get("/api/admin/upstream-config", response_model=UpstreamConfigResponse)
    async def get_admin_upstream_config(request: Request) -> UpstreamConfigResponse:
        get_security().require_admin(request)
        return build_upstream_response(get_store().get_upstream_config())

    @app.put("/api/admin/upstream-config")
    async def update_admin_upstream_config(request: Request) -> dict:
        security = get_security()
        security.validate_csrf(request)
        security.require_admin(request)
        body = await request.json()
        payload = validate_upstream_payload(body)
        current = get_store().get_upstream_config().config
        effective_access_key = payload.access_key if payload.access_key not in {None, ""} else current.access_key
        effective_qwen_api_key = payload.qwen_api_key if payload.qwen_api_key not in {None, ""} else current.qwen_api_key
        try:
            next_config = RealtimeUpstreamConfig.model_validate(
                {
                    "mode": payload.mode,
                    "base_url": payload.base_url,
                    "app_id": payload.app_id,
                    "access_key": effective_access_key,
                    "resource_id": payload.resource_id,
                    "app_key": payload.app_key,
                    "qwen_api_key": effective_qwen_api_key,
                    "qwen_base_url": payload.qwen_base_url,
                    "qwen_model": payload.qwen_model,
                    "qwen_voice": payload.qwen_voice,
                    "aliyun_asr_model": payload.aliyun_asr_model,
                    "aliyun_llm_model": payload.aliyun_llm_model,
                    "aliyun_tts_model": payload.aliyun_tts_model,
                    "aliyun_tts_voice": payload.aliyun_tts_voice,
                    "aliyun_asr_max_sentence_silence": payload.aliyun_asr_max_sentence_silence,
                }
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=build_validation_detail(exc)) from exc

        snapshot = get_store().save_upstream_config(next_config, updated_by="admin")
        app.state.settings.apply_upstream_config(snapshot.config)
        logger.info(
            "admin_upstream_config_updated mode=%s base_url=%s app_id=%s resource_id=%s access_key_configured=%s",
            snapshot.config.mode,
            snapshot.config.base_url,
            snapshot.config.app_id,
            snapshot.config.resource_id,
            bool(snapshot.config.access_key),
        )
        return {
            "updatedAt": snapshot.updated_at.isoformat(),
            "mode": snapshot.config.mode,
        }

    @app.post("/api/admin/session/reset")
    async def reset_runtime_session(request: Request) -> dict:
        security = get_security()
        security.validate_csrf(request)
        security.require_admin(request)
        closed = await get_sessions().force_close_active("admin_reset")
        return {"closed": closed}

    @app.websocket("/api/realtime")
    async def realtime_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        context = ConnectionContext()
        sessions = get_sessions()
        logger.info("ws_connected client=%s", websocket.client)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text"):
                    payload = json.loads(message["text"])
                    msg_type = payload.get("type")
                    _ws_log = logger.debug if msg_type in ("heartbeat", "voice_activity") else logger.info
                    _ws_log(
                        "ws_message type=%s client_id=%s session_id=%s",
                        msg_type,
                        context.client_id,
                        context.session.session_id if context.session is not None else None,
                    )
                    if msg_type == "hello":
                        context.client_id = payload.get("clientId")
                        context.resume_token = payload.get("resumeToken")
                    elif msg_type == "start_session":
                        client_id = payload.get("clientId") or context.client_id
                        if not client_id:
                            await websocket.send_json({"type": "error", "message": "missing clientId"})
                            continue
                        context.client_id = client_id
                        context.resume_token = payload.get("resumeToken") or context.resume_token
                        try:
                            context.session = await sessions.start_session(
                                websocket,
                                client_id=context.client_id,
                                resume_token=context.resume_token,
                            )
                            context.resume_token = context.session.resume_token
                            logger.info(
                                "ws_session_started session_id=%s upstream_mode=%s",
                                context.session.session_id,
                                settings.upstream_mode,
                            )
                        except Exception as exc:
                            logger.exception("ws_session_start_failed client_id=%s", context.client_id)
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": str(exc) or "开启对话失败，请检查实时服务配置。",
                                }
                            )
                            context.session = None
                    elif msg_type == "end_session":
                        if context.session is not None:
                            await sessions.close_session(context.session, payload.get("reason", "manual_end"))
                            context.session = None
                    elif msg_type == "heartbeat":
                        if context.session is not None:
                            await sessions.heartbeat(context.session)
                    elif msg_type == "voice_activity":
                        if context.session is not None:
                            await sessions.record_voice_activity(
                                context.session,
                                speaking=bool(payload.get("speaking", False)),
                                level=payload.get("level"),
                            )
                    elif msg_type == "playback_ended" and context.session is not None:
                        from .realtime.aliyun_split import AliyunSplitClient
                        if isinstance(context.session.upstream, AliyunSplitClient):
                            context.session.upstream.notify_playback_ended()
                    elif msg_type == "interrupt" and context.session is not None:
                        logger.info("ws_interrupt session_id=%s", context.session.session_id)
                        get_store().log_session_event(
                            context.session.session_id,
                            context.session.client_id,
                            context.session.config_version,
                            "soft_interrupt",
                            None,
                        )
                elif message.get("bytes") is not None and context.session is not None:
                    logger.debug(
                        "ws_audio_chunk session_id=%s bytes=%s",
                        context.session.session_id,
                        len(message["bytes"]),
                    )
                    await sessions.send_audio(context.session, message["bytes"])
        except WebSocketDisconnect:
            logger.info(
                "ws_disconnected client_id=%s session_id=%s",
                context.client_id,
                context.session.session_id if context.session is not None else None,
            )
        finally:
            if context.session is not None:
                await sessions.detach(context.session, websocket)

    dist_dir = settings.frontend_dist_dir
    if dist_dir.exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def frontend_files(full_path: str) -> Response:
            candidate = dist_dir / full_path
            if full_path and candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist_dir / "index.html")

    else:

        @app.get("/")
        async def no_frontend() -> dict:
            return {
                "message": "frontend build not found",
                "expectedPath": str(Path(dist_dir)),
            }

    return app


app = create_app()
