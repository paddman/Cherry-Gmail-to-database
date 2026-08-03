from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def http_status(error: BaseException) -> int | None:
    if isinstance(error, HttpError):
        return int(getattr(error.resp, "status", 0) or 0)
    return None


class GmailClient:
    def __init__(self, token_dir: Path) -> None:
        self.token_dir = token_dir
        self.token_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def execute(request: Any, retries: int = 5) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(retries + 1):
            try:
                return request.execute(num_retries=2)
            except HttpError as exc:
                status = http_status(exc)
                if status not in _RETRYABLE_STATUSES or attempt >= retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 16)
        raise RuntimeError("Gmail request retry loop ended unexpectedly")

    @staticmethod
    def _save_credentials(credentials: Credentials, token_path: Path) -> None:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = token_path.with_suffix(".tmp")
        temporary.write_text(credentials.to_json(), encoding="utf-8")
        temporary.replace(token_path)
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _build(credentials: Credentials) -> Resource:
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def authorize_new(self, credentials_path: Path) -> tuple[str, Path]:
        if not credentials_path.is_file():
            raise FileNotFoundError(f"OAuth credentials file not found: {credentials_path}")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        credentials = flow.run_local_server(
            port=0,
            open_browser=True,
            access_type="offline",
            prompt="consent",
            authorization_prompt_message=(
                "เปิดเบราว์เซอร์เพื่อเชื่อม Gmail กับ Cherry Mail Memory: {url}"
            ),
            success_message="เชื่อม Gmail สำเร็จแล้ว ปิดแท็บนี้และกลับไปที่แอปได้",
        )
        service = self._build(credentials)
        profile = self.execute(service.users().getProfile(userId="me"))
        email = str(profile["emailAddress"]).strip()
        digest = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:20]
        token_path = self.token_dir / f"gmail-{digest}.json"
        self._save_credentials(credentials, token_path)
        return email, token_path

    def service_from_token(self, token_path: Path) -> Resource:
        if not token_path.is_file():
            raise FileNotFoundError(f"Gmail token not found: {token_path}")
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self._save_credentials(credentials, token_path)
        if not credentials.valid:
            raise RuntimeError("Gmail authorization is invalid; connect the account again")
        return self._build(credentials)

    def profile(self, service: Resource) -> dict[str, Any]:
        return self.execute(service.users().getProfile(userId="me"))

    def list_messages_page(
        self, service: Resource, page_token: str | None = None
    ) -> dict[str, Any]:
        return self.execute(
            service.users()
            .messages()
            .list(
                userId="me",
                includeSpamTrash=True,
                maxResults=500,
                pageToken=page_token,
            )
        )

    def history_page(
        self,
        service: Resource,
        start_history_id: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        return self.execute(
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
                maxResults=500,
                pageToken=page_token,
            )
        )

    def fetch_messages(
        self,
        service: Resource,
        message_ids: Sequence[str],
        *,
        message_format: str = "raw",
        batch_size: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, BaseException]]:
        responses: dict[str, dict[str, Any]] = {}
        errors: dict[str, BaseException] = {}

        for offset in range(0, len(message_ids), batch_size):
            group = [str(item) for item in message_ids[offset : offset + batch_size]]

            def callback(
                request_id: str,
                response: dict[str, Any] | None,
                exception: BaseException | None,
            ) -> None:
                if exception is not None:
                    errors[request_id] = exception
                elif response is not None:
                    responses[request_id] = response

            batch = service.new_batch_http_request(callback=callback)
            for message_id in group:
                batch.add(
                    service.users().messages().get(
                        userId="me", messageId=message_id, format=message_format
                    ),
                    request_id=message_id,
                )
            try:
                batch.execute()
            except Exception as exc:
                for message_id in group:
                    errors[message_id] = exc

            failed = [message_id for message_id in group if message_id not in responses]
            for message_id in failed:
                try:
                    responses[message_id] = self.execute(
                        service.users().messages().get(
                            userId="me", messageId=message_id, format=message_format
                        )
                    )
                    errors.pop(message_id, None)
                except Exception as exc:
                    errors[message_id] = exc

        ordered = [responses[item] for item in message_ids if item in responses]
        return ordered, errors
