from __future__ import annotations

import base64
import hashlib
import html
import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from cherry_mail_memory.models import AttachmentRecord, ParsedMessage


_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_UNSAFE_FILENAME_RE = re.compile(r"[^\w.()\-\u0E00-\u0E7F ]+", re.UNICODE)


class _HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._hidden_depth += 1
        if not self._hidden_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1
        if not self._hidden_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)

    def text(self) -> str:
        return normalize_text("".join(self.parts))


def decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(_WHITESPACE_RE.sub(" ", line).rstrip() for line in value.splitlines())
    value = _BLANK_LINES_RE.sub("\n\n", value)
    return value.strip()


def html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value)
        parser.close()
        return parser.text()
    except Exception:
        return normalize_text(html.unescape(re.sub(r"<[^>]+>", " ", value)))


def _part_text(part: Message, payload: bytes) -> str:
    try:
        content = part.get_content()
        if isinstance(content, str):
            return normalize_text(content)
        if isinstance(content, bytes):
            payload = content
    except Exception:
        pass

    charset = part.get_content_charset() or "utf-8"
    try:
        return normalize_text(payload.decode(charset, errors="replace"))
    except LookupError:
        return normalize_text(payload.decode("utf-8", errors="replace"))


def parse_gmail_raw(message: dict) -> ParsedMessage:
    raw_value = str(message.get("raw", ""))
    if not raw_value:
        raise ValueError(f"Gmail message {message.get('id', '<unknown>')} has no raw MIME payload")

    raw_bytes = decode_base64url(raw_value)
    mime_message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[AttachmentRecord] = []

    for part in mime_message.walk():
        if part.is_multipart():
            continue

        mime_type = part.get_content_type().lower()
        disposition = (part.get_content_disposition() or "").lower()
        filename = str(part.get_filename() or "").strip()
        content_id = str(part.get("Content-ID", "")).strip("<>")
        payload = part.get_payload(decode=True) or b""
        is_attachment = bool(filename) or disposition == "attachment"

        if is_attachment:
            attachments.append(
                AttachmentRecord(
                    filename=filename or "attachment.bin",
                    mime_type=mime_type,
                    size=len(payload),
                    content_id=content_id,
                    data=payload,
                )
            )
            continue

        if mime_type == "text/plain":
            text = _part_text(part, payload)
            if text:
                plain_parts.append(text)
        elif mime_type == "text/html":
            text = _part_text(part, payload)
            if text:
                html_parts.append(text)

    body_html = normalize_text("\n\n".join(html_parts))
    body_text = normalize_text("\n\n".join(plain_parts))
    if not body_text and body_html:
        body_text = html_to_text(body_html)

    def header_value(name: str) -> str:
        return normalize_text(str(mime_message.get(name, "")))

    return ParsedMessage(
        message_id=str(message.get("id", "")),
        thread_id=str(message.get("threadId", "")),
        history_id=str(message.get("historyId", "")),
        internal_date=int(message.get("internalDate", 0) or 0),
        rfc822_message_id=header_value("Message-ID"),
        sender=header_value("From"),
        recipients=header_value("To"),
        cc=header_value("Cc"),
        bcc=header_value("Bcc"),
        reply_to=header_value("Reply-To"),
        subject=header_value("Subject") or "(ไม่มีหัวข้อ)",
        snippet=normalize_text(str(message.get("snippet", ""))),
        body_text=body_text,
        body_html=body_html,
        label_ids=[str(item) for item in message.get("labelIds", [])],
        size_estimate=int(message.get("sizeEstimate", len(raw_bytes)) or len(raw_bytes)),
        raw_bytes=raw_bytes,
        attachments=attachments,
    )


def message_content_hash(message: ParsedMessage) -> str:
    digest = hashlib.sha256()
    for value in (
        message.subject,
        message.sender,
        message.recipients,
        message.cc,
        message.bcc,
        message.reply_to,
        message.body_text,
        message.body_html,
    ):
        digest.update(value.encode("utf-8", errors="replace"))
        digest.update(b"\x00")
    return digest.hexdigest()


def chunk_message(message: ParsedMessage, chunk_size: int, overlap: int) -> list[str]:
    prefix = (
        f"Subject: {message.subject}\n"
        f"From: {message.sender}\n"
        f"To: {message.recipients}\n"
        f"Cc: {message.cc}\n"
        f"Date-Epoch-Ms: {message.internal_date}\n"
        f"Message-ID: {message.message_id}\n\n"
    )
    content = normalize_text(message.body_text or message.snippet)
    document = prefix + content
    if len(document) <= chunk_size:
        return [document]

    chunks: list[str] = []
    start = 0
    minimum_boundary = int(chunk_size * 0.60)
    while start < len(document):
        desired_end = min(start + chunk_size, len(document))
        end = desired_end
        if desired_end < len(document):
            boundary_floor = start + minimum_boundary
            newline = document.rfind("\n", boundary_floor, desired_end)
            space = document.rfind(" ", boundary_floor, desired_end)
            boundary = max(newline, space)
            if boundary > start:
                end = boundary

        chunk = document[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(document):
            break
        start = max(end - overlap, start + 1)

    return chunks


def safe_filename(filename: str, fallback: str = "attachment.bin") -> str:
    candidate = Path(filename).name.strip().replace("\x00", "")
    candidate = _UNSAFE_FILENAME_RE.sub("_", candidate).strip(" .")
    if not candidate:
        candidate = fallback
    return candidate[:180]
