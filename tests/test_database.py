from __future__ import annotations

from pathlib import Path

import numpy as np

from cherry_mail_memory.database import Database
from cherry_mail_memory.mime_utils import parse_gmail_raw
from cherry_mail_memory.rag import RAGEngine
from cherry_mail_memory.config import AppSettings
from tests.test_mime_utils import gmail_message


def test_archive_search_delete_restore_and_raw_export(tmp_path: Path) -> None:
    database = Database(tmp_path / "mail.db")
    account_id = database.upsert_account("owner@example.com", str(tmp_path / "token.json"))
    payload, raw = gmail_message()
    parsed = parse_gmail_raw(payload)

    changed = database.upsert_messages(
        account_id,
        [parsed],
        chunk_size=600,
        chunk_overlap=50,
        store_raw_mime=True,
    )
    assert changed == 1
    assert database.count_messages(account_id) == 1
    assert database.get_raw_message(account_id, "gmail-1") == raw

    results = database.search_messages(account_id, "ระบบทำงาน")
    assert [item["message_id"] for item in results] == ["gmail-1"]
    assert database.search_messages(account_id, "from:alice@example.com")
    assert database.search_messages(account_id, 'subject:"รายงานระบบประจำวัน"')

    assert database.mark_messages_deleted(account_id, ["gmail-1"]) == 1
    assert database.count_messages(account_id) == 0
    assert database.get_raw_message(account_id, "gmail-1") == raw

    restored = database.upsert_messages(
        account_id,
        [parsed],
        chunk_size=600,
        chunk_overlap=50,
        store_raw_mime=True,
    )
    assert restored == 0
    assert database.count_messages(account_id) == 1
    assert database.search_messages(account_id, "ระบบทำงาน")


def test_embeddings_and_lexical_rag(tmp_path: Path) -> None:
    database = Database(tmp_path / "mail.db")
    account_id = database.upsert_account("owner@example.com", str(tmp_path / "token.json"))
    payload, _ = gmail_message()
    database.upsert_messages(
        account_id,
        [parse_gmail_raw(payload)],
        chunk_size=600,
        chunk_overlap=50,
        store_raw_mime=False,
    )

    pending = database.pending_chunks(account_id, "openai-compatible", "test-model", 10)
    assert pending
    vector = np.asarray([1.0, 0.0, 0.5], dtype=np.float32)
    database.store_embeddings(
        [(int(pending[0]["id"]), vector.tobytes(), vector.size)],
        provider="openai-compatible",
        model="test-model",
    )
    rows = database.vector_rows(account_id, "openai-compatible", "test-model")
    assert rows and rows[0][2] == 3

    settings = AppSettings(embedding_model="", llm_model="")
    engine = RAGEngine(database, settings)
    chunks = engine.retrieve(account_id, "งานค้าง")
    assert chunks
    assert chunks[0].message_id == "gmail-1"


def test_incremental_label_metadata_update(tmp_path: Path) -> None:
    database = Database(tmp_path / "mail.db")
    account_id = database.upsert_account("owner@example.com", str(tmp_path / "token.json"))
    payload, _ = gmail_message()
    database.upsert_messages(
        account_id,
        [parse_gmail_raw(payload)],
        chunk_size=600,
        chunk_overlap=50,
        store_raw_mime=True,
    )

    assert database.existing_message_ids(account_id, ["gmail-1", "missing"]) == {"gmail-1"}
    count = database.update_message_labels(
        account_id,
        [{"id": "gmail-1", "historyId": "202", "labelIds": ["STARRED"]}],
    )
    assert count == 1
    message = database.get_message(account_id, "gmail-1")
    assert message["history_id"] == "202"
    assert message["labels_json"] == '["STARRED"]'
