from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def initialize(self, legacy_owner_username: str | None = None) -> None:
        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reference_images (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    role TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    bytes INTEGER NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    job_id TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_assets (
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    asset_type TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(message_id, asset_type, asset_id)
                );
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    conversation_id TEXT,
                    message_id TEXT,
                    parent_image_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    attempt_id TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
                """
            )
            self.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),))
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(reference_images)").fetchall()}
            if "sort_order" not in columns:
                self.conn.execute("ALTER TABLE reference_images ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            if "user_id" not in columns:
                self.conn.execute("ALTER TABLE reference_images ADD COLUMN user_id INTEGER REFERENCES users(id)")
            conversation_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "user_id" not in conversation_columns:
                self.conn.execute("ALTER TABLE conversations ADD COLUMN user_id INTEGER REFERENCES users(id)")
            job_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "progress_json" not in job_columns:
                self.conn.execute("ALTER TABLE jobs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'")
            if "user_id" not in job_columns:
                self.conn.execute("ALTER TABLE jobs ADD COLUMN user_id INTEGER REFERENCES users(id)")
            if "attempt_id" not in job_columns:
                self.conn.execute("ALTER TABLE jobs ADD COLUMN attempt_id TEXT")
            image_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(images)").fetchall()}
            if "user_id" not in image_columns:
                self.conn.execute("ALTER TABLE images ADD COLUMN user_id INTEGER REFERENCES users(id)")
            if "favorite" not in image_columns:
                self.conn.execute("ALTER TABLE images ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
            for column, definition in {
                "conversation_id": "TEXT",
                "message_id": "TEXT",
                "parent_image_id": "TEXT",
            }.items():
                if column not in image_columns:
                    self.conn.execute(f"ALTER TABLE images ADD COLUMN {column} {definition}")
            # Create indexes only after legacy databases have received the new
            # nullable image relationship columns.
            self.conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS conversations_updated ON conversations(archived, updated_at DESC);
                CREATE INDEX IF NOT EXISTS conversations_user_updated ON conversations(user_id, archived, updated_at DESC);
                CREATE INDEX IF NOT EXISTS messages_conversation_created ON messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS images_conversation_created ON images(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS images_user_created ON images(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS references_user_order ON reference_images(user_id, sort_order, created_at DESC);
                CREATE INDEX IF NOT EXISTS jobs_user_status_created ON jobs(user_id, status, created_at DESC);
                """
            )
            legacy_tables = ("reference_images", "conversations", "images", "jobs")
            null_ownership = {
                table: int(self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL").fetchone()[0])
                for table in legacy_tables
            }
            if any(null_ownership.values()):
                users = self.conn.execute("SELECT id, username FROM users ORDER BY id").fetchall()
                owner_id = None
                if legacy_owner_username and legacy_owner_username.strip():
                    owner = next(
                        (row for row in users if row["username"].casefold() == legacy_owner_username.strip().casefold()),
                        None,
                    )
                    if owner is None:
                        raise ValueError(
                            "MOSS_LEGACY_OWNER_USERNAME does not match an existing account; "
                            "legacy rows were not reassigned"
                        )
                    owner_id = int(owner["id"])
                elif len(users) == 1:
                    owner_id = int(users[0]["id"])
                elif len(users) > 1:
                    affected = ", ".join(table for table, count in null_ownership.items() if count)
                    raise ValueError(
                        "legacy records have no account owner and multiple accounts exist "
                        f"(tables: {affected}); set MOSS_LEGACY_OWNER_USERNAME to an explicit owner"
                    )
                if owner_id is not None:
                    for table in legacy_tables:
                        self.conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (owner_id,))

    def user_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, username: str, password_hash: str) -> int:
        with self.lock, self.conn:
            first_user = self.conn.execute("SELECT MIN(id) FROM users").fetchone()[0]
            cur = self.conn.execute(
                "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, utc_now()),
            )
            user_id = int(cur.lastrowid)
            if first_user is None:
                for table in ("reference_images", "conversations", "images", "jobs"):
                    self.conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,))
            return user_id

    def get_user(self, username: str):
        with self.lock:
            return self.conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def create_session(self, user_id: int, token: str, csrf_token: str, days: int) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO sessions(token, user_id, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (token, user_id, csrf_token, expires, utc_now()),
            )

    def get_session(self, token: str):
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT sessions.*, users.username FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token = ?",
                (token,),
            ).fetchone()
            if not row:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                return None
            return row

    def delete_session(self, token: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def add_conversation(self, conversation_id: str, title: str = "New conversation", user_id: int | None = None) -> None:
        now = utc_now()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO conversations(id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, user_id, title[:200] or "New conversation", now, now),
            )

    def list_conversations(self, limit: int = 100, search: str = "", user_id: int | None = None):
        with self.lock:
            owner_sql = " AND c.user_id = ?" if user_id is not None else ""
            if search.strip():
                term = search.strip()
                params: list[Any] = [term, term]
                if user_id is not None:
                    params.append(user_id)
                params.append(limit)
                return self.conn.execute(
                    "SELECT c.* FROM conversations AS c "
                    "WHERE c.archived = 0 AND ("
                    "instr(lower(c.title), lower(?)) > 0 OR EXISTS ("
                    "SELECT 1 FROM messages AS m WHERE m.conversation_id = c.id "
                    "AND instr(lower(m.content), lower(?)) > 0"
                    "))" + owner_sql + " ORDER BY c.updated_at DESC LIMIT ?",
                    params,
                ).fetchall()
            if user_id is None:
                return self.conn.execute(
                    "SELECT * FROM conversations WHERE archived = 0 ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return self.conn.execute(
                "SELECT * FROM conversations WHERE archived = 0 AND user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()

    def get_conversation(self, conversation_id: str, user_id: int | None = None):
        with self.lock:
            if user_id is None:
                return self.conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            return self.conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
            ).fetchone()

    def update_conversation(self, conversation_id: str, title: str | None = None, archived: bool | None = None, user_id: int | None = None) -> bool:
        updates, values = [], []
        if title is not None:
            updates.append("title = ?")
            values.append(title[:200] or "New conversation")
        if archived is not None:
            updates.append("archived = ?")
            values.append(1 if archived else 0)
        if not updates:
            return False
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(conversation_id)
        owner_sql = ""
        if user_id is not None:
            owner_sql = " AND user_id = ?"
            values.append(user_id)
        with self.lock, self.conn:
            cur = self.conn.execute(f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?{owner_sql}", values)
            return cur.rowcount == 1

    def add_message(self, values: dict[str, Any]) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO messages(id, conversation_id, role, content, status, plan_json, job_id, error, created_at) "
                "VALUES (:id, :conversation_id, :role, :content, :status, :plan_json, :job_id, :error, :created_at)",
                {
                    "status": "completed",
                    "plan_json": "{}",
                    "job_id": None,
                    "error": "",
                    **values,
                },
            )
            self.conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (utc_now(), values["conversation_id"]))

    def get_message(self, message_id: str):
        with self.lock:
            return self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()

    def get_job(self, job_id: str, user_id: int | None = None):
        with self.lock:
            if user_id is None:
                return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self.conn.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()

    def list_messages(self, conversation_id: str):
        with self.lock:
            return self.conn.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)).fetchall()

    def update_message(self, message_id: str, status: str | None = None, job_id: str | None = None, error: str | None = None) -> bool:
        updates, values = [], []
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if job_id is not None:
            updates.append("job_id = ?")
            values.append(job_id)
        if error is not None:
            updates.append("error = ?")
            values.append(error[:4000])
        if not updates:
            return False
        values.append(message_id)
        with self.lock, self.conn:
            cur = self.conn.execute(f"UPDATE messages SET {', '.join(updates)} WHERE id = ?", values)
            return cur.rowcount == 1

    def delete_message(self, message_id: str) -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            return cur.rowcount == 1

    def add_message_asset(self, message_id: str, asset_type: str, asset_id: str, ordinal: int = 0, role: str = "", note: str = "") -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO message_assets(message_id, asset_type, asset_id, ordinal, role, note) VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, asset_type, asset_id, ordinal, role[:32], note[:500]),
            )

    def finalize_generation(
        self,
        image_values: list[dict[str, Any]],
        *,
        message_id: str | None = None,
        assistant_message: dict[str, Any] | None = None,
        output_ids: list[str] | None = None,
    ) -> None:
        """Commit a generation's rows as one SQLite transaction.

        Files are written by the inference adapter before this method is called.
        Callers own cleanup when this transaction raises; no partial database
        rows are allowed to survive a failed finalization.
        """
        with self.lock, self.conn:
            columns = [
                "id",
                "user_id",
                "path",
                "kind",
                "prompt",
                "metadata_json",
                "created_at",
                "conversation_id",
                "message_id",
                "parent_image_id",
            ]
            for values in image_values:
                self.conn.execute(
                    f"INSERT INTO images({', '.join(columns)}) VALUES ({', '.join(':' + column for column in columns)})",
                    {column: values.get(column) for column in columns},
                )

            if message_id:
                updated = self.conn.execute(
                    "UPDATE messages SET status = 'completed', error = '' WHERE id = ?",
                    (message_id,),
                )
                if updated.rowcount != 1:
                    raise ValueError(f"generation message does not exist: {message_id}")

            if assistant_message:
                self.conn.execute(
                    "INSERT INTO messages(id, conversation_id, role, content, status, plan_json, job_id, error, created_at) "
                    "VALUES (:id, :conversation_id, :role, :content, :status, :plan_json, :job_id, :error, :created_at)",
                    {
                        "status": "completed",
                        "plan_json": "{}",
                        "job_id": None,
                        "error": "",
                        **assistant_message,
                    },
                )
                self.conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (utc_now(), assistant_message["conversation_id"]),
                )
                for ordinal, output_id in enumerate(output_ids or []):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO message_assets(message_id, asset_type, asset_id, ordinal, role, note) "
                        "VALUES (?, 'image', ?, ?, 'output', '')",
                        (assistant_message["id"], output_id, ordinal),
                    )

    def list_message_assets(self, message_id: str):
        with self.lock:
            return self.conn.execute("SELECT * FROM message_assets WHERE message_id = ? ORDER BY ordinal", (message_id,)).fetchall()

    def list_conversation_images(self, conversation_id: str, user_id: int | None = None):
        with self.lock:
            if user_id is None:
                return self.conn.execute("SELECT * FROM images WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)).fetchall()
            return self.conn.execute(
                "SELECT * FROM images WHERE conversation_id = ? AND user_id = ? ORDER BY created_at",
                (conversation_id, user_id),
            ).fetchall()

    def add_reference(self, values: dict[str, Any]) -> None:
        with self.lock, self.conn:
            next_order = self.conn.execute("SELECT COALESCE(MIN(sort_order), 0) - 1 FROM reference_images").fetchone()[0]
            self.conn.execute(
                "INSERT INTO reference_images(id, user_id, filename, path, role, note, width, height, bytes, sort_order, created_at) "
                "VALUES (:id, :user_id, :filename, :path, :role, :note, :width, :height, :bytes, :sort_order, :created_at)",
                {
                    **values,
                    "user_id": values.get("user_id"),
                    "sort_order": values.get("sort_order", next_order),
                },
            )

    def list_references(self, user_id: int | None = None):
        with self.lock:
            if user_id is None:
                return self.conn.execute("SELECT * FROM reference_images ORDER BY sort_order, created_at DESC").fetchall()
            return self.conn.execute(
                "SELECT * FROM reference_images WHERE user_id = ? ORDER BY sort_order, created_at DESC", (user_id,)
            ).fetchall()

    def reorder_references(self, reference_ids: list[str], user_id: int | None = None) -> bool:
        with self.lock, self.conn:
            if user_id is None:
                rows = self.conn.execute("SELECT id FROM reference_images").fetchall()
            else:
                rows = self.conn.execute("SELECT id FROM reference_images WHERE user_id = ?", (user_id,)).fetchall()
            existing = {row[0] for row in rows}
            if len(reference_ids) != len(existing) or set(reference_ids) != existing:
                return False
            owner_sql = "" if user_id is None else " AND user_id = ?"
            values = [
                (position, reference_id) if user_id is None else (position, reference_id, user_id)
                for position, reference_id in enumerate(reference_ids)
            ]
            self.conn.executemany(
                f"UPDATE reference_images SET sort_order = ? WHERE id = ?{owner_sql}", values
            )
            return True

    def get_reference(self, reference_id: str, user_id: int | None = None):
        with self.lock:
            if user_id is None:
                return self.conn.execute("SELECT * FROM reference_images WHERE id = ?", (reference_id,)).fetchone()
            return self.conn.execute(
                "SELECT * FROM reference_images WHERE id = ? AND user_id = ?", (reference_id, user_id)
            ).fetchone()

    def delete_reference(self, reference_id: str, user_id: int | None = None):
        with self.lock, self.conn:
            if user_id is None:
                row = self.conn.execute("SELECT * FROM reference_images WHERE id = ?", (reference_id,)).fetchone()
                self.conn.execute("DELETE FROM reference_images WHERE id = ?", (reference_id,))
            else:
                row = self.conn.execute(
                    "SELECT * FROM reference_images WHERE id = ? AND user_id = ?", (reference_id, user_id)
                ).fetchone()
                self.conn.execute("DELETE FROM reference_images WHERE id = ? AND user_id = ?", (reference_id, user_id))
            return row

    def add_image(self, values: dict[str, Any]) -> None:
        with self.lock, self.conn:
            columns = ["id", "user_id", "path", "kind", "prompt", "metadata_json", "created_at", "conversation_id", "message_id", "parent_image_id"]
            self.conn.execute(
                f"INSERT INTO images({', '.join(columns)}) VALUES ({', '.join(':' + column for column in columns)})",
                {column: values.get(column) for column in columns},
            )

    def list_images(self, limit: int | None = 100, user_id: int | None = None):
        with self.lock:
            limit_sql = " LIMIT ?" if limit is not None else ""
            if user_id is None:
                params = () if limit is None else (limit,)
                return self.conn.execute(f"SELECT * FROM images ORDER BY created_at DESC{limit_sql}", params).fetchall()
            params = (user_id,) if limit is None else (user_id, limit)
            return self.conn.execute(
                f"SELECT * FROM images WHERE user_id = ? ORDER BY created_at DESC{limit_sql}", params
            ).fetchall()

    def set_image_favorite(self, image_id: str, favorite: bool, user_id: int | None = None) -> bool:
        with self.lock, self.conn:
            if user_id is None:
                cur = self.conn.execute("UPDATE images SET favorite = ? WHERE id = ?", (1 if favorite else 0, image_id))
            else:
                cur = self.conn.execute(
                    "UPDATE images SET favorite = ? WHERE id = ? AND user_id = ?",
                    (1 if favorite else 0, image_id, user_id),
                )
            return cur.rowcount == 1

    def delete_image(self, image_id: str, user_id: int | None = None):
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT * FROM images WHERE id = ?" + (" AND user_id = ?" if user_id is not None else ""),
                (image_id,) if user_id is None else (image_id, user_id),
            ).fetchone()
            if row:
                self.conn.execute(
                    "DELETE FROM images WHERE id = ?" + (" AND user_id = ?" if user_id is not None else ""),
                    (image_id,) if user_id is None else (image_id, user_id),
                )
            return row

    def list_media_paths(self) -> list[str]:
        """Return every persisted image/reference path for reconciliation."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT path FROM images UNION SELECT path FROM reference_images"
            ).fetchall()
            return [str(row[0]) for row in rows if row[0]]

    def get_image(self, image_id: str, user_id: int | None = None):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM images WHERE id = ?" + (" AND user_id = ?" if user_id is not None else ""),
                (image_id,) if user_id is None else (image_id, user_id),
            ).fetchone()

    def add_job(self, job_id: str, kind: str, params: dict[str, Any], max_queue: int | None = None, user_id: int | None = None) -> bool:
        with self.lock, self.conn:
            if max_queue is not None:
                active = self.conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running', 'cancelling')"
                ).fetchone()[0]
                if active >= max_queue:
                    return False
            self.conn.execute(
                "INSERT INTO jobs(id, user_id, kind, status, params_json, progress_json, created_at) VALUES (?, ?, ?, 'queued', ?, '{}', ?)",
                (job_id, user_id, kind, json.dumps(params, ensure_ascii=False), utc_now()),
            )
            return True

    def queued_count(self, user_id: int | None = None) -> int:
        with self.lock:
            if user_id is None:
                return int(self.conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running', 'cancelling')").fetchone()[0])
            return int(self.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE user_id = ? AND status IN ('queued', 'running', 'cancelling')",
                (user_id,),
            ).fetchone()[0])

    def claim_next_job(self):
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            attempt_id = str(uuid.uuid4())
            self.conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, attempt_id = ? WHERE id = ?",
                (utc_now(), attempt_id, row["id"]),
            )
            claimed = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            return dict(claimed)

    def finish_job(self, job_id: str, status: str, error: str = "") -> None:
        progress = {"stage": status, "known": True}
        if status == "completed":
            progress["percent"] = 100
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE jobs SET status = ?, error = ?, progress_json = ?, finished_at = ? WHERE id = ?",
                (status, error[:4000], json.dumps(progress), utc_now(), job_id),
            )

    def update_job_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE jobs SET progress_json = ? WHERE id = ? AND status = 'running'",
                (json.dumps(progress, ensure_ascii=False), job_id),
            )

    def recover_running_jobs(self) -> list[dict[str, Any]]:
        with self.lock, self.conn:
            rows = [
                dict(row)
                for row in self.conn.execute("SELECT * FROM jobs WHERE status IN ('running', 'cancelling')").fetchall()
            ]
            self.conn.execute(
                "UPDATE jobs SET status = 'failed', error = 'Application restarted while this job was running', progress_json = ?, finished_at = ? "
                "WHERE status IN ('running', 'cancelling')",
                (json.dumps({"stage": "failed", "reason": "application_restart"}), utc_now()),
            )
            return rows

    def is_job_cancel_requested(self, job_id: str) -> bool:
        with self.lock:
            row = self.conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return bool(row and row[0] == "cancelling")

    def request_job_cancel(self, job_id: str, user_id: int | None = None) -> bool:
        with self.lock, self.conn:
            owner_sql = "" if user_id is None else " AND user_id = ?"
            cur = self.conn.execute(
                f"UPDATE jobs SET status = 'cancelling', error = 'Cancellation requested' WHERE id = ? AND status = 'running'{owner_sql}",
                (job_id,) if user_id is None else (job_id, user_id),
            )
            return cur.rowcount == 1

    def list_jobs(self, limit: int = 50, user_id: int | None = None):
        with self.lock:
            if user_id is None:
                return self.conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return self.conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
            ).fetchall()

    def clear_user_history(self, user_id: int) -> dict[str, Any]:
        """Delete one account's user-created history and return its media paths.

        Model caches, users, sessions, and application configuration are
        deliberately outside this operation. The caller owns filesystem
        cleanup and must verify that returned paths are still not referenced by
        another account before unlinking them.
        """
        with self.lock, self.conn:
            active_jobs = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE user_id = ? AND status IN ('queued', 'running', 'cancelling')",
                    (user_id,),
                ).fetchone()[0]
            )
            if active_jobs:
                return {"active_jobs": active_jobs}

            conversation_count = int(
                self.conn.execute("SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)).fetchone()[0]
            )
            message_count = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id IN "
                    "(SELECT id FROM conversations WHERE user_id = ?)",
                    (user_id,),
                ).fetchone()[0]
            )
            asset_count = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM message_assets WHERE message_id IN "
                    "(SELECT m.id FROM messages AS m JOIN conversations AS c ON c.id = m.conversation_id WHERE c.user_id = ?)",
                    (user_id,),
                ).fetchone()[0]
            )
            image_rows = self.conn.execute("SELECT path FROM images WHERE user_id = ?", (user_id,)).fetchall()
            reference_rows = self.conn.execute("SELECT path FROM reference_images WHERE user_id = ?", (user_id,)).fetchall()
            job_count = int(self.conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id = ?", (user_id,)).fetchone()[0])
            image_count = len(image_rows)
            reference_count = len(reference_rows)

            self.conn.execute("DELETE FROM jobs WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM images WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM reference_images WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
            return {
                "active_jobs": 0,
                "conversation_count": conversation_count,
                "message_count": message_count,
                "asset_count": asset_count,
                "job_count": job_count,
                "image_count": image_count,
                "reference_count": reference_count,
                "paths": list({str(row["path"]) for row in (*image_rows, *reference_rows) if row["path"]}),
            }

    def cancel_job(self, job_id: str, user_id: int | None = None) -> bool:
        with self.lock, self.conn:
            owner_sql = "" if user_id is None else " AND user_id = ?"
            cur = self.conn.execute(
                f"UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'queued'{owner_sql}",
                (utc_now(), job_id) if user_id is None else (utc_now(), job_id, user_id),
            )
            return cur.rowcount == 1

    def retry_job(self, job_id: str, max_queue: int | None = None, user_id: int | None = None) -> str:
        with self.lock, self.conn:
            if max_queue is not None:
                active = self.conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running', 'cancelling')"
                ).fetchone()[0]
                if active >= max_queue:
                    return "full"
            cur = self.conn.execute(
                "UPDATE jobs SET status = 'queued', error = '', progress_json = '{}', started_at = NULL, finished_at = NULL "
                ", attempt_id = NULL "
                "WHERE id = ? AND status IN ('failed', 'cancelled')" + (" AND user_id = ?" if user_id is not None else ""),
                (job_id,) if user_id is None else (job_id, user_id),
            )
            return "queued" if cur.rowcount == 1 else "not_retryable"
