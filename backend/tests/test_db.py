import tempfile
from pathlib import Path

import pytest

from app.db import Database, utc_now


def test_queued_job_lifecycle_and_restart_recovery():
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "test.sqlite3")
        db.initialize()
        db.add_job("job-1", "generate", {"prompt": "a test"})
        claimed = db.claim_next_job()
        assert claimed["id"] == "job-1"
        assert claimed["attempt_id"]
        db.recover_running_jobs()
        assert db.list_jobs()[0]["status"] == "failed"
        assert "restarted" in db.list_jobs()[0]["error"]


def test_queue_limit_count_includes_running_jobs():
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "test.sqlite3")
        db.initialize()
        db.add_job("job-1", "generate", {"prompt": "a test"})
        assert db.queued_count() == 1


def test_atomic_enqueue_rejects_when_queue_is_full():
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "test.sqlite3")
        db.initialize()
        assert db.add_job("job-1", "generate", {"prompt": "a test"}, max_queue=1)
        assert not db.add_job("job-2", "generate", {"prompt": "another"}, max_queue=1)
        assert [row["id"] for row in db.list_jobs()] == ["job-1"]


def test_failed_job_can_be_retried():
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "test.sqlite3")
        db.initialize()
        db.add_job("job-1", "generate", {"prompt": "a test"})
        db.claim_next_job()
        db.finish_job("job-1", "failed", "backend unavailable")
        assert db.retry_job("job-1")
        assert db.list_jobs()[0]["status"] == "queued"
        assert db.list_jobs()[0]["attempt_id"] is None


def test_running_job_can_request_cooperative_cancellation():
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "test.sqlite3")
        db.initialize()
        db.add_job("edit-job", "edit", {"prompt": "edit"})
        db.claim_next_job()
        assert db.request_job_cancel("edit-job")
        assert db.is_job_cancel_requested("edit-job")
        assert db.queued_count() == 1
        db.finish_job("edit-job", "cancelled", "Cancelled by user")
        assert db.list_jobs()[0]["status"] == "cancelled"


def test_generation_finalization_rolls_back_all_rows_on_constraint_failure(tmp_path):
    db = Database(tmp_path / "finalize-transaction.sqlite3")
    db.initialize()
    values = {
        "id": "duplicate-output",
        "user_id": None,
        "path": str(tmp_path / "duplicate-output.png"),
        "kind": "generate",
        "prompt": "test",
        "metadata_json": "{}",
        "conversation_id": None,
        "message_id": None,
        "parent_image_id": None,
        "created_at": utc_now(),
    }

    with pytest.raises(Exception):
        db.finalize_generation([values, values])

    assert db.get_image("duplicate-output") is None


def test_atomic_retry_rejects_when_queue_is_full():
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "test.sqlite3")
        db.initialize()
        db.add_job("failed", "generate", {"prompt": "failed"})
        db.claim_next_job()
        db.finish_job("failed", "failed", "backend unavailable")
        db.add_job("active", "generate", {"prompt": "active"})
        assert db.retry_job("failed", max_queue=1) == "full"
        failed = next(row for row in db.list_jobs() if row["id"] == "failed")
        assert failed["status"] == "failed"


def test_reference_order_is_persistent_and_requires_a_complete_permutation(tmp_path):
    db = Database(tmp_path / "references.sqlite3")
    db.initialize()
    for reference_id in ("a", "b", "c"):
        db.add_reference(
            {
                "id": reference_id,
                "filename": f"{reference_id}.png",
                "path": str(tmp_path / f"{reference_id}.png"),
                "role": "reference",
                "note": "",
                "width": 1,
                "height": 1,
                "bytes": 1,
                "created_at": utc_now(),
            }
        )

    assert db.reorder_references(["a", "c", "b"])
    assert [row["id"] for row in db.list_references()] == ["a", "c", "b"]
    assert not db.reorder_references(["a", "a", "b"])
    assert not db.reorder_references(["a", "b"])


def test_conversation_messages_and_image_branch_metadata_are_persistent(tmp_path):
    db = Database(tmp_path / "conversation.sqlite3")
    db.initialize()
    db.add_conversation("conversation-1", "A test conversation")
    db.add_message({
        "id": "message-1",
        "conversation_id": "conversation-1",
        "role": "user",
        "content": "Generate a castle",
        "status": "queued",
        "plan_json": '{"kind":"generate"}',
        "created_at": utc_now(),
    })
    db.add_image({
        "id": "image-1",
        "path": str(tmp_path / "image.png"),
        "kind": "generate",
        "prompt": "Generate a castle",
        "metadata_json": "{}",
        "conversation_id": "conversation-1",
        "message_id": "message-1",
        "parent_image_id": None,
        "created_at": utc_now(),
    })
    db.add_message_asset("message-1", "image", "image-1", 0, "output")
    assert db.list_conversation_images("conversation-1")[0]["id"] == "image-1"
    assert db.list_message_assets("message-1")[0]["asset_id"] == "image-1"


def test_conversation_search_matches_titles_and_message_content(tmp_path):
    db = Database(tmp_path / "conversation-search.sqlite3")
    db.initialize()
    db.add_conversation("title-match", "Rainy cyberpunk scene")
    db.add_conversation("content-match", "Untitled conversation")
    db.add_message(
        {
            "id": "content-message",
            "conversation_id": "content-match",
            "role": "user",
            "content": "Preserve the person's pose while changing the background",
            "created_at": utc_now(),
        }
    )

    assert [row["id"] for row in db.list_conversations(search="cyberpunk")] == ["title-match"]
    assert [row["id"] for row in db.list_conversations(search="pose")] == ["content-match"]
    assert db.list_conversations(search="missing") == []


def test_user_scoped_records_are_not_returned_to_other_accounts(tmp_path):
    db = Database(tmp_path / "ownership.sqlite3")
    db.initialize()
    first_user = db.create_user("first", "hash-one")
    second_user = db.create_user("second", "hash-two")
    db.add_conversation("first-conversation", "First private chat", first_user)
    db.add_conversation("second-conversation", "Second private chat", second_user)
    db.add_image(
        {
            "id": "first-image",
            "user_id": first_user,
            "path": str(tmp_path / "first.png"),
            "kind": "generate",
            "prompt": "first",
            "metadata_json": "{}",
            "conversation_id": "first-conversation",
            "message_id": None,
            "parent_image_id": None,
            "created_at": utc_now(),
        }
    )
    db.add_job("first-job", "generate", {"prompt": "first"}, user_id=first_user)

    assert [row["id"] for row in db.list_conversations(user_id=first_user)] == ["first-conversation"]
    assert db.get_conversation("second-conversation", first_user) is None
    assert [row["id"] for row in db.list_images(user_id=first_user)] == ["first-image"]
    assert db.get_image("first-image", second_user) is None
    assert [row["id"] for row in db.list_jobs(user_id=first_user)] == ["first-job"]
    assert db.get_job("first-job", second_user) is None


def test_clear_user_history_is_account_scoped_and_preserves_model_records(tmp_path):
    db = Database(tmp_path / "clear-history.sqlite3")
    db.initialize()
    first_user = db.create_user("history-first", "hash-one")
    second_user = db.create_user("history-second", "hash-two")
    db.add_conversation("history-conversation-a", "Private history", first_user)
    db.add_message(
        {
            "id": "history-message-a",
            "conversation_id": "history-conversation-a",
            "role": "user",
            "content": "Delete this history",
            "created_at": utc_now(),
        }
    )
    db.add_image(
        {
            "id": "history-image-a",
            "user_id": first_user,
            "path": str(tmp_path / "history-image-a.png"),
            "kind": "generate",
            "prompt": "Delete this image",
            "metadata_json": "{}",
            "conversation_id": "history-conversation-a",
            "message_id": "history-message-a",
            "parent_image_id": None,
            "created_at": utc_now(),
        }
    )
    db.add_reference(
        {
            "id": "history-reference-a",
            "user_id": first_user,
            "filename": "source.png",
            "path": str(tmp_path / "source.png"),
            "role": "source",
            "note": "",
            "width": 512,
            "height": 512,
            "bytes": 12,
            "created_at": utc_now(),
        }
    )
    db.add_job("history-job-a", "generate", {"prompt": "delete"}, user_id=first_user)
    db.finish_job("history-job-a", "failed", "test failure")
    db.add_conversation("history-conversation-b", "Keep this history", second_user)

    deleted = db.clear_user_history(first_user)

    assert deleted["conversation_count"] == 1
    assert deleted["message_count"] == 1
    assert deleted["asset_count"] == 0
    assert deleted["job_count"] == 1
    assert deleted["image_count"] == 1
    assert deleted["reference_count"] == 1
    assert db.list_conversations(user_id=first_user) == []
    assert db.list_images(user_id=first_user) == []
    assert db.list_references(first_user) == []
    assert db.list_jobs(user_id=first_user) == []
    assert [row["id"] for row in db.list_conversations(user_id=second_user)] == ["history-conversation-b"]


def test_clear_user_history_refuses_active_jobs(tmp_path):
    db = Database(tmp_path / "clear-active-history.sqlite3")
    db.initialize()
    user_id = db.create_user("history-active", "hash")
    db.add_job("history-active-job", "generate", {"prompt": "busy"}, user_id=user_id)

    result = db.clear_user_history(user_id)

    assert result["active_jobs"] == 1
    assert db.get_job("history-active-job", user_id)["status"] == "queued"


def test_legacy_unowned_rows_require_explicit_owner_when_multiple_users_exist(tmp_path):
    db = Database(tmp_path / "legacy-ownership.sqlite3")
    db.initialize()
    first_user = db.create_user("first", "hash-one")
    second_user = db.create_user("second", "hash-two")
    db.add_conversation("legacy-conversation", "Legacy data", first_user)
    with db.lock, db.conn:
        db.conn.execute("UPDATE conversations SET user_id = NULL WHERE id = ?", ("legacy-conversation",))

    with pytest.raises(ValueError, match="MOSS_LEGACY_OWNER_USERNAME"):
        db.initialize()

    db.initialize(legacy_owner_username="second")
    assert db.get_conversation("legacy-conversation", second_user)
    assert db.get_conversation("legacy-conversation", first_user) is None


def test_legacy_owner_username_must_match_an_existing_account(tmp_path):
    db = Database(tmp_path / "legacy-invalid-owner.sqlite3")
    db.initialize()
    db.create_user("first", "hash-one")
    db.create_user("second", "hash-two")
    db.add_conversation("legacy-conversation", "Legacy data")
    with db.lock, db.conn:
        db.conn.execute("UPDATE conversations SET user_id = NULL WHERE id = ?", ("legacy-conversation",))

    with pytest.raises(ValueError, match="does not match"):
        db.initialize(legacy_owner_username="missing")


def test_expired_sessions_are_removed_on_lookup_and_startup(tmp_path):
    db = Database(tmp_path / "expired-sessions.sqlite3")
    db.initialize()
    user_id = db.create_user("session-user", "hash")
    db.create_session(user_id, "expired-token", "csrf", days=1)
    with db.lock, db.conn:
        db.conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            ("2000-01-01T00:00:00+00:00", "expired-token"),
        )

    assert db.get_session("expired-token") is None
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

    db.create_session(user_id, "startup-expired-token", "csrf", days=1)
    with db.lock, db.conn:
        db.conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            ("2000-01-01T00:00:00+00:00", "startup-expired-token"),
        )
    db.initialize()
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
