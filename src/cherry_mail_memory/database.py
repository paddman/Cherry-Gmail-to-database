from __future__ import annotations

import hashlib
import json
import shlex
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from cherry_mail_memory.mime_utils import chunk_message, message_content_hash
from cherry_mail_memory.models import Account, ParsedMessage, RetrievedChunk


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    token_path TEXT NOT NULL,
                    history_id TEXT,
                    last_sync_at TEXT,
                    sync_status TEXT NOT NULL DEFAULT 'idle',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    account_id INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    history_id TEXT NOT NULL,
                    internal_date INTEGER NOT NULL DEFAULT 0,
                    rfc822_message_id TEXT NOT NULL DEFAULT '',
                    sender TEXT NOT NULL DEFAULT '',
                    recipients TEXT NOT NULL DEFAULT '',
                    cc TEXT NOT NULL DEFAULT '',
                    bcc TEXT NOT NULL DEFAULT '',
                    reply_to TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    snippet TEXT NOT NULL DEFAULT '',
                    body_text TEXT NOT NULL DEFAULT '',
                    body_html TEXT NOT NULL DEFAULT '',
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    size_estimate INTEGER NOT NULL DEFAULT 0,
                    raw_mime BLOB,
                    raw_compressed INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    has_attachments INTEGER NOT NULL DEFAULT 0,
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, message_id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_account_date
                    ON messages(account_id, is_deleted, internal_date DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_thread
                    ON messages(account_id, thread_id);
                CREATE INDEX IF NOT EXISTS idx_messages_sender
                    ON messages(account_id, sender);

                CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    attachment_index INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0,
                    content_id TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    downloaded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, message_id, attachment_index),
                    FOREIGN KEY (account_id, message_id)
                        REFERENCES messages(account_id, message_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS email_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_provider TEXT,
                    embedding_model TEXT,
                    embedding BLOB,
                    dims INTEGER,
                    indexed_at TEXT,
                    UNIQUE(account_id, message_id, chunk_index),
                    FOREIGN KEY (account_id, message_id)
                        REFERENCES messages(account_id, message_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_account_message
                    ON email_chunks(account_id, message_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding
                    ON email_chunks(account_id, embedding_provider, embedding_model);

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT 'New chat',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                );
                """
            )
            self._ensure_fts(connection)
            connection.execute(
                "INSERT INTO app_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def _ensure_fts(self, connection: sqlite3.Connection) -> None:
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_fts'"
        ).fetchone()
        if existing:
            return

        tokenizer = "trigram"
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE message_fts USING fts5("
                "account_id UNINDEXED, message_id UNINDEXED, subject, sender, recipients, body_text, "
                "tokenize='trigram')"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE chunk_fts USING fts5("
                "chunk_id UNINDEXED, account_id UNINDEXED, message_id UNINDEXED, text, "
                "tokenize='trigram')"
            )
        except sqlite3.OperationalError:
            connection.execute("DROP TABLE IF EXISTS message_fts")
            connection.execute("DROP TABLE IF EXISTS chunk_fts")
            tokenizer = "unicode61"
            connection.execute(
                "CREATE VIRTUAL TABLE message_fts USING fts5("
                "account_id UNINDEXED, message_id UNINDEXED, subject, sender, recipients, body_text, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE chunk_fts USING fts5("
                "chunk_id UNINDEXED, account_id UNINDEXED, message_id UNINDEXED, text, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
        connection.execute(
            "INSERT INTO app_meta(key, value) VALUES('fts_tokenizer', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (tokenizer,),
        )

    def get_meta(self, key: str, default: str = "") -> str:
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def upsert_account(self, email: str, token_path: str) -> int:
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO accounts(email, token_path, created_at, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    token_path=excluded.token_path,
                    updated_at=excluded.updated_at,
                    last_error=NULL
                """,
                (email.strip(), token_path, now, now),
            )
            row = connection.execute(
                "SELECT id FROM accounts WHERE email=? COLLATE NOCASE", (email.strip(),)
            ).fetchone()
            if not row:
                raise RuntimeError("Unable to save Gmail account")
            return int(row["id"])

    def list_accounts(self) -> list[Account]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM accounts ORDER BY email COLLATE NOCASE").fetchall()
        return [Account(**dict(row)) for row in rows]

    def get_account(self, account_id: int) -> Account:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown account id: {account_id}")
        return Account(**dict(row))

    def clear_account_history(self, account_id: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE accounts SET history_id=NULL, updated_at=? WHERE id=?",
                (utc_now(), account_id),
            )

    def update_account_sync(
        self,
        account_id: int,
        *,
        status: str,
        error: str | None = None,
        history_id: str | None = None,
        completed: bool = False,
    ) -> None:
        now = utc_now()
        assignments = ["sync_status=?", "last_error=?", "updated_at=?"]
        values: list[Any] = [status, error, now]
        if history_id is not None:
            assignments.append("history_id=?")
            values.append(str(history_id))
        if completed:
            assignments.append("last_sync_at=?")
            values.append(now)
        values.append(account_id)
        with self.connection() as connection:
            connection.execute(
                f"UPDATE accounts SET {', '.join(assignments)} WHERE id=?", values
            )

    @staticmethod
    def _encode_raw(raw_bytes: bytes | None) -> tuple[bytes | None, int]:
        if not raw_bytes:
            return None, 0
        compressed = zlib.compress(raw_bytes, level=1)
        if len(compressed) < len(raw_bytes) * 0.95:
            return compressed, 1
        return raw_bytes, 0

    def upsert_messages(
        self,
        account_id: int,
        messages: Sequence[ParsedMessage],
        *,
        chunk_size: int,
        chunk_overlap: int,
        store_raw_mime: bool,
    ) -> int:
        if not messages:
            return 0
        now = utc_now()
        changed = 0
        with self.connection() as connection:
            for message in messages:
                content_hash = message_content_hash(message)
                existing = connection.execute(
                    "SELECT content_hash, is_deleted FROM messages "
                    "WHERE account_id=? AND message_id=?",
                    (account_id, message.message_id),
                ).fetchone()
                content_changed = not existing or existing["content_hash"] != content_hash
                was_deleted = bool(existing and existing["is_deleted"])

                raw_blob, raw_compressed = (
                    self._encode_raw(message.raw_bytes) if store_raw_mime else (None, 0)
                )
                connection.execute(
                    """
                    INSERT INTO messages(
                        account_id, message_id, thread_id, history_id, internal_date,
                        rfc822_message_id, sender, recipients, cc, bcc, reply_to,
                        subject, snippet, body_text, body_html, labels_json,
                        size_estimate, raw_mime, raw_compressed, content_hash,
                        has_attachments, is_deleted, created_at, updated_at
                    ) VALUES(
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?
                    )
                    ON CONFLICT(account_id, message_id) DO UPDATE SET
                        thread_id=excluded.thread_id,
                        history_id=excluded.history_id,
                        internal_date=excluded.internal_date,
                        rfc822_message_id=excluded.rfc822_message_id,
                        sender=excluded.sender,
                        recipients=excluded.recipients,
                        cc=excluded.cc,
                        bcc=excluded.bcc,
                        reply_to=excluded.reply_to,
                        subject=excluded.subject,
                        snippet=excluded.snippet,
                        body_text=excluded.body_text,
                        body_html=excluded.body_html,
                        labels_json=excluded.labels_json,
                        size_estimate=excluded.size_estimate,
                        raw_mime=COALESCE(excluded.raw_mime, messages.raw_mime),
                        raw_compressed=CASE WHEN excluded.raw_mime IS NULL
                            THEN messages.raw_compressed ELSE excluded.raw_compressed END,
                        content_hash=excluded.content_hash,
                        has_attachments=excluded.has_attachments,
                        is_deleted=0,
                        updated_at=excluded.updated_at
                    """,
                    (
                        account_id,
                        message.message_id,
                        message.thread_id,
                        message.history_id,
                        message.internal_date,
                        message.rfc822_message_id,
                        message.sender,
                        message.recipients,
                        message.cc,
                        message.bcc,
                        message.reply_to,
                        message.subject,
                        message.snippet,
                        message.body_text,
                        message.body_html,
                        json.dumps(message.label_ids, ensure_ascii=False),
                        message.size_estimate,
                        sqlite3.Binary(raw_blob) if raw_blob is not None else None,
                        raw_compressed,
                        content_hash,
                        int(bool(message.attachments)),
                        now,
                        now,
                    ),
                )

                if content_changed:
                    changed += 1
                    previous_attachments = {
                        (row["filename"], int(row["size"]), row["content_id"]): dict(row)
                        for row in connection.execute(
                            "SELECT * FROM attachments WHERE account_id=? AND message_id=?",
                            (account_id, message.message_id),
                        ).fetchall()
                    }
                    connection.execute(
                        "DELETE FROM attachments WHERE account_id=? AND message_id=?",
                        (account_id, message.message_id),
                    )
                    for index, attachment in enumerate(message.attachments):
                        previous = previous_attachments.get(
                            (attachment.filename, attachment.size, attachment.content_id)
                        )
                        file_path = attachment.file_path or (
                            str(previous["file_path"]) if previous else ""
                        )
                        downloaded = attachment.downloaded or bool(
                            previous and previous["downloaded"]
                        )
                        connection.execute(
                            """
                            INSERT INTO attachments(
                                account_id, message_id, attachment_index, filename,
                                mime_type, size, content_id, file_path, downloaded, created_at
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                account_id,
                                message.message_id,
                                index,
                                attachment.filename,
                                attachment.mime_type,
                                attachment.size,
                                attachment.content_id,
                                file_path,
                                int(downloaded),
                                now,
                            ),
                        )

                    old_chunk_ids = [
                        int(row["id"])
                        for row in connection.execute(
                            "SELECT id FROM email_chunks WHERE account_id=? AND message_id=?",
                            (account_id, message.message_id),
                        ).fetchall()
                    ]
                    for chunk_id in old_chunk_ids:
                        connection.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk_id,))
                    connection.execute(
                        "DELETE FROM email_chunks WHERE account_id=? AND message_id=?",
                        (account_id, message.message_id),
                    )
                    for index, text in enumerate(
                        chunk_message(message, chunk_size=chunk_size, overlap=chunk_overlap)
                    ):
                        chunk_hash = hashlib.sha256(
                            text.encode("utf-8", errors="replace")
                        ).hexdigest()
                        cursor = connection.execute(
                            """
                            INSERT INTO email_chunks(
                                account_id, message_id, chunk_index, text, content_hash
                            ) VALUES(?, ?, ?, ?, ?)
                            """,
                            (account_id, message.message_id, index, text, chunk_hash),
                        )
                        chunk_id = int(cursor.lastrowid)
                        connection.execute(
                            "INSERT INTO chunk_fts(chunk_id, account_id, message_id, text) "
                            "VALUES(?, ?, ?, ?)",
                            (chunk_id, account_id, message.message_id, text),
                        )

                connection.execute(
                    "DELETE FROM message_fts WHERE account_id=? AND message_id=?",
                    (account_id, message.message_id),
                )
                connection.execute(
                    "INSERT INTO message_fts(account_id, message_id, subject, sender, recipients, body_text) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        account_id,
                        message.message_id,
                        message.subject,
                        message.sender,
                        " ".join(filter(None, (message.recipients, message.cc, message.bcc))),
                        message.body_text,
                    ),
                )

                if not content_changed:
                    for index, attachment in enumerate(message.attachments):
                        if attachment.downloaded and attachment.file_path:
                            connection.execute(
                                "UPDATE attachments SET file_path=?, downloaded=1 "
                                "WHERE account_id=? AND message_id=? AND attachment_index=?",
                                (
                                    attachment.file_path,
                                    account_id,
                                    message.message_id,
                                    index,
                                ),
                            )

                if was_deleted and not content_changed:
                    chunks = connection.execute(
                        "SELECT id, text FROM email_chunks WHERE account_id=? AND message_id=?",
                        (account_id, message.message_id),
                    ).fetchall()
                    for chunk in chunks:
                        connection.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk["id"],))
                        connection.execute(
                            "INSERT INTO chunk_fts(chunk_id, account_id, message_id, text) "
                            "VALUES(?, ?, ?, ?)",
                            (chunk["id"], account_id, message.message_id, chunk["text"]),
                        )
        return changed

    def existing_message_ids(self, account_id: int, message_ids: Iterable[str]) -> set[str]:
        ids = sorted({str(item) for item in message_ids if item})
        if not ids:
            return set()
        existing: set[str] = set()
        with self.connection() as connection:
            for offset in range(0, len(ids), 500):
                group = ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in group)
                rows = connection.execute(
                    f"SELECT message_id FROM messages WHERE account_id=? "
                    f"AND message_id IN ({placeholders})",
                    [account_id, *group],
                ).fetchall()
                existing.update(str(row["message_id"]) for row in rows)
        return existing

    def update_message_labels(
        self, account_id: int, updates: Sequence[dict[str, Any]]
    ) -> int:
        if not updates:
            return 0
        now = utc_now()
        changed = 0
        with self.connection() as connection:
            for update in updates:
                message_id = str(update.get("id", ""))
                if not message_id:
                    continue
                existing = connection.execute(
                    "SELECT is_deleted, subject, sender, recipients, cc, bcc, body_text "
                    "FROM messages WHERE account_id=? AND message_id=?",
                    (account_id, message_id),
                ).fetchone()
                if not existing:
                    continue
                cursor = connection.execute(
                    "UPDATE messages SET labels_json=?, history_id=?, is_deleted=0, updated_at=? "
                    "WHERE account_id=? AND message_id=?",
                    (
                        json.dumps(update.get("labelIds", []), ensure_ascii=False),
                        str(update.get("historyId", "")),
                        now,
                        account_id,
                        message_id,
                    ),
                )
                changed += cursor.rowcount
                if existing["is_deleted"]:
                    connection.execute(
                        "DELETE FROM message_fts WHERE account_id=? AND message_id=?",
                        (account_id, message_id),
                    )
                    connection.execute(
                        "INSERT INTO message_fts(account_id, message_id, subject, sender, recipients, body_text) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (
                            account_id,
                            message_id,
                            existing["subject"],
                            existing["sender"],
                            " ".join(
                                filter(
                                    None,
                                    (
                                        existing["recipients"],
                                        existing["cc"],
                                        existing["bcc"],
                                    ),
                                )
                            ),
                            existing["body_text"],
                        ),
                    )
                    chunks = connection.execute(
                        "SELECT id, text FROM email_chunks WHERE account_id=? AND message_id=?",
                        (account_id, message_id),
                    ).fetchall()
                    for chunk in chunks:
                        connection.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk["id"],))
                        connection.execute(
                            "INSERT INTO chunk_fts(chunk_id, account_id, message_id, text) "
                            "VALUES(?, ?, ?, ?)",
                            (chunk["id"], account_id, message_id, chunk["text"]),
                        )
        return changed

    def mark_messages_deleted(self, account_id: int, message_ids: Iterable[str]) -> int:
        ids = sorted({str(item) for item in message_ids if item})
        if not ids:
            return 0
        now = utc_now()
        changed = 0
        with self.connection() as connection:
            for message_id in ids:
                cursor = connection.execute(
                    "UPDATE messages SET is_deleted=1, updated_at=? "
                    "WHERE account_id=? AND message_id=? AND is_deleted=0",
                    (now, account_id, message_id),
                )
                changed += cursor.rowcount
                connection.execute(
                    "DELETE FROM message_fts WHERE account_id=? AND message_id=?",
                    (account_id, message_id),
                )
                chunk_ids = connection.execute(
                    "SELECT id FROM email_chunks WHERE account_id=? AND message_id=?",
                    (account_id, message_id),
                ).fetchall()
                for row in chunk_ids:
                    connection.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (row["id"],))
        return changed

    def reconcile_messages(self, account_id: int, remote_ids: set[str]) -> int:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT message_id FROM messages WHERE account_id=? AND is_deleted=0",
                (account_id,),
            ).fetchall()
        local_ids = {str(row["message_id"]) for row in rows}
        return self.mark_messages_deleted(account_id, local_ids - remote_ids)

    def recent_messages(self, account_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT account_id, message_id, thread_id, internal_date, sender, recipients,
                       subject, snippet, labels_json, size_estimate, has_attachments
                FROM messages
                WHERE account_id=? AND is_deleted=0
                ORDER BY internal_date DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _parse_search(query: str) -> tuple[str, dict[str, Any]]:
        filters: dict[str, Any] = {}
        free: list[str] = []
        try:
            tokens = shlex.split(query)
        except ValueError:
            tokens = query.split()
        for token in tokens:
            prefix, separator, value = token.partition(":")
            key = prefix.lower()
            if separator and key in {"from", "to", "subject", "label", "after", "before"}:
                filters[key] = value.strip()
            else:
                free.append(token)
        return " ".join(free).strip(), filters

    @staticmethod
    def _fts_query(text: str) -> str:
        terms = [term for term in text.split() if term]
        escaped = [f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms]
        return " AND ".join(escaped)

    @staticmethod
    def _date_ms(value: str) -> int | None:
        try:
            date = datetime.fromisoformat(value).replace(tzinfo=UTC)
            return int(date.timestamp() * 1000)
        except ValueError:
            return None

    def search_messages(
        self, account_id: int, query: str, limit: int = 300
    ) -> list[dict[str, Any]]:
        free_text, filters = self._parse_search(query)
        where = ["m.account_id=?", "m.is_deleted=0"]
        params: list[Any] = [account_id]
        mapping = {
            "from": "m.sender LIKE ?",
            "to": "(m.recipients LIKE ? OR m.cc LIKE ? OR m.bcc LIKE ?)",
            "subject": "m.subject LIKE ?",
            "label": "m.labels_json LIKE ?",
        }
        for key in ("from", "subject", "label"):
            if value := filters.get(key):
                where.append(mapping[key])
                params.append(f"%{value}%")
        if value := filters.get("to"):
            where.append(mapping["to"])
            params.extend([f"%{value}%"] * 3)
        if value := filters.get("after"):
            if timestamp := self._date_ms(value):
                where.append("m.internal_date>=?")
                params.append(timestamp)
        if value := filters.get("before"):
            if timestamp := self._date_ms(value):
                where.append("m.internal_date<?")
                params.append(timestamp)

        columns = (
            "m.account_id, m.message_id, m.thread_id, m.internal_date, m.sender, "
            "m.recipients, m.subject, m.snippet, m.labels_json, m.size_estimate, "
            "m.has_attachments"
        )
        with self.connection() as connection:
            if free_text:
                try:
                    rows = connection.execute(
                        f"""
                        SELECT {columns}, bm25(message_fts) AS rank
                        FROM message_fts
                        JOIN messages m
                          ON m.account_id=CAST(message_fts.account_id AS INTEGER)
                         AND m.message_id=message_fts.message_id
                        WHERE message_fts MATCH ? AND {' AND '.join(where)}
                        ORDER BY rank, m.internal_date DESC
                        LIMIT ?
                        """,
                        [self._fts_query(free_text), *params, limit],
                    ).fetchall()
                    return [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    like = f"%{free_text}%"
                    where.append(
                        "(m.subject LIKE ? OR m.sender LIKE ? OR m.recipients LIKE ? "
                        "OR m.body_text LIKE ?)"
                    )
                    params.extend([like, like, like, like])

            rows = connection.execute(
                f"""
                SELECT {columns}
                FROM messages m
                WHERE {' AND '.join(where)}
                ORDER BY m.internal_date DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def get_message(self, account_id: int, message_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE account_id=? AND message_id=?",
                (account_id, message_id),
            ).fetchone()
            if not row:
                raise KeyError(f"Unknown message: {message_id}")
            result = dict(row)
            result["attachments"] = [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM attachments WHERE account_id=? AND message_id=? "
                    "ORDER BY attachment_index",
                    (account_id, message_id),
                ).fetchall()
            ]
        return result

    def get_raw_message(self, account_id: int, message_id: str) -> bytes:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT raw_mime, raw_compressed FROM messages "
                "WHERE account_id=? AND message_id=?",
                (account_id, message_id),
            ).fetchone()
        if not row or row["raw_mime"] is None:
            raise KeyError("Raw MIME was not stored for this message")
        payload = bytes(row["raw_mime"])
        return zlib.decompress(payload) if row["raw_compressed"] else payload

    def count_messages(self, account_id: int) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE account_id=? AND is_deleted=0",
                (account_id,),
            ).fetchone()
            return int(row["count"])

    def count_pending_chunks(self, account_id: int, provider: str, model: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM email_chunks c
                JOIN messages m ON m.account_id=c.account_id AND m.message_id=c.message_id
                WHERE c.account_id=? AND m.is_deleted=0
                  AND (c.embedding IS NULL OR c.embedding_provider<>? OR c.embedding_model<>?)
                """,
                (account_id, provider, model),
            ).fetchone()
            return int(row["count"])

    def pending_chunks(
        self, account_id: int, provider: str, model: str, limit: int
    ) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.text
                FROM email_chunks c
                JOIN messages m ON m.account_id=c.account_id AND m.message_id=c.message_id
                WHERE c.account_id=? AND m.is_deleted=0
                  AND (c.embedding IS NULL OR c.embedding_provider<>? OR c.embedding_model<>?)
                ORDER BY c.id
                LIMIT ?
                """,
                (account_id, provider, model, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def store_embeddings(
        self,
        rows: Sequence[tuple[int, bytes, int]],
        *,
        provider: str,
        model: str,
    ) -> None:
        if not rows:
            return
        now = utc_now()
        with self.connection() as connection:
            connection.executemany(
                """
                UPDATE email_chunks
                SET embedding_provider=?, embedding_model=?, embedding=?, dims=?, indexed_at=?
                WHERE id=?
                """,
                [
                    (provider, model, sqlite3.Binary(blob), dims, now, chunk_id)
                    for chunk_id, blob, dims in rows
                ],
            )

    def vector_rows(
        self, account_id: int, provider: str, model: str
    ) -> list[tuple[int, bytes, int]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.embedding, c.dims
                FROM email_chunks c
                JOIN messages m ON m.account_id=c.account_id AND m.message_id=c.message_id
                WHERE c.account_id=? AND m.is_deleted=0
                  AND c.embedding IS NOT NULL
                  AND c.embedding_provider=? AND c.embedding_model=?
                ORDER BY c.id
                """,
                (account_id, provider, model),
            ).fetchall()
        return [(int(row["id"]), bytes(row["embedding"]), int(row["dims"])) for row in rows]

    def search_chunks_fts(
        self, account_id: int, query: str, limit: int = 40
    ) -> list[tuple[int, float]]:
        if not query.strip():
            return []
        with self.connection() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT CAST(chunk_fts.chunk_id AS INTEGER) AS chunk_id,
                           bm25(chunk_fts) AS rank
                    FROM chunk_fts
                    JOIN messages m
                      ON m.account_id=CAST(chunk_fts.account_id AS INTEGER)
                     AND m.message_id=chunk_fts.message_id
                    WHERE chunk_fts MATCH ?
                      AND m.account_id=? AND m.is_deleted=0
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (self._fts_query(query), account_id, limit),
                ).fetchall()
                return [(int(row["chunk_id"]), float(row["rank"])) for row in rows]
            except sqlite3.OperationalError:
                rows = connection.execute(
                    """
                    SELECT c.id AS chunk_id, 0.0 AS rank
                    FROM email_chunks c
                    JOIN messages m ON m.account_id=c.account_id AND m.message_id=c.message_id
                    WHERE c.account_id=? AND m.is_deleted=0 AND c.text LIKE ?
                    LIMIT ?
                    """,
                    (account_id, f"%{query}%", limit),
                ).fetchall()
                return [(int(row["chunk_id"]), 0.0) for row in rows]

    def get_chunks(self, chunk_ids: Sequence[int]) -> list[RetrievedChunk]:
        ids = [int(item) for item in chunk_ids]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT c.id AS chunk_id, c.message_id, c.text,
                       m.subject, m.sender, m.recipients, m.internal_date
                FROM email_chunks c
                JOIN messages m ON m.account_id=c.account_id AND m.message_id=c.message_id
                WHERE c.id IN ({placeholders}) AND m.is_deleted=0
                """,
                ids,
            ).fetchall()
        by_id = {
            int(row["chunk_id"]): RetrievedChunk(
                chunk_id=int(row["chunk_id"]),
                message_id=str(row["message_id"]),
                text=str(row["text"]),
                score=0.0,
                subject=str(row["subject"]),
                sender=str(row["sender"]),
                recipients=str(row["recipients"]),
                internal_date=int(row["internal_date"]),
            )
            for row in rows
        }
        return [by_id[item] for item in ids if item in by_id]

    def create_chat_session(self, account_id: int, title: str = "New chat") -> int:
        now = utc_now()
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO chat_sessions(account_id, title, created_at, updated_at) "
                "VALUES(?, ?, ?, ?)",
                (account_id, title[:120], now, now),
            )
            return int(cursor.lastrowid)

    def save_chat_message(self, session_id: int, role: str, content: str) -> None:
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO chat_messages(session_id, role, content, created_at) "
                "VALUES(?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            connection.execute(
                "UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id)
            )

    def chat_history(self, session_id: int, limit: int = 12) -> list[dict[str, str]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM chat_messages
                    WHERE session_id=? ORDER BY id DESC LIMIT ?
                ) ORDER BY id
                """,
                (session_id, limit),
            ).fetchall()
        return [{"role": str(row["role"]), "content": str(row["content"])} for row in rows]

    def statistics(self, account_id: int) -> dict[str, int]:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN is_deleted=0 THEN 1 ELSE 0 END) AS active_messages,
                    SUM(CASE WHEN is_deleted=1 THEN 1 ELSE 0 END) AS archived_deleted,
                    COALESCE(SUM(CASE WHEN is_deleted=0 THEN size_estimate ELSE 0 END), 0) AS bytes
                FROM messages WHERE account_id=?
                """,
                (account_id,),
            ).fetchone()
            chunks = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM email_chunks c
                JOIN messages m ON m.account_id=c.account_id AND m.message_id=c.message_id
                WHERE c.account_id=? AND m.is_deleted=0
                """,
                (account_id,),
            ).fetchone()
        return {
            "active_messages": int(row["active_messages"] or 0),
            "archived_deleted": int(row["archived_deleted"] or 0),
            "bytes": int(row["bytes"] or 0),
            "chunks": int(chunks["count"] or 0),
        }
