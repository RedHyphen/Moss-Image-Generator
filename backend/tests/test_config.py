import pytest

from app import resolution
from app.config import Settings


def test_trusted_subnets_are_normalized_and_private(monkeypatch, tmp_path):
    monkeypatch.setenv("MOSS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOSS_TRUSTED_SUBNETS", "192.168.1.0/24, fd00::/8")
    settings = Settings.from_env()
    assert settings.trusted_subnets == ("192.168.1.0/24", "fd00::/8")


def test_private_network_bind_is_allowed(monkeypatch):
    monkeypatch.setenv("MOSS_HOST", "192.168.1.20")
    monkeypatch.setenv("MOSS_TRUSTED_SUBNETS", "192.168.1.0/24")
    settings = Settings.from_env()
    assert settings.host == "192.168.1.20"
    assert settings.trusted_subnets == ("192.168.1.0/24",)


def test_model_storage_directory_is_configurable(monkeypatch, tmp_path):
    model_dir = tmp_path / "model-cache"
    monkeypatch.setenv("MOSS_MODELS_DIR", str(model_dir))
    assert Settings.from_env().models_dir == model_dir


def test_public_trusted_subnet_is_rejected(monkeypatch):
    monkeypatch.setenv("MOSS_TRUSTED_SUBNETS", "8.8.8.0/24")
    with pytest.raises(ValueError, match="private or link-local"):
        Settings.from_env()


def test_wide_bind_requires_an_explicit_trusted_subnet(monkeypatch):
    monkeypatch.setenv("MOSS_HOST", "0.0.0.0")
    monkeypatch.delenv("MOSS_TRUSTED_SUBNETS", raising=False)
    with pytest.raises(ValueError, match="wide binding"):
        Settings.from_env()


def test_public_bind_address_is_rejected(monkeypatch):
    monkeypatch.setenv("MOSS_HOST", "8.8.8.8")
    monkeypatch.setenv("MOSS_TRUSTED_SUBNETS", "192.168.1.0/24")
    with pytest.raises(ValueError, match="loopback, private, or link-local"):
        Settings.from_env()


def test_arbitrary_bind_hostname_is_rejected(monkeypatch):
    monkeypatch.setenv("MOSS_HOST", "public.example.test")
    monkeypatch.setenv("MOSS_TRUSTED_SUBNETS", "192.168.1.0/24")
    with pytest.raises(ValueError, match="localhost or a numeric"):
        Settings.from_env()


def test_localhost_bind_hostname_remains_allowed(monkeypatch):
    monkeypatch.setenv("MOSS_HOST", "localhost")
    monkeypatch.delenv("MOSS_TRUSTED_SUBNETS", raising=False)
    assert Settings.from_env().host == "localhost"


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_invalid_web_port_is_rejected(monkeypatch, port):
    monkeypatch.setenv("MOSS_PORT", port)
    with pytest.raises(ValueError, match="TCP port|between 1"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("MOSS_MAX_QUEUE", "0", "MOSS_MAX_QUEUE must be at least 1"),
        ("MOSS_MAX_PIXELS", "not-a-number", "MOSS_MAX_PIXELS must be an integer or auto"),
        ("MOSS_MAX_UPLOAD_MB", "-1", "MOSS_MAX_UPLOAD_MB must be at least 1"),
        ("MOSS_MAX_MEDIA_MB", "0", "MOSS_MAX_MEDIA_MB must be at least 1"),
    ],
)
def test_invalid_resource_limits_fail_fast(monkeypatch, variable, value, message):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_inference_urls_default_to_local_targets(monkeypatch):
    monkeypatch.delenv("MOSS_VLLM_URL", raising=False)
    monkeypatch.delenv("VLLM_CHAT_URL", raising=False)
    monkeypatch.delenv("MOSS_ALLOW_EXTERNAL_INFERENCE", raising=False)
    settings = Settings.from_env()
    assert settings.moss_vllm_url == "http://127.0.0.1:8001"
    assert settings.vllm_chat_url is None
    assert settings.allow_external_inference is False
    assert settings.local_only is True


def test_auto_resolution_uses_eighty_percent_vram_and_standard_rounding(monkeypatch):
    monkeypatch.setenv("MOSS_MAX_PIXELS", "auto")
    monkeypatch.setattr(resolution, "query_total_vram_bytes", lambda: 16 * 1024**3)
    monkeypatch.setattr(resolution, "query_used_vram_bytes", lambda: None)

    settings = Settings.from_env()

    assert settings.max_pixels == 1024 * 1024
    assert settings.max_pixels_source == "auto"
    assert settings.vram_total_bytes == 16 * 1024**3
    assert settings.vram_budget_bytes == int(16 * 1024**3 * 0.8)
    assert settings.vram_free_bytes is None


def test_auto_resolution_accounts_for_currently_used_vram(monkeypatch):
    monkeypatch.setenv("MOSS_MAX_PIXELS", "auto")
    total = 16 * 1024**3
    used = 4 * 1024**3
    monkeypatch.setattr(resolution, "query_total_vram_bytes", lambda: total)
    monkeypatch.setattr(resolution, "query_used_vram_bytes", lambda: used)

    settings = Settings.from_env()

    assert settings.vram_free_bytes == total - used
    assert settings.vram_budget_bytes == int(total * 0.8)
    assert settings.max_pixels == 1024 * 1024


def test_auto_resolution_falls_back_to_conservative_baseline_without_gpu(monkeypatch):
    monkeypatch.setenv("MOSS_MAX_PIXELS", "auto")
    monkeypatch.setattr(resolution, "query_total_vram_bytes", lambda: None)

    settings = Settings.from_env()

    assert settings.max_pixels == resolution.BASELINE_MAX_PIXELS
    assert settings.max_pixels_source == "auto-fallback"
    assert settings.vram_free_bytes is None


def test_local_only_rejects_private_remote_worker(monkeypatch):
    monkeypatch.setenv("MOSS_VLLM_URL", "http://192.168.1.20:8001")
    with pytest.raises(ValueError, match="must point to loopback"):
        Settings.from_env()


@pytest.mark.parametrize("url", ["http://8.8.8.8:8000", "https://example.com/v1"])
def test_public_inference_urls_are_rejected_by_default(monkeypatch, url):
    monkeypatch.setenv("MOSS_VLLM_URL", url)
    monkeypatch.delenv("MOSS_ALLOW_EXTERNAL_INFERENCE", raising=False)
    with pytest.raises(ValueError, match="outside the local network|must point to loopback"):
        Settings.from_env()


def test_external_inference_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("MOSS_VLLM_URL", "https://example.com/v1")
    monkeypatch.setenv("VLLM_CHAT_URL", "https://router.example.com")
    monkeypatch.setenv("MOSS_ALLOW_EXTERNAL_INFERENCE", "true")
    monkeypatch.setenv("MOSS_LOCAL_ONLY", "false")
    settings = Settings.from_env()
    assert settings.moss_vllm_url == "https://example.com/v1"
    assert settings.vllm_chat_url == "https://router.example.com"
    assert settings.allow_external_inference is True


def test_offline_mode_is_supported_without_editing_configuration(monkeypatch):
    monkeypatch.setenv("MOSS_OFFLINE_MODE", "true")
    settings = Settings.from_env()
    assert settings.offline_mode is True


def test_offline_mode_rejects_external_inference(monkeypatch):
    monkeypatch.setenv("MOSS_OFFLINE_MODE", "true")
    monkeypatch.setenv("MOSS_ALLOW_EXTERNAL_INFERENCE", "true")
    with pytest.raises(ValueError, match="cannot be combined"):
        Settings.from_env()


def test_inference_url_credentials_are_rejected(monkeypatch):
    monkeypatch.setenv("MOSS_VLLM_URL", "http://user:password@127.0.0.1:8000")
    with pytest.raises(ValueError, match="must not embed credentials"):
        Settings.from_env()
