from __future__ import annotations

import base64
import io
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .layout import compile_layout_prompt

logger = logging.getLogger(__name__)


class InferenceError(RuntimeError):
    pass


class InferenceCancelled(InferenceError):
    """Raised when a local pipeline observes a user cancellation request."""


class VLLMImageClient:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout_seconds: int = 3600):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health", headers=self._headers())
                models = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
            try:
                model_payload = models.json() if models.is_success else {}
                model_data = model_payload.get("data", []) if isinstance(model_payload, dict) else []
            except (ValueError, TypeError, AttributeError):
                model_data = []
            ok = response.is_success and models.is_success and isinstance(model_data, list) and bool(model_data)
            return {
                "ok": ok,
                "models": model_data,
                "error": "" if ok else "vLLM health or model discovery failed",
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "models": []}

    async def generate(self, params: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": params.get("model") or self.model,
            "prompt": params["prompt"],
            "size": f"{params['width']}x{params['height']}",
            "n": params.get("n", 1),
            "num_inference_steps": params.get("steps", 50),
            "guidance_scale": params.get("guidance_scale", 3.0),
            "flow_shift": params.get("shift", 5.0),
            "seed": params.get("seed"),
            "negative_prompt": params.get("negative_prompt", ""),
            "response_format": "b64_json",
            "extra_params": {
                "uncond_skip_layers": [22] if params.get("slg", True) else [],
                "slg_start": 0.01,
                "slg_stop": 1.0,
            },
        }
        if payload["seed"] is None:
            payload.pop("seed")
        if params.get("layout_json"):
            # Store the user-facing absolute-pixel draft for provenance, but
            # compile it only at the MOSS boundary into centralized yxyx.
            payload["prompt"] = compile_layout_prompt(params["layout_json"])
            identity_instruction = str(params.get("identity_instruction") or "").strip()
            if identity_instruction:
                payload["prompt"] = f"{payload['prompt']}\n\n{identity_instruction}"
        try:
            # Generation itself may legitimately take several minutes, but a
            # dead worker must not leave the persistent queue running forever.
            timeout = httpx.Timeout(connect=10, read=self.timeout_seconds, write=30, pool=10)
            request_headers = {**self._headers(), "Content-Type": "application/json"}
            if params.get("job_id"):
                request_headers["X-MOSS-Job-ID"] = str(params["job_id"])
            if params.get("attempt_id"):
                request_headers["X-MOSS-Attempt-ID"] = str(params["attempt_id"])
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/images/generations",
                    headers=request_headers,
                    json=payload,
                )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise InferenceError(f"MOSS-Image vLLM request failed: {exc}") from exc

        outputs = []
        for item in body.get("data", []):
            encoded = item.get("b64_json")
            if not encoded:
                raise InferenceError("vLLM returned no local b64_json image")
            image_id = str(__import__("uuid").uuid4())
            path = output_dir / f"{image_id}.png"
            raw = base64.b64decode(encoded)
            try:
                with Image.open(io.BytesIO(raw)) as generated:
                    generated.verify()
                with Image.open(io.BytesIO(raw)) as generated:
                    generated.convert("RGBA" if "A" in generated.getbands() else "RGB").save(path, format="PNG")
            except Exception as exc:
                raise InferenceError(f"vLLM returned invalid image data: {exc}") from exc
            outputs.append({"id": image_id, "path": path, "seed": item.get("seed", payload.get("seed", "random"))})
        if not outputs:
            raise InferenceError("vLLM returned an empty image response")
        return outputs


class VLLMChatClient:
    def __init__(self, base_url: str | None, model: str | None, api_key: str | None):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.model = model
        self.api_key = api_key

    async def classify(self, prompt: str, *, has_attached_image: bool, has_conversation_image: bool) -> dict[str, str]:
        """Ask an explicitly configured local chat model for a routing hint.

        The classifier receives text and asset-presence flags only. It never
        replaces the actual source raster or chooses an image asset ID.
        """
        if not self.base_url or not self.model:
            raise InferenceError("VLLM_CHAT_URL and VLLM_CHAT_MODEL are not configured")
        instruction = (
            "Classify the user's image-studio request. Return only a JSON object "
            'with intent one of "generate", "edit", or "question", and preservation '
            'one of "close", "balanced", or "creative". Use edit when the user '
            "wants to change an attached or previous image. Use question only for "
            "a visual question that requires image understanding. Do not invent "
            "asset IDs or describe image pixels."
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instruction": prompt,
                            "has_attached_image": has_attached_image,
                            "has_conversation_image": has_conversation_image,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 128,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.strip("`").removeprefix("json").strip()
            value = json.loads(content)
            intent = value.get("intent")
            preservation = value.get("preservation", "balanced")
            if intent not in {"generate", "edit", "question"} or preservation not in {"close", "balanced", "creative"}:
                raise ValueError("invalid routing fields")
            return {"intent": intent, "preservation": preservation}
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise InferenceError(f"Local routing assistant returned invalid output: {exc}") from exc

    async def enhance(self, prompt: str, mode: str, *, width: int = 1024, height: int = 1024) -> str:
        if not self.base_url or not self.model:
            raise InferenceError("VLLM_CHAT_URL and VLLM_CHAT_MODEL are not configured")
        instruction = (
            "Rewrite the user's image idea into a detailed, faithful image prompt. Preserve all explicit constraints. "
            "Return only the prompt, no commentary."
            if mode == "caption"
            else f"Convert the user's image idea into the current MOSS-Image absolute-pixel layout draft schema "
            f"for a {width}x{height} canvas. Return only JSON with width={width}, height={height}, "
            "bbox_coord_space='abs', bbox_format='xyxy', summary.caption, and a nonempty flat items array."
        )
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 2048,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=body)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise InferenceError(f"vLLM prompt assistance failed: {exc}") from exc

