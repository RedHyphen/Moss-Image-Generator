import io
import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest
from PIL import Image


# Configure the application before importing app.main; the application owns one
# process-local SQLite connection and reads its settings at import time.
_test_data = tempfile.TemporaryDirectory(prefix="moss-api-test-")
os.environ["MOSS_DATA_DIR"] = _test_data.name
os.environ["MOSS_VLLM_URL"] = "http://127.0.0.1:9"
os.environ["MOSS_LOG_LEVEL"] = "CRITICAL"
os.environ["MOSS_TRUSTED_SUBNETS"] = "192.168.1.0/24"

from app import main as main_app  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.mark.asyncio
async def test_authenticated_api_flow_and_protected_routes(monkeypatch):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        security_status = await client.get("/api/auth/status")
        assert security_status.headers["x-content-type-options"] == "nosniff"
        assert security_status.headers["referrer-policy"] == "no-referrer"
        assert security_status.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in security_status.headers["content-security-policy"]
        for path in ("/api/references", "/api/images", "/api/jobs", "/api/system", "/api/models"):
            assert (await client.get(path)).status_code == 401

        response = await client.post(
            "/api/auth/setup",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        assert response.status_code == 200
        second_setup = await client.post(
            "/api/auth/setup",
            json={"username": "second-admin", "password": "another correct password"},
        )
        assert second_setup.status_code == 409
        headers = {"X-CSRF-Token": response.json()["csrf_token"]}
        system_payload = (await client.get("/api/system", headers=headers)).json()
        assert system_payload["offline_mode"] is False
        assert system_payload["prompt_assistance"]["configured"] is False

        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), (20, 80, 160)).save(buffer, format="JPEG")
        response = await client.post(
            "/api/uploads",
            files={"file": ("source.jpg", buffer.getvalue(), "image/jpeg")},
            data={"role": "source", "note": "Preserve the subject's facial appearance."},
            headers=headers,
        )
        assert response.status_code == 200
        reference_id = response.json()["id"]
        assert response.json()["note"] == "Preserve the subject's facial appearance."
        references = await client.get("/api/references")
        assert references.status_code == 200
        assert references.json()[0]["id"] == reference_id

        invalid = await client.post(
            "/api/jobs",
            json={"kind": "generate", "prompt": "test", "width": 1000, "height": 1024},
            headers=headers,
        )
        assert invalid.status_code == 422

        for invalid_numeric_settings in (
            {"width": 0, "height": 512},
            {"width": 2064, "height": 512},
            {"width": "", "height": 512},
            {"steps": 0},
            {"guidance_scale": 21},
            {"n": 5},
        ):
            invalid_settings = await client.post(
                "/api/jobs",
                json={"kind": "generate", "prompt": "test", **invalid_numeric_settings},
                headers=headers,
            )
            assert invalid_settings.status_code == 422

        unsupported_moss = await client.post(
            "/api/jobs",
            json={"kind": "generate", "prompt": "test", "model": "untrusted/other-model"},
            headers=headers,
        )
        assert unsupported_moss.status_code == 422

        async def allow_configured_moss(_model_id):
            return None

        monkeypatch.setattr(main_app, "_require_moss_model", allow_configured_moss)
        default_model_job = await client.post(
            "/api/jobs",
            json={"kind": "generate", "prompt": "record the configured model"},
            headers=headers,
        )
        assert default_model_job.status_code == 200
        default_model_id = default_model_job.json()["id"]
        default_model_row = next(item for item in (await client.get("/api/jobs")).json() if item["id"] == default_model_id)
        assert default_model_row["params"]["model"] == main_app.settings.moss_model
        assert (await client.post(f"/api/jobs/{default_model_id}/cancel", headers=headers)).status_code == 200
        monkeypatch.undo()

        source_on_generation = await client.post(
            "/api/jobs",
            json={"kind": "generate", "prompt": "test", "source_reference_id": reference_id},
            headers=headers,
        )
        assert source_on_generation.status_code == 422

        reference_on_generation = await client.post(
            "/api/jobs",
            json={"kind": "generate", "prompt": "test", "reference_ids": [reference_id]},
            headers=headers,
        )
        assert reference_on_generation.status_code == 422
        assert "does not support uploaded-image conditioning" in reference_on_generation.json()["detail"]

        mask_on_img2img = await client.post(
            "/api/jobs",
            json={
                "kind": "edit",
                "prompt": "change the background",
                "source_reference_id": reference_id,
                "mask_reference_id": reference_id,
                "operation": "img2img",
            },
            headers=headers,
        )
        assert mask_on_img2img.status_code == 422

        invalid_layout_edit = await client.post(
            "/api/jobs",
            json={
                "kind": "edit",
                "prompt": "change the background",
                "source_reference_id": reference_id,
                "layout_json": {"summary": {}, "items": []},
            },
            headers=headers,
        )
        assert invalid_layout_edit.status_code == 422

        invalid_inpaint = await client.post(
            "/api/jobs",
            json={
                "kind": "edit",
                "operation": "inpaint",
                "prompt": "change the background",
                "width": 512,
                "height": 512,
                "source_reference_id": reference_id,
            },
            headers=headers,
        )
        assert invalid_inpaint.status_code == 422

        rejected_edit = await client.post(
            "/api/jobs",
            json={
                "kind": "edit",
                "prompt": "change background",
                "original_prompt": "preserve the original subject while changing the background",
                "width": 512,
                "height": 512,
                "source_reference_id": reference_id,
                "source_note": "client supplied spoof",
            },
            headers=headers,
        )
        assert rejected_edit.status_code == 422

        conversation = await client.post(
            "/api/conversations",
            json={"title": "Worker failure persistence"},
            headers=headers,
        )
        assert conversation.status_code == 200
        conversation_id = conversation.json()["id"]
        exported_empty = await client.get(f"/api/conversations/{conversation_id}/export", headers=headers)
        assert exported_empty.status_code == 200
        assert "Content-Disposition" in exported_empty.headers
        assert exported_empty.json()["id"] == conversation_id
        assert "images" in exported_empty.json()
        failed_request = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Generate a local test image"},
            headers=headers,
        )
        assert failed_request.status_code == 503
        title_search = await client.get("/api/conversations", params={"q": "Worker failure"}, headers=headers)
        assert [item["id"] for item in title_search.json()] == [conversation_id]
        content_search = await client.get("/api/conversations", params={"q": "local test image"}, headers=headers)
        assert [item["id"] for item in content_search.json()] == [conversation_id]
        oversized_search = await client.get("/api/conversations", params={"q": "x" * 201}, headers=headers)
        assert oversized_search.status_code == 422
        persisted = await client.get(f"/api/conversations/{conversation_id}", headers=headers)
        assert persisted.status_code == 200
        messages = persisted.json()["messages"]
        assert any(item["role"] == "user" and item["status"] == "failed" for item in messages)
        assert any(item["role"] == "assistant" and item["status"] == "failed" for item in messages)

        reusable_path = Path(main_app.settings.images_dir) / "reuse-source.png"
        Image.new("RGB", (512, 512), (40, 80, 120)).save(reusable_path)
        main_app.db.add_image(
            {
                "id": "reuse-source",
                "user_id": main_app.db.get_user("admin")["id"],
                "path": str(reusable_path),
                "kind": "generate",
                "prompt": "A reusable source",
                "metadata_json": json.dumps(
                    {
                        "original_prompt": "A reusable source with saved settings",
                        "model": "OpenMOSS-Team/MOSS-Image-SFT",
                        "width": 512,
                        "height": 512,
                        "steps": 50,
                        "guidance_scale": 3,
                        "negative_prompt": "avoid clutter",
                        "output_seed": 17,
                        "n": 2,
                        "randomized_batch": True,
                        "layout_json": {"summary": {"caption": "Saved layout"}, "items": []},
                        "source_note": "Preserve the source subject.",
                    }
                ),
                "conversation_id": None,
                "message_id": None,
                "parent_image_id": None,
                "created_at": main_app.utc_now(),
            }
        )
        with main_app.db.lock, main_app.db.conn:
            main_app.db.conn.execute(
                "UPDATE images SET conversation_id = ? WHERE id = ?",
                (conversation_id, "reuse-source"),
            )
        assistant_message_id = next(item["id"] for item in messages if item["role"] == "assistant")
        main_app.db.add_message_asset(assistant_message_id, "image", "reuse-source", 0, "output")
        conversation_with_asset = await client.get(f"/api/conversations/{conversation_id}", headers=headers)
        assert conversation_with_asset.status_code == 200
        assistant_asset = next(
            asset
            for message in conversation_with_asset.json()["messages"]
            if message["id"] == assistant_message_id
            for asset in message["assets"]
        )
        assert assistant_asset["metadata"]["randomized_batch"] is True
        assert assistant_asset["metadata"]["negative_prompt"] == "avoid clutter"
        assert assistant_asset["metadata"]["output_seed"] == 17
        assert "user_id" not in assistant_asset["metadata"]
        exported_with_image = await client.get(f"/api/conversations/{conversation_id}/export", headers=headers)
        assert exported_with_image.status_code == 200
        exported_image = next(item for item in exported_with_image.json()["images"] if item["id"] == "reuse-source")
        assert "path" not in exported_image
        assert exported_image["metadata"]["model"] == "OpenMOSS-Team/MOSS-Image-SFT"
        assert exported_image["url"] == "/api/images/reuse-source/file"
        reference_failure = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Transform the source image into a rainy scene", "reference_ids": [reference_id]},
            headers=headers,
        )
        assert reference_failure.status_code == 422
        assert any(item["id"] == reference_id for item in (await client.get("/api/references")).json())
        assert (await client.post("/api/auth/logout", headers=headers)).status_code == 200
        assert (await client.get("/api/auth/me")).status_code == 401
        assert (await client.get(f"/api/conversations/{conversation_id}/export")).status_code == 401


@pytest.mark.asyncio
async def test_public_client_is_rejected_by_network_guard():
    transport = httpx.ASGITransport(app=app, client=("8.8.8.8", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/api/auth/status")).status_code == 403


def test_direct_jobs_reject_internal_relationship_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        main_app.JobRequest.model_validate(
            {"prompt": "test", "conversation_id": "another-user-conversation", "message_id": "message"}
        )


@pytest.mark.asyncio
async def test_conversations_are_scoped_to_authenticated_accounts():
    username_a = "account-isolation-a"
    username_b = "account-isolation-b"
    if not main_app.db.get_user(username_a):
        main_app.db.create_user(username_a, hash_password("account-isolation-password-a"))
    if not main_app.db.get_user(username_b):
        main_app.db.create_user(username_b, hash_password("account-isolation-password-b"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        login_a = await client.post(
            "/api/auth/login", json={"username": username_a, "password": "account-isolation-password-a"}
        )
        assert login_a.status_code == 200
        csrf_a = login_a.json()["csrf_token"]
        created = await client.post(
            "/api/conversations", json={"title": "Private account A"}, headers={"X-CSRF-Token": csrf_a}
        )
        assert created.status_code == 200
        conversation_id = created.json()["id"]
        assert (await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_a})).status_code == 200

        login_b = await client.post(
            "/api/auth/login", json={"username": username_b, "password": "account-isolation-password-b"}
        )
        assert login_b.status_code == 200
        csrf_b = login_b.json()["csrf_token"]
        assert not any(
            item["id"] == conversation_id
            for item in (await client.get("/api/conversations")).json()
        )
        assert (await client.get(f"/api/conversations/{conversation_id}")).status_code == 404
        assert (await client.get(f"/api/conversations/{conversation_id}/export")).status_code == 404
        other_conversation = await client.post(
            "/api/conversations", json={"title": "Keep account B history"}, headers={"X-CSRF-Token": csrf_b}
        )
        assert other_conversation.status_code == 200
        other_conversation_id = other_conversation.json()["id"]
        assert (await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_b})).status_code == 200

        login_a_again = await client.post(
            "/api/auth/login", json={"username": username_a, "password": "account-isolation-password-a"}
        )
        assert login_a_again.status_code == 200
        csrf_a_again = login_a_again.json()["csrf_token"]
        user_a_id = main_app.db.get_user(username_a)["id"]
        reference_path = Path(main_app.settings.uploads_dir) / "account-a-history.png"
        image_path = Path(main_app.settings.images_dir) / "account-a-history.png"
        Image.new("RGB", (32, 32), (120, 40, 80)).save(reference_path)
        Image.new("RGB", (32, 32), (40, 120, 80)).save(image_path)
        main_app.db.add_reference(
            {
                "id": "account-a-history-reference",
                "user_id": user_a_id,
                "filename": "history.png",
                "path": str(reference_path),
                "role": "source",
                "note": "",
                "width": 32,
                "height": 32,
                "bytes": reference_path.stat().st_size,
                "created_at": main_app.utc_now(),
            }
        )
        main_app.db.add_image(
            {
                "id": "account-a-history-image",
                "user_id": user_a_id,
                "path": str(image_path),
                "kind": "generate",
                "prompt": "private image",
                "metadata_json": "{}",
                "conversation_id": conversation_id,
                "message_id": None,
                "parent_image_id": None,
                "created_at": main_app.utc_now(),
            }
        )
        cleared = await client.delete("/api/account/history", headers={"X-CSRF-Token": csrf_a_again})
        assert cleared.status_code == 200
        assert cleared.json()["deleted"]["conversation_count"] == 1
        assert cleared.json()["deleted"]["image_count"] == 1
        assert cleared.json()["deleted"]["reference_count"] == 1
        assert not reference_path.exists()
        assert not image_path.exists()
        assert (await client.get("/api/conversations")).json() == []
        assert (await client.get(f"/api/conversations/{conversation_id}")).status_code == 404
        assert (await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_a_again})).status_code == 200

        login_b_again = await client.post(
            "/api/auth/login", json={"username": username_b, "password": "account-isolation-password-b"}
        )
        assert login_b_again.status_code == 200
        assert (await client.get(f"/api/conversations/{other_conversation_id}")).status_code == 200
        assert (await client.post("/api/auth/logout", headers={"X-CSRF-Token": login_b_again.json()["csrf_token"]})).status_code == 200


@pytest.mark.asyncio
async def test_ipv4_mapped_ipv6_client_matches_trusted_ipv4_subnet():
    transport = httpx.ASGITransport(app=app, client=("::ffff:192.168.1.25", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/api/auth/status")).status_code == 200
