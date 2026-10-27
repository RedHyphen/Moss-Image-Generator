from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings
from .controller import interpret_request
from .db import Database, utc_now
from .inference import InferenceError, VLLMChatClient, VLLMImageClient
from .jobs import JobWorker
from .layout import validate_layout_draft
from .monitoring import detect_hardware, detect_runtime_compatibility
from .security import hash_password, new_token, verify_password
from .storage import (
    contained_path,
    find_orphan_media,
    media_key,
    media_usage_bytes,
    resolve_stored_path,
    safe_display_filename,
    save_upload,
)

logging.basicConfig(level=os.getenv("MOSS_LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application wiring and process-wide services
# ---------------------------------------------------------------------------
settings = Settings.from_env()
settings.ensure_dirs()
db = Database(settings.db_path)
db.initialize(legacy_owner_username=os.getenv("MOSS_LEGACY_OWNER_USERNAME") or None)
vllm = VLLMImageClient(settings.moss_vllm_url, settings.moss_model, settings.vllm_api_key, settings.inference_timeout_seconds)
chat = VLLMChatClient(settings.vllm_chat_url, settings.vllm_chat_model, settings.vllm_api_key)
reference_mutation_lock = asyncio.Lock()
worker = JobWorker(db, settings, vllm)
login_failures: dict[str, list[float]] = {}
SUPPORTED_MOSS_MODELS = frozenset(
    {
        "OpenMOSS-Team/MOSS-Image-Base",
        "OpenMOSS-Team/MOSS-Image-SFT",
    }
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    referenced_paths = set()
    for raw_path in db.list_media_paths():
        try:
            referenced_paths.add(
                resolve_stored_path(raw_path, settings.data_dir, settings.uploads_dir, settings.images_dir)
            )
        except ValueError:
            logger.warning("media reconciliation skipped an unsafe stored path")
    orphaned = find_orphan_media(
        (settings.uploads_dir, settings.images_dir),
        referenced_paths,
    )
    if orphaned:
        logger.warning("media reconciliation found %s old unreferenced file(s); run scripts/reconcile_media.py", len(orphaned))
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(title="Moss-Image-Generator", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)


class NetworkGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else None
        if not client:
            return JSONResponse({"detail": "Unable to validate client network address"}, status_code=403)
        try:
            address = ipaddress.ip_address(client)
            # ASGI servers and reverse proxies may represent an IPv4 client
            # as an IPv4-mapped IPv6 address. Normalize it before comparing
            # against configured IPv4 LAN CIDRs so dual-stack deployments do
            # not accidentally reject an authorized client.
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_loopback and not any(address in ipaddress.ip_network(net) for net in settings.trusted_subnets):
                return JSONResponse({"detail": "Client is outside the configured trusted LAN subnets"}, status_code=403)
        except ValueError:
            return JSONResponse({"detail": "Unable to validate client network address"}, status_code=403)
        return await call_next(request)


app.add_middleware(NetworkGuardMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply browser-side isolation and prevent private API response caching."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/" or request.url.path.endswith(".html"):
            # The entrypoint selects hashed frontend assets. Do not let a
            # rollback or deployment leave browsers running a discarded UI.
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Serialization and authentication dependencies
# ---------------------------------------------------------------------------
def row_json(row) -> dict[str, Any]:
    return dict(row) if row else {}


def session_from_request(request: Request):
    token = request.cookies.get("moss_session")
    return db.get_session(token) if token else None


async def current_session(request: Request):
    session = session_from_request(request)
    if not session:
        raise HTTPException(401, "Authentication required")
    return session


async def csrf_session(request: Request):
    session = await current_session(request)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("X-CSRF-Token") != session["csrf_token"]:
        raise HTTPException(403, "Missing or invalid CSRF token")
    return session


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="generate", pattern=r"^generate$")
    prompt: str = Field(min_length=1, max_length=16_000)
    original_prompt: str | None = Field(default=None, max_length=16_000)
    negative_prompt: str = Field(default="", max_length=4_000)
    model: str | None = None
    width: int = Field(default=512, ge=512, le=2048)
    height: int = Field(default=512, ge=512, le=2048)
    steps: int = Field(default=50, ge=1, le=200)
    guidance_scale: float = Field(default=3.0, ge=0, le=20)
    shift: float = Field(default=5.0, ge=0, le=20)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    n: int = Field(default=1, ge=1, le=4)
    randomized_batch: bool = False
    slg: bool = True
    layout_json: dict[str, Any] | None = None
    reference_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("width", "height")
    @classmethod
    def multiples_of_16(cls, value: int):
        if value % 16:
            raise ValueError("width and height must be multiples of 16")
        return value

    @field_validator("layout_json")
    @classmethod
    def validate_layout(cls, value):
        if value is None:
            return value
        try:
            return validate_layout_draft(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class EnhanceRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8_000)
    mode: str = Field(default="caption", pattern=r"^(caption|layout)$")
    width: int = Field(default=512, ge=512, le=2048)
    height: int = Field(default=512, ge=512, le=2048)

    @field_validator("width", "height")
    @classmethod
    def enhancement_dimensions(cls, value: int):
        if value % 16:
            raise ValueError("width and height must be multiples of 16")
        return value


class ReferenceOrderRequest(BaseModel):
    ids: list[str] = Field(max_length=32)


class FavoriteRequest(BaseModel):
    favorite: bool


class ConversationRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ConversationMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16_000)
    reference_ids: list[str] = Field(default_factory=list, max_length=8)
    image_ids: list[str] = Field(default_factory=list, max_length=8)
    parent_image_id: str | None = None
    negative_prompt: str = Field(default="", max_length=4_000)
    width: int = Field(default=512, ge=512, le=2048)
    height: int = Field(default=512, ge=512, le=2048)
    steps: int = Field(default=50, ge=1, le=200)
    guidance_scale: float = Field(default=3.0, ge=0, le=20)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    n: int = Field(default=1, ge=1, le=4)
    randomized_batch: bool = False

    @field_validator("width", "height")
    @classmethod
    def conversation_dimensions(cls, value: int):
        if value % 16:
            raise ValueError("width and height must be multiples of 16")
        return value


# ---------------------------------------------------------------------------
# Authentication and account routes
# ---------------------------------------------------------------------------
@app.get("/api/auth/status")
async def auth_status():
    return {"setup_required": db.user_count() == 0}


def login_response(user_id: int, username: str):
    token, csrf = new_token(), new_token()
    db.create_session(user_id, token, csrf, settings.session_days)
    response = JSONResponse({"username": username, "csrf_token": csrf})
    response.set_cookie("moss_session", token, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=settings.session_days * 86400)
    return response


@app.post("/api/auth/setup")
async def setup(body: SetupRequest):
    # The first-user check and insert must be one critical section. Otherwise
    # two simultaneous first-run requests can both observe an empty database
    # and create multiple administrator accounts.
    with db.lock:
        if db.user_count():
            raise HTTPException(409, "Administrator account already exists")
        user_id = db.create_user(body.username, hash_password(body.password))
    return login_response(user_id, body.username)


@app.post("/api/auth/login")
async def login(request: Request, body: LoginRequest):
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{body.username.lower()}"
    now = time.monotonic()
    recent = [stamp for stamp in login_failures.get(key, []) if now - stamp < 300]
    if len(recent) >= 5:
        raise HTTPException(429, "Too many failed login attempts; try again later")
    user = db.get_user(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        recent.append(now)
        login_failures[key] = recent
        raise HTTPException(401, "Invalid username or password")
    login_failures.pop(key, None)
    return login_response(user["id"], user["username"])


@app.post("/api/auth/logout")
async def logout(request: Request, session=Depends(csrf_session)):
    token = request.cookies.get("moss_session")
    if token:
        db.delete_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie("moss_session")
    return response


@app.get("/api/auth/me")
async def me(request: Request):
    session = await current_session(request)
    return {"username": session["username"], "csrf_token": session["csrf_token"]}


def _user_id(session) -> int:
    return int(session["user_id"])


@app.delete("/api/account/history")
async def clear_account_history(session=Depends(csrf_session)):
    """Clear only the authenticated account's conversations and media history."""
    user_id = _user_id(session)
    async with reference_mutation_lock:
        with db.lock:
            deleted = db.clear_user_history(user_id)
            active_jobs = int(deleted.get("active_jobs", 0))
            if active_jobs:
                raise HTTPException(
                    409,
                    f"History cannot be cleared while {active_jobs} generation job(s) are active; wait for completion first",
                )

            files_removed = 0
            cleanup_errors = 0
            for raw_path in deleted.pop("paths", []):
                try:
                    path = resolve_stored_path(raw_path, settings.data_dir, settings.uploads_dir, settings.images_dir)
                except (KeyError, TypeError, ValueError):
                    logger.warning("skipping unsafe history path during account cleanup")
                    cleanup_errors += 1
                    continue
                # A promoted gallery output may also be referenced by another
                # account. Never unlink a path that remains in the database.
                if path.is_file() and not _reference_record_uses_path(path) and not _image_record_uses_path(path):
                    try:
                        path.unlink()
                        files_removed += 1
                    except OSError:
                        logger.warning("history row was cleared but media file could not be removed: %s", path)
                        cleanup_errors += 1
            deleted["files_removed"] = files_removed
            deleted["cleanup_errors"] = cleanup_errors
            return {
                "ok": True,
                "deleted": deleted,
                "preserved": ["account", "model files"],
            }


# ---------------------------------------------------------------------------
# Conversation routes and message serialization
# ---------------------------------------------------------------------------
def _message_json(row) -> dict[str, Any]:
    item = row_json(row)
    try:
        item["plan"] = json.loads(item.pop("plan_json", "{}"))
    except json.JSONDecodeError:
        item["plan"] = {}
    assets = []
    for asset in db.list_message_assets(item["id"]):
        asset_item = row_json(asset)
        if asset_item["asset_type"] == "reference":
            source = db.get_reference(asset_item["asset_id"])
            if source:
                asset_item.update({"filename": source["filename"], "role": source["role"], "note": source["note"]})
                asset_item["url"] = f"/api/references/{source['id']}/file"
        elif asset_item["asset_type"] == "image":
            source = db.get_image(asset_item["asset_id"])
            if source:
                asset_item.update({"prompt": source["prompt"], "kind": source["kind"]})
                try:
                    metadata = json.loads(source["metadata_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    metadata = {}
                if isinstance(metadata, dict):
                    asset_item["metadata"] = {
                        key: metadata[key]
                        for key in (
                            "original_prompt",
                            "negative_prompt",
                            "model",
                            "width",
                            "height",
                            "steps",
                            "guidance_scale",
                            "shift",
                            "seed",
                            "output_seed",
                            "n",
                            "randomized_batch",
                            "slg",
                            "layout_json",
                        )
                        if key in metadata
                    }
                asset_item["url"] = f"/api/images/{source['id']}/file"
        if asset_item.get("url"):
            assets.append(asset_item)
    item["assets"] = assets
    return item


async def _require_moss_model(model_id: str) -> None:
    if model_id not in SUPPORTED_MOSS_MODELS:
        raise HTTPException(
            422,
            f"Unsupported MOSS-Image checkpoint {model_id}; choose Base or SFT",
        )
    health = await vllm.health()
    if not health.get("ok"):
        raise HTTPException(503, "The local MOSS-Image vLLM worker is unavailable; generation was not queued")
    worker_model_ids = {item.get("id") for item in health.get("models", []) if isinstance(item, dict)}
    if model_id not in worker_model_ids:
        available = ", ".join(sorted(str(value) for value in worker_model_ids if value)) or "none reported"
        raise HTTPException(422, f"MOSS model {model_id} is not served by the worker (available: {available})")


def _conversation_json(row, include_messages: bool = False) -> dict[str, Any]:
    item = row_json(row)
    if include_messages:
        item["messages"] = [_message_json(message) for message in db.list_messages(item["id"])]
    return item


def _active_job_uses_asset(asset_id: str, fields: tuple[str, ...]) -> bool:
    """Prevent deletion of a raster still required by queued/running work."""
    for row in db.list_jobs(limit=200):
        if row["status"] not in {"queued", "running", "cancelling"}:
            continue
        try:
            params = json.loads(row["params_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if any(params.get(field) == asset_id for field in fields):
            return True
    return False


def _image_record_uses_path(path: Path) -> bool:
    """Check gallery ownership using resolved paths, including legacy relatives."""
    # Deletion must consider every gallery row. A bounded recent-image scan can
    # unlink an old raster that is still referenced by a long-lived record.
    for row in db.list_images(limit=None):
        try:
            if resolve_stored_path(row["path"], settings.data_dir, settings.images_dir) == path:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def _reference_record_uses_path(path: Path) -> bool:
    """Check reference ownership using resolved paths, including legacy relatives."""
    for row in db.list_references():
        try:
            if resolve_stored_path(row["path"], settings.data_dir, settings.uploads_dir, settings.images_dir) == path:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def _record_conversation_failure(conversation_id: str, message_id: str, plan: dict[str, Any], error: str) -> None:
    """Persist a failed conversational request even when no job was queued."""
    db.update_message(message_id, status="failed", error=error)
    db.add_message(
        {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": f"I could not start that local operation: {error}",
            "status": "failed",
            "plan_json": json.dumps(plan, ensure_ascii=False),
            "job_id": None,
            "error": error,
            "created_at": utc_now(),
        }
    )


@app.get("/api/conversations")
async def conversations(q: str = Query(default="", max_length=200), session=Depends(current_session)):
    return [_conversation_json(row) for row in db.list_conversations(search=q, user_id=_user_id(session))]


@app.post("/api/conversations")
async def create_conversation(body: ConversationRequest | None = None, session=Depends(csrf_session)):
    conversation_id = str(uuid.uuid4())
    user_id = _user_id(session)
    db.add_conversation(conversation_id, body.title if body else "New conversation", user_id=user_id)
    return _conversation_json(db.get_conversation(conversation_id, user_id), include_messages=True)


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, session=Depends(current_session)):
    row = db.get_conversation(conversation_id, _user_id(session))
    if not row:
        raise HTTPException(404, "Conversation not found")
    return _conversation_json(row, include_messages=True)


@app.get("/api/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, session=Depends(current_session)):
    """Export conversation state and provenance without server filesystem paths."""
    user_id = _user_id(session)
    row = db.get_conversation(conversation_id, user_id)
    if not row:
        raise HTTPException(404, "Conversation not found")
    payload = _conversation_json(row, include_messages=True)
    reference_ids = {
        asset["asset_id"]
        for message in db.list_messages(conversation_id)
        for asset in db.list_message_assets(message["id"])
        if asset["asset_type"] == "reference"
    }
    exported_references = []
    for reference_id in reference_ids:
        reference = db.get_reference(reference_id, user_id)
        if not reference:
            continue
        reference_item = row_json(reference)
        reference_item.pop("path", None)
        reference_item["filename"] = safe_display_filename(reference_item.get("filename"))
        reference_item["url"] = f"/api/references/{reference_item['id']}/file"
        exported_references.append(reference_item)
    payload["references"] = sorted(exported_references, key=lambda item: (item.get("created_at", ""), item["id"]))
    exported_images = []
    for image in db.list_conversation_images(conversation_id, user_id):
        item = row_json(image)
        item.pop("path", None)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json", "{}"))
        except (TypeError, json.JSONDecodeError):
            item["metadata"] = {}
        item["url"] = f"/api/images/{item['id']}/file"
        exported_images.append(item)
    payload["images"] = exported_images
    payload["exported_at"] = utc_now()
    filename = safe_display_filename(f"moss-conversation-{conversation_id}.json")
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.patch("/api/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationRequest, session=Depends(csrf_session)):
    user_id = _user_id(session)
    if not db.update_conversation(conversation_id, title=body.title, user_id=user_id):
        raise HTTPException(404, "Conversation not found")
    return _conversation_json(db.get_conversation(conversation_id, user_id))


@app.post("/api/conversations/{conversation_id}/messages")
async def create_conversation_message(conversation_id: str, body: ConversationMessageRequest, session=Depends(csrf_session)):
    user_id = _user_id(session)
    if not db.get_conversation(conversation_id, user_id):
        raise HTTPException(404, "Conversation not found")
    if db.queued_count() >= settings.max_queue:
        raise HTTPException(429, "Generation queue is full")
    if body.width * body.height > settings.max_pixels:
        raise HTTPException(422, f"Resolution exceeds the configured {settings.max_pixels:,}-pixel limit")
    if len(set(body.reference_ids)) != len(body.reference_ids) or len(set(body.image_ids)) != len(body.image_ids):
        raise HTTPException(422, "Attachments must not be repeated")
    for reference_id in body.reference_ids:
        if not db.get_reference(reference_id, user_id):
            raise HTTPException(422, f"Reference image {reference_id} does not exist")
    for image_id in body.image_ids:
        if not db.get_image(image_id, user_id):
            raise HTTPException(422, f"Generated image {image_id} does not exist")
    if body.parent_image_id and not db.get_image(body.parent_image_id, user_id):
        raise HTTPException(422, "The selected parent image does not exist")
    images = db.list_conversation_images(conversation_id, user_id)
    latest_image_id = images[-1]["id"] if images else None
    latest_image_prompt = str(images[-1]["prompt"] or "") if images else None
    routing_hint = None
    if settings.router_assistance:
        try:
            routing_hint = await chat.classify(
                body.content,
                has_attached_image=bool(body.reference_ids or body.image_ids or body.parent_image_id),
                has_conversation_image=bool(latest_image_id),
            )
        except InferenceError as exc:
            # The optional classifier must never make the local image workflow
            # unavailable; the deterministic structured fallback remains safe.
            logger.warning("optional local routing assistant unavailable; using fallback: %s", exc)
    try:
        plan = interpret_request(
            body.content,
            reference_ids=body.reference_ids,
            image_ids=body.image_ids,
            parent_image_id=body.parent_image_id,
            latest_image_id=latest_image_id,
            moss_model=settings.moss_model,
            intent_hint=routing_hint.get("intent") if routing_hint else None,
            preservation_hint=routing_hint.get("preservation") if routing_hint else None,
            previous_prompt=latest_image_prompt,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    message_id = str(uuid.uuid4())
    execution_plan = plan.as_dict()
    db.add_message(
        {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": "user",
            "content": body.content.strip(),
            "status": "queued",
            "plan_json": json.dumps(execution_plan, ensure_ascii=False),
            "job_id": None,
            "error": "",
            "created_at": utc_now(),
        }
    )
    for ordinal, reference_id in enumerate(body.reference_ids):
        reference = db.get_reference(reference_id, user_id)
        db.add_message_asset(message_id, "reference", reference_id, ordinal, reference["role"], reference["note"])
    for ordinal, image_id in enumerate(body.image_ids):
        db.add_message_asset(message_id, "image", image_id, ordinal, "source")
    try:
        await _require_moss_model(plan.model)
    except HTTPException as exc:
        detail = str(exc.detail)
        _record_conversation_failure(conversation_id, message_id, execution_plan, detail)
        raise

    params = {
        "kind": "generate",
        "prompt": plan.prompt,
        "original_prompt": plan.original_prompt,
        "identity_instruction": plan.identity_instruction,
        "negative_prompt": body.negative_prompt,
        "model": plan.model,
        "width": body.width,
        "height": body.height,
        "steps": body.steps,
        "guidance_scale": body.guidance_scale,
        "shift": 5.0,
        "seed": body.seed,
        "n": body.n,
        "randomized_batch": body.randomized_batch,
        "slg": True,
        "operation": "text-to-image",
        "parent_image_id": latest_image_id if plan.context_mode == "prompt-continuation" else None,
        "context_mode": plan.context_mode,
        "previous_prompt": latest_image_prompt if plan.context_mode == "prompt-continuation" else None,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "execution_plan": execution_plan,
    }
    with db.lock:
        job_id = str(uuid.uuid4())
        params["user_id"] = user_id
        if not db.add_job(job_id, plan.kind, params, settings.max_queue, user_id=user_id):
            # The earlier count is only an inexpensive fast-fail. The database
            # transaction is authoritative under concurrent requests. Remove
            # the just-created message and its assets if another request filled
            # the queue between those two checks.
            db.delete_message(message_id)
            raise HTTPException(429, "Generation queue is full")
    db.update_message(message_id, job_id=job_id)
    conversation = db.get_conversation(conversation_id, user_id)
    if conversation and conversation["title"] == "New conversation":
        db.update_conversation(conversation_id, title=body.content.strip()[:80], user_id=user_id)
    return {
        "job": {"id": job_id, "status": "queued"},
        "message": _message_json(db.get_message(message_id)),
        "plan": execution_plan,
    }


# ---------------------------------------------------------------------------
# Reference and upload routes
# ---------------------------------------------------------------------------
@app.post("/api/uploads")
async def upload_reference(
    request: Request,
    file: UploadFile = File(...),
    role: str = Form("reference"),
    note: str = Form(""),
    session=Depends(csrf_session),
):
    # Count/quota checks and the reference insert must cover the whole write
    # path. Without one process-local reservation lock, concurrent uploads can
    # all observe available capacity and exceed either configured limit.
    async with reference_mutation_lock:
        user_id = _user_id(session)
        if len(db.list_references(user_id)) >= settings.max_upload_count:
            raise HTTPException(413, "Reference image limit reached")
        if media_usage_bytes(settings.uploads_dir, settings.images_dir) >= settings.max_media_bytes:
            raise HTTPException(413, "Local media storage limit reached; delete unused images or raise MOSS_MAX_MEDIA_MB")
        saved = await save_upload(file, settings.uploads_dir, settings.max_upload_bytes, settings.max_pixels)
        saved_path = Path(saved["path"])
        if media_usage_bytes(settings.uploads_dir, settings.images_dir) > settings.max_media_bytes:
            try:
                contained_path(saved_path, settings.uploads_dir).unlink(missing_ok=True)
            except (KeyError, ValueError, OSError):
                logger.warning("media limit was exceeded but the just-uploaded file could not be removed")
            raise HTTPException(413, "Local media storage limit reached; delete unused images or raise MOSS_MAX_MEDIA_MB")
        values = {
            **saved,
            "path": media_key(saved_path, settings.data_dir),
            "role": role[:32],
            "note": note[:500],
            "created_at": utc_now(),
            "user_id": user_id,
        }
        try:
            db.add_reference(values)
        except Exception:
            try:
                contained_path(saved_path, settings.uploads_dir).unlink(missing_ok=True)
            except (KeyError, ValueError, OSError):
                logger.warning("reference insert failed and the uploaded file could not be removed")
            raise
        return {key: values[key] for key in ("id", "filename", "role", "note", "width", "height", "bytes", "created_at")}


@app.get("/api/references")
async def references(session=Depends(current_session)):
    return [row_json(row) for row in db.list_references(_user_id(session))]


@app.post("/api/references/order")
async def reorder_references(body: ReferenceOrderRequest, session=Depends(csrf_session)):
    if len(body.ids) != len(set(body.ids)):
        raise HTTPException(422, "Reference order contains duplicate IDs")
    if not db.reorder_references(body.ids, _user_id(session)):
        raise HTTPException(422, "Reference order must contain every current reference exactly once")
    return {"ok": True}


@app.get("/api/references/{reference_id}/file")
async def reference_file(reference_id: str, session=Depends(current_session)):
    row = db.get_reference(reference_id, _user_id(session))
    if not row:
        raise HTTPException(404, "Reference image not found")
    try:
        path = resolve_stored_path(row["path"], settings.data_dir, settings.uploads_dir, settings.images_dir)
    except ValueError as exc:
        logger.warning("rejecting unsafe reference path for %s: %s", reference_id, exc)
        raise HTTPException(404, "Reference image not found") from exc
    if not path.is_file():
        raise HTTPException(404, "Reference image not found")
    return FileResponse(path, media_type="image/png", filename=safe_display_filename(row["filename"]))


@app.delete("/api/references/{reference_id}")
async def delete_reference(reference_id: str, session=Depends(csrf_session)):
    user_id = _user_id(session)
    with db.lock:
        row = db.delete_reference(reference_id, user_id)
        if not row:
            raise HTTPException(404, "Reference image not found")
        try:
            # Promoted gallery references intentionally point into
            # images_dir; normal uploads point into uploads_dir. Both are
            # trusted application storage roots, and neither may escape them.
            path = resolve_stored_path(row["path"], settings.data_dir, settings.uploads_dir, settings.images_dir)
        except ValueError:
            path = None
        # Gallery outputs promoted to references intentionally share their
        # raster path. Keep the ownership check in the same critical section
        # as deletion so a concurrent promotion cannot race the unlink.
        if path and path.is_file() and not _reference_record_uses_path(path) and not _image_record_uses_path(path):
            path.unlink()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Queue and server-sent event routes
# ---------------------------------------------------------------------------
@app.post("/api/jobs")
async def create_job(body: JobRequest, session=Depends(csrf_session)):
    user_id = _user_id(session)
    if body.width * body.height > settings.max_pixels:
        raise HTTPException(422, f"Resolution exceeds the configured {settings.max_pixels:,}-pixel limit")
    if body.layout_json is not None:
        if body.layout_json["width"] != body.width or body.layout_json["height"] != body.height:
            raise HTTPException(422, "layout_json width and height must match the requested output dimensions")
    if body.reference_ids:
        raise HTTPException(
            422,
            "MOSS-Image does not support uploaded-image conditioning in this workflow; "
            "the references were not used. Remove the references and submit a text-to-image request.",
        )
    if db.queued_count() >= settings.max_queue:
        raise HTTPException(429, "Generation queue is full")
    await _require_moss_model(body.model or settings.moss_model)
    params = body.model_dump()
    params["original_prompt"] = body.original_prompt or body.prompt
    # The client accepts an omitted model as "use the configured MOSS
    # checkpoint". Persist that resolved value so gallery metadata and
    # retries identify the model that will actually run.
    params["model"] = body.model or settings.moss_model
    params["operation"] = "text-to-image"
    with db.lock:
        job_id = str(uuid.uuid4())
        params["user_id"] = user_id
        if not db.add_job(job_id, body.kind, params, settings.max_queue, user_id=user_id):
            raise HTTPException(429, "Generation queue is full")
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
async def jobs(session=Depends(current_session)):
    result = []
    for row in db.list_jobs(user_id=_user_id(session)):
        item = row_json(row)
        item["params"] = json.loads(item.pop("params_json"))
        item["progress"] = json.loads(item.pop("progress_json", "{}"))
        result.append(item)
    return result


@app.get("/api/events")
async def events(request: Request, session=Depends(current_session)):
    async def stream():
        while not await request.is_disconnected():
            snapshot = []
            for row in db.list_jobs(limit=20, user_id=_user_id(session)):
                item = row_json(row)
                item["params"] = json.loads(item.pop("params_json"))
                item["progress"] = json.loads(item.pop("progress_json", "{}"))
                snapshot.append(item)
            yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, session=Depends(csrf_session)):
    user_id = _user_id(session)
    job = db.get_job(job_id, user_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "queued":
        if job["status"] == "running":
            raise HTTPException(409, "Active MOSS requests cannot be interrupted by this backend")
        raise HTTPException(409, "This job is no longer cancellable")
    if not db.cancel_job(job_id, user_id):
        raise HTTPException(409, "Only queued jobs can be cancelled")
    try:
        params = json.loads(job["params_json"])
    except (TypeError, json.JSONDecodeError):
        params = {}
    if params.get("message_id"):
        db.update_message(params["message_id"], status="cancelled", error="Cancelled by user")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, session=Depends(csrf_session)):
    user_id = _user_id(session)
    if db.queued_count() >= settings.max_queue:
        raise HTTPException(429, "Generation queue is full")
    job = db.get_job(job_id, user_id)
    result = db.retry_job(job_id, settings.max_queue, user_id)
    if result == "full":
        raise HTTPException(429, "Generation queue is full")
    if result != "queued":
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    if job:
        try:
            params = json.loads(job["params_json"])
        except (TypeError, json.JSONDecodeError):
            params = {}
        if params.get("message_id"):
            db.update_message(params["message_id"], status="queued", error="")
    return {"ok": True, "status": "queued"}


# ---------------------------------------------------------------------------
# Gallery and image routes
# ---------------------------------------------------------------------------
@app.get("/api/images")
async def images(session=Depends(current_session)):
    result = []
    for row in db.list_images(user_id=_user_id(session)):
        item = row_json(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        item["favorite"] = bool(item.get("favorite", 0))
        item["url"] = f"/api/images/{item['id']}/file"
        result.append(item)
    return result


@app.patch("/api/images/{image_id}/favorite")
async def favorite_image(image_id: str, body: FavoriteRequest, session=Depends(csrf_session)):
    if not db.set_image_favorite(image_id, body.favorite, _user_id(session)):
        raise HTTPException(404, "Image not found")
    return {"id": image_id, "favorite": body.favorite}


@app.get("/api/images/{image_id}/metadata")
async def image_metadata(image_id: str, session=Depends(current_session)):
    row = db.get_image(image_id, _user_id(session))
    if not row:
        raise HTTPException(404, "Image not found")
    payload = {
        "id": row["id"],
        "kind": row["kind"],
        "prompt": row["prompt"],
        "favorite": bool(row["favorite"]),
        "created_at": row["created_at"],
        "metadata": json.loads(row["metadata_json"]),
    }
    download_name = safe_display_filename(f"moss-{image_id}.json")
    return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="{download_name}"'})


@app.delete("/api/images/{image_id}")
async def delete_image(image_id: str, session=Depends(csrf_session)):
    user_id = _user_id(session)
    with db.lock:
        row = db.get_image(image_id, user_id)
        if not row:
            raise HTTPException(404, "Image not found")
        if _active_job_uses_asset(image_id, ("parent_image_id",)):
            raise HTTPException(409, "This image is required by a queued or running job")
        try:
            path = resolve_stored_path(row["path"], settings.data_dir, settings.images_dir)
        except ValueError as exc:
            raise HTTPException(404, "Image not found") from exc
        removed = db.delete_image(image_id, user_id)
        # A path can be shared by a promoted reference or a legacy/duplicate
        # gallery record. Keep canonical ownership checks under the same
        # database lock as deletion so a concurrent promotion cannot race the
        # unlink.
        if removed and path.is_file() and not _reference_record_uses_path(path) and not _image_record_uses_path(path):
            path.unlink()
    return {"ok": True}


@app.get("/api/images/{image_id}/file")
async def image_file(image_id: str, session=Depends(current_session)):
    row = db.get_image(image_id, _user_id(session))
    if not row:
        raise HTTPException(404, "Image not found")
    try:
        path = resolve_stored_path(row["path"], settings.data_dir, settings.images_dir)
    except ValueError as exc:
        logger.warning("rejecting unsafe image path for %s", image_id)
        raise HTTPException(404, "Image not found") from exc
    if not path.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(path, media_type="image/png")


@app.post("/api/images/{image_id}/to-reference")
async def image_to_reference(image_id: str, session=Depends(csrf_session)):
    user_id = _user_id(session)
    async with reference_mutation_lock:
        # Promotion and deletion both hold the database lock, so a reference
        # can never be inserted after its gallery source has been removed.
        with db.lock:
            row = db.get_image(image_id, user_id)
            if not row:
                raise HTTPException(404, "Image not found")
            try:
                image_path = resolve_stored_path(row["path"], settings.data_dir, settings.images_dir)
            except ValueError as exc:
                logger.warning("rejecting unsafe image path for promotion %s", image_id)
                raise HTTPException(404, "Image not found") from exc
            if not image_path.is_file():
                raise HTTPException(404, "Image not found")
            if len(db.list_references(user_id)) >= settings.max_upload_count:
                raise HTTPException(413, "Reference image limit reached")
            try:
                from PIL import Image

                with Image.open(image_path) as image:
                    width, height = image.size
            except Exception as exc:
                raise HTTPException(422, "Stored output is not a readable image") from exc
            reference_id = str(uuid.uuid4())
            values = {
                "id": reference_id,
                "user_id": user_id,
                "filename": f"moss-output-{image_id}.png",
                "path": media_key(image_path, settings.data_dir),
                "role": "source",
                "note": "Promoted from gallery output",
                "width": width,
                "height": height,
                "bytes": image_path.stat().st_size,
                "created_at": utc_now(),
            }
            db.add_reference(values)
            return {key: values[key] for key in ("id", "filename", "role", "note", "width", "height", "bytes", "created_at")}


# ---------------------------------------------------------------------------
# Prompt assistance and runtime monitoring routes
# ---------------------------------------------------------------------------
@app.post("/api/prompt/enhance")
async def enhance_prompt(body: EnhanceRequest, session=Depends(csrf_session)):
    try:
        result = await chat.enhance(body.prompt, body.mode, width=body.width, height=body.height)
    except InferenceError as exc:
        raise HTTPException(503, str(exc)) from exc
    if body.mode == "layout":
        try:
            result = json.loads(result)
        except json.JSONDecodeError as exc:
            raise HTTPException(502, "Prompt backend returned invalid layout JSON") from exc
        if isinstance(result, dict):
            result.setdefault("width", body.width)
            result.setdefault("height", body.height)
    return {"result": result}


@app.get("/api/system")
async def system(session=Depends(current_session)):
    hardware = await asyncio.to_thread(detect_hardware, settings)
    return {
        "hardware": hardware,
        "vllm": await vllm.health(),
        "moss_only": True,
        "offline_mode": settings.offline_mode,
        "prompt_assistance": {
            "configured": bool(settings.vllm_chat_url and settings.vllm_chat_model),
            "router_enabled": settings.router_assistance,
        },
    }


@app.get("/api/models")
async def models(session=Depends(current_session)):
    health = await vllm.health()
    worker_model_ids = {item.get("id") for item in health.get("models", []) if isinstance(item, dict)}
    moss_models = [
        {
            "id": model_id,
            "backend": "vllm-omni",
            "capabilities": ["text-to-image", "layout"],
            "active": bool(health.get("ok")) and model_id == settings.moss_model,
            "installed": model_id in worker_model_ids,
            "downloadable": False,
            "status": "Managed by the local vLLM worker",
            "license": "Apache-2.0 (review model card)",
            "hardware_notes": "MOSS-Image runs on the local vLLM worker; the web app delegates model loading to that local process.",
            "size_bytes": None,
            "size_note": "Managed by the local worker; the web host does not measure its weights",
        }
        for model_id in ("OpenMOSS-Team/MOSS-Image-Base", "OpenMOSS-Team/MOSS-Image-SFT")
    ]
    return {"models": moss_models, "vllm_models": health.get("models", [])}

if settings.static_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
