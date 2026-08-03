from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Account:
    id: int
    email: str
    token_path: str
    history_id: str | None
    last_sync_at: str | None
    sync_status: str
    last_error: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class AttachmentRecord:
    filename: str
    mime_type: str
    size: int
    content_id: str = ""
    file_path: str = ""
    downloaded: bool = False
    data: bytes | None = field(default=None, repr=False)


@dataclass(slots=True)
class ParsedMessage:
    message_id: str
    thread_id: str
    history_id: str
    internal_date: int
    rfc822_message_id: str
    sender: str
    recipients: str
    cc: str
    bcc: str
    reply_to: str
    subject: str
    snippet: str
    body_text: str
    body_html: str
    label_ids: list[str]
    size_estimate: int
    raw_bytes: bytes | None
    attachments: list[AttachmentRecord]


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: int
    message_id: str
    text: str
    score: float
    subject: str
    sender: str
    recipients: str
    internal_date: int


@dataclass(slots=True)
class AIAnswer:
    answer: str
    sources: list[RetrievedChunk]


ProgressCallback = Any
