from __future__ import annotations

import os
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .resolution import resolve_resolution_budget


def _is_private_network(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    return network.is_private or network.is_link_local


def _is_loopback_private_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return address.is_loopback or address.is_private or address.is_link_local


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _port(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a numeric TCP port") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


def _inference_url(name: str, default: str, *, allow_external: bool, loopback_only: bool) -> str:
    """Validate an inference endpoint before any client can contact it.

    Local operation is the default. Numeric loopback/private/link-local targets
    and conventional local-only hostnames are accepted; public targets require
    the explicit MOSS_ALLOW_EXTERNAL_INFERENCE opt-in.
    """
    value = (os.getenv(name) or default).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an HTTP(S) URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} must not embed credentials in the URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{name} contains an invalid port")

    hostname = parsed.hostname.rstrip(".").lower()
    local_target = hostname in {"localhost", "localhost6"} or hostname.endswith((".local", ".lan", ".internal"))
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        local_target = (
            not address.is_unspecified
            and not address.is_multicast
            and (address.is_loopback or address.is_private or address.is_link_local)
        )
    else:
        # A single-label name is a local DNS/hosts alias;
        # arbitrary dotted names need the explicit external-inference opt-in.
        local_target = local_target or "." not in hostname

    loopback_target = hostname in {"localhost", "localhost6"} or (
        address is not None and address.is_loopback
    )
    if loopback_only and not loopback_target:
        raise ValueError(f"{name} must point to loopback in MOSS_LOCAL_ONLY mode")
    if not local_target and not allow_external:
        raise ValueError(
            f"{name} points outside the local network ({hostname}); set "
            "MOSS_ALLOW_EXTERNAL_INFERENCE=true only for an intentional external integration"
        )
    return value


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    host: str
    port: int
    trusted_subnets: tuple[str, ...]
    session_days: int
    cookie_secure: bool
    max_upload_bytes: int
    max_upload_count: int
    max_media_bytes: int
    max_pixels: int
    max_pixels_source: str
    vram_total_bytes: int | None
    vram_budget_bytes: int | None
    vram_free_bytes: int | None
    max_queue: int
    inference_timeout_seconds: int
    moss_vllm_url: str
    moss_model: str
    vllm_api_key: str | None
    vllm_chat_url: str | None
    vllm_chat_model: str | None
    allow_external_inference: bool
    local_only: bool
    offline_mode: bool
    router_assistance: bool
    model_storage_dir: Path
    static_dir: Path
    moss_only: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parents[2]
        data_dir = Path(os.getenv("MOSS_DATA_DIR", str(root / "data"))).expanduser()
        trusted_values = tuple(x.strip() for x in os.getenv("MOSS_TRUSTED_SUBNETS", "").split(",") if x.strip())
        trusted = []
        for value in trusted_values:
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"MOSS_TRUSTED_SUBNETS contains an invalid network: {value}") from exc
            if not _is_private_network(network):
                raise ValueError(
                    "MOSS_TRUSTED_SUBNETS must contain private or link-local networks: "
                    f"{value}"
                )
            trusted.append(str(network))
        host = os.getenv("MOSS_HOST", "127.0.0.1").strip()
        if host in {"0.0.0.0", "::"} and not trusted:
            raise ValueError("MOSS_HOST wide binding requires an explicit private/link-local MOSS_TRUSTED_SUBNETS allowlist")
        try:
            host_address = ipaddress.ip_address(host)
        except ValueError:
            host_address = None
        if host_address is None and host.lower() != "localhost":
            raise ValueError("MOSS_HOST must be localhost or a numeric loopback/private/link-local address")
        if host_address is not None and not _is_loopback_private_address(host_address):
            raise ValueError(f"MOSS_HOST must be loopback, private, or link-local: {host}")
        allow_external_inference = _bool("MOSS_ALLOW_EXTERNAL_INFERENCE", False)
        local_only = _bool("MOSS_LOCAL_ONLY", True)
        if local_only and allow_external_inference:
            raise ValueError("MOSS_LOCAL_ONLY cannot be combined with MOSS_ALLOW_EXTERNAL_INFERENCE=true")
        offline_mode = _bool("MOSS_OFFLINE_MODE", False)
        if offline_mode and allow_external_inference:
            raise ValueError("MOSS_OFFLINE_MODE cannot be combined with MOSS_ALLOW_EXTERNAL_INFERENCE=true")
        moss_vllm_url = _inference_url(
            "MOSS_VLLM_URL",
            "http://127.0.0.1:8001",
            allow_external=allow_external_inference,
            loopback_only=local_only,
        )
        vllm_chat_url_value = (os.getenv("VLLM_CHAT_URL") or "").strip()
        vllm_chat_url = (
            _inference_url(
                "VLLM_CHAT_URL",
                vllm_chat_url_value,
                allow_external=allow_external_inference,
                loopback_only=local_only,
            )
            if vllm_chat_url_value
            else None
        )
        resolution_budget = resolve_resolution_budget()
        return cls(
            root=root,
            data_dir=data_dir,
            host=host,
            port=_port("MOSS_PORT", 8000),
            trusted_subnets=tuple(trusted),
            session_days=_int("MOSS_SESSION_DAYS", 14, minimum=1),
            cookie_secure=_bool("MOSS_COOKIE_SECURE", False),
            max_upload_bytes=_int("MOSS_MAX_UPLOAD_MB", 25, minimum=1) * 1024 * 1024,
            max_upload_count=_int("MOSS_MAX_UPLOAD_COUNT", 8, minimum=1),
            max_media_bytes=_int("MOSS_MAX_MEDIA_MB", 50_000, minimum=1) * 1024 * 1024,
            max_pixels=resolution_budget.max_pixels,
            max_pixels_source=resolution_budget.source,
            vram_total_bytes=resolution_budget.vram_total_bytes,
            vram_budget_bytes=resolution_budget.vram_budget_bytes,
            vram_free_bytes=resolution_budget.vram_free_bytes,
            max_queue=_int("MOSS_MAX_QUEUE", 1, minimum=1),
            inference_timeout_seconds=max(60, _int("MOSS_INFERENCE_TIMEOUT_SECONDS", 3600, minimum=1)),
            moss_vllm_url=moss_vllm_url,
            moss_model=os.getenv("MOSS_MODEL", "OpenMOSS-Team/MOSS-Image-SFT"),
            vllm_api_key=os.getenv("VLLM_API_KEY") or None,
            vllm_chat_url=vllm_chat_url,
            vllm_chat_model=os.getenv("VLLM_CHAT_MODEL") or None,
            allow_external_inference=allow_external_inference,
            local_only=local_only,
            offline_mode=offline_mode,
            router_assistance=_bool("MOSS_ROUTER_ASSISTANCE", False),
            model_storage_dir=Path(os.getenv("MOSS_MODELS_DIR", str(data_dir / "models"))).expanduser(),
            static_dir=Path(os.getenv("MOSS_STATIC_DIR", str(root / "frontend" / "dist"))).expanduser(),
            moss_only=_bool("MOSS_ONLY_MODE", True),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "moss.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def models_dir(self) -> Path:
        return self.model_storage_dir

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.images_dir, self.models_dir):
            path.mkdir(parents=True, exist_ok=True)
