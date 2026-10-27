import base64
import io

import pytest
from PIL import Image

from app import inference


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_vllm_image_contract(monkeypatch, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs["json"]
            captured["headers"] = kwargs["headers"]
            return FakeResponse({"data": [{"b64_json": encoded, "seed": 42}]})

    monkeypatch.setattr(inference.httpx, "AsyncClient", FakeClient)
    outputs = await inference.VLLMImageClient("http://worker:8000", "OpenMOSS-Team/MOSS-Image-SFT").generate(
        {
            "prompt": "a test image",
            "width": 1024,
            "height": 1024,
            "steps": 50,
            "guidance_scale": 3,
            "shift": 5,
            "seed": 42,
            "negative_prompt": "bad",
            "slg": True,
        },
        tmp_path,
    )
    assert captured["url"].endswith("/v1/images/generations")
    assert captured["payload"]["size"] == "1024x1024"
    assert captured["payload"]["flow_shift"] == 5
    assert captured["payload"]["negative_prompt"] == "bad"
    assert captured["payload"]["extra_params"]["uncond_skip_layers"] == [22]
    assert captured["payload"]["extra_params"]["slg_start"] == 0.01
    assert captured["payload"]["extra_params"]["slg_stop"] == 1.0
    assert "X-MOSS-Job-ID" not in captured["headers"]
    assert Image.open(outputs[0]["path"]).size == (16, 16)


@pytest.mark.asyncio
async def test_vllm_generation_propagates_job_attempt_identity(monkeypatch, tmp_path):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, _url, **kwargs):
            captured["headers"] = kwargs["headers"]
            return FakeResponse({"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]})

    monkeypatch.setattr(inference.httpx, "AsyncClient", FakeClient)
    with pytest.raises(inference.InferenceError, match="invalid image data"):
        await inference.VLLMImageClient("http://worker:8000", "OpenMOSS-Team/MOSS-Image-SFT").generate(
            {"prompt": "test", "width": 512, "height": 512, "job_id": "job-1", "attempt_id": "attempt-1"},
            tmp_path,
        )
    assert captured["headers"]["X-MOSS-Job-ID"] == "job-1"
    assert captured["headers"]["X-MOSS-Attempt-ID"] == "attempt-1"


@pytest.mark.asyncio
async def test_vllm_random_seed_is_opt_in(monkeypatch, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, _url, **kwargs):
            captured["payload"] = kwargs["json"]
            return FakeResponse({"data": [{"b64_json": encoded}]})

    monkeypatch.setattr(inference.httpx, "AsyncClient", FakeClient)
    await inference.VLLMImageClient("http://worker:8000", "OpenMOSS-Team/MOSS-Image-SFT").generate(
        {"prompt": "stable", "width": 1024, "height": 1024, "seed": None}, tmp_path
    )
    assert "seed" not in captured["payload"]


@pytest.mark.asyncio
async def test_vllm_layout_payload_is_compiled_at_inference_boundary(monkeypatch, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["payload"] = kwargs["json"]
            return FakeResponse({"data": [{"b64_json": encoded}]})

    monkeypatch.setattr(inference.httpx, "AsyncClient", FakeClient)
    await inference.VLLMImageClient("http://worker:8000", "OpenMOSS-Team/MOSS-Image-SFT").generate(
        {
            "prompt": "layout",
            "width": 1024,
            "height": 1024,
            "layout_json": {
                "width": 1024,
                "height": 1024,
                "bbox_coord_space": "abs",
                "bbox_format": "xyxy",
                "summary": {"caption": "A poster"},
                "items": [{"label": "object", "caption": "A cup", "bbox": [0, 0, 512, 512]}],
            },
        },
        tmp_path,
    )
    payload_layout = __import__("json").loads(captured["payload"]["prompt"])
    assert payload_layout["bbox_coord_space"] == "abs_centralized"
    assert payload_layout["bbox_format"] == "yxyx"
    assert payload_layout["items"][0]["bbox"] == [-512, -512, 0, 0]


@pytest.mark.asyncio
async def test_optional_local_router_contract(monkeypatch):
    captured = {}

    class RouterResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"intent":"edit","preservation":"close"}'}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs["json"]
            return RouterResponse()

    monkeypatch.setattr(inference.httpx, "AsyncClient", FakeClient)
    result = await inference.VLLMChatClient("http://local-chat", "router", None).classify(
        "change the outfit",
        has_attached_image=True,
        has_conversation_image=True,
    )

    assert result == {"intent": "edit", "preservation": "close"}
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["payload"]["messages"][1]["content"].find("change the outfit") >= 0


@pytest.mark.asyncio
async def test_vllm_health_requires_health_and_model_discovery(monkeypatch):
    class Response:
        is_success = True

        def json(self):
            return {"data": [{"id": "OpenMOSS-Team/MOSS-Image-SFT"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return Response()

    monkeypatch.setattr(inference.httpx, "AsyncClient", FakeClient)
    health = await inference.VLLMImageClient("http://worker:8000", "OpenMOSS-Team/MOSS-Image-SFT").health()
    assert health["ok"] is True
    assert health["models"][0]["id"] == "OpenMOSS-Team/MOSS-Image-SFT"
