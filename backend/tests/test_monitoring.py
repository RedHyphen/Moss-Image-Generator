from dataclasses import replace
import asyncio
from contextlib import nullcontext
import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import json

import pytest
from PIL import Image

from app import main
from app import monitoring


def test_nvidia_telemetry_is_parsed_into_resource_metrics(monkeypatch):
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(
        monitoring.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="RTX 4060, 555.1, 8192, 1024, 37, 8.9, 61\n",
            stderr="",
        ),
    )

    gpu = monitoring.detect_hardware(main.settings)["gpu"]

    assert monitoring.detect_hardware(main.settings)["scope"] == "web-runtime"
    assert gpu["available"] is True
    assert gpu["name"] == "RTX 4060"
    assert gpu["memory_total_bytes"] == 8192 * 1024 * 1024
    assert gpu["memory_used_bytes"] == 1024 * 1024 * 1024
    assert gpu["utilization_percent"] == 37.0
    assert gpu["compute_capability"] == "8.9"
    assert gpu["temperature_c"] == 61.0


def test_torch_runtime_probe_reports_cuda_compatibility(monkeypatch):
    monitoring.runtime_probe_cache = None
    payload = {
        "torch_version": "2.9.0",
        "torch_cuda_version": "12.8",
        "torch_cuda_available": True,
        "torch_device_count": 2,
        "torch_devices": [{"index": 0, "name": "RTX 4060", "capability": "8.9"}],
    }
    monkeypatch.setattr(
        monitoring.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    runtime = main.detect_runtime_compatibility()
    assert runtime["torch_cuda_available"] is True
    assert runtime["torch_cuda_version"] == "12.8"
    assert runtime["torch_devices"][0]["capability"] == "8.9"
    monitoring.runtime_probe_cache = None


def test_deleting_promoted_reference_preserves_gallery_output(monkeypatch, tmp_path):
    settings = replace(main.settings, data_dir=tmp_path)
    monkeypatch.setattr(main, "settings", settings)
    target = settings.images_dir / "promoted-output.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png placeholder")

    class FakeDB:
        lock = nullcontext()

        def get_reference(self, _reference_id, _user_id=None):
            return {"id": "reference", "path": str(target), "filename": "promoted.png"}

        def delete_reference(self, _reference_id, _user_id=None):
            return {"id": "reference", "path": str(target), "filename": "promoted.png"}

        def list_references(self, _user_id=None):
            return []

        def list_images(self, limit=100, _user_id=None):
            return [{"path": str(target)}]

        def list_jobs(self, limit=50, _user_id=None):
            return []

    monkeypatch.setattr(main, "db", FakeDB())
    asyncio.run(main.delete_reference("reference", {"user_id": 1}))

    assert target.exists(), "deleting a promoted reference must not remove its gallery output"


def test_deleting_orphaned_promoted_reference_removes_raster(monkeypatch, tmp_path):
    settings = replace(main.settings, data_dir=tmp_path)
    monkeypatch.setattr(main, "settings", settings)
    target = settings.images_dir / "orphaned-promoted-output.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png placeholder")

    class FakeDB:
        lock = nullcontext()

        def get_reference(self, _reference_id, _user_id=None):
            return {"id": "reference", "path": str(target), "filename": "promoted.png"}

        def delete_reference(self, _reference_id, _user_id=None):
            return {"id": "reference", "path": str(target), "filename": "promoted.png"}

        def list_references(self, _user_id=None):
            return []

        def list_images(self, limit=100, _user_id=None):
            return []

        def list_jobs(self, limit=50, _user_id=None):
            return []

    monkeypatch.setattr(main, "db", FakeDB())
    asyncio.run(main.delete_reference("reference", {"user_id": 1}))

    assert not target.exists(), "an orphaned promoted raster should be cleaned after its last owner is deleted"


def test_deleting_one_shared_gallery_record_preserves_raster(monkeypatch, tmp_path):
    settings = replace(main.settings, data_dir=tmp_path)
    monkeypatch.setattr(main, "settings", settings)
    target = settings.images_dir / "shared-output.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png placeholder")

    class FakeDB:
        lock = nullcontext()

        def get_image(self, image_id, _user_id=None):
            return {"id": image_id, "path": str(target)}

        def delete_image(self, _image_id, _user_id=None):
            return {"id": "deleted", "path": str(target)}

        def list_images(self, limit=100, _user_id=None):
            return [{"id": "remaining", "path": str(target)}]

        def list_references(self, _user_id=None):
            return []

        def list_jobs(self, limit=50, _user_id=None):
            return []

    monkeypatch.setattr(main, "db", FakeDB())
    asyncio.run(main.delete_image("deleted", {"user_id": 1}))

    assert target.exists(), "deleting one shared gallery record must not remove the remaining raster"


def test_gallery_path_ownership_scan_is_unbounded(monkeypatch, tmp_path):
    calls = []

    class FakeDB:
        def list_images(self, **kwargs):
            calls.append(kwargs)
            return []

    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "settings", replace(main.settings, data_dir=tmp_path))

    assert not main._image_record_uses_path(tmp_path / "not-present.png")
    assert calls == [{"limit": None}]


def test_gallery_promotion_uses_database_lock_for_source_lookup(monkeypatch):
    source = (Path(__file__).parents[2] / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    marker = "async def image_to_reference"
    start = source.index(marker)
    body = source[start:source.index("@app.post(\"/api/prompt/enhance\")", start)]
    assert "async with reference_mutation_lock" in body
    assert "with db.lock:" in body


def test_upload_rejects_media_storage_overage_and_removes_new_file(monkeypatch, tmp_path):
    settings = replace(main.settings, data_dir=tmp_path, max_media_bytes=1)
    monkeypatch.setattr(main, "settings", settings)
    settings.ensure_dirs()

    class FakeDB:
        def list_references(self, _user_id=None):
            return []

        def add_reference(self, _values):
            raise AssertionError("over-limit uploads must not be persisted")

    class FakeUpload:
        filename = "source.png"

        def __init__(self):
            payload = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(payload, format="PNG")
            self.payload = payload.getvalue()

        async def read(self, _limit):
            return self.payload

    monkeypatch.setattr(main, "db", FakeDB())
    with pytest.raises(main.HTTPException) as exc_info:
        asyncio.run(main.upload_reference(None, FakeUpload(), "source", "", {"user_id": 1}))
    assert exc_info.value.status_code == 413
    assert not list(settings.uploads_dir.glob("*"))


def test_concurrent_uploads_respect_reference_count_limit(monkeypatch, tmp_path):
    settings = replace(main.settings, data_dir=tmp_path, max_upload_count=1, max_media_bytes=10_000)
    monkeypatch.setattr(main, "settings", settings)
    settings.ensure_dirs()

    class FakeDB:
        def __init__(self):
            self.references = []

        def list_references(self, _user_id=None):
            return list(self.references)

        def add_reference(self, values):
            self.references.append(values)

    class FakeUpload:
        filename = "source.png"

        async def read(self, _limit):
            payload = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(payload, format="PNG")
            return payload.getvalue()

    database = FakeDB()
    monkeypatch.setattr(main, "db", database)

    async def run():
        return await asyncio.gather(
            main.upload_reference(None, FakeUpload(), "source", "", {"user_id": 1}),
            main.upload_reference(None, FakeUpload(), "source", "", {"user_id": 1}),
            return_exceptions=True,
        )

    results = asyncio.run(run())
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, main.HTTPException)]
    assert len(failures) == 1
    assert failures[0].status_code == 413
    assert len(database.references) == 1
    assert len(list(settings.uploads_dir.glob("*.png"))) == 1


def test_worker_health_script_passes_bearer_key_without_printing_it(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl-args.txt"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        r'''#!/usr/bin/env bash
printf '%s\n' "$@" >> "$CURL_LOG"
case "$*" in
  */health) printf '%s' '{}';;
  */v1/models) printf '%s' '{"data":[{"id":"OpenMOSS-Team/MOSS-Image-SFT"}]}';;
esac
''',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CURL_LOG": str(curl_log),
        "VLLM_API_KEY": "test-secret-token",
        "MOSS_VLLM_URL": "http://worker.internal:8000",
        "MOSS_LOCAL_ONLY": "false",
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "check_vllm_worker.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Authorization: Bearer test-secret-token" in curl_log.read_text(encoding="utf-8")
    assert "test-secret-token" not in result.stdout


def test_worker_health_script_rejects_unserved_moss_checkpoint(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        r'''#!/usr/bin/env bash
case "$*" in
  */health) printf '%s' '{}';;
  */v1/models) printf '%s' '{"data":[{"id":"OpenMOSS-Team/MOSS-Image-SFT"}]}';;
esac
''',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "check_vllm_worker.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "MOSS_VLLM_URL": "http://worker.internal:8000",
            "MOSS_MODEL": "OpenMOSS-Team/MOSS-Image-Base",
            "MOSS_LOCAL_ONLY": "false",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "configured MOSS model is not served" in result.stderr


def test_live_moss_smoke_validates_official_dimension_bounds_before_request():
    source = (Path(__file__).parents[2] / "scripts" / "smoke_live_moss.sh").read_text(encoding="utf-8")
    assert "WIDTH < 512" in source
    assert "WIDTH > 2048" in source
    assert "WIDTH % 16 != 0" in source
    assert "MOSS_LIVE_SIZE must be between 512x512" in source


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
    ],
)
def test_inference_url_validator_accepts_local_targets(url):
    script = Path(__file__).parents[2] / "scripts" / "validate_inference_url.py"
    result = subprocess.run(
        [sys.executable, str(script), url, "false", "false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "inference_target=allowed" in result.stdout


def test_inference_url_validator_requires_explicit_external_opt_in():
    script = Path(__file__).parents[2] / "scripts" / "validate_inference_url.py"
    rejected = subprocess.run(
        [sys.executable, str(script), "https://8.8.8.8:443", "false", "false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 2
    assert "outside the local network" in rejected.stderr

    allowed = subprocess.run(
        [sys.executable, str(script), "https://8.8.8.8:443", "true", "false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert allowed.returncode == 0


def test_inference_url_validator_never_accepts_embedded_credentials():
    script = Path(__file__).parents[2] / "scripts" / "validate_inference_url.py"
    result = subprocess.run(
        [sys.executable, str(script), "https://user:secret@8.8.8.8:443", "true"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "must not embed credentials" in result.stderr


def test_local_hardware_diagnostic_reports_ai_python_packages():
    source = (Path(__file__).parents[2] / "scripts" / "check_hardware.sh").read_text(encoding="utf-8")
    assert '"diffusers"' not in source
    assert '"transformers"' in source
    assert '"vllm"' in source


def test_env_example_documents_two_gpu_selection_unambiguously():
    source = (Path(__file__).parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "CUDA_VISIBLE_DEVICES=0,1" in source
    assert "VLLM_TENSOR_PARALLEL_SIZE=2" in source
    assert "CUDA_VISIBLE_DEVICES=0,1 and 2" not in source


def test_conversation_search_owns_initial_list_load_and_does_not_duplicate_creation():
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    assert "const suffix = query.trim()" in source
    assert "!items.length && session && !query.trim()" in source
    assert 'refresh();\n    loadConversationList();\n    const events' not in source
    assert 'setConversationSearch(""); setConversations((old) => [created, ...old])' in source


def test_random_generation_is_opt_in_and_fixed_seed_is_preserved():
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    assert 'const [randomSeed, setRandomSeed] = useState(false);' in source
    assert 'const outputSeed = randomizedBatch || randomSeed ? null : numericPayload(seed);' in source
    assert 'const outputCount = numericPayload(count);' in source
    assert 'checked={randomizedBatch || randomSeed}' in source
    assert 'setWidth(Number(' not in source
    assert 'min="512"' not in source
    assert 'max="2048"' not in source


def test_frontend_formats_structured_api_validation_errors():
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    assert "function formatApiError(detail: unknown, fallback: string)" in source
    assert "return messages.join(\"; \");" in source
    assert "formatApiError(body.detail" in source


def test_moss_launcher_rejects_unauthenticated_worker_by_default():
    environment = {key: value for key, value in os.environ.items() if key != "VLLM_API_KEY"}
    environment.update(
        {
            "MOSS_ALLOW_UNAUTHENTICATED_VLLM": "false",
            "VLLM_MODE": "native",
        }
    )
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "unauthenticated vLLM" in result.stderr


def test_moss_launcher_rejects_public_bind_address():
    environment = {
        **os.environ,
        "MOSS_ALLOW_UNAUTHENTICATED_VLLM": "false",
        "VLLM_API_KEY": "test-secret-token",
        "VLLM_MODE": "native",
        "VLLM_HOST": "8.8.8.8",
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "must resolve only to loopback/private/link-local" in result.stderr


def test_moss_launcher_rejects_untrusted_docker_image_by_default():
    environment = {
        **os.environ,
        "MOSS_ALLOW_UNAUTHENTICATED_VLLM": "false",
        "VLLM_API_KEY": "test-secret-token",
        "VLLM_MODE": "docker",
        "VLLM_IMAGE": "registry.example.invalid/unreviewed:v1",
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "untrusted VLLM_IMAGE" in result.stderr


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("VLLM_PORT", "65536", "VLLM_PORT must be between 1 and 65535"),
        ("VLLM_MODE", "typo", "VLLM_MODE must be native or docker"),
    ],
)
def test_moss_launcher_rejects_invalid_service_configuration(variable, value, message):
    environment = {
        **os.environ,
        "MOSS_ALLOW_UNAUTHENTICATED_VLLM": "false",
        "VLLM_API_KEY": "test-secret-token",
        "VLLM_MODE": "native",
        variable: value,
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert message in result.stderr


def test_moss_launcher_uses_supported_cpu_offload_option():
    launcher = Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh"
    source = launcher.read_text(encoding="utf-8")
    assert "--enable-cpu-offload" in source
    assert "--cpu-offload-gb" not in source


def test_moss_launcher_requires_explicit_loopback_review_for_unauthenticated_worker(tmp_path):
    environment = {
        **os.environ,
        "VLLM_MODE": "native",
        "VLLM_HOST": "127.0.0.1",
        "MOSS_ALLOW_UNAUTHENTICATED_VLLM": "false",
        "MOSS_ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM": "false",
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "clients must use the authenticated web app" in result.stderr


def test_moss_launcher_passes_verified_diffusion_layer_offload_config(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_file = tmp_path / "vllm-args.txt"
    (fake_bin / "nvidia-smi").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' '0, 16384, 8.9'\n",
        encoding="utf-8",
    )
    (fake_bin / "vllm").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$FAKE_VLLM_ARGS\"\n",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    for item in fake_bin.iterdir():
        item.chmod(0o755)
    config = '{"mode":"layer","components":["dit","text_encoder"],"layer_options":{"dit":{"weight_transfer":"rank-local"},"text_encoder":{"weight_transfer":"rank-local"}}}'
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_VLLM_ARGS": str(args_file),
        "VLLM_MODE": "native",
        "VLLM_API_KEY": "test-secret-token",
        "MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG": config,
        "VLLM_HOST": "127.0.0.1",
    }
    subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "--diffusion-offload-config" in args
    assert args[args.index("--diffusion-offload-config") + 1] == config


def test_moss_launcher_passes_cpu_offload_amount_to_native_vllm(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_file = tmp_path / "vllm-args.txt"
    (fake_bin / "nvidia-smi").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' '0, 8192, 8.9'\n",
        encoding="utf-8",
    )
    (fake_bin / "vllm").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$FAKE_VLLM_ARGS\"\n",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    for item in fake_bin.iterdir():
        item.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_VLLM_ARGS": str(args_file),
        "VLLM_MODE": "native",
        "VLLM_API_KEY": "test-secret-token",
        "MOSS_VLLM_CPU_OFFLOAD": "true",
        "CUDA_VISIBLE_DEVICES": "0",
        "VLLM_TENSOR_PARALLEL_SIZE": "1",
        "VLLM_HOST": "127.0.0.1",
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "--enable-cpu-offload" in args
    assert "--cpu-offload-gb" not in args
    assert "--api-key" not in args
    assert result.returncode == 0


def test_compose_worker_keeps_api_key_out_of_command_arguments():
    compose = (Path(__file__).parents[2] / "compose.yaml").read_text(encoding="utf-8")
    assert "--api-key" not in compose
    assert 'entrypoint: ["/opt/moss/vllm-entrypoint.sh"]' in compose
    assert "file: ${VLLM_API_KEY_FILE:-./.env.docker}" in compose

    entrypoint = (Path(__file__).parents[2] / "docker" / "vllm-entrypoint.sh").read_text(encoding="utf-8")
    assert "run/secrets/vllm_api_key" in entrypoint
    assert 'exec "$@"' in entrypoint


def test_moss_launcher_defaults_8gb_turing_to_fp16_layerwise_offload(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_file = tmp_path / "vllm-args.txt"
    (fake_bin / "nvidia-smi").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' '0, 8192, 7.5'\n",
        encoding="utf-8",
    )
    (fake_bin / "vllm").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$FAKE_VLLM_ARGS\"\n",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    for item in fake_bin.iterdir():
        item.chmod(0o755)
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_VLLM_ARGS": str(args_file),
            "VLLM_MODE": "native",
            "VLLM_API_KEY": "test-secret-token",
            "VLLM_HOST": "127.0.0.1",
            "MOSS_GPU_PROFILE": "8gb-turing",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--dtype") + 1] == "float16"
    offload = args[args.index("--diffusion-offload-config") + 1]
    assert '"mode":"layer"' in offload
    assert "8GB Turing local profile" in result.stderr


def test_moss_launcher_serves_an_explicit_local_checkpoint_in_native_mode(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    model_dir = tmp_path / "MOSS-Image-SFT"
    model_dir.mkdir()
    (model_dir / "model_index.json").write_text("{}", encoding="utf-8")
    args_file = tmp_path / "vllm-args.txt"
    (fake_bin / "nvidia-smi").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' '0, 16384, 8.9'\n",
        encoding="utf-8",
    )
    (fake_bin / "vllm").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$FAKE_VLLM_ARGS\"\n",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    for item in fake_bin.iterdir():
        item.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_VLLM_ARGS": str(args_file),
        "VLLM_MODE": "native",
        "VLLM_API_KEY": "test-secret-token",
        "MOSS_MODEL_PATH": str(model_dir),
        "VLLM_HOST": "127.0.0.1",
    }
    subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts" / "start_vllm_omni.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("serve") + 1] == str(model_dir)
    assert args[args.index("--served-model-name") + 1] == "OpenMOSS-Team/MOSS-Image-SFT"


def test_lan_boundary_diagnostic_checks_loopback_and_rejects_unallowlisted_wide_bind():
    script = Path(__file__).parents[2] / "scripts" / "check_lan_boundary.sh"
    environment = {**os.environ, "MOSS_HOST": "127.0.0.1", "MOSS_PORT": "8000", "MOSS_TRUSTED_SUBNETS": ""}
    safe = subprocess.run([str(script)], env=environment, capture_output=True, text=True, timeout=10)
    assert safe.returncode == 0
    assert "bind_scope: loopback-only" in safe.stdout

    environment["MOSS_HOST"] = "192.168.1.20"
    environment["MOSS_TRUSTED_SUBNETS"] = "192.168.1.0/24"
    private_network = subprocess.run([str(script)], env=environment, capture_output=True, text=True, timeout=10)
    assert private_network.returncode == 0
    assert "bind_scope: private or link-local network" in private_network.stdout

    environment["MOSS_HOST"] = "0.0.0.0"
    environment["MOSS_TRUSTED_SUBNETS"] = ""
    unsafe = subprocess.run([str(script)], env=environment, capture_output=True, text=True, timeout=10)
    assert unsafe.returncode == 2
    assert "wide bind has no trusted subnet allowlist" in unsafe.stdout

    environment["MOSS_HOST"] = "127.0.0.1"
    environment["MOSS_PORT"] = "65536"
    invalid_port = subprocess.run([str(script)], env=environment, capture_output=True, text=True, timeout=10)
    assert invalid_port.returncode == 2
    assert "port must be between 1 and 65535" in invalid_port.stdout


def test_start_requires_a_complete_frontend_build():
    script = Path(__file__).parents[2] / "scripts" / "start.sh"
    source = script.read_text(encoding="utf-8")
    assert 'STATIC_DIR="${MOSS_STATIC_DIR:-$ROOT_DIR/frontend/dist}"' in source
    assert '[[ ! -f "$STATIC_DIR/index.html" ]]' in source
    assert "Configured MOSS_STATIC_DIR has no index.html" in source
    assert "[[ -d frontend/dist ]]" not in source


def test_stop_targets_only_this_checkout():
    script = Path(__file__).parents[2] / "scripts" / "stop.sh"
    source = script.read_text(encoding="utf-8")
    assert "CANDIDATE_PIDS" in source
    assert 'readlink -f "/proc/${pid}/cwd"' in source
    assert '[[ "$process_cwd" == "$ROOT_DIR" ]] || continue' in source
    assert "fuser -k" not in source
