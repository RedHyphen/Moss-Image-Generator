from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from .db import Database, utc_now
from .inference import InferenceCancelled, InferenceError, VLLMImageClient
from .storage import contained_path, media_key, media_usage_bytes

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, db: Database, settings, vllm: VLLMImageClient):
        self.db = db
        self.settings = settings
        self.vllm = vllm
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()

    async def start(self):
        if self.task and not self.task.done():
            return
        for recovered in self.db.recover_running_jobs():
            self._record_assistant_error(recovered, "Application restarted while this job was running")
        self.stop_event = asyncio.Event()
        self.task = asyncio.create_task(self.run(), name="moss-job-worker")

    async def stop(self):
        if not self.task:
            return
        self.stop_event.set()
        await self.task
        self.task = None

    async def run(self):
        while not self.stop_event.is_set():
            job = self.db.claim_next_job()
            if not job:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                self.db.update_job_progress(job["id"], {"stage": "starting", "known": True})
                await self.process(job)
                if self.db.is_job_cancel_requested(job["id"]):
                    raise InferenceCancelled("Cancelled by user")
                self.db.finish_job(job["id"], "completed")
            except InferenceCancelled as exc:
                logger.info("job %s cancelled: %s", job["id"], exc)
                self.db.finish_job(job["id"], "cancelled", str(exc))
                self._record_assistant_cancelled(job, str(exc))
            except Exception as exc:
                logger.exception("job %s failed", job["id"])
                self.db.finish_job(job["id"], "failed", str(exc))
                self._record_assistant_error(job, str(exc))

    def _record_assistant_cancelled(self, job: dict[str, Any], reason: str) -> None:
        params = json.loads(job["params_json"])
        message_id = params.get("message_id")
        if message_id:
            self.db.update_message(message_id, status="cancelled", error=reason)
        conversation_id = params.get("conversation_id")
        if not conversation_id:
            return
        self.db.add_message(
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": "I cancelled the local image operation before it completed.",
                "status": "cancelled",
                "plan_json": json.dumps(params.get("execution_plan", {}), ensure_ascii=False),
                "job_id": job["id"],
                "error": reason,
                "created_at": utc_now(),
            }
        )

    def _record_assistant_error(self, job: dict[str, Any], error: str) -> None:
        params = json.loads(job["params_json"])
        message_id = params.get("message_id")
        if message_id:
            self.db.update_message(message_id, status="failed", error=error)
        conversation_id = params.get("conversation_id")
        if not conversation_id:
            return
        self.db.add_message(
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": f"I could not complete that request: {error}",
                "status": "failed",
                "plan_json": json.dumps(params.get("execution_plan", {}), ensure_ascii=False),
                "job_id": job["id"],
                "error": error,
                "created_at": utc_now(),
            }
        )

    async def process(self, job: dict[str, Any]):
        params = json.loads(job["params_json"])
        params["job_id"] = job["id"]
        params["attempt_id"] = job.get("attempt_id")
        data_root = getattr(self.settings, "data_dir", self.settings.images_dir.parent)
        if job["kind"] != "generate":
            raise InferenceError("Image editing is not available; only local MOSS-Image generation is supported")
        self.db.update_job_progress(job["id"], {"stage": "local_vllm_request", "known": True})
        outputs = await self.vllm.generate(params, self.settings.images_dir)
        if not outputs:
            raise InferenceError("Inference completed without returning an image")
        validated_outputs = []
        for output in outputs:
            try:
                output_path = contained_path(Path(output["path"]), self.settings.images_dir)
            except (KeyError, TypeError, ValueError) as exc:
                raise InferenceError("Inference output path is outside application storage") from exc
            if not output_path.is_file():
                raise InferenceError("Inference returned an output file that does not exist")
            validated_outputs.append({**output, "path": output_path})
        outputs = validated_outputs
        media_limit = getattr(self.settings, "max_media_bytes", None)
        if media_limit is not None:
            media_roots = [self.settings.images_dir]
            uploads_dir = getattr(self.settings, "uploads_dir", None)
            if uploads_dir is not None:
                media_roots.append(uploads_dir)
            if media_usage_bytes(*media_roots) > media_limit:
                # The local worker writes rasters before returning them. Remove only
                # the current outputs after proving they are inside the image
                # root, leaving earlier gallery/source files recoverable.
                for output in outputs:
                    try:
                        contained_path(Path(output["path"]), self.settings.images_dir).unlink(missing_ok=True)
                    except (KeyError, OSError, ValueError):
                        logger.warning("media limit cleanup skipped an unsafe output path")
                raise InferenceError(
                    "Local media storage limit reached; delete unused images or raise MOSS_MAX_MEDIA_MB"
                )
        assistant_id = str(uuid.uuid4()) if params.get("conversation_id") else None
        image_values = []
        for output in outputs:
            metadata = {
                **params,
                "job_id": job["id"],
                "output_seed": output.get("seed"),
                "backend": job["kind"],
                "backend_model": output.get(
                    "backend_model",
                    params.get("model"),
                ),
                "backend_model_revision": output.get(
                    "backend_model_revision",
                    None,
                ),
                "operation": None,
                "model_input_size": output.get("model_input_size"),
                "source_preparation": output.get("source_preparation"),
            }
            image_values.append(
                {
                    "id": output["id"],
                    "user_id": params.get("user_id"),
                    "path": media_key(Path(output["path"]), data_root),
                    "kind": job["kind"],
                    "prompt": params.get("prompt", ""),
                    "metadata_json": json.dumps(metadata, ensure_ascii=False),
                    "conversation_id": params.get("conversation_id"),
                    "message_id": assistant_id,
                    "parent_image_id": params.get("parent_image_id"),
                    "created_at": utc_now(),
                }
            )
        message_id = params.get("message_id")
        conversation_id = params.get("conversation_id")
        assistant_message = None
        content = ""
        if conversation_id:
            if params.get("context_mode") == "prompt-continuation":
                content = (
                    "Added your instruction to the previous prompt and generated a new image with MOSS-Image. "
                    "MOSS-Image does not receive the previous raster, so visual continuity is not guaranteed."
                )
            else:
                content = "Generated a new image with MOSS-Image."
            plan = params.get("execution_plan") or {}
            warnings = plan.get("warnings") if isinstance(plan, dict) else None
            identity_warnings = [
                str(item)
                for item in warnings or []
                if isinstance(item, str) and "Named identity" in item
            ]
            if identity_warnings:
                content += f" Note: {identity_warnings[0]}"
            assistant_message = {
                "id": assistant_id,
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": content,
                "status": "completed",
                "plan_json": json.dumps(params.get("execution_plan", {}), ensure_ascii=False),
                "job_id": job["id"],
                "error": "",
                "created_at": utc_now(),
            }
        try:
            self.db.finalize_generation(
                image_values,
                message_id=message_id,
                assistant_message=assistant_message,
                output_ids=[output["id"] for output in outputs],
            )
        except Exception:
            for output in outputs:
                try:
                    contained_path(Path(output["path"]), self.settings.images_dir).unlink(missing_ok=True)
                except (KeyError, OSError, ValueError):
                    logger.warning("generation finalization failed and output cleanup was skipped")
            raise
