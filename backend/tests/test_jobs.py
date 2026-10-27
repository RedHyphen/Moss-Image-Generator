import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.db import Database, utc_now
from app.inference import InferenceError
from app.jobs import JobWorker


class FakeVLLM:
    async def generate(self, params, output_dir: Path):
        output = output_dir / "fake-output.png"
        Image.new("RGB", (16, 16), "green").save(output)
        return [{"id": "fake-output", "path": output, "seed": 123}]


class EmptyVLLM:
    async def generate(self, params, output_dir: Path):
        return []


class UnsafeVLLM:
    def __init__(self, outside_path: Path):
        self.outside_path = outside_path

    async def generate(self, params, output_dir: Path):
        Image.new("RGB", (16, 16), "purple").save(self.outside_path)
        return [{"id": "unsafe-output", "path": self.outside_path, "seed": 1}]


def test_job_process_persists_output_metadata(tmp_path):
    db = Database(tmp_path / "jobs.sqlite3")
    db.initialize()
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    worker = JobWorker(db, SimpleNamespace(images_dir=images_dir), FakeVLLM())
    db.add_job("job-1", "generate", {"prompt": "test", "width": 512, "height": 512})
    asyncio.run(worker.process(db.claim_next_job()))

    image = db.get_image("fake-output")
    assert image is not None
    assert image["path"] == "images/fake-output.png"
    assert '"job_id": "job-1"' in image["metadata_json"]


def test_generation_finalization_failure_removes_written_outputs(tmp_path):
    db = Database(tmp_path / "finalization-failure.sqlite3")
    db.initialize()
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    worker = JobWorker(db, SimpleNamespace(images_dir=images_dir), FakeVLLM())

    def fail_finalization(*_args, **_kwargs):
        raise RuntimeError("injected finalization failure")

    worker.db.finalize_generation = fail_finalization
    db.add_job("job-finalization-failure", "generate", {"prompt": "test"})

    with pytest.raises(RuntimeError, match="injected finalization failure"):
        asyncio.run(worker.process(db.claim_next_job()))

    assert not (images_dir / "fake-output.png").exists()
    assert db.get_image("fake-output") is None


def test_non_generate_job_is_rejected_without_an_edit_adapter(tmp_path):
    db = Database(tmp_path / "no-edit.sqlite3")
    db.initialize()
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    worker = JobWorker(db, SimpleNamespace(images_dir=images_dir), FakeVLLM())
    db.add_job("edit-job", "edit", {"kind": "edit", "operation": "img2img"})

    with pytest.raises(InferenceError, match="Image editing is not available"):
        asyncio.run(worker.process(db.claim_next_job()))


def test_job_rejects_output_outside_images_root(tmp_path):
    db = Database(tmp_path / "unsafe-output.sqlite3")
    db.initialize()
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    outside_path = tmp_path / "outside.png"
    worker = JobWorker(db, SimpleNamespace(images_dir=images_dir), UnsafeVLLM(outside_path))
    db.add_job("unsafe-job", "generate", {"prompt": "test", "width": 512, "height": 512})

    with pytest.raises(InferenceError, match="outside application storage"):
        asyncio.run(worker.process(db.claim_next_job()))
    assert outside_path.is_file()
    assert db.get_image("unsafe-output") is None


def test_conversational_prompt_continuation_links_parent_without_claiming_raster_input(tmp_path):
    db = Database(tmp_path / "conversation-jobs.sqlite3")
    db.initialize()
    db.add_conversation("conversation-1", "Continuation")
    db.add_image({
        "id": "previous-image", "path": str(tmp_path / "previous.png"), "kind": "generate",
        "prompt": "A knight in a snowy forest", "metadata_json": "{}",
        "conversation_id": "conversation-1", "message_id": None, "parent_image_id": None,
        "created_at": utc_now(),
    })
    db.add_message({
        "id": "message-1", "conversation_id": "conversation-1", "role": "user",
        "content": "Add a red cape", "status": "queued",
        "plan_json": '{"kind":"generate","context_mode":"prompt-continuation"}',
        "created_at": utc_now(),
    })
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    worker = JobWorker(db, SimpleNamespace(images_dir=images_dir), FakeVLLM())
    db.add_job("job-1", "generate", {
        "prompt": "Previous generation prompt: A knight in a snowy forest\nAdditional user instruction: Add a red cape",
        "conversation_id": "conversation-1", "message_id": "message-1", "parent_image_id": "previous-image",
        "context_mode": "prompt-continuation", "execution_plan": {"kind": "generate"},
    })
    asyncio.run(worker.process(db.claim_next_job()))

    output = db.get_image("fake-output")
    assert output["parent_image_id"] == "previous-image"
    assistant = next(item for item in db.list_messages("conversation-1") if item["role"] == "assistant")
    assert "does not receive the previous raster" in assistant["content"]


def test_empty_inference_result_is_not_reported_as_success(tmp_path):
    db = Database(tmp_path / "empty-job.sqlite3")
    db.initialize()
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    worker = JobWorker(db, SimpleNamespace(images_dir=images_dir), EmptyVLLM())
    db.add_job("empty-job", "generate", {"prompt": "test", "width": 512, "height": 512})
    with pytest.raises(InferenceError, match="without returning an image"):
        asyncio.run(worker.process(db.claim_next_job()))


def test_worker_restart_recovery_updates_conversation_state(tmp_path):
    db = Database(tmp_path / "recovery.sqlite3")
    db.initialize()
    db.add_conversation("conversation-1", "Recovery")
    db.add_message({
        "id": "message-1", "conversation_id": "conversation-1", "role": "user",
        "content": "Generate an image", "status": "running", "plan_json": '{"kind":"generate"}',
        "created_at": utc_now(),
    })
    db.add_job("job-running", "generate", {
        "prompt": "Generate an image", "conversation_id": "conversation-1", "message_id": "message-1",
        "execution_plan": {"kind": "generate"},
    })
    db.claim_next_job()
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    worker = JobWorker(db, SimpleNamespace(images_dir=images_dir), FakeVLLM())

    async def start_and_stop():
        await worker.start()
        await worker.stop()

    asyncio.run(start_and_stop())
    assert db.get_message("message-1")["status"] == "failed"
    assert any(item["role"] == "assistant" and item["status"] == "failed" for item in db.list_messages("conversation-1"))
