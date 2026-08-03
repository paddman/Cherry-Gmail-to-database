from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from googleapiclient.errors import HttpError

from cherry_mail_memory.config import AppPaths, AppSettings
from cherry_mail_memory.database import Database
from cherry_mail_memory.gmail_client import GmailClient, http_status
from cherry_mail_memory.mime_utils import decode_base64url, parse_gmail_raw, safe_filename
from cherry_mail_memory.models import ParsedMessage


LOGGER = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]


class SyncCancelled(RuntimeError):
    pass


class SyncService:
    def __init__(self, database: Database, settings: AppSettings, paths: AppPaths) -> None:
        self.database = database
        self.settings = settings
        self.paths = paths
        self.gmail = GmailClient(paths.token_dir)

    def refresh_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def connect_account(self, credentials_path: Path) -> int:
        email, token_path = self.gmail.authorize_new(credentials_path)
        return self.database.upsert_account(email, str(token_path))

    @staticmethod
    def _check_cancel(cancel_event: threading.Event | None) -> None:
        if cancel_event and cancel_event.is_set():
            raise SyncCancelled("Gmail synchronization was cancelled")

    def _write_attachments(self, account_email: str, message: ParsedMessage) -> None:
        maximum = self.settings.max_attachment_mb * 1024 * 1024
        target_dir = (
            self.paths.attachment_dir
            / safe_filename(account_email.replace("@", "_at_"), "gmail")
            / safe_filename(message.message_id, "message")
        )
        used_names: set[str] = set()
        for index, attachment in enumerate(message.attachments):
            if not self.settings.download_attachments or attachment.data is None:
                attachment.data = None
                continue
            if maximum and attachment.size > maximum:
                attachment.data = None
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = safe_filename(attachment.filename, f"attachment-{index}.bin")
            candidate = filename
            suffix = 1
            while candidate.lower() in used_names:
                stem = Path(filename).stem
                extension = Path(filename).suffix
                candidate = f"{stem}-{suffix}{extension}"
                suffix += 1
            used_names.add(candidate.lower())
            path = target_dir / candidate
            path.write_bytes(attachment.data)
            attachment.file_path = str(path)
            attachment.downloaded = True
            attachment.data = None

    @staticmethod
    def _minimal_message(raw: dict[str, Any]) -> ParsedMessage:
        raw_bytes = decode_base64url(str(raw.get("raw", "")))
        return ParsedMessage(
            message_id=str(raw.get("id", "")),
            thread_id=str(raw.get("threadId", "")),
            history_id=str(raw.get("historyId", "")),
            internal_date=int(raw.get("internalDate", 0) or 0),
            rfc822_message_id="",
            sender="",
            recipients="",
            cc="",
            bcc="",
            reply_to="",
            subject="(อ่าน MIME ไม่สำเร็จ)",
            snippet=str(raw.get("snippet", "")),
            body_text="",
            body_html="",
            label_ids=[str(item) for item in raw.get("labelIds", [])],
            size_estimate=int(raw.get("sizeEstimate", len(raw_bytes)) or len(raw_bytes)),
            raw_bytes=raw_bytes,
            attachments=[],
        )

    def _parse_batch(self, account_email: str, raw_messages: list[dict[str, Any]]) -> list[ParsedMessage]:
        parsed: list[ParsedMessage] = []
        for raw in raw_messages:
            try:
                message = parse_gmail_raw(raw)
            except Exception:
                LOGGER.exception("Unable to parse Gmail message %s", raw.get("id"))
                message = self._minimal_message(raw)
            self._write_attachments(account_email, message)
            parsed.append(message)
        return parsed

    def sync_account(
        self,
        account_id: int,
        *,
        force_full: bool = False,
        progress: Progress | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        account = self.database.get_account(account_id)
        self.database.update_account_sync(account_id, status="syncing", error=None)
        full_attempted = False
        try:
            service = self.gmail.service_from_token(Path(account.token_path))
            if force_full or not account.history_id:
                full_attempted = True
                result = self._full_sync(
                    account_id,
                    account.email,
                    service,
                    progress=progress,
                    cancel_event=cancel_event,
                )
            else:
                try:
                    result = self._incremental_sync(
                        account_id,
                        account.email,
                        service,
                        account.history_id,
                        progress=progress,
                        cancel_event=cancel_event,
                    )
                except HttpError as exc:
                    if http_status(exc) != 404:
                        raise
                    LOGGER.info(
                        "Gmail history id expired for %s; falling back to full sync", account.email
                    )
                    if progress:
                        progress(0, 0, "historyId หมดอายุ กำลังทำ full sync ใหม่")
                    full_attempted = True
                    result = self._full_sync(
                        account_id,
                        account.email,
                        service,
                        progress=progress,
                        cancel_event=cancel_event,
                    )
            self.database.update_account_sync(
                account_id,
                status="idle",
                error=None,
                history_id=str(result["history_id"]),
                completed=True,
            )
            return result
        except SyncCancelled:
            if full_attempted:
                self.database.clear_account_history(account_id)
            self.database.update_account_sync(account_id, status="idle", error=None)
            raise
        except Exception as exc:
            if full_attempted:
                self.database.clear_account_history(account_id)
            self.database.update_account_sync(account_id, status="error", error=str(exc))
            raise

    def _full_sync(
        self,
        account_id: int,
        account_email: str,
        service: Any,
        *,
        progress: Progress | None,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        page_token: str | None = None
        processed = 0
        changed = 0
        failures: dict[str, str] = {}
        remote_ids: set[str] = set()
        total = 0
        history_anchor_message_id: str | None = None
        initial_history_id: str | None = None

        while True:
            self._check_cancel(cancel_event)
            response = self.gmail.list_messages_page(service, page_token)
            if not total:
                total = int(response.get("resultSizeEstimate", 0) or 0)
            ids = [str(item["id"]) for item in response.get("messages", [])]
            if history_anchor_message_id is None and ids:
                history_anchor_message_id = ids[0]
            remote_ids.update(ids)

            for offset in range(0, len(ids), self.settings.batch_size):
                self._check_cancel(cancel_event)
                group = ids[offset : offset + self.settings.batch_size]
                raw_messages, errors = self.gmail.fetch_messages(
                    service,
                    group,
                    message_format="raw",
                    batch_size=self.settings.batch_size,
                )
                if initial_history_id is None and history_anchor_message_id in group:
                    anchor = next(
                        (item for item in raw_messages if item.get("id") == history_anchor_message_id),
                        None,
                    )
                    if anchor and anchor.get("historyId"):
                        initial_history_id = str(anchor["historyId"])
                parsed = self._parse_batch(account_email, raw_messages)
                changed += self.database.upsert_messages(
                    account_id,
                    parsed,
                    chunk_size=self.settings.chunk_size,
                    chunk_overlap=self.settings.chunk_overlap,
                    store_raw_mime=self.settings.store_raw_mime,
                )
                failures.update({key: str(value) for key, value in errors.items()})
                processed += len(group)
                if progress:
                    progress(processed, total, f"เก็บ Gmail {processed:,}/{total or processed:,}")

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        if failures:
            raise RuntimeError(
                f"ดึง Gmail ไม่สำเร็จ {len(failures):,} ข้อความ ระบบจะไม่เลื่อน historyId เพื่อให้ลองใหม่ได้"
            )
        deleted = self.database.reconcile_messages(account_id, remote_ids)
        if initial_history_id is None:
            profile = self.gmail.profile(service)
            initial_history_id = str(profile["historyId"])
        return {
            "mode": "full",
            "processed": processed,
            "changed": changed,
            "deleted": deleted,
            "failed": failures,
            "history_id": initial_history_id,
        }

    def _incremental_sync(
        self,
        account_id: int,
        account_email: str,
        service: Any,
        start_history_id: str,
        *,
        progress: Progress | None,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        page_token: str | None = None
        latest_history_id = start_history_id
        processed = 0
        changed_count = 0
        deleted_count = 0
        failures: dict[str, str] = {}

        while True:
            self._check_cancel(cancel_event)
            response = self.gmail.history_page(service, start_history_id, page_token)
            added_ids: set[str] = set()
            label_changed_ids: set[str] = set()
            deleted_ids: set[str] = set()
            for history in response.get("history", []):
                for item in history.get("messagesAdded", []):
                    added_ids.add(str(item.get("message", {}).get("id", "")))
                for item in history.get("labelsAdded", []):
                    label_changed_ids.add(str(item.get("message", {}).get("id", "")))
                for item in history.get("labelsRemoved", []):
                    label_changed_ids.add(str(item.get("message", {}).get("id", "")))
                for item in history.get("messagesDeleted", []):
                    deleted_ids.add(str(item.get("message", {}).get("id", "")))

            added_ids.discard("")
            label_changed_ids.discard("")
            deleted_ids.discard("")
            existing = self.database.existing_message_ids(account_id, label_changed_ids)
            raw_ids = sorted((added_ids | (label_changed_ids - existing)) - deleted_ids)
            minimal_ids = sorted((label_changed_ids & existing) - added_ids - deleted_ids)
            page_total = len(raw_ids) + len(minimal_ids)
            page_processed = 0

            for offset in range(0, len(raw_ids), self.settings.batch_size):
                self._check_cancel(cancel_event)
                group = raw_ids[offset : offset + self.settings.batch_size]
                raw_messages, errors = self.gmail.fetch_messages(
                    service,
                    group,
                    message_format="raw",
                    batch_size=self.settings.batch_size,
                )
                parsed = self._parse_batch(account_email, raw_messages)
                changed_count += self.database.upsert_messages(
                    account_id,
                    parsed,
                    chunk_size=self.settings.chunk_size,
                    chunk_overlap=self.settings.chunk_overlap,
                    store_raw_mime=self.settings.store_raw_mime,
                )
                for message_id, error in errors.items():
                    if http_status(error) == 404:
                        deleted_ids.add(message_id)
                    else:
                        failures[message_id] = str(error)
                processed += len(group)
                page_processed += len(group)
                if progress:
                    progress(
                        page_processed,
                        page_total,
                        f"อัปเดตเมลและเนื้อหา {page_processed:,}/{page_total:,}",
                    )

            for offset in range(0, len(minimal_ids), self.settings.batch_size):
                self._check_cancel(cancel_event)
                group = minimal_ids[offset : offset + self.settings.batch_size]
                metadata, errors = self.gmail.fetch_messages(
                    service,
                    group,
                    message_format="minimal",
                    batch_size=self.settings.batch_size,
                )
                changed_count += self.database.update_message_labels(account_id, metadata)
                for message_id, error in errors.items():
                    if http_status(error) == 404:
                        deleted_ids.add(message_id)
                    else:
                        failures[message_id] = str(error)
                processed += len(group)
                page_processed += len(group)
                if progress:
                    progress(
                        page_processed,
                        page_total,
                        f"อัปเดต labels {page_processed:,}/{page_total:,}",
                    )

            deleted_count += self.database.mark_messages_deleted(account_id, deleted_ids)
            latest_history_id = str(response.get("historyId", latest_history_id))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        if failures:
            raise RuntimeError(
                f"อัปเดต Gmail ไม่สำเร็จ {len(failures):,} ข้อความ ระบบจะเก็บ historyId เดิมไว้เพื่อลองใหม่"
            )
        if progress and processed == 0 and deleted_count == 0:
            progress(1, 1, "Gmail ไม่มีรายการเปลี่ยนแปลงใหม่")
        return {
            "mode": "incremental",
            "processed": processed,
            "changed": changed_count,
            "deleted": deleted_count,
            "failed": failures,
            "history_id": latest_history_id,
        }
