from __future__ import annotations

import base64
from email.message import EmailMessage

from cherry_mail_memory.mime_utils import chunk_message, parse_gmail_raw, safe_filename


def gmail_message() -> tuple[dict, bytes]:
    email = EmailMessage()
    email["From"] = "Alice <alice@example.com>"
    email["To"] = "Bob <bob@example.com>"
    email["Subject"] = "รายงานระบบประจำวัน"
    email["Message-ID"] = "<test-1@example.com>"
    email.set_content("ระบบทำงานปกติ และมีงานค้าง 3 รายการ")
    email.add_alternative("<html><body><p>ระบบทำงานปกติ</p></body></html>", subtype="html")
    email.add_attachment(b"hello attachment", maintype="text", subtype="plain", filename="note.txt")
    raw = email.as_bytes()
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return (
        {
            "id": "gmail-1",
            "threadId": "thread-1",
            "historyId": "101",
            "internalDate": "1760000000000",
            "labelIds": ["INBOX", "IMPORTANT"],
            "sizeEstimate": len(raw),
            "raw": encoded,
        },
        raw,
    )


def test_parse_gmail_raw_extracts_text_and_attachment() -> None:
    payload, raw = gmail_message()
    parsed = parse_gmail_raw(payload)

    assert parsed.message_id == "gmail-1"
    assert parsed.subject == "รายงานระบบประจำวัน"
    assert "ระบบทำงานปกติ" in parsed.body_text
    assert parsed.raw_bytes == raw
    assert parsed.label_ids == ["INBOX", "IMPORTANT"]
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "note.txt"
    assert parsed.attachments[0].data == b"hello attachment"


def test_chunk_message_keeps_mail_metadata() -> None:
    payload, _ = gmail_message()
    parsed = parse_gmail_raw(payload)
    parsed.body_text = "ข้อความยาว " * 300
    chunks = chunk_message(parsed, chunk_size=500, overlap=50)

    assert len(chunks) > 1
    assert chunks[0].startswith("Subject: รายงานระบบประจำวัน")
    assert all(chunk.strip() for chunk in chunks)


def test_safe_filename_removes_path_traversal() -> None:
    assert safe_filename("../../secret?.txt") == "secret_.txt"
